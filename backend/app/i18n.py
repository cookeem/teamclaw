from __future__ import annotations

from collections.abc import Mapping
from typing import Any

SUPPORTED_LANGUAGES = ("en", "zh")
DEFAULT_LANGUAGE = "en"

_MESSAGES: dict[str, dict[str, str]] = {
    "en": {
        "auth.required": "Authentication required",
        "auth.invalid_access_token": "Invalid access token",
        "auth.invalid_access_payload": "Invalid access token payload",
        "auth.user_not_found": "User not found",
        "auth.user_blocked": "User is blocked",
        "auth.admin_required": "Admin role required",
        "auth.email_or_username_exists": "Email or username already exists",
        "auth.invalid_credentials": "Invalid credentials",
        "auth.invalid_refresh_token": "Invalid refresh token",
        "auth.user_blocked_or_missing": "User is blocked or missing",
        "auth.session_inactive": "Session expired due to inactivity. Please log in again.",
        "auth.forgot.generic": "If the account exists, a reset email has been sent.",
        "auth.forgot.code_generation_failed": "Failed to generate verification code, please retry.",
        "auth.forgot.smtp_debug_mode": "SMTP is disabled. Using debug verification code mode.",
        "auth.forgot.smtp_not_configured": "Password reset email failed to send: SMTP is not fully configured.",
        "auth.forgot.smtp_sent": "Password reset email sent successfully. Please check your inbox.",
        "auth.forgot.smtp_failed": "Password reset email failed to send. Please check SMTP settings.",
        "auth.reset.invalid_email_or_code": "Invalid email or verification code",
        "auth.reset.invalid_or_expired_code": "Invalid or expired verification code",
        "user.username_exists": "Username already exists",
        "user.email_exists": "Email already exists",
        "user.current_password_required": "current_password is required when changing password",
        "user.current_password_incorrect": "Current password is incorrect",
        "user.avatar_file_required": "Please upload an avatar file.",
        "user.avatar_type_invalid": "Avatar must be an image file (png/jpg/jpeg/webp/gif).",
        "user.avatar_upload_failed": "Failed to save avatar file.",
        "conversation.not_found": "Conversation not found",
        "conversation.attachment_file_required": "Please upload at least one file.",
        "conversation.attachment_too_large": "File '{name}' is too large (max {max_mb} MB).",
        "conversation.attachment_upload_failed": "Failed to save uploaded file.",
        "conversation.attachment_not_found": "Attachment not found.",
        "conversation.limit_reached": "Conversation limit reached ({limit}). Delete some conversations or contact admin.",
        "conversation.default_title": "New Chat",
        "conversation.scheduled_task_not_found": "Scheduled task not found",
        "conversation.scheduled_task_deleted": "Scheduled task deleted",
        "conversation.scheduled_task_queued": "Scheduled task queued for execution",
        "conversation.files.invalid_skills_directory": "Invalid skills directory configuration.",
        "conversation.files.invalid_root": "Invalid root. Expected 'uploads' or 'skills'.",
        "conversation.files.path_root_mismatch": "Path root mismatch: got '{got}', expected '{expected}'.",
        "conversation.files.path_required": "Path is required.",
        "conversation.files.path_escapes_root": "Path escapes '{root}' root directory.",
        "conversation.files.path_not_found": "Path not found.",
        "conversation.files.directory_escapes_root": "Directory path escapes '{root}' root directory.",
        "conversation.files.target_not_directory": "Target path is not a directory.",
        "conversation.files.directory_not_found": "Directory not found.",
        "conversation.files.invalid_name": "Invalid file or directory name.",
        "conversation.files.too_many_same_name": "Too many files with similar names in this directory.",
        "conversation.files.directory_exists": "Directory already exists.",
        "conversation.files.file_exists": "File already exists.",
        "conversation.files.text_too_large": "Text content is too large.",
        "conversation.files.upload_file_required": "Please upload at least one file.",
        "conversation.files.upload_too_large": "File '{name}' is too large for upload.",
        "conversation.files.rename_root_forbidden": "Cannot rename '{root}' root directory.",
        "conversation.files.rename_target_escapes": "Rename target escapes '{root}' root directory.",
        "conversation.files.name_conflict": "A file or directory with the same name already exists.",
        "conversation.files.delete_root_forbidden": "Cannot delete '{root}' root directory.",
        "conversation.files.directory_not_empty_recursive_required": "Directory is not empty. Set recursive=true to delete.",
        "conversation.files.confirm_name_mismatch": "Confirmation name does not match directory name.",
        "conversation.files.deleted": "Deleted successfully.",
        "conversation.files.path_not_file": "Target path is not a file.",
        "conversation.files.text_too_large_edit": "File is too large to open in editor.",
        "conversation.files.not_text_file": "This file is not recognized as a text file.",
        "conversation.files.not_utf8_text": "Text file must be UTF-8 encoded.",
        "conversation.files.text_content_too_large": "Edited content is too large to save.",
        "conversation.files.autocopy_notice": "New files were generated in `/workspace` root and automatically copied to `/workspace/uploads`. You can view them in the File tab.",
        "conversation.files.autocopy_more": "- ... and {count} more file(s)",
        "conversation.archive.member_path_empty": "Archive member path is empty.",
        "conversation.archive.unsafe_path": "Archive contains unsafe path traversal entry.",
        "conversation.archive.nesting_too_deep": "Archive member path is nested too deep.",
        "conversation.archive.extraction_escapes_target": "Extraction target escapes target directory.",
        "conversation.archive.too_many_entries": "Archive has too many entries.",
        "conversation.archive.symlink_entry": "Archive contains symlink entry which is not allowed.",
        "conversation.archive.member_too_large": "Archive member is too large.",
        "conversation.archive.unsafe_compression_ratio": "Archive member compression ratio is unsafe.",
        "conversation.archive.unpacked_too_large": "Total unpacked size exceeds limit.",
        "conversation.archive.unsupported_link_or_device": "Archive contains unsupported link or device entry.",
        "conversation.archive.path_is_directory": "Archive path must be a file, not a directory.",
        "conversation.archive.unsupported_format": "Unsupported archive format.",
        "conversation.archive.directory_path_must_be_directory": "Directory path must point to a directory.",
        "conversation.archive.directory_too_many_files": "Directory contains too many files to archive.",
        "conversation.archive.directory_oversized_file": "Directory contains an oversized file that cannot be archived.",
        "conversation.archive.directory_total_too_large": "Directory total size exceeds archive limit.",
        "schedule.invalid_type": "schedule_type must be 'cron' or 'interval'.",
        "schedule.invalid_timezone": "Invalid timezone: {timezone}",
        "schedule.interval_required": "interval_minutes is required for interval schedules.",
        "schedule.interval_min": "interval_minutes must be at least 1.",
        "schedule.cron_required": "cron_expr is required for cron schedules.",
        "schedule.cron_fields": "cron_expr must contain 5 fields: minute hour day month weekday.",
        "schedule.cron_unresolvable": "Unable to calculate next cron run time within 2 years.",
        "schedule.cron_field_empty": "Invalid cron field: empty value.",
        "schedule.cron_token_invalid": "Invalid cron token: '{token}'",
        "schedule.cron_step_invalid": "Invalid cron step: '{token}'",
        "schedule.cron_range_invalid": "Invalid cron range: '{token}'",
        "schedule.cron_value_invalid": "Invalid cron value: '{token}'",
        "schedule.cron_value_out_of_range": "Cron value out of range: '{token}'",
        "schedule.cron_field_invalid": "Invalid cron field: '{token}'",
        "schedule.validation_error": "Invalid schedule configuration.",
        "schedule.hybrid_command_required": "script_command is required for hybrid_task.",
        "schedule.skill_name_required": "skill_name is required for skill_task.",
        "sandbox.not_found": "Sandbox not found",
        "avatar.not_found": "Avatar not found",
        "admin.user_not_found": "User not found",
        "admin.self_admin_block_update_forbidden": "You cannot change your own admin or blocked status",
        "admin.last_active_admin_required": "At least one active admin must remain",
        "admin.self_delete_forbidden": "You cannot delete your own account",
        "admin.audit_log_not_found": "Audit log not found",
        "ws.invalid_payload": "Invalid request payload: {errors}",
        "ws.validation.required": "required",
        "ws.validation.invalid_cursor": "invalid cursor",
        "ws.validation.unsupported_message_type": "unsupported message type",
        "ws.auth_required": "Authentication required",
        "ws.user_blocked_or_missing": "User is blocked or missing",
        "ws.conversation_not_found": "Conversation not found",
        "ws.conversation_running": "Conversation is already running. Please wait for completion.",
        "ws.agent_execution_failed": "Agent execution failed: {error}",
        "validation.password_policy": "Password must include uppercase, lowercase, number, and special character.",
        "validation.message_or_attachments_required": "message or attachments must be provided",
        "scheduled.error.worker_restarted": "Worker restarted before task run was completed.",
        "scheduled.error.invalid_schedule_definition": "Invalid schedule definition: {error}",
        "scheduled.error.context_missing": "Task conversation or user no longer exists.",
        "scheduled.error.conversation_deleted": "Conversation is deleted; task run aborted.",
        "scheduled.error.conversation_busy_running": "Conversation is busy running another request.",
        "scheduled.error.task_missing_when_updating_counter": "Task no longer exists while updating run counters.",
        "scheduled.message.task_skipped_busy": "{marker} Task '{task_name}' skipped because the conversation is currently busy.",
        "scheduled.message.task_started": "{marker} Task '{task_name}' started at {scheduled_for}.",
        "scheduled.message.task_started_manual": "{marker} Task '{task_name}' started at {scheduled_for} (manual trigger).",
        "scheduled.message.task_failed_before_summary": "{marker} Task '{task_name}' failed before summary: {error}",
        "scheduled.error.script_execution_failed": "Script execution failed: {error}",
        "scheduled.error.script_exited_with_code": "Script exited with code {exit_code}.",
        "scheduled.error.summary_rejected_busy": "Conversation is busy and LLM summary run was rejected.",
        "scheduled.message.summary_skipped_busy": "{marker} Task '{task_name}' summary skipped because the conversation is busy.",
        "scheduled.message.summary_timeout": "{marker} Task '{task_name}' timed out while waiting for summary.",
        "scheduled.error.summary_timeout": "Timed out while waiting for LLM summary.",
        "scheduled.message.summary_empty": "{marker} Task '{task_name}' completed but no summary message was produced.",
        "scheduled.error.summary_empty": "LLM summary finished but no assistant message was produced.",
        "scheduled.message.skill_missing_name": "{marker} Task '{task_name}' failed: missing skill_name for skill_task.",
        "scheduled.error.skill_name_required": "skill_name is required for skill_task.",
        "scheduled.error.skill_rejected_busy": "Conversation is busy and skill task run was rejected.",
        "scheduled.message.skill_skipped_busy": "{marker} Task '{task_name}' skipped because the conversation is busy.",
        "scheduled.message.skill_timeout": "{marker} Task '{task_name}' timed out while waiting for skill execution.",
        "scheduled.error.skill_timeout": "Timed out while waiting for skill task execution.",
        "scheduled.message.skill_empty": "{marker} Task '{task_name}' completed but no assistant message was produced.",
        "scheduled.error.skill_empty": "Skill task run finished but no assistant message was produced.",
        "scheduled.error.skill_no_tool_result": "No tool execution result recorded for skill_task.",
        "scheduled.error.skill_last_exit_code": "Last tool exit_code is {exit_code}.",
        "scheduled.error.skill_last_status": "Last tool result is '{status}'.",
        "scheduled.prompt.hybrid_default": "Please summarize the script execution result in Chinese. Include: overall status, key findings, and next actions.",
        "scheduled.prompt.truncated_for_summary": "... (truncated for summary input)",
        "scheduled.prompt.no_output": "<no output>",
        "scheduled.prompt.hybrid_request": (
            "This is an automated hybrid scheduled task run.\n"
            "Task Name: {task_name}\n"
            "Please summarize this run for the user.\n\n"
            "Custom Prompt:\n{custom_prompt}\n\n"
            "Script Command:\n{script_command}\n\n"
            "Script Exit Code: {script_exit_code}\n\n"
            "Script Output:\n"
            "```text\n"
            "{script_output}\n"
            "```"
        ),
        "scheduled.prompt.skill_default": "Please execute this skill task via tools and provide a Chinese summary including execution status, key steps, output paths, and next suggestions.",
        "scheduled.prompt.skill_request": (
            "This is an automated skill scheduled task run.\n"
            "Task Name: {task_name}\n"
            "Skill Name: {skill_name}\n"
            "Skill Input: {skill_input}\n\n"
            "Requirements:\n"
            "- You should execute this task by using available tools/skills in sandbox.\n"
            "- Provide clear progress and final result for user.\n"
            "- Keep operations scoped to the current conversation workspace.\n\n"
            "Custom Prompt:\n{custom_prompt}\n"
        ),
        "runtime.scheduled.name_required": "name is required",
        "runtime.scheduled.name_empty": "name cannot be empty",
        "runtime.scheduled.name_too_long": "name is too long (max 128 chars)",
        "runtime.scheduled.invalid_task_type": "task_type must be hybrid_task or skill_task",
        "runtime.scheduled.script_command_too_long": "script_command is too long (max 20000 chars)",
        "runtime.scheduled.skill_name_too_long": "skill_name is too long (max 128 chars)",
        "runtime.scheduled.skill_input_too_long": "skill_input is too long (max 20000 chars)",
        "runtime.scheduled.validation_error_detail": "Invalid schedule configuration: {error}",
        "runtime.scheduled.summary_prompt_too_long": "summary_prompt is too long (max 10000 chars)",
        "runtime.scheduled.max_runs_must_be_int": "max_runs must be an integer >= 1",
        "runtime.scheduled.max_runs_min": "max_runs must be >= 1",
        "runtime.scheduled.task_id_required": "task_id is required",
        "runtime.scheduled.task_not_found": "scheduled task not found: {task_id}",
        "runtime.scheduled.task_reached_max_runs": "task has reached max_runs and cannot be enabled",
        "runtime.sandbox.no_output_skill_tool": "No output from sandbox skill tool.",
        "runtime.sandbox.skill_tool_failed": "Skill tool {tool_name} failed: {error}",
        "runtime.sandbox.no_output_network_tool": "No output from sandbox tool.",
        "runtime.sandbox.fetch_url_error": "Fetch URL error: {error}",
        "runtime.sandbox.request_error": "Request error: {error}",
    },
    "zh": {
        "auth.required": "需要登录认证",
        "auth.invalid_access_token": "访问令牌无效",
        "auth.invalid_access_payload": "访问令牌载荷无效",
        "auth.user_not_found": "用户不存在",
        "auth.user_blocked": "用户已被禁用",
        "auth.admin_required": "需要管理员权限",
        "auth.email_or_username_exists": "邮箱或用户名已存在",
        "auth.invalid_credentials": "账号或密码错误",
        "auth.invalid_refresh_token": "刷新令牌无效",
        "auth.user_blocked_or_missing": "用户已被禁用或不存在",
        "auth.session_inactive": "登录已因长时间不活跃失效，请重新登录。",
        "auth.forgot.generic": "如果账号存在，我们已发送重置邮件。",
        "auth.forgot.code_generation_failed": "生成验证码失败，请重试。",
        "auth.forgot.smtp_debug_mode": "SMTP 未启用，当前使用调试验证码模式。",
        "auth.forgot.smtp_not_configured": "重置邮件发送失败：SMTP 配置不完整。",
        "auth.forgot.smtp_sent": "重置邮件发送成功，请查收邮箱。",
        "auth.forgot.smtp_failed": "重置邮件发送失败，请检查 SMTP 配置。",
        "auth.reset.invalid_email_or_code": "邮箱或验证码无效",
        "auth.reset.invalid_or_expired_code": "验证码无效或已过期",
        "user.username_exists": "用户名已存在",
        "user.email_exists": "邮箱已存在",
        "user.current_password_required": "修改密码时必须提供 current_password",
        "user.current_password_incorrect": "当前密码不正确",
        "user.avatar_file_required": "请上传头像文件。",
        "user.avatar_type_invalid": "头像文件必须是图片（png/jpg/jpeg/webp/gif）。",
        "user.avatar_upload_failed": "头像保存失败。",
        "conversation.not_found": "对话不存在",
        "conversation.attachment_file_required": "请至少上传一个文件。",
        "conversation.attachment_too_large": "文件“{name}”过大（最大 {max_mb} MB）。",
        "conversation.attachment_upload_failed": "上传文件保存失败。",
        "conversation.attachment_not_found": "附件不存在。",
        "conversation.limit_reached": "已达到对话数量上限（{limit}）。请删除部分对话或联系管理员。",
        "conversation.default_title": "新对话",
        "conversation.scheduled_task_not_found": "计划任务不存在",
        "conversation.scheduled_task_deleted": "计划任务已删除",
        "conversation.scheduled_task_queued": "计划任务已加入执行队列",
        "conversation.files.invalid_skills_directory": "技能目录配置无效。",
        "conversation.files.invalid_root": "根目录参数无效，只允许 uploads 或 skills。",
        "conversation.files.path_root_mismatch": "路径根目录不匹配：当前为“{got}”，期望为“{expected}”。",
        "conversation.files.path_required": "必须提供路径。",
        "conversation.files.path_escapes_root": "路径越界，超出“{root}”根目录。",
        "conversation.files.path_not_found": "路径不存在。",
        "conversation.files.directory_escapes_root": "目录路径越界，超出“{root}”根目录。",
        "conversation.files.target_not_directory": "目标路径不是目录。",
        "conversation.files.directory_not_found": "目录不存在。",
        "conversation.files.invalid_name": "文件或目录名称无效。",
        "conversation.files.too_many_same_name": "同名文件过多，无法自动生成唯一名称。",
        "conversation.files.directory_exists": "目录已存在。",
        "conversation.files.file_exists": "文件已存在。",
        "conversation.files.text_too_large": "文本内容过大。",
        "conversation.files.upload_file_required": "请至少上传一个文件。",
        "conversation.files.upload_too_large": "文件“{name}”过大，无法上传。",
        "conversation.files.rename_root_forbidden": "不能重命名“{root}”根目录。",
        "conversation.files.rename_target_escapes": "重命名目标越界，超出“{root}”根目录。",
        "conversation.files.name_conflict": "目标名称冲突，已存在同名文件或目录。",
        "conversation.files.delete_root_forbidden": "不能删除“{root}”根目录。",
        "conversation.files.directory_not_empty_recursive_required": "目录非空，需设置 recursive=true 才能删除。",
        "conversation.files.confirm_name_mismatch": "确认名称与目录名称不一致。",
        "conversation.files.deleted": "删除成功。",
        "conversation.files.path_not_file": "目标路径不是文件。",
        "conversation.files.text_too_large_edit": "文件过大，无法在编辑器中打开。",
        "conversation.files.not_text_file": "该文件不是可识别的文本文件。",
        "conversation.files.not_utf8_text": "文本文件必须为 UTF-8 编码。",
        "conversation.files.text_content_too_large": "编辑后内容过大，无法保存。",
        "conversation.files.autocopy_notice": "检测到本轮在 `/workspace` 根目录生成了新文件，已自动复制到 `/workspace/uploads`，可在“文件”标签页查看。",
        "conversation.files.autocopy_more": "- ... 还有 {count} 个文件",
        "conversation.archive.member_path_empty": "压缩包成员路径为空。",
        "conversation.archive.unsafe_path": "压缩包包含不安全路径（路径穿越）。",
        "conversation.archive.nesting_too_deep": "压缩包目录层级过深。",
        "conversation.archive.extraction_escapes_target": "解压目标越界，超出目标目录。",
        "conversation.archive.too_many_entries": "压缩包包含条目过多。",
        "conversation.archive.symlink_entry": "压缩包包含符号链接条目，不允许解压。",
        "conversation.archive.member_too_large": "压缩包中存在超大文件。",
        "conversation.archive.unsafe_compression_ratio": "压缩包中存在可疑压缩比（疑似压缩炸弹）。",
        "conversation.archive.unpacked_too_large": "解压后总大小超出限制。",
        "conversation.archive.unsupported_link_or_device": "压缩包包含不支持的链接或设备文件。",
        "conversation.archive.path_is_directory": "压缩包路径必须是文件，不能是目录。",
        "conversation.archive.unsupported_format": "不支持的压缩格式。",
        "conversation.archive.directory_path_must_be_directory": "目录路径必须指向目录。",
        "conversation.archive.directory_too_many_files": "目录中文件过多，无法压缩。",
        "conversation.archive.directory_oversized_file": "目录中存在超大文件，无法压缩。",
        "conversation.archive.directory_total_too_large": "目录总大小超出压缩限制。",
        "schedule.invalid_type": "schedule_type 必须是 'cron' 或 'interval'。",
        "schedule.invalid_timezone": "时区无效：{timezone}",
        "schedule.interval_required": "interval 类型任务必须提供 interval_minutes。",
        "schedule.interval_min": "interval_minutes 必须大于等于 1。",
        "schedule.cron_required": "cron 类型任务必须提供 cron_expr。",
        "schedule.cron_fields": "cron_expr 必须包含 5 个字段：minute hour day month weekday。",
        "schedule.cron_unresolvable": "无法在 2 年内计算出下一次 cron 执行时间。",
        "schedule.cron_field_empty": "无效的 cron 字段：为空。",
        "schedule.cron_token_invalid": "无效的 cron token：'{token}'",
        "schedule.cron_step_invalid": "无效的 cron 步长：'{token}'",
        "schedule.cron_range_invalid": "无效的 cron 范围：'{token}'",
        "schedule.cron_value_invalid": "无效的 cron 值：'{token}'",
        "schedule.cron_value_out_of_range": "cron 值超出范围：'{token}'",
        "schedule.cron_field_invalid": "无效的 cron 字段：'{token}'",
        "schedule.validation_error": "计划任务配置无效。",
        "schedule.hybrid_command_required": "hybrid_task 必须提供 script_command。",
        "schedule.skill_name_required": "skill_task 必须提供 skill_name。",
        "sandbox.not_found": "Sandbox 不存在",
        "avatar.not_found": "头像不存在",
        "admin.user_not_found": "用户不存在",
        "admin.self_admin_block_update_forbidden": "不能修改自己的管理员或禁用状态",
        "admin.last_active_admin_required": "系统至少需要保留一个可用管理员",
        "admin.self_delete_forbidden": "不能删除自己的账号",
        "admin.audit_log_not_found": "审计日志不存在",
        "ws.invalid_payload": "请求参数无效: {errors}",
        "ws.validation.required": "必填",
        "ws.validation.invalid_cursor": "cursor 无效",
        "ws.validation.unsupported_message_type": "不支持的消息类型",
        "ws.auth_required": "需要登录认证",
        "ws.user_blocked_or_missing": "用户已被禁用或不存在",
        "ws.conversation_not_found": "对话不存在",
        "ws.conversation_running": "当前对话正在执行中，请等待完成后再试。",
        "ws.agent_execution_failed": "智能体执行失败: {error}",
        "validation.password_policy": "密码必须包含大写字母、小写字母、数字和特殊字符。",
        "validation.message_or_attachments_required": "message 和 attachments 不能同时为空。",
        "scheduled.error.worker_restarted": "任务执行过程中 Worker 重启，任务已标记失败。",
        "scheduled.error.invalid_schedule_definition": "计划任务配置无效：{error}",
        "scheduled.error.context_missing": "任务对应的对话或用户不存在。",
        "scheduled.error.conversation_deleted": "对话已删除，任务执行已中止。",
        "scheduled.error.conversation_busy_running": "对话正在处理其他请求，任务被跳过。",
        "scheduled.error.task_missing_when_updating_counter": "更新执行计数时任务不存在。",
        "scheduled.message.task_skipped_busy": "{marker} 任务“{task_name}”已跳过：当前对话正忙。",
        "scheduled.message.task_started": "{marker} 任务“{task_name}”已启动，计划时间：{scheduled_for}。",
        "scheduled.message.task_started_manual": "{marker} 任务“{task_name}”已启动（手动触发），计划时间：{scheduled_for}。",
        "scheduled.message.task_failed_before_summary": "{marker} 任务“{task_name}”在总结前失败：{error}",
        "scheduled.error.script_execution_failed": "脚本执行失败：{error}",
        "scheduled.error.script_exited_with_code": "脚本以退出码 {exit_code} 结束。",
        "scheduled.error.summary_rejected_busy": "对话正忙，LLM 总结任务被拒绝执行。",
        "scheduled.message.summary_skipped_busy": "{marker} 任务“{task_name}”总结已跳过：当前对话正忙。",
        "scheduled.message.summary_timeout": "{marker} 任务“{task_name}”等待总结超时。",
        "scheduled.error.summary_timeout": "等待 LLM 总结超时。",
        "scheduled.message.summary_empty": "{marker} 任务“{task_name}”已完成，但未生成总结消息。",
        "scheduled.error.summary_empty": "LLM 总结完成但未产出助手消息。",
        "scheduled.message.skill_missing_name": "{marker} 任务“{task_name}”失败：skill_task 缺少 skill_name。",
        "scheduled.error.skill_name_required": "skill_task 必须提供 skill_name。",
        "scheduled.error.skill_rejected_busy": "对话正忙，技能任务被拒绝执行。",
        "scheduled.message.skill_skipped_busy": "{marker} 任务“{task_name}”已跳过：当前对话正忙。",
        "scheduled.message.skill_timeout": "{marker} 任务“{task_name}”等待技能执行超时。",
        "scheduled.error.skill_timeout": "等待技能任务执行超时。",
        "scheduled.message.skill_empty": "{marker} 任务“{task_name}”已完成，但未生成助手消息。",
        "scheduled.error.skill_empty": "技能任务执行完成但未产出助手消息。",
        "scheduled.error.skill_no_tool_result": "未记录到 skill_task 的工具执行结果。",
        "scheduled.error.skill_last_exit_code": "最后一个工具退出码为 {exit_code}。",
        "scheduled.error.skill_last_status": "最后一个工具状态为“{status}”。",
        "scheduled.prompt.hybrid_default": "请用中文总结脚本执行结果，包含：整体状态、关键发现、后续建议。",
        "scheduled.prompt.truncated_for_summary": "...（为总结输入已截断）",
        "scheduled.prompt.no_output": "<无输出>",
        "scheduled.prompt.hybrid_request": (
            "这是一次自动触发的 hybrid 计划任务。\n"
            "任务名：{task_name}\n"
            "请为用户总结本次执行结果。\n\n"
            "自定义提示词：\n{custom_prompt}\n\n"
            "脚本命令：\n{script_command}\n\n"
            "脚本退出码：{script_exit_code}\n\n"
            "脚本输出：\n"
            "```text\n"
            "{script_output}\n"
            "```"
        ),
        "scheduled.prompt.skill_default": "请执行该技能任务，并在执行过程中通过工具调用推进任务；最后用中文输出结果总结，包含执行状态、关键步骤、产物路径、后续建议。",
        "scheduled.prompt.skill_request": (
            "这是一次自动触发的 skill 计划任务。\n"
            "任务名：{task_name}\n"
            "技能名：{skill_name}\n"
            "技能输入：{skill_input}\n\n"
            "要求：\n"
            "- 通过可用工具/技能完成任务。\n"
            "- 对用户清晰反馈执行进展和最终结果。\n"
            "- 操作范围仅限当前对话工作目录。\n\n"
            "自定义提示词：\n{custom_prompt}\n"
        ),
        "runtime.scheduled.name_required": "必须提供 name",
        "runtime.scheduled.name_empty": "name 不能为空",
        "runtime.scheduled.name_too_long": "name 过长（最多 128 个字符）",
        "runtime.scheduled.invalid_task_type": "task_type 必须为 hybrid_task 或 skill_task",
        "runtime.scheduled.script_command_too_long": "script_command 过长（最多 20000 个字符）",
        "runtime.scheduled.skill_name_too_long": "skill_name 过长（最多 128 个字符）",
        "runtime.scheduled.skill_input_too_long": "skill_input 过长（最多 20000 个字符）",
        "runtime.scheduled.validation_error_detail": "计划任务配置无效：{error}",
        "runtime.scheduled.summary_prompt_too_long": "summary_prompt 过长（最多 10000 个字符）",
        "runtime.scheduled.max_runs_must_be_int": "max_runs 必须是大于等于 1 的整数",
        "runtime.scheduled.max_runs_min": "max_runs 必须大于等于 1",
        "runtime.scheduled.task_id_required": "必须提供 task_id",
        "runtime.scheduled.task_not_found": "计划任务不存在：{task_id}",
        "runtime.scheduled.task_reached_max_runs": "任务已达到 max_runs 上限，不能启用",
        "runtime.sandbox.no_output_skill_tool": "沙箱技能工具无输出。",
        "runtime.sandbox.skill_tool_failed": "技能工具 {tool_name} 执行失败：{error}",
        "runtime.sandbox.no_output_network_tool": "沙箱网络工具无输出。",
        "runtime.sandbox.fetch_url_error": "抓取 URL 失败：{error}",
        "runtime.sandbox.request_error": "请求失败：{error}",
    },
}


def normalize_language(raw: Any) -> str:
    if isinstance(raw, str):
        cleaned = raw.strip().lower()
        if cleaned in SUPPORTED_LANGUAGES:
            return cleaned
    return DEFAULT_LANGUAGE


def get_config_language(config: Any) -> str:
    raw_value = getattr(config, "language", DEFAULT_LANGUAGE)
    return normalize_language(raw_value)


def get_app_language(app: Any) -> str:
    config = getattr(getattr(app, "state", object()), "config", None)
    if config is None:
        return DEFAULT_LANGUAGE
    return get_config_language(config)


def tr(key: str, *, language: str, params: Mapping[str, Any] | None = None) -> str:
    lang = normalize_language(language)
    text = _MESSAGES.get(lang, {}).get(key)
    if text is None:
        text = _MESSAGES[DEFAULT_LANGUAGE].get(key, key)
    if params:
        for name, value in params.items():
            text = text.replace(f"{{{name}}}", str(value))
    return text


def tr_app(app: Any, key: str, **params: Any) -> str:
    return tr(key, language=get_app_language(app), params=params or None)
