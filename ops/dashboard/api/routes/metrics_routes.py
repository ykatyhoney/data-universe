from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dashboard.api.auth import AuthDep
from dashboard.api.dto import MetricSeries, MetricsSummaryDTO
from datastore.models import MetricsSnapshot
from shared.infra import get_session

router = APIRouter(prefix="/api/metrics", tags=["metrics"])

# The set of metrics we surface on the dashboard summary. Names match the
# canonical registry in ``common/metrics.py``.
DEFAULT_SUMMARY_METRICS: tuple[str, ...] = (
    "scrape_tasks_total",
    "scrape_items_total",
    "proxy_pool_size",
    "worker_busy",
    "ondemand_request_duration_seconds",
    "self_validation_pass_ratio",
)


@router.get("/summary", response_model=MetricsSummaryDTO)
async def summary(
    _: AuthDep,
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=1000)] = 240,
    metrics: Annotated[list[str] | None, Query()] = None,
) -> MetricsSummaryDTO:
    """Return the last ``limit`` snapshots (default ≈ last 1h at 15s tick) per metric.

    Contract tested under property-based checks in M1.E.
    """
    wanted = tuple(metrics) if metrics else DEFAULT_SUMMARY_METRICS
    series: list[MetricSeries] = []
    for metric in wanted:
        res = await session.execute(
            select(MetricsSnapshot.ts, MetricsSnapshot.value)
            .where(MetricsSnapshot.metric == metric)
            .order_by(MetricsSnapshot.ts.desc())
            .limit(limit)
        )
        rows = list(res.all())
        # Reverse into chronological order for charting.
        points = [(ts, float(v)) for ts, v in reversed(rows)]
        series.append(MetricSeries(metric=metric, points=points))
    return MetricsSummaryDTO(series=series)
