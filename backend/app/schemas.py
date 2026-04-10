from __future__ import annotations

import datetime as dt
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

USERNAME_MIN_LENGTH = 4
USERNAME_MAX_LENGTH = 64
EMAIL_MIN_LENGTH = 8
EMAIL_MAX_LENGTH = 128
PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 256
PASSWORD_POLICY_ERROR = "Password must include uppercase, lowercase, number, and special character."
_PASSWORD_SPECIAL_PATTERN = re.compile(r"[^A-Za-z0-9\s]")


def _is_password_complex(value: str) -> bool:
    if not any(ch.islower() for ch in value):
        return False
    if not any(ch.isupper() for ch in value):
        return False
    if not any(ch.isdigit() for ch in value):
        return False
    if _PASSWORD_SPECIAL_PATTERN.search(value) is None:
        return False
    return True


class ChatAttachmentInput(BaseModel):
    path: str = Field(min_length=1, max_length=512)
    name: str | None = Field(default=None, max_length=255)
    mime_type: str | None = Field(default=None, max_length=128)
    size: int | None = Field(default=None, ge=0)


class ChatRequest(BaseModel):
    type: Literal["chat"] = "chat"
    session_id: str = Field(min_length=1)
    message: str = ""
    attachments: list[ChatAttachmentInput] = Field(default_factory=list)
    provider: str | None = None
    model: str | None = None

    @model_validator(mode="after")
    def validate_non_empty_input(self) -> "ChatRequest":
        if self.message.strip() or self.attachments:
            return self
        raise ValueError("message or attachments must be provided")


class UserPublic(BaseModel):
    id: str
    email: str
    username: str
    display_name: str | None = None
    avatar_url: str | None = None
    is_admin: bool
    is_blocked: bool
    conversation_limit: int | None = None
    last_login_at: dt.datetime | None = None
    last_active_at: dt.datetime | None = None
    created_at: dt.datetime


class AuthTokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    user: UserPublic


class SignupRequest(BaseModel):
    email: str = Field(min_length=EMAIL_MIN_LENGTH, max_length=EMAIL_MAX_LENGTH)
    username: str = Field(min_length=USERNAME_MIN_LENGTH, max_length=USERNAME_MAX_LENGTH)
    password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)
    display_name: str | None = Field(default=None, max_length=128)

    @field_validator("password")
    @classmethod
    def validate_password_policy(cls, value: str) -> str:
        if not _is_password_complex(value):
            raise ValueError(PASSWORD_POLICY_ERROR)
        return value


class LoginRequest(BaseModel):
    account: str = Field(min_length=2, max_length=320)
    password: str = Field(min_length=8, max_length=256)


class RefreshTokenRequest(BaseModel):
    refresh_token: str = Field(min_length=20, max_length=512)


class LogoutRequest(BaseModel):
    refresh_token: str | None = Field(default=None, max_length=512)
    revoke_all: bool = False


class ForgotPasswordRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)


class ForgotPasswordResponse(BaseModel):
    ok: bool
    delivery: Literal["email", "debug_token", "failed", "none"] = "none"
    message: str | None = None
    error: str | None = None
    reset_code: str | None = None
    # Backward compatibility for older frontend field.
    reset_token: str | None = None
    expires_at: dt.datetime | None = None


class ResetPasswordRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")
    new_password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)

    @field_validator("new_password")
    @classmethod
    def validate_new_password_policy(cls, value: str) -> str:
        if not _is_password_complex(value):
            raise ValueError(PASSWORD_POLICY_ERROR)
        return value


class UpdateMeRequest(BaseModel):
    display_name: str | None = Field(default=None, max_length=128)
    email: str | None = Field(default=None, min_length=EMAIL_MIN_LENGTH, max_length=EMAIL_MAX_LENGTH)
    current_password: str | None = Field(default=None, min_length=8, max_length=256)
    new_password: str | None = Field(default=None, min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)

    @field_validator("new_password")
    @classmethod
    def validate_optional_new_password_policy(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _is_password_complex(value):
            raise ValueError(PASSWORD_POLICY_ERROR)
        return value


class AdminUpdateUserRequest(BaseModel):
    display_name: str | None = Field(default=None, max_length=128)
    email: str | None = Field(default=None, min_length=EMAIL_MIN_LENGTH, max_length=EMAIL_MAX_LENGTH)
    new_password: str | None = Field(default=None, min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)
    is_admin: bool | None = None
    is_blocked: bool | None = None
    conversation_limit: int | None = Field(default=None, ge=-1)

    @field_validator("new_password")
    @classmethod
    def validate_optional_admin_password_policy(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _is_password_complex(value):
            raise ValueError(PASSWORD_POLICY_ERROR)
        return value


class AdminCreateUserRequest(BaseModel):
    email: str = Field(min_length=EMAIL_MIN_LENGTH, max_length=EMAIL_MAX_LENGTH)
    username: str = Field(min_length=USERNAME_MIN_LENGTH, max_length=USERNAME_MAX_LENGTH)
    password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)
    display_name: str | None = Field(default=None, max_length=128)
    is_admin: bool = False
    is_blocked: bool = False
    conversation_limit: int | None = Field(default=None, ge=-1)

    @field_validator("password")
    @classmethod
    def validate_admin_password_policy(cls, value: str) -> str:
        if not _is_password_complex(value):
            raise ValueError(PASSWORD_POLICY_ERROR)
        return value


class ConversationCreateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    default_provider: str | None = Field(default=None, max_length=64)
    default_model: str | None = Field(default=None, max_length=128)


class ConversationUpdateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    default_provider: str | None = Field(default=None, max_length=64)
    default_model: str | None = Field(default=None, max_length=128)
    is_pinned: bool | None = None
    status: Literal["active", "archived"] | None = None


class ConversationPublic(BaseModel):
    id: str
    user_id: str
    title: str
    default_provider: str | None
    default_model: str | None
    workspace_host_path: str
    workspace_mount_path: str
    status: str
    is_pinned: bool
    created_at: dt.datetime
    updated_at: dt.datetime


class ConversationAttachmentPublic(BaseModel):
    name: str
    path: str
    mime_type: str | None
    size: int
    kind: Literal["image", "file"]
    workspace_path: str


class ConversationFileNodePublic(BaseModel):
    path: str
    name: str
    node_type: Literal["directory", "file"]
    size: int | None = None
    mime_type: str | None = None
    is_text: bool = False
    created_at: dt.datetime
    modified_at: dt.datetime
    children: list["ConversationFileNodePublic"] = Field(default_factory=list)


class ConversationFileTreePublic(BaseModel):
    root_path: str
    items: list[ConversationFileNodePublic] = Field(default_factory=list)


class ConversationFileMkdirRequest(BaseModel):
    directory_path: str = Field(min_length=1, max_length=1024)


class ConversationFileCreateTextRequest(BaseModel):
    file_path: str = Field(min_length=1, max_length=1024)
    content: str = ""


class ConversationFileRenameRequest(BaseModel):
    path: str = Field(min_length=1, max_length=1024)
    new_name: str = Field(min_length=1, max_length=255)


class ConversationFileDeleteRequest(BaseModel):
    path: str = Field(min_length=1, max_length=1024)
    recursive: bool = False
    confirm_name: str | None = Field(default=None, max_length=255)


class ConversationFileExtractRequest(BaseModel):
    archive_path: str = Field(min_length=1, max_length=1024)
    target_dir: str | None = Field(default=None, max_length=1024)


class ConversationFileArchiveRequest(BaseModel):
    directory_path: str = Field(min_length=1, max_length=1024)
    target_dir: str | None = Field(default=None, max_length=1024)
    output_name: str | None = Field(default=None, max_length=255)


class ConversationFileTextWriteRequest(BaseModel):
    path: str = Field(min_length=1, max_length=1024)
    content: str = ""


class ConversationFileActionResultPublic(BaseModel):
    ok: bool = True
    message: str | None = None
    path: str | None = None


class ConversationFileExtractResultPublic(BaseModel):
    ok: bool = True
    target_path: str
    extracted_count: int


class ConversationFileTextContentPublic(BaseModel):
    path: str
    size: int
    content: str
    is_text: bool = True


class ScheduledTaskCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    task_type: Literal["hybrid_task", "skill_task"] = "hybrid_task"
    enabled: bool = True
    schedule_type: Literal["cron", "interval"] = "cron"
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    cron_expr: str | None = Field(default=None, max_length=128)
    interval_minutes: int | None = Field(default=None, ge=1)
    script_command: str | None = Field(default=None, max_length=20000)
    skill_name: str | None = Field(default=None, max_length=128)
    skill_input: str | None = Field(default=None, max_length=20000)
    summary_prompt: str | None = Field(default=None, max_length=10000)
    max_runs: int | None = Field(default=None, ge=1)


class ScheduledTaskUpdateRequest(BaseModel):
    task_type: Literal["hybrid_task", "skill_task"] | None = None
    name: str | None = Field(default=None, min_length=1, max_length=128)
    enabled: bool | None = None
    schedule_type: Literal["cron", "interval"] | None = None
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    cron_expr: str | None = Field(default=None, max_length=128)
    interval_minutes: int | None = Field(default=None, ge=1)
    script_command: str | None = Field(default=None, max_length=20000)
    skill_name: str | None = Field(default=None, max_length=128)
    skill_input: str | None = Field(default=None, max_length=20000)
    summary_prompt: str | None = Field(default=None, max_length=10000)
    max_runs: int | None = Field(default=None, ge=1)


class ScheduledTaskPublic(BaseModel):
    id: str
    conversation_id: str
    user_id: str
    name: str
    task_type: str
    enabled: bool
    schedule_type: str
    timezone: str
    cron_expr: str | None
    interval_minutes: int | None
    script_command: str | None
    skill_name: str | None
    skill_input: str | None
    summary_prompt: str | None
    max_runs: int | None
    run_count: int
    next_run_at: dt.datetime
    run_now_requested_at: dt.datetime | None
    last_run_at: dt.datetime | None
    created_at: dt.datetime
    updated_at: dt.datetime


class ScheduledTaskRunPublic(BaseModel):
    id: str
    task_id: str
    conversation_id: str
    user_id: str
    status: str
    scheduled_for: dt.datetime
    started_at: dt.datetime | None
    finished_at: dt.datetime | None
    start_message_id: str | None
    result_message_id: str | None
    script_exit_code: int | None
    script_output_text: str | None
    summary_text: str | None
    error_text: str | None
    created_at: dt.datetime


class MessagePublic(BaseModel):
    id: str
    conversation_id: str
    role: str
    content: str
    attachments: list[ConversationAttachmentPublic] = Field(default_factory=list)
    provider: str | None
    model: str | None
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    duration_ms: int | None
    created_at: dt.datetime


class ToolEventPublic(BaseModel):
    id: str
    conversation_id: str
    message_id: str | None
    tool_call_id: str | None
    tool_name: str
    display_text: str | None
    args_json: dict[str, Any] | None
    command: str | None
    output_text: str | None
    status: str
    exit_code: int | None
    started_at: dt.datetime
    finished_at: dt.datetime | None
    created_at: dt.datetime


class SandboxInstancePublic(BaseModel):
    id: str
    conversation_id: str
    docker_host: str | None
    image: str | None
    container_id: str | None
    container_name: str | None
    status: str
    last_heartbeat_at: dt.datetime | None
    created_at: dt.datetime
    updated_at: dt.datetime
    destroyed_at: dt.datetime | None


class AuditLogPublic(BaseModel):
    id: str
    actor_user_id: str | None
    action: str
    target_type: str | None
    target_id: str | None
    result: str
    detail_json: dict[str, Any] | None
    ip_address: str | None
    user_agent: str | None
    created_at: dt.datetime
