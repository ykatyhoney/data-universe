from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


class Settings(BaseSettings):
    """Single 12-factor config object for every ops service.

    Reads from environment variables prefixed with ``OPS_``.
    """

    model_config = SettingsConfigDict(
        env_prefix="OPS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    service_name: str = Field(default="ops", description="identifier used in logs and metrics")
    log_level: LogLevel = "INFO"

    redis_url: str = "redis://localhost:6379/0"

    # SQLite — one file, no server. All ops + staging tables live here.
    # Default path is relative to the process CWD (pm2 sets CWD to ``ops/``).
    # For production override with an absolute path.
    database_url: str = "sqlite+aiosqlite:///./ops.db"

    dashboard_api_host: str = "0.0.0.0"
    dashboard_api_port: int = 8000

    exporter_host: str = "0.0.0.0"
    exporter_port: int = 9108

    # Cookie auth — set OPS_DASHBOARD_PASSWORD in the environment; clients POST
    # to /api/auth/login with {"password": ...} to receive the cookie.
    dashboard_password: str = "change-me"
    dashboard_session_secret: str = "dev-secret-change-me-32-bytes-minimum-zzzz"
    dashboard_session_ttl_seconds: int = 60 * 60 * 24 * 7  # 7 days

    # Metric snapshot poller (M2). Comma-separated list of URLs to scrape.
    metric_targets: str = "http://localhost:8000/metrics"
    metric_poll_seconds: int = 15
    metric_retention_days: int = 30
    metric_max_series_per_metric: int = 50

    # Staging table retention (M2.5). Stay generous: dedup outlives normalized
    # so we keep suppressing duplicates past the freshness window.
    staging_raw_retention_days: int = 7
    staging_norm_retention_days: int = 7
    staging_dedup_retention_days: int = 35
    staging_val_retention_days: int = 30
    staging_promo_retention_days: int = 30


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
