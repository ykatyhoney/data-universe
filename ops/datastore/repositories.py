"""Repository wrappers — one per aggregate.

Keep these thin. Business logic belongs to services; this layer just talks
SQL and returns ORM rows or pydantic DTOs. Every method takes an explicit
:class:`AsyncSession` so callers control transactions.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import Integer, func, insert, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from shared.clock import now_utc

from .models import (
    Account,
    ChainState,
    DDJob,
    MetricsSnapshot,
    Proxy,
    StgDedupIndex,
    StgNormalizedItem,
    StgPromotionLog,
    StgRawItem,
    StgValidationResult,
    Task,
    TaskEvent,
    Worker,
)


class ProxyRepo:
    @staticmethod
    async def list_all(session: AsyncSession, limit: int = 500) -> Sequence[Proxy]:
        res = await session.execute(select(Proxy).order_by(Proxy.created_at.desc()).limit(limit))
        return res.scalars().all()

    @staticmethod
    async def counts_by_state(session: AsyncSession) -> dict[str, int]:
        res = await session.execute(select(Proxy.state, func.count()).group_by(Proxy.state))
        return {state: n for state, n in res.all()}

    @staticmethod
    async def get(session: AsyncSession, proxy_id: str) -> Proxy | None:
        res = await session.execute(select(Proxy).where(Proxy.id == proxy_id))
        return res.scalar_one_or_none()

    @staticmethod
    async def healthy(session: AsyncSession) -> Sequence[Proxy]:
        res = await session.execute(select(Proxy).where(Proxy.state == "healthy"))
        return res.scalars().all()

    @staticmethod
    async def upsert(
        session: AsyncSession,
        *,
        proxy_id: str,
        endpoint: str,
        backend: str,
    ) -> None:
        """Insert new proxy as healthy; leave state alone on existing rows."""
        stmt = (
            sqlite_insert(Proxy)
            .values(
                id=proxy_id,
                endpoint=endpoint,
                backend=backend,
                state="healthy",
            )
            .on_conflict_do_update(
                index_elements=[Proxy.id],
                set_={"endpoint": endpoint, "backend": backend},
            )
        )
        await session.execute(stmt)

    @staticmethod
    async def set_state(
        session: AsyncSession,
        *,
        proxy_id: str,
        state: str,
        fail_streak: int | None = None,
        quarantined_until: datetime | None = None,
        last_probe_at: datetime | None = None,
    ) -> None:
        values: dict[str, Any] = {"state": state}
        if fail_streak is not None:
            values["fail_streak"] = fail_streak
        if quarantined_until is not None:
            values["quarantined_until"] = quarantined_until
        if last_probe_at is not None:
            values["last_probe_at"] = last_probe_at
        await session.execute(update(Proxy).where(Proxy.id == proxy_id).values(**values))

    @staticmethod
    async def bump_fail_streak(session: AsyncSession, proxy_id: str) -> int:
        await session.execute(
            update(Proxy).where(Proxy.id == proxy_id).values(fail_streak=Proxy.fail_streak + 1)
        )
        res = await session.execute(select(Proxy.fail_streak).where(Proxy.id == proxy_id))
        return int(res.scalar_one_or_none() or 0)

    @staticmethod
    async def reset_fail_streak(session: AsyncSession, proxy_id: str) -> None:
        await session.execute(update(Proxy).where(Proxy.id == proxy_id).values(fail_streak=0))

    @staticmethod
    async def known_ids(session: AsyncSession) -> set[str]:
        res = await session.execute(select(Proxy.id))
        return {row[0] for row in res.all()}


class AccountRepo:
    @staticmethod
    async def list_all(session: AsyncSession, limit: int = 500) -> Sequence[Account]:
        res = await session.execute(select(Account).order_by(Account.created_at.desc()).limit(limit))
        return res.scalars().all()

    @staticmethod
    async def counts_by_source_state(session: AsyncSession) -> list[tuple[str, str, int]]:
        res = await session.execute(
            select(Account.source, Account.state, func.count())
            .group_by(Account.source, Account.state)
            .order_by(Account.source, Account.state)
        )
        return [(src, state, n) for src, state, n in res.all()]

    @staticmethod
    async def get(session: AsyncSession, account_id: str) -> Account | None:
        res = await session.execute(select(Account).where(Account.id == account_id))
        return res.scalar_one_or_none()

    @staticmethod
    async def active_for_source(session: AsyncSession, source: str) -> Sequence[Account]:
        res = await session.execute(
            select(Account).where(
                Account.source == source,
                Account.state.in_(("active", "new")),
            )
        )
        return res.scalars().all()

    @staticmethod
    async def insert(
        session: AsyncSession,
        *,
        account_id: str,
        source: str,
        user_agent: str,
        cookies_sealed: bytes,
        pinned_proxy_id: str | None,
        imported_at: datetime,
        notes: str | None = None,
    ) -> None:
        """Insert only — re-importing the same account_id is a conflict."""
        stmt = insert(Account).values(
            id=account_id,
            source=source,
            user_agent=user_agent,
            cookies_sealed=cookies_sealed,
            pinned_proxy_id=pinned_proxy_id,
            imported_at=imported_at,
            state="new",
            notes=notes,
        )
        await session.execute(stmt)

    @staticmethod
    async def set_state(
        session: AsyncSession,
        *,
        account_id: str,
        state: str,
        cooling_until: datetime | None = None,
        last_fail_at: datetime | None = None,
        last_fail_reason: str | None = None,
        fail_streak: int | None = None,
    ) -> None:
        values: dict[str, Any] = {"state": state}
        if cooling_until is not None:
            values["cooling_until"] = cooling_until
        if last_fail_at is not None:
            values["last_fail_at"] = last_fail_at
        if last_fail_reason is not None:
            values["last_fail_reason"] = last_fail_reason
        if fail_streak is not None:
            values["fail_streak"] = fail_streak
        await session.execute(update(Account).where(Account.id == account_id).values(**values))

    @staticmethod
    async def touch_ok(session: AsyncSession, account_id: str, ts: datetime) -> None:
        await session.execute(
            update(Account)
            .where(Account.id == account_id)
            .values(last_ok_at=ts, fail_streak=0, last_fail_reason=None)
        )

    @staticmethod
    async def bump_fail(session: AsyncSession, account_id: str, ts: datetime, reason: str) -> int:
        await session.execute(
            update(Account)
            .where(Account.id == account_id)
            .values(
                last_fail_at=ts,
                last_fail_reason=reason,
                fail_streak=Account.fail_streak + 1,
            )
        )
        res = await session.execute(select(Account.fail_streak).where(Account.id == account_id))
        return int(res.scalar_one_or_none() or 0)

    @staticmethod
    async def promote_new_to_active(session: AsyncSession, account_id: str) -> None:
        """First OK release moves ``new`` → ``active``."""
        await session.execute(
            update(Account).where(Account.id == account_id, Account.state == "new").values(state="active")
        )


class WorkerRepo:
    @staticmethod
    async def list_all(session: AsyncSession, limit: int = 500) -> Sequence[Worker]:
        res = await session.execute(
            select(Worker).order_by(Worker.last_heartbeat_at.desc().nullslast()).limit(limit)
        )
        return res.scalars().all()

    @staticmethod
    async def counts_by_state(session: AsyncSession) -> dict[str, int]:
        res = await session.execute(select(Worker.state, func.count()).group_by(Worker.state))
        return {state: n for state, n in res.all()}

    @staticmethod
    async def upsert_heartbeat(
        session: AsyncSession,
        *,
        worker_id: str,
        host: str,
        state: str,
        current_task_id: str | None,
        browser_context_count: int,
        memory_mb: float,
        last_heartbeat_at: datetime,
    ) -> None:
        stmt = (
            sqlite_insert(Worker)
            .values(
                id=worker_id,
                host=host,
                state=state,
                current_task_id=current_task_id,
                browser_context_count=browser_context_count,
                memory_mb=memory_mb,
                last_heartbeat_at=last_heartbeat_at,
            )
            .on_conflict_do_update(
                index_elements=[Worker.id],
                set_={
                    "host": host,
                    "state": state,
                    "current_task_id": current_task_id,
                    "browser_context_count": browser_context_count,
                    "memory_mb": memory_mb,
                    "last_heartbeat_at": last_heartbeat_at,
                },
            )
        )
        await session.execute(stmt)

    @staticmethod
    async def mark_offline(session: AsyncSession, worker_id: str) -> None:
        await session.execute(update(Worker).where(Worker.id == worker_id).values(state="offline"))


class TaskRepo:
    @staticmethod
    async def list_recent(session: AsyncSession, limit: int = 200) -> Sequence[Task]:
        res = await session.execute(select(Task).order_by(Task.created_at.desc()).limit(limit))
        return res.scalars().all()

    @staticmethod
    async def counts_by_state(session: AsyncSession) -> dict[str, int]:
        res = await session.execute(select(Task.state, func.count()).group_by(Task.state))
        return {state: n for state, n in res.all()}


class TaskEventRepo:
    @staticmethod
    async def list_for_task(session: AsyncSession, task_id: str, limit: int = 50) -> Sequence[TaskEvent]:
        res = await session.execute(
            select(TaskEvent).where(TaskEvent.task_id == task_id).order_by(TaskEvent.ts.desc()).limit(limit)
        )
        return res.scalars().all()


class MetricsRepo:
    @staticmethod
    async def latest(session: AsyncSession, metric: str, limit: int = 100) -> Sequence[MetricsSnapshot]:
        res = await session.execute(
            select(MetricsSnapshot)
            .where(MetricsSnapshot.metric == metric)
            .order_by(MetricsSnapshot.ts.desc())
            .limit(limit)
        )
        return res.scalars().all()


class DDJobRepo:
    @staticmethod
    async def list_active(session: AsyncSession) -> Sequence[DDJob]:
        res = await session.execute(select(DDJob).order_by(DDJob.weight.desc()))
        return res.scalars().all()


class ChainStateRepo:
    @staticmethod
    async def latest_for_hotkey(session: AsyncSession, hotkey: str) -> ChainState | None:
        res = await session.execute(
            select(ChainState).where(ChainState.hotkey == hotkey).order_by(ChainState.ts.desc()).limit(1)
        )
        return res.scalar_one_or_none()


# ============================================================================
# Staging repositories (M2.5)
# ============================================================================


class StgRawItemRepo:
    @staticmethod
    async def insert(
        session: AsyncSession,
        *,
        task_id: str | None,
        source: str,
        uri: str,
        raw_json: dict[str, Any],
        fetched_at: datetime | None = None,
        har_s3_key: str | None = None,
    ) -> int:
        res = await session.execute(
            insert(StgRawItem)
            .values(
                task_id=task_id,
                source=source,
                uri=uri,
                raw_json=raw_json,
                fetched_at=fetched_at or now_utc(),
                har_s3_key=har_s3_key,
            )
            .returning(StgRawItem.id)
        )
        return int(res.scalar_one())


class StgDedupRepo:
    """Conditional upserts against ``stg_dedup_index``.

    ``reserve`` is the single gate that decides whether a scraped row turns
    into a ``stg_normalized_items`` row. Returning ``False`` means "already
    stored by some earlier batch; drop the duplicate."
    """

    @staticmethod
    async def reserve(
        session: AsyncSession,
        *,
        canonical_uri: str,
        content_hash: str,
        source: str,
        item_datetime: datetime,
    ) -> bool:
        """Attempt to reserve ``canonical_uri``.

        Returns ``True`` if we successfully wrote the dedup row (new unique
        item) and ``False`` if it was already present. Atomic on SQLite via
        ``INSERT ... ON CONFLICT DO NOTHING``.
        """
        stmt = (
            sqlite_insert(StgDedupIndex)
            .values(
                canonical_uri=canonical_uri,
                content_hash=content_hash,
                source=source,
                item_datetime=item_datetime,
            )
            .on_conflict_do_nothing(index_elements=[StgDedupIndex.canonical_uri])
        )
        result = await session.execute(stmt)
        return bool(getattr(result, "rowcount", 0) or 0)


class StgNormalizedItemRepo:
    @staticmethod
    async def insert_pending(
        session: AsyncSession,
        *,
        raw_id: int | None,
        source: str,
        uri: str,
        content_hash: str,
        item_datetime: datetime,
        label: str | None,
        normalized_json: dict[str, Any],
        content_size_bytes: int,
    ) -> int:
        res = await session.execute(
            insert(StgNormalizedItem)
            .values(
                raw_id=raw_id,
                source=source,
                uri=uri,
                content_hash=content_hash,
                item_datetime=item_datetime,
                label=label,
                normalized_json=normalized_json,
                content_size_bytes=content_size_bytes,
                state="pending",
            )
            .returning(StgNormalizedItem.id)
        )
        return int(res.scalar_one())

    @staticmethod
    async def claim_pending(session: AsyncSession, *, batch: int = 200) -> Sequence[StgNormalizedItem]:
        """Claim up to ``batch`` pending rows by flipping them to ``validating``.

        Single-writer: the bridge/promoter is the sole caller (serialized by
        dashboard-api). On read-replica setups this would need ``FOR UPDATE
        SKIP LOCKED`` — N/A for SQLite.
        """
        rows = (
            (
                await session.execute(
                    select(StgNormalizedItem)
                    .where(StgNormalizedItem.state == "pending")
                    .order_by(StgNormalizedItem.id)
                    .limit(batch)
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            return []
        ids = [r.id for r in rows]
        await session.execute(
            update(StgNormalizedItem)
            .where(StgNormalizedItem.id.in_(ids))
            .values(state="validating", updated_at=now_utc())
        )
        return rows

    @staticmethod
    async def mark(
        session: AsyncSession,
        *,
        ids: Sequence[int],
        state: str,
        reason: str | None = None,
    ) -> int:
        if not ids:
            return 0
        res = await session.execute(
            update(StgNormalizedItem)
            .where(StgNormalizedItem.id.in_(list(ids)))
            .values(state=state, state_reason=reason, updated_at=now_utc())
        )
        return int(getattr(res, "rowcount", 0) or 0)

    @staticmethod
    async def counts_by_state(session: AsyncSession) -> dict[str, int]:
        res = await session.execute(
            select(StgNormalizedItem.state, func.count()).group_by(StgNormalizedItem.state)
        )
        return {state: int(n) for state, n in res.all()}

    @staticmethod
    async def coverage_by_label(
        session: AsyncSession,
        *,
        source: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Per-label rollup for a source: total items + state split + last
        seen timestamp. Powers the dashboard's per-subreddit coverage panel.
        ``label`` is lowercase (``r/bittensor_``) for Reddit.
        """
        res = await session.execute(
            select(
                StgNormalizedItem.label,
                func.count().label("total"),
                func.sum(
                    func.coalesce(
                        (StgNormalizedItem.state == "promoted").cast(Integer),
                        0,
                    )
                ).label("promoted"),
                func.sum(
                    func.coalesce(
                        (StgNormalizedItem.state == "quarantined").cast(Integer),
                        0,
                    )
                ).label("quarantined"),
                func.max(StgNormalizedItem.item_datetime).label("last_seen"),
            )
            .where(StgNormalizedItem.source == source)
            .where(StgNormalizedItem.label.is_not(None))
            .group_by(StgNormalizedItem.label)
            .order_by(func.count().desc())
            .limit(limit)
        )
        return [
            {
                "label": row.label,
                "total": int(row.total or 0),
                "promoted": int(row.promoted or 0),
                "quarantined": int(row.quarantined or 0),
                "last_seen": row.last_seen,
            }
            for row in res.all()
        ]


class StgValidationRepo:
    @staticmethod
    async def record(
        session: AsyncSession,
        *,
        normalized_item_id: int,
        passed: bool,
        validator_scraper: str,
        field_diffs: dict[str, Any] | None = None,
    ) -> int:
        res = await session.execute(
            insert(StgValidationResult)
            .values(
                normalized_item_id=normalized_item_id,
                passed=passed,
                validator_scraper=validator_scraper,
                field_diffs=field_diffs or {},
            )
            .returning(StgValidationResult.id)
        )
        return int(res.scalar_one())


class StgPromotionRepo:
    @staticmethod
    async def log(
        session: AsyncSession,
        *,
        normalized_item_id: int,
        miner_uri: str,
    ) -> int:
        res = await session.execute(
            insert(StgPromotionLog)
            .values(normalized_item_id=normalized_item_id, miner_uri=miner_uri)
            .returning(StgPromotionLog.id)
        )
        return int(res.scalar_one())
