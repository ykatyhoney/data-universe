"""/api/metrics/summary schema contract — the frontend depends on this shape."""

from __future__ import annotations

import os
from datetime import UTC

from fastapi.testclient import TestClient

os.environ.setdefault("OPS_DASHBOARD_PASSWORD", "hunter2")


def _authed_client() -> TestClient:
    from shared.config import get_settings

    get_settings.cache_clear()
    from dashboard.api.main import app

    client = TestClient(app)
    client.__enter__()  # enter lifespan
    # Ignore DB errors — the shape test doesn't need the db.
    return client


def test_summary_schema_with_no_db() -> None:
    """Without a database the endpoint 500s — that's fine; we only pin the
    shape when a DB exists. Here we just make sure auth gating works and the
    URL is registered."""
    client = _authed_client()
    try:
        # No cookie → 401.
        resp = client.get("/api/metrics/summary")
        assert resp.status_code == 401
    finally:
        client.close()
        client.__exit__(None, None, None)


def test_summary_dto_shape_via_pydantic() -> None:
    """Validate the DTO itself so FE type drift is caught here, not in prod."""
    from datetime import datetime

    from dashboard.api.dto import MetricSeries, MetricsSummaryDTO

    now = datetime.now(UTC)
    dto = MetricsSummaryDTO(
        series=[
            MetricSeries(metric="scrape_tasks_total", points=[(now, 1.0), (now, 2.0)]),
            MetricSeries(metric="worker_busy", points=[]),
        ]
    )
    data = dto.model_dump_json()
    assert "scrape_tasks_total" in data
    assert "points" in data
    # Round-trip back.
    reparsed = MetricsSummaryDTO.model_validate_json(data)
    assert len(reparsed.series) == 2
    assert reparsed.series[0].metric == "scrape_tasks_total"
