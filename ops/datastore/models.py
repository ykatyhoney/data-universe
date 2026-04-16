"""SQLAlchemy 2.x async declarative models — SQLite flavour.

M1 introduces only the tables the dashboard read-model depends on. M2.5 adds
the ``stg_*`` staging family on top of this baseline without disturbing it.

Conventions:
- No database-level schemas (SQLite has none). ``ops.*`` → bare names;
  ``staging.*`` → ``stg_*`` prefix in M2.5.
- IDs generated outside the DB (workers, tasks, proxies) stored as TEXT
  (36-char canonical UUIDs); IDs where volume matters use INTEGER
  autoincrement.
- Timestamps are ``DateTime(timezone=True)`` — SQLAlchemy serialises them as
  ISO-8601 strings on SQLite and preserves the tzinfo on load.
- ``sa.JSON`` for polymorphic payloads (stored as TEXT on SQLite with the
  ``json_valid`` check built in by SQLAlchemy).
- Indexing stays conservative: only what REST endpoints + retention queries
  need.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    # No schema — SQLite is flat. Each subclass owns its __table_args__
    # (tuple of Indexes).
    pass


# ---------- Proxies ---------- #


class Proxy(Base):
    __tablename__ = "proxies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    endpoint: Mapped[str] = mapped_column(String(512), nullable=False)
    backend: Mapped[str] = mapped_column(String(64), nullable=False, default="static_list")
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="healthy")
    session_id: Mapped[str | None] = mapped_column(String(64))
    last_probe_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fail_streak: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quarantined_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), nullable=False
    )

    __table_args__ = (Index("ix_proxies_state", "state"),)


# ---------- Accounts ---------- #


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="new")
    pinned_proxy_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("proxies.id", ondelete="SET NULL"),
    )
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_ok_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cooling_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), nullable=False
    )

    # M4 additions — populated by the account pool import / lease / release path.
    # cookies_sealed is Fernet-encrypted bytes; plaintext NEVER lands in SQL.
    cookies_sealed: Mapped[bytes | None] = mapped_column(LargeBinary)
    user_agent: Mapped[str | None] = mapped_column(String(512))
    last_fail_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_fail_reason: Mapped[str | None] = mapped_column(String(128))
    fail_streak: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    notes: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_accounts_source_state", "source", "state"),)


# ---------- Workers ---------- #


class Worker(Base):
    __tablename__ = "workers"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)  # e.g. worker-x-3
    host: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="offline")
    current_task_id: Mapped[str | None] = mapped_column(String(36))
    browser_context_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    memory_mb: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), nullable=False
    )

    __table_args__ = (Index("ix_workers_state", "state"),)


# ---------- Tasks + events ---------- #


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    label: Mapped[str] = mapped_column(String(256), nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    worker_id: Mapped[str | None] = mapped_column(String(128), ForeignKey("workers.id", ondelete="SET NULL"))
    outcome: Mapped[str | None] = mapped_column(String(32))
    error: Mapped[str | None] = mapped_column(Text)

    events: Mapped[list[TaskEvent]] = relationship(
        "TaskEvent",
        back_populates="task",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_tasks_state", "state"),
        Index("ix_tasks_source_label", "source", "label"),
        Index("ix_tasks_created_at", "created_at"),
    )


class TaskEvent(Base):
    __tablename__ = "task_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    task: Mapped[Task] = relationship("Task", back_populates="events")

    __table_args__ = (
        Index("ix_task_events_task_id", "task_id"),
        Index("ix_task_events_ts", "ts"),
    )


# ---------- Metrics snapshots ---------- #


class MetricsSnapshot(Base):
    """Rolling time-series of metrics harvested from each service's /metrics
    endpoint. dashboard-api's poller (M2) inserts here; the web UI reads it
    to render sparklines.
    """

    __tablename__ = "metrics_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), nullable=False
    )
    metric: Mapped[str] = mapped_column(String(128), nullable=False)
    labels: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    value: Mapped[float] = mapped_column(Float, nullable=False)

    __table_args__ = (Index("ix_metrics_metric_ts", "metric", "ts"),)


# ---------- DD / Gravity snapshots ---------- #


class DDJob(Base):
    __tablename__ = "dd_jobs"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    label: Mapped[str] = mapped_column(String(256), nullable=False)
    keyword: Mapped[str | None] = mapped_column(String(256))
    weight: Mapped[float] = mapped_column(Float, nullable=False)
    post_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    post_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), nullable=False
    )

    __table_args__ = (Index("ix_dd_jobs_source_label", "source", "label"),)


# ---------- On-chain telemetry (append-only audit trail) ---------- #


class ChainState(Base):
    __tablename__ = "chain_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), nullable=False
    )
    hotkey: Mapped[str] = mapped_column(String(128), nullable=False)
    incentive: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    stake: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    credibility_p2p: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    credibility_s3: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    credibility_od: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    rank: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (Index("ix_chain_state_hotkey_ts", "hotkey", "ts"),)


# ============================================================================
# Staging tables (M2.5).  Flat SQLite namespace, so "staging" → ``stg_`` prefix.
#
# Data flow: worker → scrape:results → normalizer → stg_raw_items +
#   stg_normalized_items → validation:queue → self-validator →
#   stg_validation_results → bridge promoter → SqliteMinerStorage.
# ============================================================================


class StgRawItem(Base):
    """Immutable audit trail of the raw blob a scraper produced, pre-normalization.

    Short retention (7d default). Useful for replaying through an updated
    normalizer, or investigating a flagged row.
    """

    __tablename__ = "stg_raw_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str | None] = mapped_column(String(36))
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    uri: Mapped[str] = mapped_column(String(1024), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    raw_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    har_s3_key: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), nullable=False
    )

    __table_args__ = (Index("ix_stg_raw_items_source_fetched_at", "source", "fetched_at"),)


class StgNormalizedItem(Base):
    """Post-normalization buffer, pre-promotion.

    State machine: ``pending`` → ``validating`` → ``promoted`` | ``quarantined``
    | ``dropped``. The bridge adapter promotes ``validating`` rows into the
    existing ``SqliteMinerStorage`` and flips state to ``promoted``.
    """

    __tablename__ = "stg_normalized_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    raw_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("stg_raw_items.id", ondelete="SET NULL"),
    )
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    uri: Mapped[str] = mapped_column(String(1024), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)  # sha256 hex
    item_datetime: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    label: Mapped[str | None] = mapped_column(String(256))
    normalized_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    content_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    state_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), nullable=False
    )

    __table_args__ = (
        Index("ix_stg_norm_state", "state"),
        Index("ix_stg_norm_source_state", "source", "state"),
        Index("ix_stg_norm_content_hash", "content_hash"),
    )


class StgDedupIndex(Base):
    """Global URI/content uniqueness index.

    Populated by the normalizer *before* inserting ``stg_normalized_items``.
    Retention outlasts ``stg_normalized_items`` (35d vs 7d) so we continue
    suppressing duplicates for 5 days after the source row's freshness
    window has closed.
    """

    __tablename__ = "stg_dedup_index"

    canonical_uri: Mapped[str] = mapped_column(String(1024), primary_key=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    item_datetime: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )

    __table_args__ = (
        Index("ix_stg_dedup_content_hash", "content_hash"),
        Index("ix_stg_dedup_first_seen", "first_seen_at"),
    )


class StgValidationResult(Base):
    """Output of the self-validation shim (M10). One row per sample check."""

    __tablename__ = "stg_validation_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    normalized_item_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("stg_normalized_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    validated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    passed: Mapped[bool] = mapped_column(nullable=False)
    validator_scraper: Mapped[str] = mapped_column(String(64), nullable=False)
    field_diffs: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    __table_args__ = (
        Index("ix_stg_val_norm_id", "normalized_item_id"),
        Index("ix_stg_val_passed", "passed"),
    )


class StgPromotionLog(Base):
    """Audit trail of ``stg_normalized_items`` → ``storage/miner.sqlite`` promotions."""

    __tablename__ = "stg_promotion_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    normalized_item_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("stg_normalized_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    promoted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    miner_uri: Mapped[str] = mapped_column(String(1024), nullable=False)

    __table_args__ = (Index("ix_stg_promo_norm_id", "normalized_item_id"),)
