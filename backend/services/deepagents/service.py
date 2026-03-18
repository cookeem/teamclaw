from __future__ import annotations

import json
import logging
from pathlib import Path
import re
import shlex
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

import docker
from deepagents import create_deep_agent
from langchain.chat_models import init_chat_model
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.memory import MemorySaver
from tavily import TavilyClient

from backend.core.config import get_settings
from backend.services.deepagents.conversation_runtime import ConversationRuntimeMixin, _active_conversation_id
from backend.services.deepagents.docker_manager import DockerExecutionManager
from backend.services.deepagents.skills_loader import SkillsMixin, TeamClawFilesystemBackend

logger = logging.getLogger(__name__)


class DeepAgentService(SkillsMixin, ConversationRuntimeMixin):
    def __init__(self) -> None:
        self._agents: dict[str, Any] = {}
        self._model_label = None
        self._pending_interrupts: dict[str, dict[str, Any]] = {}
        self._conversation_pending_interrupt: dict[str, str] = {}
        self._resolved_interrupts: set[str] = set()
        self._allow_all_conversations: set[str] = set()
        self._docker_manager: DockerExecutionManager | None = None
        self._tavily_client: TavilyClient | None = None
        self._workspace_root = (Path.cwd() / "workspaces").resolve()
        self._docker_workdir = "/workspace"
        self._conversation_daemons: dict[str, dict[str, Any]] = {}
        self._conversation_skill_paths: dict[str, list[str]] = {}
        self._conversation_skill_tool_names: dict[str, list[str]] = {}
        self._conversation_agent_skills_dir: dict[str, Path] = {}
        self._conversation_user_id: dict[str, str] = {}

    def _resolve_daemon_host(self, conversation_id: str) -> str:
        settings = get_settings()
        docker_cfg = (settings.model_extra or {}).get("docker", {}) or {}
        daemon_cfg = self._resolve_conversation_daemon(conversation_id) or {}
        daemon_host = str(
            daemon_cfg.get("host")
            or daemon_cfg.get("daemon_host")
            or daemon_cfg.get("docker_host")
            or ""
        ).strip()
        if not daemon_host:
            daemon_host = str(docker_cfg.get("daemon_host") or docker_cfg.get("docker_host") or "").strip()
        return daemon_host

    def _create_docker_client(
        self,
        docker_cfg: dict[str, Any],
        daemon_cfg: dict[str, Any] | None = None,
    ) -> docker.DockerClient:
        cfg = dict(docker_cfg or {})
        if isinstance(daemon_cfg, dict):
            cfg.update(daemon_cfg)
        daemon_host = str(cfg.get("host") or cfg.get("daemon_host") or cfg.get("docker_host") or "").strip()
        client_timeout = int(cfg.get("client_timeout", 10))
        if not daemon_host:
            return docker.from_env(timeout=client_timeout)

        tls_cfg = cfg.get("tls", {}) or {}
        tls_enabled = bool(tls_cfg.get("enabled", False))
        tls_obj = None
        if tls_enabled:
            certs_dir = Path(str(tls_cfg.get("certs_dir", "./certs/client"))).expanduser()
            if not certs_dir.is_absolute():
                certs_dir = (Path.cwd() / certs_dir).resolve()
            ca_cert = str(tls_cfg.get("ca_cert") or (certs_dir / "ca.pem"))
            client_cert = str(tls_cfg.get("client_cert") or (certs_dir / "cert.pem"))
            client_key = str(tls_cfg.get("client_key") or (certs_dir / "key.pem"))
            verify = bool(tls_cfg.get("verify", True))
            tls_obj = docker.tls.TLSConfig(
                client_cert=(client_cert, client_key),
                ca_cert=ca_cert,
                verify=verify,
            )
        return docker.DockerClient(base_url=daemon_host, tls=tls_obj, timeout=client_timeout)

    def cleanup_orphan_containers(self) -> list[dict[str, Any]]:
        settings = get_settings()
        docker_cfg = (settings.model_extra or {}).get("docker", {}) or {}
        prefix = str(docker_cfg.get("container_name_prefix") or "teamclaw-agent")
        hosts_cfg = docker_cfg.get("daemon_hosts")
        daemon_entries: list[dict[str, Any]] = []
        if isinstance(hosts_cfg, list):
            for entry in hosts_cfg:
                if isinstance(entry, dict) and entry.get("host"):
                    daemon_entries.append(entry)
        else:
            daemon_host = docker_cfg.get("daemon_host") or docker_cfg.get("docker_host")
            if daemon_host:
                daemon_entries.append({"host": daemon_host})
        if not daemon_entries:
            daemon_entries = [{}]

        removed: list[dict[str, Any]] = []
        for daemon_cfg in daemon_entries:
            try:
                client = self._create_docker_client(docker_cfg, daemon_cfg)
                containers = client.containers.list(all=True, filters={"label": "teamclaw.managed=true"})
                for container in containers:
                    name = getattr(container, "name", "") or ""
                    if prefix and not name.startswith(prefix):
                        continue
                    try:
                        container.remove(force=True)
                        removed.append(
                            {
                                "daemon": daemon_cfg.get("host") or "local",
                                "container": name,
                            }
                        )
                    except Exception:
                        continue
            except Exception:
                continue

        return removed

    def _agent_skills_mount_source(self, conversation_id: str, user_id: str, agent_user_dir: Path) -> str:
        daemon_host = self._resolve_daemon_host(conversation_id)
        if daemon_host:
            return f"/agent_skills/{user_id}"
        return str(agent_user_dir)

    def _terminal_tool(self, commands: Any) -> str:
        if isinstance(commands, str):
            try:
                parsed = json.loads(commands)
                commands = parsed if isinstance(parsed, list) else [commands]
            except json.JSONDecodeError:
                commands = [commands]
        elif not isinstance(commands, list):
            return f"Error: Invalid commands format: {type(commands)}"

        conversation_id = _active_conversation_id.get() or "global"
        outputs: list[str] = []

        for cmd in commands:
            cmd_text = str(cmd).strip().strip("[]").strip("\"").strip("'")
            if not cmd_text:
                continue
            allowed, reason = self._validate_terminal_command(cmd_text)
            if not allowed:
                outputs.append(f"Error: command rejected by workspace policy. {reason}")
                continue
            if self._docker_manager is None:
                outputs.append("Error: Docker manager not initialized")
                continue
            outputs.append(self._docker_manager.execute(conversation_id, cmd_text))

        return "\n".join(outputs).strip()

    def _validate_terminal_command(self, command: str) -> tuple[bool, str]:
        cmd = (command or "").strip()
        if not cmd:
            return (False, "empty command")
        return (True, "")

    def _internet_search_tool(
        self,
        query: str,
        max_results: int = 5,
        topic: str = "general",
        include_raw_content: bool = False,
    ) -> str:
        if self._tavily_client is None:
            return "Error: Tavily client is not initialized. Please set api_keys.tavily in config.yaml."
        if not query or not str(query).strip():
            return "Error: query is required."
        normalized_topic = str(topic or "general").lower()
        if normalized_topic not in {"general", "news", "finance"}:
            normalized_topic = "general"
        try:
            result = self._tavily_client.search(
                str(query),
                max_results=max(1, min(int(max_results or 5), 10)),
                include_raw_content=bool(include_raw_content),
                topic=normalized_topic,
            )
            return json.dumps(result, ensure_ascii=False)
        except Exception as exc:  # noqa: BLE001
            return f"Error running internet_search: {exc}"

    def _web_search_tool(
        self,
        query: str,
        max_results: int = 5,
        topic: str = "general",
        include_raw_content: bool = False,
    ) -> str:
        return self._internet_search_tool(
            query=query,
            max_results=max_results,
            topic=topic,
            include_raw_content=include_raw_content,
        )

    def _fetch_url_tool(
        self,
        url: str,
        max_bytes: int = 200_000,
        timeout_seconds: int = 15,
        user_agent: str | None = None,
    ) -> str:
        if not url or not str(url).strip():
            return "Error: url is required."
        parsed = urllib.parse.urlparse(str(url).strip())
        if parsed.scheme not in {"http", "https"}:
            return "Error: only http/https URLs are allowed."

        try:
            max_bytes_int = int(max_bytes)
        except Exception:
            max_bytes_int = 200_000
        max_bytes_int = max(1, min(max_bytes_int, 2_000_000))

        try:
            timeout_val = max(1, int(timeout_seconds))
        except Exception:
            timeout_val = 15

        headers = {
            "User-Agent": user_agent or "teamclaw-fetch/1.0",
            "Accept": "*/*",
        }

        try:
            req = urllib.request.Request(str(url).strip(), headers=headers)
            with urllib.request.urlopen(req, timeout=timeout_val) as resp:
                status = getattr(resp, "status", None) or resp.getcode()
                raw_headers = dict(resp.headers.items())
                body = resp.read(max_bytes_int + 1)
                truncated = len(body) > max_bytes_int
                if truncated:
                    body = body[:max_bytes_int]
                text = body.decode("utf-8", errors="replace")
                payload = {
                    "url": str(url).strip(),
                    "status": status,
                    "headers": raw_headers,
                    "truncated": truncated,
                    "content": text,
                }
                return json.dumps(payload, ensure_ascii=False)
        except urllib.error.HTTPError as exc:
            return f"Error fetching url: HTTP {exc.code}"
        except urllib.error.URLError as exc:
            return f"Error fetching url: {exc.reason}"
        except Exception as exc:  # noqa: BLE001
            return f"Error fetching url: {exc}"

    def _build_agent_for_conversation(self, conversation_id: str) -> Any:
        settings = get_settings()
        extra = settings.model_extra or {}

        providers: dict[str, Any] = extra.get("models", {}).get("providers", {})
        if not providers:
            raise RuntimeError("models.providers missing in config.yaml")

        provider_name = next(iter(providers.keys()))
        provider_cfg = providers[provider_name]
        model_name = provider_cfg["models"][0]
        params = provider_cfg.get("params", {}) or {}
        generic_params = {k: v for k, v in params.items() if not isinstance(v, dict)}
        model_params = params.get(model_name, {}) if isinstance(params.get(model_name, {}), dict) else {}
        effective_model_params = {**generic_params, **model_params}
        effective_model_params.setdefault("streaming", True)

        llm = init_chat_model(
            f"{provider_name}:{model_name}",
            base_url=provider_cfg.get("base_url"),
            api_key=provider_cfg.get("api_key"),
            **effective_model_params,
        )

        docker_cfg = extra.get("docker", {}) or {}
        self._workspace_root = self._resolve_workspace_root(docker_cfg.get("workspace_root"), self._workspace_root)
        self._docker_workdir = str(docker_cfg.get("workdir", "/workspace"))
        self._docker_manager = DockerExecutionManager(docker_cfg, self._resolve_conversation_daemon)
        user_id = self._conversation_user_id.get(conversation_id)
        agent_dir = self._conversation_agent_skills_dir.get(conversation_id)
        agent_mounts: list[str] = []
        if user_id and agent_dir and self._docker_manager:
            mount_source = self._agent_skills_mount_source(conversation_id, user_id, agent_dir)
            agent_mounts.append(f"{mount_source}:{self._docker_workdir.rstrip('/')}/agent_skills:rw")
        tavily_api_key = (extra.get("api_keys", {}) or {}).get("tavily")
        if tavily_api_key:
            self._tavily_client = TavilyClient(api_key=tavily_api_key)
        else:
            self._tavily_client = None

        shell_tool = StructuredTool.from_function(
            func=self._terminal_tool,
            name="terminal",
            description="Run shell commands. Commands can be a string or a JSON array of strings.",
        )
        web_search_tool = StructuredTool.from_function(
            func=self._web_search_tool,
            name="web_search",
            description="Run web search via Tavily. Inputs: query, max_results, topic(general/news/finance), include_raw_content.",
        )
        fetch_url_tool = StructuredTool.from_function(
            func=self._fetch_url_tool,
            name="fetch_url",
            description="Fetch a URL via HTTP GET. Inputs: url, max_bytes=200000, timeout_seconds=15, user_agent(optional).",
        )
        internet_search_tool = StructuredTool.from_function(
            func=self._internet_search_tool,
            name="internet_search",
            description="Run web search via Tavily. Inputs: query, max_results, topic(general/news/finance), include_raw_content.",
        )
        # Important: do NOT register host filesystem tools from langchain_community here.
        # Deepagents already provides filesystem tools via FilesystemMiddleware(backend=...),
        # which we sandbox to per-conversation workspace.
        tools = [shell_tool, web_search_tool, fetch_url_tool, internet_search_tool]
        tool_name_set = {tool.name for tool in tools if getattr(tool, "name", None)}

        interrupt_config = {
            "terminal": True,
            "ls": True,
            "read_file": True,
            "write_file": True,
            "edit_file": True,
        }

        agent_interrupt = extra.get("agent", {}).get("interrupt_on", {})
        for k, v in agent_interrupt.items():
            interrupt_config[k] = bool(v)

        for tool in tools:
            tool_name = getattr(tool, "name", None)
            if tool_name:
                interrupt_config[tool_name] = True

        def backend_factory(_runtime: Any) -> TeamClawFilesystemBackend:
            conversation_id = _active_conversation_id.get() or "global"
            conversation_workspace = self._conversation_workspace(conversation_id)
            agent_skills_dir = self._conversation_agent_skills_dir.get(conversation_id)
            readonly_skills_dir = (conversation_workspace / "skills").resolve()
            builtin_skills_dir = self._resolve_builtin_skills_dir(skills_cfg)
            return TeamClawFilesystemBackend(
                repo_root=Path.cwd(),
                workspace_dir=conversation_workspace,
                workdir_alias=self._docker_workdir,
                agent_skills_dir=agent_skills_dir,
                readonly_skills_dir=readonly_skills_dir,
                builtin_skills_dir=builtin_skills_dir,
            )

        workspace_prefix = "/" + str(self._docker_workdir).strip("/")
        skills_cfg = extra.get("skills", {})
        skill_paths, skill_tool_names, skills_mounts = self._collect_builtin_skills(
            conversation_id,
            skills_cfg,
            workspace_prefix,
        )

        conversation_skill_paths = self._conversation_skill_paths.get(conversation_id, [])
        conversation_skill_tool_names = self._conversation_skill_tool_names.get(conversation_id, [])
        if conversation_skill_paths:
            seen: set[str] = set()
            merged: list[str] = []
            for path in [*skill_paths, *conversation_skill_paths]:
                if path not in seen:
                    seen.add(path)
                    merged.append(path)
            skill_paths = merged
        if conversation_skill_tool_names:
            skill_tool_names.extend(conversation_skill_tool_names)
        skill_paths = [p for p in skill_paths if p.startswith(workspace_prefix)]

        builtin_dir = self._resolve_builtin_skills_dir(skills_cfg)
        skill_tools: list[StructuredTool] = []
        for skill_name in skill_tool_names:
            if not re.match(r"^[A-Za-z0-9_-]+$", skill_name):
                continue
            if skill_name in tool_name_set:
                continue
            doc_path = self._resolve_skill_doc_path(conversation_id, skill_name, builtin_dir)
            if not doc_path:
                continue

            def _skill_tool(offset: int = 0, limit: int = 2000, _p: Path = doc_path) -> str:
                return self._read_skill_doc(_p, offset=offset, limit=limit)

            skill_tools.append(
                StructuredTool.from_function(
                    func=_skill_tool,
                    name=skill_name,
                    description=(
                        f"Load SKILL.md for '{skill_name}'. Optional args: offset (line offset), limit (max lines)."
                    ),
                )
            )
            tool_name_set.add(skill_name)

        if skill_tools:
            tools.extend(skill_tools)

        for skill_name in skill_tool_names:
            interrupt_config[skill_name] = True

        if self._docker_manager:
            volumes: list[str] = []
            for entry in [*agent_mounts, *skills_mounts]:
                if entry not in volumes:
                    volumes.append(entry)
            if volumes:
                self._docker_manager.set_conversation_volumes(conversation_id, volumes)

        workspace_prompt = (
            "Workspace policy (hard requirement):\n"
            "1) File operations may access any path inside the container.\n"
            "2) /workspace/skills and /workspace/skills-builtin are read-only; do not attempt to modify them.\n"
            "3) If you need to create or edit skills, write them under /workspace/agent_skills instead.\n"
            "   - When asked to create a new skill, create it directly under /workspace/agent_skills/<skill_name>/.\n"
            "   - Do not list or inspect /workspace/skills unless the user explicitly asks for published skills or all available skills.\n"
            "   - /workspace/skills-builtin contains built-in skills; when listing available skills, list it first, then /workspace/skills.\n"
            "   - When you decide to use a skill, call the tool with the same name to load its SKILL.md.\n"
            "   - You may execute skills under /workspace/agent_skills for testing purposes.\n"
            "4) Prefer explicit paths when operating outside /workspace.\n"
            "5) If file is not found, report missing file and ask user; do not probe unrelated directories."
        )
        agent = create_deep_agent(
            tools=tools,
            model=llm,
            system_prompt=workspace_prompt,
            checkpointer=MemorySaver(),
            backend=backend_factory,
            skills=skill_paths,
            interrupt_on=interrupt_config,
        )
        self._model_label = f"{provider_name}:{model_name}"
        return agent

    def _get_agent(self, conversation_id: str) -> Any:
        if conversation_id not in self._agents:
            self._ensure_conversation_skills(conversation_id)
            self._agents[conversation_id] = self._build_agent_for_conversation(conversation_id)
        return self._agents[conversation_id]

    def ensure_conversation_ready(self, conversation_id: str) -> dict[str, Any]:
        self._get_agent(conversation_id)
        if self._docker_manager is None:
            return {
                "enabled": False,
                "container_name": None,
                "image": None,
                "workdir": None,
                "host_workspace_dir": None,
                "init_error": "Docker manager not initialized",
            }
        return self._docker_manager.status(conversation_id)

    def _resolve_conversation_daemon(self, conversation_id: str) -> dict[str, Any] | None:
        return self._conversation_daemons.get(conversation_id)

    def set_conversation_daemon(self, conversation_id: str, daemon_cfg: dict[str, Any] | None) -> None:
        if not conversation_id:
            return
        if daemon_cfg is None:
            self._conversation_daemons.pop(conversation_id, None)
            return
        if isinstance(daemon_cfg, dict):
            self._conversation_daemons[conversation_id] = daemon_cfg

    def cleanup_all(self) -> None:
        if self._docker_manager is None:
            return
        self._agents.clear()
        self._conversation_skill_paths.clear()
        self._conversation_skill_tool_names.clear()
        self._conversation_agent_skills_dir.clear()
        self._conversation_user_id.clear()
        self._docker_manager.cleanup_all()

    def cleanup_conversation(self, conversation_id: str) -> None:
        if not conversation_id or self._docker_manager is None:
            return
        self._agents.pop(conversation_id, None)
        self._conversation_skill_paths.pop(conversation_id, None)
        self._conversation_skill_tool_names.pop(conversation_id, None)
        self._conversation_agent_skills_dir.pop(conversation_id, None)
        self._conversation_user_id.pop(conversation_id, None)
        self._docker_manager.clear_conversation_volumes(conversation_id)
        self._docker_manager.cleanup_conversation(conversation_id)

    def debug_exec(self, conversation_id: str, command: str) -> dict[str, Any]:
        self._get_agent(conversation_id)
        if self._docker_manager is None:
            raise RuntimeError("Docker manager not initialized")

        output = self._docker_manager.execute(conversation_id, command)
        status = self._docker_manager.status(conversation_id)
        if status.get("enabled") is False and status.get("init_error"):
            output = f"{output}\nDocker init error: {status['init_error']}".strip()
        return {
            "conversation_id": conversation_id,
            "command": command,
            "docker": status,
            "output": output,
        }


deepagent_service = DeepAgentService()
