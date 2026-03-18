from __future__ import annotations

from pathlib import Path
import logging
import os
import random

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from backend.api.router import api_router
from backend.core.config import get_settings
from backend.core.database import Base, engine
from backend.core import models  # noqa: F401
from backend.ws.chat import router as ws_router
from backend.services.deepagents_service import deepagent_service
from backend.services.stream_events import stream_event_publisher

settings = get_settings()
logger = logging.getLogger(__name__)

app = FastAPI(
    title=settings.app.name,
    version="0.1.0",
    debug=settings.app.debug,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)
app.include_router(ws_router)
uploads_dir = Path("uploads")
uploads_dir.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(uploads_dir)), name="uploads")


@app.on_event("startup")
def on_startup() -> None:
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE conversation_messages ADD COLUMN IF NOT EXISTS tool_name VARCHAR(128)"))
        conn.execute(text("ALTER TABLE conversation_messages ADD COLUMN IF NOT EXISTS attachments_json JSONB"))
        conn.execute(text("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS daemon_host VARCHAR(256)"))
        conn.execute(text("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS pending_interrupt_id VARCHAR(64)"))
        conn.execute(text("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS is_pinned BOOLEAN DEFAULT FALSE"))
        conn.execute(text("ALTER TABLE conversations ADD COLUMN IF NOT EXISTS pinned_at TIMESTAMPTZ NULL"))
        conn.execute(text("UPDATE conversations SET is_pinned = FALSE WHERE is_pinned IS NULL"))

        conn.execute(text("ALTER TABLE IF EXISTS skills ADD COLUMN IF NOT EXISTS source_type VARCHAR(16)"))
        conn.execute(text("ALTER TABLE IF EXISTS skills ADD COLUMN IF NOT EXISTS status VARCHAR(32)"))
        conn.execute(text("ALTER TABLE IF EXISTS skills ADD COLUMN IF NOT EXISTS display_name VARCHAR(128)"))
        conn.execute(text("ALTER TABLE IF EXISTS skills ADD COLUMN IF NOT EXISTS description TEXT"))
        conn.execute(text("ALTER TABLE IF EXISTS skills ADD COLUMN IF NOT EXISTS is_public BOOLEAN DEFAULT FALSE"))
        conn.execute(text("ALTER TABLE IF EXISTS skills ADD COLUMN IF NOT EXISTS is_public_edit BOOLEAN DEFAULT FALSE"))
        conn.execute(text("ALTER TABLE IF EXISTS skills ADD COLUMN IF NOT EXISTS usage_count INTEGER DEFAULT 0"))
        conn.execute(text("ALTER TABLE IF EXISTS skills ADD COLUMN IF NOT EXISTS cloned_from_skill_id VARCHAR(36)"))
        conn.execute(text("ALTER TABLE IF EXISTS skills ADD COLUMN IF NOT EXISTS pending_comment TEXT"))
        conn.execute(text("ALTER TABLE IF EXISTS skills ADD COLUMN IF NOT EXISTS published_at TIMESTAMPTZ NULL"))
        conn.execute(text("ALTER TABLE IF EXISTS skills ADD COLUMN IF NOT EXISTS published_by VARCHAR(36)"))
        conn.execute(text("ALTER TABLE IF EXISTS skills ADD COLUMN IF NOT EXISTS rejected_at TIMESTAMPTZ NULL"))
        conn.execute(text("ALTER TABLE IF EXISTS skills ADD COLUMN IF NOT EXISTS rejected_by VARCHAR(36)"))
        conn.execute(text("ALTER TABLE IF EXISTS skills ADD COLUMN IF NOT EXISTS rejected_reason TEXT"))
        conn.execute(text("UPDATE skills SET usage_count = 0 WHERE usage_count IS NULL"))
        conn.execute(
            text(
                "UPDATE skills SET usage_count = ("
                "SELECT COUNT(1) FROM conversation_messages "
                "WHERE conversation_messages.message_type = 'ToolMessage' "
                "AND conversation_messages.tool_name = skills.name)"
            )
        )

        docker_cfg = (settings.model_extra or {}).get("docker", {}) or {}
        hosts_cfg = docker_cfg.get("daemon_hosts")
        if isinstance(hosts_cfg, list):
            daemon_hosts = [str(h.get("host")) for h in hosts_cfg if isinstance(h, dict) and h.get("host")]
        else:
            single_host = docker_cfg.get("daemon_host")
            daemon_hosts = [str(single_host)] if single_host else []

        if daemon_hosts:
            rows = conn.execute(text("SELECT id FROM conversations WHERE daemon_host IS NULL")).fetchall()
            for row in rows:
                conn.execute(
                    text("UPDATE conversations SET daemon_host = :host WHERE id = :id"),
                    {"host": random.choice(daemon_hosts), "id": row[0]},
                )
    try:
        removed = deepagent_service.cleanup_orphan_containers()
        if removed:
            logger.info("Removed stale teamclaw containers: %s", removed)
    except Exception as exc:  # noqa: BLE001
        logger.warning("cleanup_orphan_containers failed: %s", exc)


@app.get("/")
def root() -> dict:
    return {
        "service": settings.app.name,
        "status": "ok",
        "docs": "/docs",
        "health": "/api/v1/health",
    }


@app.on_event("shutdown")
def on_shutdown() -> None:
    stream_event_publisher.close_all()
    deepagent_service.cleanup_all()
    try:
        removed = deepagent_service.cleanup_orphan_containers()
        if removed:
            logger.info("Removed stale teamclaw containers on shutdown: %s", removed)
    except Exception as exc:  # noqa: BLE001
        logger.warning("cleanup_orphan_containers on shutdown failed: %s", exc)


def run() -> None:
    import uvicorn

    reload_excludes: list[str] = []
    exclude_paths: list[Path] = []
    try:
        storage = settings.skill_storage
        reload_excludes.extend(
            [
                storage.userskills_dir,
                storage.preskills_dir,
                storage.skills_dir,
                storage.agentskills_dir,
                storage.conversationskills_dir,
            ]
        )
    except Exception:
        pass
    docker_cfg = (settings.model_extra or {}).get("docker", {}) or {}
    workspace_root = docker_cfg.get("workspace_root")
    if workspace_root:
        reload_excludes.append(str(workspace_root))

    resolved_excludes: list[str] = []
    cwd = Path.cwd()
    for item in reload_excludes:
        if not item:
            continue
        path = Path(str(item)).expanduser()
        if not path.is_absolute():
            path = (cwd / path).resolve()
        exclude_paths.append(path)
        try:
            rel = path.relative_to(cwd)
            rel_str = str(rel)
        except ValueError:
            # Keep relative pattern even if outside cwd (e.g., ../skills/**).
            rel_str = os.path.relpath(path, cwd)
        resolved_excludes.append(rel_str)
        resolved_excludes.append(str(Path(rel_str) / "**"))
    reload_excludes = resolved_excludes

    for path in exclude_paths:
        try:
            path.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

    reload_dirs = [str(Path(__file__).resolve().parent)]
    logger.info("Uvicorn reload_dirs=%s reload_excludes=%s", reload_dirs, reload_excludes)

    uvicorn.run(
        "backend.main:app",
        host=settings.app.host,
        port=settings.app.port,
        reload=bool(settings.app.debug),
        reload_dirs=reload_dirs,
        reload_excludes=reload_excludes or None,
        timeout_graceful_shutdown=5,
    )


if __name__ == "__main__":
    run()
