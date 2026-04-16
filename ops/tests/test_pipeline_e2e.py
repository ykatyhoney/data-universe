"""End-to-end pipeline acceptance test for M2.5.

Pushes a synthetic ``ScrapeResultEnvelope`` onto ``scrape:results`` through
fakeredis → orchestrator normalises + stages + validates + promotes →
DataEntity row appears in a temp ``SqliteMinerStorage`` file.

This is the M2.5 acceptance gate listed in milestones.md §2.5.8.
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import fakeredis.aioredis
import pytest
import pytest_asyncio

# SN13 imports live at the repo root, one level above ops/.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Skip if SN13 miner deps (torch) aren't installed. M11 wires the full env.
pytest.importorskip("torch", reason="SN13 miner deps required; install requirements.txt")


@pytest_asyncio.fixture
async def fake_redis(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[fakeredis.aioredis.FakeRedis]:
    """Patch every spot that imports ``get_redis`` from ``datastore.streams``
    or ``shared.infra``."""
    from datastore import streams as streams_mod
    from shared import infra

    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(streams_mod, "get_redis", lambda: client)
    monkeypatch.setattr(infra, "get_redis", lambda: client)
    try:
        yield client
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_e2e_round_trip_through_orchestrator(
    fake_redis: fakeredis.aioredis.FakeRedis,
    tmp_path: Path,
) -> None:
    from storage.miner.sqlite_miner_storage import SqliteMinerStorage

    from datastore import streams as streams_mod
    from datastore.sqlite_adapter import BridgePromoter
    from normalizer.base import PassthroughNormalizer
    from pipeline.orchestrator import PipelineOrchestrator
    from self_validator.base import AlwaysPassValidator
    from shared.infra import get_session_factory
    from shared.pipeline import (
        ScrapeResultEnvelope,
        StreamName,
    )
    from shared.schemas import ScrapeOutcome, Source

    miner_db = tmp_path / "miner.sqlite"
    miner_storage = SqliteMinerStorage(database=str(miner_db), max_database_size_gb_hint=1)
    promoter = BridgePromoter(miner_storage, batch_size=50)

    orch = PipelineOrchestrator(
        normalizers={Source.X.value: PassthroughNormalizer(Source.X)},
        validator=AlwaysPassValidator(),
        promoter=promoter,
        promote_interval_s=0.05,
        metrics_interval_s=10.0,
    )
    await orch.start()
    try:
        # Seed: one envelope with one item.
        now = datetime.now(UTC)
        env = ScrapeResultEnvelope(
            task_id="task-e2e-1",
            worker_id="worker-x-1",
            source=Source.X,
            outcome=ScrapeOutcome.OK,
            items=[
                {
                    "uri": "https://twitter.com/elonmusk/status/9001?utm_source=share",
                    "datetime": now.isoformat(),
                    "label": "#bitcoin",
                    "content": "hello universe",
                }
            ],
            started_at=now,
            finished_at=now,
        )
        await streams_mod.publish(StreamName.SCRAPE_RESULTS, env)

        # Wait until the row appears in the miner DB or we time out.
        canonical = "https://x.com/elonmusk/status/9001"
        deadline = asyncio.get_event_loop().time() + 5.0
        row = None
        while asyncio.get_event_loop().time() < deadline:
            with sqlite3.connect(str(miner_db)) as conn:
                cur = conn.execute("SELECT uri, content FROM DataEntity WHERE uri = ?", (canonical,))
                row = cur.fetchone()
            if row:
                break
            await asyncio.sleep(0.05)

        assert row is not None, "DataEntity never appeared in miner DB"
        assert row[0] == canonical
        assert row[1] == b"hello universe"

        # Re-publish the same item with a different host: dedup must drop it.
        # Promotion count for the canonical URI in miner DB must NOT double.
        dup = env.model_copy(update={"task_id": "task-e2e-1-dup"})
        await streams_mod.publish(StreamName.SCRAPE_RESULTS, dup)
        await asyncio.sleep(0.5)  # let the orchestrator process

        # Confirm the dedup row exists exactly once.
        from sqlalchemy import func, select

        from datastore.models import StgDedupIndex

        factory = get_session_factory()
        async with factory() as s:
            count = (
                await s.execute(
                    select(func.count())
                    .select_from(StgDedupIndex)
                    .where(StgDedupIndex.canonical_uri == canonical)
                )
            ).scalar_one()
        assert count == 1

        # And the validation_queue → self_validator → promoter loop processed
        # exactly the original (it was the only item that reserved dedup).
        from datastore.models import StgPromotionLog

        async with factory() as s:
            promo_count = (
                await s.execute(
                    select(func.count())
                    .select_from(StgPromotionLog)
                    .where(StgPromotionLog.miner_uri == canonical)
                )
            ).scalar_one()
        assert promo_count == 1

        # Finally — confirm validation got recorded as passed.
        from datastore.models import StgValidationResult

        async with factory() as s:
            passed = (
                await s.execute(
                    select(func.count())
                    .select_from(StgValidationResult)
                    .where(StgValidationResult.passed.is_(True))
                )
            ).scalar_one()
        assert passed >= 1
    finally:
        await orch.stop()
