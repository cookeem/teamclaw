from __future__ import annotations

import importlib
import inspect
import os
from dataclasses import dataclass
from typing import Any

from app.config_loader import TeamClawConfig


@dataclass
class RuntimeCheckpointer:
    saver: Any
    backend: str
    _context_manager: Any

    async def aclose(self) -> None:
        if self._context_manager is None:
            return
        await self._context_manager.__aexit__(None, None, None)


def _resolve_checkpoint_url(config: TeamClawConfig) -> str:
    raw = os.environ.get("TEAMCLAW_CHECKPOINTER_URL")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return config.database.checkpoint_url


async def create_postgres_checkpointer(config: TeamClawConfig) -> RuntimeCheckpointer:
    conn_string = _resolve_checkpoint_url(config)
    try:
        module = importlib.import_module("langgraph.checkpoint.postgres.aio")
    except ModuleNotFoundError as exc:
        msg = (
            "Postgres checkpointer dependency is missing. "
            "Please install 'langgraph-checkpoint-postgres' and restart backend."
        )
        raise RuntimeError(msg) from exc

    saver_cls = getattr(module, "AsyncPostgresSaver", None)
    if saver_cls is None:
        msg = "AsyncPostgresSaver is unavailable in langgraph.checkpoint.postgres.aio"
        raise RuntimeError(msg)

    context_manager = saver_cls.from_conn_string(conn_string)
    saver = await context_manager.__aenter__()

    setup = getattr(saver, "setup", None)
    if callable(setup):
        result = setup()
        if inspect.isawaitable(result):
            await result

    return RuntimeCheckpointer(
        saver=saver,
        backend="postgres",
        _context_manager=context_manager,
    )
