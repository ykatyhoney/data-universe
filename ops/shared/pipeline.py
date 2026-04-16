"""Typed envelopes for Redis Streams messages in the data pipeline.

Every stream has one schema. Workers / normalizer / self-validator encode
outgoing messages via ``encode_*`` and decode incoming via ``decode_*``. Adding
a new stream = new envelope class here + a new stream name in ``STREAMS``.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .clock import now_utc
from .schemas import ScrapeOutcome, ScrapeTaskMode, Source


class StreamName(StrEnum):
    """Canonical Redis Stream names. One consumer group per stream."""

    SCRAPE_TASKS = "scrape:tasks"
    SCRAPE_RESULTS = "scrape:results"
    VALIDATION_QUEUE = "validation:queue"
    ONDEMAND_REQUESTS = "ondemand:requests"


class ConsumerGroup(StrEnum):
    """Canonical consumer-group names — one group per logical consumer."""

    WORKERS = "workers"
    NORMALIZER = "normalizer"
    SELF_VALIDATOR = "self_validator"
    OD_FAST_LANE = "od_fast_lane"


class _Envelope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    envelope_version: int = 1
    trace_id: str | None = None
    ts: datetime = Field(default_factory=now_utc)


# ---------- scrape:tasks (strategist → workers) ---------- #


class ScrapeTaskEnvelope(_Envelope):
    task_id: str  # canonical 36-char UUID
    source: Source
    mode: ScrapeTaskMode
    label: str
    params: dict[str, Any] = Field(default_factory=dict)
    priority: int = 0  # >0 for on-demand fast-lane gap-fills


# ---------- scrape:results (workers → normalizer) ---------- #


class ScrapeResultEnvelope(_Envelope):
    """Raw scraper output handed to the normalizer. Not yet persisted.

    ``items`` holds the raw provider-shape blobs; the normalizer transforms
    each into the validator-parity shape before writing to
    ``stg_normalized_items``.
    """

    task_id: str
    worker_id: str
    source: Source
    outcome: ScrapeOutcome
    items: list[dict[str, Any]] = Field(default_factory=list)
    fetched_at: datetime = Field(default_factory=now_utc)
    started_at: datetime
    finished_at: datetime
    error: str | None = None


# ---------- validation:queue (promoter → self-validator) ---------- #


class ValidationEnvelope(_Envelope):
    """Sample of ``stg_normalized_items`` for the self-validator to re-scrape
    via the validator's own scraper and diff fields. Passed rows promote;
    failures flip the normalized row to ``quarantined``.
    """

    normalized_item_id: int
    source: Source
    uri: str


# ---------- ondemand:requests (miner protocol → OD fast lane) ---------- #


class OnDemandRequestEnvelope(_Envelope):
    """Validator-issued on-demand request. OD fast lane answers from the
    local store + priority-queued scrapes for gaps."""

    request_id: str
    source: Source
    # Open-ended filters; shape evolves as OD API grows. Kept loose so we
    # don't need a migration for every new filter.
    filters: dict[str, Any] = Field(default_factory=dict)
    limit: int = 100
    deadline_seconds: int = 10  # reward curve tops out at 0-120 s; aim <10 s


# ---------- stream → envelope-class map ---------- #

_REGISTRY: dict[StreamName, type[_Envelope]] = {
    StreamName.SCRAPE_TASKS: ScrapeTaskEnvelope,
    StreamName.SCRAPE_RESULTS: ScrapeResultEnvelope,
    StreamName.VALIDATION_QUEUE: ValidationEnvelope,
    StreamName.ONDEMAND_REQUESTS: OnDemandRequestEnvelope,
}


def envelope_class(stream: StreamName) -> type[_Envelope]:
    return _REGISTRY[stream]
