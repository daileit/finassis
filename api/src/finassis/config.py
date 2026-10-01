"""Configuration: environment variables for secrets/deployment, optional YAML for behaviour."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]
# In containers the package is installed into site-packages; FINASSIS_API_DIR points at the copied api/ dir.
API_DIR = Path(os.environ.get("FINASSIS_API_DIR", str(REPO_ROOT / "api")))


class Behaviour(BaseModel):
    """Hot-reloadable behaviour from config.yaml (defaults here)."""

    raw_text_max_bytes: int = 32 * 1024
    interaction_default_ttl_days: int = 14
    pair_detection_window_days: int = 2
    rollup_reclose_interval_s: int = 3600
    snapshot_hour_utc: int = 19  # 02:00 Asia/Ho_Chi_Minh
    partitions_months_ahead: int = 3
    free_plan_limits: dict[str, dict[str, int]] = Field(
        default_factory=lambda: {
            "api.request": {"month": 20000},
            "raw.item": {"month": 2000},
            "compiler.call": {"month": 10},
            "tag.custom": {"cap": 50},
            "mcp.call": {"month": 5000},
        }
    )
    metered_kinds: list[str] = Field(default_factory=lambda: ["raw.item", "compiler.call", "tag.custom"])


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FINASSIS_", env_file=".env", extra="ignore")

    env: str = "dev"
    log_level: str = "INFO"
    public_base_url: str = "http://localhost:8000"
    api_prefix: str = "/api/v1"

    database_url: str = "postgresql://finassis:finassis@localhost:5432/finassis"
    database_url_app: str | None = None  # optional separate login for request traffic; defaults to database_url
    database_url_admin: str | None = None  # optional separate login for privileged work; defaults to database_url
    db_pool_min: int = 2
    db_pool_max: int = 10

    redis_url: str = "redis://localhost:6379/0"

    telegram_bot_token: str | None = None
    telegram_webhook_secret: str | None = None
    channel_telegram_mode: str = "inprocess"

    bootstrap_admin_telegram_id: str | None = None
    bootstrap_admin_api_key: str | None = None

    seeds_dir: Path = REPO_ROOT / "seeds"
    i18n_dir: Path = REPO_ROOT / "i18n"
    schema_sql: Path = API_DIR / "db" / "schema.sql"
    config_yaml: Path | None = None
    api_dir: Path = API_DIR

    ai_api_key: str | None = None
    ai_model: str = "claude-sonnet-4-5"

    @property
    def app_dsn(self) -> str:
        return self.database_url_app or self.database_url

    @property
    def admin_dsn(self) -> str:
        return self.database_url_admin or self.database_url

    def load_behaviour(self) -> Behaviour:
        path = self.config_yaml or Path(os.environ.get("FINASSIS_CONFIG_YAML", API_DIR / "config.yaml"))
        data: dict[str, Any] = {}
        if path and Path(path).exists():
            with Path(path).open(encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
        return Behaviour(**data)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


@lru_cache(maxsize=1)
def get_behaviour() -> Behaviour:
    return get_settings().load_behaviour()
