from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.config_loader import DatabaseConfig
from app.orm_models import Base

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def init_database(config: DatabaseConfig) -> None:
    global _engine, _sessionmaker

    _engine = create_async_engine(
        config.url,
        echo=config.echo,
        pool_pre_ping=True,
    )
    _sessionmaker = async_sessionmaker(
        bind=_engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )


async def create_schema() -> None:
    if _engine is None:
        raise RuntimeError("Database engine is not initialized")
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Lightweight schema evolution for non-Alembic setup.
        await conn.execute(
            text(
                """
                ALTER TABLE conversations
                ADD COLUMN IF NOT EXISTS is_pinned BOOLEAN NOT NULL DEFAULT FALSE
                """
            )
        )
        await conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS ix_conversations_is_pinned
                ON conversations (is_pinned)
                """
            )
        )
        await conn.execute(
            text(
                """
                ALTER TABLE users
                ADD COLUMN IF NOT EXISTS avatar_url VARCHAR(512)
                """
            )
        )
        await conn.execute(
            text(
                """
                ALTER TABLE users
                ADD COLUMN IF NOT EXISTS last_active_at TIMESTAMPTZ
                """
            )
        )
        await conn.execute(
            text(
                """
                ALTER TABLE users
                ADD COLUMN IF NOT EXISTS conversation_limit INTEGER
                """
            )
        )
        await conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS ix_users_last_active_at
                ON users (last_active_at)
                """
            )
        )
        await conn.execute(
            text(
                """
                ALTER TABLE scheduled_tasks
                ADD COLUMN IF NOT EXISTS skill_name VARCHAR(128)
                """
            )
        )
        await conn.execute(
            text(
                """
                ALTER TABLE scheduled_tasks
                ADD COLUMN IF NOT EXISTS skill_input TEXT
                """
            )
        )
        await conn.execute(
            text(
                """
                ALTER TABLE scheduled_tasks
                ADD COLUMN IF NOT EXISTS run_now_requested_at TIMESTAMPTZ
                """
            )
        )
        await conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS ix_scheduled_tasks_run_now_requested_at
                ON scheduled_tasks (run_now_requested_at)
                """
            )
        )
        await conn.execute(
            text(
                """
                ALTER TABLE scheduled_tasks
                ALTER COLUMN script_command DROP NOT NULL
                """
            )
        )
        await conn.execute(
            text(
                """
                ALTER TABLE scheduled_tasks
                ADD COLUMN IF NOT EXISTS max_runs INTEGER
                """
            )
        )
        await conn.execute(
            text(
                """
                ALTER TABLE scheduled_tasks
                ADD COLUMN IF NOT EXISTS run_count INTEGER NOT NULL DEFAULT 0
                """
            )
        )
        await conn.execute(
            text(
                """
                UPDATE scheduled_tasks
                SET run_count = 0
                WHERE run_count IS NULL
                """
            )
        )
        await conn.execute(
            text(
                """
                ALTER TABLE scheduled_tasks
                ALTER COLUMN run_count SET DEFAULT 0
                """
            )
        )
        await conn.execute(
            text(
                """
                ALTER TABLE scheduled_tasks
                ALTER COLUMN run_count SET NOT NULL
                """
            )
        )
        await conn.execute(
            text(
                """
                ALTER TABLE scheduled_task_runs
                ADD COLUMN IF NOT EXISTS start_message_id VARCHAR(36)
                """
            )
        )
        await conn.execute(
            text(
                """
                ALTER TABLE scheduled_task_runs
                ADD COLUMN IF NOT EXISTS result_message_id VARCHAR(36)
                """
            )
        )


async def close_database() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


async def get_db() -> AsyncIterator[AsyncSession]:
    if _sessionmaker is None:
        raise RuntimeError("Database sessionmaker is not initialized")
    async with _sessionmaker() as session:
        yield session


def session_factory() -> async_sessionmaker[AsyncSession]:
    if _sessionmaker is None:
        raise RuntimeError("Database sessionmaker is not initialized")
    return _sessionmaker
