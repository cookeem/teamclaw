from __future__ import annotations

import asyncio
import datetime as dt
import logging
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from sqlalchemy import or_, select

from app.agent_runtime import WebAgentRuntime
from app.crud import now_utc
from app.db import session_factory
from app.i18n import get_config_language, tr
from app.orm_models import Conversation, Message, ScheduledTask, ScheduledTaskRun, ToolEvent, User
from app.schemas import ChatRequest
from app.scheduling import ScheduleValidationError, compute_next_run_at

logger = logging.getLogger(__name__)
SCHEDULED_START_MARKER = "[scheduled-task:start]"
SCHEDULED_RESULT_MARKER = "[scheduled-task:result]"


@dataclass(frozen=True)
class ClaimedScheduledRun:
    run_id: str
    task_id: str
    conversation_id: str
    user_id: str
    scheduled_for: dt.datetime
    manual_trigger: bool


class ScheduledTaskWorker:
    def __init__(
        self,
        *,
        runtime: WebAgentRuntime,
        run_manager: Any,
        poll_interval_seconds: int,
        batch_size: int,
        llm_wait_timeout_seconds: int,
        max_script_output_chars: int,
        max_summary_input_chars: int,
        update_sandbox_instance: Callable[[str], Awaitable[None]],
    ) -> None:
        self._runtime = runtime
        self._run_manager = run_manager
        self._poll_interval_seconds = max(1, poll_interval_seconds)
        self._batch_size = max(1, batch_size)
        self._llm_wait_timeout_seconds = max(60, llm_wait_timeout_seconds)
        self._max_script_output_chars = max(1000, max_script_output_chars)
        self._max_summary_input_chars = max(1000, max_summary_input_chars)
        self._update_sandbox_instance = update_sandbox_instance
        self._loop_task: asyncio.Task[None] | None = None
        self._language = get_config_language(runtime.config)

    def _tr(self, key: str, **params: Any) -> str:
        return tr(key, language=self._language, params=params or None)

    def start(self) -> None:
        if self._loop_task is not None and not self._loop_task.done():
            return
        self._loop_task = asyncio.create_task(self._run_loop())

    async def aclose(self) -> None:
        if self._loop_task is None:
            return
        self._loop_task.cancel()
        try:
            await self._loop_task
        except asyncio.CancelledError:
            pass
        self._loop_task = None

    async def _run_loop(self) -> None:
        await self._recover_stale_runs()
        while True:
            try:
                claimed = await self._claim_due_runs()
                for item in claimed:
                    await self._execute_claimed_run(item)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("Scheduled task worker loop error")
            await asyncio.sleep(self._poll_interval_seconds)

    async def _recover_stale_runs(self) -> None:
        async with session_factory()() as db:
            stale_runs = (
                await db.execute(
                    select(ScheduledTaskRun).where(
                        ScheduledTaskRun.status.in_(("queued", "running")),
                        ScheduledTaskRun.finished_at.is_(None),
                    )
                )
            ).scalars().all()
            if not stale_runs:
                return
            now = now_utc()
            for run in stale_runs:
                run.status = "failed"
                run.started_at = run.started_at or now
                run.finished_at = now
                run.error_text = self._tr("scheduled.error.worker_restarted")
            await db.commit()

    async def _claim_due_runs(self) -> list[ClaimedScheduledRun]:
        now = now_utc()
        claims: list[ClaimedScheduledRun] = []
        async with session_factory()() as db:
            due_tasks = (
                await db.execute(
                    select(ScheduledTask)
                    .where(
                        ScheduledTask.task_type.in_(("hybrid_task", "skill_task")),
                        or_(
                            (
                                ScheduledTask.enabled.is_(True)
                                & (ScheduledTask.next_run_at <= now)
                                & (
                                    ScheduledTask.max_runs.is_(None)
                                    | (ScheduledTask.run_count < ScheduledTask.max_runs)
                                )
                            ),
                            (
                                ScheduledTask.run_now_requested_at.is_not(None)
                                & (ScheduledTask.run_now_requested_at <= now)
                            ),
                        ),
                    )
                    .order_by(
                        ScheduledTask.run_now_requested_at.asc().nullslast(),
                        ScheduledTask.next_run_at.asc(),
                    )
                    .limit(self._batch_size)
                    .with_for_update(skip_locked=True)
                )
            ).scalars().all()

            for task in due_tasks:
                manual_trigger = (
                    task.run_now_requested_at is not None and task.run_now_requested_at <= now
                )
                due_by_schedule = task.enabled and task.next_run_at <= now
                scheduled_for = task.run_now_requested_at if manual_trigger else task.next_run_at
                run = ScheduledTaskRun(
                    task_id=task.id,
                    conversation_id=task.conversation_id,
                    user_id=task.user_id,
                    status="queued",
                    scheduled_for=scheduled_for,
                )
                db.add(run)
                await db.flush()

                try:
                    if due_by_schedule:
                        task.next_run_at = compute_next_run_at(
                            schedule_type=task.schedule_type,
                            timezone=task.timezone,
                            cron_expr=task.cron_expr,
                            interval_seconds=task.interval_seconds,
                            from_time=max(now, scheduled_for),
                        )
                    task.run_now_requested_at = None
                    task.last_run_at = now
                    claims.append(
                        ClaimedScheduledRun(
                            run_id=run.id,
                            task_id=task.id,
                            conversation_id=task.conversation_id,
                            user_id=task.user_id,
                            scheduled_for=scheduled_for,
                            manual_trigger=manual_trigger,
                        )
                    )
                except ScheduleValidationError as exc:
                    task.enabled = False
                    task.run_now_requested_at = None
                    task.last_run_at = now
                    run.status = "failed"
                    run.started_at = now
                    run.finished_at = now
                    run.error_text = self._tr(
                        "scheduled.error.invalid_schedule_definition",
                        error=exc,
                    )

            await db.commit()

        return claims

    async def _execute_claimed_run(self, item: ClaimedScheduledRun) -> None:
        await self._update_run(
            run_id=item.run_id,
            status="running",
            started_at=now_utc(),
        )

        task, conversation, user = await self._load_run_context(item)
        if task is None or conversation is None or user is None:
            await self._update_run(
                run_id=item.run_id,
                status="failed",
                finished_at=now_utc(),
                error_text=self._tr("scheduled.error.context_missing"),
            )
            return

        if conversation.deleted_at is not None or conversation.status == "deleted":
            await self._update_run(
                run_id=item.run_id,
                status="failed",
                finished_at=now_utc(),
                error_text=self._tr("scheduled.error.conversation_deleted"),
            )
            return

        is_running = await self._run_manager.is_running(
            conversation_id=item.conversation_id,
            user_id=item.user_id,
        )
        if is_running:
            skipped_message_id = await self._append_system_message(
                conversation_id=item.conversation_id,
                content=(
                    self._tr(
                        "scheduled.message.task_skipped_busy",
                        marker=SCHEDULED_START_MARKER,
                        task_name=task.name,
                    )
                ),
            )
            await self._update_run(
                run_id=item.run_id,
                status="skipped",
                finished_at=now_utc(),
                start_message_id=skipped_message_id,
                result_message_id=skipped_message_id,
                error_text=self._tr("scheduled.error.conversation_busy_running"),
            )
            return

        start_message_id = await self._append_system_message(
            conversation_id=item.conversation_id,
            content=(
                self._tr(
                    "scheduled.message.task_started_manual"
                    if item.manual_trigger
                    else "scheduled.message.task_started",
                    marker=SCHEDULED_START_MARKER,
                    task_name=task.name,
                    scheduled_for=item.scheduled_for.isoformat(),
                )
            ),
        )
        await self._update_run(
            run_id=item.run_id,
            start_message_id=start_message_id,
        )
        incremented = await self._increment_task_run_count(task_id=item.task_id)
        if incremented is None:
            await self._update_run(
                run_id=item.run_id,
                status="failed",
                finished_at=now_utc(),
                result_message_id=start_message_id,
                error_text=self._tr("scheduled.error.task_missing_when_updating_counter"),
            )
            return

        if task.task_type == "skill_task":
            await self._execute_skill_task_run(
                item=item,
                task=task,
                conversation=conversation,
                start_message_id=start_message_id,
            )
            return

        script_started_at = now_utc()
        try:
            script_result = await self._runtime.execute_sandbox_command(
                session_id=item.conversation_id,
                user_id=item.user_id,
                command=task.script_command or "",
                timeout=self._runtime.config.docker_sandbox.timeout,
            )
            script_finished_at = now_utc()
            await self._update_sandbox_instance(item.conversation_id)
        except Exception as exc:  # noqa: BLE001
            script_finished_at = now_utc()
            logger.exception("Scheduled script execution failed")
            failed_message_id = await self._append_system_message(
                conversation_id=item.conversation_id,
                content=(
                    self._tr(
                        "scheduled.message.task_failed_before_summary",
                        marker=SCHEDULED_START_MARKER,
                        task_name=task.name,
                        error=exc,
                    )
                ),
            )
            await self._append_tool_event(
                conversation_id=item.conversation_id,
                message_id=failed_message_id,
                tool_call_id=f"scheduled-script-{item.run_id}",
                tool_name="scheduled_script",
                display_text=f"scheduled_script(command={task.script_command!r})",
                command=task.script_command or "",
                output_text=self._tr("scheduled.error.script_execution_failed", error=exc),
                status="error",
                exit_code=None,
                started_at=script_started_at,
                finished_at=script_finished_at,
            )
            await self._update_run(
                run_id=item.run_id,
                status="failed",
                finished_at=now_utc(),
                result_message_id=failed_message_id,
                error_text=self._tr("scheduled.error.script_execution_failed", error=exc),
            )
            return

        script_output = str(script_result.get("output") or "")
        if len(script_output) > self._max_script_output_chars:
            script_output = script_output[: self._max_script_output_chars] + "\n\n... (truncated)"
        script_exit_code = script_result.get("exit_code")
        script_status = "success"
        if isinstance(script_exit_code, int) and script_exit_code != 0:
            script_status = "error"
        final_run_status = "failed" if script_status == "error" else "success"
        final_run_error_text = (
            self._tr("scheduled.error.script_exited_with_code", exit_code=script_exit_code)
            if final_run_status == "failed" and isinstance(script_exit_code, int)
            else None
        )

        await self._update_run(
            run_id=item.run_id,
            script_exit_code=script_exit_code if isinstance(script_exit_code, int) else None,
            script_output_text=script_output,
        )

        llm_message = self._build_hybrid_summary_request(
            task_name=task.name,
            script_command=task.script_command or "",
            script_exit_code=script_exit_code,
            script_output=script_output,
            summary_prompt=task.summary_prompt,
        )
        await self._execute_summary_llm_run(
            item=item,
            task=task,
            conversation=conversation,
            start_message_id=start_message_id,
            llm_message=llm_message,
            busy_skip_error=self._tr("scheduled.error.summary_rejected_busy"),
            busy_skip_message=self._tr(
                "scheduled.message.summary_skipped_busy",
                marker=SCHEDULED_START_MARKER,
                task_name=task.name,
            ),
            timeout_message=self._tr(
                "scheduled.message.summary_timeout",
                marker=SCHEDULED_START_MARKER,
                task_name=task.name,
            ),
            timeout_error=self._tr("scheduled.error.summary_timeout"),
            empty_summary_message=self._tr(
                "scheduled.message.summary_empty",
                marker=SCHEDULED_START_MARKER,
                task_name=task.name,
            ),
            empty_summary_error=self._tr("scheduled.error.summary_empty"),
            success_status=final_run_status,
            success_error_text=final_run_error_text,
            pseudo_tool_event={
                "tool_call_id": f"scheduled-script-{item.run_id}",
                "tool_name": "scheduled_script",
                "display_text": f"scheduled_script(command={task.script_command!r})",
                "command": task.script_command or "",
                "output_text": script_output,
                "status": script_status,
                "exit_code": script_exit_code if isinstance(script_exit_code, int) else None,
                "started_at": script_started_at,
                "finished_at": script_finished_at,
            },
        )

    async def _execute_skill_task_run(
        self,
        *,
        item: ClaimedScheduledRun,
        task: ScheduledTask,
        conversation: Conversation,
        start_message_id: str | None = None,
    ) -> None:
        if not (task.skill_name or "").strip():
            await self._append_system_message(
                conversation_id=item.conversation_id,
                content=(
                    self._tr(
                        "scheduled.message.skill_missing_name",
                        marker=SCHEDULED_START_MARKER,
                        task_name=task.name,
                    )
                ),
            )
            await self._update_run(
                run_id=item.run_id,
                status="failed",
                finished_at=now_utc(),
                error_text=self._tr("scheduled.error.skill_name_required"),
            )
            return
        llm_message = self._build_skill_task_request(
            task_name=task.name,
            skill_name=task.skill_name or "",
            skill_input=task.skill_input,
            summary_prompt=task.summary_prompt,
        )
        await self._execute_summary_llm_run(
            item=item,
            task=task,
            conversation=conversation,
            start_message_id=start_message_id,
            llm_message=llm_message,
            busy_skip_error=self._tr("scheduled.error.skill_rejected_busy"),
            busy_skip_message=self._tr(
                "scheduled.message.skill_skipped_busy",
                marker=SCHEDULED_START_MARKER,
                task_name=task.name,
            ),
            timeout_message=self._tr(
                "scheduled.message.skill_timeout",
                marker=SCHEDULED_START_MARKER,
                task_name=task.name,
            ),
            timeout_error=self._tr("scheduled.error.skill_timeout"),
            empty_summary_message=self._tr(
                "scheduled.message.skill_empty",
                marker=SCHEDULED_START_MARKER,
                task_name=task.name,
            ),
            empty_summary_error=self._tr("scheduled.error.skill_empty"),
            success_status="success",
            success_error_text=None,
            evaluate_skill_tool_result=True,
        )

    async def _execute_summary_llm_run(
        self,
        *,
        item: ClaimedScheduledRun,
        task: ScheduledTask,
        conversation: Conversation,
        start_message_id: str | None,
        llm_message: str,
        busy_skip_error: str,
        busy_skip_message: str,
        timeout_message: str,
        timeout_error: str,
        empty_summary_message: str,
        empty_summary_error: str,
        success_status: str,
        success_error_text: str | None,
        evaluate_skill_tool_result: bool = False,
        pseudo_tool_event: dict[str, Any] | None = None,
    ) -> None:
        llm_started_at = now_utc()
        request = ChatRequest(
            session_id=item.conversation_id,
            message=llm_message,
            provider=conversation.default_provider,
            model=conversation.default_model,
        )
        started = await self._run_manager.start_run(
            request=request,
            user_id=item.user_id,
            provider=conversation.default_provider,
            model=conversation.default_model,
            ip_address=None,
            user_agent=f"scheduled-task/{task.id}",
        )
        if not started:
            skipped_message_id = await self._append_system_message(
                conversation_id=item.conversation_id,
                content=busy_skip_message,
            )
            await self._update_run(
                run_id=item.run_id,
                status="skipped",
                finished_at=now_utc(),
                start_message_id=start_message_id,
                result_message_id=skipped_message_id,
                error_text=busy_skip_error,
            )
            return
        if pseudo_tool_event is not None:
            await self._publish_pseudo_tool_event(
                conversation_id=item.conversation_id,
                tool_call_id=str(pseudo_tool_event.get("tool_call_id") or f"scheduled-script-{item.run_id}"),
                tool_name=str(pseudo_tool_event.get("tool_name") or "scheduled_script"),
                display_text=str(pseudo_tool_event.get("display_text") or "scheduled_script()"),
                command=str(pseudo_tool_event.get("command") or ""),
                output_text=str(pseudo_tool_event.get("output_text") or ""),
                status=str(pseudo_tool_event.get("status") or "success"),
            )

        completed = await self._wait_for_run_completion(
            conversation_id=item.conversation_id,
            user_id=item.user_id,
            timeout_seconds=self._llm_wait_timeout_seconds,
        )
        if not completed:
            await self._run_manager.cancel_run(conversation_id=item.conversation_id, user_id=item.user_id)
            timeout_message_id = await self._append_system_message(
                conversation_id=item.conversation_id,
                content=timeout_message,
            )
            await self._update_run(
                run_id=item.run_id,
                status="failed",
                finished_at=now_utc(),
                start_message_id=start_message_id,
                result_message_id=timeout_message_id,
                error_text=timeout_error,
            )
            return

        summary_message_id, summary_text = await self._load_latest_assistant_message(
            conversation_id=item.conversation_id,
            created_after=llm_started_at,
        )
        if not summary_text:
            failed_message_id = await self._append_system_message(
                conversation_id=item.conversation_id,
                content=empty_summary_message,
            )
            if pseudo_tool_event is not None:
                await self._append_tool_event(
                    conversation_id=item.conversation_id,
                    message_id=failed_message_id,
                    tool_call_id=str(pseudo_tool_event.get("tool_call_id") or f"scheduled-script-{item.run_id}"),
                    tool_name=str(pseudo_tool_event.get("tool_name") or "scheduled_script"),
                    display_text=str(pseudo_tool_event.get("display_text") or "scheduled_script()"),
                    command=str(pseudo_tool_event.get("command") or ""),
                    output_text=str(pseudo_tool_event.get("output_text") or ""),
                    status=str(pseudo_tool_event.get("status") or "success"),
                    exit_code=pseudo_tool_event.get("exit_code")
                    if isinstance(pseudo_tool_event.get("exit_code"), int)
                    else None,
                    started_at=pseudo_tool_event.get("started_at")
                    if isinstance(pseudo_tool_event.get("started_at"), dt.datetime)
                    else llm_started_at,
                    finished_at=pseudo_tool_event.get("finished_at")
                    if isinstance(pseudo_tool_event.get("finished_at"), dt.datetime)
                    else now_utc(),
                )
            await self._update_run(
                run_id=item.run_id,
                status="failed",
                finished_at=now_utc(),
                start_message_id=start_message_id,
                result_message_id=failed_message_id,
                error_text=empty_summary_error,
            )
            return
        if pseudo_tool_event is not None and summary_message_id:
            await self._append_tool_event(
                conversation_id=item.conversation_id,
                message_id=summary_message_id,
                tool_call_id=str(pseudo_tool_event.get("tool_call_id") or f"scheduled-script-{item.run_id}"),
                tool_name=str(pseudo_tool_event.get("tool_name") or "scheduled_script"),
                display_text=str(pseudo_tool_event.get("display_text") or "scheduled_script()"),
                command=str(pseudo_tool_event.get("command") or ""),
                output_text=str(pseudo_tool_event.get("output_text") or ""),
                status=str(pseudo_tool_event.get("status") or "success"),
                exit_code=pseudo_tool_event.get("exit_code")
                if isinstance(pseudo_tool_event.get("exit_code"), int)
                else None,
                started_at=pseudo_tool_event.get("started_at")
                if isinstance(pseudo_tool_event.get("started_at"), dt.datetime)
                else llm_started_at,
                finished_at=pseudo_tool_event.get("finished_at")
                if isinstance(pseudo_tool_event.get("finished_at"), dt.datetime)
                else now_utc(),
            )
        final_status = success_status
        final_error_text = success_error_text
        final_exit_code: int | None = None
        if evaluate_skill_tool_result and summary_message_id:
            final_status, final_error_text, final_exit_code = await self._evaluate_skill_task_tool_result(
                message_id=summary_message_id,
            )

        await self._update_run(
            run_id=item.run_id,
            status=final_status,
            finished_at=now_utc(),
            start_message_id=start_message_id,
            result_message_id=summary_message_id,
            script_exit_code=final_exit_code if evaluate_skill_tool_result else None,
            summary_text=summary_text,
            error_text=final_error_text,
        )

    async def _evaluate_skill_task_tool_result(
        self,
        *,
        message_id: str,
    ) -> tuple[str, str | None, int | None]:
        async with session_factory()() as db:
            events = (
                await db.execute(
                    select(ToolEvent)
                    .where(ToolEvent.message_id == message_id)
                    .order_by(ToolEvent.started_at.asc(), ToolEvent.id.asc())
                )
            ).scalars().all()

        if not events:
            return "failed", self._tr("scheduled.error.skill_no_tool_result"), None

        last_event = events[-1]
        last_status = str(last_event.status or "").strip().lower()
        last_exit_code = (
            int(last_event.exit_code)
            if isinstance(last_event.exit_code, int)
            else self._extract_exit_code_from_text(last_event.output_text or "")
        )
        if isinstance(last_exit_code, int) and last_exit_code != 0:
            return (
                "failed",
                self._tr("scheduled.error.skill_last_exit_code", exit_code=last_exit_code),
                last_exit_code,
            )
        if last_status != "success":
            return (
                "failed",
                self._tr(
                    "scheduled.error.skill_last_status",
                    status=last_event.status or "unknown",
                ),
                last_exit_code,
            )

        return "success", None, last_exit_code

    @staticmethod
    def _extract_exit_code_from_text(text: str) -> int | None:
        source = str(text or "")
        if not source:
            return None
        failed_match = re.search(r"Command failed with exit code\s+(-?\d+)", source, flags=re.IGNORECASE)
        if failed_match:
            try:
                return int(failed_match.group(1))
            except ValueError:
                return None
        generic_match = re.search(r"exit[_\s-]*code\s*=?\s*(-?\d+)", source, flags=re.IGNORECASE)
        if generic_match:
            try:
                return int(generic_match.group(1))
            except ValueError:
                return None
        return None

    async def _load_run_context(
        self,
        item: ClaimedScheduledRun,
    ) -> tuple[ScheduledTask | None, Conversation | None, User | None]:
        async with session_factory()() as db:
            task = await db.get(ScheduledTask, item.task_id)
            conversation = await db.get(Conversation, item.conversation_id)
            user = await db.get(User, item.user_id)
        return task, conversation, user

    async def _increment_task_run_count(self, *, task_id: str) -> tuple[int, int | None] | None:
        async with session_factory()() as db:
            task = (
                await db.execute(
                    select(ScheduledTask)
                    .where(ScheduledTask.id == task_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if task is None:
                return None
            current = int(task.run_count or 0) + 1
            task.run_count = current
            max_runs = task.max_runs if isinstance(task.max_runs, int) else None
            if max_runs is not None and current >= max_runs:
                task.enabled = False
                task.run_now_requested_at = None
            await db.commit()
            return current, max_runs

    async def _update_run(
        self,
        *,
        run_id: str,
        status: str | None = None,
        started_at: dt.datetime | None = None,
        finished_at: dt.datetime | None = None,
        start_message_id: str | None = None,
        result_message_id: str | None = None,
        script_exit_code: int | None = None,
        script_output_text: str | None = None,
        summary_text: str | None = None,
        error_text: str | None = None,
    ) -> None:
        async with session_factory()() as db:
            run = await db.get(ScheduledTaskRun, run_id)
            if run is None:
                return
            if status is not None:
                run.status = status
            if started_at is not None:
                run.started_at = started_at
            if finished_at is not None:
                run.finished_at = finished_at
            if start_message_id is not None:
                run.start_message_id = start_message_id
            if result_message_id is not None:
                run.result_message_id = result_message_id
            if script_exit_code is not None or script_exit_code == 0:
                run.script_exit_code = script_exit_code
            if script_output_text is not None:
                run.script_output_text = script_output_text
            if summary_text is not None:
                run.summary_text = summary_text
            if error_text is not None:
                run.error_text = error_text
            await db.commit()

    async def _publish_pseudo_tool_event(
        self,
        *,
        conversation_id: str,
        tool_call_id: str,
        tool_name: str,
        display_text: str,
        command: str,
        output_text: str,
        status: str,
    ) -> None:
        publish_fn = getattr(self._run_manager, "publish_tool_event", None)
        if not callable(publish_fn):
            return
        await publish_fn(
            conversation_id=conversation_id,
            tool_call_id=tool_call_id,
            name=tool_name,
            display=display_text,
            command=command,
            output=output_text,
            status=status,
        )

    async def _wait_for_run_completion(
        self,
        *,
        conversation_id: str,
        user_id: str,
        timeout_seconds: int,
    ) -> bool:
        deadline = asyncio.get_running_loop().time() + max(1, timeout_seconds)
        while True:
            running = await self._run_manager.is_running(conversation_id=conversation_id, user_id=user_id)
            if not running:
                return True
            if asyncio.get_running_loop().time() >= deadline:
                return False
            await asyncio.sleep(0.5)

    async def _load_latest_assistant_message(
        self,
        *,
        conversation_id: str,
        created_after: dt.datetime,
    ) -> tuple[str | None, str | None]:
        async with session_factory()() as db:
            row = (
                await db.execute(
                    select(Message)
                    .where(
                        Message.conversation_id == conversation_id,
                        Message.role == "assistant",
                        Message.created_at >= created_after,
                    )
                    .order_by(Message.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if row is None:
                return None, None
            content = row.content or ""
            if not content.startswith(SCHEDULED_RESULT_MARKER):
                row.content = f"{SCHEDULED_RESULT_MARKER}\n{content}".strip()
                await db.commit()
                content = row.content
            return row.id, content

    async def _append_system_message(
        self,
        *,
        conversation_id: str,
        content: str,
    ) -> str:
        async with session_factory()() as db:
            message = Message(
                conversation_id=conversation_id,
                role="system",
                content=content,
            )
            db.add(message)
            await db.flush()
            await db.commit()
            message_id = message.id
        await self._publish_system_message(
            conversation_id=conversation_id,
            content=content,
            message_id=message_id,
        )
        return message_id

    async def _publish_system_message(
        self,
        *,
        conversation_id: str,
        content: str,
        message_id: str | None = None,
    ) -> None:
        publish_fn = getattr(self._run_manager, "publish_system_message", None)
        if not callable(publish_fn):
            return
        await publish_fn(conversation_id=conversation_id, message=content, message_id=message_id)

    async def _append_tool_event(
        self,
        *,
        conversation_id: str,
        message_id: str | None,
        tool_call_id: str | None,
        tool_name: str,
        display_text: str | None,
        command: str | None,
        output_text: str | None,
        status: str,
        exit_code: int | None,
        started_at: dt.datetime,
        finished_at: dt.datetime | None,
    ) -> None:
        async with session_factory()() as db:
            db.add(
                ToolEvent(
                    conversation_id=conversation_id,
                    message_id=message_id,
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    display_text=display_text,
                    command=command,
                    output_text=output_text,
                    status=status,
                    exit_code=exit_code,
                    started_at=started_at,
                    finished_at=finished_at,
                )
            )
            await db.commit()

    def _build_hybrid_summary_request(
        self,
        *,
        task_name: str,
        script_command: str,
        script_exit_code: Any,
        script_output: str,
        summary_prompt: str | None,
    ) -> str:
        prompt = (summary_prompt or "").strip() or self._tr("scheduled.prompt.hybrid_default")
        output_for_summary = script_output
        if len(output_for_summary) > self._max_summary_input_chars:
            output_for_summary = (
                output_for_summary[: self._max_summary_input_chars]
                + "\n\n"
                + self._tr("scheduled.prompt.truncated_for_summary")
            )
        return self._tr(
            "scheduled.prompt.hybrid_request",
            task_name=task_name,
            custom_prompt=prompt,
            script_command=script_command,
            script_exit_code=script_exit_code,
            script_output=(output_for_summary or self._tr("scheduled.prompt.no_output")),
        )

    def _build_skill_task_request(
        self,
        *,
        task_name: str,
        skill_name: str,
        skill_input: str | None,
        summary_prompt: str | None,
    ) -> str:
        prompt = (summary_prompt or "").strip()
        if not prompt:
            prompt = self._tr("scheduled.prompt.skill_default")
        return self._tr(
            "scheduled.prompt.skill_request",
            task_name=task_name,
            skill_name=skill_name,
            skill_input=(skill_input or ""),
            custom_prompt=prompt,
        )
