"""Bridge adapter — exercises the staging→SqliteMinerStorage promotion path
against a real (temp) miner SQLite file.

The SN13 miner module transitively imports ``torch`` (via
``common.utils``). Full miner deps land in M11; until then these tests skip
gracefully when torch isn't installed."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

# The bridge imports SN13's `common.data` and `storage.miner.sqlite_miner_storage`,
# which live at the REPO ROOT (one level above ops/). Make sure they're on
# sys.path before the test collection touches the import.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Skip this whole module if torch isn't available (SN13 dep chain).
pytest.importorskip("torch", reason="SN13 miner deps required for bridge tests; install requirements.txt")


@pytest.mark.asyncio
async def test_bridge_promotes_pending_into_miner_storage(tmp_path: Path) -> None:
    from storage.miner.sqlite_miner_storage import SqliteMinerStorage

    from datastore.repositories import StgNormalizedItemRepo, StgRawItemRepo
    from datastore.sqlite_adapter import BridgePromoter
    from shared.infra import get_session_factory

    miner_db = tmp_path / "miner.sqlite"
    miner = SqliteMinerStorage(database=str(miner_db), max_database_size_gb_hint=1)

    # Stage one normalized item ready for promotion.
    factory = get_session_factory()
    async with factory() as s, s.begin():
        raw_id = await StgRawItemRepo.insert(
            s,
            task_id=None,
            source="x",
            uri="https://x.com/u/status/bridge-1",
            raw_json={"mock": True},
        )
        await StgNormalizedItemRepo.insert_pending(
            s,
            raw_id=raw_id,
            source="x",
            uri="https://x.com/u/status/bridge-1",
            content_hash="ee" * 32,
            item_datetime=datetime.now(UTC),
            label="#bitcoin",
            normalized_json={"content": "hello bridge"},
            content_size_bytes=len(b"hello bridge"),
        )

    promoter = BridgePromoter(miner, batch_size=10)
    async with factory() as s:
        result = await promoter.promote_once(s)

    assert result.attempted >= 1
    assert result.promoted >= 1
    assert result.failed == 0

    # Verify the miner DB actually has the row.
    import sqlite3

    with sqlite3.connect(str(miner_db)) as conn:
        cur = conn.execute(
            "SELECT uri, content, contentSizeBytes FROM DataEntity WHERE uri = ?",
            ("https://x.com/u/status/bridge-1",),
        )
        row = cur.fetchone()
    assert row is not None
    assert row[0] == "https://x.com/u/status/bridge-1"
    assert row[1] == b"hello bridge"
    assert row[2] == len(b"hello bridge")


@pytest.mark.asyncio
async def test_bridge_quarantines_row_with_bad_normalized_json(tmp_path: Path) -> None:
    """A row with no ``content`` key in ``normalized_json`` must end up
    quarantined, not poison the miner DB."""
    from storage.miner.sqlite_miner_storage import SqliteMinerStorage

    from datastore.repositories import StgNormalizedItemRepo
    from datastore.sqlite_adapter import BridgePromoter
    from shared.infra import get_session_factory

    miner = SqliteMinerStorage(database=str(tmp_path / "miner.sqlite"), max_database_size_gb_hint=1)
    factory = get_session_factory()

    async with factory() as s, s.begin():
        norm_id = await StgNormalizedItemRepo.insert_pending(
            s,
            raw_id=None,
            source="x",
            uri="https://x.com/u/status/bridge-bad",
            content_hash="ff" * 32,
            item_datetime=datetime.now(UTC),
            label=None,
            normalized_json={"NOT_content": "missing"},
            content_size_bytes=10,
        )

    async with factory() as s:
        result = await BridgePromoter(miner, batch_size=10).promote_once(s)

    assert result.promoted == 0
    assert result.failed >= 1

    # Row must be marked quarantined, not still pending.
    from sqlalchemy import select

    from datastore.models import StgNormalizedItem

    async with factory() as s:
        row = (await s.execute(select(StgNormalizedItem).where(StgNormalizedItem.id == norm_id))).scalar_one()
    assert row.state == "quarantined"
