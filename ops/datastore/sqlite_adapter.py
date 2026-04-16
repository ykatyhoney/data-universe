"""Bridge from ``stg_normalized_items`` into the existing ``SqliteMinerStorage``.

SqliteMinerStorage (the miner's own DB) is SN13-provided code — we do NOT
modify it. This adapter:
    1. Claims a batch of ``state=validating`` rows (after the self-validator
       has stamped them pass/quarantined).
    2. Builds ``DataEntity`` objects (the shape the miner serves to validators).
    3. Calls ``store_data_entities`` synchronously (it's a thread-blocking
       API); we dispatch via ``asyncio.to_thread`` to keep the event loop
       free.
    4. On success, marks the staging rows ``promoted`` + writes to
       ``stg_promotion_log``.
    5. On failure, flips rows back to ``pending`` so the next tick retries.

``SqliteMinerStorage`` serialises writes behind ``clearing_space_lock`` — we
therefore size our batches conservatively (default 200) and run at most one
bridge tick in flight.

The bridge writes into ``storage/miner.sqlite`` **as configured on the
miner process**. During the ops-only phase (pre-M11) the adapter is still
useful for integration tests that point at a tmp SQLite file.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from dataclasses import dataclass
from typing import Any

# SN13 public API — do not modify. Located at the repo root, imported via the
# ``data-universe`` top-level path (added to PYTHONPATH by pm2/ecosystem or
# by tests).
from shared.data import DataEntity, DataLabel, DataSource
from sqlalchemy.ext.asyncio import AsyncSession
from storage.miner.sqlite_miner_storage import SqliteMinerStorage

from shared.logging import get_logger
from shared.schemas import Source as OpsSource

from .repositories import (
    StgNormalizedItemRepo,
    StgPromotionRepo,
)

log = get_logger(__name__)

# SN13 DataSource IDs (see common/data.py).
_SOURCE_MAP: dict[OpsSource, DataSource] = {
    OpsSource.REDDIT: DataSource.REDDIT,
    OpsSource.X: DataSource.X,
    OpsSource.YOUTUBE: DataSource.YOUTUBE,
}


@dataclass(frozen=True)
class BridgeResult:
    attempted: int
    promoted: int
    failed: int


def _to_data_entity(row: Any) -> DataEntity:
    """Build a ``DataEntity`` from a ``StgNormalizedItem`` row.

    ``normalized_json["content"]`` holds the serialised content blob (e.g.
    a JSON-encoded RedditContent / XContent). Callers ensure it's always
    present and a string.
    """
    content_str = row.normalized_json.get("content")
    if not isinstance(content_str, str):
        raise ValueError(f"normalized row {row.id} has no 'content' string in normalized_json")
    content_bytes = content_str.encode("utf-8")

    # Ensure timezone-aware; SN13's miner DB stores naive UTC datetimes.
    item_dt: dt.datetime = row.item_datetime
    if item_dt.tzinfo is None:
        item_dt = item_dt.replace(tzinfo=dt.UTC)

    source_enum = OpsSource(row.source)

    return DataEntity(
        uri=row.uri,
        datetime=item_dt,
        source=_SOURCE_MAP[source_enum],
        label=DataLabel(value=row.label) if row.label else None,
        content=content_bytes,
        content_size_bytes=row.content_size_bytes or len(content_bytes),
    )


class BridgePromoter:
    """Async wrapper over the sync ``SqliteMinerStorage.store_data_entities``.

    One instance per dashboard-api process. The storage handle is owned by
    us so we can hot-swap the target DB in tests.
    """

    def __init__(self, miner_storage: SqliteMinerStorage, *, batch_size: int = 200) -> None:
        self._storage = miner_storage
        self._batch = max(1, batch_size)

    async def promote_once(self, session: AsyncSession) -> BridgeResult:
        """Process one batch. Returns counts; caller controls the cadence."""
        rows = await StgNormalizedItemRepo.claim_pending(session, batch=self._batch)
        if not rows:
            return BridgeResult(attempted=0, promoted=0, failed=0)

        # Build entities *before* commit so bad rows fail this tick
        # (and stay in ``validating`` for a human to inspect).
        entities: list[DataEntity] = []
        accepted_rows: list[Any] = []
        for row in rows:
            try:
                entities.append(_to_data_entity(row))
                accepted_rows.append(row)
            except Exception as e:
                log.warning("bridge.row_build_failed", row_id=row.id, error=str(e))
                await StgNormalizedItemRepo.mark(
                    session, ids=[row.id], state="quarantined", reason=f"bridge: {e}"
                )

        if not entities:
            await session.commit()
            return BridgeResult(attempted=len(rows), promoted=0, failed=len(rows))

        # ``store_data_entities`` is synchronous + uses its own transaction.
        try:
            await asyncio.to_thread(self._storage.store_data_entities, entities)
        except Exception as e:
            log.warning("bridge.store_failed", error=str(e), batch=len(entities))
            # Flip back to ``pending`` so the next tick retries.
            await StgNormalizedItemRepo.mark(
                session,
                ids=[r.id for r in accepted_rows],
                state="pending",
                reason=f"bridge retry: {e}",
            )
            await session.commit()
            return BridgeResult(attempted=len(rows), promoted=0, failed=len(accepted_rows))

        # Success — mark promoted + log.
        await StgNormalizedItemRepo.mark(session, ids=[r.id for r in accepted_rows], state="promoted")
        for r in accepted_rows:
            await StgPromotionRepo.log(session, normalized_item_id=r.id, miner_uri=r.uri)
        await session.commit()

        return BridgeResult(
            attempted=len(rows),
            promoted=len(accepted_rows),
            failed=len(rows) - len(accepted_rows),
        )
