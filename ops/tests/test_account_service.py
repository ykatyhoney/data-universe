"""AccountPoolService — import, lease/release, state machine, budget."""

from __future__ import annotations

from collections.abc import AsyncIterator

import fakeredis.aioredis
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet

from account_pool.crypto import CookieSealer
from account_pool.schemas import (
    AccountImport,
    AccountLeaseRequest,
    AccountReleaseRequest,
    LeaseOutcome,
)
from account_pool.service import (
    BUDGET_PER_MINUTE,
    COOLING_MINUTES,
    QUARANTINE_FAIL_THRESHOLD,
    AccountLeaseNotFound,
    AccountPoolService,
    AccountUnavailable,
)


@pytest_asyncio.fixture
async def fake_redis(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[fakeredis.aioredis.FakeRedis]:
    from account_pool import service as acct_svc
    from shared import infra

    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(acct_svc, "get_redis", lambda: client)
    monkeypatch.setattr(infra, "get_redis", lambda: client)
    try:
        yield client
    finally:
        await client.aclose()


def _mk_service() -> AccountPoolService:
    return AccountPoolService(sealer=CookieSealer(Fernet.generate_key().decode()), proxy_pool=None)


def _sample_import(source: str = "x") -> AccountImport:
    return AccountImport(
        source=source,
        user_agent="Mozilla/5.0 (Test)",
        cookies=[{"name": "auth_token", "value": "s3cret", "domain": ".x.com"}],
        pinned_proxy_id=None,
        notes="test account",
    )


@pytest.mark.asyncio
async def test_import_round_trip(fake_redis: fakeredis.aioredis.FakeRedis) -> None:
    _ = fake_redis
    svc = _mk_service()
    account_id = await svc.import_account(_sample_import())
    snap = await svc.snapshot()
    assert any(a.id == account_id for a in snap.accounts)
    # Cookies ARE NOT in the snapshot payload.
    assert all(not hasattr(a, "cookies") for a in snap.accounts)


@pytest.mark.asyncio
async def test_import_then_lease_returns_cookies(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    _ = fake_redis
    svc = _mk_service()
    await svc.import_account(_sample_import())
    resp = await svc.lease(AccountLeaseRequest(source="x"))
    assert resp.user_agent == "Mozilla/5.0 (Test)"
    assert len(resp.cookies) == 1
    assert resp.cookies[0]["name"] == "auth_token"
    assert resp.cookies[0]["value"] == "s3cret"


@pytest.mark.asyncio
async def test_lease_unavailable_when_no_active(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    _ = fake_redis
    svc = _mk_service()
    with pytest.raises(AccountUnavailable):
        await svc.lease(AccountLeaseRequest(source="x"))


@pytest.mark.asyncio
async def test_first_ok_release_promotes_new_to_active(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    _ = fake_redis
    svc = _mk_service()
    account_id = await svc.import_account(_sample_import())
    snap = await svc.snapshot()
    assert next(a.state for a in snap.accounts if a.id == account_id) == "new"

    resp = await svc.lease(AccountLeaseRequest(source="x"))
    await svc.release(AccountReleaseRequest(lease_id=resp.lease_id, outcome=LeaseOutcome.OK))

    snap = await svc.snapshot()
    assert next(a.state for a in snap.accounts if a.id == account_id) == "active"


@pytest.mark.asyncio
async def test_auth_failed_cools_account(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    _ = fake_redis
    svc = _mk_service()
    account_id = await svc.import_account(_sample_import())

    resp = await svc.lease(AccountLeaseRequest(source="x"))
    await svc.release(AccountReleaseRequest(lease_id=resp.lease_id, outcome=LeaseOutcome.AUTH_FAILED))

    snap = await svc.snapshot()
    a = next(a for a in snap.accounts if a.id == account_id)
    assert a.state == "cooling"
    assert a.last_fail_reason == "auth_failed"
    assert a.cooling_until is not None
    assert a.fail_streak == 1

    # Next lease: pool empty (cooling account not picked), request fails.
    with pytest.raises(AccountUnavailable):
        await svc.lease(AccountLeaseRequest(source="x"))


@pytest.mark.asyncio
async def test_three_auth_fails_quarantine(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    _ = fake_redis
    svc = _mk_service()
    await svc.import_account(_sample_import())

    for _ in range(QUARANTINE_FAIL_THRESHOLD):
        # Flip cooling-ready each loop so the same account is picked again.
        from datastore.repositories import AccountRepo
        from shared.infra import get_session_factory

        async with get_session_factory()() as s, s.begin():
            rows = await AccountRepo.list_all(s)
            for r in rows:
                await AccountRepo.set_state(s, account_id=r.id, state="active")
        resp = await svc.lease(AccountLeaseRequest(source="x"))
        await svc.release(AccountReleaseRequest(lease_id=resp.lease_id, outcome=LeaseOutcome.AUTH_FAILED))

    snap = await svc.snapshot()
    assert snap.accounts[0].state == "quarantined"


@pytest.mark.asyncio
async def test_rate_limited_does_not_cool(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    _ = fake_redis
    svc = _mk_service()
    await svc.import_account(_sample_import())
    resp = await svc.lease(AccountLeaseRequest(source="x"))
    await svc.release(AccountReleaseRequest(lease_id=resp.lease_id, outcome=LeaseOutcome.RATE_LIMITED))
    snap = await svc.snapshot()
    assert snap.accounts[0].state in ("new", "active")  # unchanged
    assert snap.accounts[0].fail_streak == 0


@pytest.mark.asyncio
async def test_release_unknown_lease(fake_redis: fakeredis.aioredis.FakeRedis) -> None:
    _ = fake_redis
    svc = _mk_service()
    with pytest.raises(AccountLeaseNotFound):
        await svc.release(AccountReleaseRequest(lease_id="bogus", outcome=LeaseOutcome.OK))


@pytest.mark.asyncio
async def test_budget_exhaustion_blocks_next_lease(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """Fill the minute budget, next lease must 503."""
    _ = fake_redis
    svc = _mk_service()
    await svc.import_account(_sample_import())

    leases = []
    for _ in range(BUDGET_PER_MINUTE):
        r = await svc.lease(AccountLeaseRequest(source="x"))
        leases.append(r)
        # Don't release — leases hold the budget.

    with pytest.raises(AccountUnavailable) as exc:
        await svc.lease(AccountLeaseRequest(source="x"))
    assert "budget exhausted" in str(exc.value)


@pytest.mark.asyncio
async def test_admin_activate_flips_quarantined_back(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    _ = fake_redis
    svc = _mk_service()
    account_id = await svc.import_account(_sample_import())
    await svc.set_state(account_id, "quarantined")
    await svc.set_state(account_id, "active")

    snap = await svc.snapshot()
    assert snap.accounts[0].state == "active"
    # And the account is now leasable.
    resp = await svc.lease(AccountLeaseRequest(source="x"))
    assert resp.account_id == account_id


def test_cooling_minutes_constant_is_30() -> None:
    """Sanity: the state machine defaults match the M4 spec (30 min)."""
    assert COOLING_MINUTES == 30
