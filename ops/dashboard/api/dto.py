"""Response DTOs for the REST surface.

Kept separate from ORM models so the wire shape can drift from the database
schema without forcing a migration. Frontend generates TypeScript types from
these via a small ``scripts/export_openapi.py`` helper (M1.E).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class _DTO(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class ProxyDTO(_DTO):
    id: UUID
    endpoint: str
    backend: str
    state: str
    session_id: str | None
    last_probe_at: datetime | None
    fail_streak: int
    quarantined_until: datetime | None
    created_at: datetime


class AccountDTO(_DTO):
    id: UUID
    source: str
    state: str
    pinned_proxy_id: UUID | None
    imported_at: datetime
    last_ok_at: datetime | None
    cooling_until: datetime | None
    created_at: datetime


class WorkerDTO(_DTO):
    id: str
    host: str
    state: str
    current_task_id: UUID | None
    browser_context_count: int
    memory_mb: float
    last_heartbeat_at: datetime | None
    created_at: datetime


class TaskDTO(_DTO):
    id: UUID
    source: str
    mode: str
    label: str
    priority: int
    state: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    worker_id: str | None
    outcome: str | None
    error: str | None


class DDJobDTO(_DTO):
    id: str
    source: str
    label: str
    keyword: str | None
    weight: float
    post_start: datetime | None
    post_end: datetime | None
    seen_at: datetime


class ChainStateDTO(_DTO):
    hotkey: str
    ts: datetime
    incentive: float
    stake: float
    credibility_p2p: float
    credibility_s3: float
    credibility_od: float
    rank: int


class OverviewDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proxies_by_state: dict[str, int]
    accounts_by_source_state: list[tuple[str, str, int]]
    workers_by_state: dict[str, int]
    tasks_by_state: dict[str, int]
    active_dd_jobs: int
    latest_chain_state: ChainStateDTO | None


class MetricSeries(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: str
    points: list[tuple[datetime, float]]  # (ts, value)


class MetricsSummaryDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    series: list[MetricSeries]
