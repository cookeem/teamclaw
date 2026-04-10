from __future__ import annotations

import datetime as dt
import os
from pathlib import Path
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.orm_models import (
    AuditLog,
    Conversation,
    Message,
    MessageAttachment,
    ScheduledTask,
    ScheduledTaskRun,
    SandboxInstance,
    ToolEvent,
    User,
)
from app.schemas import (
    AuditLogPublic,
    ConversationPublic,
    MessagePublic,
    ScheduledTaskPublic,
    ScheduledTaskRunPublic,
    SandboxInstancePublic,
    ToolEventPublic,
    UserPublic,
)


def normalize_email(value: str) -> str:
    return value.strip().lower()


def normalize_username(value: str) -> str:
    return value.strip()


def workspace_root_from_env() -> Path:
    raw = os.environ.get("TEAMCLAW_WORKSPACES_ROOT", "workspaces")
    return Path(raw).resolve()


def conversation_workspace_path(conversation_id: str) -> Path:
    return workspace_root_from_env() / conversation_id


async def add_audit_log(
    db: AsyncSession,
    *,
    actor_user_id: str | None,
    action: str,
    target_type: str | None = None,
    target_id: str | None = None,
    result: str = "success",
    detail_json: dict[str, Any] | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> AuditLog:
    log = AuditLog(
        actor_user_id=actor_user_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        result=result,
        detail_json=detail_json,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.add(log)
    await db.flush()
    return log


async def count_users(db: AsyncSession) -> int:
    result = await db.execute(select(func.count()).select_from(User))
    return int(result.scalar_one() or 0)


def user_to_public(user: User) -> UserPublic:
    return UserPublic(
        id=user.id,
        email=user.email,
        username=user.username,
        display_name=user.display_name,
        avatar_url=user.avatar_url,
        is_admin=user.is_admin,
        is_blocked=user.is_blocked,
        conversation_limit=user.conversation_limit,
        last_login_at=user.last_login_at,
        last_active_at=user.last_active_at,
        created_at=user.created_at,
    )


def conversation_to_public(conversation: Conversation) -> ConversationPublic:
    return ConversationPublic(
        id=conversation.id,
        user_id=conversation.user_id,
        title=conversation.title,
        default_provider=conversation.default_provider,
        default_model=conversation.default_model,
        workspace_host_path=conversation.workspace_host_path,
        workspace_mount_path=conversation.workspace_mount_path,
        status=conversation.status,
        is_pinned=conversation.is_pinned,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


def message_attachment_to_public(attachment: MessageAttachment):
    from app.schemas import ConversationAttachmentPublic

    return ConversationAttachmentPublic(
        name=attachment.name,
        path=attachment.path,
        mime_type=attachment.mime_type,
        size=attachment.size,
        kind=("image" if attachment.kind == "image" else "file"),
        workspace_path=attachment.workspace_path,
    )


def message_to_public(
    message: Message,
    attachments: list[MessageAttachment] | None = None,
) -> MessagePublic:
    return MessagePublic(
        id=message.id,
        conversation_id=message.conversation_id,
        role=message.role,
        content=message.content,
        attachments=[message_attachment_to_public(item) for item in (attachments or [])],
        provider=message.provider,
        model=message.model,
        input_tokens=message.input_tokens,
        output_tokens=message.output_tokens,
        total_tokens=message.total_tokens,
        duration_ms=message.duration_ms,
        created_at=message.created_at,
    )


def tool_event_to_public(event: ToolEvent) -> ToolEventPublic:
    return ToolEventPublic(
        id=event.id,
        conversation_id=event.conversation_id,
        message_id=event.message_id,
        tool_call_id=event.tool_call_id,
        tool_name=event.tool_name,
        display_text=event.display_text,
        args_json=event.args_json,
        command=event.command,
        output_text=event.output_text,
        status=event.status,
        exit_code=event.exit_code,
        started_at=event.started_at,
        finished_at=event.finished_at,
        created_at=event.created_at,
    )


def sandbox_to_public(sandbox: SandboxInstance) -> SandboxInstancePublic:
    return SandboxInstancePublic(
        id=sandbox.id,
        conversation_id=sandbox.conversation_id,
        docker_host=sandbox.docker_host,
        image=sandbox.image,
        container_id=sandbox.container_id,
        container_name=sandbox.container_name,
        status=sandbox.status,
        last_heartbeat_at=sandbox.last_heartbeat_at,
        created_at=sandbox.created_at,
        updated_at=sandbox.updated_at,
        destroyed_at=sandbox.destroyed_at,
    )


def audit_to_public(log: AuditLog) -> AuditLogPublic:
    return AuditLogPublic(
        id=log.id,
        actor_user_id=log.actor_user_id,
        action=log.action,
        target_type=log.target_type,
        target_id=log.target_id,
        result=log.result,
        detail_json=log.detail_json,
        ip_address=log.ip_address,
        user_agent=log.user_agent,
        created_at=log.created_at,
    )


def scheduled_task_to_public(task: ScheduledTask) -> ScheduledTaskPublic:
    interval_minutes: int | None = None
    if isinstance(task.interval_seconds, int):
        interval_minutes = max(1, (task.interval_seconds + 59) // 60)
    return ScheduledTaskPublic(
        id=task.id,
        conversation_id=task.conversation_id,
        user_id=task.user_id,
        name=task.name,
        task_type=task.task_type,
        enabled=task.enabled,
        schedule_type=task.schedule_type,
        timezone=task.timezone,
        cron_expr=task.cron_expr,
        interval_minutes=interval_minutes,
        script_command=task.script_command,
        skill_name=task.skill_name,
        skill_input=task.skill_input,
        summary_prompt=task.summary_prompt,
        max_runs=task.max_runs,
        run_count=task.run_count,
        next_run_at=task.next_run_at,
        run_now_requested_at=task.run_now_requested_at,
        last_run_at=task.last_run_at,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


def scheduled_task_run_to_public(run: ScheduledTaskRun) -> ScheduledTaskRunPublic:
    return ScheduledTaskRunPublic(
        id=run.id,
        task_id=run.task_id,
        conversation_id=run.conversation_id,
        user_id=run.user_id,
        status=run.status,
        scheduled_for=run.scheduled_for,
        started_at=run.started_at,
        finished_at=run.finished_at,
        start_message_id=run.start_message_id,
        result_message_id=run.result_message_id,
        script_exit_code=run.script_exit_code,
        script_output_text=run.script_output_text,
        summary_text=run.summary_text,
        error_text=run.error_text,
        created_at=run.created_at,
    )


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def base_conversation_query_for_user(user_id: str) -> Select[tuple[Conversation]]:
    return select(Conversation).where(
        Conversation.user_id == user_id,
        Conversation.deleted_at.is_(None),
    )
