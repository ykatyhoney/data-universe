"""Cookie auth + REST route gating."""

from __future__ import annotations

import os

from fastapi.testclient import TestClient

os.environ["OPS_DASHBOARD_PASSWORD"] = "hunter2"


def _client() -> TestClient:
    from shared.config import get_settings

    get_settings.cache_clear()  # reload password
    from dashboard.api.main import app

    # raise_server_exceptions=False so 500s (e.g. DB unavailable) don't
    # propagate; we only care that the auth gate behaved correctly.
    return TestClient(app, raise_server_exceptions=False)


def test_public_routes_do_not_require_auth() -> None:
    with _client() as client:
        assert client.get("/api/health").status_code in (200, 503)
        assert client.get("/metrics").status_code == 200
        assert client.get("/").status_code == 200


def test_protected_routes_require_cookie() -> None:
    with _client() as client:
        for route in ("/api/overview", "/api/proxies", "/api/accounts", "/api/workers", "/api/tasks"):
            resp = client.get(route)
            assert resp.status_code == 401, f"{route} should require auth"


def test_login_flow_issues_cookie() -> None:
    with _client() as client:
        # Wrong password → 401.
        assert client.post("/api/auth/login", json={"password": "nope"}).status_code == 401

        # Right password → 204 + cookie set.
        resp = client.post("/api/auth/login", json={"password": "hunter2"})
        assert resp.status_code == 204
        assert "ops_session" in client.cookies

        # Now protected routes are reachable (may still 500 if DB isn't up —
        # but they must no longer 401).
        r = client.get("/api/overview")
        assert r.status_code != 401

        # Logout clears the cookie.
        resp = client.post("/api/auth/logout")
        assert resp.status_code == 204
        assert client.get("/api/overview").status_code == 401


def test_ws_rejects_without_cookie() -> None:
    from starlette.websockets import WebSocketDisconnect

    with _client() as client:
        try:
            with client.websocket_connect("/ws/live"):
                raise AssertionError("should have been rejected")
        except WebSocketDisconnect as e:
            assert e.code == 4401
