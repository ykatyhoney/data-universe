"""Redis Streams helpers — tested with fakeredis so the suite stays offline.

The PEL-replay path (consumer dies mid-batch, message resurfaces) is exercised
by ``test_pipeline_e2e`` against the real ``PipelineOrchestrator``; mocking it
here would just be re-testing fakeredis."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import fakeredis.aioredis
import pytest
import pytest_asyncio

from datastore import streams as streams_mod
from shared.pipeline import (
    ConsumerGroup,
    ScrapeResultEnvelope,
    StreamName,
)
from shared.schemas import ScrapeOutcome, Source


@pytest_asyncio.fixture
async def fake_redis_patched(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[fakeredis.aioredis.FakeRedis]:
    """Replace ``storage.streams.get_redis`` with a fakeredis client."""
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(streams_mod, "get_redis", lambda: client)
    try:
        yield client
    finally:
        await client.aclose()


def _envelope(task_id: str = "t-1") -> ScrapeResultEnvelope:
    now = datetime.now(UTC)
    return ScrapeResultEnvelope(
        task_id=task_id,
        worker_id="w-1",
        source=Source.X,
        outcome=ScrapeOutcome.OK,
        items=[{"uri": "https://x.com/u/status/1", "datetime": now.isoformat(), "content": "hello"}],
        started_at=now,
        finished_at=now,
    )


@pytest.mark.asyncio
async def test_publish_and_consume_round_trip(fake_redis_patched: fakeredis.aioredis.FakeRedis) -> None:
    await streams_mod.ensure_group(StreamName.SCRAPE_RESULTS, ConsumerGroup.NORMALIZER)

    msg_id = await streams_mod.publish(StreamName.SCRAPE_RESULTS, _envelope())
    assert msg_id

    seen: list[tuple[str, ScrapeResultEnvelope]] = []
    async for got_id, env in streams_mod.consume(
        StreamName.SCRAPE_RESULTS,
        ConsumerGroup.NORMALIZER,
        consumer_name="c-1",
        block_ms=100,
        count=10,
    ):
        assert isinstance(env, ScrapeResultEnvelope)
        seen.append((got_id, env))
        await streams_mod.ack(StreamName.SCRAPE_RESULTS, ConsumerGroup.NORMALIZER, got_id)
        if len(seen) == 1:
            break

    assert len(seen) == 1
    assert seen[0][1].task_id == "t-1"


@pytest.mark.asyncio
async def test_ensure_group_is_idempotent(
    fake_redis_patched: fakeredis.aioredis.FakeRedis,
) -> None:
    """Calling ensure_group twice on the same stream must not raise."""
    _ = fake_redis_patched
    await streams_mod.ensure_group(StreamName.SCRAPE_RESULTS, ConsumerGroup.NORMALIZER)
    await streams_mod.ensure_group(StreamName.SCRAPE_RESULTS, ConsumerGroup.NORMALIZER)


@pytest.mark.asyncio
async def test_stream_length(
    fake_redis_patched: fakeredis.aioredis.FakeRedis,
) -> None:
    _ = fake_redis_patched
    await streams_mod.ensure_group(StreamName.SCRAPE_RESULTS, ConsumerGroup.NORMALIZER)
    for i in range(3):
        await streams_mod.publish(StreamName.SCRAPE_RESULTS, _envelope(f"t-{i}"))

    assert await streams_mod.stream_length(StreamName.SCRAPE_RESULTS) == 3
    # Nothing claimed yet → pending == 0.
    assert await streams_mod.pending_count(StreamName.SCRAPE_RESULTS, ConsumerGroup.NORMALIZER) == 0
