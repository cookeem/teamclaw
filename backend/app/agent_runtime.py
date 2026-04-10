from __future__ import annotations

import asyncio
import ast
import re
import base64
import datetime as dt
import hashlib
import inspect
import json
import logging
import mimetypes
import os
import re
import shlex
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy import select

from app.bootstrap import bootstrap_deepagents_paths
from app.config_loader import RuntimeModelConfig, TeamClawConfig
from app.crud import conversation_workspace_path, now_utc
from app.db import session_factory
from app.i18n import get_config_language, tr
from app.orm_models import ScheduledTask, ScheduledTaskRun
from app.schemas import ChatRequest
from app.scheduling import ScheduleValidationError, compute_next_run_at, normalize_schedule

# Validate pip-installed DeepAgents dependencies at import time.
REPO_ROOT = bootstrap_deepagents_paths()

from app.docker_sandbox import DockerSandboxManager
from deepagents_cli.agent import create_cli_agent, get_system_prompt
from deepagents_cli.config import create_model, settings

logger = logging.getLogger(__name__)

IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".tif"})
MAX_MEDIA_ATTACHMENT_BYTES = 20 * 1024 * 1024
MAX_TEXT_ATTACHMENT_EMBED_BYTES = 256 * 1024
SANDBOX_ENV_MANIFEST_FILE = ".teamclaw/sandbox-environment.json"


@dataclass
class AgentBundle:
    cache_key: str
    graph: Any


class WebAgentRuntime:
    def __init__(
        self,
        config: TeamClawConfig,
        repo_root: Path,
        *,
        checkpointer: Any | None = None,
    ) -> None:
        self.config = config
        self.repo_root = repo_root
        self._checkpointer = checkpointer if checkpointer is not None else MemorySaver()
        self._session_agent_cache: dict[str, AgentBundle] = {}
        self._build_lock = asyncio.Lock()
        self._runtime_loop: asyncio.AbstractEventLoop | None = None
        self._language = get_config_language(config)
        docker_cfg = self.config.docker_sandbox
        self._docker_sandbox_manager = DockerSandboxManager(docker_cfg)
        logger.info(
            "Docker sandbox runtime initialized (image=%s, workspace_root=%s, workdir=%s)",
            docker_cfg.image,
            docker_cfg.workspace_root,
            docker_cfg.workdir,
        )

    def list_models(self) -> dict[str, Any]:
        return self.config.list_models()

    def _tr(self, key: str, **params: Any) -> str:
        return tr(key, language=self._language, params=params or None)

    async def aclose(self) -> None:
        await asyncio.to_thread(self._docker_sandbox_manager.close_all)

    async def close_session(self, session_id: str) -> None:
        self._session_agent_cache.pop(session_id, None)
        await asyncio.to_thread(self._docker_sandbox_manager.close_session, session_id)

    async def get_sandbox_info(self, session_id: str) -> dict[str, str] | None:
        return await asyncio.to_thread(self._docker_sandbox_manager.get_session_info, session_id)

    async def execute_sandbox_command(
        self,
        *,
        session_id: str,
        user_id: str,
        command: str,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        sandbox = await asyncio.to_thread(
            self._docker_sandbox_manager.get_or_create,
            session_id,
            user_id,
        )
        result = await asyncio.to_thread(sandbox.execute, command, timeout=timeout)
        info = await asyncio.to_thread(self._docker_sandbox_manager.get_session_info, session_id)
        return {
            "output": str(getattr(result, "output", "")),
            "exit_code": getattr(result, "exit_code", None),
            "truncated": bool(getattr(result, "truncated", False)),
            "sandbox_info": info or {},
        }

    async def stream_chat(
        self,
        request: ChatRequest,
        *,
        user_id: str,
    ) -> AsyncIterator[dict[str, Any]]:
        loop = asyncio.get_running_loop()
        if self._runtime_loop is None or self._runtime_loop.is_closed():
            self._runtime_loop = loop

        runtime_model = self.config.resolve_runtime_model(
            provider=request.provider,
            model=request.model,
        )
        await self._ensure_sandbox_environment_manifest_for_session(
            session_id=request.session_id,
            user_id=user_id,
        )
        bundle = await self._get_or_build_agent(
            runtime_model,
            request.session_id,
            user_id=user_id,
        )
        await self._sync_request_attachments_to_sandbox(request)

        yield {
            "type": "status",
            "status": "running",
            "provider": runtime_model.provider,
            "model": runtime_model.model,
        }

        message_content = self._build_message_content(request)
        graph_input = {
            "messages": [
                {
                    "role": "user",
                    "content": message_content,
                }
            ]
        }
        graph_config = {"configurable": {"thread_id": request.session_id}}

        tool_states: dict[str, dict[str, Any]] = {}
        debug_fp = None
        emitted_model_events = False
        emitted_assistant_text = False
        if self.config.llm_message_debug:
            debug_path = self._build_debug_log_path(request.session_id)
            debug_fp = debug_path.open("a", encoding="utf-8")
            self._write_debug_log(
                debug_fp,
                {
                    "event": "session_start",
                    "session_id": request.session_id,
                    "provider": runtime_model.provider,
                    "model": runtime_model.model,
                    "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
                },
            )

        try:
            attempt = 0
            while attempt < 2:
                try:
                    async for chunk in bundle.graph.astream(
                        graph_input,
                        stream_mode=["messages", "updates"],
                        subgraphs=True,
                        config=graph_config,
                        durability="exit",
                    ):
                        if not isinstance(chunk, tuple) or len(chunk) != 3:  # noqa: PLR2004
                            continue

                        namespace, stream_mode, data = chunk
                        if stream_mode == "messages":
                            if debug_fp is not None:
                                self._write_debug_log(
                                    debug_fp,
                                    self._build_message_debug_payload(
                                        session_id=request.session_id,
                                        namespace=namespace,
                                        data=data,
                                    ),
                                )

                            text_blocks, tool_calls, tool_results = self._parse_message_chunk(
                                data,
                                tool_states,
                            )
                            # Keep assistant text at root namespace only to avoid duplicated
                            # streamed prose, but ingest tool activity from all namespaces so
                            # we don't lose tool arguments emitted inside subgraphs.
                            if not namespace:
                                for text in text_blocks:
                                    if text:
                                        emitted_model_events = True
                                        emitted_assistant_text = True
                                        yield {"type": "text", "delta": text}
                            for tool_call in tool_calls:
                                emitted_model_events = True
                                yield {"type": "tool_call", **tool_call}
                            for tool_result in tool_results:
                                emitted_model_events = True
                                yield {"type": "tool_result", **tool_result}
                            continue

                        if (
                            stream_mode == "updates"
                            and isinstance(data, dict)
                            and data.get("__interrupt__")
                        ):
                            yield {
                                "type": "warning",
                                "message": "Interrupt received. Web mode runs with auto-approve enabled.",
                            }
                    break
                except ValueError as exc:
                    is_empty_stream = self._is_no_generations_stream_error(exc)
                    if attempt == 0 and is_empty_stream and not emitted_assistant_text:
                        logger.warning(
                            "Model returned empty generation stream; attempting session recovery and one retry (session=%s, model_events=%s, assistant_text=%s)",
                            request.session_id,
                            emitted_model_events,
                            emitted_assistant_text,
                        )
                        yield {
                            "type": "warning",
                            "message": (
                                "Model returned an empty stream (likely upstream model/gateway instability). "
                                "Session state is being recovered automatically; retrying once."
                            ),
                        }
                        await self._recover_session_after_empty_stream(request.session_id)
                        bundle = await self._get_or_build_agent(
                            runtime_model,
                            request.session_id,
                            user_id=user_id,
                        )
                        tool_states = {}
                        emitted_model_events = False
                        emitted_assistant_text = False
                        attempt += 1
                        continue
                    raise
        finally:
            if debug_fp is not None:
                self._write_debug_log(
                    debug_fp,
                    {
                        "event": "session_end",
                        "session_id": request.session_id,
                        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
                    },
                )
                debug_fp.close()

        yield {"type": "done"}

    @staticmethod
    def _is_no_generations_stream_error(exc: Exception) -> bool:
        if not isinstance(exc, ValueError):
            return False
        text = str(exc).strip().lower()
        return "no generations found in stream" in text

    async def _recover_session_after_empty_stream(self, session_id: str) -> None:
        self._session_agent_cache.pop(session_id, None)
        deleted = await self._delete_checkpoint_thread_best_effort(session_id)
        if deleted:
            logger.info("Recovered session=%s by clearing checkpoint thread and agent cache", session_id)
        else:
            logger.info(
                "Recovered session=%s by clearing agent cache; checkpointer thread deletion unsupported",
                session_id,
            )

    async def _delete_checkpoint_thread_best_effort(self, session_id: str) -> bool:
        checkpointer = self._checkpointer
        for method_name in ("adelete_thread", "delete_thread"):
            method = getattr(checkpointer, method_name, None)
            if not callable(method):
                continue
            try:
                result = method(session_id)
                if inspect.isawaitable(result):
                    await result
                return True
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Failed calling checkpointer.%s for session=%s: %s",
                    method_name,
                    session_id,
                    exc,
                )
        return False

    async def _ensure_sandbox_environment_manifest_for_session(
        self,
        *,
        session_id: str,
        user_id: str,
    ) -> None:
        sandbox = await asyncio.to_thread(
            self._docker_sandbox_manager.get_or_create,
            session_id,
            user_id,
        )
        await asyncio.to_thread(self._ensure_sandbox_environment_manifest, sandbox)

    def _sandbox_environment_manifest_path(self) -> str:
        workdir = self.config.docker_sandbox.workdir.rstrip("/") or "/workspace"
        return f"{workdir}/{SANDBOX_ENV_MANIFEST_FILE}"

    def _ensure_sandbox_environment_manifest(self, sandbox: Any) -> None:
        manifest_path = self._sandbox_environment_manifest_path()
        workdir = self.config.docker_sandbox.workdir.rstrip("/") or "/workspace"
        command = self._build_sandbox_environment_manifest_command(
            manifest_path=manifest_path,
            workdir=workdir,
        )
        result = sandbox.execute(command, timeout=60)
        if result.exit_code not in {0, None}:
            logger.warning(
                "Failed creating sandbox environment manifest path=%s exit=%s output=%s",
                manifest_path,
                result.exit_code,
                (result.output or "")[:600],
            )

    @staticmethod
    def _build_sandbox_environment_manifest_command(*, manifest_path: str, workdir: str) -> str:
        manifest_q = shlex.quote(manifest_path)
        workdir_q = shlex.quote(workdir)
        return (
            "set -eu\n"
            f"MANIFEST_PATH={manifest_q}\n"
            f"WORKDIR_PATH={workdir_q}\n"
            "if [ -s \"$MANIFEST_PATH\" ]; then\n"
            "  exit 0\n"
            "fi\n"
            "mkdir -p \"$(dirname \"$MANIFEST_PATH\")\"\n"
            "MANIFEST_PATH=\"$MANIFEST_PATH\" WORKDIR_PATH=\"$WORKDIR_PATH\" python3 - <<'PY'\n"
            "import datetime as dt\n"
            "import importlib.metadata as md\n"
            "import json\n"
            "import os\n"
            "import platform\n"
            "import shutil\n"
            "import subprocess\n"
            "from pathlib import Path\n"
            "\n"
            "manifest_path = os.environ.get('MANIFEST_PATH', '/workspace/.teamclaw/sandbox-environment.json')\n"
            "workdir = os.environ.get('WORKDIR_PATH', '/workspace')\n"
            "\n"
            "def run_cmd(parts, timeout=2.0):\n"
            "    try:\n"
            "        proc = subprocess.run(parts, capture_output=True, text=True, timeout=timeout, check=False)\n"
            "    except Exception:\n"
            "        return None\n"
            "    text = (proc.stdout or proc.stderr or '').strip()\n"
            "    if not text:\n"
            "        return None\n"
            "    return text.splitlines()[0][:240]\n"
            "\n"
            "def discover_path_commands():\n"
            "    command_map = {}\n"
            "    for raw_dir in os.environ.get('PATH', '').split(':'):\n"
            "        path_dir = raw_dir.strip() or '.'\n"
            "        if not os.path.isdir(path_dir):\n"
            "            continue\n"
            "        try:\n"
            "            with os.scandir(path_dir) as it:\n"
            "                for entry in it:\n"
            "                    name = entry.name.strip()\n"
            "                    if not name or name in command_map:\n"
            "                        continue\n"
            "                    try:\n"
            "                        is_file = entry.is_file(follow_symlinks=True)\n"
            "                    except OSError:\n"
            "                        continue\n"
            "                    if not is_file:\n"
            "                        continue\n"
            "                    if not os.access(entry.path, os.X_OK):\n"
            "                        continue\n"
            "                    command_map[name] = entry.path\n"
            "        except OSError:\n"
            "            continue\n"
            "    return dict(sorted(command_map.items(), key=lambda item: item[0].lower()))\n"
            "\n"
            "def command_version(path):\n"
            "    probes = (\n"
            "        [path, '--version'],\n"
            "        [path, '-V'],\n"
            "        [path, 'version'],\n"
            "        [path, '-v'],\n"
            "    )\n"
            "    for probe in probes:\n"
            "        version = run_cmd(probe, timeout=0.8)\n"
            "        if version:\n"
            "            return version\n"
            "    return None\n"
            "\n"
            "path_commands = discover_path_commands()\n"
            "version_probe_limit = 120\n"
            "version_probe_count = 0\n"
            "command_inventory = {}\n"
            "for name, path in path_commands.items():\n"
            "    info = {'installed': True, 'path': path}\n"
            "    if version_probe_count < version_probe_limit:\n"
            "        info['version'] = command_version(path)\n"
            "        version_probe_count += 1\n"
            "    else:\n"
            "        info['version'] = None\n"
            "        info['version_probe_skipped'] = True\n"
            "    command_inventory[name] = info\n"
            "\n"
            "pip_inventory = {}\n"
            "try:\n"
            "    for dist in md.distributions():\n"
            "        name = (dist.metadata.get('Name') or dist.metadata.get('Summary') or dist.name or '').strip()\n"
            "        if not name:\n"
            "            continue\n"
            "        pip_inventory[name] = dist.version\n"
            "except Exception:\n"
            "    pip_inventory = {}\n"
            "\n"
            "font_families = []\n"
            "if shutil.which('fc-list'):\n"
            "    try:\n"
            "        proc = subprocess.run(['fc-list', ':', 'family'], capture_output=True, text=True, timeout=8, check=False)\n"
            "        for line in (proc.stdout or '').splitlines():\n"
            "            for family in [x.strip() for x in line.split(',') if x.strip()]:\n"
            "                if family not in font_families:\n"
            "                    font_families.append(family)\n"
            "        font_families = sorted(font_families)[:300]\n"
            "    except Exception:\n"
            "        font_families = []\n"
            "\n"
            "selected_apt_packages = {}\n"
            "if shutil.which('dpkg-query'):\n"
            "    try:\n"
            "        proc = subprocess.run(\n"
            "            ['dpkg-query', '-W', '-f=${Package}\\t${Version}\\n'],\n"
            "            capture_output=True,\n"
            "            text=True,\n"
            "            timeout=15,\n"
            "            check=False,\n"
            "        )\n"
            "    except Exception:\n"
            "        proc = None\n"
            "    if proc is not None and proc.returncode == 0:\n"
            "        for line in (proc.stdout or '').splitlines():\n"
            "            if not line or '\\t' not in line:\n"
            "                continue\n"
            "            pkg, version = line.split('\\t', 1)\n"
            "            pkg = pkg.strip()\n"
            "            if not pkg:\n"
            "                continue\n"
            "            selected_apt_packages[pkg] = version.strip()\n"
            "\n"
            "os_release = {}\n"
            "release_file = Path('/etc/os-release')\n"
            "if release_file.is_file():\n"
            "    for line in release_file.read_text(encoding='utf-8', errors='ignore').splitlines():\n"
            "        if '=' not in line:\n"
            "            continue\n"
            "        key, value = line.split('=', 1)\n"
            "        os_release[key] = value.strip().strip('\"')\n"
            "\n"
            "manifest = {\n"
            "    'schema_version': 1,\n"
            "    'generated_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),\n"
            "    'workdir': workdir,\n"
            "    'os': {\n"
            "        'platform': platform.platform(),\n"
            "        'release': os_release,\n"
            "    },\n"
            "    'python': {\n"
            "        'version': platform.python_version(),\n"
            "        'executable': shutil.which('python3') or '',\n"
            "        'package_count': len(pip_inventory),\n"
            "        'packages': dict(sorted(pip_inventory.items(), key=lambda item: item[0].lower())),\n"
            "    },\n"
            "    'software': {\n"
            "        'command_count': len(command_inventory),\n"
            "        'version_probe_limit': version_probe_limit,\n"
            "        'version_probe_count': version_probe_count,\n"
            "        'commands': command_inventory,\n"
            "    },\n"
            "    'apt_packages': selected_apt_packages,\n"
            "    'fonts': {\n"
            "        'family_count': len(font_families),\n"
            "        'families_sample': font_families,\n"
            "    },\n"
            "}\n"
            "\n"
            "Path(manifest_path).write_text(\n"
            "    json.dumps(manifest, ensure_ascii=False, indent=2),\n"
            "    encoding='utf-8',\n"
            ")\n"
            "PY\n"
        )

    async def _sync_request_attachments_to_sandbox(self, request: ChatRequest) -> None:
        if not request.attachments:
            return

        sandbox = await asyncio.to_thread(
            self._docker_sandbox_manager.get_session_sandbox,
            request.session_id,
        )
        if sandbox is None:
            return

        workspace_dir = conversation_workspace_path(request.session_id).resolve()
        workdir = self.config.docker_sandbox.workdir.rstrip("/") or "/workspace"
        files_to_upload: list[tuple[str, bytes]] = []

        for attachment in request.attachments:
            resolved_path = self._resolve_attachment_path(workspace_dir, attachment.path)
            if resolved_path is None:
                continue
            normalized_rel = str(Path(attachment.path or "").as_posix()).lstrip("/")
            if not normalized_rel:
                continue
            try:
                content = resolved_path.read_bytes()
            except OSError:
                continue
            if not content:
                continue
            files_to_upload.append((f"{workdir}/{normalized_rel}", content))

        if not files_to_upload:
            return

        upload_results = await asyncio.to_thread(sandbox.upload_files, files_to_upload)
        for result in upload_results:
            if getattr(result, "error", None):
                logger.warning(
                    "Failed syncing attachment into sandbox session=%s path=%s error=%s",
                    request.session_id,
                    getattr(result, "path", ""),
                    getattr(result, "error", ""),
                )

    def _build_message_content(self, request: ChatRequest) -> str | list[dict[str, Any]]:
        if not request.attachments:
            return request.message

        workspace_dir = conversation_workspace_path(request.session_id).resolve()
        message_text = request.message.strip()
        text_sections: list[str] = [message_text] if message_text else []
        image_blocks: list[dict[str, Any]] = []
        file_sections: list[str] = []

        for attachment in request.attachments:
            resolved_path = self._resolve_attachment_path(workspace_dir, attachment.path)
            display_name = attachment.name or Path(attachment.path).name
            sandbox_path = self._to_sandbox_attachment_path(attachment.path)
            if resolved_path is None:
                file_sections.append(
                    f"### {display_name}\nPath: `{sandbox_path}`\nFile not found in workspace uploads."
                )
                continue

            if self._is_image_attachment(resolved_path, attachment.mime_type):
                image_block = self._build_image_block(resolved_path, attachment.mime_type)
                if image_block is not None:
                    image_blocks.append(image_block)
                    continue

            file_sections.append(
                self._build_file_attachment_section(
                    file_path=resolved_path,
                    display_name=display_name,
                    sandbox_path=sandbox_path,
                )
            )

        if file_sections:
            text_sections.append("## Uploaded Files")
            text_sections.extend(file_sections)

        final_text = "\n\n".join(section for section in text_sections if section).strip()
        if not image_blocks:
            return final_text or request.message

        blocks: list[dict[str, Any]] = []
        if final_text:
            blocks.append({"type": "text", "text": final_text})
        blocks.extend(image_blocks)
        return blocks

    @staticmethod
    def _resolve_attachment_path(workspace_dir: Path, relative_path: str) -> Path | None:
        normalized_rel = str(relative_path or "").strip().lstrip("/")
        if not normalized_rel:
            return None
        rel_obj = Path(normalized_rel)
        if not rel_obj.parts or rel_obj.parts[0] != "uploads":
            return None

        uploads_dir = (workspace_dir / "uploads").resolve()
        candidate = (workspace_dir / rel_obj).resolve()
        if uploads_dir not in candidate.parents:
            return None
        if not candidate.exists() or not candidate.is_file():
            return None
        return candidate

    def _to_sandbox_attachment_path(self, relative_path: str) -> str:
        normalized_rel = str(relative_path or "").strip().lstrip("/")
        workdir = self.config.docker_sandbox.workdir.rstrip("/") or "/workspace"
        return f"{workdir}/{normalized_rel}" if normalized_rel else workdir

    @staticmethod
    def _is_image_attachment(file_path: Path, mime_type: str | None) -> bool:
        if isinstance(mime_type, str) and mime_type.strip().lower().startswith("image/"):
            return True
        return file_path.suffix.lower() in IMAGE_SUFFIXES

    def _build_image_block(self, file_path: Path, mime_type: str | None) -> dict[str, Any] | None:
        try:
            raw = file_path.read_bytes()
        except OSError:
            return None
        if not raw or len(raw) > MAX_MEDIA_ATTACHMENT_BYTES:
            return None

        normalized_mime = mime_type.strip().lower() if isinstance(mime_type, str) and mime_type.strip() else None
        if not normalized_mime or not normalized_mime.startswith("image/"):
            guessed, _ = mimetypes.guess_type(file_path.name)
            normalized_mime = guessed if isinstance(guessed, str) and guessed.startswith("image/") else "image/png"

        encoded = base64.b64encode(raw).decode("ascii")
        return {"type": "image_url", "image_url": {"url": f"data:{normalized_mime};base64,{encoded}"}}

    @staticmethod
    def _build_file_attachment_section(
        *,
        file_path: Path,
        display_name: str,
        sandbox_path: str,
    ) -> str:
        try:
            file_size = file_path.stat().st_size
        except OSError:
            return f"### {display_name}\nPath: `{sandbox_path}`\nUnable to read file metadata."

        if file_size > MAX_TEXT_ATTACHMENT_EMBED_BYTES:
            size_kb = file_size // 1024
            return (
                f"### {display_name}\n"
                f"Path: `{sandbox_path}`\n"
                f"Size: {size_kb}KB (too large to embed, use read_file tool to inspect)"
            )

        try:
            text = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return (
                f"### {display_name}\n"
                f"Path: `{sandbox_path}`\n"
                "Binary or non-UTF8 file (use read_file tool to inspect)"
            )
        except OSError:
            return f"### {display_name}\nPath: `{sandbox_path}`\nUnable to read file content."

        return f"### {display_name}\nPath: `{sandbox_path}`\n```\n{text}\n```"

    async def _get_or_build_agent(
        self,
        runtime_model: RuntimeModelConfig,
        session_id: str,
        *,
        user_id: str,
    ) -> AgentBundle:
        prompt_fingerprint = self._prompt_fingerprint()
        cache_key = f"{runtime_model.provider}:{runtime_model.model}:prompt:{prompt_fingerprint}"
        cached = self._session_agent_cache.get(session_id)
        if cached is not None and cached.cache_key == cache_key:
            return cached

        async with self._build_lock:
            cached = self._session_agent_cache.get(session_id)
            if cached is not None and cached.cache_key == cache_key:
                return cached

            bundle = await self._build_agent_bundle(
                runtime_model,
                cache_key,
                session_id,
                user_id=user_id,
            )
            self._session_agent_cache[session_id] = bundle
            logger.info("Built docker-sandbox web agent for session=%s model=%s", session_id, cache_key)

            return bundle

    async def _build_agent_bundle(
        self,
        runtime_model: RuntimeModelConfig,
        cache_key: str,
        session_id: str,
        *,
        user_id: str,
    ) -> AgentBundle:
        self._sync_environment(runtime_model)

        # Refresh deepagents settings after environment updates.
        settings.reload_from_environment(start_path=self.repo_root)

        model_spec = f"{runtime_model.provider}:{runtime_model.model}"
        extra_kwargs = {**runtime_model.provider_kwargs, **runtime_model.model_kwargs}
        model_result = create_model(model_spec, extra_kwargs=extra_kwargs)
        model_result.apply_to_settings()

        docker_cfg = self.config.docker_sandbox
        sandbox = await asyncio.to_thread(
            self._docker_sandbox_manager.get_or_create,
            session_id,
            user_id,
        )
        await asyncio.to_thread(self._ensure_sandbox_environment_manifest, sandbox)
        base_system_prompt = self._load_system_prompt(
            assistant_id="web-agent",
            interactive=False,
            cwd=Path(docker_cfg.workdir),
        )
        if not base_system_prompt:
            base_system_prompt = get_system_prompt(
                assistant_id="web-agent",
                interactive=False,
                cwd=Path(docker_cfg.workdir),
            )
        base_system_prompt = self._strip_default_user_skills_hint(base_system_prompt)
        system_prompt = self._append_docker_skills_prompt(base_system_prompt, user_id=user_id)

        # Important: keep tool execution fully inside the docker sandbox.
        # We therefore do not attach host-side utility tools in this mode.
        # Network helpers are implemented as container-executed wrappers.
        docker_network_tools = self._build_docker_network_tools(sandbox)
        docker_skill_tools = self._build_docker_skill_tools(sandbox)
        scheduled_task_tools = self._build_scheduled_task_tools(
            conversation_id=session_id,
            user_id=user_id,
        )
        graph, _ = self._create_cli_agent_compat(
            model=model_result.model,
            assistant_id="web-agent",
            tools=[*docker_network_tools, *docker_skill_tools, *scheduled_task_tools],
            sandbox=sandbox,
            system_prompt=system_prompt,
            auto_approve=True,
            enable_memory=False,
            enable_skills=False,
            enable_shell=False,
            interactive=False,
            cwd=Path(docker_cfg.workdir),
            checkpointer=self._checkpointer,
            # Supported by some deepagents-cli versions only.
            enable_ask_user=False,
        )
        return AgentBundle(cache_key=cache_key, graph=graph)

    def _append_docker_skills_prompt(
        self,
        base_prompt: str | None,
        *,
        user_id: str,
    ) -> str:
        docker_cfg = self.config.docker_sandbox
        workdir = docker_cfg.workdir.rstrip("/") or "/workspace"
        sandbox_builtin_skills_dir = f"{workdir}/skills-builtin"
        sandbox_user_skills_dir = f"{workdir}/skills"
        sandbox_env_manifest = self._sandbox_environment_manifest_path()

        builtin_skill_entries: list[str] = []
        local_builtin_skills_root = docker_cfg.skills_builtin_dir
        if local_builtin_skills_root.exists() and local_builtin_skills_root.is_dir():
            for child in sorted(local_builtin_skills_root.iterdir(), key=lambda item: item.name.lower()):
                if not child.is_dir():
                    continue
                if not (child / "SKILL.md").is_file():
                    continue
                builtin_skill_entries.append(
                    f"- `{sandbox_builtin_skills_dir}/{child.name}/SKILL.md`"
                )

        safe_user_id = self._sanitize_skill_user_id(user_id)
        user_skill_entries: list[str] = []
        local_user_skills_root = docker_cfg.skills_user_dir / safe_user_id
        if local_user_skills_root.exists() and local_user_skills_root.is_dir():
            for child in sorted(local_user_skills_root.iterdir(), key=lambda item: item.name.lower()):
                if not child.is_dir():
                    continue
                if not (child / "SKILL.md").is_file():
                    continue
                user_skill_entries.append(f"- `{sandbox_user_skills_dir}/{child.name}/SKILL.md`")

        extra_sections: list[str] = [
            "## Skills Directories",
            f"- User skills root in this sandbox: `{sandbox_user_skills_dir}`",
            f"- Built-in skills root in this sandbox: `{sandbox_builtin_skills_dir}`",
            "- Prefer creating/updating user skills under `/workspace/skills`.",
            "- Use built-in skills under `/workspace/skills-builtin` as reusable references.",
            "- User-visible artifacts (reports, exports, generated files) should be saved under `/workspace/uploads` by default.",
            f"- Sandbox environment manifest file: `{sandbox_env_manifest}`.",
            "- Before installing any software/python dependencies/fonts, first read the sandbox environment manifest.",
            "- Only install missing dependencies; avoid re-installing tools/dependencies/fonts that are already present.",
            "- If the manifest is missing, recreate it in the sandbox and then continue.",
            "- When a task matches a skill, inspect its `SKILL.md` first and follow it.",
            "- Execute any referenced scripts inside the sandbox workspace.",
        ]
        if user_skill_entries:
            extra_sections.append("- Discovered user skills:")
            extra_sections.extend(user_skill_entries)
        if builtin_skill_entries:
            extra_sections.append("- Discovered built-in skills:")
            extra_sections.extend(builtin_skill_entries)

        addon = "\n".join(extra_sections).strip()
        if base_prompt and base_prompt.strip():
            return f"{base_prompt.rstrip()}\n\n{addon}"
        return addon

    @staticmethod
    def _sanitize_skill_user_id(raw_user_id: str) -> str:
        cleaned = re.sub(r"[^0-9A-Za-z._-]+", "-", str(raw_user_id or "").strip()).strip("._-")
        if not cleaned:
            return "user"
        return cleaned[:64]

    @staticmethod
    def _strip_default_user_skills_hint(prompt: str | None) -> str | None:
        if not prompt:
            return prompt

        cleaned = re.sub(
            r"\n### Skills Directory\n.*?(?=\n### |\Z)",
            "\n",
            prompt,
            flags=re.DOTALL,
        )
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
        return cleaned

    def _build_docker_network_tools(self, sandbox: Any) -> list[Any]:
        def docker_http_request(
            url: str,
            method: str = "GET",
            headers: dict[str, str] | None = None,
            data: str | dict | None = None,
            params: dict[str, str] | None = None,
            timeout: int = 30,
        ) -> dict[str, Any]:
            """Make HTTP requests from inside docker sandbox using Python stdlib."""
            payload = {
                "url": url,
                "method": method,
                "headers": headers,
                "data": data,
                "params": params,
                "timeout": timeout,
            }
            return self._run_docker_network_tool(sandbox, "http_request", payload)

        docker_http_request.__name__ = "http_request"

        def docker_fetch_url(url: str, timeout: int = 30) -> dict[str, Any]:
            """Fetch URL from inside docker sandbox and return markdown-like content."""
            payload = {"url": url, "timeout": timeout}
            return self._run_docker_network_tool(sandbox, "fetch_url", payload)

        docker_fetch_url.__name__ = "fetch_url"
        return [docker_http_request, docker_fetch_url]

    def _build_docker_skill_tools(self, sandbox: Any) -> list[Any]:
        def docker_skill_create(
            name: str,
            description: str,
            body_markdown: str = "",
            overwrite: bool = False,
            with_evals: bool = False,
        ) -> dict[str, Any]:
            """Create a user skill under /workspace/skills/<name> with SKILL.md."""
            payload = {
                "name": name,
                "description": description,
                "body_markdown": body_markdown,
                "overwrite": overwrite,
                "with_evals": with_evals,
            }
            return self._run_docker_skill_tool(sandbox, "skill_create", payload)

        docker_skill_create.__name__ = "skill_create"

        def docker_skill_update_files(
            name: str,
            files: list[dict[str, Any]],
        ) -> dict[str, Any]:
            """Update multiple files under /workspace/skills/<name> safely."""
            payload = {
                "name": name,
                "files": files,
            }
            return self._run_docker_skill_tool(sandbox, "skill_update_files", payload)

        docker_skill_update_files.__name__ = "skill_update_files"

        def docker_skill_validate(name: str) -> dict[str, Any]:
            """Validate a user skill via skill-creator validator script."""
            payload = {"name": name}
            return self._run_docker_skill_tool(sandbox, "skill_validate", payload)

        docker_skill_validate.__name__ = "skill_validate"

        def docker_skill_package(
            name: str,
            output_dir: str = "/workspace/skills/.packages",
        ) -> dict[str, Any]:
            """Package a user skill into a .skill archive."""
            payload = {"name": name, "output_dir": output_dir}
            return self._run_docker_skill_tool(sandbox, "skill_package", payload)

        docker_skill_package.__name__ = "skill_package"

        def docker_skill_list(
            include_user: bool = True,
            include_builtin: bool = True,
        ) -> dict[str, Any]:
            """List skills from /workspace/skills and /workspace/skills-builtin."""
            payload = {
                "include_user": include_user,
                "include_builtin": include_builtin,
            }
            return self._run_docker_skill_tool(sandbox, "skill_list", payload)

        docker_skill_list.__name__ = "skill_list"

        return [
            docker_skill_create,
            docker_skill_update_files,
            docker_skill_validate,
            docker_skill_package,
            docker_skill_list,
        ]

    def _build_scheduled_task_tools(
        self,
        *,
        conversation_id: str,
        user_id: str,
    ) -> list[Any]:
        def scheduled_task_list(limit: int = 50, include_disabled: bool = True) -> dict[str, Any]:
            """List scheduled tasks of current conversation."""
            return self._run_coro_sync(
                self._scheduled_task_list(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    limit=limit,
                    include_disabled=include_disabled,
                )
            )

        scheduled_task_list.__name__ = "scheduled_task_list"

        def scheduled_task_create(
            name: str,
            task_type: str = "hybrid_task",
            script_command: str | None = None,
            skill_name: str | None = None,
            skill_input: str | None = None,
            schedule_type: str = "cron",
            timezone: str | None = None,
            cron_expr: str | None = "0 9 * * *",
            interval_minutes: int | None = None,
            enabled: bool = True,
            summary_prompt: str | None = None,
            max_runs: int | None = None,
        ) -> dict[str, Any]:
            """Create a scheduled task in current conversation."""
            return self._run_coro_sync(
                self._scheduled_task_create(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    name=name,
                    task_type=task_type,
                    script_command=script_command,
                    skill_name=skill_name,
                    skill_input=skill_input,
                    schedule_type=schedule_type,
                    timezone=timezone,
                    cron_expr=cron_expr,
                    interval_minutes=interval_minutes,
                    enabled=enabled,
                    summary_prompt=summary_prompt,
                    max_runs=max_runs,
                )
            )

        scheduled_task_create.__name__ = "scheduled_task_create"

        def scheduled_task_update(
            task_id: str,
            task_type: str | None = None,
            name: str | None = None,
            script_command: str | None = None,
            skill_name: str | None = None,
            skill_input: str | None = None,
            schedule_type: str | None = None,
            timezone: str | None = None,
            cron_expr: str | None = None,
            interval_minutes: int | None = None,
            enabled: bool | None = None,
            summary_prompt: str | None = None,
            max_runs: int | None = None,
        ) -> dict[str, Any]:
            """Update a scheduled task in current conversation."""
            return self._run_coro_sync(
                self._scheduled_task_update(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    task_id=task_id,
                    task_type=task_type,
                    name=name,
                    script_command=script_command,
                    skill_name=skill_name,
                    skill_input=skill_input,
                    schedule_type=schedule_type,
                    timezone=timezone,
                    cron_expr=cron_expr,
                    interval_minutes=interval_minutes,
                    enabled=enabled,
                    summary_prompt=summary_prompt,
                    max_runs=max_runs,
                )
            )

        scheduled_task_update.__name__ = "scheduled_task_update"

        def scheduled_task_delete(task_id: str) -> dict[str, Any]:
            """Delete a scheduled task in current conversation."""
            return self._run_coro_sync(
                self._scheduled_task_delete(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    task_id=task_id,
                )
            )

        scheduled_task_delete.__name__ = "scheduled_task_delete"

        def scheduled_task_enable(task_id: str) -> dict[str, Any]:
            """Enable a scheduled task."""
            return self._run_coro_sync(
                self._scheduled_task_set_enabled(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    task_id=task_id,
                    enabled=True,
                )
            )

        scheduled_task_enable.__name__ = "scheduled_task_enable"

        def scheduled_task_disable(task_id: str) -> dict[str, Any]:
            """Disable a scheduled task."""
            return self._run_coro_sync(
                self._scheduled_task_set_enabled(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    task_id=task_id,
                    enabled=False,
                )
            )

        scheduled_task_disable.__name__ = "scheduled_task_disable"

        def scheduled_task_execute(task_id: str) -> dict[str, Any]:
            """Request immediate execution for a scheduled task."""
            return self._run_coro_sync(
                self._scheduled_task_execute_now(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    task_id=task_id,
                )
            )

        scheduled_task_execute.__name__ = "scheduled_task_execute"

        def scheduled_task_list_runs(task_id: str, limit: int = 20) -> dict[str, Any]:
            """List recent run records of a scheduled task."""
            return self._run_coro_sync(
                self._scheduled_task_list_runs(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    task_id=task_id,
                    limit=limit,
                )
            )

        scheduled_task_list_runs.__name__ = "scheduled_task_list_runs"

        return [
            scheduled_task_list,
            scheduled_task_create,
            scheduled_task_update,
            scheduled_task_delete,
            scheduled_task_enable,
            scheduled_task_disable,
            scheduled_task_execute,
            scheduled_task_list_runs,
        ]

    async def _scheduled_task_list(
        self,
        *,
        conversation_id: str,
        user_id: str,
        limit: int,
        include_disabled: bool,
    ) -> dict[str, Any]:
        safe_limit = max(1, min(int(limit or 50), 200))
        async with session_factory()() as db:
            stmt = (
                select(ScheduledTask)
                .where(
                    ScheduledTask.conversation_id == conversation_id,
                    ScheduledTask.user_id == user_id,
                )
                .order_by(ScheduledTask.created_at.desc())
                .limit(safe_limit)
            )
            if not include_disabled:
                stmt = stmt.where(ScheduledTask.enabled.is_(True))
            tasks = (await db.execute(stmt)).scalars().all()
        return {
            "ok": True,
            "count": len(tasks),
            "tasks": [self._scheduled_task_to_dict(task) for task in tasks],
        }

    async def _scheduled_task_create(
        self,
        *,
        conversation_id: str,
        user_id: str,
        name: str,
        task_type: str,
        script_command: str | None,
        skill_name: str | None,
        skill_input: str | None,
        schedule_type: str,
        timezone: str | None,
        cron_expr: str | None,
        interval_minutes: int | None,
        enabled: bool,
        summary_prompt: str | None,
        max_runs: int | None,
    ) -> dict[str, Any]:
        task_name = str(name or "").strip()
        if not task_name:
            return {"ok": False, "error": self._tr("runtime.scheduled.name_required")}
        if len(task_name) > 128:
            return {"ok": False, "error": self._tr("runtime.scheduled.name_too_long")}

        task_type_value = str(task_type or "").strip().lower()
        if task_type_value not in {"hybrid_task", "skill_task"}:
            return {"ok": False, "error": self._tr("runtime.scheduled.invalid_task_type")}

        command_text = str(script_command or "").strip() or None
        if command_text is not None and len(command_text) > 20000:
            return {"ok": False, "error": self._tr("runtime.scheduled.script_command_too_long")}

        skill_name_text = str(skill_name or "").strip() or None
        if skill_name_text is not None and len(skill_name_text) > 128:
            return {"ok": False, "error": self._tr("runtime.scheduled.skill_name_too_long")}
        skill_input_text = str(skill_input or "").strip() or None
        if skill_input_text is not None and len(skill_input_text) > 20000:
            return {"ok": False, "error": self._tr("runtime.scheduled.skill_input_too_long")}

        if task_type_value == "hybrid_task":
            if not command_text:
                return {"ok": False, "error": self._tr("schedule.hybrid_command_required")}
            skill_name_text = None
            skill_input_text = None
        else:
            if not skill_name_text:
                return {"ok": False, "error": self._tr("schedule.skill_name_required")}
            command_text = None

        try:
            schedule_timezone = str(timezone or "").strip() or self.config.sandbox_timezone
            normalized_interval_seconds = (
                int(interval_minutes) * 60
                if interval_minutes is not None
                else None
            )
            normalized = normalize_schedule(
                schedule_type=schedule_type,
                timezone=schedule_timezone,
                cron_expr=cron_expr,
                interval_seconds=normalized_interval_seconds,
            )
            next_run_at = compute_next_run_at(
                schedule_type=normalized.schedule_type,
                timezone=normalized.timezone,
                cron_expr=normalized.cron_expr,
                interval_seconds=normalized.interval_seconds,
                from_time=now_utc(),
            )
        except ScheduleValidationError as exc:
            return {
                "ok": False,
                "error": self._tr("runtime.scheduled.validation_error_detail", error=exc),
            }

        summary_text = (summary_prompt or "").strip() or None
        if summary_text is not None and len(summary_text) > 10000:
            return {"ok": False, "error": self._tr("runtime.scheduled.summary_prompt_too_long")}
        max_runs_value: int | None = None
        if max_runs is not None:
            try:
                max_runs_value = int(max_runs)
            except (TypeError, ValueError):
                return {"ok": False, "error": self._tr("runtime.scheduled.max_runs_must_be_int")}
            if max_runs_value < 1:
                return {"ok": False, "error": self._tr("runtime.scheduled.max_runs_min")}

        async with session_factory()() as db:
            task = ScheduledTask(
                conversation_id=conversation_id,
                user_id=user_id,
                name=task_name,
                task_type=task_type_value,
                enabled=bool(enabled),
                schedule_type=normalized.schedule_type,
                timezone=normalized.timezone,
                cron_expr=normalized.cron_expr,
                interval_seconds=normalized.interval_seconds,
                script_command=command_text,
                skill_name=skill_name_text,
                skill_input=skill_input_text,
                summary_prompt=summary_text,
                max_runs=max_runs_value,
                run_count=0,
                next_run_at=next_run_at,
            )
            db.add(task)
            await db.commit()
            await db.refresh(task)
        return {"ok": True, "task": self._scheduled_task_to_dict(task)}

    async def _scheduled_task_update(
        self,
        *,
        conversation_id: str,
        user_id: str,
        task_id: str,
        task_type: str | None,
        name: str | None,
        script_command: str | None,
        skill_name: str | None,
        skill_input: str | None,
        schedule_type: str | None,
        timezone: str | None,
        cron_expr: str | None,
        interval_minutes: int | None,
        enabled: bool | None,
        summary_prompt: str | None,
        max_runs: int | None,
    ) -> dict[str, Any]:
        target_task_id = str(task_id or "").strip()
        if not target_task_id:
            return {"ok": False, "error": self._tr("runtime.scheduled.task_id_required")}

        async with session_factory()() as db:
            task = (
                await db.execute(
                    select(ScheduledTask).where(
                        ScheduledTask.id == target_task_id,
                        ScheduledTask.conversation_id == conversation_id,
                        ScheduledTask.user_id == user_id,
                    )
                )
            ).scalar_one_or_none()
            if task is None:
                return {
                    "ok": False,
                    "error": self._tr("runtime.scheduled.task_not_found", task_id=target_task_id),
                }

            if isinstance(name, str):
                new_name = name.strip()
                if not new_name:
                    return {"ok": False, "error": self._tr("runtime.scheduled.name_empty")}
                if len(new_name) > 128:
                    return {"ok": False, "error": self._tr("runtime.scheduled.name_too_long")}
                task.name = new_name

            if isinstance(script_command, str):
                new_script = script_command.strip()
                if len(new_script) > 20000:
                    return {"ok": False, "error": self._tr("runtime.scheduled.script_command_too_long")}
                task.script_command = new_script or None

            if isinstance(skill_name, str):
                new_skill_name = skill_name.strip()
                if len(new_skill_name) > 128:
                    return {"ok": False, "error": self._tr("runtime.scheduled.skill_name_too_long")}
                task.skill_name = new_skill_name or None

            if isinstance(skill_input, str):
                new_skill_input = skill_input.strip()
                if len(new_skill_input) > 20000:
                    return {"ok": False, "error": self._tr("runtime.scheduled.skill_input_too_long")}
                task.skill_input = new_skill_input or None

            if isinstance(summary_prompt, str):
                cleaned_summary = summary_prompt.strip()
                if len(cleaned_summary) > 10000:
                    return {"ok": False, "error": self._tr("runtime.scheduled.summary_prompt_too_long")}
                task.summary_prompt = cleaned_summary or None
            if max_runs is not None:
                try:
                    next_max_runs = int(max_runs)
                except (TypeError, ValueError):
                    return {"ok": False, "error": self._tr("runtime.scheduled.max_runs_must_be_int")}
                if next_max_runs < 1:
                    return {"ok": False, "error": self._tr("runtime.scheduled.max_runs_min")}
                task.max_runs = next_max_runs

            if isinstance(task_type, str):
                next_task_type = task_type.strip().lower()
                if next_task_type not in {"hybrid_task", "skill_task"}:
                    return {"ok": False, "error": self._tr("runtime.scheduled.invalid_task_type")}
                task.task_type = next_task_type

            if task.task_type == "hybrid_task":
                if not (task.script_command or "").strip():
                    return {"ok": False, "error": self._tr("schedule.hybrid_command_required")}
                task.skill_name = None
                task.skill_input = None
            elif task.task_type == "skill_task":
                if not (task.skill_name or "").strip():
                    return {"ok": False, "error": self._tr("schedule.skill_name_required")}
                task.script_command = None

            schedule_changed = any(
                value is not None
                for value in (schedule_type, timezone, cron_expr, interval_minutes, enabled)
            )
            if isinstance(enabled, bool):
                task.enabled = enabled

            if schedule_changed:
                next_schedule_type = schedule_type if schedule_type is not None else task.schedule_type
                next_timezone = timezone if timezone is not None else task.timezone
                next_cron_expr = cron_expr if cron_expr is not None else task.cron_expr
                next_interval_seconds = (
                    (int(interval_minutes) * 60) if interval_minutes is not None else task.interval_seconds
                )
                try:
                    normalized = normalize_schedule(
                        schedule_type=next_schedule_type,
                        timezone=next_timezone,
                        cron_expr=next_cron_expr,
                        interval_seconds=next_interval_seconds,
                    )
                except ScheduleValidationError as exc:
                    return {
                        "ok": False,
                        "error": self._tr("runtime.scheduled.validation_error_detail", error=exc),
                    }
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

            await db.commit()
            await db.refresh(task)
            return {"ok": True, "task": self._scheduled_task_to_dict(task)}

    async def _scheduled_task_delete(
        self,
        *,
        conversation_id: str,
        user_id: str,
        task_id: str,
    ) -> dict[str, Any]:
        target_task_id = str(task_id or "").strip()
        if not target_task_id:
            return {"ok": False, "error": self._tr("runtime.scheduled.task_id_required")}
        async with session_factory()() as db:
            task = (
                await db.execute(
                    select(ScheduledTask).where(
                        ScheduledTask.id == target_task_id,
                        ScheduledTask.conversation_id == conversation_id,
                        ScheduledTask.user_id == user_id,
                    )
                )
            ).scalar_one_or_none()
            if task is None:
                return {
                    "ok": False,
                    "error": self._tr("runtime.scheduled.task_not_found", task_id=target_task_id),
                }
            task_name = task.name
            await db.delete(task)
            await db.commit()
        return {
            "ok": True,
            "deleted_task_id": target_task_id,
            "name": task_name,
        }

    async def _scheduled_task_set_enabled(
        self,
        *,
        conversation_id: str,
        user_id: str,
        task_id: str,
        enabled: bool,
    ) -> dict[str, Any]:
        target_task_id = str(task_id or "").strip()
        if not target_task_id:
            return {"ok": False, "error": self._tr("runtime.scheduled.task_id_required")}
        async with session_factory()() as db:
            task = (
                await db.execute(
                    select(ScheduledTask).where(
                        ScheduledTask.id == target_task_id,
                        ScheduledTask.conversation_id == conversation_id,
                        ScheduledTask.user_id == user_id,
                    )
                )
            ).scalar_one_or_none()
            if task is None:
                return {
                    "ok": False,
                    "error": self._tr("runtime.scheduled.task_not_found", task_id=target_task_id),
                }
            if enabled and task.max_runs is not None and task.run_count >= task.max_runs:
                task.enabled = False
                task.run_now_requested_at = None
                await db.commit()
                await db.refresh(task)
                return {
                    "ok": False,
                    "error": self._tr("runtime.scheduled.task_reached_max_runs"),
                    "task": self._scheduled_task_to_dict(task),
                }
            task.enabled = bool(enabled)
            await db.commit()
            await db.refresh(task)
        return {"ok": True, "task": self._scheduled_task_to_dict(task)}

    async def _scheduled_task_execute_now(
        self,
        *,
        conversation_id: str,
        user_id: str,
        task_id: str,
    ) -> dict[str, Any]:
        target_task_id = str(task_id or "").strip()
        if not target_task_id:
            return {"ok": False, "error": self._tr("runtime.scheduled.task_id_required")}
        async with session_factory()() as db:
            task = (
                await db.execute(
                    select(ScheduledTask).where(
                        ScheduledTask.id == target_task_id,
                        ScheduledTask.conversation_id == conversation_id,
                        ScheduledTask.user_id == user_id,
                    )
                )
            ).scalar_one_or_none()
            if task is None:
                return {
                    "ok": False,
                    "error": self._tr("runtime.scheduled.task_not_found", task_id=target_task_id),
                }
            task.run_now_requested_at = now_utc()
            await db.commit()
            await db.refresh(task)
        return {"ok": True, "task": self._scheduled_task_to_dict(task), "queued": True}

    async def _scheduled_task_list_runs(
        self,
        *,
        conversation_id: str,
        user_id: str,
        task_id: str,
        limit: int,
    ) -> dict[str, Any]:
        target_task_id = str(task_id or "").strip()
        if not target_task_id:
            return {"ok": False, "error": self._tr("runtime.scheduled.task_id_required")}
        safe_limit = max(1, min(int(limit or 20), 200))
        async with session_factory()() as db:
            task = (
                await db.execute(
                    select(ScheduledTask).where(
                        ScheduledTask.id == target_task_id,
                        ScheduledTask.conversation_id == conversation_id,
                        ScheduledTask.user_id == user_id,
                    )
                )
            ).scalar_one_or_none()
            if task is None:
                return {
                    "ok": False,
                    "error": self._tr("runtime.scheduled.task_not_found", task_id=target_task_id),
                }
            runs = (
                await db.execute(
                    select(ScheduledTaskRun)
                    .where(ScheduledTaskRun.task_id == task.id)
                    .order_by(ScheduledTaskRun.created_at.desc())
                    .limit(safe_limit)
                )
            ).scalars().all()
        return {
            "ok": True,
            "count": len(runs),
            "task_id": target_task_id,
            "runs": [self._scheduled_task_run_to_dict(item) for item in runs],
        }

    def _run_coro_sync(self, coro: Any) -> Any:
        target_loop = self._runtime_loop
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None

        if running_loop is None and target_loop is not None and target_loop.is_running():
            return asyncio.run_coroutine_threadsafe(coro, target_loop).result()

        if (
            running_loop is not None
            and target_loop is not None
            and target_loop is not running_loop
            and target_loop.is_running()
        ):
            return asyncio.run_coroutine_threadsafe(coro, target_loop).result()

        if running_loop is None:
            return asyncio.run(coro)

        state: dict[str, Any] = {}

        def runner() -> None:
            try:
                state["result"] = asyncio.run(coro)
            except Exception as exc:  # noqa: BLE001
                state["error"] = exc

        thread = threading.Thread(target=runner, daemon=True)
        thread.start()
        thread.join()
        if "error" in state:
            raise state["error"]
        return state.get("result")

    @staticmethod
    def _scheduled_task_to_dict(task: ScheduledTask) -> dict[str, Any]:
        return {
            "id": task.id,
            "conversation_id": task.conversation_id,
            "user_id": task.user_id,
            "name": task.name,
            "task_type": task.task_type,
            "enabled": task.enabled,
            "schedule_type": task.schedule_type,
            "timezone": task.timezone,
            "cron_expr": task.cron_expr,
            "interval_minutes": (
                max(1, (task.interval_seconds + 59) // 60)
                if isinstance(task.interval_seconds, int)
                else None
            ),
            "script_command": task.script_command,
            "skill_name": task.skill_name,
            "skill_input": task.skill_input,
            "summary_prompt": task.summary_prompt,
            "max_runs": task.max_runs,
            "run_count": task.run_count,
            "next_run_at": task.next_run_at.isoformat(),
            "run_now_requested_at": task.run_now_requested_at.isoformat() if task.run_now_requested_at else None,
            "last_run_at": task.last_run_at.isoformat() if task.last_run_at else None,
            "created_at": task.created_at.isoformat(),
            "updated_at": task.updated_at.isoformat(),
        }

    @staticmethod
    def _scheduled_task_run_to_dict(run: ScheduledTaskRun) -> dict[str, Any]:
        return {
            "id": run.id,
            "task_id": run.task_id,
            "conversation_id": run.conversation_id,
            "user_id": run.user_id,
            "status": run.status,
            "scheduled_for": run.scheduled_for.isoformat(),
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "start_message_id": run.start_message_id,
            "result_message_id": run.result_message_id,
            "script_exit_code": run.script_exit_code,
            "script_output_text": run.script_output_text,
            "summary_text": run.summary_text,
            "error_text": run.error_text,
            "created_at": run.created_at.isoformat(),
        }

    def _run_docker_skill_tool(
        self,
        sandbox: Any,
        tool_name: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        marker = "__TEAMCLAW_SKILL_TOOL_JSON__:"
        script = self._docker_skill_tool_script(marker)
        script_b64 = base64.b64encode(script.encode("utf-8")).decode("ascii")
        payload_b64 = base64.b64encode(
            json.dumps(
                {"tool_name": tool_name, "data": payload},
                ensure_ascii=False,
            ).encode("utf-8")
        ).decode("ascii")

        command = (
            "python3 -c "
            + shlex.quote(
                "import base64,json; "
                f"payload=json.loads(base64.b64decode('{payload_b64}').decode('utf-8')); "
                f"code=base64.b64decode('{script_b64}').decode('utf-8'); "
                "globals_dict={'payload': payload}; "
                "exec(compile(code, '<docker_skill_tool>', 'exec'), globals_dict, globals_dict)"
            )
        )

        exec_result = sandbox.execute(command, timeout=90)
        output = str(getattr(exec_result, "output", "") or "")
        exit_code = getattr(exec_result, "exit_code", None)
        for line in output.splitlines():
            if line.startswith(marker):
                raw_json = line[len(marker) :].strip()
                try:
                    parsed = json.loads(raw_json)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict):
                    return parsed

        error_text = output.strip() or self._tr("runtime.sandbox.no_output_skill_tool")
        if exit_code not in {0, None}:
            error_text = f"{error_text}\n(exit_code={exit_code})"
        return {
            "ok": False,
            "error": self._tr("runtime.sandbox.skill_tool_failed", tool_name=tool_name, error=error_text),
        }

    @staticmethod
    def _docker_skill_tool_script(marker: str) -> str:
        script = """
import json
import os
import re
import subprocess
import zipfile

payload = payload if isinstance(payload, dict) else {}
tool_name = str(payload.get("tool_name") or "").strip()
data = payload.get("data")
if not isinstance(data, dict):
    data = {}

USER_ROOT = "/workspace/skills"
BUILTIN_ROOT = "/workspace/skills-builtin"
NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,63})$")

def emit(obj):
    print("__MARKER__" + json.dumps(obj, ensure_ascii=False))

def fail(message, **extra):
    res = {"ok": False, "error": message}
    res.update(extra)
    emit(res)
    raise SystemExit(0)

def as_bool(value, default=False):
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return default

def ensure_name(raw):
    if not isinstance(raw, str):
        fail("skill name must be a string")
    name = raw.strip()
    if not NAME_RE.match(name):
        fail("invalid skill name; use kebab-case letters/numbers/hyphen, max 64 chars", name=name)
    return name

def ensure_dir(path):
    os.makedirs(path, exist_ok=True)

def parse_frontmatter(skill_md_path):
    try:
        text = open(skill_md_path, "r", encoding="utf-8").read()
    except Exception:
        return {}
    if not text.startswith("---"):
        return {}
    match = re.match(r"^---\\s*\\n(.*?)\\n---\\s*(?:\\n|$)", text, flags=re.DOTALL)
    if not match:
        return {}
    frontmatter = {}
    for line in match.group(1).splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        frontmatter[key.strip()] = value.strip().strip('"').strip("'")
    return frontmatter

def list_skills_from_root(root, kind):
    items = []
    if not os.path.isdir(root):
        return items
    for name in sorted(os.listdir(root), key=lambda x: x.lower()):
        skill_dir = os.path.join(root, name)
        skill_md = os.path.join(skill_dir, "SKILL.md")
        if not os.path.isdir(skill_dir) or not os.path.isfile(skill_md):
            continue
        meta = parse_frontmatter(skill_md)
        try:
            modified_at = int(os.stat(skill_md).st_mtime)
        except Exception:
            modified_at = None
        items.append(
            {
                "name": name,
                "path": skill_dir,
                "kind": kind,
                "description": meta.get("description"),
                "modified_at": modified_at,
            }
        )
    return items

def resolve_under(root, relative_path):
    if not isinstance(relative_path, str):
        fail("relative path must be a string")
    rel = relative_path.replace("\\\\", "/").strip().lstrip("/")
    if not rel:
        fail("relative path is required")
    parts = [part for part in rel.split("/") if part not in {"", "."}]
    if any(part == ".." for part in parts):
        fail("path traversal is not allowed", path=relative_path)
    safe_rel = "/".join(parts)
    root_norm = os.path.normpath(root)
    target = os.path.normpath(os.path.join(root_norm, safe_rel))
    if target != root_norm and not target.startswith(root_norm + os.sep):
        fail("path escapes skill directory", path=relative_path)
    return safe_rel, target

def fallback_validate(skill_dir):
    skill_md = os.path.join(skill_dir, "SKILL.md")
    if not os.path.isfile(skill_md):
        return False, "SKILL.md not found"
    frontmatter = parse_frontmatter(skill_md)
    if not frontmatter:
        return False, "No valid YAML frontmatter found"
    name = str(frontmatter.get("name") or "").strip()
    description = str(frontmatter.get("description") or "").strip()
    if not name:
        return False, "Missing 'name' in frontmatter"
    if not NAME_RE.match(name):
        return False, "Invalid name in frontmatter"
    if not description:
        return False, "Missing 'description' in frontmatter"
    if len(description) > 1024:
        return False, "Description is too long"
    return True, "Skill is valid!"

def run_validation(name, skill_dir):
    validator = "/workspace/skills-builtin/skill-creator/scripts/quick_validate.py"
    if os.path.isfile(validator):
        proc = subprocess.run(
            ["python3", validator, skill_dir],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        output = "\\n".join(part for part in [(proc.stdout or "").strip(), (proc.stderr or "").strip()] if part).strip()
        valid = proc.returncode == 0
        return {
            "valid": valid,
            "name": name,
            "path": skill_dir,
            "validator": "skill-creator",
            "message": output or ("Skill is valid!" if valid else "Validation failed"),
            "exit_code": proc.returncode,
        }

    valid, message = fallback_validate(skill_dir)
    return {
        "valid": valid,
        "name": name,
        "path": skill_dir,
        "validator": "fallback",
        "message": message,
    }

try:
    if tool_name == "skill_list":
        include_user = as_bool(data.get("include_user"), True)
        include_builtin = as_bool(data.get("include_builtin"), True)
        user_skills = list_skills_from_root(USER_ROOT, "user") if include_user else []
        builtin_skills = list_skills_from_root(BUILTIN_ROOT, "builtin") if include_builtin else []
        emit(
            {
                "ok": True,
                "user_skills": user_skills,
                "builtin_skills": builtin_skills,
                "total": len(user_skills) + len(builtin_skills),
            }
        )
        raise SystemExit(0)

    if tool_name == "skill_create":
        name = ensure_name(data.get("name"))
        description = data.get("description")
        if not isinstance(description, str) or not description.strip():
            fail("description is required")
        description = description.strip()
        body_markdown = data.get("body_markdown")
        if not isinstance(body_markdown, str):
            body_markdown = ""
        overwrite = as_bool(data.get("overwrite"), False)
        with_evals = as_bool(data.get("with_evals"), False)

        skill_dir = os.path.join(USER_ROOT, name)
        exists = os.path.isdir(skill_dir)
        if exists and not overwrite:
            fail("skill already exists", name=name, path=skill_dir)
        ensure_dir(skill_dir)

        lines = [
            "---",
            f"name: {json.dumps(name, ensure_ascii=False)}",
            f"description: {json.dumps(description, ensure_ascii=False)}",
            "---",
            "",
        ]
        body = body_markdown.strip()
        if body:
            lines.append(body)
        else:
            lines.extend(
                [
                    "## Overview",
                    "Describe when this skill should be used and what it should do.",
                ]
            )

        skill_md_path = os.path.join(skill_dir, "SKILL.md")
        with open(skill_md_path, "w", encoding="utf-8") as fp:
            fp.write("\\n".join(lines).rstrip() + "\\n")

        created_files = ["SKILL.md"]
        if with_evals:
            evals_dir = os.path.join(skill_dir, "evals")
            ensure_dir(evals_dir)
            evals_path = os.path.join(evals_dir, "evals.json")
            if overwrite or not os.path.exists(evals_path):
                with open(evals_path, "w", encoding="utf-8") as fp:
                    json.dump({"skill_name": name, "evals": []}, fp, ensure_ascii=False, indent=2)
                    fp.write("\\n")
                created_files.append("evals/evals.json")

        validation = run_validation(name, skill_dir)
        emit(
            {
                "ok": bool(validation.get("valid")),
                "name": name,
                "path": skill_dir,
                "created_files": created_files,
                "overwritten": bool(exists and overwrite),
                "validation": validation,
            }
        )
        raise SystemExit(0)

    if tool_name == "skill_update_files":
        name = ensure_name(data.get("name"))
        files = data.get("files")
        if not isinstance(files, list) or not files:
            fail("files must be a non-empty list")

        skill_dir = os.path.join(USER_ROOT, name)
        if not os.path.isdir(skill_dir):
            fail("skill not found", name=name, path=skill_dir)

        updated_files = []
        for item in files:
            if not isinstance(item, dict):
                fail("each files item must be an object")
            rel_path = item.get("relative_path", item.get("path"))
            safe_rel, target_path = resolve_under(skill_dir, rel_path)
            content = item.get("content", "")
            if not isinstance(content, str):
                content = json.dumps(content, ensure_ascii=False, indent=2)

            mode = str(item.get("mode") or "overwrite").strip().lower()
            if mode not in {"overwrite", "append", "create"}:
                fail("invalid file mode", mode=mode, path=safe_rel)

            ensure_dir(os.path.dirname(target_path))
            if mode == "create" and os.path.exists(target_path):
                fail("target file already exists", path=safe_rel)
            write_mode = "a" if mode == "append" else "w"
            with open(target_path, write_mode, encoding="utf-8") as fp:
                fp.write(content)
            updated_files.append(safe_rel)

        validation = run_validation(name, skill_dir)
        emit(
            {
                "ok": bool(validation.get("valid")),
                "name": name,
                "path": skill_dir,
                "updated_files": updated_files,
                "count": len(updated_files),
                "validation": validation,
            }
        )
        raise SystemExit(0)

    if tool_name == "skill_validate":
        name = ensure_name(data.get("name"))
        skill_dir = os.path.join(USER_ROOT, name)
        if not os.path.isdir(skill_dir):
            fail("skill not found", name=name, path=skill_dir)

        result = run_validation(name, skill_dir)
        emit(
            {
                "ok": bool(result.get("valid")),
                **result,
            }
        )
        raise SystemExit(0)

    if tool_name == "skill_package":
        name = ensure_name(data.get("name"))
        skill_dir = os.path.join(USER_ROOT, name)
        if not os.path.isdir(skill_dir):
            fail("skill not found", name=name, path=skill_dir)

        output_dir = data.get("output_dir")
        if not isinstance(output_dir, str) or not output_dir.strip():
            output_dir = "/workspace/skills/.packages"
        output_dir = os.path.normpath(output_dir.strip())
        user_root_norm = os.path.normpath(USER_ROOT)
        if output_dir != user_root_norm and not output_dir.startswith(user_root_norm + os.sep):
            fail("output_dir must be inside /workspace/skills", output_dir=output_dir)
        ensure_dir(output_dir)

        output_path = os.path.join(output_dir, f"{name}.skill")
        packager = "/workspace/skills-builtin/skill-creator/scripts/package_skill.py"
        if os.path.isfile(packager):
            proc = subprocess.run(
                ["python3", packager, skill_dir, output_dir],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            output = "\\n".join(part for part in [(proc.stdout or "").strip(), (proc.stderr or "").strip()] if part).strip()
            if proc.returncode != 0 and not os.path.isfile(output_path):
                fail(
                    "packaging failed",
                    name=name,
                    path=skill_dir,
                    output_dir=output_dir,
                    message=output or "unknown package error",
                    exit_code=proc.returncode,
                )
            emit(
                {
                    "ok": True,
                    "name": name,
                    "path": skill_dir,
                    "output_path": output_path,
                    "packager": "skill-creator",
                    "message": output,
                    "exit_code": proc.returncode,
                }
            )
            raise SystemExit(0)

        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for root, _dirs, files in os.walk(skill_dir):
                for file_name in files:
                    full_path = os.path.join(root, file_name)
                    arc_name = os.path.relpath(full_path, os.path.dirname(skill_dir))
                    archive.write(full_path, arc_name)

        emit(
            {
                "ok": True,
                "name": name,
                "path": skill_dir,
                "output_path": output_path,
                "packager": "fallback",
                "message": "packaged with built-in zip fallback",
            }
        )
        raise SystemExit(0)

    fail("unsupported skill tool", tool_name=tool_name)
except SystemExit:
    pass
except Exception as exc:
    emit({"ok": False, "error": f"skill tool failed: {exc}"})
"""
        return script.replace("__MARKER__", marker)

    def _run_docker_network_tool(
        self,
        sandbox: Any,
        tool_name: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        marker = "__TEAMCLAW_TOOL_JSON__:"
        script = self._docker_network_tool_script(tool_name, marker)
        script_b64 = base64.b64encode(script.encode("utf-8")).decode("ascii")
        payload_b64 = base64.b64encode(
            json.dumps(payload, ensure_ascii=False).encode("utf-8")
        ).decode("ascii")

        command = (
            "python3 -c "
            + shlex.quote(
                "import base64,json; "
                f"payload=json.loads(base64.b64decode('{payload_b64}').decode('utf-8')); "
                f"code=base64.b64decode('{script_b64}').decode('utf-8'); "
                "globals_dict={'payload': payload}; "
                "exec(compile(code, '<docker_network_tool>', 'exec'), globals_dict, globals_dict)"
            )
        )

        timeout = payload.get("timeout")
        timeout_value = timeout if isinstance(timeout, int) and timeout > 0 else 30
        exec_result = sandbox.execute(command, timeout=max(5, timeout_value + 10))
        output = str(getattr(exec_result, "output", "") or "")
        exit_code = getattr(exec_result, "exit_code", None)
        for line in output.splitlines():
            if line.startswith(marker):
                raw_json = line[len(marker) :].strip()
                try:
                    parsed = json.loads(raw_json)
                    if isinstance(parsed, dict):
                        return parsed
                except json.JSONDecodeError:
                    break

        error_text = output.strip() or self._tr("runtime.sandbox.no_output_network_tool")
        if exit_code not in {0, None}:
            error_text = f"{error_text}\n(exit_code={exit_code})"
        if tool_name == "fetch_url":
            url = payload.get("url")
            return {
                "error": self._tr("runtime.sandbox.fetch_url_error", error=error_text),
                "url": url,
            }
        return {
            "success": False,
            "status_code": 0,
            "headers": {},
            "content": self._tr("runtime.sandbox.request_error", error=error_text),
            "url": payload.get("url"),
        }

    @staticmethod
    def _docker_network_tool_script(tool_name: str, marker: str) -> str:
        if tool_name == "fetch_url":
            return f"""
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

payload = payload if isinstance(payload, dict) else {{}}
url = str(payload.get("url") or "").strip()
timeout = payload.get("timeout", 30)
if not isinstance(timeout, int) or timeout <= 0:
    timeout = 30

class _HTMLToMarkdown(HTMLParser):
    def __init__(self):
        super().__init__()
        self._parts = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag in ("br",):
            self._parts.append("\\n")
        elif tag in ("p", "div", "section", "article", "li"):
            self._parts.append("\\n")
        elif tag == "h1":
            self._parts.append("\\n# ")
        elif tag == "h2":
            self._parts.append("\\n## ")
        elif tag == "h3":
            self._parts.append("\\n### ")
        elif tag == "h4":
            self._parts.append("\\n#### ")
        elif tag == "h5":
            self._parts.append("\\n##### ")
        elif tag == "h6":
            self._parts.append("\\n###### ")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip_depth:
            self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if tag in ("p", "div", "section", "article", "li", "h1", "h2", "h3", "h4", "h5", "h6"):
            self._parts.append("\\n")

    def handle_data(self, data):
        if self._skip_depth:
            return
        if data and data.strip():
            self._parts.append(data.strip() + " ")

    def markdown(self):
        text = "".join(self._parts)
        text = re.sub(r"\\n\\s*\\n\\s*\\n+", "\\n\\n", text)
        return text.strip()

def _decode_content(raw, headers):
    content_type = headers.get("content-type", "") if isinstance(headers, dict) else ""
    charset = "utf-8"
    if "charset=" in content_type.lower():
        charset = content_type.lower().split("charset=", 1)[1].split(";", 1)[0].strip() or "utf-8"
    try:
        text = raw.decode(charset, errors="replace")
    except Exception:
        text = raw.decode("utf-8", errors="replace")
    return text

def _emit(data):
    print("{marker}" + json.dumps(data, ensure_ascii=False))

if not url:
    _emit({{"error": "Fetch URL error: url is required", "url": url}})
else:
    try:
        req = urllib.request.Request(
            url,
            method="GET",
            headers={{"User-Agent": "Mozilla/5.0 (compatible; DeepAgents/1.0)"}},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            status_code = int(resp.getcode() or 200)
            final_url = str(resp.geturl())
            headers = dict(resp.headers.items())
            text = _decode_content(raw, headers)
            if "html" in headers.get("content-type", "").lower():
                parser = _HTMLToMarkdown()
                parser.feed(text)
                markdown_content = parser.markdown()
            else:
                markdown_content = text.strip()
            _emit({{
                "url": final_url,
                "markdown_content": markdown_content,
                "status_code": status_code,
                "content_length": len(markdown_content),
            }})
    except urllib.error.HTTPError as exc:
        _emit({{"error": f"Fetch URL error: HTTP {{exc.code}}: {{exc.reason}}", "url": url}})
    except urllib.error.URLError as exc:
        _emit({{"error": f"Fetch URL error: {{exc.reason}}", "url": url}})
    except Exception as exc:
        _emit({{"error": f"Fetch URL error: {{exc}}", "url": url}})
"""

        return f"""
import json
import urllib.error
import urllib.parse
import urllib.request

payload = payload if isinstance(payload, dict) else {{}}
url = str(payload.get("url") or "").strip()
method = str(payload.get("method") or "GET").upper()
headers = payload.get("headers")
if not isinstance(headers, dict):
    headers = {{}}
data = payload.get("data")
params = payload.get("params")
timeout = payload.get("timeout", 30)
if not isinstance(timeout, int) or timeout <= 0:
    timeout = 30

def _emit(data):
    print("{marker}" + json.dumps(data, ensure_ascii=False))

def _decode_content(raw, response_headers):
    content_type = response_headers.get("content-type", "") if isinstance(response_headers, dict) else ""
    charset = "utf-8"
    if "charset=" in content_type.lower():
        charset = content_type.lower().split("charset=", 1)[1].split(";", 1)[0].strip() or "utf-8"
    try:
        text = raw.decode(charset, errors="replace")
    except Exception:
        text = raw.decode("utf-8", errors="replace")
    return text

if not url:
    _emit({{
        "success": False,
        "status_code": 0,
        "headers": {{}},
        "content": "Request error: url is required",
        "url": url,
    }})
else:
    try:
        if isinstance(params, dict) and params:
            query = urllib.parse.urlencode(params, doseq=True)
            sep = "&" if "?" in url else "?"
            url = f"{{url}}{{sep}}{{query}}"

        body = None
        if isinstance(data, dict):
            body = json.dumps(data).encode("utf-8")
            if not any(k.lower() == "content-type" for k in headers):
                headers["Content-Type"] = "application/json"
        elif isinstance(data, str):
            body = data.encode("utf-8")

        req = urllib.request.Request(url, data=body, method=method, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            status_code = int(resp.getcode() or 200)
            final_url = str(resp.geturl())
            response_headers = dict(resp.headers.items())
            text = _decode_content(raw, response_headers)
            content = text
            try:
                content = json.loads(text)
            except Exception:
                pass
            _emit({{
                "success": status_code < 400,
                "status_code": status_code,
                "headers": response_headers,
                "content": content,
                "url": final_url,
            }})
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read()
        except Exception:
            raw = b""
        response_headers = dict(exc.headers.items()) if getattr(exc, "headers", None) else {{}}
        text = _decode_content(raw, response_headers) if raw else f"HTTP {{exc.code}}: {{exc.reason}}"
        content = text
        try:
            content = json.loads(text)
        except Exception:
            pass
        _emit({{
            "success": False,
            "status_code": int(exc.code or 0),
            "headers": response_headers,
            "content": content,
            "url": url,
        }})
    except urllib.error.URLError as exc:
        _emit({{
            "success": False,
            "status_code": 0,
            "headers": {{}},
            "content": f"Request error: {{exc.reason}}",
            "url": url,
        }})
    except Exception as exc:
        _emit({{
            "success": False,
            "status_code": 0,
            "headers": {{}},
            "content": f"Request error: {{exc}}",
            "url": url,
        }})
"""

    def _create_cli_agent_compat(
        self,
        *,
        model: Any,
        assistant_id: str,
        **kwargs: Any,
    ) -> tuple[Any, Any]:
        """Call create_cli_agent with version-compatible kwargs."""
        signature = inspect.signature(create_cli_agent)
        accepted = set(signature.parameters)
        filtered_kwargs = {key: value for key, value in kwargs.items() if key in accepted}
        dropped = sorted(set(kwargs) - set(filtered_kwargs))
        if dropped:
            logger.debug("Ignoring unsupported create_cli_agent kwargs: %s", ", ".join(dropped))
        return create_cli_agent(model, assistant_id, **filtered_kwargs)

    def _sync_environment(self, runtime_model: RuntimeModelConfig) -> None:
        provider_key_env = {
            "openai": "OPENAI_API_KEY",
            "azure_openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "google_genai": "GOOGLE_API_KEY",
            "google_vertexai": "GOOGLE_API_KEY",
            "nvidia": "NVIDIA_API_KEY",
        }

        api_key = runtime_model.provider_kwargs.get("api_key")
        env_key = provider_key_env.get(runtime_model.provider)
        if env_key and isinstance(api_key, str) and api_key.strip():
            os.environ[env_key] = api_key.strip()

        project = runtime_model.provider_kwargs.get("project")
        if runtime_model.provider == "google_vertexai" and isinstance(project, str):
            os.environ["GOOGLE_CLOUD_PROJECT"] = project

        if self.config.tavily_api_key:
            os.environ["TAVILY_API_KEY"] = self.config.tavily_api_key

    def _parse_message_chunk(
        self,
        data: Any,
        tool_states: dict[str, dict[str, Any]],
    ) -> tuple[list[str], list[dict[str, Any]], list[dict[str, Any]]]:
        if not isinstance(data, tuple) or len(data) != 2:  # noqa: PLR2004
            return [], [], []

        message_obj, metadata = data
        if isinstance(metadata, dict) and metadata.get("lc_source") == "summarization":
            return [], [], []

        if isinstance(message_obj, AIMessage):
            return self._parse_ai_message(message_obj, tool_states)

        if isinstance(message_obj, ToolMessage):
            event = self._build_tool_result_event(message_obj, tool_states)
            if event is None:
                return [], [], []
            return [], [], [event]

        return [], [], []

    def _parse_ai_message(
        self,
        message_obj: AIMessage,
        tool_states: dict[str, dict[str, Any]],
    ) -> tuple[list[str], list[dict[str, Any]], list[dict[str, Any]]]:
        texts: list[str] = []
        tool_calls: list[dict[str, Any]] = []

        raw_tool_calls = getattr(message_obj, "tool_calls", None)
        if (not isinstance(raw_tool_calls, list) or not raw_tool_calls) and isinstance(
            getattr(message_obj, "additional_kwargs", None),
            dict,
        ):
            maybe_tool_calls = message_obj.additional_kwargs.get("tool_calls")
            if isinstance(maybe_tool_calls, list):
                raw_tool_calls = maybe_tool_calls

        if isinstance(raw_tool_calls, list):
            for raw_tool_call in raw_tool_calls:
                event = self._ingest_tool_call_data(raw_tool_call, tool_states)
                if event is not None:
                    tool_calls.append(event)

        blocks = getattr(message_obj, "content_blocks", None)
        if not isinstance(blocks, list):
            content = getattr(message_obj, "content", None)
            if isinstance(content, str) and content:
                texts.append(content)
                return texts, tool_calls, []

            if isinstance(content, list):
                for item in content:
                    if isinstance(item, str) and item:
                        texts.append(item)
                        continue

                    if not isinstance(item, dict):
                        continue

                    text = item.get("text")
                    if isinstance(text, str) and text:
                        texts.append(text)
            return texts, tool_calls, []

        for block in blocks:
            if not isinstance(block, dict):
                continue

            block_type = block.get("type")
            if block_type == "text":
                text = block.get("text")
                if isinstance(text, str) and text:
                    texts.append(text)
                continue

            if block_type in {"tool_call", "tool_call_chunk"}:
                event = self._ingest_tool_call_block(block, tool_states)
                if event is not None:
                    tool_calls.append(event)

        return texts, tool_calls, []

    def _ingest_tool_call_block(
        self,
        block: dict[str, Any],
        tool_states: dict[str, dict[str, Any]],
    ) -> dict[str, Any] | None:
        raw_name = block.get("name")
        fallback_name = raw_name if isinstance(raw_name, str) and raw_name else "tool"

        chunk_id = block.get("id")
        chunk_index = block.get("index")
        if chunk_index is not None:
            state_key = f"idx:{chunk_index}"
        elif chunk_id is not None:
            state_key = str(chunk_id)
        else:
            state_key = fallback_name

        canonical_id = None
        if chunk_id is not None:
            normalized_id = str(chunk_id).strip()
            if normalized_id:
                canonical_id = normalized_id
        if canonical_id is not None and canonical_id in tool_states:
            entry = tool_states[canonical_id]
            tool_states[state_key] = entry
        else:
            entry = tool_states.setdefault(
                state_key,
                {
                    "name": None,
                    "args": {},
                    "args_buffer": "",
                    "last_signature": None,
                },
            )

        if isinstance(raw_name, str) and raw_name:
            entry["name"] = raw_name

        if canonical_id is not None:
            entry["tool_call_id"] = canonical_id
            # Ensure tool results (which use tool_call_id) can resolve this state.
            tool_states[canonical_id] = entry

        args_payload = self._extract_tool_args_payload(block)
        if isinstance(args_payload, dict):
            entry["args"] = args_payload
        elif isinstance(args_payload, str) and args_payload:
            entry["args_buffer"] = str(entry.get("args_buffer", "")) + args_payload
            parsed = self._parse_tool_args_json(entry["args_buffer"])
            if parsed is not None:
                entry["args"] = parsed

        resolved_name = entry.get("name")
        if not isinstance(resolved_name, str) or not resolved_name:
            # Wait for a later chunk carrying the tool name to avoid noisy "tool(...)" placeholders.
            return None

        tool_call_id = str(entry.get("tool_call_id") or state_key)
        event = self._build_tool_call_event(tool_call_id, entry)
        signature = (event.get("display", ""), event.get("command", ""), event.get("tool_call_id", ""))
        if signature == entry.get("last_signature"):
            return None

        entry["last_signature"] = signature
        return event

    def _build_debug_log_path(self, session_id: str) -> Path:
        safe_session_id = self._sanitize_log_path_segment(session_id)
        session_dir = self.repo_root / "logs" / safe_session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        return session_dir / f"stream-{timestamp}.ndjson"

    @staticmethod
    def _sanitize_log_path_segment(raw: str) -> str:
        cleaned = re.sub(r"[^0-9A-Za-z._-]+", "_", raw).strip("._")
        if not cleaned:
            return "session"
        return cleaned[:120]

    def _build_message_debug_payload(
        self,
        *,
        session_id: str,
        namespace: Any,
        data: Any,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "event": "message",
            "session_id": session_id,
            "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
            "namespace": list(namespace) if isinstance(namespace, tuple) else namespace,
        }

        if isinstance(data, tuple) and len(data) == 2:  # noqa: PLR2004
            message_obj, metadata = data
            payload["metadata"] = metadata if isinstance(metadata, dict) else str(metadata)
            payload["message"] = self._serialize_message_for_debug(message_obj)
            return payload

        payload["raw"] = data
        return payload

    @staticmethod
    def _serialize_message_for_debug(message_obj: Any) -> Any:
        if hasattr(message_obj, "model_dump"):
            try:
                dumped = message_obj.model_dump(mode="json")
            except TypeError:
                dumped = message_obj.model_dump()
            if isinstance(dumped, dict):
                return dumped

        return {
            "type": type(message_obj).__name__,
            "repr": repr(message_obj),
        }

    @staticmethod
    def _write_debug_log(debug_fp: Any, payload: dict[str, Any]) -> None:
        debug_fp.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
        debug_fp.flush()

    def _ingest_tool_call_data(
        self,
        raw_tool_call: Any,
        tool_states: dict[str, dict[str, Any]],
    ) -> dict[str, Any] | None:
        if not isinstance(raw_tool_call, dict):
            return None

        name = raw_tool_call.get("name")
        if (not isinstance(name, str) or not name) and isinstance(raw_tool_call.get("function"), dict):
            maybe_name = raw_tool_call["function"].get("name")
            if isinstance(maybe_name, str) and maybe_name:
                name = maybe_name
        if not isinstance(name, str) or not name:
            return None

        tool_call_id = str(raw_tool_call.get("id") or name)
        entry = tool_states.setdefault(
            tool_call_id,
            {
                "name": name,
                "args": {},
                "args_buffer": "",
                "last_signature": None,
                "tool_call_id": tool_call_id,
            },
        )
        entry["name"] = name
        entry["tool_call_id"] = tool_call_id

        args_payload = self._extract_tool_args_payload(raw_tool_call)
        if isinstance(args_payload, dict):
            entry["args"] = args_payload
        elif isinstance(args_payload, str) and args_payload:
            parsed = self._parse_tool_args_json(args_payload)
            if parsed is not None:
                entry["args"] = parsed

        event = self._build_tool_call_event(tool_call_id, entry)
        signature = (event.get("display", ""), event.get("command", ""), event.get("tool_call_id", ""))
        if signature == entry.get("last_signature"):
            return None

        entry["last_signature"] = signature
        return event

    def _build_tool_call_event(
        self,
        tool_call_id: str,
        entry: dict[str, Any],
    ) -> dict[str, Any]:
        name = str(entry.get("name", "tool"))
        args = entry.get("args")
        args_dict = args if isinstance(args, dict) else {}
        command = self._first_string_arg(args_dict, ["command", "cmd", "shell_command"])

        return {
            "tool_call_id": tool_call_id,
            "name": name,
            "args": args_dict,
            "command": command,
            "display": self._format_tool_call_display(name, args_dict),
        }

    def _build_tool_result_event(
        self,
        message_obj: ToolMessage,
        tool_states: dict[str, dict[str, Any]],
    ) -> dict[str, Any] | None:
        tool_call_id = str(getattr(message_obj, "tool_call_id", "") or "")
        state = tool_states.setdefault(tool_call_id, {})

        state_name = state.get("name")
        msg_name = getattr(message_obj, "name", "")
        name = str(msg_name or state_name or "tool")

        state_args = state.get("args")
        args_dict = state_args if isinstance(state_args, dict) else {}
        command = self._first_string_arg(args_dict, ["command", "cmd", "shell_command"])

        status = str(getattr(message_obj, "status", "success") or "success")
        output = self._stringify_tool_content(getattr(message_obj, "content", ""))
        display = self._format_tool_call_display(name, args_dict)
        parsed_exit_code = self._extract_exit_code_from_tool_output(output)
        if isinstance(parsed_exit_code, int) and parsed_exit_code != 0:
            status = "error"

        result_signature = (display, status, output)
        if result_signature == state.get("last_result_signature"):
            return None
        state["last_result_signature"] = result_signature

        return {
            "tool_call_id": tool_call_id,
            "name": name,
            "status": status,
            "output": output,
            "command": command,
            "display": display,
            "exit_code": parsed_exit_code,
        }

    @staticmethod
    def _extract_exit_code_from_tool_output(output: str) -> int | None:
        text = str(output or "")
        if not text:
            return None

        failed_match = re.search(r"Command failed with exit code\s+(-?\d+)", text, flags=re.IGNORECASE)
        if failed_match:
            try:
                return int(failed_match.group(1))
            except ValueError:
                return None

        generic_match = re.search(r"exit[_\s-]*code\s*=?\s*(-?\d+)", text, flags=re.IGNORECASE)
        if generic_match:
            try:
                return int(generic_match.group(1))
            except ValueError:
                return None

        return None

    @staticmethod
    def _parse_tool_args_json(raw_args: str) -> dict[str, Any] | None:
        stripped = raw_args.strip()
        if not stripped:
            return None

        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            try:
                parsed = ast.literal_eval(stripped)
            except (ValueError, SyntaxError):
                return None

        if isinstance(parsed, dict):
            return parsed
        return None

    @staticmethod
    def _extract_tool_args_payload(raw: dict[str, Any]) -> Any:
        # Primary shapes emitted by LangChain/LangGraph providers.
        for key in ("args", "input", "arguments"):
            if key in raw and raw.get(key) is not None:
                return raw.get(key)

        # OpenAI-style nested payload: {"function": {"name": "...", "arguments": "..."}}
        nested = raw.get("function")
        if isinstance(nested, dict):
            for key in ("args", "input", "arguments"):
                if key in nested and nested.get(key) is not None:
                    return nested.get(key)
        return None

    @staticmethod
    def _first_string_arg(args: dict[str, Any], keys: list[str]) -> str | None:
        for key in keys:
            value = args.get(key)
            if isinstance(value, str) and value:
                return value
        return None

    @staticmethod
    def _stringify_tool_content(content: Any) -> str:
        if isinstance(content, str):
            return content

        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                    continue

                if isinstance(item, dict):
                    if item.get("type") == "text" and isinstance(item.get("text"), str):
                        parts.append(item["text"])
                    else:
                        parts.append(json.dumps(item, ensure_ascii=False))
                    continue

                parts.append(str(item))
            return "\n".join(part for part in parts if part)

        if isinstance(content, dict):
            return json.dumps(content, ensure_ascii=False, indent=2)

        return str(content)

    @staticmethod
    def _format_tool_call_display(name: str, args: dict[str, Any]) -> str:
        # Built-in tools: mirror CLI's key argument display so users can immediately
        # understand what the tool is operating on.
        if name in {"read_file", "write_file", "edit_file", "ls"}:
            path = WebAgentRuntime._first_string_arg(
                args,
                ["path", "file_path", "filepath", "target_file", "directory", "dir"],
            )
            if path:
                return f'read_file(path="{WebAgentRuntime._truncate(path, 180)}")' if name == "read_file" else (
                    f'{name}(path="{WebAgentRuntime._truncate(path, 180)}")'
                )
            return f"{name}()"

        if name in {"glob", "grep"}:
            pattern = WebAgentRuntime._first_string_arg(
                args,
                ["pattern", "query", "regex", "search"],
            )
            if pattern:
                return f'{name}(pattern="{WebAgentRuntime._truncate(pattern, 160)}")'
            if args:
                return f"{name}({WebAgentRuntime._format_key_value_args(args)})"
            return f"{name}()"

        if name == "execute":
            command = WebAgentRuntime._first_string_arg(args, ["command", "cmd", "shell_command"])
            timeout = args.get("timeout")
            if isinstance(command, str) and command:
                escaped = command.replace('"', '\\"')
                if isinstance(timeout, int):
                    return f'execute("{escaped}", timeout={timeout}s)'
                return f'execute("{escaped}")'
            return "execute(...)"

        if name == "http_request":
            method = WebAgentRuntime._first_string_arg(args, ["method"])
            url = WebAgentRuntime._first_string_arg(args, ["url", "uri"])
            if method and url:
                return f'http_request(method="{method.upper()}", url="{WebAgentRuntime._truncate(url, 180)}")'
            if url:
                return f'http_request(url="{WebAgentRuntime._truncate(url, 180)}")'
            if args:
                return f"http_request({WebAgentRuntime._format_key_value_args(args)})"
            return "http_request()"

        if name == "fetch_url":
            url = WebAgentRuntime._first_string_arg(args, ["url", "uri"])
            if url:
                return f'fetch_url(url="{WebAgentRuntime._truncate(url, 180)}")'
            if args:
                return f"fetch_url({WebAgentRuntime._format_key_value_args(args)})"
            return "fetch_url()"

        if name in {"skill_create", "skill_validate", "skill_package"}:
            skill_name = WebAgentRuntime._first_string_arg(args, ["name", "skill_name"])
            if skill_name:
                return f'{name}(name="{WebAgentRuntime._truncate(skill_name, 80)}")'
            if args:
                return f"{name}({WebAgentRuntime._format_key_value_args(args)})"
            return f"{name}()"

        if name == "skill_update_files":
            skill_name = WebAgentRuntime._first_string_arg(args, ["name", "skill_name"])
            files = args.get("files")
            file_count = len(files) if isinstance(files, list) else None
            if skill_name and isinstance(file_count, int):
                return f'skill_update_files(name="{WebAgentRuntime._truncate(skill_name, 80)}", files={file_count})'
            if args:
                return f"skill_update_files({WebAgentRuntime._format_key_value_args(args)})"
            return "skill_update_files()"

        if name == "skill_list":
            include_user = args.get("include_user")
            include_builtin = args.get("include_builtin")
            if include_user is None and include_builtin is None:
                return "skill_list()"
            return (
                f"skill_list(include_user={bool(include_user)}, "
                f"include_builtin={bool(include_builtin)})"
            )

        if name == "scheduled_task_create":
            task_name = WebAgentRuntime._first_string_arg(args, ["name"])
            schedule_type = WebAgentRuntime._first_string_arg(args, ["schedule_type"])
            task_type = WebAgentRuntime._first_string_arg(args, ["task_type"])
            skill_name = WebAgentRuntime._first_string_arg(args, ["skill_name"])
            if task_name and task_type == "skill_task" and skill_name:
                return (
                    f'scheduled_task_create(name="{WebAgentRuntime._truncate(task_name, 80)}", '
                    f'skill_name="{WebAgentRuntime._truncate(skill_name, 80)}")'
                )
            if task_name and schedule_type:
                return (
                    f'scheduled_task_create(name="{WebAgentRuntime._truncate(task_name, 80)}", '
                    f'schedule_type="{schedule_type}")'
                )
            if task_name:
                return f'scheduled_task_create(name="{WebAgentRuntime._truncate(task_name, 80)}")'
            return "scheduled_task_create(...)"

        if name in {"scheduled_task_update", "scheduled_task_delete", "scheduled_task_list_runs"}:
            task_id = WebAgentRuntime._first_string_arg(args, ["task_id", "id"])
            if task_id:
                return f'{name}(task_id="{WebAgentRuntime._truncate(task_id, 80)}")'
            return f"{name}(...)"

        if name in {"scheduled_task_enable", "scheduled_task_disable", "scheduled_task_execute"}:
            task_id = WebAgentRuntime._first_string_arg(args, ["task_id", "id"])
            if task_id:
                return f'{name}(task_id="{WebAgentRuntime._truncate(task_id, 80)}")'
            return f"{name}(...)"

        if name == "scheduled_task_list":
            include_disabled = args.get("include_disabled")
            limit = args.get("limit")
            if include_disabled is None and limit is None:
                return "scheduled_task_list()"
            return (
                "scheduled_task_list("
                f"limit={int(limit) if isinstance(limit, int) else 50}, "
                f"include_disabled={bool(include_disabled) if include_disabled is not None else True}"
                ")"
            )

        if not args:
            return f"{name}()"

        return f"{name}({WebAgentRuntime._format_key_value_args(args)})"

    @staticmethod
    def _format_key_value_args(args: dict[str, Any]) -> str:
        parts: list[str] = []
        for key, value in args.items():
            value_preview = WebAgentRuntime._format_tool_arg_value(value)
            parts.append(f"{key}={value_preview}")

        args_preview = ", ".join(parts)
        if len(args_preview) > 220:
            args_preview = args_preview[:220] + "..."
        return args_preview

    @staticmethod
    def _format_tool_arg_value(value: Any) -> str:
        if isinstance(value, str):
            escaped = value.replace("\\", "\\\\").replace('"', '\\"')
            if len(escaped) > 120:
                escaped = escaped[:120] + "..."
            return f'"{escaped}"'

        if isinstance(value, (int, float, bool)) or value is None:
            return str(value)

        serialized = json.dumps(value, ensure_ascii=False)
        if len(serialized) > 120:
            serialized = serialized[:120] + "..."
        return serialized

    @staticmethod
    def _truncate(text: str, max_len: int) -> str:
        if len(text) <= max_len:
            return text
        return text[:max_len] + "..."

    def _prompt_fingerprint(self) -> str:
        prompt_cfg = self.config.prompt_config
        if not prompt_cfg.enabled or prompt_cfg.directory is None:
            return "disabled"

        payload = {
            "dir": str(prompt_cfg.directory.resolve()),
            "mode": prompt_cfg.system_mode,
            "system_file": prompt_cfg.system_file,
            "behavior_file": prompt_cfg.behavior_file,
            "system": self._read_prompt_file(prompt_cfg.directory, prompt_cfg.system_file) or "",
            "behavior": self._read_prompt_file(prompt_cfg.directory, prompt_cfg.behavior_file) or "",
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()[:16]

    def _load_system_prompt(
        self,
        *,
        assistant_id: str,
        interactive: bool,
        cwd: Path,
    ) -> str | None:
        prompt_cfg = self.config.prompt_config
        if not prompt_cfg.enabled or prompt_cfg.directory is None:
            return None

        prompt_dir = prompt_cfg.directory
        if not prompt_dir.exists() or not prompt_dir.is_dir():
            logger.warning("Prompt directory is not available: %s", prompt_dir)
            return None

        default_prompt = get_system_prompt(
            assistant_id=assistant_id,
            interactive=interactive,
            cwd=cwd,
        )

        system_text = self._read_prompt_file(prompt_dir, prompt_cfg.system_file)
        behavior_text = self._read_prompt_file(prompt_dir, prompt_cfg.behavior_file)

        if prompt_cfg.system_mode == "override":
            if system_text:
                composed = system_text
            else:
                logger.warning(
                    "Prompt system_mode=override but system file is missing/empty (%s/%s); fallback to default",
                    prompt_dir,
                    prompt_cfg.system_file,
                )
                composed = default_prompt
        else:
            composed_parts: list[str] = [default_prompt]
            if system_text:
                composed_parts.append(system_text)
            composed = "\n\n".join(part.strip() for part in composed_parts if part and part.strip())

        if behavior_text:
            if composed.strip():
                composed = f"{composed.rstrip()}\n\n{behavior_text.strip()}"
            else:
                composed = behavior_text.strip()

        return composed if composed.strip() else None

    @staticmethod
    def _read_prompt_file(prompt_dir: Path, file_name: str) -> str | None:
        safe_name = file_name.strip()
        if not safe_name:
            return None

        root = prompt_dir.resolve()
        candidate = (root / safe_name).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            logger.warning("Prompt file is outside prompt directory and will be ignored: %s", file_name)
            return None

        if not candidate.exists() or not candidate.is_file():
            return None

        content = candidate.read_text(encoding="utf-8").strip()
        return content or None
