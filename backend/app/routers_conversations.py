from __future__ import annotations

import datetime as dt
import mimetypes
import re
from pathlib import Path
import shutil
import stat
import tarfile
import uuid
import zipfile
from dataclasses import dataclass
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import (
    add_audit_log,
    base_conversation_query_for_user,
    conversation_to_public,
    conversation_workspace_path,
    message_to_public,
    now_utc,
    scheduled_task_run_to_public,
    scheduled_task_to_public,
    sandbox_to_public,
    tool_event_to_public,
)
from app.db import get_db
from app.deps import find_conversation_for_user, get_current_user, get_current_user_with_query_token
from app.i18n import tr_app
from app.orm_models import (
    Conversation,
    Message,
    MessageAttachment,
    SandboxInstance,
    ScheduledTask,
    ScheduledTaskRun,
    ToolEvent,
    User,
)
from app.scheduling import ScheduleValidationError, compute_next_run_at, normalize_schedule
from app.schemas import (
    ConversationCreateRequest,
    ConversationAttachmentPublic,
    ConversationFileActionResultPublic,
    ConversationFileArchiveRequest,
    ConversationFileCreateTextRequest,
    ConversationFileDeleteRequest,
    ConversationFileExtractRequest,
    ConversationFileExtractResultPublic,
    ConversationFileMkdirRequest,
    ConversationFileNodePublic,
    ConversationFileRenameRequest,
    ConversationFileTextContentPublic,
    ConversationFileTextWriteRequest,
    ConversationFileTreePublic,
    ConversationPublic,
    ConversationUpdateRequest,
    MessagePublic,
    ScheduledTaskCreateRequest,
    ScheduledTaskPublic,
    ScheduledTaskRunPublic,
    ScheduledTaskUpdateRequest,
    SandboxInstancePublic,
    ToolEventPublic,
)

router = APIRouter(prefix="/api/v1/conversations", tags=["conversations"])

MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
MAX_FILE_MANAGER_UPLOAD_BYTES = 100 * 1024 * 1024
MAX_EDITABLE_TEXT_BYTES = 2 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 5000
MAX_ARCHIVE_MEMBER_BYTES = 50 * 1024 * 1024
MAX_ARCHIVE_TOTAL_BYTES = 300 * 1024 * 1024
MAX_ARCHIVE_DEPTH = 20
MAX_ARCHIVE_RATIO = 300
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".tif"})
TEXT_FILE_SUFFIXES = frozenset(
    {
        ".txt",
        ".md",
        ".markdown",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".ini",
        ".cfg",
        ".csv",
        ".tsv",
        ".xml",
        ".html",
        ".htm",
        ".css",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".py",
        ".java",
        ".go",
        ".rs",
        ".c",
        ".cc",
        ".cpp",
        ".h",
        ".hpp",
        ".sh",
        ".bash",
        ".zsh",
        ".sql",
        ".log",
    }
)

FILE_ROOTS = frozenset({"uploads", "skills"})


@dataclass(frozen=True)
class ConversationFileScope:
    root_name: Literal["uploads", "skills"]
    root_dir: Path


def _translate_schedule_validation_error(request: Request, exc: ScheduleValidationError) -> str:
    message = str(exc or "").strip()
    if message == "schedule_type must be 'cron' or 'interval'.":
        return tr_app(request.app, "schedule.invalid_type")
    if message.startswith("Invalid timezone: "):
        return tr_app(request.app, "schedule.invalid_timezone", timezone=message.removeprefix("Invalid timezone: ").strip())
    if message == "interval_minutes is required for interval schedules.":
        return tr_app(request.app, "schedule.interval_required")
    if message == "interval_minutes must be at least 1.":
        return tr_app(request.app, "schedule.interval_min")
    if message == "cron_expr is required for cron schedules.":
        return tr_app(request.app, "schedule.cron_required")
    if message == "cron_expr must contain 5 fields: minute hour day month weekday.":
        return tr_app(request.app, "schedule.cron_fields")
    if message == "Unable to calculate next cron run time within 2 years.":
        return tr_app(request.app, "schedule.cron_unresolvable")
    if message == "Invalid cron field: empty value.":
        return tr_app(request.app, "schedule.cron_field_empty")

    token_patterns: list[tuple[str, str]] = [
        (r"^Invalid cron token: '(.+)'$", "schedule.cron_token_invalid"),
        (r"^Invalid cron step: '(.+)'$", "schedule.cron_step_invalid"),
        (r"^Invalid cron range: '(.+)'$", "schedule.cron_range_invalid"),
        (r"^Invalid cron value: '(.+)'$", "schedule.cron_value_invalid"),
        (r"^Cron value out of range: '(.+)'$", "schedule.cron_value_out_of_range"),
        (r"^Invalid cron field: '(.+)'$", "schedule.cron_field_invalid"),
    ]
    for pattern, key in token_patterns:
        matched = re.match(pattern, message)
        if matched:
            return tr_app(request.app, key, token=matched.group(1))
    return tr_app(request.app, "schedule.validation_error")


async def _get_owned_conversation(
    *,
    db: AsyncSession,
    user: User,
    conversation_id: str,
    request: Request,
) -> Conversation:
    conversation = await find_conversation_for_user(
        db=db,
        conversation_id=conversation_id,
        user_id=user.id,
    )
    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=tr_app(request.app, "conversation.not_found"),
        )

    expected_workspace_dir = conversation_workspace_path(conversation.id).resolve()
    expected_workspace_dir.mkdir(parents=True, exist_ok=True)
    if conversation.workspace_host_path != str(expected_workspace_dir):
        conversation.workspace_host_path = str(expected_workspace_dir)
    expected_mount_path = request.app.state.runtime.config.docker_sandbox.workdir
    if conversation.workspace_mount_path != expected_mount_path:
        conversation.workspace_mount_path = expected_mount_path
    return conversation


async def _get_owned_scheduled_task(
    *,
    db: AsyncSession,
    user: User,
    conversation_id: str,
    task_id: str,
    request: Request,
) -> ScheduledTask:
    task = (
        await db.execute(
            select(ScheduledTask).where(
                ScheduledTask.id == task_id,
                ScheduledTask.conversation_id == conversation_id,
                ScheduledTask.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=tr_app(request.app, "conversation.scheduled_task_not_found"),
        )
    return task


def _sanitize_attachment_name(raw_name: str) -> str:
    base_name = Path(raw_name).name.strip() or "file"
    suffix = Path(base_name).suffix.lower()[:16]
    stem = Path(base_name).stem
    safe_stem = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "-" for ch in stem).strip("-._")
    if not safe_stem:
        safe_stem = "file"
    safe_stem = safe_stem[:80]
    safe_suffix = "".join(ch for ch in suffix if ch.isalnum() or ch == ".")
    return f"{safe_stem}{safe_suffix}"


def _is_image_upload(mime_type: str | None, suffix: str) -> bool:
    if isinstance(mime_type, str) and mime_type.strip().lower().startswith("image/"):
        return True
    return suffix.lower() in IMAGE_SUFFIXES


def _build_stored_attachment_name(safe_name: str) -> str:
    safe_path = Path(safe_name)
    stem = safe_path.stem or "file"
    suffix = safe_path.suffix
    timestamp = int(now_utc().timestamp())
    random_code = uuid.uuid4().hex[:8]
    return f"{stem}-{timestamp}-{random_code}{suffix}"


def _uploads_dir_for_conversation(conversation: Conversation) -> Path:
    workspace_dir = conversation_workspace_path(conversation.id).resolve()
    uploads_dir = (workspace_dir / "uploads").resolve()
    uploads_dir.mkdir(parents=True, exist_ok=True)
    return uploads_dir


def _sanitize_user_dir_segment(raw: str, *, max_len: int = 96) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "-" for ch in str(raw or ""))
    cleaned = cleaned.strip("-._")
    if not cleaned:
        cleaned = "user"
    return cleaned[:max_len]


def _skills_dir_for_user(*, request: Request, user: User) -> Path:
    base_dir = request.app.state.runtime.config.docker_sandbox.skills_user_dir.resolve()
    safe_user_id = _sanitize_user_dir_segment(user.id)
    skills_dir = (base_dir / safe_user_id).resolve()
    if skills_dir != base_dir and base_dir not in skills_dir.parents:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=tr_app(request.app, "conversation.files.invalid_skills_directory"),
        )
    skills_dir.mkdir(parents=True, exist_ok=True)
    return skills_dir


def _normalize_scope_root(
    raw_root: str | None,
    *,
    request: Request,
) -> Literal["uploads", "skills"] | None:
    if raw_root is None:
        return None
    value = str(raw_root).strip().lower()
    if not value:
        return None
    if value not in FILE_ROOTS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=tr_app(request.app, "conversation.files.invalid_root"),
        )
    if value == "skills":
        return "skills"
    return "uploads"


def _detect_scope_root_from_path(raw_path: str | None) -> Literal["uploads", "skills"] | None:
    text = str(raw_path or "").replace("\\", "/").strip()
    if not text:
        return None
    normalized = text.lstrip("/")
    if normalized == "uploads" or normalized.startswith("uploads/"):
        return "uploads"
    if normalized == "skills" or normalized.startswith("skills/"):
        return "skills"
    return None


def _resolve_file_scope(
    *,
    conversation: Conversation,
    request: Request,
    user: User,
    root_hint: str | None = None,
    raw_path: str | None = None,
) -> ConversationFileScope:
    normalized_hint = _normalize_scope_root(root_hint, request=request)
    path_root = _detect_scope_root_from_path(raw_path)
    root_name = normalized_hint or path_root or "uploads"
    if root_name == "skills":
        return ConversationFileScope(root_name="skills", root_dir=_skills_dir_for_user(request=request, user=user))
    return ConversationFileScope(root_name="uploads", root_dir=_uploads_dir_for_conversation(conversation))


def _normalize_scope_relative_path(
    raw_path: str | None,
    *,
    root_name: Literal["uploads", "skills"],
    request: Request,
) -> str:
    text = str(raw_path or "").replace("\\", "/").strip()
    if not text:
        return ""
    normalized = text.lstrip("/")
    if normalized == root_name:
        return ""
    root_prefix = f"{root_name}/"
    if normalized.startswith(root_prefix):
        normalized = normalized[len(root_prefix) :]
    else:
        head = normalized.split("/", 1)[0]
        if head in FILE_ROOTS and head != root_name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=tr_app(request.app, "conversation.files.path_root_mismatch", got=head, expected=root_name),
            )
    return normalized.strip("/")


def _resolve_file_path(
    *,
    scope: ConversationFileScope,
    raw_path: str,
    request: Request,
    must_exist: bool = True,
) -> Path:
    normalized_rel = _normalize_scope_relative_path(raw_path, root_name=scope.root_name, request=request)
    if not normalized_rel:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=tr_app(request.app, "conversation.files.path_required"),
        )
    candidate = (scope.root_dir / normalized_rel).resolve()
    if candidate != scope.root_dir and scope.root_dir not in candidate.parents:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=tr_app(request.app, "conversation.files.path_escapes_root", root=scope.root_name),
        )
    if must_exist and not candidate.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=tr_app(request.app, "conversation.files.path_not_found"),
        )
    return candidate


def _resolve_file_directory(
    *,
    scope: ConversationFileScope,
    raw_path: str | None,
    request: Request,
    must_exist: bool = True,
) -> Path:
    normalized_rel = _normalize_scope_relative_path(raw_path, root_name=scope.root_name, request=request)
    candidate = scope.root_dir if not normalized_rel else (scope.root_dir / normalized_rel).resolve()
    if candidate != scope.root_dir and scope.root_dir not in candidate.parents:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=tr_app(request.app, "conversation.files.directory_escapes_root", root=scope.root_name),
        )
    if candidate.exists() and not candidate.is_dir():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=tr_app(request.app, "conversation.files.target_not_directory"),
        )
    if must_exist and not candidate.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=tr_app(request.app, "conversation.files.directory_not_found"),
        )
    if must_exist and not candidate.is_dir():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=tr_app(request.app, "conversation.files.target_not_directory"),
        )
    return candidate


def _to_scope_workspace_path(scope: ConversationFileScope, target: Path) -> str:
    rel = target.relative_to(scope.root_dir).as_posix()
    return scope.root_name if not rel else f"{scope.root_name}/{rel}"


def _safe_node_name(raw_name: str, *, request: Request) -> str:
    cleaned = Path(raw_name or "").name.strip()
    if cleaned in {"", ".", ".."}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=tr_app(request.app, "conversation.files.invalid_name"),
        )
    return cleaned


def _ensure_unique_path(directory: Path, name: str, *, request: Request) -> Path:
    base_name = _safe_node_name(name, request=request)
    initial = directory / base_name
    if not initial.exists():
        return initial

    stem = Path(base_name).stem or "file"
    suffix = Path(base_name).suffix
    for idx in range(1, 1000):
        candidate = directory / f"{stem}-{idx}{suffix}"
        if not candidate.exists():
            return candidate
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=tr_app(request.app, "conversation.files.too_many_same_name"),
    )


def _is_text_file(path: Path, mime_type: str | None) -> bool:
    if isinstance(mime_type, str) and mime_type.startswith("text/"):
        return True
    if path.suffix.lower() in TEXT_FILE_SUFFIXES:
        return True
    try:
        probe = path.read_bytes()[:4096]
    except OSError:
        return False
    if not probe:
        return True
    if b"\x00" in probe:
        return False
    try:
        probe.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def _build_file_node(scope: ConversationFileScope, target: Path) -> ConversationFileNodePublic:
    stat_result = target.stat()
    is_dir = target.is_dir()
    mime_type = None if is_dir else mimetypes.guess_type(target.name)[0]
    children: list[ConversationFileNodePublic] = []
    if is_dir:
        children = _build_file_nodes(scope, target)
    is_text = False if is_dir else _is_text_file(target, mime_type)
    created_ts = getattr(stat_result, "st_birthtime", None)
    if not isinstance(created_ts, (int, float)):
        # Linux does not expose birthtime reliably; fallback to ctime as best effort.
        created_ts = stat_result.st_ctime
    return ConversationFileNodePublic(
        path=_to_scope_workspace_path(scope, target),
        name=target.name,
        node_type=("directory" if is_dir else "file"),
        size=(None if is_dir else stat_result.st_size),
        mime_type=mime_type,
        is_text=is_text,
        created_at=dt.datetime.fromtimestamp(created_ts, tz=dt.timezone.utc),
        modified_at=dt.datetime.fromtimestamp(stat_result.st_mtime, tz=dt.timezone.utc),
        children=children,
    )


def _build_file_nodes(scope: ConversationFileScope, parent: Path) -> list[ConversationFileNodePublic]:
    directories: list[Path] = []
    files: list[Path] = []
    try:
        for entry in parent.iterdir():
            if entry.is_symlink():
                continue
            if entry.is_dir():
                directories.append(entry)
            else:
                files.append(entry)
    except OSError:
        return []

    ordered = sorted(directories, key=lambda item: item.name.lower()) + sorted(files, key=lambda item: item.name.lower())
    return [_build_file_node(scope, item) for item in ordered]


def _ensure_archive_target_path(target_dir: Path, member_name: str, *, request: Request) -> Path:
    normalized_name = member_name.replace("\\", "/").strip()
    if not normalized_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=tr_app(request.app, "conversation.archive.member_path_empty"),
        )
    if normalized_name.startswith("/") or normalized_name.startswith("../") or "/../" in f"/{normalized_name}/":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=tr_app(request.app, "conversation.archive.unsafe_path"),
        )
    relative = Path(normalized_name)
    if len(relative.parts) > MAX_ARCHIVE_DEPTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=tr_app(request.app, "conversation.archive.nesting_too_deep"),
        )
    candidate = (target_dir / relative).resolve()
    if candidate != target_dir and target_dir not in candidate.parents:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=tr_app(request.app, "conversation.archive.extraction_escapes_target"),
        )
    return candidate


def _extract_zip_archive(archive_path: Path, target_dir: Path, *, request: Request) -> int:
    extracted_count = 0
    total_unpacked = 0
    with zipfile.ZipFile(archive_path, "r") as archive:
        members = archive.infolist()
        if len(members) > MAX_ARCHIVE_ENTRIES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=tr_app(request.app, "conversation.archive.too_many_entries"),
            )
        for member in members:
            member_name = (member.filename or "").strip()
            if not member_name:
                continue

            mode = (member.external_attr >> 16) & 0o170000
            if mode == stat.S_IFLNK:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=tr_app(request.app, "conversation.archive.symlink_entry"),
                )
            if member.file_size > MAX_ARCHIVE_MEMBER_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=tr_app(request.app, "conversation.archive.member_too_large"),
                )
            if member.compress_size > 0 and member.file_size > member.compress_size * MAX_ARCHIVE_RATIO:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=tr_app(request.app, "conversation.archive.unsafe_compression_ratio"),
                )

            total_unpacked += member.file_size
            if total_unpacked > MAX_ARCHIVE_TOTAL_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=tr_app(request.app, "conversation.archive.unpacked_too_large"),
                )

            target_path = _ensure_archive_target_path(target_dir, member_name, request=request)
            if member.is_dir():
                target_path.mkdir(parents=True, exist_ok=True)
                continue

            target_path.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member, "r") as src, target_path.open("wb") as dst:
                shutil.copyfileobj(src, dst, 1024 * 1024)
            extracted_count += 1
    return extracted_count


def _extract_tar_archive(archive_path: Path, target_dir: Path, *, request: Request) -> int:
    extracted_count = 0
    total_unpacked = 0
    with tarfile.open(archive_path, "r:*") as archive:
        members = archive.getmembers()
        if len(members) > MAX_ARCHIVE_ENTRIES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=tr_app(request.app, "conversation.archive.too_many_entries"),
            )
        for member in members:
            member_name = (member.name or "").strip()
            if not member_name:
                continue
            if member.issym() or member.islnk() or member.isdev():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=tr_app(request.app, "conversation.archive.unsupported_link_or_device"),
                )
            target_path = _ensure_archive_target_path(target_dir, member_name, request=request)
            if member.isdir():
                target_path.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                continue
            if member.size > MAX_ARCHIVE_MEMBER_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=tr_app(request.app, "conversation.archive.member_too_large"),
                )
            total_unpacked += member.size
            if total_unpacked > MAX_ARCHIVE_TOTAL_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=tr_app(request.app, "conversation.archive.unpacked_too_large"),
                )
            target_path.parent.mkdir(parents=True, exist_ok=True)
            stream = archive.extractfile(member)
            if stream is None:
                continue
            with stream, target_path.open("wb") as dst:
                shutil.copyfileobj(stream, dst, 1024 * 1024)
            extracted_count += 1
    return extracted_count


@router.post("", response_model=ConversationPublic)
async def create_conversation(
    payload: ConversationCreateRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ConversationPublic:
    default_limit = request.app.state.config.default_user_conversation_limit
    effective_limit = user.conversation_limit if user.conversation_limit is not None else default_limit
    if effective_limit >= 0:
        existing_total = int(
            (
                await db.execute(
                    select(func.count()).select_from(base_conversation_query_for_user(user.id).subquery())
                )
            ).scalar_one()
            or 0
        )
        if existing_total >= effective_limit:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=tr_app(request.app, "conversation.limit_reached", limit=effective_limit),
            )

    title = (
        payload.title.strip()
        if isinstance(payload.title, str) and payload.title.strip()
        else tr_app(request.app, "conversation.default_title")
    )

    conversation = Conversation(
        user_id=user.id,
        title=title,
        default_provider=payload.default_provider,
        default_model=payload.default_model,
        workspace_host_path="",
        workspace_mount_path=request.app.state.runtime.config.docker_sandbox.workdir,
        status="active",
    )
    db.add(conversation)
    await db.flush()

    workspace_dir = conversation_workspace_path(conversation.id)
    workspace_dir.mkdir(parents=True, exist_ok=True)
    conversation.workspace_host_path = str(workspace_dir)

    sandbox = SandboxInstance(
        conversation_id=conversation.id,
        status="pending",
        image=request.app.state.runtime.config.docker_sandbox.image,
    )
    db.add(sandbox)

    await add_audit_log(
        db,
        actor_user_id=user.id,
        action="conversation.create",
        target_type="conversation",
        target_id=conversation.id,
        detail_json={"title": title, "workspace": str(workspace_dir)},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    await db.commit()
    await db.refresh(conversation)
    return conversation_to_public(conversation)


@router.get("")
async def list_conversations(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    base_query = base_conversation_query_for_user(user.id)
    total_query = select(func.count()).select_from(base_query.subquery())
    total = int((await db.execute(total_query)).scalar_one() or 0)

    query = (
        base_query.order_by(Conversation.is_pinned.desc(), Conversation.updated_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = (await db.execute(query)).scalars().all()
    return {
        "items": [conversation_to_public(item).model_dump() for item in items],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/{conversation_id}", response_model=ConversationPublic)
async def get_conversation(
    conversation_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ConversationPublic:
    conversation = await _get_owned_conversation(
        db=db,
        user=user,
        conversation_id=conversation_id,
        request=request,
    )
    return conversation_to_public(conversation)


@router.patch("/{conversation_id}", response_model=ConversationPublic)
async def update_conversation(
    conversation_id: str,
    payload: ConversationUpdateRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ConversationPublic:
    conversation = await _get_owned_conversation(
        db=db,
        user=user,
        conversation_id=conversation_id,
        request=request,
    )
    if payload.title is not None and payload.title.strip():
        conversation.title = payload.title.strip()
    if payload.default_provider is not None:
        conversation.default_provider = payload.default_provider
    if payload.default_model is not None:
        conversation.default_model = payload.default_model
    if payload.is_pinned is not None:
        conversation.is_pinned = payload.is_pinned
    if payload.status is not None:
        conversation.status = payload.status

    await add_audit_log(
        db,
        actor_user_id=user.id,
        action="conversation.update",
        target_type="conversation",
        target_id=conversation.id,
        detail_json=payload.model_dump(exclude_none=True),
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    await db.commit()
    await db.refresh(conversation)
    return conversation_to_public(conversation)


@router.delete("/{conversation_id}")
async def delete_conversation(
    conversation_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, bool]:
    conversation = await _get_owned_conversation(
        db=db,
        user=user,
        conversation_id=conversation_id,
        request=request,
    )
    conversation.deleted_at = now_utc()
    conversation.status = "deleted"

    sandbox = (
        await db.execute(select(SandboxInstance).where(SandboxInstance.conversation_id == conversation.id))
    ).scalar_one_or_none()
    if sandbox is not None:
        sandbox.status = "stopped"
        sandbox.destroyed_at = now_utc()
        sandbox.container_id = None
        sandbox.container_name = None

    runtime = request.app.state.runtime
    await runtime.close_session(conversation.id)

    await add_audit_log(
        db,
        actor_user_id=user.id,
        action="conversation.delete",
        target_type="conversation",
        target_id=conversation.id,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    await db.commit()
    return {"ok": True}


@router.post("/{conversation_id}/attachments", response_model=list[ConversationAttachmentPublic])
async def upload_conversation_attachments(
    conversation_id: str,
    request: Request,
    files: list[UploadFile] = File(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ConversationAttachmentPublic]:
    conversation = await _get_owned_conversation(
        db=db,
        user=user,
        conversation_id=conversation_id,
        request=request,
    )
    if not files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=tr_app(request.app, "conversation.attachment_file_required"),
        )

    workspace_dir = conversation_workspace_path(conversation.id).resolve()
    uploads_dir = (workspace_dir / "uploads").resolve()
    uploads_dir.mkdir(parents=True, exist_ok=True)

    uploaded: list[ConversationAttachmentPublic] = []
    for upload in files:
        data = await upload.read()
        if not data:
            continue
        if len(data) > MAX_ATTACHMENT_BYTES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=tr_app(
                    request.app,
                    "conversation.attachment_too_large",
                    name=Path(upload.filename or "file").name,
                    max_mb=MAX_ATTACHMENT_BYTES // (1024 * 1024),
                ),
            )

        safe_name = _sanitize_attachment_name(upload.filename or "file")
        stored_name = _build_stored_attachment_name(safe_name)
        target_path = (uploads_dir / stored_name).resolve()
        if uploads_dir not in target_path.parents:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=tr_app(request.app, "conversation.attachment_upload_failed"),
            )
        try:
            target_path.write_bytes(data)
        except OSError as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=tr_app(request.app, "conversation.attachment_upload_failed"),
            ) from exc

        rel_path = Path("uploads") / stored_name
        rel_path_posix = rel_path.as_posix()
        mount_root = conversation.workspace_mount_path.rstrip("/") or "/workspace"
        workspace_path = f"{mount_root}/{rel_path_posix}"
        mime_type = upload.content_type.strip().lower() if isinstance(upload.content_type, str) else None

        uploaded.append(
            ConversationAttachmentPublic(
                name=Path(upload.filename or safe_name).name,
                path=rel_path_posix,
                mime_type=mime_type,
                size=len(data),
                kind=("image" if _is_image_upload(mime_type, target_path.suffix.lower()) else "file"),
                workspace_path=workspace_path,
            )
        )

    if not uploaded:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=tr_app(request.app, "conversation.attachment_file_required"),
        )

    await add_audit_log(
        db,
        actor_user_id=user.id,
        action="conversation.upload_attachment",
        target_type="conversation",
        target_id=conversation.id,
        detail_json={
            "count": len(uploaded),
            "files": [item.name for item in uploaded],
        },
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    await db.commit()
    return uploaded


@router.get("/{conversation_id}/files/tree", response_model=ConversationFileTreePublic)
async def get_conversation_files_tree(
    conversation_id: str,
    request: Request,
    root: str = Query(default="uploads", pattern="^(uploads|skills)$"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ConversationFileTreePublic:
    conversation = await _get_owned_conversation(
        db=db,
        user=user,
        conversation_id=conversation_id,
        request=request,
    )
    scope = _resolve_file_scope(
        conversation=conversation,
        request=request,
        user=user,
        root_hint=root,
    )
    return ConversationFileTreePublic(
        root_path=scope.root_name,
        items=_build_file_nodes(scope, scope.root_dir),
    )


@router.post("/{conversation_id}/files/mkdir", response_model=ConversationFileNodePublic)
async def create_conversation_directory(
    conversation_id: str,
    payload: ConversationFileMkdirRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ConversationFileNodePublic:
    conversation = await _get_owned_conversation(
        db=db,
        user=user,
        conversation_id=conversation_id,
        request=request,
    )
    scope = _resolve_file_scope(
        conversation=conversation,
        request=request,
        user=user,
        raw_path=payload.directory_path,
    )
    target_path = _resolve_file_path(
        scope=scope,
        raw_path=payload.directory_path,
        request=request,
        must_exist=False,
    )
    if target_path.exists():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=tr_app(request.app, "conversation.files.directory_exists"),
        )
    target_path.mkdir(parents=True, exist_ok=False)
    await add_audit_log(
        db,
        actor_user_id=user.id,
        action="conversation.files.mkdir",
        target_type="conversation",
        target_id=conversation.id,
        detail_json={"path": _to_scope_workspace_path(scope, target_path)},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    await db.commit()
    return _build_file_node(scope, target_path)


@router.post("/{conversation_id}/files/create-text", response_model=ConversationFileNodePublic)
async def create_conversation_text_file(
    conversation_id: str,
    payload: ConversationFileCreateTextRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ConversationFileNodePublic:
    conversation = await _get_owned_conversation(
        db=db,
        user=user,
        conversation_id=conversation_id,
        request=request,
    )
    scope = _resolve_file_scope(
        conversation=conversation,
        request=request,
        user=user,
        raw_path=payload.file_path,
    )
    target_path = _resolve_file_path(
        scope=scope,
        raw_path=payload.file_path,
        request=request,
        must_exist=False,
    )
    if target_path.exists():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=tr_app(request.app, "conversation.files.file_exists"),
        )
    target_path.parent.mkdir(parents=True, exist_ok=True)
    content = payload.content or ""
    if len(content.encode("utf-8")) > MAX_EDITABLE_TEXT_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=tr_app(request.app, "conversation.files.text_too_large"),
        )
    target_path.write_text(content, encoding="utf-8")
    await add_audit_log(
        db,
        actor_user_id=user.id,
        action="conversation.files.create_text",
        target_type="conversation",
        target_id=conversation.id,
        detail_json={"path": _to_scope_workspace_path(scope, target_path)},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    await db.commit()
    return _build_file_node(scope, target_path)


@router.post("/{conversation_id}/files/upload", response_model=list[ConversationFileNodePublic])
async def upload_conversation_files_to_directory(
    conversation_id: str,
    request: Request,
    files: list[UploadFile] = File(...),
    target_dir: str = Form(default=""),
    root: str = Form(default=""),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ConversationFileNodePublic]:
    conversation = await _get_owned_conversation(
        db=db,
        user=user,
        conversation_id=conversation_id,
        request=request,
    )
    if not files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=tr_app(request.app, "conversation.files.upload_file_required"),
        )

    scope = _resolve_file_scope(
        conversation=conversation,
        request=request,
        user=user,
        root_hint=root,
        raw_path=target_dir,
    )
    target_dir_path = _resolve_file_directory(
        scope=scope,
        raw_path=target_dir,
        request=request,
        must_exist=False,
    )
    target_dir_path.mkdir(parents=True, exist_ok=True)

    created: list[ConversationFileNodePublic] = []
    for upload in files:
        raw = await upload.read()
        if not raw:
            continue
        if len(raw) > MAX_FILE_MANAGER_UPLOAD_BYTES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=tr_app(
                    request.app,
                    "conversation.files.upload_too_large",
                    name=Path(upload.filename or "file").name,
                ),
            )
        safe_name = _safe_node_name(upload.filename or "file", request=request)
        save_path = _ensure_unique_path(target_dir_path, safe_name, request=request)
        save_path.write_bytes(raw)
        created.append(_build_file_node(scope, save_path))

    await add_audit_log(
        db,
        actor_user_id=user.id,
        action="conversation.files.upload",
        target_type="conversation",
        target_id=conversation.id,
        detail_json={
            "target_dir": _to_scope_workspace_path(scope, target_dir_path),
            "files": [item.path for item in created],
        },
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    await db.commit()
    return created


@router.post("/{conversation_id}/files/rename", response_model=ConversationFileNodePublic)
async def rename_conversation_file_node(
    conversation_id: str,
    payload: ConversationFileRenameRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ConversationFileNodePublic:
    conversation = await _get_owned_conversation(
        db=db,
        user=user,
        conversation_id=conversation_id,
        request=request,
    )
    scope = _resolve_file_scope(
        conversation=conversation,
        request=request,
        user=user,
        raw_path=payload.path,
    )
    target_path = _resolve_file_path(scope=scope, raw_path=payload.path, request=request, must_exist=True)
    if target_path == scope.root_dir:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=tr_app(request.app, "conversation.files.rename_root_forbidden", root=scope.root_name),
        )
    new_name = _safe_node_name(payload.new_name, request=request)
    destination = target_path.with_name(new_name)
    destination = destination.resolve()
    if destination != scope.root_dir and scope.root_dir not in destination.parents:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=tr_app(request.app, "conversation.files.rename_target_escapes", root=scope.root_name),
        )
    if destination.exists():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=tr_app(request.app, "conversation.files.name_conflict"),
        )
    target_path.rename(destination)
    await add_audit_log(
        db,
        actor_user_id=user.id,
        action="conversation.files.rename",
        target_type="conversation",
        target_id=conversation.id,
        detail_json={
            "from": _to_scope_workspace_path(scope, target_path),
            "to": _to_scope_workspace_path(scope, destination),
        },
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    await db.commit()
    return _build_file_node(scope, destination)


@router.post("/{conversation_id}/files/delete", response_model=ConversationFileActionResultPublic)
async def delete_conversation_file_node(
    conversation_id: str,
    payload: ConversationFileDeleteRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ConversationFileActionResultPublic:
    conversation = await _get_owned_conversation(
        db=db,
        user=user,
        conversation_id=conversation_id,
        request=request,
    )
    scope = _resolve_file_scope(
        conversation=conversation,
        request=request,
        user=user,
        raw_path=payload.path,
    )
    target_path = _resolve_file_path(scope=scope, raw_path=payload.path, request=request, must_exist=True)
    if target_path == scope.root_dir:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=tr_app(request.app, "conversation.files.delete_root_forbidden", root=scope.root_name),
        )

    deleted_path = _to_scope_workspace_path(scope, target_path)
    if target_path.is_dir():
        child_count = sum(1 for _ in target_path.iterdir())
        if child_count > 0:
            if not payload.recursive:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=tr_app(request.app, "conversation.files.directory_not_empty_recursive_required"),
                )
            if (payload.confirm_name or "").strip() != target_path.name:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=tr_app(request.app, "conversation.files.confirm_name_mismatch"),
                )
            shutil.rmtree(target_path)
        else:
            target_path.rmdir()
    else:
        target_path.unlink()

    await add_audit_log(
        db,
        actor_user_id=user.id,
        action="conversation.files.delete",
        target_type="conversation",
        target_id=conversation.id,
        detail_json={"path": deleted_path},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    await db.commit()
    return ConversationFileActionResultPublic(
        ok=True,
        path=deleted_path,
        message=tr_app(request.app, "conversation.files.deleted"),
    )


@router.post("/{conversation_id}/files/extract", response_model=ConversationFileExtractResultPublic)
async def extract_conversation_archive(
    conversation_id: str,
    payload: ConversationFileExtractRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ConversationFileExtractResultPublic:
    conversation = await _get_owned_conversation(
        db=db,
        user=user,
        conversation_id=conversation_id,
        request=request,
    )
    scope = _resolve_file_scope(
        conversation=conversation,
        request=request,
        user=user,
        raw_path=payload.archive_path,
    )
    archive_path = _resolve_file_path(scope=scope, raw_path=payload.archive_path, request=request, must_exist=True)
    if archive_path.is_dir():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=tr_app(request.app, "conversation.archive.path_is_directory"),
        )

    if payload.target_dir and payload.target_dir.strip():
        target_dir = _resolve_file_directory(
            scope=scope,
            raw_path=payload.target_dir,
            request=request,
            must_exist=False,
        )
    else:
        base_name = archive_path.name
        for suffix in (".tar.gz", ".tar.bz2", ".tar.xz", ".tgz", ".tbz", ".txz", ".zip", ".tar"):
            if base_name.lower().endswith(suffix):
                base_name = base_name[: -len(suffix)] or "extracted"
                break
        target_dir = _ensure_unique_path(archive_path.parent, base_name, request=request)
    target_dir.mkdir(parents=True, exist_ok=True)

    lower_name = archive_path.name.lower()
    if zipfile.is_zipfile(archive_path):
        extracted_count = _extract_zip_archive(archive_path, target_dir, request=request)
    elif tarfile.is_tarfile(archive_path) or lower_name.endswith((".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz", ".tar.xz", ".txz")):
        extracted_count = _extract_tar_archive(archive_path, target_dir, request=request)
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=tr_app(request.app, "conversation.archive.unsupported_format"),
        )

    target_workspace_path = _to_scope_workspace_path(scope, target_dir)
    await add_audit_log(
        db,
        actor_user_id=user.id,
        action="conversation.files.extract",
        target_type="conversation",
        target_id=conversation.id,
        detail_json={
            "archive": _to_scope_workspace_path(scope, archive_path),
            "target": target_workspace_path,
            "count": extracted_count,
        },
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    await db.commit()
    return ConversationFileExtractResultPublic(
        ok=True,
        target_path=target_workspace_path,
        extracted_count=extracted_count,
    )


@router.post("/{conversation_id}/files/archive", response_model=ConversationFileNodePublic)
async def archive_conversation_directory(
    conversation_id: str,
    payload: ConversationFileArchiveRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ConversationFileNodePublic:
    conversation = await _get_owned_conversation(
        db=db,
        user=user,
        conversation_id=conversation_id,
        request=request,
    )
    scope = _resolve_file_scope(
        conversation=conversation,
        request=request,
        user=user,
        raw_path=payload.directory_path,
    )
    source_dir = _resolve_file_path(scope=scope, raw_path=payload.directory_path, request=request, must_exist=True)
    if not source_dir.is_dir():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=tr_app(request.app, "conversation.archive.directory_path_must_be_directory"),
        )

    destination_parent = _resolve_file_directory(
        scope=scope,
        raw_path=payload.target_dir,
        request=request,
        must_exist=False,
    )
    destination_parent.mkdir(parents=True, exist_ok=True)

    raw_name = payload.output_name.strip() if isinstance(payload.output_name, str) and payload.output_name.strip() else f"{source_dir.name}.zip"
    safe_name = _safe_node_name(raw_name, request=request)
    if not safe_name.lower().endswith(".zip"):
        safe_name = f"{safe_name}.zip"
    output_path = _ensure_unique_path(destination_parent, safe_name, request=request)

    file_count = 0
    total_size = 0
    try:
        with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for child in sorted(source_dir.rglob("*"), key=lambda item: item.as_posix().lower()):
                if child == output_path:
                    continue
                if child.is_symlink() or child.is_dir():
                    continue
                stat_info = child.stat()
                file_count += 1
                total_size += stat_info.st_size
                if file_count > MAX_ARCHIVE_ENTRIES:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=tr_app(request.app, "conversation.archive.directory_too_many_files"),
                    )
                if stat_info.st_size > MAX_ARCHIVE_MEMBER_BYTES:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=tr_app(request.app, "conversation.archive.directory_oversized_file"),
                    )
                if total_size > MAX_ARCHIVE_TOTAL_BYTES:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=tr_app(request.app, "conversation.archive.directory_total_too_large"),
                    )
                archive.write(child, arcname=child.relative_to(source_dir).as_posix())
    except HTTPException:
        if output_path.exists():
            output_path.unlink(missing_ok=True)
        raise

    await add_audit_log(
        db,
        actor_user_id=user.id,
        action="conversation.files.archive",
        target_type="conversation",
        target_id=conversation.id,
        detail_json={
            "source": _to_scope_workspace_path(scope, source_dir),
            "output": _to_scope_workspace_path(scope, output_path),
            "count": file_count,
        },
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    await db.commit()
    return _build_file_node(scope, output_path)


@router.get("/{conversation_id}/files/content", response_model=ConversationFileTextContentPublic)
async def read_conversation_text_file(
    conversation_id: str,
    request: Request,
    path: str = Query(min_length=1, max_length=1024),
    root: str | None = Query(default=None, pattern="^(uploads|skills)$"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ConversationFileTextContentPublic:
    conversation = await _get_owned_conversation(
        db=db,
        user=user,
        conversation_id=conversation_id,
        request=request,
    )
    scope = _resolve_file_scope(
        conversation=conversation,
        request=request,
        user=user,
        root_hint=root,
        raw_path=path,
    )
    target_path = _resolve_file_path(scope=scope, raw_path=path, request=request, must_exist=True)
    if not target_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=tr_app(request.app, "conversation.files.path_not_file"),
        )
    stat_info = target_path.stat()
    if stat_info.st_size > MAX_EDITABLE_TEXT_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=tr_app(request.app, "conversation.files.text_too_large_edit"),
        )
    mime_type = mimetypes.guess_type(target_path.name)[0]
    if not _is_text_file(target_path, mime_type):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=tr_app(request.app, "conversation.files.not_text_file"),
        )
    try:
        content = target_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=tr_app(request.app, "conversation.files.not_utf8_text"),
        ) from exc
    return ConversationFileTextContentPublic(
        path=_to_scope_workspace_path(scope, target_path),
        size=stat_info.st_size,
        content=content,
        is_text=True,
    )


@router.put("/{conversation_id}/files/content", response_model=ConversationFileNodePublic)
async def write_conversation_text_file(
    conversation_id: str,
    payload: ConversationFileTextWriteRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ConversationFileNodePublic:
    conversation = await _get_owned_conversation(
        db=db,
        user=user,
        conversation_id=conversation_id,
        request=request,
    )
    scope = _resolve_file_scope(
        conversation=conversation,
        request=request,
        user=user,
        raw_path=payload.path,
    )
    target_path = _resolve_file_path(scope=scope, raw_path=payload.path, request=request, must_exist=True)
    if not target_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=tr_app(request.app, "conversation.files.path_not_file"),
        )
    encoded = payload.content.encode("utf-8")
    if len(encoded) > MAX_EDITABLE_TEXT_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=tr_app(request.app, "conversation.files.text_content_too_large"),
        )
    target_path.write_text(payload.content, encoding="utf-8")
    await add_audit_log(
        db,
        actor_user_id=user.id,
        action="conversation.files.write_text",
        target_type="conversation",
        target_id=conversation.id,
        detail_json={"path": _to_scope_workspace_path(scope, target_path), "size": len(encoded)},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    await db.commit()
    return _build_file_node(scope, target_path)


@router.get("/{conversation_id}/attachments/{attachment_path:path}")
async def get_conversation_attachment(
    conversation_id: str,
    attachment_path: str,
    request: Request,
    inline: bool = Query(default=False),
    name: str | None = Query(default=None, max_length=255),
    user: User = Depends(get_current_user_with_query_token),
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    conversation = await _get_owned_conversation(
        db=db,
        user=user,
        conversation_id=conversation_id,
        request=request,
    )
    scope = _resolve_file_scope(
        conversation=conversation,
        request=request,
        user=user,
        raw_path=attachment_path,
    )
    try:
        normalized_rel = _normalize_scope_relative_path(
            attachment_path,
            root_name=scope.root_name,
            request=request,
        )
    except HTTPException:
        normalized_rel = ""
    target_path = (scope.root_dir / normalized_rel).resolve() if normalized_rel else scope.root_dir
    if (
        normalized_rel == ""
        or (target_path != scope.root_dir and scope.root_dir not in target_path.parents)
        or not target_path.exists()
        or not target_path.is_file()
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=tr_app(request.app, "conversation.attachment_not_found"),
        )

    media_type, _ = mimetypes.guess_type(target_path.name)
    requested_name = Path(name).name.strip() if isinstance(name, str) and name.strip() else target_path.name
    download_name = requested_name or target_path.name
    return FileResponse(
        path=target_path,
        media_type=media_type or "application/octet-stream",
        filename=download_name,
        content_disposition_type=("inline" if inline else "attachment"),
    )


@router.get("/{conversation_id}/messages")
async def list_messages(
    conversation_id: str,
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    conversation = await _get_owned_conversation(
        db=db,
        user=user,
        conversation_id=conversation_id,
        request=request,
    )
    where_clause = and_(Message.conversation_id == conversation.id)

    total = int(
        (
            await db.execute(select(func.count()).select_from(Message).where(where_clause))
        ).scalar_one()
        or 0
    )
    query = (
        select(Message)
        .where(where_clause)
        .order_by(Message.created_at.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = (await db.execute(query)).scalars().all()
    message_ids = [item.id for item in items]
    attachments_by_message: dict[str, list[MessageAttachment]] = {}
    if message_ids:
        attachment_items = (
            await db.execute(
                select(MessageAttachment)
                .where(
                    MessageAttachment.conversation_id == conversation.id,
                    MessageAttachment.message_id.in_(message_ids),
                )
                .order_by(MessageAttachment.created_at.asc(), MessageAttachment.id.asc())
            )
        ).scalars().all()
        for item in attachment_items:
            attachments_by_message.setdefault(item.message_id, []).append(item)

    return {
        "items": [
            message_to_public(item, attachments=attachments_by_message.get(item.id, [])).model_dump()
            for item in items
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/{conversation_id}/tool-events")
async def list_tool_events(
    conversation_id: str,
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=200, ge=1, le=500),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    conversation = await _get_owned_conversation(
        db=db,
        user=user,
        conversation_id=conversation_id,
        request=request,
    )
    where_clause = and_(ToolEvent.conversation_id == conversation.id)

    total = int(
        (
            await db.execute(select(func.count()).select_from(ToolEvent).where(where_clause))
        ).scalar_one()
        or 0
    )
    query = (
        select(ToolEvent)
        .where(where_clause)
        .order_by(ToolEvent.started_at.asc(), ToolEvent.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = (await db.execute(query)).scalars().all()
    return {
        "items": [tool_event_to_public(item).model_dump() for item in items],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/{conversation_id}/sandbox", response_model=SandboxInstancePublic)
async def get_sandbox_status(
    conversation_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SandboxInstancePublic:
    conversation = await _get_owned_conversation(
        db=db,
        user=user,
        conversation_id=conversation_id,
        request=request,
    )
    sandbox = (
        await db.execute(select(SandboxInstance).where(SandboxInstance.conversation_id == conversation.id))
    ).scalar_one_or_none()
    if sandbox is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=tr_app(request.app, "sandbox.not_found"),
        )
    return sandbox_to_public(sandbox)


@router.post("/{conversation_id}/sandbox/restart", response_model=SandboxInstancePublic)
async def restart_sandbox(
    conversation_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SandboxInstancePublic:
    conversation = await _get_owned_conversation(
        db=db,
        user=user,
        conversation_id=conversation_id,
        request=request,
    )
    sandbox = (
        await db.execute(select(SandboxInstance).where(SandboxInstance.conversation_id == conversation.id))
    ).scalar_one_or_none()
    if sandbox is None:
        sandbox = SandboxInstance(
            conversation_id=conversation.id,
            status="pending",
            image=request.app.state.runtime.config.docker_sandbox.image,
        )
        db.add(sandbox)
    else:
        lifecycle_now = now_utc()
        sandbox.status = "pending"
        sandbox.docker_host = None
        sandbox.container_id = None
        sandbox.container_name = None
        sandbox.last_heartbeat_at = None
        # Reset lifecycle timestamp: a restart means a brand-new sandbox container lifecycle.
        sandbox.created_at = lifecycle_now
        sandbox.destroyed_at = None
        sandbox.updated_at = lifecycle_now

    runtime = request.app.state.runtime
    await runtime.close_session(conversation.id)

    await add_audit_log(
        db,
        actor_user_id=user.id,
        action="sandbox.restart",
        target_type="conversation",
        target_id=conversation.id,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    await db.commit()
    await db.refresh(sandbox)
    return sandbox_to_public(sandbox)


@router.get("/{conversation_id}/scheduled-tasks", response_model=list[ScheduledTaskPublic])
async def list_scheduled_tasks(
    conversation_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ScheduledTaskPublic]:
    conversation = await _get_owned_conversation(
        db=db,
        user=user,
        conversation_id=conversation_id,
        request=request,
    )
    items = (
        await db.execute(
            select(ScheduledTask)
            .where(
                ScheduledTask.conversation_id == conversation.id,
                ScheduledTask.user_id == user.id,
            )
            .order_by(ScheduledTask.created_at.desc())
        )
    ).scalars().all()
    return [scheduled_task_to_public(item) for item in items]


@router.post("/{conversation_id}/scheduled-tasks", response_model=ScheduledTaskPublic)
async def create_scheduled_task(
    conversation_id: str,
    payload: ScheduledTaskCreateRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ScheduledTaskPublic:
    conversation = await _get_owned_conversation(
        db=db,
        user=user,
        conversation_id=conversation_id,
        request=request,
    )
    try:
        configured_timezone = getattr(request.app.state.config, "sandbox_timezone", "UTC")
        timezone = (
            payload.timezone
            if ("timezone" in payload.model_fields_set and payload.timezone)
            else configured_timezone
        )
        interval_seconds = (
            int(payload.interval_minutes) * 60
            if payload.interval_minutes is not None
            else None
        )
        normalized = normalize_schedule(
            schedule_type=payload.schedule_type,
            timezone=timezone,
            cron_expr=payload.cron_expr,
            interval_seconds=interval_seconds,
        )
        next_run_at = compute_next_run_at(
            schedule_type=normalized.schedule_type,
            timezone=normalized.timezone,
            cron_expr=normalized.cron_expr,
            interval_seconds=normalized.interval_seconds,
            from_time=now_utc(),
        )
    except ScheduleValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_translate_schedule_validation_error(request, exc),
        ) from exc

    task_type = payload.task_type
    script_command = (payload.script_command or "").strip() or None
    skill_name = (payload.skill_name or "").strip() or None
    skill_input = (payload.skill_input or "").strip() or None

    if task_type == "hybrid_task":
        if not script_command:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=tr_app(request.app, "schedule.hybrid_command_required"),
            )
        skill_name = None
        skill_input = None
    elif task_type == "skill_task":
        if not skill_name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=tr_app(request.app, "schedule.skill_name_required"),
            )
        script_command = None

    summary_prompt = (payload.summary_prompt or "").strip() or None
    task = ScheduledTask(
        conversation_id=conversation.id,
        user_id=user.id,
        name=payload.name.strip(),
        task_type=task_type,
        enabled=payload.enabled,
        schedule_type=normalized.schedule_type,
        timezone=normalized.timezone,
        cron_expr=normalized.cron_expr,
        interval_seconds=normalized.interval_seconds,
        script_command=script_command,
        skill_name=skill_name,
        skill_input=skill_input,
        summary_prompt=summary_prompt,
        max_runs=payload.max_runs,
        run_count=0,
        next_run_at=next_run_at,
    )
    db.add(task)
    await add_audit_log(
        db,
        actor_user_id=user.id,
        action="scheduled_task.create",
        target_type="conversation",
        target_id=conversation.id,
        detail_json={"task_name": task.name, "task_type": task.task_type, "schedule_type": task.schedule_type},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    await db.commit()
    await db.refresh(task)
    return scheduled_task_to_public(task)


@router.patch("/{conversation_id}/scheduled-tasks/{task_id}", response_model=ScheduledTaskPublic)
async def update_scheduled_task(
    conversation_id: str,
    task_id: str,
    payload: ScheduledTaskUpdateRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ScheduledTaskPublic:
    conversation = await _get_owned_conversation(
        db=db,
        user=user,
        conversation_id=conversation_id,
        request=request,
    )
    task = await _get_owned_scheduled_task(
        db=db,
        user=user,
        conversation_id=conversation.id,
        task_id=task_id,
        request=request,
    )

    fields = payload.model_fields_set
    if "task_type" in fields and payload.task_type is not None:
        task.task_type = payload.task_type
    if "name" in fields and payload.name is not None:
        task.name = payload.name.strip()
    if "enabled" in fields and payload.enabled is not None:
        task.enabled = payload.enabled
    if "script_command" in fields:
        task.script_command = (payload.script_command or "").strip() or None
    if "skill_name" in fields:
        task.skill_name = (payload.skill_name or "").strip() or None
    if "skill_input" in fields:
        task.skill_input = (payload.skill_input or "").strip() or None
    if "summary_prompt" in fields:
        task.summary_prompt = (payload.summary_prompt or "").strip() or None
    if "max_runs" in fields:
        task.max_runs = payload.max_runs

    if task.task_type == "hybrid_task":
        if not (task.script_command or "").strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=tr_app(request.app, "schedule.hybrid_command_required"),
            )
        task.skill_name = None
        task.skill_input = None
    elif task.task_type == "skill_task":
        if not (task.skill_name or "").strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=tr_app(request.app, "schedule.skill_name_required"),
            )
        task.script_command = None

    schedule_type = payload.schedule_type if "schedule_type" in fields and payload.schedule_type else task.schedule_type
    timezone = payload.timezone if "timezone" in fields and payload.timezone else task.timezone
    cron_expr = payload.cron_expr if "cron_expr" in fields else task.cron_expr
    interval_seconds = (
        int(payload.interval_minutes) * 60
        if "interval_minutes" in fields and payload.interval_minutes is not None
        else task.interval_seconds
    )
    schedule_changed = any(
        key in fields for key in ("schedule_type", "timezone", "cron_expr", "interval_minutes", "enabled")
    )
    if schedule_changed:
        try:
            normalized = normalize_schedule(
                schedule_type=schedule_type,
                timezone=timezone,
                cron_expr=cron_expr,
                interval_seconds=interval_seconds,
            )
        except ScheduleValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=_translate_schedule_validation_error(request, exc),
            ) from exc
        task.schedule_type = normalized.schedule_type
        task.timezone = normalized.timezone
        task.cron_expr = normalized.cron_expr
        task.interval_seconds = normalized.interval_seconds
        if task.enabled:
            task.next_run_at = compute_next_run_at(
                schedule_type=task.schedule_type,
                timezone=task.timezone,
                cron_expr=task.cron_expr,
                interval_seconds=task.interval_seconds,
                from_time=now_utc(),
            )

    if task.max_runs is not None and task.run_count >= task.max_runs:
        task.enabled = False
        task.run_now_requested_at = None

    await add_audit_log(
        db,
        actor_user_id=user.id,
        action="scheduled_task.update",
        target_type="scheduled_task",
        target_id=task.id,
        detail_json=payload.model_dump(exclude_none=False),
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    await db.commit()
    await db.refresh(task)
    return scheduled_task_to_public(task)


@router.delete("/{conversation_id}/scheduled-tasks/{task_id}", response_model=ConversationFileActionResultPublic)
async def delete_scheduled_task(
    conversation_id: str,
    task_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ConversationFileActionResultPublic:
    conversation = await _get_owned_conversation(
        db=db,
        user=user,
        conversation_id=conversation_id,
        request=request,
    )
    task = await _get_owned_scheduled_task(
        db=db,
        user=user,
        conversation_id=conversation.id,
        task_id=task_id,
        request=request,
    )
    await add_audit_log(
        db,
        actor_user_id=user.id,
        action="scheduled_task.delete",
        target_type="scheduled_task",
        target_id=task.id,
        detail_json={"task_name": task.name},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    await db.delete(task)
    await db.commit()
    return ConversationFileActionResultPublic(
        ok=True,
        message=tr_app(request.app, "conversation.scheduled_task_deleted"),
    )


@router.post(
    "/{conversation_id}/scheduled-tasks/{task_id}/run",
    response_model=ConversationFileActionResultPublic,
)
async def run_scheduled_task_now(
    conversation_id: str,
    task_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ConversationFileActionResultPublic:
    conversation = await _get_owned_conversation(
        db=db,
        user=user,
        conversation_id=conversation_id,
        request=request,
    )
    task = await _get_owned_scheduled_task(
        db=db,
        user=user,
        conversation_id=conversation.id,
        task_id=task_id,
        request=request,
    )
    task.run_now_requested_at = now_utc()
    await add_audit_log(
        db,
        actor_user_id=user.id,
        action="scheduled_task.run_now",
        target_type="scheduled_task",
        target_id=task.id,
        detail_json={"task_name": task.name, "task_type": task.task_type},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    await db.commit()
    return ConversationFileActionResultPublic(
        ok=True,
        message=tr_app(request.app, "conversation.scheduled_task_queued"),
    )


@router.get("/{conversation_id}/scheduled-tasks/{task_id}/runs", response_model=list[ScheduledTaskRunPublic])
async def list_scheduled_task_runs(
    conversation_id: str,
    task_id: str,
    request: Request,
    limit: int = Query(default=20, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ScheduledTaskRunPublic]:
    conversation = await _get_owned_conversation(
        db=db,
        user=user,
        conversation_id=conversation_id,
        request=request,
    )
    task = await _get_owned_scheduled_task(
        db=db,
        user=user,
        conversation_id=conversation.id,
        task_id=task_id,
        request=request,
    )
    runs = (
        await db.execute(
            select(ScheduledTaskRun)
            .where(ScheduledTaskRun.task_id == task.id)
            .order_by(ScheduledTaskRun.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    return [scheduled_task_run_to_public(item) for item in runs]
