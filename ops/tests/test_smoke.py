from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient


def test_settings_loads_with_defaults() -> None:
    from shared.config import get_settings

    s = get_settings()
    assert s.service_name == "test"
    assert s.redis_url.startswith("redis://")
    assert s.database_url.startswith("sqlite")


def test_clock_is_freezable() -> None:
    from shared import clock

    fixed = datetime(2026, 4, 14, 12, 0, tzinfo=UTC)
    clock.set_clock(lambda: fixed)
    try:
        assert clock.now_utc() == fixed
    finally:
        clock.reset_clock()

    now = clock.now_utc()
    assert now.tzinfo is UTC


def test_schemas_roundtrip() -> None:
    from shared.schemas import (
        Account,
        AccountState,
        Proxy,
        ProxyState,
        ScrapeOutcome,
        ScrapeResult,
        ScrapeTask,
        ScrapeTaskMode,
        Source,
    )

    proxy = Proxy(endpoint="http://proxy.example:8080")
    assert proxy.state is ProxyState.HEALTHY
    assert isinstance(proxy.id, UUID)

    account = Account(source=Source.X)
    assert account.state is AccountState.NEW

    task = ScrapeTask(source=Source.X, mode=ScrapeTaskMode.SEARCH, label="#bitcoin")
    assert task.label == "#bitcoin"

    result = ScrapeResult(
        task_id=task.id,
        worker_id="worker-1",
        source=Source.X,
        outcome=ScrapeOutcome.OK,
        item_count=50,
        started_at=task.created_at,
        finished_at=task.created_at,
    )
    assert ScrapeResult.model_validate_json(result.model_dump_json()) == result


def test_schemas_are_frozen() -> None:
    from pydantic import ValidationError

    from shared.schemas import Proxy

    proxy = Proxy(endpoint="http://a:1")
    try:
        proxy.endpoint = "http://b:2"  # type: ignore[misc]
    except (TypeError, ValidationError):
        return
    raise AssertionError("Proxy should be immutable")


def test_dashboard_api_health_degrades_gracefully() -> None:
    """With no Postgres/Redis running, /api/health must return 503 with
    machine-readable per-dependency status — not a 500 stack trace."""
    from dashboard.api.main import app

    with TestClient(app) as client:
        resp = client.get("/api/health")
        # On a dev box without services, expect 503; if services are up, 200.
        assert resp.status_code in (200, 503)
        body = resp.json()
        assert body["milestone"].startswith("M")
        assert body["database"] in ("ok", "down")
        assert body["redis"] in ("ok", "down")

        metrics = client.get("/metrics")
        assert metrics.status_code == 200
        assert "api_requests_total" in metrics.text


def test_dashboard_serves_index_html() -> None:
    """/ serves either the built Vite dist/index.html or the fallback stub."""
    from dashboard.api.main import app

    with TestClient(app) as client:
        resp = client.get("/")
        assert resp.status_code == 200
        # Either the real SPA (after `make build-web`) or the fallback stub.
        assert ("Miner Control Room" in resp.text) or ("Frontend not built" in resp.text)


def test_metric_contract_registered() -> None:
    """The canonical metric names MUST exist from M0, even with zero values.

    Downstream milestones import from ``common.metrics`` rather than redefining
    metrics. Renaming here is a breaking change — flag it in review.
    """
    from prometheus_client import generate_latest

    from shared.metrics import registry

    body = generate_latest(registry).decode("utf-8")
    for expected in (
        "scrape_tasks_total",
        "scrape_task_duration_seconds",
        "scrape_items_total",
        "proxy_requests_total",
        "proxy_pool_size",
        "account_state",
        "account_rate_budget_remaining",
        "worker_heartbeat_age_seconds",
        "worker_busy",
        "worker_tasks_total",
        "worker_task_duration_seconds",
        "worker_browser_crashes_total",
        "ondemand_request_duration_seconds",
        "ondemand_requests_total",
        "self_validation_pass_ratio",
        "staging_rows",
        "stream_lag_messages",
    ):
        assert expected in body, f"missing canonical metric: {expected}"


# ---------- Integration: only run when services are actually up ---------- #


def _services_reachable() -> bool:
    """Quick synchronous probe used to skip integration tests in CI."""
    import socket

    def open_port(host: str, port: int, timeout: float = 0.3) -> bool:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            return False

    # Only Redis is a server. SQLite is file-based — it's always "reachable"
    # as long as the process can open the file.
    return open_port("localhost", 6379)


@pytest.mark.skipif(
    os.environ.get("OPS_RUN_INTEGRATION") != "1" or not _services_reachable(),
    reason="Set OPS_RUN_INTEGRATION=1 and run Redis locally to enable.",
)
@pytest.mark.asyncio
async def test_infra_pings_when_services_up() -> None:
    from shared.infra import ping_database, ping_redis

    assert await ping_redis(), "Redis did not respond to PING"
    assert await ping_database(), "SQLite did not respond to SELECT 1"
