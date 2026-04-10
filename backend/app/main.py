from __future__ import annotations

import asyncio
import datetime as dt
import gzip
import logging
import os
import shutil
from collections import deque
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError
from sqlalchemy import func, select

from app.agent_runtime import REPO_ROOT, WebAgentRuntime
from app.checkpointer import RuntimeCheckpointer, create_postgres_checkpointer
from app.config_loader import discover_config_path, load_teamclaw_config
from app.crud import add_audit_log, conversation_workspace_path, now_utc
from app.db import create_schema, init_database, session_factory
from app.deps import authenticate_websocket_user, find_conversation_for_user, validate_and_touch_user_session
from app.i18n import tr_app
from app.orm_models import Message, MessageAttachment, SandboxInstance, ToolEvent, User
from app.routers_admin import router as admin_router
from app.routers_auth import me_router, router as auth_router
from app.routers_conversations import router as conversations_router
from app.security import access_token_ttl_seconds, refresh_token_ttl_days
from app.scheduled_tasks import ScheduledTaskWorker
from app.schemas import ChatRequest

logger = logging.getLogger(__name__)
startup_logger = logging.getLogger("uvicorn.error")
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".tif"})
INTERRUPTED_MESSAGE_MARKER = "[interrupted]"
TEAMCLAW_ASCII_LOGO = r"""
  _______                    _____ _                
 |__   __|                  / ____| |               
    | | ___  __ _ _ __ ___ | |    | | __ ___      __
    | |/ _ \/ _` | '_ ` _ \| |    | |/ _` \ \ /\ / /
    | |  __/ (_| | | | | | | |____| | (_| |\ V  V / 
    |_|\___|\__,_|_| |_| |_|\_____|_|\__,_| \_/\_/  
"""


def _create_daily_gzip_file_handler(
    *,
    logs_dir: Path,
    formatter: logging.Formatter,
    backup_count: int = 30,
) -> TimedRotatingFileHandler:
    logs_dir.mkdir(parents=True, exist_ok=True)
    handler = TimedRotatingFileHandler(
        filename=str((logs_dir / "backend.log").resolve()),
        when="midnight",
        interval=1,
        backupCount=backup_count,
        encoding="utf-8",
        utc=False,
    )
    handler.suffix = "%Y-%m-%d"
    handler.setFormatter(formatter)

    def _namer(default_name: str) -> str:
        return f"{default_name}.gz"

    def _rotator(source: str, dest: str) -> None:
        with open(source, "rb") as src, gzip.open(dest, "wb") as dst:
            shutil.copyfileobj(src, dst)
        os.remove(source)

    handler.namer = _namer
    handler.rotator = _rotator
    return handler


def _has_file_handler(target: logging.Logger, *, log_file: Path) -> bool:
    normalized = str(log_file.resolve())
    for handler in target.handlers:
        if isinstance(handler, TimedRotatingFileHandler):
            base_name = getattr(handler, "baseFilename", "")
            if base_name and os.path.abspath(base_name) == normalized:
                return True
    return False


def _configure_log_datetime_format(*, logs_dir: Path) -> None:
    log_format = "[%(levelname)s] [%(asctime)s] %(message)s"
    date_format = "%m-%d %H:%M:%S"
    formatter = logging.Formatter(log_format, datefmt=date_format)
    backend_log_file = (logs_dir / "backend.log").resolve()

    uvicorn_error_logger = logging.getLogger("uvicorn.error")
    uvicorn_access_logger = logging.getLogger("uvicorn.access")
    target_loggers = [logging.getLogger(), uvicorn_error_logger, uvicorn_access_logger]
    applied = False
    for target in target_loggers:
        for handler in target.handlers:
            handler.setFormatter(formatter)
            applied = True
    if not applied and not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format=log_format, datefmt=date_format)

    for target in target_loggers:
        if _has_file_handler(target, log_file=backend_log_file):
            continue
        file_handler = _create_daily_gzip_file_handler(logs_dir=logs_dir, formatter=formatter)
        target.addHandler(file_handler)

    uvicorn_error_logger.propagate = False
    uvicorn_access_logger.propagate = False


def _emit_startup_banner(version: str) -> None:
    banner = TEAMCLAW_ASCII_LOGO.rstrip("\n")
    lines = [f"\n{banner}", f"TeamClaw {version} starting"]
    logged = False
    for line in lines:
        try:
            startup_logger.info(line)
            logged = True
        except Exception:
            continue
    # Safety fallback for cases where logger handlers/levels hide app logs.
    if not logged:
        for line in lines:
            print(line, flush=True)


def _is_image_attachment(*, mime_type: str | None, suffix: str) -> bool:
    if isinstance(mime_type, str) and mime_type.strip().lower().startswith("image/"):
        return True
    return suffix.lower() in IMAGE_SUFFIXES


async def _ensure_active_admin_exists() -> None:
    async with session_factory()() as db:
        active_admin_count = int(
            (
                await db.execute(
                    select(func.count()).select_from(User).where(
                        User.is_admin.is_(True),
                        User.is_blocked.is_(False),
                    )
                )
            ).scalar_one()
            or 0
        )
        if active_admin_count > 0:
            return

        first_user = (
            await db.execute(
                select(User).order_by(User.created_at.asc(), User.id.asc()).limit(1)
            )
        ).scalar_one_or_none()
        if first_user is None:
            return

        first_user.is_admin = True
        first_user.is_blocked = False
        await add_audit_log(
            db,
            actor_user_id=None,
            action="system.ensure_admin",
            target_type="user",
            target_id=first_user.id,
            result="success",
            detail_json={"reason": "no_active_admin_on_startup"},
        )
        await db.commit()
        logger.warning(
            "No active admin found at startup. Promoted first user '%s' (%s) to active admin.",
            first_user.username,
            first_user.id,
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    _configure_log_datetime_format(logs_dir=REPO_ROOT / "logs")
    config_path = discover_config_path(REPO_ROOT)
    loaded_config = load_teamclaw_config(config_path)
    _emit_startup_banner(loaded_config.app_version)
    # Keep all workspace writers/readers on one canonical root.
    os.environ["TEAMCLAW_WORKSPACES_ROOT"] = str(loaded_config.docker_sandbox.workspace_root)
    init_database(loaded_config.database)
    await create_schema()
    await _ensure_active_admin_exists()

    runtime_checkpointer: RuntimeCheckpointer | None = await create_postgres_checkpointer(loaded_config)
    app.state.runtime_checkpointer = runtime_checkpointer
    app.state.runtime = WebAgentRuntime(
        loaded_config,
        REPO_ROOT,
        checkpointer=runtime_checkpointer.saver if runtime_checkpointer is not None else None,
    )
    if runtime_checkpointer is not None:
        logger.info(
            "Runtime checkpointer initialized (backend=%s)",
            runtime_checkpointer.backend,
        )
    app.state.run_manager = ConversationRunManager(app=app, runtime=app.state.runtime)
    scheduled_cfg = loaded_config.scheduled_tasks
    app.state.scheduled_task_worker = None
    if scheduled_cfg.enabled:
        worker = ScheduledTaskWorker(
            runtime=app.state.runtime,
            run_manager=app.state.run_manager,
            poll_interval_seconds=scheduled_cfg.poll_interval_seconds,
            batch_size=scheduled_cfg.batch_size,
            llm_wait_timeout_seconds=scheduled_cfg.llm_wait_timeout_seconds,
            max_script_output_chars=scheduled_cfg.max_script_output_chars,
            max_summary_input_chars=scheduled_cfg.max_summary_input_chars,
            update_sandbox_instance=lambda conversation_id: _update_sandbox_instance_from_runtime(
                app=app,
                conversation_id=conversation_id,
            ),
        )
        worker.start()
        app.state.scheduled_task_worker = worker
        logger.info(
            "Scheduled task worker started (poll=%ss, batch=%s)",
            scheduled_cfg.poll_interval_seconds,
            scheduled_cfg.batch_size,
        )
    app.state.config = loaded_config
    app.state.config_path = config_path
    app.state.avatars_dir = loaded_config.avatars_dir.resolve()
    app.state.avatars_dir.mkdir(parents=True, exist_ok=True)
    logger.info(
        "Session policy loaded (idle_timeout_seconds=%s, touch_interval_seconds=%s, access_token_ttl_seconds=%s, refresh_token_ttl_days=%s)",
        loaded_config.session.idle_timeout_seconds,
        loaded_config.session.touch_interval_seconds,
        access_token_ttl_seconds(),
        refresh_token_ttl_days(),
    )
    logger.info("Loaded web-agent config from %s", config_path)
    try:
        yield
    finally:
        scheduled_task_worker = getattr(app.state, "scheduled_task_worker", None)
        if scheduled_task_worker is not None:
            await scheduled_task_worker.aclose()
        run_manager: ConversationRunManager = app.state.run_manager
        await run_manager.aclose()
        runtime: WebAgentRuntime = app.state.runtime
        await runtime.aclose()
        runtime_checkpointer = getattr(app.state, "runtime_checkpointer", None)
        if runtime_checkpointer is not None:
            await runtime_checkpointer.aclose()


app = FastAPI(title="DeepAgents Web", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(me_router)
app.include_router(admin_router)
app.include_router(conversations_router)


def _translate_validation_error_message(*, app: FastAPI, raw_message: str) -> str:
    text = str(raw_message or "").strip()
    mapping = {
        "message or attachments must be provided": "validation.message_or_attachments_required",
        "validation.message_or_attachments_required": "validation.message_or_attachments_required",
        "Password must include uppercase, lowercase, number, and special character.": "validation.password_policy",
        "Password must be 8-256 chars and include uppercase, lowercase, number, and special character.": "validation.password_policy",
        "validation.password_policy": "validation.password_policy",
    }
    key = mapping.get(text)
    if key is None:
        return text
    return tr_app(app, key)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    translated_errors: list[dict[str, Any]] = []
    for item in exc.errors():
        err = dict(item)
        err["msg"] = _translate_validation_error_message(
            app=request.app,
            raw_message=str(item.get("msg") or ""),
        )
        translated_errors.append(err)
    return JSONResponse(status_code=422, content={"detail": translated_errors})


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/models")
async def list_models() -> dict[str, object]:
    runtime: WebAgentRuntime = app.state.runtime
    return runtime.list_models()


@app.get("/api/config-path")
async def config_path() -> dict[str, str]:
    cfg_path: Path = app.state.config_path
    return {"config_path": str(cfg_path)}


@app.get("/api/settings")
async def app_settings() -> dict[str, object]:
    cfg = app.state.config
    return {
        "version": cfg.app_version,
        "language": cfg.language,
        "supported_languages": list(cfg.supported_languages),
        "sandbox_timezone": cfg.sandbox_timezone,
        "default_user_conversation_limit": cfg.default_user_conversation_limit,
    }


@app.get("/api/v1/avatars/{file_name}")
async def get_avatar_file(file_name: str) -> FileResponse:
    avatars_dir = Path(app.state.avatars_dir).resolve()
    safe_name = Path(file_name).name
    candidate = (avatars_dir / safe_name).resolve()
    if avatars_dir not in candidate.parents or not candidate.exists() or not candidate.is_file():
        raise HTTPException(status_code=404, detail=tr_app(app, "avatar.not_found"))
    return FileResponse(candidate)


async def _update_sandbox_instance_from_runtime(
    *,
    app: FastAPI,
    conversation_id: str,
) -> None:
    runtime: WebAgentRuntime = app.state.runtime
    info = await runtime.get_sandbox_info(conversation_id)
    if not info:
        return

    async with session_factory()() as db:
        sandbox = (
            await db.execute(select(SandboxInstance).where(SandboxInstance.conversation_id == conversation_id))
        ).scalar_one_or_none()
        if sandbox is None:
            return

        previous_container_id = sandbox.container_id
        current_container_id = info.get("container_id")
        sandbox.container_id = current_container_id
        sandbox.container_name = info.get("container_name")
        sandbox.docker_host = info.get("docker_host") or info.get("client_name")
        sandbox.status = "running"
        heartbeat_now = now_utc()
        # When container id changes, treat it as a new sandbox container lifecycle.
        if current_container_id and current_container_id != previous_container_id:
            sandbox.created_at = heartbeat_now
        sandbox.last_heartbeat_at = heartbeat_now
        await db.commit()


@dataclass(slots=True)
class _RunSubscription:
    conversation_id: str
    user_id: str
    queue: asyncio.Queue[dict[str, Any]]


@dataclass
class _ConversationRunState:
    user_id: str
    running: bool = False
    task: asyncio.Task[None] | None = None
    next_seq: int = 1
    events: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=4096))
    subscribers: set[asyncio.Queue[dict[str, Any]]] = field(default_factory=set)


class ConversationRunManager:
    def __init__(self, *, app: FastAPI, runtime: WebAgentRuntime) -> None:
        self._app = app
        self._runtime = runtime
        self._lock = asyncio.Lock()
        self._runs: dict[str, _ConversationRunState] = {}

    async def aclose(self) -> None:
        tasks: list[asyncio.Task[None]] = []
        async with self._lock:
            for run in self._runs.values():
                if run.task is not None and not run.task.done():
                    run.task.cancel()
                    tasks.append(run.task)

        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task

    async def is_running(self, *, conversation_id: str, user_id: str) -> bool:
        async with self._lock:
            run = self._runs.get(conversation_id)
            if run is None or run.user_id != user_id:
                return False
            return run.running

    async def subscribe(
        self,
        *,
        conversation_id: str,
        user_id: str,
        cursor: int | None,
    ) -> tuple[_RunSubscription, list[dict[str, Any]]]:
        async with self._lock:
            run = self._runs.get(conversation_id)
            if run is None:
                run = _ConversationRunState(user_id=user_id)
                self._runs[conversation_id] = run
            elif run.user_id != user_id:
                raise PermissionError("Conversation ownership mismatch")

            queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
            run.subscribers.add(queue)

            if cursor is None:
                replay_events = list(run.events) if run.running else []
            else:
                replay_events = [event for event in run.events if int(event.get("seq") or 0) > cursor]

            return _RunSubscription(conversation_id=conversation_id, user_id=user_id, queue=queue), replay_events

    async def unsubscribe(self, subscription: _RunSubscription) -> None:
        async with self._lock:
            run = self._runs.get(subscription.conversation_id)
            if run is None or run.user_id != subscription.user_id:
                return
            run.subscribers.discard(subscription.queue)

    async def start_run(
        self,
        *,
        request: ChatRequest,
        user_id: str,
        provider: str | None,
        model: str | None,
        ip_address: str | None,
        user_agent: str | None,
    ) -> bool:
        async with self._lock:
            run = self._runs.get(request.session_id)
            if run is None:
                run = _ConversationRunState(user_id=user_id)
                self._runs[request.session_id] = run
            elif run.user_id != user_id:
                return False
            if run.running:
                return False

            run.running = True
            run.events.clear()
            run.task = asyncio.create_task(
                self._execute_run(
                    request=request,
                    user_id=user_id,
                    provider=provider,
                    model=model,
                    ip_address=ip_address,
                    user_agent=user_agent,
                )
            )
        return True

    async def cancel_run(self, *, conversation_id: str, user_id: str) -> bool:
        task_to_cancel: asyncio.Task[None] | None = None
        async with self._lock:
            run = self._runs.get(conversation_id)
            if run is None or run.user_id != user_id or not run.running:
                return False
            if run.task is None or run.task.done():
                return False
            task_to_cancel = run.task

        if task_to_cancel is not None:
            task_to_cancel.cancel()
        return True

    async def publish_tool_event(
        self,
        *,
        conversation_id: str,
        tool_call_id: str,
        name: str,
        display: str,
        command: str | None = None,
        output: str | None = None,
        status: str | None = None,
    ) -> None:
        await self._publish_event(
            conversation_id=conversation_id,
            payload={
                "type": "tool_call",
                "tool_call_id": tool_call_id,
                "name": name,
                "display": display,
                "command": command,
            },
        )
        if output is not None or status is not None:
            await self._publish_event(
                conversation_id=conversation_id,
                payload={
                    "type": "tool_result",
                    "tool_call_id": tool_call_id,
                    "name": name,
                    "display": display,
                    "command": command,
                    "output": output or "",
                    "status": status or "success",
                },
            )

    async def publish_system_message(
        self,
        *,
        conversation_id: str,
        message: str,
        message_id: str | None = None,
    ) -> None:
        await self._publish_event(
            conversation_id=conversation_id,
            payload={
                "type": "system_message",
                "message": message,
                "message_id": message_id,
            },
        )

    async def _publish_event(self, *, conversation_id: str, payload: dict[str, Any]) -> None:
        async with self._lock:
            run = self._runs.get(conversation_id)
            if run is None:
                return
            seq = run.next_seq
            run.next_seq += 1

            event = dict(payload)
            event["session_id"] = conversation_id
            event["seq"] = seq
            run.events.append(event)
            subscribers = list(run.subscribers)

        for queue in subscribers:
            queue.put_nowait(event)

    async def _mark_finished(self, *, conversation_id: str) -> None:
        async with self._lock:
            run = self._runs.get(conversation_id)
            if run is None:
                return
            run.running = False
            run.task = None

    async def _execute_run(
        self,
        *,
        request: ChatRequest,
        user_id: str,
        provider: str | None,
        model: str | None,
        ip_address: str | None,
        user_agent: str | None,
    ) -> None:
        assistant_chunks: list[str] = []
        tool_events: dict[str, dict[str, Any]] = {}
        start_time = dt.datetime.now(dt.timezone.utc)
        saw_done = False
        stream_error: Exception | None = None
        interrupted = False
        workspace_root_files_before = self._snapshot_workspace_root_files(request.session_id)

        try:
            async for event in self._runtime.stream_chat(request, user_id=user_id):
                if event.get("type") == "done":
                    saw_done = True
                await self._publish_event(conversation_id=request.session_id, payload=event)

                event_type = event.get("type")
                if event_type == "text":
                    delta = event.get("delta")
                    if isinstance(delta, str):
                        assistant_chunks.append(delta)
                    continue

                if event_type == "tool_call":
                    tool_call_id = str(event.get("tool_call_id") or "")
                    tool_events[tool_call_id] = {
                        "tool_call_id": tool_call_id,
                        "tool_name": str(event.get("name") or "tool"),
                        "display_text": str(event.get("display") or ""),
                        "args_json": event.get("args") if isinstance(event.get("args"), dict) else None,
                        "command": event.get("command") if isinstance(event.get("command"), str) else None,
                        "status": "running",
                        "output_text": None,
                        "exit_code": None,
                        "started_at": now_utc(),
                        "finished_at": None,
                    }
                    continue

                if event_type == "tool_result":
                    tool_call_id = str(event.get("tool_call_id") or "")
                    entry = tool_events.get(tool_call_id)
                    if entry is None:
                        entry = {
                            "tool_call_id": tool_call_id,
                            "tool_name": str(event.get("name") or "tool"),
                            "display_text": str(event.get("display") or ""),
                            "args_json": None,
                            "command": event.get("command") if isinstance(event.get("command"), str) else None,
                            "started_at": now_utc(),
                        }
                        tool_events[tool_call_id] = entry
                    entry["status"] = str(event.get("status") or "success")
                    entry["output_text"] = str(event.get("output") or "")
                    if isinstance(event.get("exit_code"), int):
                        entry["exit_code"] = int(event["exit_code"])
                    entry["finished_at"] = now_utc()
                    continue
        except asyncio.CancelledError:
            interrupted = True
            logger.info("Background chat execution interrupted (session=%s)", request.session_id)
        except Exception as exc:  # noqa: BLE001
            stream_error = exc
            logger.exception("Background chat execution failed")
            await self._publish_event(
                conversation_id=request.session_id,
                payload={
                    "type": "error",
                    "message": tr_app(self._app, "ws.agent_execution_failed", error=exc),
                },
            )
        finally:
            if not saw_done:
                await self._publish_event(
                    conversation_id=request.session_id,
                    payload={"type": "done", "interrupted": interrupted},
                )

            duration_ms = int((dt.datetime.now(dt.timezone.utc) - start_time).total_seconds() * 1000)
            assistant_content = "".join(assistant_chunks).strip()

            try:
                await self._persist_assistant_turn(
                    request=request,
                    user_id=user_id,
                    provider=provider,
                    model=model,
                    assistant_content=assistant_content,
                    duration_ms=duration_ms,
                    tool_events=tool_events,
                    stream_error=stream_error,
                    interrupted=interrupted,
                    workspace_root_files_before=workspace_root_files_before,
                    ip_address=ip_address,
                    user_agent=user_agent,
                )
            finally:
                await self._mark_finished(conversation_id=request.session_id)
                await _update_sandbox_instance_from_runtime(app=self._app, conversation_id=request.session_id)

    async def _persist_assistant_turn(
        self,
        *,
        request: ChatRequest,
        user_id: str,
        provider: str | None,
        model: str | None,
        assistant_content: str,
        duration_ms: int,
        tool_events: dict[str, dict[str, Any]],
        stream_error: Exception | None,
        interrupted: bool,
        workspace_root_files_before: set[str],
        ip_address: str | None,
        user_agent: str | None,
    ) -> None:
        should_create_assistant_message = (
            interrupted or stream_error is None or bool(assistant_content) or bool(tool_events)
        )
        async with session_factory()() as db:
            user = await db.get(User, user_id)
            if user is None or user.is_blocked:
                return

            conversation = await find_conversation_for_user(
                db=db,
                conversation_id=request.session_id,
                user_id=user.id,
            )
            if conversation is None:
                return

            assistant_message: Message | None = None
            copied_workspace_paths: list[str] = []
            auto_copy_message: Message | None = None
            if should_create_assistant_message:
                final_content = assistant_content
                if interrupted:
                    trimmed = final_content.rstrip()
                    if trimmed.endswith(INTERRUPTED_MESSAGE_MARKER):
                        final_content = trimmed
                    elif trimmed:
                        final_content = f"{trimmed}\n\n{INTERRUPTED_MESSAGE_MARKER}"
                    else:
                        final_content = INTERRUPTED_MESSAGE_MARKER
                assistant_message = Message(
                    conversation_id=conversation.id,
                    role="assistant",
                    content=final_content,
                    provider=provider,
                    model=model,
                    duration_ms=duration_ms,
                )
                db.add(assistant_message)
                await db.flush()

            if assistant_message is not None:
                for event in tool_events.values():
                    db.add(
                        ToolEvent(
                            conversation_id=conversation.id,
                            message_id=assistant_message.id,
                            tool_call_id=event.get("tool_call_id"),
                            tool_name=str(event.get("tool_name") or "tool"),
                            display_text=event.get("display_text"),
                            args_json=event.get("args_json"),
                            command=event.get("command"),
                            output_text=event.get("output_text"),
                            status=str(event.get("status") or "success"),
                            exit_code=event.get("exit_code"),
                            started_at=event.get("started_at") or now_utc(),
                            finished_at=event.get("finished_at"),
                        )
                    )

            copied_workspace_paths = self._copy_new_workspace_root_files_to_uploads(
                conversation_id=conversation.id,
                before_file_names=workspace_root_files_before,
                mount_root=conversation.workspace_mount_path,
            )
            if copied_workspace_paths:
                preview_limit = 10
                lines = [
                    tr_app(self._app, "conversation.files.autocopy_notice"),
                ]
                for path in copied_workspace_paths[:preview_limit]:
                    lines.append(f"- `{path}`")
                remaining = len(copied_workspace_paths) - preview_limit
                if remaining > 0:
                    lines.append(
                        tr_app(
                            self._app,
                            "conversation.files.autocopy_more",
                            count=remaining,
                        )
                    )
                auto_copy_message = Message(
                    conversation_id=conversation.id,
                    role="system",
                    content="\n".join(lines),
                    provider=None,
                    model=None,
                )
                db.add(auto_copy_message)
                await db.flush()

            conversation.updated_at = now_utc()
            if request.provider:
                conversation.default_provider = request.provider
            if request.model:
                conversation.default_model = request.model

            if interrupted:
                await add_audit_log(
                    db,
                    actor_user_id=user.id,
                    action="chat.send",
                    target_type="conversation",
                    target_id=conversation.id,
                    result="interrupted",
                    detail_json={
                        "provider": request.provider,
                        "model": request.model,
                        "duration_ms": duration_ms,
                        "tool_events": len(tool_events),
                        "attachments": len(request.attachments),
                        "interrupted": True,
                    },
                    ip_address=ip_address,
                    user_agent=user_agent,
                )
            elif stream_error is None:
                await add_audit_log(
                    db,
                    actor_user_id=user.id,
                    action="chat.send",
                    target_type="conversation",
                    target_id=conversation.id,
                    result="success",
                    detail_json={
                        "provider": request.provider,
                        "model": request.model,
                        "duration_ms": duration_ms,
                        "tool_events": len(tool_events),
                        "attachments": len(request.attachments),
                    },
                    ip_address=ip_address,
                    user_agent=user_agent,
                )
            else:
                await add_audit_log(
                    db,
                    actor_user_id=user.id,
                    action="chat.send",
                    target_type="conversation",
                    target_id=conversation.id,
                    result="failed",
                    detail_json={
                        "provider": request.provider,
                        "model": request.model,
                        "duration_ms": duration_ms,
                        "tool_events": len(tool_events),
                        "attachments": len(request.attachments),
                        "error": str(stream_error),
                    },
                    ip_address=ip_address,
                    user_agent=user_agent,
                )
            await db.commit()

            if auto_copy_message is not None:
                await self.publish_system_message(
                    conversation_id=conversation.id,
                    message=auto_copy_message.content,
                    message_id=auto_copy_message.id,
                )

    @staticmethod
    def _snapshot_workspace_root_files(conversation_id: str) -> set[str]:
        workspace_dir = conversation_workspace_path(conversation_id).resolve()
        if not workspace_dir.exists() or not workspace_dir.is_dir():
            return set()

        names: set[str] = set()
        for item in workspace_dir.iterdir():
            if item.is_file():
                names.add(item.name)
        return names

    @staticmethod
    def _dedupe_upload_target(uploads_dir: Path, file_name: str) -> Path:
        target = uploads_dir / file_name
        if not target.exists():
            return target

        stem = target.stem or "file"
        suffix = target.suffix
        index = 1
        while True:
            candidate = uploads_dir / f"{stem}-{index}{suffix}"
            if not candidate.exists():
                return candidate
            index += 1

    def _copy_new_workspace_root_files_to_uploads(
        self,
        *,
        conversation_id: str,
        before_file_names: set[str],
        mount_root: str | None,
    ) -> list[str]:
        workspace_dir = conversation_workspace_path(conversation_id).resolve()
        if not workspace_dir.exists() or not workspace_dir.is_dir():
            return []

        uploads_dir = (workspace_dir / "uploads").resolve()
        uploads_dir.mkdir(parents=True, exist_ok=True)
        mount_base = (mount_root or "/workspace").rstrip("/") or "/workspace"
        copied_paths: list[str] = []

        for item in sorted(workspace_dir.iterdir(), key=lambda entry: entry.name.lower()):
            if not item.is_file():
                continue
            if item.name in before_file_names:
                continue

            target = self._dedupe_upload_target(uploads_dir, item.name)
            try:
                shutil.copy2(item, target)
            except Exception:
                logger.warning(
                    "Failed to auto-copy workspace root file to uploads (conversation=%s, file=%s)",
                    conversation_id,
                    item.name,
                    exc_info=True,
                )
                continue

            copied_paths.append(f"{mount_base}/uploads/{target.name}")

        return copied_paths


def _parse_ws_cursor(raw_value: Any) -> int | None:
    if raw_value is None:
        return None
    if isinstance(raw_value, bool):
        raise ValueError("ws.validation.invalid_cursor")
    if isinstance(raw_value, int):
        return max(0, raw_value)
    if isinstance(raw_value, str) and raw_value.strip():
        return max(0, int(raw_value.strip()))
    raise ValueError("ws.validation.invalid_cursor")


@app.websocket("/ws/chat")
async def chat_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    runtime: WebAgentRuntime = app.state.runtime
    run_manager: ConversationRunManager = app.state.run_manager

    async with session_factory()() as auth_db:
        authed_user = await authenticate_websocket_user(websocket, auth_db)
    if authed_user is None:
        await websocket.send_json({"type": "error", "message": tr_app(app, "ws.auth_required")})
        await websocket.close(code=4401)
        return

    user_id = authed_user.id
    subscription: _RunSubscription | None = None
    forwarder_task: asyncio.Task[None] | None = None

    async def _load_active_ws_user(db_session) -> tuple[User | None, str | None]:
        user = await db_session.get(User, user_id)
        if user is None or user.is_blocked:
            return None, tr_app(app, "ws.user_blocked_or_missing")
        session_cfg = app.state.config.session
        if not await validate_and_touch_user_session(
            db=db_session,
            user=user,
            idle_timeout_seconds=session_cfg.idle_timeout_seconds,
            touch_interval_seconds=session_cfg.touch_interval_seconds,
        ):
            return None, tr_app(app, "auth.session_inactive")
        return user, None

    async def _close_subscription() -> None:
        nonlocal subscription, forwarder_task
        if forwarder_task is not None:
            forwarder_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await forwarder_task
            forwarder_task = None
        if subscription is not None:
            await run_manager.unsubscribe(subscription)
            subscription = None

    async def _start_subscription(*, session_id: str, cursor: int | None) -> None:
        nonlocal subscription, forwarder_task
        await _close_subscription()
        subscription, replay_events = await run_manager.subscribe(
            conversation_id=session_id,
            user_id=user_id,
            cursor=cursor,
        )
        for event in replay_events:
            await websocket.send_json(event)

        async def _forward_events() -> None:
            assert subscription is not None
            local_subscription = subscription
            while True:
                event = await local_subscription.queue.get()
                await websocket.send_json(event)

        forwarder_task = asyncio.create_task(_forward_events())

    try:
        while True:
            payload = await websocket.receive_json()

            message_type = str(payload.get("type") or "")
            if message_type == "ping":
                async with session_factory()() as db:
                    user, error_message = await _load_active_ws_user(db)
                    if user is None:
                        await websocket.send_json({"type": "error", "message": error_message})
                        await websocket.close(code=4401)
                        return
                await websocket.send_json({"type": "pong"})
                continue

            if message_type == "subscribe":
                session_id = str(payload.get("session_id") or "").strip()
                if not session_id:
                    await websocket.send_json(
                        {
                            "type": "error",
                            "message": tr_app(
                                app,
                                "ws.invalid_payload",
                                errors=[
                                    {
                                        "loc": ["session_id"],
                                        "msg": tr_app(app, "ws.validation.required"),
                                        "type": "value_error.missing",
                                    }
                                ],
                            ),
                        }
                    )
                    continue
                try:
                    cursor = _parse_ws_cursor(payload.get("cursor"))
                except ValueError:
                    await websocket.send_json(
                        {
                            "type": "error",
                            "message": tr_app(
                                app,
                                "ws.invalid_payload",
                                errors=[
                                    {
                                        "loc": ["cursor"],
                                        "msg": tr_app(app, "ws.validation.invalid_cursor"),
                                        "type": "value_error",
                                    }
                                ],
                            ),
                        }
                    )
                    continue

                async with session_factory()() as db:
                    user, error_message = await _load_active_ws_user(db)
                    if user is None:
                        await websocket.send_json({"type": "error", "message": error_message})
                        continue
                    conversation = await find_conversation_for_user(
                        db=db,
                        conversation_id=session_id,
                        user_id=user.id,
                    )
                    if conversation is None:
                        await websocket.send_json(
                            {"type": "error", "message": tr_app(app, "ws.conversation_not_found")}
                        )
                        continue

                await _start_subscription(session_id=session_id, cursor=cursor)
                running = await run_manager.is_running(
                    conversation_id=session_id,
                    user_id=user_id,
                )
                await websocket.send_json(
                    {
                        "type": "subscribed",
                        "session_id": session_id,
                        "running": running,
                    }
                )
                continue

            if message_type == "interrupt":
                session_id = str(payload.get("session_id") or "").strip()
                if not session_id:
                    await websocket.send_json(
                        {
                            "type": "error",
                            "message": tr_app(
                                app,
                                "ws.invalid_payload",
                                errors=[
                                    {
                                        "loc": ["session_id"],
                                        "msg": tr_app(app, "ws.validation.required"),
                                        "type": "value_error.missing",
                                    }
                                ],
                            ),
                        }
                    )
                    continue

                async with session_factory()() as db:
                    user, error_message = await _load_active_ws_user(db)
                    if user is None:
                        await websocket.send_json({"type": "error", "message": error_message})
                        continue
                    conversation = await find_conversation_for_user(
                        db=db,
                        conversation_id=session_id,
                        user_id=user.id,
                    )
                    if conversation is None:
                        await websocket.send_json(
                            {"type": "error", "message": tr_app(app, "ws.conversation_not_found")}
                        )
                        continue

                interrupted = await run_manager.cancel_run(
                    conversation_id=session_id,
                    user_id=user_id,
                )
                await websocket.send_json(
                    {
                        "type": "interrupt_ack",
                        "session_id": session_id,
                        "accepted": interrupted,
                    }
                )
                continue

            if message_type != "chat":
                await websocket.send_json(
                    {
                        "type": "error",
                        "message": tr_app(
                            app,
                            "ws.invalid_payload",
                            errors=[
                                {
                                    "loc": ["type"],
                                    "msg": tr_app(app, "ws.validation.unsupported_message_type"),
                                    "type": "value_error",
                                }
                            ],
                        ),
                    }
                )
                continue

            try:
                request = ChatRequest.model_validate(payload)
            except ValidationError as exc:
                await websocket.send_json(
                    {
                        "type": "error",
                        "message": tr_app(app, "ws.invalid_payload", errors=exc.errors()),
                    }
                )
                continue

            async with session_factory()() as db:
                user, error_message = await _load_active_ws_user(db)
                if user is None:
                    await websocket.send_json({"type": "error", "message": error_message})
                    continue

                conversation = await find_conversation_for_user(
                    db=db,
                    conversation_id=request.session_id,
                    user_id=user.id,
                )
                if conversation is None:
                    await websocket.send_json(
                        {"type": "error", "message": tr_app(app, "ws.conversation_not_found")}
                    )
                    continue

                already_running = await run_manager.is_running(
                    conversation_id=conversation.id,
                    user_id=user.id,
                )
                if already_running:
                    await websocket.send_json(
                        {
                            "type": "error",
                            "message": tr_app(app, "ws.conversation_running"),
                        }
                    )
                    continue

                user_message_content = request.message.strip()

                user_message = Message(
                    conversation_id=conversation.id,
                    role="user",
                    content=user_message_content,
                    provider=request.provider or conversation.default_provider,
                    model=request.model or conversation.default_model,
                )
                db.add(user_message)
                await db.flush()
                if request.attachments:
                    workspace_dir = conversation_workspace_path(conversation.id).resolve()
                    uploads_dir = (workspace_dir / "uploads").resolve()
                    mount_root = runtime.config.docker_sandbox.workdir.rstrip("/") or "/workspace"
                    for item in request.attachments:
                        rel_path = Path(item.path).as_posix().lstrip("/")
                        if not rel_path:
                            continue
                        target_path = (workspace_dir / rel_path).resolve()
                        if uploads_dir not in target_path.parents or not target_path.exists() or not target_path.is_file():
                            continue

                        attachment_name = Path(item.name).name if isinstance(item.name, str) and item.name.strip() else Path(rel_path).name
                        mime_type = item.mime_type.strip().lower() if isinstance(item.mime_type, str) else None
                        attachment_size = item.size if isinstance(item.size, int) and item.size >= 0 else target_path.stat().st_size
                        workspace_path = f"{mount_root}/{rel_path}"
                        db.add(
                            MessageAttachment(
                                conversation_id=conversation.id,
                                message_id=user_message.id,
                                name=attachment_name,
                                path=rel_path,
                                mime_type=mime_type,
                                size=attachment_size,
                                kind=(
                                    "image"
                                    if _is_image_attachment(mime_type=mime_type, suffix=target_path.suffix.lower())
                                    else "file"
                                ),
                                workspace_path=workspace_path,
                            )
                        )

                # Persist user message first to avoid losing it when websocket disconnects mid-stream.
                conversation.updated_at = now_utc()
                if request.provider:
                    conversation.default_provider = request.provider
                if request.model:
                    conversation.default_model = request.model
                await db.commit()

                started = await run_manager.start_run(
                    request=request,
                    user_id=user.id,
                    provider=request.provider or conversation.default_provider,
                    model=request.model or conversation.default_model,
                    ip_address=websocket.client.host if websocket.client else None,
                    user_agent=websocket.headers.get("user-agent"),
                )
                if not started:
                    await websocket.send_json(
                        {
                            "type": "error",
                            "message": tr_app(app, "ws.conversation_running"),
                        }
                    )
                    continue

            await websocket.send_json({"type": "accepted", "session_id": request.session_id})
            await _update_sandbox_instance_from_runtime(app=app, conversation_id=request.session_id)
    except WebSocketDisconnect:
        logger.info("Websocket client disconnected")
    finally:
        await _close_subscription()
