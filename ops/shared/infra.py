"""Shared connection singletons: SQLite (aiosqlite via SQLAlchemy) + Redis.

SQLite powers all ops + staging tables. One file per deployment, WAL mode
enabled for good read concurrency under a single writer (which matches our
dashboard-api-as-coordinator topology). Workers can still read in parallel;
writes funnel through the bridge adapter in M2.5 with a bounded batch size.

Redis is the task-queue + pub-sub substrate (streams land in M2.5).

Pool sizing env vars (all prefixed ``OPS_``):
- ``REDIS_MAX_CONNECTIONS``  : cap on the per-process Redis pool (default 100)
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

import redis.asyncio as aioredis
from pydantic import Field
from pydantic_settings import SettingsConfigDict
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool, StaticPool

from .config import Settings as _BaseSettings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class InfraSettings(_BaseSettings):
    """Extends :class:`common.config.Settings` with pool-tuning knobs."""

    model_config = SettingsConfigDict(
        env_prefix="OPS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    redis_max_connections: int = Field(default=100, ge=1, le=2000)


@lru_cache(maxsize=1)
def get_infra_settings() -> InfraSettings:
    return InfraSettings()


# ---------- SQLite ---------- #

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _is_memory_url(url: str) -> bool:
    """In-memory SQLite URLs need StaticPool so every call reuses the same
    connection (otherwise each checkout gets a *new* empty database)."""
    return ":memory:" in url or url.endswith("sqlite+aiosqlite://")


def get_engine() -> AsyncEngine:
    """Return the process-wide SQLAlchemy async engine (lazy-initialised)."""
    global _engine, _session_factory
    if _engine is None:
        s = get_infra_settings()
        url = s.database_url
        kwargs: dict[str, object] = {
            "future": True,
            "connect_args": {"check_same_thread": False},
        }
        if _is_memory_url(url):
            kwargs["poolclass"] = StaticPool
        else:
            # NullPool avoids the "connection reused across asyncio loops"
            # trap in tests; aiosqlite opens a fresh file handle per checkout,
            # which is cheap for local-file SQLite.
            kwargs["poolclass"] = NullPool
        _engine = create_async_engine(url, **kwargs)

        # Apply pragmas on every new connection — WAL + sensible defaults.
        @event.listens_for(_engine.sync_engine, "connect")
        def _set_sqlite_pragmas(dbapi_conn: object, _record: object) -> None:
            cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
            try:
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA synchronous=NORMAL")
                cursor.execute("PRAGMA busy_timeout=5000")
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA temp_store=MEMORY")
            finally:
                cursor.close()

        _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        get_engine()
    assert _session_factory is not None
    return _session_factory


async def dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


async def ping_database() -> bool:
    """Cheap ``SELECT 1`` round-trip. Used by health checks."""
    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


# ---------- Redis ---------- #

_redis: aioredis.Redis[str] | None = None


def get_redis() -> aioredis.Redis[str]:
    """Return the process-wide async Redis client (lazy-initialised)."""
    global _redis
    if _redis is None:
        s = get_infra_settings()
        _redis = aioredis.from_url(
            s.redis_url,
            max_connections=s.redis_max_connections,
            encoding="utf-8",
            decode_responses=True,
            health_check_interval=30,
            retry_on_timeout=True,
        )
    return _redis


async def dispose_redis() -> None:
    global _redis
    if _redis is not None:
        # `aclose` is the recommended method since redis 5.0.1; the types-redis
        # stubs haven't caught up yet, hence the targeted ignore.
        await _redis.aclose()  # type: ignore[attr-defined]
    _redis = None


async def ping_redis() -> bool:
    try:
        return bool(await get_redis().ping())
    except Exception:
        return False


# ---------- Session dependency for FastAPI ---------- #


async def get_session() -> AsyncIterator[AsyncSession]:
    factory = get_session_factory()
    async with factory() as session:
        yield session
