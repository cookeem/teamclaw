from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from app.i18n import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES, normalize_language

@dataclass(frozen=True)
class DockerDaemonHostConfig:
    name: str
    host: str


@dataclass(frozen=True)
class DockerTlsConfig:
    enabled: bool
    certs_dir: Path | None
    ca_cert: Path | None
    client_cert: Path | None
    client_key: Path | None
    verify: bool


@dataclass(frozen=True)
class DockerSandboxConfig:
    image: str
    container_name_prefix: str
    workspace_root: Path
    skills_builtin_dir: Path
    skills_user_dir: Path
    workdir: str
    readonly_workdir: bool
    auto_remove: bool
    timeout: int
    client_timeout: int
    environment: dict[str, str]
    cpu_limit: float | None
    memory_limit_bytes: int | None
    hostnames: tuple[tuple[str, str], ...]
    daemon_hosts: tuple[DockerDaemonHostConfig, ...]
    daemon_workspace_root: str | None
    daemon_skills_builtin_dir: str | None
    daemon_skills_user_dir: str | None
    daemon_tls: DockerTlsConfig | None


@dataclass(frozen=True)
class ScheduledTasksConfig:
    enabled: bool
    poll_interval_seconds: int
    batch_size: int
    llm_wait_timeout_seconds: int
    max_script_output_chars: int
    max_summary_input_chars: int


@dataclass(frozen=True)
class PromptConfig:
    enabled: bool
    directory: Path | None
    system_file: str
    behavior_file: str
    system_mode: str


@dataclass(frozen=True)
class DatabaseConfig:
    host: str
    port: int
    username: str
    password: str
    dbname: str
    echo: bool

    @property
    def url(self) -> str:
        user = quote_plus(self.username)
        password = quote_plus(self.password)
        dbname = quote_plus(self.dbname)
        return f"postgresql+asyncpg://{user}:{password}@{self.host}:{self.port}/{dbname}"

    @property
    def checkpoint_url(self) -> str:
        """Driver-agnostic Postgres URL for LangGraph checkpointer backends."""
        return self.url.replace("postgresql+asyncpg://", "postgresql://", 1)


@dataclass(frozen=True)
class SmtpConfig:
    enabled: bool
    host: str | None
    port: int
    username: str | None
    password: str | None
    from_email: str | None
    from_name: str
    reply_to: str | None
    use_tls: bool
    use_ssl: bool
    timeout: int
    reset_code_ttl_seconds: int
    reset_subject: str
    reset_url_template: str | None

    @property
    def is_configured(self) -> bool:
        return bool(self.host and self.from_email)

    @property
    def can_send(self) -> bool:
        return self.enabled and self.is_configured


@dataclass(frozen=True)
class RuntimeModelConfig:
    provider: str
    model: str
    provider_kwargs: dict[str, Any]
    model_kwargs: dict[str, Any]


@dataclass(frozen=True)
class SessionConfig:
    idle_timeout_seconds: int
    touch_interval_seconds: int


@dataclass(frozen=True)
class TeamClawConfig:
    raw: dict[str, Any]
    path: Path

    @property
    def app_version(self) -> str:
        env_version = self._as_optional_non_empty_str(os.environ.get("TEAMCLAW_VERSION"))
        if env_version:
            return env_version

        app_cfg = self.raw.get("app")
        if not isinstance(app_cfg, dict):
            return "dev"

        # Preferred key.
        direct = self._as_optional_non_empty_str(app_cfg.get("version"))
        if direct:
            return direct

        # Backward-compatible fallback when version is placed under prompts.
        prompts_cfg = app_cfg.get("prompts")
        if isinstance(prompts_cfg, dict):
            nested = self._as_optional_non_empty_str(prompts_cfg.get("version"))
            if nested:
                return nested

        return "dev"

    @property
    def default_user_conversation_limit(self) -> int:
        app_cfg = self.raw.get("app")
        if not isinstance(app_cfg, dict):
            return 4
        return self._to_int_with_min(app_cfg.get("default_user_conversation_limit"), default=4, min_value=-1)

    @property
    def llm_message_debug(self) -> bool:
        app_cfg = self.raw.get("app")
        if not isinstance(app_cfg, dict):
            return False

        debug_cfg = app_cfg.get("debug")
        if isinstance(debug_cfg, dict) and "llm_message" in debug_cfg:
            return self._to_bool(debug_cfg.get("llm_message"))

        # Backward-compatible fallback for old key.
        if "message_debug" in app_cfg:
            return self._to_bool(app_cfg.get("message_debug"))

        return False

    @property
    def sandbox_timezone(self) -> str:
        explicit = self._as_optional_non_empty_str(os.environ.get("TEAMCLAW_SANDBOX_TZ"))
        if explicit:
            return self._normalize_timezone(explicit, default="UTC")

        docker_tz = self._as_optional_non_empty_str(self.docker_sandbox.environment.get("TZ"))
        if docker_tz:
            return self._normalize_timezone(docker_tz, default="UTC")

        env_tz = self._as_optional_non_empty_str(os.environ.get("TZ"))
        if env_tz:
            return self._normalize_timezone(env_tz, default="UTC")

        return "UTC"

    @property
    def language(self) -> str:
        env_lang = os.environ.get("TEAMCLAW_LANGUAGE")
        if isinstance(env_lang, str) and env_lang.strip():
            return normalize_language(env_lang)
        app_cfg = self.raw.get("app")
        if not isinstance(app_cfg, dict):
            return DEFAULT_LANGUAGE
        raw = app_cfg.get("language", app_cfg.get("locale"))
        i18n_cfg = app_cfg.get("i18n")
        if isinstance(i18n_cfg, dict):
            raw = i18n_cfg.get("language", i18n_cfg.get("locale", raw))
        return normalize_language(raw)

    @property
    def supported_languages(self) -> tuple[str, ...]:
        return SUPPORTED_LANGUAGES

    @property
    def session(self) -> SessionConfig:
        app_cfg = self.raw.get("app")
        if not isinstance(app_cfg, dict):
            app_cfg = {}
        session_cfg = app_cfg.get("session")
        if not isinstance(session_cfg, dict):
            session_cfg = {}

        idle_timeout_seconds = self._to_positive_int(
            session_cfg.get("idle_timeout_seconds"),
            3600,
        )
        touch_interval_seconds = self._to_positive_int(
            session_cfg.get("touch_interval_seconds"),
            60,
        )
        return SessionConfig(
            idle_timeout_seconds=idle_timeout_seconds,
            touch_interval_seconds=touch_interval_seconds,
        )

    @property
    def avatars_dir(self) -> Path:
        app_cfg = self.raw.get("app")
        if not isinstance(app_cfg, dict):
            app_cfg = {}
        raw_dir = os.environ.get("TEAMCLAW_AVATARS_DIR", app_cfg.get("avatars_dir", "./avatars"))
        return _resolve_from_config_root(raw_dir, self.path.parent)

    @property
    def database(self) -> DatabaseConfig:
        db_cfg = self.raw.get("database")
        if not isinstance(db_cfg, dict):
            app_cfg = self.raw.get("app")
            db_cfg = app_cfg.get("database") if isinstance(app_cfg, dict) else None
        if not isinstance(db_cfg, dict):
            db_cfg = {}

        return DatabaseConfig(
            host=self._as_non_empty_str(
                os.environ.get("TEAMCLAW_DB_HOST"),
                self._as_non_empty_str(db_cfg.get("host"), "127.0.0.1"),
            ),
            port=self._to_positive_int(
                os.environ.get("TEAMCLAW_DB_PORT"),
                self._to_positive_int(db_cfg.get("port"), 5432),
            ),
            username=self._as_non_empty_str(
                os.environ.get("TEAMCLAW_DB_USER"),
                self._as_non_empty_str(db_cfg.get("username"), "teamclaw"),
            ),
            password=self._as_non_empty_str(
                os.environ.get("TEAMCLAW_DB_PASSWORD"),
                self._as_non_empty_str(db_cfg.get("password"), "teamclaw_dev_password"),
            ),
            dbname=self._as_non_empty_str(
                os.environ.get("TEAMCLAW_DB_NAME"),
                self._as_non_empty_str(db_cfg.get("dbname"), "teamclaw"),
            ),
            echo=self._to_bool(
                os.environ.get("TEAMCLAW_DB_ECHO", db_cfg.get("echo", False))
            ),
        )

    @property
    def smtp(self) -> SmtpConfig:
        smtp_cfg = self.raw.get("smtp")
        if not isinstance(smtp_cfg, dict):
            app_cfg = self.raw.get("app")
            smtp_cfg = app_cfg.get("smtp") if isinstance(app_cfg, dict) else None
        if not isinstance(smtp_cfg, dict):
            smtp_cfg = {}

        host = self._as_optional_non_empty_str(
            os.environ.get("TEAMCLAW_SMTP_HOST"),
            self._as_optional_non_empty_str(smtp_cfg.get("host")),
        )
        port = self._to_positive_int(
            os.environ.get("TEAMCLAW_SMTP_PORT"),
            self._to_positive_int(smtp_cfg.get("port"), 587),
        )
        username = self._as_optional_non_empty_str(
            os.environ.get("TEAMCLAW_SMTP_USERNAME"),
            self._as_optional_non_empty_str(smtp_cfg.get("username"), self._as_optional_non_empty_str(smtp_cfg.get("user"))),
        )
        password = self._as_optional_non_empty_str(
            os.environ.get("TEAMCLAW_SMTP_PASSWORD"),
            self._as_optional_non_empty_str(smtp_cfg.get("password"), self._as_optional_non_empty_str(smtp_cfg.get("pass"))),
        )
        from_email = self._as_optional_non_empty_str(
            os.environ.get("TEAMCLAW_SMTP_FROM_EMAIL"),
            self._as_optional_non_empty_str(
                smtp_cfg.get("from_email"),
                self._as_optional_non_empty_str(
                    smtp_cfg.get("from_address"),
                    self._as_optional_non_empty_str(smtp_cfg.get("sender_email")),
                ),
            ),
        )
        from_name = self._as_non_empty_str(
            os.environ.get("TEAMCLAW_SMTP_FROM_NAME"),
            self._as_non_empty_str(
                smtp_cfg.get("from_name"),
                self._as_non_empty_str(smtp_cfg.get("sender_name"), "TeamClaw"),
            ),
        )
        reply_to = self._as_optional_non_empty_str(
            os.environ.get("TEAMCLAW_SMTP_REPLY_TO"),
            self._as_optional_non_empty_str(smtp_cfg.get("reply_to")),
        )
        use_tls = self._to_bool(
            os.environ.get(
                "TEAMCLAW_SMTP_USE_TLS",
                smtp_cfg.get("use_tls", smtp_cfg.get("starttls", smtp_cfg.get("tls", True))),
            )
        )
        use_ssl = self._to_bool(
            os.environ.get("TEAMCLAW_SMTP_USE_SSL", smtp_cfg.get("use_ssl", smtp_cfg.get("ssl", False)))
        )
        timeout = self._to_positive_int(
            os.environ.get("TEAMCLAW_SMTP_TIMEOUT"),
            self._to_positive_int(smtp_cfg.get("timeout", smtp_cfg.get("timeout_seconds")), 15),
        )
        reset_code_ttl_seconds = self._to_positive_int(
            os.environ.get("TEAMCLAW_SMTP_RESET_CODE_TTL_SECONDS"),
            self._to_positive_int(
                smtp_cfg.get("reset_code_ttl_seconds", smtp_cfg.get("reset_ttl_seconds")),
                600,
            ),
        )
        reset_subject = self._as_non_empty_str(
            os.environ.get("TEAMCLAW_SMTP_RESET_SUBJECT"),
            self._as_non_empty_str(smtp_cfg.get("reset_subject"), "TeamClaw Password Reset"),
        )
        reset_url_template = self._as_optional_non_empty_str(
            os.environ.get("TEAMCLAW_SMTP_RESET_URL_TEMPLATE"),
            self._as_optional_non_empty_str(
                smtp_cfg.get("reset_url_template"),
                self._as_optional_non_empty_str(smtp_cfg.get("reset_link_template")),
            ),
        )

        enabled_default = bool(host and from_email)
        enabled = self._to_bool(
            os.environ.get("TEAMCLAW_SMTP_ENABLED", smtp_cfg.get("enabled", enabled_default))
        )
        return SmtpConfig(
            enabled=enabled,
            host=host,
            port=port,
            username=username,
            password=password,
            from_email=from_email,
            from_name=from_name,
            reply_to=reply_to,
            use_tls=use_tls,
            use_ssl=use_ssl,
            timeout=timeout,
            reset_code_ttl_seconds=reset_code_ttl_seconds,
            reset_subject=reset_subject,
            reset_url_template=reset_url_template,
        )

    @property
    def prompt_config(self) -> PromptConfig:
        app_cfg = self.raw.get("app")
        if not isinstance(app_cfg, dict):
            return PromptConfig(
                enabled=False,
                directory=None,
                system_file="system.md",
                behavior_file="behavior.md",
                system_mode="append",
            )

        prompts_cfg = app_cfg.get("prompts")
        if not isinstance(prompts_cfg, dict):
            return PromptConfig(
                enabled=False,
                directory=None,
                system_file="system.md",
                behavior_file="behavior.md",
                system_mode="append",
            )

        prompt_dir = _resolve_optional_from_config_root(prompts_cfg.get("dir"), self.path.parent)
        enabled = self._to_bool(prompts_cfg.get("enabled", prompt_dir is not None))

        system_mode_raw = prompts_cfg.get("system_mode")
        system_mode = (
            system_mode_raw.strip().lower()
            if isinstance(system_mode_raw, str) and system_mode_raw.strip()
            else "append"
        )
        if system_mode not in {"append", "override"}:
            system_mode = "append"

        return PromptConfig(
            enabled=enabled and prompt_dir is not None,
            directory=prompt_dir if enabled else None,
            system_file=self._as_non_empty_str(prompts_cfg.get("system_file"), "system.md"),
            behavior_file=self._as_non_empty_str(prompts_cfg.get("behavior_file"), "behavior.md"),
            system_mode=system_mode,
        )

    @property
    def tavily_api_key(self) -> str | None:
        api_keys = self.raw.get("api_keys")
        if not isinstance(api_keys, dict):
            return None
        tavily_key = api_keys.get("tavily")
        if isinstance(tavily_key, str) and tavily_key.strip():
            return tavily_key.strip()
        return None

    @property
    def docker_sandbox(self) -> DockerSandboxConfig:
        docker_cfg = self.raw.get("docker")
        if not isinstance(docker_cfg, dict):
            docker_cfg = {}

        workspace_root_raw = os.environ.get(
            "TEAMCLAW_WORKSPACES_ROOT",
            docker_cfg.get("workspace_root", "./workspaces"),
        )
        workspace_root = _resolve_from_config_root(workspace_root_raw, self.path.parent)
        skills_builtin_dir = _resolve_from_config_root(
            docker_cfg.get("skills_builtin_dir") or "./skills-builtin",
            self.path.parent,
        )
        skills_user_dir = _resolve_from_config_root(
            docker_cfg.get("skills_user_dir") or "./skills/user",
            self.path.parent,
        )

        daemon_hosts: list[DockerDaemonHostConfig] = []
        raw_hosts = docker_cfg.get("daemon_hosts")
        if isinstance(raw_hosts, list):
            for idx, item in enumerate(raw_hosts):
                if not isinstance(item, dict):
                    continue
                host = item.get("host")
                if not isinstance(host, str) or not host.strip():
                    continue
                raw_name = item.get("name")
                if isinstance(raw_name, str) and raw_name.strip():
                    name = raw_name.strip()
                else:
                    name = f"daemon-{idx}"
                daemon_hosts.append(DockerDaemonHostConfig(name=name, host=host.strip()))

        daemon_tls_cfg: DockerTlsConfig | None = None
        raw_tls = docker_cfg.get("daemon_tls")
        if isinstance(raw_tls, dict):
            tls_enabled = self._to_bool(raw_tls.get("enabled"))
            if tls_enabled:
                daemon_tls_cfg = DockerTlsConfig(
                    enabled=True,
                    certs_dir=_resolve_optional_from_config_root(raw_tls.get("certs_dir"), self.path.parent),
                    ca_cert=_resolve_optional_from_config_root(raw_tls.get("ca_cert"), self.path.parent),
                    client_cert=_resolve_optional_from_config_root(raw_tls.get("client_cert"), self.path.parent),
                    client_key=_resolve_optional_from_config_root(raw_tls.get("client_key"), self.path.parent),
                    verify=self._to_bool(raw_tls.get("verify", True)),
                )

        cpu_limit = self._to_optional_positive_float(docker_cfg.get("cpu_limit"))
        memory_limit_bytes = self._to_optional_memory_bytes(docker_cfg.get("memory_limit"))
        hostnames = self._parse_hostnames(docker_cfg.get("hostnames"))

        return DockerSandboxConfig(
            image=self._as_non_empty_str(docker_cfg.get("image"), "python:3.12-slim"),
            container_name_prefix=self._as_non_empty_str(
                docker_cfg.get("container_name_prefix"),
                "teamclaw-exec",
            ),
            workspace_root=workspace_root,
            skills_builtin_dir=skills_builtin_dir,
            skills_user_dir=skills_user_dir,
            workdir=self._normalize_workdir(docker_cfg.get("workdir")),
            readonly_workdir=self._to_bool(docker_cfg.get("readonly_workdir", False)),
            auto_remove=self._to_bool(docker_cfg.get("auto_remove", False)),
            timeout=self._to_positive_int(docker_cfg.get("timeout"), 300),
            client_timeout=self._to_positive_int(docker_cfg.get("client_timeout"), 10),
            environment=self._parse_environment(docker_cfg.get("environment")),
            cpu_limit=cpu_limit,
            memory_limit_bytes=memory_limit_bytes,
            hostnames=hostnames,
            daemon_hosts=tuple(daemon_hosts),
            daemon_workspace_root=self._normalize_optional_absolute_path(
                docker_cfg.get("daemon_workspace_root")
            ),
            daemon_skills_builtin_dir=self._normalize_optional_absolute_path(
                docker_cfg.get("daemon_skills_builtin_dir", "/skills-builtin")
            ),
            daemon_skills_user_dir=self._normalize_optional_absolute_path(
                docker_cfg.get("daemon_skills_user_dir", "/skills/user")
            ),
            daemon_tls=daemon_tls_cfg,
        )

    @property
    def scheduled_tasks(self) -> ScheduledTasksConfig:
        raw = self.raw.get("scheduled_tasks")
        if not isinstance(raw, dict):
            raw = {}
        return ScheduledTasksConfig(
            enabled=self._to_bool(raw.get("enabled", True)),
            poll_interval_seconds=self._to_positive_int(raw.get("poll_interval_seconds"), 10),
            batch_size=self._to_positive_int(raw.get("batch_size"), 3),
            llm_wait_timeout_seconds=self._to_positive_int(raw.get("llm_wait_timeout_seconds"), 1800),
            max_script_output_chars=self._to_positive_int(raw.get("max_script_output_chars"), 20000),
            max_summary_input_chars=self._to_positive_int(raw.get("max_summary_input_chars"), 12000),
        )

    @property
    def providers(self) -> dict[str, dict[str, Any]]:
        models_root = self.raw.get("models")
        if not isinstance(models_root, dict):
            return {}
        providers = models_root.get("providers")
        if not isinstance(providers, dict):
            return {}

        normalized: dict[str, dict[str, Any]] = {}
        for provider_name, provider_config in providers.items():
            if isinstance(provider_name, str) and isinstance(provider_config, dict):
                normalized[provider_name] = provider_config
        return normalized

    def default_provider(self) -> str:
        for provider_name, provider_cfg in self.providers.items():
            models = provider_cfg.get("models")
            if isinstance(models, list) and models:
                return provider_name
        msg = "No enabled model provider found in config.yaml models.providers"
        raise ValueError(msg)

    def default_model(self, provider: str | None = None) -> str:
        selected_provider = provider or self.default_provider()
        provider_cfg = self.providers.get(selected_provider)
        if provider_cfg is None:
            msg = f"Provider '{selected_provider}' is not defined in config.yaml"
            raise ValueError(msg)

        models = provider_cfg.get("models")
        if not isinstance(models, list) or not models:
            msg = f"Provider '{selected_provider}' has no models configured"
            raise ValueError(msg)

        first_model = models[0]
        if not isinstance(first_model, str) or not first_model.strip():
            msg = f"Provider '{selected_provider}' has an invalid model entry"
            raise ValueError(msg)
        return first_model.strip()

    def list_models(self) -> dict[str, Any]:
        providers: list[dict[str, Any]] = []
        for provider_name, provider_cfg in self.providers.items():
            models_value = provider_cfg.get("models")
            models: list[str] = []
            if isinstance(models_value, list):
                models = [m.strip() for m in models_value if isinstance(m, str) and m.strip()]
            providers.append({"name": provider_name, "models": models})

        default_provider = self.default_provider()
        default_model = self.default_model(default_provider)

        return {
            "providers": providers,
            "default_provider": default_provider,
            "default_model": default_model,
        }

    def resolve_runtime_model(
        self,
        provider: str | None = None,
        model: str | None = None,
    ) -> RuntimeModelConfig:
        selected_provider = provider or self.default_provider()
        provider_cfg = self.providers.get(selected_provider)
        if provider_cfg is None:
            msg = f"Provider '{selected_provider}' was not found in config.yaml"
            raise ValueError(msg)

        available_models = provider_cfg.get("models")
        if not isinstance(available_models, list) or not available_models:
            msg = f"Provider '{selected_provider}' has no available models"
            raise ValueError(msg)

        normalized_models = [
            item.strip() for item in available_models if isinstance(item, str) and item.strip()
        ]
        if not normalized_models:
            msg = f"Provider '{selected_provider}' has no valid model names"
            raise ValueError(msg)

        selected_model = (model or normalized_models[0]).strip()
        if selected_model not in normalized_models:
            msg = (
                f"Model '{selected_model}' is not configured for provider "
                f"'{selected_provider}'"
            )
            raise ValueError(msg)

        provider_kwargs = {
            key: value
            for key, value in provider_cfg.items()
            if key not in {"models", "params"} and value is not None
        }
        model_kwargs = _merge_model_kwargs(provider_cfg.get("params"), selected_model)

        return RuntimeModelConfig(
            provider=selected_provider,
            model=selected_model,
            provider_kwargs=provider_kwargs,
            model_kwargs=model_kwargs,
        )

    @staticmethod
    def _to_bool(raw_value: Any) -> bool:
        if isinstance(raw_value, bool):
            return raw_value
        if isinstance(raw_value, str):
            return raw_value.strip().lower() in {"1", "true", "yes", "on"}
        if isinstance(raw_value, int):
            return raw_value != 0
        return False

    @staticmethod
    def _as_non_empty_str(raw_value: Any, default: str) -> str:
        if isinstance(raw_value, str) and raw_value.strip():
            return raw_value.strip()
        return default

    @staticmethod
    def _as_optional_non_empty_str(raw_value: Any, fallback: str | None = None) -> str | None:
        if isinstance(raw_value, str) and raw_value.strip():
            return raw_value.strip()
        return fallback

    @staticmethod
    def _to_positive_int(raw_value: Any, default: int) -> int:
        if isinstance(raw_value, bool):
            return default
        if isinstance(raw_value, int):
            return raw_value if raw_value > 0 else default
        if isinstance(raw_value, str):
            try:
                parsed = int(raw_value.strip())
            except ValueError:
                return default
            return parsed if parsed > 0 else default
        return default

    @staticmethod
    def _to_int_with_min(raw_value: Any, default: int, min_value: int) -> int:
        if isinstance(raw_value, bool):
            return default
        parsed: int | None = None
        if isinstance(raw_value, int):
            parsed = raw_value
        elif isinstance(raw_value, str):
            cleaned = raw_value.strip()
            if not cleaned:
                return default
            try:
                parsed = int(cleaned)
            except ValueError:
                return default
        if parsed is None:
            return default
        return parsed if parsed >= min_value else default

    @staticmethod
    def _normalize_workdir(raw_value: Any) -> str:
        if isinstance(raw_value, str) and raw_value.strip():
            normalized = raw_value.strip()
            if normalized.startswith("/"):
                return normalized
            return "/" + normalized
        return "/workspace"

    @staticmethod
    def _normalize_optional_absolute_path(raw_value: Any) -> str | None:
        if not isinstance(raw_value, str):
            return None
        cleaned = raw_value.strip()
        if not cleaned:
            return None
        if cleaned.startswith("/"):
            return cleaned
        return "/" + cleaned

    @staticmethod
    def _parse_environment(raw_value: Any) -> dict[str, str]:
        if isinstance(raw_value, dict):
            result: dict[str, str] = {}
            for key, value in raw_value.items():
                if not isinstance(key, str) or not key.strip():
                    continue
                if value is None:
                    continue
                result[key.strip()] = str(value)
            return result

        if isinstance(raw_value, list):
            result: dict[str, str] = {}
            for item in raw_value:
                if not isinstance(item, str):
                    continue
                if "=" in item:
                    key, value = item.split("=", 1)
                    key = key.strip()
                    if key:
                        result[key] = value
                    continue
                key = item.strip()
                if key:
                    result[key] = ""
            return result

        return {}

    @staticmethod
    def _normalize_timezone(raw_value: str, default: str = "UTC") -> str:
        candidate = str(raw_value or "").strip()
        if not candidate:
            return default
        try:
            ZoneInfo(candidate)
        except ZoneInfoNotFoundError:
            return default
        return candidate

    @staticmethod
    def _to_optional_positive_float(raw_value: Any) -> float | None:
        if raw_value is None:
            return None
        if isinstance(raw_value, bool):
            return None
        if isinstance(raw_value, (int, float)):
            parsed = float(raw_value)
            return parsed if parsed > 0 else None
        if isinstance(raw_value, str):
            value = raw_value.strip()
            if not value:
                return None
            try:
                parsed = float(value)
            except ValueError:
                return None
            return parsed if parsed > 0 else None
        return None

    @staticmethod
    def _to_optional_memory_bytes(raw_value: Any) -> int | None:
        if raw_value is None:
            return None
        if isinstance(raw_value, bool):
            return None
        if isinstance(raw_value, int):
            return raw_value if raw_value > 0 else None
        if isinstance(raw_value, float):
            value = int(raw_value)
            return value if value > 0 else None
        if not isinstance(raw_value, str):
            return None

        value = raw_value.strip().lower()
        if not value:
            return None
        if value.isdigit():
            parsed = int(value)
            return parsed if parsed > 0 else None

        units = {
            "k": 1024,
            "m": 1024**2,
            "g": 1024**3,
            "t": 1024**4,
            "ki": 1024,
            "mi": 1024**2,
            "gi": 1024**3,
            "ti": 1024**4,
            "kb": 1000,
            "mb": 1000**2,
            "gb": 1000**3,
            "tb": 1000**4,
        }
        suffix: str | None = None
        number_raw = value
        for candidate in sorted(units.keys(), key=len, reverse=True):
            if value.endswith(candidate):
                suffix = candidate
                number_raw = value[: -len(candidate)].strip()
                break

        if suffix is None or not number_raw:
            return None

        try:
            number = float(number_raw)
        except ValueError:
            return None
        if number <= 0:
            return None

        parsed = int(number * units[suffix])
        return parsed if parsed > 0 else None

    @staticmethod
    def _parse_hostnames(raw_value: Any) -> tuple[tuple[str, str], ...]:
        # Unified format:
        # hostnames:
        #   - "api.internal:10.0.0.10"
        #   - "db.internal:10.0.0.11"
        if not isinstance(raw_value, list):
            return tuple()

        entries: list[tuple[str, str]] = []
        for item in raw_value:
            if not isinstance(item, str) or ":" not in item:
                continue
            host, ip = item.split(":", 1)
            hostname = host.strip()
            addr = ip.strip()
            if not hostname or not addr:
                continue
            entries.append((hostname, addr))

        deduped: dict[str, str] = {}
        for host, ip in entries:
            deduped[host] = ip
        return tuple(deduped.items())


def discover_config_path(repo_root: Path) -> Path:
    candidates: list[Path] = []

    explicit = os.environ.get("TEAMCLAW_CONFIG_PATH")
    if isinstance(explicit, str) and explicit.strip():
        candidates.append(Path(explicit).expanduser())

    candidates.append(Path.cwd() / "config.yaml")
    candidates.append(repo_root / "config.yaml")

    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.exists():
            return resolved

    msg = "Unable to find config.yaml. Set TEAMCLAW_CONFIG_PATH to an explicit file path."
    raise FileNotFoundError(msg)


def load_teamclaw_config(path: Path) -> TeamClawConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        msg = f"Config file {path} is not a YAML dictionary"
        raise ValueError(msg)
    return TeamClawConfig(raw=raw, path=path)


def _merge_model_kwargs(params: Any, model_name: str) -> dict[str, Any]:
    if not isinstance(params, dict):
        return {}

    shared: dict[str, Any] = {}
    model_overrides: dict[str, Any] = {}

    for key, value in params.items():
        if key == model_name and isinstance(value, dict):
            model_overrides = dict(value)
            continue

        if isinstance(value, dict):
            continue

        shared[str(key)] = value

    return {**shared, **model_overrides}


def _resolve_from_config_root(raw_path: Any, config_root: Path) -> Path:
    if isinstance(raw_path, str) and raw_path.strip():
        candidate = Path(raw_path.strip()).expanduser()
        if not candidate.is_absolute():
            candidate = (config_root / candidate).resolve()
        return candidate
    return (config_root / "workspaces").resolve()


def _resolve_optional_from_config_root(raw_path: Any, config_root: Path) -> Path | None:
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None
    candidate = Path(raw_path.strip()).expanduser()
    if not candidate.is_absolute():
        candidate = (config_root / candidate).resolve()
    return candidate
