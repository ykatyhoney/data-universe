"""Proxy-pool REST routes — auth gate + happy-path."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Generator

import fakeredis.aioredis
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

os.environ.setdefault("OPS_DASHBOARD_PASSWORD", "hunter2")
# Seed two static endpoints so lifespan has something to sync.
os.environ.setdefault(
    "OPS_PROXY_STATIC_ENDPOINTS",
    "http://u:p@routes-a:8000,http://u:p@routes-b:8001",
)


@pytest_asyncio.fixture
async def patched_redis(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[fakeredis.aioredis.FakeRedis]:
    from proxy_pool import service as svc_mod
    from shared import infra

    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(svc_mod, "get_redis", lambda: client)
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


def _login(client: TestClient) -> None:
    resp = client.post("/api/auth/login", json={"password": "hunter2"})
    assert resp.status_code == 204


def test_lease_requires_auth(client: TestClient) -> None:
    resp = client.post("/api/proxy-pool/lease", json={"source": "x"})
    assert resp.status_code == 401


def test_state_after_lifespan_has_two_proxies(client: TestClient) -> None:
    _login(client)
    resp = client.get("/api/proxy-pool/state")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["proxies"]) == 2
    assert body["counts_by_state"].get("healthy", 0) == 2


def test_lease_release_happy_path(client: TestClient) -> None:
    _login(client)
    resp = client.post("/api/proxy-pool/lease", json={"source": "x"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["proxy_id"]
    assert body["lease_id"]

    rel = client.post(
        "/api/proxy-pool/release",
        json={"lease_id": body["lease_id"], "outcome": "ok"},
    )
    assert rel.status_code == 204


def test_admin_disable_then_enable(client: TestClient) -> None:
    _login(client)
    state = client.get("/api/proxy-pool/state").json()
    proxy_id = state["proxies"][0]["id"]

    r = client.post(f"/api/proxy-pool/admin/{proxy_id}/disable")
    assert r.status_code == 204
    after = client.get("/api/proxy-pool/state").json()
    assert any(p["id"] == proxy_id and p["state"] == "disabled" for p in after["proxies"])

    r = client.post(f"/api/proxy-pool/admin/{proxy_id}/enable")
    assert r.status_code == 204
    after = client.get("/api/proxy-pool/state").json()
    assert any(p["id"] == proxy_id and p["state"] == "healthy" for p in after["proxies"])


def test_release_unknown_lease_returns_404(client: TestClient) -> None:
    _login(client)
    resp = client.post("/api/proxy-pool/release", json={"lease_id": "bogus-x", "outcome": "ok"})
    assert resp.status_code == 404
