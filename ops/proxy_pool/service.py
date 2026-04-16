"""Proxy pool service — the sole authority over ``ops.proxies`` state.

Responsibilities:
    1. ``sync_from_backends`` — pull endpoints from every configured backend
       and upsert them into the SQLite ``proxies`` table.
    2. ``lease`` — return a healthy proxy for (account_id, source), optionally
       binding to a sticky session stored in Redis with TTL.
    3. ``release`` — apply outcome to the selected proxy (fail-streak bump,
       quarantine at 3 fails, reset on ok); delete the lease from Redis.
    4. ``snapshot`` — full pool state for the dashboard.

All reads/writes to ``ops.proxies`` go through here; scrapers never touch
the DB directly.
"""

from __future__ import annotations

import asyncio
import secrets
from datetime import timedelta
from uuid import UUID

from datastore.models import Proxy
from datastore.repositories import ProxyRepo
from proxy_pool.backends.protocol import ProxyBackendAdapter
from proxy_pool.backends.static_list import masked_url
from proxy_pool.schemas import (
    LeaseOutcome,
    LeaseRequest,
    LeaseResponse,
    PoolState,
    ProxySnapshot,
    ReleaseRequest,
)
from shared.clock import now_utc
from shared.infra import get_redis, get_session_factory
from shared.logging import get_logger
from shared.metrics import proxy_pool_size, proxy_requests_total

log = get_logger(__name__)

# Thresholds.
QUARANTINE_MINUTES_BASE = 10
QUARANTINE_FAIL_THRESHOLD = 3
DEFAULT_LEASE_TTL_SECONDS = 60 * 60  # 1 h hard cap so stuck workers release

# Redis keys.
_STICKY_PREFIX = "proxy:sticky:"  # + f"{account_id}:{session_id}" → proxy_id
_LEASE_PREFIX = "proxy:lease:"  # + lease_id → JSON {proxy_id, account_id, issued_at}


def _sticky_key(account_id: UUID, session_id: str) -> str:
    return f"{_STICKY_PREFIX}{account_id}:{session_id}"


def _lease_key(lease_id: str) -> str:
    return f"{_LEASE_PREFIX}{lease_id}"


def _new_session_id() -> str:
    return secrets.token_urlsafe(9)  # 12-char URL-safe


def _new_lease_id() -> str:
    return secrets.token_urlsafe(12)  # 16-char URL-safe


class ProxyUnavailable(Exception):
    """Raised when no healthy proxy is available."""


class LeaseNotFound(Exception):
    """Raised on release of an unknown / expired lease."""


class ProxyPoolService:
    def __init__(self, backends: list[ProxyBackendAdapter]) -> None:
        if not backends:
            raise ValueError("ProxyPoolService requires at least one backend")
        self._backends = backends
        self._endpoints: dict[str, tuple[ProxyBackendAdapter, str]] = {}
        # proxy_id → (backend, raw_url)  — used for session injection on lease
        # Lazy-created in ``_write_guard`` so the lock is bound to the live
        # event loop (tests spin new loops per run).
        self._db_write_lock: asyncio.Lock | None = None

    def _write_guard(self) -> asyncio.Lock:
        if self._db_write_lock is None:
            self._db_write_lock = asyncio.Lock()
        return self._db_write_lock

    # ---------- sync ---------- #

    async def sync_from_backends(self) -> int:
        """Upsert proxies from every backend into the DB. Returns row count."""
        rows = 0
        endpoints: dict[str, tuple[ProxyBackendAdapter, str]] = {}
        factory = get_session_factory()
        async with self._write_guard(), factory() as s, s.begin():
            for backend in self._backends:
                for ep in await backend.load_endpoints():
                    await ProxyRepo.upsert(s, proxy_id=ep.id, endpoint=ep.url, backend=ep.backend.value)
                    endpoints[ep.id] = (backend, ep.url)
                    rows += 1
        self._endpoints = endpoints
        # Seed Prometheus gauge with a fresh read-out.
        async with factory() as s:
            counts = await ProxyRepo.counts_by_state(s)
        for state in ("healthy", "cooling", "quarantined", "disabled"):
            proxy_pool_size.labels(state=state).set(counts.get(state, 0))
        log.info("proxy_pool.sync", rows=rows, counts=counts)
        return rows

    # ---------- lease ---------- #

    async def lease(self, req: LeaseRequest) -> LeaseResponse:
        """Pick a healthy proxy for the caller, honouring sticky sessions."""
        sticky_key = (
            _sticky_key(req.account_id, req.session_id) if (req.account_id and req.session_id) else None
        )
        redis = get_redis()

        proxy_id: str | None = None
        if sticky_key:
            proxy_id = await redis.get(sticky_key)

        factory = get_session_factory()
        async with factory() as s:
            # If the sticky binding points at a still-healthy proxy, use it;
            # otherwise fall back to random healthy.
            if proxy_id:
                proxy = await ProxyRepo.get(s, proxy_id)
                if proxy is None or proxy.state != "healthy":
                    proxy_id = None  # binding is stale — pick fresh

            if not proxy_id:
                healthy = await ProxyRepo.healthy(s)
                if not healthy:
                    proxy_requests_total.labels(proxy_id="none", outcome="unavailable").inc()
                    raise ProxyUnavailable("no healthy proxies in pool")
                proxy = secrets.choice(list(healthy))
                proxy_id = proxy.id

        assert proxy_id is not None
        backend, raw_url = self._endpoints.get(proxy_id, (self._backends[0], proxy.endpoint if proxy else ""))

        # Stickiness: either caller supplied a session_id, or they asked for
        # sticky_minutes > 0 (in which case we mint one).
        session_id = req.session_id
        sticky_minutes = req.sticky_minutes
        if not session_id and sticky_minutes > 0:
            session_id = _new_session_id()
        url = raw_url
        if session_id:
            # Only inject if the backend supports it; otherwise the caller
            # gets the raw URL but the sticky mapping still pins the *exit*
            # behaviour to this proxy_id.
            endpoint_obj = None
            for ep in await backend.load_endpoints():
                if ep.id == proxy_id:
                    endpoint_obj = ep
                    break
            if endpoint_obj is not None and endpoint_obj.supports_sticky:
                url = backend.inject_session(endpoint_obj, session_id)

        now = now_utc()
        lease_id = _new_lease_id()
        expires_at = now + timedelta(seconds=DEFAULT_LEASE_TTL_SECONDS)

        # Persist lease in Redis (for release + live counters).
        await redis.setex(
            _lease_key(lease_id),
            DEFAULT_LEASE_TTL_SECONDS,
            f"{proxy_id}|{req.account_id or ''}|{source_tag(req)}",
        )
        # Sticky binding (if any) shares the session TTL.
        if sticky_key and session_id and sticky_minutes > 0:
            await redis.setex(sticky_key, sticky_minutes * 60, proxy_id)

        proxy_requests_total.labels(proxy_id=proxy_id, outcome="lease").inc()
        log.info(
            "proxy_pool.lease",
            proxy_id=proxy_id,
            source=req.source,
            sticky=bool(sticky_key),
        )
        return LeaseResponse(
            lease_id=lease_id,
            proxy_id=proxy_id,
            url=url,
            session_id=session_id,
            expires_at=expires_at,
        )

    # ---------- release ---------- #

    async def release(self, req: ReleaseRequest) -> None:
        redis = get_redis()
        raw = await redis.get(_lease_key(req.lease_id))
        if raw is None:
            proxy_requests_total.labels(proxy_id="none", outcome="lease_not_found").inc()
            raise LeaseNotFound(req.lease_id)
        parts = raw.split("|", 2)
        proxy_id = parts[0]

        # Apply outcome — serialise writers behind an app-level lock so a
        # burst of releases doesn't blow past SQLite's busy_timeout.
        factory = get_session_factory()
        async with self._write_guard(), factory() as s, s.begin():
            if req.outcome == LeaseOutcome.OK:
                await ProxyRepo.reset_fail_streak(s, proxy_id)
            else:
                streak = await ProxyRepo.bump_fail_streak(s, proxy_id)
                if streak >= QUARANTINE_FAIL_THRESHOLD:
                    await ProxyRepo.set_state(
                        s,
                        proxy_id=proxy_id,
                        state="quarantined",
                        quarantined_until=now_utc() + timedelta(minutes=QUARANTINE_MINUTES_BASE),
                    )
                    log.warning(
                        "proxy_pool.quarantined",
                        proxy_id=proxy_id,
                        fail_streak=streak,
                    )

        await redis.delete(_lease_key(req.lease_id))
        proxy_requests_total.labels(proxy_id=proxy_id, outcome=req.outcome.value).inc()

    # ---------- admin ---------- #

    async def set_disabled(self, proxy_id: str, disabled: bool) -> None:
        factory = get_session_factory()
        target_state = "disabled" if disabled else "healthy"
        async with self._write_guard(), factory() as s, s.begin():
            await ProxyRepo.set_state(s, proxy_id=proxy_id, state=target_state, fail_streak=0)
        log.info("proxy_pool.admin_set_state", proxy_id=proxy_id, state=target_state)

    # ---------- snapshot ---------- #

    async def snapshot(self) -> PoolState:
        factory = get_session_factory()
        async with factory() as s:
            proxies = await ProxyRepo.list_all(s)
            counts = await ProxyRepo.counts_by_state(s)
        items = [self._snap(p) for p in proxies]
        return PoolState(proxies=items, counts_by_state=counts)

    @staticmethod
    def _snap(p: Proxy) -> ProxySnapshot:
        return ProxySnapshot(
            id=p.id,
            url_masked=masked_url(p.endpoint),
            backend=p.backend,
            state=p.state,
            session_id=p.session_id,
            last_probe_at=p.last_probe_at,
            fail_streak=p.fail_streak,
            quarantined_until=p.quarantined_until,
            created_at=p.created_at,
        )

    # ---------- introspection for the health prober ---------- #

    def backend_for(self, proxy_id: str) -> tuple[ProxyBackendAdapter, str] | None:
        return self._endpoints.get(proxy_id)

    @property
    def endpoints(self) -> dict[str, tuple[ProxyBackendAdapter, str]]:
        return self._endpoints


def source_tag(req: LeaseRequest) -> str:
    return req.source[:16]


# ---------- module-level singleton ---------- #
# Constructed by dashboard-api lifespan; accessible to routes via get_service().

_service: ProxyPoolService | None = None


def set_service(svc: ProxyPoolService | None) -> None:
    global _service
    _service = svc


def get_service() -> ProxyPoolService:
    if _service is None:
        raise RuntimeError("ProxyPoolService not initialised — lifespan hasn't run")
    return _service


# ---------- dashboard-hits convenience ---------- #


async def _latest_snapshot_or_empty() -> PoolState:
    """Used by dashboard REST when no pool has started yet."""
    return PoolState(proxies=[], counts_by_state={})
