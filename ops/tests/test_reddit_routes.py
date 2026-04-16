"""Reddit dashboard route — coverage rollup + account-pool slice.

Uses the same in-memory SQLite + fake Redis wiring as the other route
tests. Verifies:
    1. Endpoint is cookie-gated (401 without login).
    2. With zero data, returns empty coverage + zeroed account health.
    3. Populated staging rows produce per-subreddit rollups with promoted /
       quarantined counts and last_seen timestamps.
    4. ``with_praw_credentials`` counter reflects the ``notes`` tagging
       convention agreed in M7 import flow.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Generator
from datetime import UTC, datetime

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


async def _seed_coverage_rows() -> None:
    """Insert a small fixture set directly into stg_normalized_items."""
    from datastore.repositories import StgNormalizedItemRepo
    from shared.infra import get_session_factory

    factory = get_session_factory()
    base = datetime(2025, 4, 15, 10, 0, 0, tzinfo=UTC)
    rows = [
        ("r/bittensor_", "promoted", base),
        ("r/bittensor_", "promoted", base),
        ("r/bittensor_", "quarantined", base),
        ("r/cryptocurrency", "pending", base),
        ("r/cryptocurrency", "promoted", base),
    ]
    async with factory() as s, s.begin():
        for i, (label, state, ts) in enumerate(rows):
            norm_id = await StgNormalizedItemRepo.insert_pending(
                s,
                raw_id=None,
                source="reddit",
                uri=f"https://reddit.com/r/x/comments/a{i}",
                content_hash=f"hash-{i}",
                item_datetime=ts,
                label=label,
                normalized_json={"content": "{}"},
                content_size_bytes=2,
            )
            if state != "pending":
                await StgNormalizedItemRepo.mark(s, ids=[norm_id], state=state)


def test_overview_requires_auth(client: TestClient) -> None:
    r = client.get("/api/reddit/overview")
    assert r.status_code == 401


def test_overview_empty(client: TestClient) -> None:
    _login(client)
    r = client.get("/api/reddit/overview")
    assert r.status_code == 200
    body = r.json()
    assert body["coverage"] == []
    assert body["accounts"] == {
        "total": 0,
        "active": 0,
        "cooling": 0,
        "quarantined": 0,
        "with_praw_credentials": 0,
    }


def test_overview_returns_coverage_and_ordering(client: TestClient) -> None:
    import asyncio

    _login(client)
    asyncio.run(_seed_coverage_rows())

    r = client.get("/api/reddit/overview?limit=10")
    assert r.status_code == 200
    body = r.json()
    # r/bittensor_ has 3 items, r/cryptocurrency has 2 — ordered by count desc.
    labels = [c["label"] for c in body["coverage"]]
    assert labels == ["r/bittensor_", "r/cryptocurrency"]
    bittensor = body["coverage"][0]
    assert bittensor["total"] == 3
    assert bittensor["promoted"] == 2
    assert bittensor["quarantined"] == 1
    assert bittensor["last_seen"] is not None


def test_overview_counts_praw_via_notes_tag(client: TestClient) -> None:
    """Accounts imported with a ``notes`` field containing "praw" are
    counted as PRAW-capable. This pins the convention so the RedditPanel's
    primary-path-down warning fires correctly."""
    _login(client)

    import_payload: dict = {
        "source": "reddit",
        "user_agent": "test/1.0",
        "credentials": {
            "client_id": "c",
            "client_secret": "s",
            "refresh_token": "r",
        },
        "notes": "PRAW OAuth — import batch #1",
    }
    r = client.post("/api/account-pool/import", json=import_payload)
    assert r.status_code == 201

    r = client.get("/api/reddit/overview")
    assert r.status_code == 200
    body = r.json()
    assert body["accounts"]["total"] == 1
    assert body["accounts"]["active"] == 0  # NEW state until first OK
    assert body["accounts"]["with_praw_credentials"] == 1
