"""Typed data models shared across ops services.

These are the wire/event shapes — not DB models (those live in ops/storage/
and land in M2.5). Every service produces or consumes these; any change here
is a cross-service contract change.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from .clock import now_utc


class Source(StrEnum):
    X = "x"
    REDDIT = "reddit"
    YOUTUBE = "youtube"


class ProxyState(StrEnum):
    HEALTHY = "healthy"
    COOLING = "cooling"
    QUARANTINED = "quarantined"
    DISABLED = "disabled"


class AccountState(StrEnum):
    NEW = "new"
    WARMING = "warming"
    ACTIVE = "active"
    COOLING = "cooling"
    QUARANTINED = "quarantined"
    RETIRED = "retired"


class TaskState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkerState(StrEnum):
    IDLE = "idle"
    BUSY = "busy"
    DRAINING = "draining"
    OFFLINE = "offline"


class _Base(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Proxy(_Base):
    id: UUID = Field(default_factory=uuid4)
    endpoint: str
    backend: str = "static_list"
    state: ProxyState = ProxyState.HEALTHY
    session_id: str | None = None
    last_probe_at: datetime | None = None
    fail_streak: int = 0
    quarantined_until: datetime | None = None


class Account(_Base):
    id: UUID = Field(default_factory=uuid4)
    source: Source
    state: AccountState = AccountState.NEW
    pinned_proxy_id: UUID | None = None
    imported_at: datetime = Field(default_factory=now_utc)
    last_ok_at: datetime | None = None
    cooling_until: datetime | None = None


class ScrapeTaskMode(StrEnum):
    SEARCH = "search"
    PROFILE = "profile"
    PERMALINK = "permalink"
    CHANNEL = "channel"
    COMMENT = "comment"


class ScrapeTask(_Base):
    id: UUID = Field(default_factory=uuid4)
    source: Source
    mode: ScrapeTaskMode
    label: str
    params: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    priority: int = 0  # higher = earlier; OD fast-lane sets e.g. 100
    created_at: datetime = Field(default_factory=now_utc)


class ScrapeOutcome(StrEnum):
    OK = "ok"
    EMPTY = "empty"
    BLOCKED = "blocked"
    RATE_LIMITED = "rate_limited"
    ERROR = "error"


class ScrapeResult(_Base):
    task_id: UUID
    worker_id: str
    source: Source
    outcome: ScrapeOutcome
    item_count: int = 0
    duration_seconds: float = 0.0
    started_at: datetime
    finished_at: datetime
    error: str | None = None


class WorkerHeartbeat(_Base):
    worker_id: str
    host: str
    state: WorkerState
    current_task_id: UUID | None = None
    browser_context_count: int = 0
    memory_mb: float = 0.0
    ts: datetime = Field(default_factory=now_utc)


# Live event envelope used for the dashboard WebSocket and Redis pub/sub
# lives in ``common.events`` (richer discriminated union).
