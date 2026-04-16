"""Hourly retention sweep. Runs inside dashboard-api's lifespan.

Prunes:
- ``metrics_snapshots``       older than ``OPS_METRIC_RETENTION_DAYS``  (default 30d)
- ``stg_raw_items``           older than ``OPS_STAGING_RAW_RETENTION_DAYS`` (default 7d)
- ``stg_normalized_items``    older than ``OPS_STAGING_NORM_RETENTION_DAYS`` (default 7d)
                              and in a terminal state (promoted / dropped /
                              quarantined). ``pending`` / ``validating`` are
                              never auto-pruned — they represent in-flight work.
- ``stg_dedup_index``         older than ``OPS_STAGING_DEDUP_RETENTION_DAYS`` (default 35d)
                              — outlives ``stg_normalized_items`` so we keep
                              suppressing duplicates past the freshness window.
- ``stg_validation_results``  older than ``OPS_STAGING_VAL_RETENTION_DAYS`` (default 30d)
- ``stg_promotion_log``       older than ``OPS_STAGING_PROMO_RETENTION_DAYS`` (default 30d)
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import timedelta
from typing import Any

from sqlalchemy import delete
from sqlalchemy.orm import DeclarativeBase

from datastore.models import (
    MetricsSnapshot,
    StgDedupIndex,
    StgNormalizedItem,
    StgPromotionLog,
    StgRawItem,
    StgValidationResult,
)
from shared.clock import now_utc
from shared.config import get_settings
from shared.infra import get_session_factory
from shared.logging import get_logger

log = get_logger(__name__)

_SWEEP_INTERVAL_SECONDS = 60 * 60  # hourly
_TERMINAL_STATES = ("promoted", "dropped", "quarantined")


def _retention_column(model: type[DeclarativeBase]) -> Any:
    if model is MetricsSnapshot:
        return MetricsSnapshot.ts
    if model is StgRawItem:
        return StgRawItem.fetched_at
    if model is StgDedupIndex:
        return StgDedupIndex.first_seen_at
    if model is StgValidationResult:
        return StgValidationResult.validated_at
    if model is StgPromotionLog:
        return StgPromotionLog.promoted_at
    raise ValueError(f"no retention column registered for {model!r}")


async def sweep_once() -> dict[str, int]:
    """Run all configured prune passes. Returns ``{table: rows_deleted}``."""
    s = get_settings()
    now = now_utc()
    deletions: dict[str, int] = {}

    plan: list[tuple[str, type[DeclarativeBase], int]] = [
        ("metrics_snapshots", MetricsSnapshot, max(1, s.metric_retention_days)),
        ("stg_raw_items", StgRawItem, max(1, s.staging_raw_retention_days)),
        ("stg_dedup_index", StgDedupIndex, max(1, s.staging_dedup_retention_days)),
        ("stg_validation_results", StgValidationResult, max(1, s.staging_val_retention_days)),
        ("stg_promotion_log", StgPromotionLog, max(1, s.staging_promo_retention_days)),
    ]

    factory = get_session_factory()
    try:
        async with factory() as session, session.begin():
            for table_name, model, days in plan:
                cutoff = now - timedelta(days=days)
                col = _retention_column(model)
                result = await session.execute(delete(model).where(col < cutoff))
                deletions[table_name] = int(getattr(result, "rowcount", 0) or 0)

            # stg_normalized_items: only terminal states are eligible for prune.
            cutoff = now - timedelta(days=max(1, s.staging_norm_retention_days))
            result = await session.execute(
                delete(StgNormalizedItem).where(
                    StgNormalizedItem.created_at < cutoff,
                    StgNormalizedItem.state.in_(_TERMINAL_STATES),
                )
            )
            deletions["stg_normalized_items"] = int(getattr(result, "rowcount", 0) or 0)

        non_zero = {k: v for k, v in deletions.items() if v}
        if non_zero:
            log.info("retention.swept", **non_zero)
        return deletions
    except Exception as e:
        log.warning("retention.sweep_failed", error=str(e))
        return deletions


class RetentionSweeper:
    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        # Bound to the running loop in start().
        self._stopping: asyncio.Event | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stopping = asyncio.Event()
        self._task = asyncio.create_task(self._loop(), name="retention_sweeper")
        log.info("retention.start", interval_s=_SWEEP_INTERVAL_SECONDS)

    async def stop(self) -> None:
        if self._stopping is not None:
            self._stopping.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self._stopping = None
        log.info("retention.stop")

    async def _loop(self) -> None:
        assert self._stopping is not None
        stopping = self._stopping
        while not stopping.is_set():
            try:
                await sweep_once()
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.warning("retention.loop_failed", error=str(e))
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stopping.wait(), timeout=_SWEEP_INTERVAL_SECONDS)


sweeper = RetentionSweeper()
