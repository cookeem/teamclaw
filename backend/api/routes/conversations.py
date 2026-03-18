from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import os
import queue
import re
import random
from time import perf_counter
import traceback
from uuid import uuid4
import threading
import shutil
from typing import Any, Callable

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import desc, select, update
from sqlalchemy.orm import Session

from backend.api.deps import get_current_user
from backend.core.config import get_settings
from backend.core.database import get_db
from backend.core.models import Conversation, ConversationMessage, Skill, User
from backend.core.security import decode_access_token
from backend.i18n import t
from backend.services.deepagents_service import deepagent_service
from backend.services.office_extract import ALLOWED_OFFICE_EXTENSIONS, extract_office_to_markdown
from backend.services.stream_events import stream_event_publisher

router = APIRouter(prefix="/conversations", tags=["conversations"])
logger = logging.getLogger(__name__)


class CreateConversationRequest(BaseModel):
    title: str = Field(default_factory=lambda: t("conversation.default_title"))
    model: str = "default"
    skills: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)


class UpdateConversationRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    is_pinned: bool | None = None


class SendMessageRequest(BaseModel):
    content: str = Field(min_length=1)


class InterruptDecisionRequest(BaseModel):
    decision: str = Field(pattern="^(allow|reject|allow_all)$")

class DebugExecRequest(BaseModel):
    command: str = Field(min_length=1)


def _serialize_message(m: ConversationMessage) -> dict:
    created_at = m.created_at
    if created_at is None:
        created_at = datetime.now(timezone.utc)
    return {
        "id": m.id,
        "conversation_id": m.conversation_id,
        "sender_role": m.sender_role,
        "message_type": m.message_type,
        "tool_name": m.tool_name,
        "message_status": m.message_status,
        "content_md": m.content_md,
        "attachments_json": m.attachments_json,
        "input_tokens": m.input_tokens,
        "output_tokens": m.output_tokens,
        "total_tokens": m.total_tokens,
        "run_duration_ms": m.run_duration_ms,
        "created_at": created_at.isoformat(),
    }


def _extract_attachments(payload: object) -> list[dict]:
    if not payload:
        return []
    if isinstance(payload, dict):
        items = payload.get("items")
        if isinstance(items, list):
            return [i for i in items if isinstance(i, dict)]
        return []
    if isinstance(payload, list):
        return [i for i in payload if isinstance(i, dict)]
    return []


def _serialize_conversation(c: Conversation) -> dict:
    return {
        "id": c.id,
        "title": c.title,
        "model": c.model_name,
        "container_status": c.container_status,
        "daemon_host": c.daemon_host,
        "pending_interrupt_id": c.pending_interrupt_id,
        "is_pinned": bool(getattr(c, "is_pinned", False)),
        "pinned_at": c.pinned_at.isoformat() if c.pinned_at else None,
        "created_at": c.created_at,
        "updated_at": c.updated_at,
    }


def _publish_messages(conversation_id: str, user_id: str, messages: list[dict]) -> None:
    for msg in messages:
        stream_event_publisher.publish_conversation_event(
            conversation_id=conversation_id,
            user_id=user_id,
            event_type="message.created",
            payload=msg,
        )


def _publish_message_event(conversation_id: str, user_id: str, message: dict, event_type: str) -> None:
    stream_event_publisher.publish_conversation_event(
        conversation_id=conversation_id,
        user_id=user_id,
        event_type=event_type,
        payload=message,
    )


def _make_progress_handler(
    conversation_id: str,
    user_id: str,
    db: Session,
    started: float,
) -> tuple[Callable[[dict], None], Callable[..., ConversationMessage | None], dict]:
    state: dict[str, Any] = {
        "ai_message": None,
        "ai_buffer": "",
        "last_flush": perf_counter(),
    }

    def flush_ai(
        force: bool = False,
        final_status: str | None = None,
        tokens: dict[str, int] | None = None,
        run_duration_ms: int | None = None,
    ) -> ConversationMessage | None:
        ai_message: ConversationMessage | None = state["ai_message"]
        buffer = state["ai_buffer"]
        now = perf_counter()
        should_flush = force or len(buffer) >= 200 or (now - state["last_flush"]) >= 0.8
        if not ai_message and not buffer:
            return None
        if not ai_message:
            content = buffer
            if not content and not force:
                return None
            ai_message = ConversationMessage(
                conversation_id=conversation_id,
                sender_user_id=None,
                sender_role="assistant",
                message_type="AIMessage",
                message_status="streaming",
                content_md=content,
                run_duration_ms=int((perf_counter() - started) * 1000),
            )
            db.add(ai_message)
            db.commit()
            db.refresh(ai_message)
            _publish_message_event(conversation_id, user_id, _serialize_message(ai_message), "message.created")
            state["ai_message"] = ai_message
            state["ai_buffer"] = ""
            state["last_flush"] = now
            return ai_message

        if not should_flush and not final_status:
            return ai_message

        if buffer:
            ai_message.content_md = (ai_message.content_md or "") + buffer
            state["ai_buffer"] = ""
        if final_status:
            ai_message.message_status = final_status
        if tokens:
            ai_message.input_tokens = tokens.get("input_tokens", ai_message.input_tokens or 0)
            ai_message.output_tokens = tokens.get("output_tokens", ai_message.output_tokens or 0)
            ai_message.total_tokens = tokens.get("total_tokens", ai_message.total_tokens or 0)
        if run_duration_ms is not None:
            ai_message.run_duration_ms = run_duration_ms
        db.add(ai_message)
        db.commit()
        db.refresh(ai_message)
        _publish_message_event(conversation_id, user_id, _serialize_message(ai_message), "message.updated")
        state["last_flush"] = now
        return ai_message

    def on_progress(event: dict) -> None:
        event_type = event.get("type")
        if event_type == "tool_output":
            tool_name = event.get("tool_name")
            tool_content = str(event.get("content") or "")
            tool_status = (
                "failed"
                if "Command failed with exit code" in tool_content or "Error executing" in tool_content
                else "done"
            )
            tool_message = ConversationMessage(
                conversation_id=conversation_id,
                sender_user_id=None,
                sender_role="assistant",
                message_type="ToolMessage",
                tool_name=tool_name,
                message_status=tool_status,
                content_md=tool_content,
                run_duration_ms=int((perf_counter() - started) * 1000),
            )
            db.add(tool_message)
            if tool_name:
                db.execute(
                    update(Skill)
                    .where(Skill.name == str(tool_name))
                    .values(usage_count=Skill.usage_count + 1)
                )
            db.commit()
            db.refresh(tool_message)
            _publish_message_event(conversation_id, user_id, _serialize_message(tool_message), "message.created")
            return

        if event_type == "ai_chunk":
            chunk = str(event.get("content") or "")
            if not chunk:
                return
            state["ai_buffer"] = state["ai_buffer"] + chunk
            flush_ai(force=False)

    return on_progress, flush_ai, state


def _normalize_tool_output(tool_output: object) -> tuple[str | None, str]:
    if isinstance(tool_output, dict):
        tool_name = tool_output.get("tool_name")
        content = tool_output.get("content") or ""
        return (str(tool_name) if tool_name else None, str(content))
    text = str(tool_output or "")
    return (None, text)


def _format_exception(exc: Exception) -> str:
    msg = str(exc).strip()
    exc_name = exc.__class__.__name__
    if msg:
        return f"{exc_name}: {msg}"

    cause = getattr(exc, "__cause__", None)
    if cause:
        cause_msg = str(cause).strip()
        cause_name = cause.__class__.__name__
        if cause_msg:
            return f"{exc_name} (caused by {cause_name}: {cause_msg})"
        return f"{exc_name} (caused by {cause_name})"

    tb = "".join(traceback.format_exception_only(exc.__class__, exc)).strip()
    return tb or exc_name


def _user_from_token(token: str | None, db: Session) -> User:
    if not token:
        raise HTTPException(status_code=401, detail="Token is required")
    try:
        payload = decode_access_token(token)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=401, detail="Invalid token") from exc
    user = db.get(User, payload.get("sub"))
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    if user.is_blocked:
        raise HTTPException(status_code=403, detail="User is blocked")
    return user


def _resume_interrupt_and_persist(
    conversation: Conversation,
    interrupt_id: str,
    decision: str,
    user_id: str,
    db: Session,
) -> dict:
    started = perf_counter()
    on_progress, flush_ai, _state = _make_progress_handler(conversation.id, user_id, db, started)
    try:
        result = deepagent_service.resume_interrupt(
            conversation_id=conversation.id,
            interrupt_id=interrupt_id,
            decision=decision,
            on_progress=on_progress,
        )
        elapsed_ms = int((perf_counter() - started) * 1000)

        if result.get("interrupted"):
            flush_ai(
                force=True,
                final_status="streaming",
                tokens={
                    "input_tokens": int(result.get("input_tokens", 0) or 0),
                    "output_tokens": int(result.get("output_tokens", 0) or 0),
                    "total_tokens": int(result.get("total_tokens", 0) or 0),
                },
                run_duration_ms=elapsed_ms,
            )

            interrupt_text = deepagent_service.format_interrupt_message(result["interrupts"])
            conversation.pending_interrupt_id = result["interrupt_id"]
            db.add(conversation)
            interrupt_message = ConversationMessage(
                conversation_id=conversation.id,
                sender_user_id=None,
                sender_role="assistant",
                message_type="SystemMessage",
                message_status="pending",
                content_md=interrupt_text,
                input_tokens=0,
                output_tokens=0,
                total_tokens=0,
                run_duration_ms=elapsed_ms,
            )
            db.add(interrupt_message)
            db.commit()
            db.refresh(interrupt_message)
            _publish_messages(conversation.id, user_id, [_serialize_message(interrupt_message)])
            return {
                "accepted": True,
                "requires_interrupt_decision": True,
                "interrupt_id": result["interrupt_id"],
            }

        ai_status = "done"
        ai_type = "AIMessage"
        if result.get("rejected"):
            ai_status = "cancelled"
            ai_type = "SystemMessage"

        ai_message = flush_ai(
            force=True,
            final_status=ai_status,
            tokens={
                "input_tokens": int(result.get("input_tokens", 0) or 0),
                "output_tokens": int(result.get("output_tokens", 0) or 0),
                "total_tokens": int(result.get("total_tokens", 0) or 0),
            },
            run_duration_ms=elapsed_ms,
        )
        if not ai_message:
            assistant_message = ConversationMessage(
                conversation_id=conversation.id,
                sender_user_id=None,
                sender_role="assistant",
                message_type=ai_type,
                message_status=ai_status,
                content_md=result["answer"],
                input_tokens=int(result.get("input_tokens", 0) or 0),
                output_tokens=int(result.get("output_tokens", 0) or 0),
                total_tokens=int(result.get("total_tokens", 0) or 0),
                run_duration_ms=elapsed_ms,
            )
            db.add(assistant_message)
            db.commit()
            db.refresh(assistant_message)
            _publish_messages(conversation.id, user_id, [_serialize_message(assistant_message)])
        conversation.pending_interrupt_id = None
        db.add(conversation)
        db.commit()

        return {
            "accepted": True,
        }
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        err = _format_exception(exc)
        error_message = ConversationMessage(
            conversation_id=conversation.id,
            sender_user_id=None,
            sender_role="assistant",
            message_type="SystemMessage",
            message_status="failed",
            content_md=t("system.resume_failed", error=err),
            run_duration_ms=int((perf_counter() - started) * 1000),
        )
        db.add(error_message)
        db.commit()
        db.refresh(error_message)
        _publish_messages(conversation.id, user_id, [_serialize_message(error_message)])

        return {
            "accepted": False,
            "error": err,
            "message": _serialize_message(error_message),
        }


def _resume_interrupt_background(conversation_id: str, interrupt_id: str, decision: str, user_id: str) -> None:
    from backend.core.database import SessionLocal

    with SessionLocal() as db:
        conversation = db.get(Conversation, conversation_id)
        if not conversation:
            return
        settings = get_settings()
        docker_cfg = (settings.model_extra or {}).get("docker", {}) or {}
        daemon_cfg = _ensure_daemon_for_conversation(conversation, docker_cfg, db)
        deepagent_service.set_conversation_daemon(conversation_id, daemon_cfg)
        try:
            _resume_interrupt_and_persist(conversation, interrupt_id, decision, user_id, db)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Background resume interrupt failed: %s", exc)


def _resolve_workspace_root() -> tuple[Path, str]:
    settings = get_settings()
    extra = settings.model_extra or {}
    docker_cfg = extra.get("docker", {}) or {}
    workspace_root = Path(str(docker_cfg.get("workspace_root", "./workspaces"))).expanduser()
    if not workspace_root.is_absolute():
        workspace_root = (Path.cwd() / workspace_root).resolve()
    workdir_alias = docker_cfg.get("workdir", "/workspace") or "/workspace"
    workdir_alias = "/" + str(workdir_alias).strip("/")
    return workspace_root, workdir_alias


def _sanitize_filename(filename: str) -> tuple[str, str]:
    raw_name = Path(filename).name
    base, ext = os.path.splitext(raw_name)
    ext = ext.lower()
    # Allow UTF-8 names but block path separators/control chars.
    base = base.replace("/", "_").replace("\\", "_").replace("\x00", "_")
    base = re.sub(r"[\x00-\x1f\x7f]", "_", base)
    base = re.sub(r"[<>:\"|?*]", "_", base)
    base = re.sub(r"\s+", " ", base).strip()
    base = base.strip(".")
    if len(base) > 80:
        base = base[:80].rstrip()
    if not base:
        base = "upload"
    return base, ext


def _merge_daemon_cfg(entry: dict, docker_cfg: dict) -> dict:
    merged = dict(entry)
    if "daemon_workspace_root" not in merged:
        if "workspace_root" in merged:
            merged["daemon_workspace_root"] = merged.get("workspace_root")
        elif docker_cfg.get("daemon_workspace_root"):
            merged["daemon_workspace_root"] = docker_cfg.get("daemon_workspace_root")
    if "daemon_host" not in merged and merged.get("host"):
        merged["daemon_host"] = merged.get("host")
    if "host" not in merged and merged.get("daemon_host"):
        merged["host"] = merged.get("daemon_host")
    if "tls" not in merged and docker_cfg.get("tls"):
        merged["tls"] = docker_cfg.get("tls")
    return merged


def _daemon_hosts_from_cfg(docker_cfg: dict) -> list[dict]:
    hosts = docker_cfg.get("daemon_hosts")
    if isinstance(hosts, list):
        return [h for h in hosts if isinstance(h, dict)]
    host = docker_cfg.get("daemon_host")
    if host:
        return [
            _merge_daemon_cfg(
                {"name": "default", "host": str(host), "workspace_root": docker_cfg.get("daemon_workspace_root")},
                docker_cfg,
            )
        ]
    return []


def _pick_daemon(docker_cfg: dict) -> dict | None:
    hosts = [h for h in _daemon_hosts_from_cfg(docker_cfg) if h.get("host")]
    if not hosts:
        return None
    choice = random.choice(hosts)
    return _merge_daemon_cfg(choice, docker_cfg)


def _lookup_daemon(docker_cfg: dict, host: str | None) -> dict | None:
    if not host:
        return None
    for entry in _daemon_hosts_from_cfg(docker_cfg):
        if str(entry.get("host")) == str(host):
            return _merge_daemon_cfg(entry, docker_cfg)
    return None


def _ensure_daemon_for_conversation(
    conversation: Conversation,
    docker_cfg: dict,
    db: Session,
) -> dict | None:
    daemon_cfg = _lookup_daemon(docker_cfg, conversation.daemon_host)
    if daemon_cfg:
        return daemon_cfg
    daemon_cfg = _pick_daemon(docker_cfg)
    if daemon_cfg and daemon_cfg.get("host"):
        conversation.daemon_host = str(daemon_cfg.get("host"))
        db.add(conversation)
        db.commit()
        db.refresh(conversation)
    return daemon_cfg


@router.get("")
def list_conversations(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    conversations = db.scalars(
        select(Conversation)
        .where(Conversation.user_id == current_user.id)
        .order_by(desc(Conversation.is_pinned), desc(Conversation.pinned_at), desc(Conversation.updated_at))
    ).all()
    return {"items": [_serialize_conversation(c) for c in conversations]}


@router.post("")
def create_conversation(
    payload: CreateConversationRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    settings = get_settings()
    docker_cfg = (settings.model_extra or {}).get("docker", {}) or {}
    daemon_cfg = _pick_daemon(docker_cfg)
    conversation = Conversation(
        user_id=current_user.id,
        title=payload.title,
        model_name=payload.model,
        container_status="running",
        daemon_host=daemon_cfg.get("host") if daemon_cfg else None,
    )
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    try:
        deepagent_service.set_conversation_daemon(conversation.id, daemon_cfg)
        deepagent_service.prepare_conversation_skills(conversation.id, current_user.id, db=db)
        deepagent_service.ensure_conversation_ready(conversation.id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("prepare_conversation_skills failed: %s", exc)

    return {
        **_serialize_conversation(conversation),
    }


@router.post("/{conversation_id}/refresh_skills")
def refresh_conversation_skills(
    conversation_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    conversation = db.get(Conversation, conversation_id)
    if not conversation or conversation.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Conversation not found")
    tool_names = deepagent_service.prepare_conversation_skills(conversation_id, current_user.id, db=db)
    return {"refreshed": True, "skill_count": len(tool_names)}


@router.patch("/{conversation_id}")
def update_conversation(
    conversation_id: str,
    payload: UpdateConversationRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    conversation = db.get(Conversation, conversation_id)
    if not conversation or conversation.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Conversation not found")
    settings = get_settings()
    docker_cfg = (settings.model_extra or {}).get("docker", {}) or {}
    daemon_cfg = _ensure_daemon_for_conversation(conversation, docker_cfg, db)
    deepagent_service.set_conversation_daemon(conversation_id, daemon_cfg)

    if payload.title is None and payload.is_pinned is None:
        raise HTTPException(status_code=400, detail="No updatable fields provided")

    if payload.title is not None:
        title = payload.title.strip()
        if not title:
            raise HTTPException(status_code=400, detail="title cannot be empty")
        conversation.title = title

    if payload.is_pinned is not None:
        conversation.is_pinned = bool(payload.is_pinned)
        conversation.pinned_at = datetime.now(timezone.utc) if conversation.is_pinned else None

    db.commit()
    db.refresh(conversation)
    return _serialize_conversation(conversation)


@router.delete("/{conversation_id}")
def delete_conversation(
    conversation_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    conversation = db.get(Conversation, conversation_id)
    if not conversation or conversation.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Conversation not found")

    settings = get_settings()
    docker_cfg = (settings.model_extra or {}).get("docker", {}) or {}
    daemon_cfg = _ensure_daemon_for_conversation(conversation, docker_cfg, db)
    deepagent_service.set_conversation_daemon(conversation_id, daemon_cfg)
    threading.Thread(
        target=deepagent_service.cleanup_conversation,
        args=(conversation_id,),
        daemon=True,
    ).start()

    db.delete(conversation)
    db.commit()
    workspace_deleted = False
    workspace_error = None
    skills_deleted = False
    skills_error = None
    try:
        workspace_root, _ = _resolve_workspace_root()
        workspace_dir = (workspace_root / conversation_id).resolve()
        if workspace_dir.exists() and str(workspace_dir).startswith(str(workspace_root.resolve())):
            shutil.rmtree(workspace_dir)
        workspace_deleted = True
    except Exception as exc:  # noqa: BLE001
        workspace_error = _format_exception(exc)
    try:
        skills_root = Path(get_settings().skill_storage.conversationskills_dir).expanduser()
        if not skills_root.is_absolute():
            skills_root = (Path.cwd() / skills_root).resolve()
        skills_dir = (skills_root / conversation_id).resolve()
        if skills_dir.exists() and str(skills_dir).startswith(str(skills_root)):
            shutil.rmtree(skills_dir)
        skills_deleted = True
    except Exception as exc:  # noqa: BLE001
        skills_error = _format_exception(exc)

    return {
        "deleted": True,
        "id": conversation_id,
        "workspace_deleted": workspace_deleted,
        "workspace_error": workspace_error,
        "skills_deleted": skills_deleted,
        "skills_error": skills_error,
    }


@router.get("/{conversation_id}/messages")
def get_messages(
    conversation_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    conversation = db.get(Conversation, conversation_id)
    if not conversation or conversation.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Conversation not found")
    settings = get_settings()
    docker_cfg = (settings.model_extra or {}).get("docker", {}) or {}
    daemon_cfg = _ensure_daemon_for_conversation(conversation, docker_cfg, db)
    deepagent_service.set_conversation_daemon(conversation_id, daemon_cfg)
    try:
        deepagent_service.ensure_conversation_ready(conversation_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ensure_conversation_ready failed: %s", exc)

    messages = db.scalars(
        select(ConversationMessage)
        .where(ConversationMessage.conversation_id == conversation_id)
        .order_by(ConversationMessage.created_at.asc())
    ).all()
    return {"items": [_serialize_message(m) for m in messages]}


@router.post("/{conversation_id}/messages")
def send_message(
    conversation_id: str,
    payload: SendMessageRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    conversation = db.get(Conversation, conversation_id)
    if not conversation or conversation.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Conversation not found")
    settings = get_settings()
    docker_cfg = (settings.model_extra or {}).get("docker", {}) or {}
    daemon_cfg = _ensure_daemon_for_conversation(conversation, docker_cfg, db)
    deepagent_service.set_conversation_daemon(conversation_id, daemon_cfg)

    human_message = ConversationMessage(
        conversation_id=conversation_id,
        sender_user_id=current_user.id,
        sender_role="human",
        message_type="human_text",
        message_status="done",
        content_md=payload.content,
    )
    db.add(human_message)
    db.commit()
    db.refresh(human_message)
    _publish_messages(conversation_id, current_user.id, [_serialize_message(human_message)])

    started = perf_counter()
    on_progress, flush_ai, _state = _make_progress_handler(conversation_id, current_user.id, db, started)

    try:
        result = deepagent_service.run_turn(
            conversation_id=conversation_id,
            content=payload.content,
            on_progress=on_progress,
        )
        elapsed_ms = int((perf_counter() - started) * 1000)

        if result.get("interrupted"):
            flush_ai(
                force=True,
                final_status="streaming",
                tokens={
                    "input_tokens": int(result.get("input_tokens", 0) or 0),
                    "output_tokens": int(result.get("output_tokens", 0) or 0),
                    "total_tokens": int(result.get("total_tokens", 0) or 0),
                },
                run_duration_ms=elapsed_ms,
            )

            interrupt_text = deepagent_service.format_interrupt_message(result["interrupts"])
            conversation.pending_interrupt_id = result["interrupt_id"]
            db.add(conversation)
            interrupt_message = ConversationMessage(
                conversation_id=conversation_id,
                sender_user_id=None,
                sender_role="assistant",
                message_type="SystemMessage",
                message_status="pending",
                content_md=interrupt_text,
                input_tokens=0,
                output_tokens=0,
                total_tokens=0,
                run_duration_ms=elapsed_ms,
            )
            db.add(interrupt_message)
            db.commit()
            db.refresh(interrupt_message)
            _publish_messages(conversation_id, current_user.id, [_serialize_message(interrupt_message)])
            return {
                "accepted": True,
                "requires_interrupt_decision": True,
                "interrupt_id": result["interrupt_id"],
            }

        ai_message = flush_ai(
            force=True,
            final_status="done",
            tokens={
                "input_tokens": int(result.get("input_tokens", 0) or 0),
                "output_tokens": int(result.get("output_tokens", 0) or 0),
                "total_tokens": int(result.get("total_tokens", 0) or 0),
            },
            run_duration_ms=elapsed_ms,
        )
        if not ai_message:
            assistant_message = ConversationMessage(
                conversation_id=conversation_id,
                sender_user_id=None,
                sender_role="assistant",
                message_type="AIMessage",
                message_status="done",
                content_md=result["answer"],
                input_tokens=int(result.get("input_tokens", 0) or 0),
                output_tokens=int(result.get("output_tokens", 0) or 0),
                total_tokens=int(result.get("total_tokens", 0) or 0),
                run_duration_ms=elapsed_ms,
            )
            db.add(assistant_message)
            db.commit()
            db.refresh(assistant_message)
            _publish_messages(conversation_id, current_user.id, [_serialize_message(assistant_message)])

        conversation.pending_interrupt_id = None
        db.add(conversation)
        db.commit()

        return {
            "accepted": True,
            "requires_interrupt_decision": False,
        }
    except Exception as exc:  # noqa: BLE001
        err = _format_exception(exc)
        error_message = ConversationMessage(
            conversation_id=conversation_id,
            sender_user_id=None,
            sender_role="assistant",
            message_type="SystemMessage",
            message_status="failed",
            content_md=t("system.deepagents_failed", error=err),
            run_duration_ms=int((perf_counter() - started) * 1000),
        )
        db.add(error_message)
        db.commit()
        db.refresh(error_message)

        return {
            "accepted": False,
            "error": err,
            "message": _serialize_message(error_message),
        }


@router.post("/{conversation_id}/interrupts/{interrupt_id}/decision")
def decide_interrupt(
    conversation_id: str,
    interrupt_id: str,
    payload: InterruptDecisionRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    conversation = db.get(Conversation, conversation_id)
    if not conversation or conversation.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Conversation not found")
    settings = get_settings()
    docker_cfg = (settings.model_extra or {}).get("docker", {}) or {}
    daemon_cfg = _ensure_daemon_for_conversation(conversation, docker_cfg, db)
    deepagent_service.set_conversation_daemon(conversation_id, daemon_cfg)

    decision_message = ConversationMessage(
        conversation_id=conversation_id,
        sender_user_id=current_user.id,
        sender_role="human",
        message_type="human_interrupt_decision",
        message_status="done",
        content_md=payload.decision,
    )
    db.add(decision_message)
    db.commit()
    db.refresh(decision_message)
    _publish_messages(conversation_id, current_user.id, [_serialize_message(decision_message)])

    if payload.decision == "allow_all":
        conversation.pending_interrupt_id = None
        db.add(conversation)
        db.commit()
        auto_message = ConversationMessage(
            conversation_id=conversation_id,
            sender_user_id=None,
            sender_role="assistant",
            message_type="SystemMessage",
            message_status="pending",
            content_md=t("system.allow_all_notice"),
        )
        db.add(auto_message)
        db.commit()
        db.refresh(auto_message)
        _publish_messages(conversation_id, current_user.id, [_serialize_message(auto_message)])
        threading.Thread(
            target=_resume_interrupt_background,
            args=(conversation_id, interrupt_id, payload.decision, current_user.id),
            daemon=True,
        ).start()
        return {
            "accepted": True,
            "queued": True,
        }

    return _resume_interrupt_and_persist(conversation, interrupt_id, payload.decision, current_user.id, db)


@router.get("/{conversation_id}/interrupts/pending")
def get_pending_interrupt(
    conversation_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    conversation = db.get(Conversation, conversation_id)
    if not conversation or conversation.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"interrupt_id": conversation.pending_interrupt_id}


@router.get("/{conversation_id}/events")
def stream_conversation_events(
    conversation_id: str,
    token: str | None = None,
    db: Session = Depends(get_db),
) -> StreamingResponse:
    current_user = _user_from_token(token, db)
    conversation = db.get(Conversation, conversation_id)
    if not conversation or conversation.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Conversation not found")
    settings = get_settings()
    docker_cfg = (settings.model_extra or {}).get("docker", {}) or {}
    daemon_cfg = _ensure_daemon_for_conversation(conversation, docker_cfg, db)
    deepagent_service.set_conversation_daemon(conversation_id, daemon_cfg)
    try:
        deepagent_service.ensure_conversation_ready(conversation_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ensure_conversation_ready failed: %s", exc)

    try:
        subscriber_id, q = stream_event_publisher.subscribe(conversation_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    def event_stream():
        try:
            connected_payload = {
                "event_type": "system.connected",
                "payload": {"conversation_id": conversation_id},
                "ts": str(int(datetime.now(timezone.utc).timestamp() * 1000)),
            }
            yield f"event: system.connected\ndata: {json.dumps(connected_payload, ensure_ascii=False)}\n\n"
            while True:
                if stream_event_publisher.is_shutdown():
                    break
                try:
                    item = q.get(timeout=15)
                except queue.Empty:
                    yield ": ping\n\n"
                    continue
                if item.get("event_type") == "system.shutdown":
                    break
                if item.get("user_id") != current_user.id:
                    continue
                data = {
                    "event_id": item.get("event_id"),
                    "event_type": item.get("event_type"),
                    "payload": item.get("payload"),
                    "ts": item.get("ts"),
                }
                event_type = str(item.get("event_type") or "message")
                yield f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
        finally:
            stream_event_publisher.unsubscribe(conversation_id, subscriber_id)

    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=headers)


@router.post("/{conversation_id}/debug/exec")
def debug_exec(
    conversation_id: str,
    payload: DebugExecRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    conversation = db.get(Conversation, conversation_id)
    if not conversation or conversation.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Conversation not found")
    settings = get_settings()
    docker_cfg = (settings.model_extra or {}).get("docker", {}) or {}
    daemon_cfg = _ensure_daemon_for_conversation(conversation, docker_cfg, db)
    deepagent_service.set_conversation_daemon(conversation_id, daemon_cfg)

    return deepagent_service.debug_exec(conversation_id=conversation_id, command=payload.command)


@router.post("/{conversation_id}/attachments")
def upload_attachment(
    conversation_id: str,
    file: UploadFile = File(...),
    convert_to_markdown: bool = Form(True),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    conversation = db.get(Conversation, conversation_id)
    if not conversation or conversation.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if not file or not file.filename:
        raise HTTPException(status_code=400, detail="No file uploaded")

    base, ext = _sanitize_filename(file.filename)
    if ext not in ALLOWED_OFFICE_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_OFFICE_EXTENSIONS))
        raise HTTPException(status_code=400, detail=f"Only office files are supported: {allowed}")

    workspace_root, workdir_alias = _resolve_workspace_root()
    workspace_dir = (workspace_root / conversation_id / "uploads").resolve()
    workspace_dir.mkdir(parents=True, exist_ok=True)

    unique = uuid4().hex[:8]
    saved_name = f"{base}_{unique}{ext}"
    target_path = (workspace_dir / saved_name).resolve()
    if not str(target_path).startswith(str(workspace_dir)):
        raise HTTPException(status_code=400, detail="Invalid upload path")

    data = file.file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    max_bytes = 20 * 1024 * 1024
    if len(data) > max_bytes:
        raise HTTPException(status_code=400, detail="File too large (max 20MB)")

    target_path.write_bytes(data)

    markdown_info = None
    if convert_to_markdown:
        md_name = f"{base}_{unique}.md"
        md_path = (workspace_dir / md_name).resolve()
        try:
            result = extract_office_to_markdown(target_path)
            md_path.write_text(result.markdown, encoding="utf-8")
            markdown_info = {
                "source_type": result.source_type,
                "workspace_path": f"{workdir_alias}/uploads/{md_name}",
                "char_count": len(result.markdown),
                "warnings": result.warnings,
            }
        except Exception as exc:  # noqa: BLE001
            markdown_info = {
                "source_type": "unknown",
                "error": _format_exception(exc),
            }

    attachment = {
        "original_name": file.filename,
        "saved_name": saved_name,
        "content_type": file.content_type,
        "size_bytes": len(data),
        "workspace_path": f"{workdir_alias}/uploads/{saved_name}",
        "markdown": markdown_info,
    }

    message = ConversationMessage(
        conversation_id=conversation_id,
        sender_user_id=current_user.id,
        sender_role="user",
        message_type="human_attachment",
        message_status="done",
        content_md=t("system.attachment_uploaded", filename=file.filename),
        attachments_json={"items": [attachment]},
    )
    db.add(message)
    db.commit()
    db.refresh(message)

    serialized = _serialize_message(message)
    _publish_messages(conversation_id, current_user.id, [serialized])

    return {"message": serialized, "attachment": attachment}


@router.get("/{conversation_id}/attachments")
def list_attachments(
    conversation_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    conversation = db.get(Conversation, conversation_id)
    if not conversation or conversation.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Conversation not found")

    messages = db.scalars(
        select(ConversationMessage)
        .where(ConversationMessage.conversation_id == conversation_id)
        .where(ConversationMessage.attachments_json.is_not(None))
        .order_by(desc(ConversationMessage.created_at))
    ).all()

    items: list[dict] = []
    for msg in messages:
        created_at = msg.created_at
        if created_at is None:
            created_at = datetime.now(timezone.utc)
        for att in _extract_attachments(msg.attachments_json):
            entry = dict(att)
            entry["uploaded_at"] = created_at.isoformat()
            entry["message_id"] = msg.id
            items.append(entry)

    return {"items": items}


@router.get("/{conversation_id}/attachments/download")
def download_attachment(
    conversation_id: str,
    path: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conversation = db.get(Conversation, conversation_id)
    if not conversation or conversation.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Conversation not found")

    raw = str(path or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="path is required")

    workspace_root, workdir_alias = _resolve_workspace_root()
    workspace_dir = (workspace_root / conversation_id).resolve()

    if raw.startswith(workdir_alias):
        rel = raw[len(workdir_alias):].lstrip("/")
    else:
        rel = raw.lstrip("/")

    if not rel or ".." in Path(rel).parts:
        raise HTTPException(status_code=400, detail="Invalid path")

    if not rel.startswith("uploads/"):
        raise HTTPException(status_code=400, detail="Invalid path")

    target = (workspace_dir / rel).resolve()
    if not str(target).startswith(str(workspace_dir)):
        raise HTTPException(status_code=400, detail="Invalid path")
    if not target.exists():
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(path=str(target), filename=target.name, media_type="application/octet-stream")


@router.get("/{conversation_id}/attachments/markdown")
def get_attachment_markdown(
    conversation_id: str,
    path: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    conversation = db.get(Conversation, conversation_id)
    if not conversation or conversation.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Conversation not found")

    raw = str(path or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="path is required")

    workspace_root, workdir_alias = _resolve_workspace_root()
    workspace_dir = (workspace_root / conversation_id).resolve()

    if raw.startswith(workdir_alias):
        rel = raw[len(workdir_alias):].lstrip("/")
    else:
        rel = raw.lstrip("/")

    if not rel or ".." in Path(rel).parts:
        raise HTTPException(status_code=400, detail="Invalid path")

    if not rel.startswith("uploads/"):
        raise HTTPException(status_code=400, detail="Invalid path")

    target = (workspace_dir / rel).resolve()
    if not str(target).startswith(str(workspace_dir)):
        raise HTTPException(status_code=400, detail="Invalid path")
    if target.suffix.lower() != ".md":
        raise HTTPException(status_code=400, detail="Only markdown files are allowed")
    if not target.exists():
        raise HTTPException(status_code=404, detail="File not found")

    data = target.read_bytes()
    max_bytes = 500_000
    truncated = len(data) > max_bytes
    if truncated:
        data = data[:max_bytes]
    content = data.decode("utf-8", errors="replace")

    return {"path": raw, "content": content, "truncated": truncated}
