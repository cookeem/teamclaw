from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import os

import yaml
from pydantic import BaseModel, ConfigDict, Field


class AppSection(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = "teamclaw-backend"
    locale: str = "en"
    env: str = "development"
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = True
    frontend_base_url: str = "http://localhost:8080/frontend"


class DatabaseSection(BaseModel):
    model_config = ConfigDict(extra="ignore")

    host: str = "localhost"
    port: int = 5432
    user: str = "teamclaw"
    password: str = "teamclaw_dev_password"
    name: str = "teamclaw"

    @property
    def sqlalchemy_url(self) -> str:
        host = "127.0.0.1" if self.host == "localhost" else self.host
        return f"postgresql+psycopg://{self.user}:{self.password}@{host}:{self.port}/{self.name}"


class SmtpSection(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    host: str = "smtp.example.com"
    port: int = 587
    username: str = ""
    password: str = ""
    from_email: str = "noreply@teamclaw.local"
    from_name: str = "TeamClaw"
    use_tls: bool = True
    use_ssl: bool = False
    timeout_seconds: int = 15


class AuthSection(BaseModel):
    model_config = ConfigDict(extra="ignore")

    expose_password_reset_debug: bool = False


class SkillStorageSection(BaseModel):
    model_config = ConfigDict(extra="ignore")

    userskills_dir: str = "./userskills"
    preskills_dir: str = "./preskills"
    skills_dir: str = "./skills"
    agentskills_dir: str = "./agentskills"
    conversationskills_dir: str = "./conversationskills"


class Settings(BaseModel):
    model_config = ConfigDict(extra="allow")

    app: AppSection = Field(default_factory=AppSection)
    database: DatabaseSection = Field(default_factory=DatabaseSection)
    smtp: SmtpSection = Field(default_factory=SmtpSection)
    auth: AuthSection = Field(default_factory=AuthSection)
    skill_storage: SkillStorageSection = Field(default_factory=SkillStorageSection)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    config_path = Path(os.getenv("TEAMCLAW_CONFIG", "config.yaml"))
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with config_path.open("r", encoding="utf-8") as fp:
        raw = yaml.safe_load(fp) or {}

    db_overrides = {
        "host": os.getenv("TEAMCLAW_DB_HOST"),
        "port": os.getenv("TEAMCLAW_DB_PORT"),
        "user": os.getenv("TEAMCLAW_DB_USER"),
        "password": os.getenv("TEAMCLAW_DB_PASSWORD"),
        "name": os.getenv("TEAMCLAW_DB_NAME"),
    }
    if any(db_overrides.values()):
        database = dict(raw.get("database") or {})
        for key, value in db_overrides.items():
            if value is None:
                continue
            if key == "port":
                try:
                    database[key] = int(value)
                except ValueError:
                    continue
            else:
                database[key] = value
        raw["database"] = database

    frontend_url = os.getenv("TEAMCLAW_FRONTEND_BASE_URL")
    if frontend_url:
        app_cfg = dict(raw.get("app") or {})
        app_cfg["frontend_base_url"] = frontend_url
        raw["app"] = app_cfg

    return Settings.model_validate(raw)
