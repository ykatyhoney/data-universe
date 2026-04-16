"""Staging repositories — exercised against the test SQLite DB created
by ``conftest._prepare_test_database``."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from datastore.repositories import (
    StgDedupRepo,
    StgNormalizedItemRepo,
    StgRawItemRepo,
)
from shared.infra import get_session_factory


@pytest.mark.asyncio
async def test_dedup_first_writer_wins() -> None:
    factory = get_session_factory()
    async with factory() as s, s.begin():
        first = await StgDedupRepo.reserve(
            s,
            canonical_uri="https://x.com/u/status/dedup-1",
            content_hash="aa" * 32,
            source="x",
            item_datetime=datetime.now(UTC),
        )
        second = await StgDedupRepo.reserve(
            s,
            canonical_uri="https://x.com/u/status/dedup-1",
            content_hash="bb" * 32,
            source="x",
            item_datetime=datetime.now(UTC),
        )
    assert first is True
    assert second is False


@pytest.mark.asyncio
async def test_pending_promote_state_machine() -> None:
    factory = get_session_factory()
    async with factory() as s, s.begin():
        raw_id = await StgRawItemRepo.insert(
            s,
            task_id=None,
            source="x",
            uri="https://x.com/u/status/sm-1",
            raw_json={"hello": "world"},
        )
        norm_id = await StgNormalizedItemRepo.insert_pending(
            s,
            raw_id=raw_id,
            source="x",
            uri="https://x.com/u/status/sm-1",
            content_hash="cc" * 32,
            item_datetime=datetime.now(UTC),
            label="#bitcoin",
            normalized_json={"content": "hello"},
            content_size_bytes=5,
        )

    assert norm_id > 0

    async with factory() as s, s.begin():
        claimed = await StgNormalizedItemRepo.claim_pending(s, batch=10)
    assert any(r.id == norm_id for r in claimed)
    assert all(r.state == "validating" for r in claimed)

    async with factory() as s, s.begin():
        marked = await StgNormalizedItemRepo.mark(s, ids=[norm_id], state="promoted", reason=None)
    assert marked == 1

    async with factory() as s:
        counts = await StgNormalizedItemRepo.counts_by_state(s)
    # Other tests may add rows; only check our target state has at least 1.
    assert counts.get("promoted", 0) >= 1


@pytest.mark.asyncio
async def test_terminal_state_unchanged_by_reclaim() -> None:
    """Once we mark something promoted, claim_pending must not pick it up."""
    factory = get_session_factory()
    async with factory() as s, s.begin():
        raw_id = await StgRawItemRepo.insert(
            s,
            task_id=None,
            source="x",
            uri="https://x.com/u/status/sm-2",
            raw_json={},
        )
        norm_id = await StgNormalizedItemRepo.insert_pending(
            s,
            raw_id=raw_id,
            source="x",
            uri="https://x.com/u/status/sm-2",
            content_hash="dd" * 32,
            item_datetime=datetime.now(UTC) - timedelta(minutes=1),
            label=None,
            normalized_json={"content": "x"},
            content_size_bytes=1,
        )
        await StgNormalizedItemRepo.mark(s, ids=[norm_id], state="promoted")

    async with factory() as s, s.begin():
        again = await StgNormalizedItemRepo.claim_pending(s, batch=100)
    assert all(r.id != norm_id for r in again)
