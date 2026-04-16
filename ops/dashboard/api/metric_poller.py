"""Background task that scrapes Prometheus-format ``/metrics`` endpoints and
materialises the values as rows in ``ops.metrics_snapshots``.

Runs inside dashboard-api's lifespan. One task loop per process; polls every
configured target concurrently each tick.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import defaultdict
from collections.abc import Iterable
from typing import Any

import httpx
from prometheus_client.parser import text_string_to_metric_families

from datastore.models import MetricsSnapshot
from shared.clock import now_utc
from shared.config import get_settings
from shared.infra import get_session_factory
from shared.logging import get_logger

log = get_logger(__name__)

# Internal suffix Prometheus histograms emit; we keep *_bucket / *_count / *_sum
# as first-class series rather than trying to reconstruct the histogram — the
# dashboard charts are all gauges / counters.


def _parse_targets(raw: str) -> list[str]:
    return [t.strip() for t in raw.split(",") if t.strip()]


async def _fetch_one(client: httpx.AsyncClient, url: str) -> str | None:
    try:
        resp = await client.get(url, timeout=5.0)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        log.warning("metric_poller.fetch_failed", url=url, error=str(e))
        return None


def _flatten(text: str, cap_per_metric: int) -> Iterable[tuple[str, dict[str, str], float]]:
    """Yield ``(metric_name, labels, value)`` triples.

    Drops NaN / ``+Inf`` samples. Caps series count per metric to avoid
    cardinality explosion (rotating labels, e.g. per-UUID proxy ids).
    """
    per_metric_count: dict[str, int] = defaultdict(int)
    for family in text_string_to_metric_families(text):
        for sample in family.samples:
            name = sample.name
            value = sample.value
            if value != value or value == float("inf") or value == float("-inf"):
                continue
            if per_metric_count[name] >= cap_per_metric:
                if per_metric_count[name] == cap_per_metric:
                    log.warning(
                        "metric_poller.cardinality_capped",
                        metric=name,
                        cap=cap_per_metric,
                    )
                per_metric_count[name] += 1
                continue
            per_metric_count[name] += 1
            yield name, dict(sample.labels), float(value)


async def _persist(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    factory = get_session_factory()
    async with factory() as session, session.begin():
        session.add_all(MetricsSnapshot(**r) for r in rows)
    return len(rows)


async def poll_once(targets: list[str], cap_per_metric: int) -> int:
    """One tick. Returns number of rows written."""
    async with httpx.AsyncClient() as client:
        texts = await asyncio.gather(*(_fetch_one(client, t) for t in targets))

    ts = now_utc()
    rows: list[dict[str, Any]] = []
    for text in texts:
        if not text:
            continue
        for name, labels, value in _flatten(text, cap_per_metric):
            rows.append({"ts": ts, "metric": name, "labels": labels, "value": value})
    try:
        return await _persist(rows)
    except Exception as e:
        log.warning("metric_poller.persist_failed", rows=len(rows), error=str(e))
        return 0


class MetricPoller:
    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        # Created in start() so the Event is bound to the running loop
        # (critical when the process spawns multiple loops, e.g. in tests).
        self._stopping: asyncio.Event | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stopping = asyncio.Event()
        self._task = asyncio.create_task(self._loop(), name="metric_poller")
        log.info("metric_poller.start")

    async def stop(self) -> None:
        if self._stopping is not None:
            self._stopping.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self._stopping = None
        log.info("metric_poller.stop")

    async def _loop(self) -> None:
        assert self._stopping is not None
        stopping = self._stopping
        s = get_settings()
        targets = _parse_targets(s.metric_targets)
        interval = max(1, s.metric_poll_seconds)
        cap = max(1, s.metric_max_series_per_metric)
        log.info("metric_poller.config", targets=targets, interval_s=interval, series_cap=cap)

        while not stopping.is_set():
            try:
                written = await poll_once(targets, cap)
                if written:
                    log.debug("metric_poller.tick", rows=written)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.warning("metric_poller.tick_failed", error=str(e))
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stopping.wait(), timeout=interval)


poller = MetricPoller()
