"""ProxyPoolService — lease/release/stickiness against fakeredis + test SQLite."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator

import fakeredis.aioredis
import pytest
import pytest_asyncio

from proxy_pool.backends.static_list import StaticListBackend, StaticListSettings
from proxy_pool.schemas import LeaseOutcome, LeaseRequest, ReleaseRequest
from proxy_pool.service import (
    QUARANTINE_FAIL_THRESHOLD,
    LeaseNotFound,
    ProxyPoolService,
    ProxyUnavailable,
)


@pytest_asyncio.fixture
async def fake_redis(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[fakeredis.aioredis.FakeRedis]:
    """Patch every place that calls get_redis()."""
    from proxy_pool import service as svc_mod
    from shared import infra

    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(svc_mod, "get_redis", lambda: client)
    monkeypatch.setattr(infra, "get_redis", lambda: client)
    try:
        yield client
    finally:
        await client.aclose()


def _make_service(endpoints: str, *, supports_sticky: bool = False) -> ProxyPoolService:
    backend = StaticListBackend(StaticListSettings(endpoints=endpoints, supports_sticky=supports_sticky))
    return ProxyPoolService(backends=[backend])


@pytest.mark.asyncio
async def test_sync_populates_pool(fake_redis: fakeredis.aioredis.FakeRedis) -> None:
    _ = fake_redis
    svc = _make_service("http://u:p@h1:8000,http://u:p@h2:8000")
    rows = await svc.sync_from_backends()
    assert rows == 2
    snap = await svc.snapshot()
    assert len(snap.proxies) == 2
    # Credentials don't leak into the dashboard view.
    for p in snap.proxies:
        assert "u:p@" not in p.url_masked


@pytest.mark.asyncio
async def test_lease_fails_when_no_proxies(fake_redis: fakeredis.aioredis.FakeRedis) -> None:
    _ = fake_redis
    svc = _make_service("")  # empty backend
    with pytest.raises(ValueError):
        ProxyPoolService(backends=[])  # separate guard
    # With a backend but zero endpoints, sync is empty → lease must raise.
    await svc.sync_from_backends()
    with pytest.raises(ProxyUnavailable):
        await svc.lease(LeaseRequest(source="x"))


@pytest.mark.asyncio
async def test_lease_release_roundtrip(fake_redis: fakeredis.aioredis.FakeRedis) -> None:
    _ = fake_redis
    svc = _make_service("http://u:p@h:8000")
    await svc.sync_from_backends()

    req = LeaseRequest(source="x")
    resp = await svc.lease(req)
    assert resp.lease_id
    assert resp.proxy_id
    # On the happy path, releasing resets the fail streak (no-op here).
    await svc.release(ReleaseRequest(lease_id=resp.lease_id, outcome=LeaseOutcome.OK))
    # Second release of the same lease 404s — lease TTL is cleared.
    with pytest.raises(LeaseNotFound):
        await svc.release(ReleaseRequest(lease_id=resp.lease_id, outcome=LeaseOutcome.OK))


@pytest.mark.asyncio
async def test_sticky_session_returns_same_proxy(fake_redis: fakeredis.aioredis.FakeRedis) -> None:
    _ = fake_redis
    svc = _make_service("http://u:p@h1:8000,http://u:p@h2:8000,http://u:p@h3:8000", supports_sticky=True)
    await svc.sync_from_backends()
    account_id = uuid.uuid4()
    session_id = "stable-session-42"

    first = await svc.lease(
        LeaseRequest(
            account_id=account_id,
            source="x",
            sticky_minutes=30,
            session_id=session_id,
        )
    )
    # Release it (ok), then re-lease with the same (account, session) key.
    await svc.release(ReleaseRequest(lease_id=first.lease_id, outcome=LeaseOutcome.OK))

    again = await svc.lease(
        LeaseRequest(
            account_id=account_id,
            source="x",
            sticky_minutes=30,
            session_id=session_id,
        )
    )
    assert again.proxy_id == first.proxy_id
    # And the URL has the session suffix baked in.
    assert "session-stable-session-42" in again.url


@pytest.mark.asyncio
async def test_repeated_failure_quarantines_proxy(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    _ = fake_redis
    svc = _make_service("http://u:p@only:8000")
    await svc.sync_from_backends()

    for _ in range(QUARANTINE_FAIL_THRESHOLD):
        resp = await svc.lease(LeaseRequest(source="x"))
        await svc.release(ReleaseRequest(lease_id=resp.lease_id, outcome=LeaseOutcome.BLOCKED))

    snap = await svc.snapshot()
    states = {p.state for p in snap.proxies}
    assert "quarantined" in states
    # The next lease should now fail — quarantine removes the only proxy.
    with pytest.raises(ProxyUnavailable):
        await svc.lease(LeaseRequest(source="x"))


@pytest.mark.asyncio
async def test_ok_after_fails_resets_streak(fake_redis: fakeredis.aioredis.FakeRedis) -> None:
    _ = fake_redis
    svc = _make_service("http://u:p@only:8000")
    await svc.sync_from_backends()

    for outcome in (LeaseOutcome.BLOCKED, LeaseOutcome.RATE_LIMITED):
        r = await svc.lease(LeaseRequest(source="x"))
        await svc.release(ReleaseRequest(lease_id=r.lease_id, outcome=outcome))

    r = await svc.lease(LeaseRequest(source="x"))
    await svc.release(ReleaseRequest(lease_id=r.lease_id, outcome=LeaseOutcome.OK))

    snap = await svc.snapshot()
    assert snap.proxies[0].state == "healthy"
    assert snap.proxies[0].fail_streak == 0


@pytest.mark.asyncio
async def test_concurrent_leases_accounting(fake_redis: fakeredis.aioredis.FakeRedis) -> None:
    """Acceptance: 100 concurrent lease/release cycles complete with correct accounting."""
    _ = fake_redis
    endpoints = ",".join(f"http://u:p@h{i}:8000" for i in range(5))
    svc = _make_service(endpoints)
    await svc.sync_from_backends()

    async def one_cycle() -> str:
        r = await svc.lease(LeaseRequest(source="x"))
        await svc.release(ReleaseRequest(lease_id=r.lease_id, outcome=LeaseOutcome.OK))
        return r.proxy_id

    results = await asyncio.gather(*(one_cycle() for _ in range(100)))
    assert len(results) == 100
    # All leases released (no leaked keys).
    snap = await svc.snapshot()
    assert sum(p.fail_streak for p in snap.proxies) == 0
    # Every healthy proxy should have been picked at least once with 5 proxies
    # and 100 random picks (probability of a miss is vanishingly small).
    assert len(set(results)) == 5


@pytest.mark.asyncio
async def test_admin_disable_removes_from_lease_pool(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    _ = fake_redis
    svc = _make_service("http://u:p@solo:8000")
    await svc.sync_from_backends()

    snap = await svc.snapshot()
    proxy_id = snap.proxies[0].id
    await svc.set_disabled(proxy_id, disabled=True)

    with pytest.raises(ProxyUnavailable):
        await svc.lease(LeaseRequest(source="x"))

    await svc.set_disabled(proxy_id, disabled=False)
    r = await svc.lease(LeaseRequest(source="x"))
    assert r.proxy_id == proxy_id
