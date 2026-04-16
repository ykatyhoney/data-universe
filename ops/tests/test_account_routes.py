"""Account-pool REST routes — auth gate + happy path + import/lease/release."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Generator

import fakeredis.aioredis
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

os.environ.setdefault("OPS_DASHBOARD_PASSWORD", "hunter2")


@pytest_asyncio.fixture
async def patched_redis(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[fakeredis.aioredis.FakeRedis]:
    from account_pool import service as acct_svc
    from proxy_pool import service as proxy_svc
    from shared import infra

    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(acct_svc, "get_redis", lambda: client)
    monkeypatch.setattr(proxy_svc, "get_redis", lambda: client)
    monkeypatch.setattr(infra, "get_redis", lambda: client)
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
def client(patched_redis: fakeredis.aioredis.FakeRedis) -> Generator[TestClient, None, None]:
    _ = patched_redis
    from shared.config import get_settings

    get_settings.cache_clear()
    from dashboard.api.main import app

    with TestClient(app, raise_server_exceptions=False) as tc:
        yield tc


def _login(c: TestClient) -> None:
    r = c.post("/api/auth/login", json={"password": "hunter2"})
    assert r.status_code == 204


_SAMPLE_PAYLOAD: dict = {
    "source": "x",
    "user_agent": "Mozilla/5.0 (Test)",
    "cookies": [
        {"name": "auth_token", "value": "abc", "domain": ".x.com"},
    ],
}


def test_import_requires_auth(client: TestClient) -> None:
    r = client.post("/api/account-pool/import", json=_SAMPLE_PAYLOAD)
    assert r.status_code == 401


def test_lease_requires_auth(client: TestClient) -> None:
    r = client.post("/api/account-pool/lease", json={"source": "x"})
    assert r.status_code == 401


def test_import_then_lease_release(client: TestClient) -> None:
    _login(client)

    imp = client.post("/api/account-pool/import", json=_SAMPLE_PAYLOAD)
    assert imp.status_code == 201
    account_id = imp.json()["account_id"]

    lease = client.post("/api/account-pool/lease", json={"source": "x"})
    assert lease.status_code == 200
    body = lease.json()
    assert body["account_id"] == account_id
    assert body["user_agent"] == "Mozilla/5.0 (Test)"
    assert body["cookies"][0]["name"] == "auth_token"

    rel = client.post(
        "/api/account-pool/release",
        json={"lease_id": body["lease_id"], "outcome": "ok"},
    )
    assert rel.status_code == 204


def test_state_endpoint_hides_cookies(client: TestClient) -> None:
    _login(client)
    client.post("/api/account-pool/import", json=_SAMPLE_PAYLOAD)
    snap = client.get("/api/account-pool/state")
    assert snap.status_code == 200
    body = snap.json()
    for a in body["accounts"]:
        assert "cookies" not in a
        assert "cookies_sealed" not in a


def test_admin_quarantine_and_activate(client: TestClient) -> None:
    _login(client)
    imp = client.post("/api/account-pool/import", json=_SAMPLE_PAYLOAD)
    account_id = imp.json()["account_id"]

    assert client.post(f"/api/account-pool/admin/{account_id}/quarantine").status_code == 204
    snap = client.get("/api/account-pool/state").json()
    assert next(a for a in snap["accounts"] if a["id"] == account_id)["state"] == "quarantined"

    assert client.post(f"/api/account-pool/admin/{account_id}/activate").status_code == 204
    snap = client.get("/api/account-pool/state").json()
    assert next(a for a in snap["accounts"] if a["id"] == account_id)["state"] == "active"


def test_release_unknown_lease_returns_404(client: TestClient) -> None:
    _login(client)
    r = client.post(
        "/api/account-pool/release",
        json={"lease_id": "bogus", "outcome": "ok"},
    )
    assert r.status_code == 404
