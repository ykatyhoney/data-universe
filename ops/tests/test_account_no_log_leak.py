"""Cookies must NEVER appear in structured log output.

Runs the account pool through its full lifecycle while capturing structlog
output (which goes to stdout via ``PrintLoggerFactory``) and greps for
sentinel values. This test is the CI tripwire for rule #3 (no credentials
in logs) listed in milestones.md cross-cutting rules.
"""

from __future__ import annotations

import contextlib
import io
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
from account_pool.service import AccountPoolService

_SECRET_COOKIE_VALUE = "super-secret-auth-token-aaaaaaa-DO-NOT-LEAK"
_SECRET_COOKIE_NAME = "auth_token_very_special_xxxxxxx"
_SECRET_UA = "Mozilla/5.0 (test) NEEDLE-IN-HAYSTACK"


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


@pytest.mark.asyncio
async def test_no_cookie_leak_in_logs(
    fake_redis: fakeredis.aioredis.FakeRedis, capsys: pytest.CaptureFixture[str]
) -> None:
    """structlog writes via ``PrintLoggerFactory`` → stdout. We ensure the
    service is configured (``configure_logging`` is module-init) and that
    log level is INFO so warnings land, then drive the full lifecycle."""
    _ = fake_redis

    # Force structlog level to DEBUG so every log line is emitted — makes
    # the tripwire stricter. The service sets its level from env, so we
    # just re-configure with a fresh structlog.
    import structlog

    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.add_log_level,
            structlog.processors.EventRenamer("event"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(10),  # DEBUG
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )

    sink = io.StringIO()
    with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        svc = AccountPoolService(sealer=CookieSealer(Fernet.generate_key().decode()), proxy_pool=None)
        account_id = await svc.import_account(
            AccountImport(
                source="x",
                user_agent=_SECRET_UA,
                cookies=[
                    {
                        "name": _SECRET_COOKIE_NAME,
                        "value": _SECRET_COOKIE_VALUE,
                        "domain": ".x.com",
                    }
                ],
            )
        )
        resp = await svc.lease(AccountLeaseRequest(source="x", action="test"))
        await svc.release(AccountReleaseRequest(lease_id=resp.lease_id, outcome=LeaseOutcome.AUTH_FAILED))
        del account_id

    captured = sink.getvalue()
    # Sanity: the service logged SOMETHING.
    assert "account_pool" in captured

    # The tripwires.
    assert _SECRET_COOKIE_VALUE not in captured, "cookie VALUE leaked to logs"
    assert _SECRET_COOKIE_NAME not in captured, "cookie NAME leaked to logs"
    assert _SECRET_UA not in captured, "user_agent leaked to logs"
    assert "Cookie:" not in captured
    assert "Set-Cookie" not in captured

    # Silence pytest's capsys warning (we used our own capture).
    _ = capsys
