"""Typed event envelope for the live dashboard.

Every service (workers, proxy-pool, account-pool, strategist, self-validator)
publishes ``Event`` objects onto Redis pub/sub channel ``events:live``. The
dashboard-api subscribes, rebroadcasts to connected WebSocket clients, and
persists derivative rows in Postgres (M1.B).

Discriminated union on ``kind`` — adding a new event type means adding a
pydantic class here and extending ``AnyEvent``. Consumers match on ``kind``.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from .clock import now_utc
from .schemas import (
    AccountState,
    ProxyState,
    ScrapeOutcome,
    ScrapeTaskMode,
    Source,
    TaskState,
    WorkerState,
)

LIVE_CHANNEL = "events:live"


class EventKind(StrEnum):
    PROXY_STATE_CHANGED = "proxy.state_changed"
    ACCOUNT_STATE_CHANGED = "account.state_changed"
    WORKER_HEARTBEAT = "worker.heartbeat"
    TASK_STARTED = "task.started"
    TASK_FINISHED = "task.finished"
    METRIC_TICK = "metric.tick"


class _EventBase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ts: datetime = Field(default_factory=now_utc)
    trace_id: str | None = None


class ProxyStateChanged(_EventBase):
    kind: Literal[EventKind.PROXY_STATE_CHANGED] = EventKind.PROXY_STATE_CHANGED
    proxy_id: UUID
    from_state: ProxyState | None
    to_state: ProxyState
    reason: str | None = None


class AccountStateChanged(_EventBase):
    kind: Literal[EventKind.ACCOUNT_STATE_CHANGED] = EventKind.ACCOUNT_STATE_CHANGED
    account_id: UUID
    source: Source
    from_state: AccountState | None
    to_state: AccountState
    reason: str | None = None


class WorkerHeartbeat(_EventBase):
    kind: Literal[EventKind.WORKER_HEARTBEAT] = EventKind.WORKER_HEARTBEAT
    worker_id: str
    host: str
    state: WorkerState
    current_task_id: UUID | None = None
    browser_context_count: int = 0
    memory_mb: float = 0.0


class TaskStarted(_EventBase):
    kind: Literal[EventKind.TASK_STARTED] = EventKind.TASK_STARTED
    task_id: UUID
    source: Source
    mode: ScrapeTaskMode
    label: str
    worker_id: str


class TaskFinished(_EventBase):
    kind: Literal[EventKind.TASK_FINISHED] = EventKind.TASK_FINISHED
    task_id: UUID
    source: Source
    outcome: ScrapeOutcome
    state: TaskState
    item_count: int = 0
    duration_seconds: float = 0.0
    error: str | None = None


class MetricTick(_EventBase):
    kind: Literal[EventKind.METRIC_TICK] = EventKind.METRIC_TICK
    metric: str
    labels: dict[str, str] = Field(default_factory=dict)
    value: float


AnyEvent = Annotated[
    ProxyStateChanged | AccountStateChanged | WorkerHeartbeat | TaskStarted | TaskFinished | MetricTick,
    Field(discriminator="kind"),
]


_ADAPTER: TypeAdapter[AnyEvent] = TypeAdapter(AnyEvent)


def encode(event: AnyEvent) -> str:
    """Serialise an event to the JSON wire format used on Redis + WebSockets."""
    return event.model_dump_json()


def decode(payload: str | bytes) -> AnyEvent:
    """Parse a wire payload back into the correct subclass (dispatches on ``kind``)."""
    return _ADAPTER.validate_json(payload)
