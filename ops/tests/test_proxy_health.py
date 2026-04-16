"""HealthProber — state transitions against a monkeypatched probe function."""

from __future__ import annotations

from collections.abc import AsyncIterator

import fakeredis.aioredis
import pytest
import pytest_asyncio

from proxy_pool import health as health_mod
from proxy_pool.backends.static_list import StaticListBackend, StaticListSettings
from proxy_pool.health import HealthProber
from proxy_pool.service import QUARANTINE_FAIL_THRESHOLD, ProxyPoolService


@pytest_asyncio.fixture
async def fake_redis(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[fakeredis.aioredis.FakeRedis]:
    from proxy_pool import service as svc_mod
    from shared import infra

    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(svc_mod, "get_redis", lambda: client)
    monkeypatch.setattr(infra, "get_redis", lambda: client)
    try:
        yield client
    finally:
        await client.aclose()


async def _make_svc() -> ProxyPoolService:
    svc = ProxyPoolService(backends=[StaticListBackend(StaticListSettings(endpoints="http://u:p@only:8000"))])
    await svc.sync_from_backends()
    return svc


@pytest.mark.asyncio
async def test_probe_success_keeps_healthy(
    fake_redis: fakeredis.aioredis.FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ = fake_redis
    svc = await _make_svc()

    async def _always_ok(*_args: object, **_kw: object) -> bool:
        return True

    monkeypatch.setattr(health_mod, "_probe_one", _always_ok)
    prober = HealthProber(svc, interval_seconds=60)
    results = await prober.probe_once()
    assert all(results.values())

    snap = await svc.snapshot()
    assert snap.proxies[0].state == "healthy"
    assert snap.proxies[0].fail_streak == 0
    assert snap.proxies[0].last_probe_at is not None


@pytest.mark.asyncio
async def test_three_failures_quarantine(
    fake_redis: fakeredis.aioredis.FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ = fake_redis
    svc = await _make_svc()

    async def _always_fail(*_args: object, **_kw: object) -> bool:
        return False

    monkeypatch.setattr(health_mod, "_probe_one", _always_fail)
    prober = HealthProber(svc, interval_seconds=60)
    for _ in range(QUARANTINE_FAIL_THRESHOLD):
        await prober.probe_once()

    snap = await svc.snapshot()
    assert snap.proxies[0].state == "quarantined"
    assert snap.proxies[0].quarantined_until is not None


@pytest.mark.asyncio
async def test_recovery_back_to_healthy(
    fake_redis: fakeredis.aioredis.FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Quarantined proxy → probe success → healthy + fail_streak=0."""
    _ = fake_redis
    svc = await _make_svc()

    toggle = {"ok": False}

    async def _probe(*_args: object, **_kw: object) -> bool:
        return toggle["ok"]

    monkeypatch.setattr(health_mod, "_probe_one", _probe)
    prober = HealthProber(svc, interval_seconds=60)

    # Force quarantine.
    for _ in range(QUARANTINE_FAIL_THRESHOLD):
        await prober.probe_once()
    snap = await svc.snapshot()
    assert snap.proxies[0].state == "quarantined"

    # Fast-forward "quarantined_until" by pushing it into the past.
    from datetime import timedelta

    from datastore.repositories import ProxyRepo
    from shared.clock import now_utc
    from shared.infra import get_session_factory

    async with get_session_factory()() as s, s.begin():
        await ProxyRepo.set_state(
            s,
            proxy_id=snap.proxies[0].id,
            state="quarantined",
            quarantined_until=now_utc() - timedelta(minutes=1),
        )

    # Now the probe succeeds — prober must flip back to healthy.
    toggle["ok"] = True
    await prober.probe_once()
    snap = await svc.snapshot()
    assert snap.proxies[0].state == "healthy"
    assert snap.proxies[0].fail_streak == 0
