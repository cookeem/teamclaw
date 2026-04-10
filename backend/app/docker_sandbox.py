from __future__ import annotations

import base64
import hashlib
import io
import logging
import posixpath
import secrets
import tarfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import docker
from docker.errors import APIError, DockerException, ImageNotFound, NotFound

from deepagents.backends.protocol import (
    EditResult,
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
    WriteResult,
)
from deepagents.backends.sandbox import BaseSandbox

from app.config_loader import DockerDaemonHostConfig, DockerSandboxConfig

logger = logging.getLogger(__name__)


def _sanitize_segment(raw: str, *, max_len: int = 48) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "-" for ch in raw)
    cleaned = cleaned.strip("-._")
    if not cleaned:
        cleaned = "session"
    return cleaned[:max_len]


def _map_exception_to_file_error(exc: Exception) -> str:
    if isinstance(exc, FileNotFoundError):
        return "file_not_found"
    if isinstance(exc, PermissionError):
        return "permission_denied"
    if isinstance(exc, IsADirectoryError):
        return "is_directory"
    if isinstance(exc, (NotADirectoryError, FileExistsError, ValueError)):
        return "invalid_path"
    return "permission_denied"


class DockerSandbox(BaseSandbox):
    """DeepAgents sandbox backend implemented with a persistent Docker container."""

    def __init__(
        self,
        *,
        client: docker.DockerClient,
        container: Any,
        default_timeout: int,
        workdir: str = "/workspace",
        max_output_bytes: int = 100_000,
    ) -> None:
        self._client = client
        self._container = container
        self._default_timeout = default_timeout
        self._max_output_bytes = max_output_bytes
        self._workdir = self._normalize_path(workdir if isinstance(workdir, str) and workdir.strip() else "/workspace")

    @property
    def id(self) -> str:
        return str(getattr(self._container, "id", "unknown"))

    @property
    def container_name(self) -> str:
        return str(getattr(self._container, "name", "unknown"))

    def execute(
        self,
        command: str,
        *,
        timeout: int | None = None,
    ) -> ExecuteResponse:
        if not isinstance(command, str) or not command.strip():
            return ExecuteResponse(
                output="Error: Command must be a non-empty string.",
                exit_code=1,
                truncated=False,
            )

        effective_timeout = timeout if timeout is not None else self._default_timeout
        if effective_timeout <= 0:
            msg = f"timeout must be positive, got {effective_timeout}"
            raise ValueError(msg)

        try:
            result = self._container.exec_run(
                cmd=["/bin/sh", "-lc", command],
                stdout=True,
                stderr=True,
                demux=True,
            )
        except DockerException as exc:
            logger.exception("Docker exec failed")
            return ExecuteResponse(
                output=f"Error: docker exec failed: {exc}",
                exit_code=1,
                truncated=False,
            )

        output = self._decode_exec_output(result.output)
        truncated = False
        if len(output.encode("utf-8", errors="ignore")) > self._max_output_bytes:
            output = output[: self._max_output_bytes]
            output += f"\n\n... Output truncated at {self._max_output_bytes} bytes."
            truncated = True

        if not output:
            output = "<no output>"

        exit_code = None
        if isinstance(result.exit_code, int):
            exit_code = result.exit_code

        return ExecuteResponse(output=output, exit_code=exit_code, truncated=truncated)

    def read(
        self,
        file_path: str,
        offset: int = 0,
        limit: int = 2000,
    ) -> str:
        """Read with path-tolerance for malformed tool-call arguments."""
        tried: list[str] = []
        for candidate in self._candidate_read_paths(file_path):
            resolved_candidate = self._resolve_default_file_path(candidate)
            if resolved_candidate in tried:
                continue
            tried.append(resolved_candidate)
            result = super().read(resolved_candidate, offset=offset, limit=limit)
            if not self._is_not_found_result(result):
                return result
            if self._path_exists(resolved_candidate):
                return self._build_unreadable_file_message(resolved_candidate)

        resolved = self._resolve_upload_alias_path(file_path)
        if resolved and resolved not in tried:
            result = super().read(resolved, offset=offset, limit=limit)
            if not self._is_not_found_result(result):
                return result
            if self._path_exists(resolved):
                return self._build_unreadable_file_message(resolved)

        fallback_target = self._resolve_default_file_path(file_path)
        fallback = super().read(fallback_target, offset=offset, limit=limit)
        if self._is_not_found_result(fallback) and self._path_exists(fallback_target):
            return self._build_unreadable_file_message(fallback_target)
        return fallback

    def write(
        self,
        file_path: str,
        content: str,
    ) -> WriteResult:
        normalized_path = self._resolve_default_file_path(file_path)
        return super().write(normalized_path, content)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        normalized_path = self._resolve_default_file_path(file_path)
        return super().edit(normalized_path, old_string, new_string, replace_all=replace_all)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        responses: list[FileUploadResponse] = []
        for raw_path, content in files:
            normalized_path = self._normalize_path(raw_path)
            parent_dir = posixpath.dirname(normalized_path) or "/"
            file_name = posixpath.basename(normalized_path)
            if not file_name:
                responses.append(
                    FileUploadResponse(path=normalized_path, error="invalid_path")
                )
                continue

            mkdir_result = self.execute(f"mkdir -p {self._sh_quote(parent_dir)}")
            if mkdir_result.exit_code not in {0, None}:
                responses.append(
                    FileUploadResponse(path=normalized_path, error="permission_denied")
                )
                continue

            archive = io.BytesIO()
            with tarfile.open(fileobj=archive, mode="w") as tar:
                info = tarfile.TarInfo(name=file_name)
                info.size = len(content)
                info.mode = 0o644
                tar.addfile(info, io.BytesIO(content))
            archive.seek(0)

            try:
                ok = self._container.put_archive(parent_dir, archive.getvalue())
            except Exception as exc:  # noqa: BLE001
                error = _map_exception_to_file_error(exc)
                responses.append(FileUploadResponse(path=normalized_path, error=error))
                continue

            responses.append(
                FileUploadResponse(
                    path=normalized_path,
                    error=None if ok else "permission_denied",
                )
            )

        return responses

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        responses: list[FileDownloadResponse] = []

        for raw_path in paths:
            normalized_path = self._normalize_path(raw_path)
            try:
                stream, _ = self._container.get_archive(normalized_path)
                archive_bytes = b"".join(stream)
            except NotFound:
                responses.append(
                    FileDownloadResponse(path=normalized_path, content=None, error="file_not_found")
                )
                continue
            except Exception as exc:  # noqa: BLE001
                error = _map_exception_to_file_error(exc)
                responses.append(
                    FileDownloadResponse(path=normalized_path, content=None, error=error)
                )
                continue

            try:
                with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:*") as archive:
                    member = next((m for m in archive.getmembers() if m.isfile()), None)
                    if member is None:
                        responses.append(
                            FileDownloadResponse(path=normalized_path, content=None, error="is_directory")
                        )
                        continue
                    extracted = archive.extractfile(member)
                    content = extracted.read() if extracted is not None else b""
            except Exception as exc:  # noqa: BLE001
                error = _map_exception_to_file_error(exc)
                responses.append(
                    FileDownloadResponse(path=normalized_path, content=None, error=error)
                )
                continue

            responses.append(FileDownloadResponse(path=normalized_path, content=content, error=None))

        return responses

    @staticmethod
    def _candidate_read_paths(raw_path: str) -> list[str]:
        candidates: list[str] = []
        value = str(raw_path or "")
        variants = [
            value,
            value.strip(),
            value.strip().strip('"').strip("'"),
            value.strip().strip("“”").strip("‘’").strip('"').strip("'"),
        ]
        for item in variants:
            if item and item not in candidates:
                candidates.append(item)
        return candidates

    def _resolve_default_file_path(self, raw_path: str) -> str:
        text = str(raw_path or "").replace("\\", "/").strip()
        uploads_root = self._uploads_root()
        if not text:
            return uploads_root

        # Remove matched wrapping quotes produced by malformed tool args.
        for _ in range(3):
            if len(text) >= 2 and ((text[0] == '"' and text[-1] == '"') or (text[0] == "'" and text[-1] == "'")):
                text = text[1:-1].strip()
            else:
                break

        if not text:
            return uploads_root

        if text.startswith("/"):
            return self._normalize_path(text)

        normalized = posixpath.normpath(text)
        if normalized in {".", ""}:
            return uploads_root

        if normalized.startswith("workspace/"):
            return self._normalize_path("/" + normalized)
        if normalized.startswith("uploads/"):
            return self._join_under_root(uploads_root, normalized[len("uploads/") :])
        if normalized.startswith("skills/"):
            return self._join_under_root(posixpath.join(self._workdir, "skills"), normalized[len("skills/") :])
        if normalized.startswith("skills-builtin/"):
            return self._join_under_root(
                posixpath.join(self._workdir, "skills-builtin"),
                normalized[len("skills-builtin/") :],
            )

        return self._join_under_root(uploads_root, normalized)

    def _uploads_root(self) -> str:
        workdir = self._workdir.rstrip("/") or "/workspace"
        return posixpath.normpath(posixpath.join(workdir, "uploads"))

    @staticmethod
    def _join_under_root(root: str, relative_path: str) -> str:
        rel = posixpath.normpath(str(relative_path or "").strip().lstrip("/"))
        if rel in {"", "."}:
            return posixpath.normpath(root)

        candidate = posixpath.normpath(posixpath.join(root, rel))
        normalized_root = posixpath.normpath(root)
        if candidate == normalized_root or candidate.startswith(normalized_root + "/"):
            return candidate

        fallback_name = posixpath.basename(rel) or "file"
        return posixpath.normpath(posixpath.join(normalized_root, fallback_name))

    @staticmethod
    def _is_not_found_result(result: Any) -> bool:
        if isinstance(result, str):
            lowered = result.lower()
            return " not found" in lowered or "file_not_found" in lowered
        error = getattr(result, "error", None)
        if not isinstance(error, str):
            return False
        lowered = error.lower()
        return "not found" in lowered or "file_not_found" in lowered

    @staticmethod
    def _build_unreadable_file_message(path: str) -> str:
        return (
            f"Error: File '{path}' exists but is not UTF-8 text. "
            "read_file supports text files directly; use a format-specific parser for this file type."
        )

    def _path_exists(self, raw_path: str) -> bool:
        path_b64 = base64.b64encode(str(raw_path or "").encode("utf-8")).decode("ascii")
        cmd = f"""python3 -c "
import base64
import os

path = base64.b64decode('{path_b64}').decode('utf-8', errors='ignore')
print('1' if os.path.isfile(path) else '0')
" 2>/dev/null"""
        result = self.execute(cmd)
        return result.exit_code in {0, None} and result.output.strip() == "1"

    def _resolve_upload_alias_path(self, raw_path: str) -> str | None:
        """Best-effort match for uploads path with minor spacing/quote noise."""
        path_b64 = base64.b64encode(str(raw_path or "").encode("utf-8")).decode("ascii")
        cmd = f"""python3 -c "
import base64
import os

raw = base64.b64decode('{path_b64}').decode('utf-8', errors='ignore')

def normalize(p):
    v = (p or '').strip()
    for _ in range(3):
        if len(v) >= 2 and ((v[0] == '\"' and v[-1] == '\"') or (v[0] == \"'\" and v[-1] == \"'\")):
            v = v[1:-1].strip()
        else:
            break
    return v

def key(name):
    return ''.join(ch for ch in name.lower() if ch.isalnum())

target = normalize(raw)
if os.path.isfile(target):
    print(target)
    raise SystemExit(0)

parent = os.path.dirname(target)
base = os.path.basename(target)
if '/uploads/' not in target or not parent or not os.path.isdir(parent):
    print('')
    raise SystemExit(0)

k = key(base)
for name in os.listdir(parent):
    full = os.path.join(parent, name)
    if os.path.isfile(full) and key(name) == k:
        print(full)
        raise SystemExit(0)

print('')
" 2>/dev/null"""
        result = self.execute(cmd)
        resolved = result.output.strip()
        return resolved or None

    def stop_and_remove(self) -> None:
        container_name = self.container_name
        try:
            self._container.reload()
            status = str(getattr(self._container, "status", "")).lower()
            if status == "running":
                self._container.stop(timeout=2)
        except Exception:  # noqa: BLE001
            logger.debug("Failed stopping docker container %s", container_name, exc_info=True)

        try:
            self._container.remove(force=True)
        except Exception:  # noqa: BLE001
            logger.debug("Failed removing docker container %s", container_name, exc_info=True)

    @staticmethod
    def _normalize_path(path: str) -> str:
        normalized = path.strip()
        if not normalized.startswith("/"):
            normalized = "/" + normalized
        return posixpath.normpath(normalized)

    @staticmethod
    def _sh_quote(value: str) -> str:
        return "'" + value.replace("'", "'\"'\"'") + "'"

    @staticmethod
    def _decode_exec_output(output: Any) -> str:
        if isinstance(output, tuple):
            stdout_raw, stderr_raw = output
            stdout = stdout_raw.decode("utf-8", errors="replace") if isinstance(stdout_raw, (bytes, bytearray)) else str(stdout_raw or "")
            stderr = stderr_raw.decode("utf-8", errors="replace") if isinstance(stderr_raw, (bytes, bytearray)) else str(stderr_raw or "")

            parts: list[str] = []
            if stdout:
                parts.append(stdout)
            if stderr:
                stderr_lines = stderr.strip().splitlines() or [stderr]
                parts.extend(f"[stderr] {line}" for line in stderr_lines)
            return "\n".join(parts).strip()

        if isinstance(output, (bytes, bytearray)):
            return output.decode("utf-8", errors="replace").strip()

        if output is None:
            return ""

        return str(output).strip()


@dataclass
class _ManagedSessionSandbox:
    user_id: str
    sandbox: DockerSandbox
    client: docker.DockerClient
    client_name: str
    docker_host: str
    workspace_dir: Path
    mount_source: str
    skills_mount_source: str
    user_skills_mount_source: str


class DockerSandboxManager:
    def __init__(self, config: DockerSandboxConfig) -> None:
        self._config = config
        self._workspace_root = config.workspace_root
        self._workspace_root.mkdir(parents=True, exist_ok=True)
        config.skills_builtin_dir.mkdir(parents=True, exist_ok=True)
        config.skills_user_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._sessions: dict[str, _ManagedSessionSandbox] = {}
        self._clients = self._build_clients(config)

    def get_or_create(self, session_id: str, user_id: str) -> DockerSandbox:
        safe_session_id = _sanitize_segment(session_id, max_len=64)
        safe_user_id = _sanitize_segment(user_id, max_len=64)

        with self._lock:
            existing = self._sessions.get(safe_session_id)
            if existing is not None:
                if existing.user_id != safe_user_id:
                    msg = "session is already bound to another user"
                    raise PermissionError(msg)
                return self._ensure_session_container_locked(safe_session_id, existing)

            workspace_dir = (self._workspace_root / safe_session_id).resolve()
            workspace_dir.mkdir(parents=True, exist_ok=True)

            client_name, client, daemon_host = self._pick_client()
            docker_host = self._format_docker_host(client_name, daemon_host)
            mount_source = self._build_mount_source(
                workspace_dir=workspace_dir,
                daemon_host=daemon_host,
                safe_session_id=safe_session_id,
            )
            skills_mount_source = self._build_skills_mount_source(daemon_host=daemon_host)
            user_skills_mount_source = self._build_user_skills_mount_source(
                daemon_host=daemon_host,
                safe_user_id=safe_user_id,
            )
            container_name = self._build_container_name(safe_session_id)

            container = self._ensure_container(
                client=client,
                container_name=container_name,
                mount_source=mount_source,
                skills_mount_source=skills_mount_source,
                user_skills_mount_source=user_skills_mount_source,
            )

            sandbox = DockerSandbox(
                client=client,
                container=container,
                default_timeout=self._config.timeout,
                workdir=self._config.workdir,
            )
            self._sessions[safe_session_id] = _ManagedSessionSandbox(
                user_id=safe_user_id,
                sandbox=sandbox,
                client=client,
                client_name=client_name,
                docker_host=docker_host,
                workspace_dir=workspace_dir,
                mount_source=mount_source,
                skills_mount_source=skills_mount_source,
                user_skills_mount_source=user_skills_mount_source,
            )
            return sandbox

    def close_session(self, session_id: str) -> None:
        safe_session_id = _sanitize_segment(session_id, max_len=64)
        with self._lock:
            session = self._sessions.pop(safe_session_id, None)

        if session is None:
            return

        session.sandbox.stop_and_remove()

    def get_session_info(self, session_id: str) -> dict[str, str] | None:
        safe_session_id = _sanitize_segment(session_id, max_len=64)
        with self._lock:
            session = self._sessions.get(safe_session_id)
            if session is None:
                return None
            sandbox = self._ensure_session_container_locked(safe_session_id, session)
            return {
                "container_id": sandbox.id,
                "container_name": sandbox.container_name,
                "client_name": session.client_name,
                "docker_host": session.docker_host,
                "workspace_dir": str(session.workspace_dir),
                "mount_source": session.mount_source,
                "skills_mount_source": session.skills_mount_source,
                "user_skills_mount_source": session.user_skills_mount_source,
            }

    def get_session_sandbox(self, session_id: str) -> DockerSandbox | None:
        safe_session_id = _sanitize_segment(session_id, max_len=64)
        with self._lock:
            session = self._sessions.get(safe_session_id)
            if session is None:
                return None
            return self._ensure_session_container_locked(safe_session_id, session)

    def close_all(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()

        for session in sessions:
            session.sandbox.stop_and_remove()

    def _ensure_session_container_locked(
        self,
        safe_session_id: str,
        session: _ManagedSessionSandbox,
    ) -> DockerSandbox:
        container_name = self._build_container_name(safe_session_id)
        container = self._ensure_container(
            client=session.client,
            container_name=container_name,
            mount_source=session.mount_source,
            skills_mount_source=session.skills_mount_source,
            user_skills_mount_source=session.user_skills_mount_source,
        )
        new_id = str(getattr(container, "id", ""))
        if session.sandbox.id != new_id:
            logger.info(
                "Docker sandbox session=%s healed with new container id=%s (old=%s)",
                safe_session_id,
                new_id,
                session.sandbox.id,
            )
            session.sandbox = DockerSandbox(
                client=session.client,
                container=container,
                default_timeout=self._config.timeout,
                workdir=self._config.workdir,
            )
        return session.sandbox

    def _build_clients(
        self,
        config: DockerSandboxConfig,
    ) -> list[tuple[str, docker.DockerClient, DockerDaemonHostConfig | None]]:
        if config.daemon_hosts:
            clients: list[tuple[str, docker.DockerClient, DockerDaemonHostConfig | None]] = []
            tls_config = self._build_tls_config(config)
            for host_cfg in config.daemon_hosts:
                client = docker.DockerClient(
                    base_url=host_cfg.host,
                    timeout=config.client_timeout,
                    tls=tls_config,
                )
                clients.append((host_cfg.name, client, host_cfg))
            return clients

        return [
            (
                "local",
                docker.from_env(timeout=config.client_timeout),
                None,
            )
        ]

    def _build_tls_config(self, config: DockerSandboxConfig) -> Any:
        if config.daemon_tls is None or not config.daemon_tls.enabled:
            return None

        tls_cfg = config.daemon_tls
        client = None
        if tls_cfg.client_cert and tls_cfg.client_key:
            client = (str(tls_cfg.client_cert), str(tls_cfg.client_key))

        return docker.tls.TLSConfig(
            ca_cert=str(tls_cfg.ca_cert) if tls_cfg.ca_cert else None,
            client_cert=client,
            verify=tls_cfg.verify,
        )

    def _pick_client(
        self,
    ) -> tuple[str, docker.DockerClient, DockerDaemonHostConfig | None]:
        if len(self._clients) == 1:
            return self._clients[0]

        idx = secrets.randbelow(len(self._clients))
        return self._clients[idx]

    @staticmethod
    def _format_docker_host(
        client_name: str,
        daemon_host: DockerDaemonHostConfig | None,
    ) -> str:
        if daemon_host is None:
            return client_name
        return f"{client_name} ({daemon_host.host})"

    def _build_mount_source(
        self,
        *,
        workspace_dir: Path,
        daemon_host: DockerDaemonHostConfig | None,
        safe_session_id: str,
    ) -> str:
        if daemon_host is None:
            return str(workspace_dir)

        if self._config.daemon_workspace_root:
            return posixpath.join(self._config.daemon_workspace_root, safe_session_id)

        return str(workspace_dir)

    def _build_skills_mount_source(
        self,
        *,
        daemon_host: DockerDaemonHostConfig | None,
    ) -> str:
        if daemon_host is None:
            return str(self._config.skills_builtin_dir.resolve())
        if self._config.daemon_skills_builtin_dir:
            return self._config.daemon_skills_builtin_dir
        return str(self._config.skills_builtin_dir.resolve())

    def _build_user_skills_mount_source(
        self,
        *,
        daemon_host: DockerDaemonHostConfig | None,
        safe_user_id: str,
    ) -> str:
        if daemon_host is None:
            user_root = (self._config.skills_user_dir / safe_user_id).resolve()
            user_root.mkdir(parents=True, exist_ok=True)
            return str(user_root)

        base_dir = self._config.daemon_skills_user_dir
        if base_dir:
            return posixpath.join(base_dir, safe_user_id)

        user_root = (self._config.skills_user_dir / safe_user_id).resolve()
        user_root.mkdir(parents=True, exist_ok=True)
        return str(user_root)

    def _skills_mount_target(self) -> str:
        workdir = self._config.workdir.rstrip("/") or "/workspace"
        return posixpath.join(workdir, "skills-builtin")

    def _user_skills_mount_target(self) -> str:
        workdir = self._config.workdir.rstrip("/") or "/workspace"
        return posixpath.join(workdir, "skills")

    def _build_container_name(self, safe_session_id: str) -> str:
        prefix = _sanitize_segment(self._config.container_name_prefix, max_len=24)
        digest = hashlib.sha1(safe_session_id.encode("utf-8")).hexdigest()[:8]  # noqa: S324
        return f"{prefix}-{safe_session_id[:28]}-{digest}".strip("-")

    def _ensure_container(
        self,
        *,
        client: docker.DockerClient,
        container_name: str,
        mount_source: str,
        skills_mount_source: str,
        user_skills_mount_source: str,
    ) -> Any:
        container = None
        try:
            container = client.containers.get(container_name)
            container.reload()
            if getattr(container, "status", "") != "running":
                container.start()
                container.reload()
            if self._container_has_expected_mounts(
                container,
                mount_source=mount_source,
                skills_mount_source=skills_mount_source,
                user_skills_mount_source=user_skills_mount_source,
            ) and self._container_has_expected_runtime_config(container):
                return container
            logger.info(
                "Recreating docker container %s because config is outdated",
                container_name,
            )
            try:
                container.remove(force=True)
            except Exception:  # noqa: BLE001
                logger.debug("Failed removing outdated container %s", container_name, exc_info=True)
                raise
            container = None
        except NotFound:
            container = None
        except APIError:
            logger.warning("Failed to inspect existing container %s", container_name, exc_info=True)

        mode = "ro" if self._config.readonly_workdir else "rw"
        volumes = {
            mount_source: {
                "bind": self._config.workdir,
                "mode": mode,
            },
            skills_mount_source: {
                "bind": self._skills_mount_target(),
                "mode": "ro",
            },
            user_skills_mount_source: {
                "bind": self._user_skills_mount_target(),
                "mode": "rw",
            },
        }

        environment = self._config.environment if self._config.environment else None
        extra_hosts = self._desired_extra_hosts()
        nano_cpus = self._desired_nano_cpus()
        mem_limit = self._desired_memory_limit_bytes()

        run_kwargs: dict[str, Any] = {
            "image": self._config.image,
            "name": container_name,
            "command": ["/bin/sh", "-lc", "while true; do sleep 3600; done"],
            "detach": True,
            "working_dir": self._config.workdir,
            "auto_remove": self._config.auto_remove,
            "environment": environment,
            "volumes": volumes,
        }
        if extra_hosts:
            run_kwargs["extra_hosts"] = extra_hosts
        if isinstance(nano_cpus, int) and nano_cpus > 0:
            run_kwargs["nano_cpus"] = nano_cpus
        if isinstance(mem_limit, int) and mem_limit > 0:
            run_kwargs["mem_limit"] = mem_limit

        try:
            return client.containers.run(**run_kwargs)
        except ImageNotFound:
            logger.info("Pulling docker image %s", self._config.image)
            client.images.pull(self._config.image)
            return client.containers.run(**run_kwargs)

    def _container_has_expected_mounts(
        self,
        container: Any,
        *,
        mount_source: str,
        skills_mount_source: str,
        user_skills_mount_source: str,
    ) -> bool:
        mounts = getattr(container, "attrs", {}).get("Mounts")
        if not isinstance(mounts, list):
            return False

        workdir = posixpath.normpath(self._config.workdir.rstrip("/") or "/")
        skills_target = posixpath.normpath(self._skills_mount_target())
        user_skills_target = posixpath.normpath(self._user_skills_mount_target())
        expected_workdir_rw = not self._config.readonly_workdir
        expected_workdir_source = self._normalize_source(mount_source)
        expected_skills_source = self._normalize_source(skills_mount_source)
        expected_user_skills_source = self._normalize_source(user_skills_mount_source)

        workdir_ok = False
        skills_ok = False
        user_skills_ok = False
        for mount in mounts:
            if not isinstance(mount, dict):
                continue
            destination = mount.get("Destination")
            if not isinstance(destination, str):
                continue
            normalized_dest = posixpath.normpath(destination.rstrip("/") or "/")
            source = self._normalize_source(str(mount.get("Source") or ""))
            rw = bool(mount.get("RW", False))
            if (
                normalized_dest == workdir
                and rw == expected_workdir_rw
                and source == expected_workdir_source
            ):
                workdir_ok = True
                continue
            if (
                normalized_dest == skills_target
                and not rw
                and source == expected_skills_source
            ):
                skills_ok = True
                continue
            if (
                normalized_dest == user_skills_target
                and rw
                and source == expected_user_skills_source
            ):
                user_skills_ok = True

        return workdir_ok and skills_ok and user_skills_ok

    def _container_has_expected_runtime_config(self, container: Any) -> bool:
        host_cfg = getattr(container, "attrs", {}).get("HostConfig")
        if not isinstance(host_cfg, dict):
            return False

        desired_nano_cpus = self._desired_nano_cpus() or 0
        desired_memory = self._desired_memory_limit_bytes() or 0
        actual_nano_cpus = int(host_cfg.get("NanoCpus") or 0)
        actual_memory = int(host_cfg.get("Memory") or 0)
        if actual_nano_cpus != desired_nano_cpus:
            return False
        if actual_memory != desired_memory:
            return False

        desired_hosts = self._normalize_extra_hosts(hosts=self._desired_extra_hosts())
        actual_hosts = self._normalize_extra_hosts(hosts=host_cfg.get("ExtraHosts"))
        return desired_hosts == actual_hosts

    def _desired_extra_hosts(self) -> dict[str, str] | None:
        if not self._config.hostnames:
            return None
        return {hostname: ip for hostname, ip in self._config.hostnames}

    def _desired_nano_cpus(self) -> int | None:
        limit = self._config.cpu_limit
        if not isinstance(limit, (int, float)) or limit <= 0:
            return None
        return max(1, int(float(limit) * 1_000_000_000))

    def _desired_memory_limit_bytes(self) -> int | None:
        value = self._config.memory_limit_bytes
        if not isinstance(value, int) or value <= 0:
            return None
        return value

    @staticmethod
    def _normalize_extra_hosts(hosts: Any) -> tuple[tuple[str, str], ...]:
        if hosts is None:
            return tuple()

        pairs: list[tuple[str, str]] = []
        if isinstance(hosts, dict):
            for hostname, ip in hosts.items():
                host = str(hostname).strip()
                addr = str(ip).strip()
                if host and addr:
                    pairs.append((host, addr))
        elif isinstance(hosts, list):
            for item in hosts:
                if not isinstance(item, str) or ":" not in item:
                    continue
                host, ip = item.split(":", 1)
                host = host.strip()
                ip = ip.strip()
                if host and ip:
                    pairs.append((host, ip))

        deduped: dict[str, str] = {}
        for host, ip in pairs:
            deduped[host] = ip
        return tuple(sorted(deduped.items()))

    @staticmethod
    def _normalize_source(source: str) -> str:
        normalized = source.strip()
        if not normalized:
            return ""
        return posixpath.normpath(normalized)

    def __del__(self) -> None:
        try:
            self.close_all()
        except Exception:  # noqa: BLE001
            pass
