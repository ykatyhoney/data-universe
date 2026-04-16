"""Proxy health prober.

Every :data:`PROBE_INTERVAL_SECONDS`:
- Iterate all non-disabled proxies.
- Send a HEAD to :data:`PROBE_URL` through the proxy.
- On success: reset fail_streak, set state=healthy, update last_probe_at.
- On failure: bump fail_streak; at ``QUARANTINE_FAIL_THRESHOLD`` → quarantine
  for :data:`QUARANTINE_MINUTES_BASE`.
- If state=quarantined AND now > quarantined_until: probe; on success →
  healthy, fail_streak=0. On failure → extend quarantine (capped at 60 min).

Lightweight on purpose: we don't hit target sites, we hit a neutral HTTP
endpoint that returns 204 No Content (Google's connectivity probe).
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta

import httpx

from datastore.repositories import ProxyRepo
from proxy_pool.service import (
    QUARANTINE_FAIL_THRESHOLD,
    QUARANTINE_MINUTES_BASE,
    ProxyPoolService,
)
from shared.clock import now_utc
from shared.infra import get_session_factory
from shared.logging import get_logger
from shared.metrics import proxy_pool_size, proxy_requests_total

log = get_logger(__name__)

PROBE_URL = "https://www.gstatic.com/generate_204"
PROBE_TIMEOUT_SECONDS = 5.0
PROBE_INTERVAL_SECONDS = 120  # 2 min per M3 spec
QUARANTINE_MAX_MINUTES = 60


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


async def _probe_one(client_factory: type[httpx.AsyncClient], url: str) -> bool:
    try:
        async with client_factory(
            proxy=url,
            timeout=PROBE_TIMEOUT_SECONDS,
            follow_redirects=False,
        ) as client:
            resp = await client.head(PROBE_URL)
            return 200 <= resp.status_code < 400
    except Exception as e:
        log.debug("proxy_pool.probe_failed", error=str(e))
        return False


class HealthProber:
    def __init__(
        self,
        service: ProxyPoolService,
        *,
        probe_url: str = PROBE_URL,
        interval_seconds: int = PROBE_INTERVAL_SECONDS,
    ) -> None:
        self._service = service
        self._probe_url = probe_url
        self._interval = max(5, interval_seconds)
        self._task: asyncio.Task[None] | None = None
        self._stopping: asyncio.Event | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stopping = asyncio.Event()
        self._task = asyncio.create_task(self._loop(), name="proxy_pool.health")
        log.info("proxy_pool.health.start", interval_s=self._interval)

    async def stop(self) -> None:
        if self._stopping is not None:
            self._stopping.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self._stopping = None
        log.info("proxy_pool.health.stop")

    async def probe_once(self) -> dict[str, bool]:
        """Probe every non-disabled proxy once. Returns ``{proxy_id: healthy}``."""
        factory = get_session_factory()
        async with factory() as s:
            proxies = await ProxyRepo.list_all(s)

        results: dict[str, bool] = {}
        now = now_utc()
        for p in proxies:
            if p.state == "disabled":
                continue
            # SQLite loses tz info on ``DateTime(timezone=True)``; coerce UTC
            # before comparing with ``now_utc``.
            qu = _as_utc(p.quarantined_until)
            if p.state == "quarantined" and qu and qu > now:
                continue
            binding = self._service.backend_for(p.id)
            if binding is None:
                continue
            _backend, raw_url = binding
            ok = await _probe_one(httpx.AsyncClient, raw_url)
            results[p.id] = ok
            await self._apply(p.id, ok, previous_state=p.state, previous_streak=p.fail_streak)

        # Refresh state-counts gauge.
        async with factory() as s:
            counts = await ProxyRepo.counts_by_state(s)
        for state in ("healthy", "cooling", "quarantined", "disabled"):
            proxy_pool_size.labels(state=state).set(counts.get(state, 0))
        return results

    async def _apply(self, proxy_id: str, ok: bool, *, previous_state: str, previous_streak: int) -> None:
        factory = get_session_factory()
        async with factory() as s, s.begin():
            if ok:
                await ProxyRepo.set_state(
                    s,
                    proxy_id=proxy_id,
                    state="healthy",
                    fail_streak=0,
                    last_probe_at=now_utc(),
                    quarantined_until=None,
                )
                outcome = "probe_ok"
            else:
                new_streak = previous_streak + 1
                if new_streak >= QUARANTINE_FAIL_THRESHOLD:
                    # Exponential cooldown capped at QUARANTINE_MAX_MINUTES.
                    cooldown = min(
                        QUARANTINE_MAX_MINUTES,
                        QUARANTINE_MINUTES_BASE * (2 ** (new_streak - QUARANTINE_FAIL_THRESHOLD)),
                    )
                    await ProxyRepo.set_state(
                        s,
                        proxy_id=proxy_id,
                        state="quarantined",
                        fail_streak=new_streak,
                        last_probe_at=now_utc(),
                        quarantined_until=now_utc() + timedelta(minutes=cooldown),
                    )
                else:
                    await ProxyRepo.set_state(
                        s,
                        proxy_id=proxy_id,
                        state="cooling",
                        fail_streak=new_streak,
                        last_probe_at=now_utc(),
                    )
                outcome = "probe_fail"
        proxy_requests_total.labels(proxy_id=proxy_id, outcome=outcome).inc()
        if previous_state != ("healthy" if ok else previous_state):
            log.info(
                "proxy_pool.state_transition",
                proxy_id=proxy_id,
                from_state=previous_state,
                to_state="healthy"
                if ok
                else ("cooling" if previous_streak + 1 < QUARANTINE_FAIL_THRESHOLD else "quarantined"),
            )

    async def _loop(self) -> None:
        assert self._stopping is not None
        stopping = self._stopping
        while not stopping.is_set():
            try:
                await self.probe_once()
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.warning("proxy_pool.health.loop_failed", error=str(e))
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stopping.wait(), timeout=self._interval)
