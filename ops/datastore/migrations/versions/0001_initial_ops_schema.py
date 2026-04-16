"""M1 initial ops schema — SQLite flavour.

Creates the tables the dashboard read-model needs: proxies, accounts,
workers, tasks, task_events, metrics_snapshots, dd_jobs, chain_state.

No database-level schemas (SQLite has none). M2.5 adds ``stg_*`` tables in a
follow-up revision without touching anything here.

Revision ID: 0001
Revises:
Create Date: 2026-04-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "proxies",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("endpoint", sa.String(length=512), nullable=False),
        sa.Column("backend", sa.String(length=64), nullable=False, server_default="static_list"),
        sa.Column("state", sa.String(length=32), nullable=False, server_default="healthy"),
        sa.Column("session_id", sa.String(length=64)),
        sa.Column("last_probe_at", sa.DateTime(timezone=True)),
        sa.Column("fail_streak", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("quarantined_until", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index("ix_proxies_state", "proxies", ["state"])

    op.create_table(
        "accounts",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False, server_default="new"),
        sa.Column(
            "pinned_proxy_id",
            sa.String(length=36),
            sa.ForeignKey("proxies.id", ondelete="SET NULL"),
        ),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_ok_at", sa.DateTime(timezone=True)),
        sa.Column("cooling_until", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index("ix_accounts_source_state", "accounts", ["source", "state"])

    op.create_table(
        "workers",
        sa.Column("id", sa.String(length=128), primary_key=True),
        sa.Column("host", sa.String(length=128), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False, server_default="offline"),
        sa.Column("current_task_id", sa.String(length=36)),
        sa.Column("browser_context_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("memory_mb", sa.Float(), nullable=False, server_default="0"),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index("ix_workers_state", "workers", ["state"])

    op.create_table(
        "tasks",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("label", sa.String(length=256), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("state", sa.String(length=32), nullable=False, server_default="queued"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column(
            "worker_id",
            sa.String(length=128),
            sa.ForeignKey("workers.id", ondelete="SET NULL"),
        ),
        sa.Column("outcome", sa.String(length=32)),
        sa.Column("error", sa.Text()),
    )
    op.create_index("ix_tasks_state", "tasks", ["state"])
    op.create_index("ix_tasks_source_label", "tasks", ["source", "label"])
    op.create_index("ix_tasks_created_at", "tasks", ["created_at"])

    op.create_table(
        "task_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "task_id",
            sa.String(length=36),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "ts",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.create_index("ix_task_events_task_id", "task_events", ["task_id"])
    op.create_index("ix_task_events_ts", "task_events", ["ts"])

    op.create_table(
        "metrics_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "ts",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("metric", sa.String(length=128), nullable=False),
        sa.Column("labels", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("value", sa.Float(), nullable=False),
    )
    op.create_index("ix_metrics_metric_ts", "metrics_snapshots", ["metric", "ts"])

    op.create_table(
        "dd_jobs",
        sa.Column("id", sa.String(length=128), primary_key=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("label", sa.String(length=256), nullable=False),
        sa.Column("keyword", sa.String(length=256)),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.Column("post_start", sa.DateTime(timezone=True)),
        sa.Column("post_end", sa.DateTime(timezone=True)),
        sa.Column(
            "seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index("ix_dd_jobs_source_label", "dd_jobs", ["source", "label"])

    op.create_table(
        "chain_state",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "ts",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("hotkey", sa.String(length=128), nullable=False),
        sa.Column("incentive", sa.Float(), nullable=False, server_default="0"),
        sa.Column("stake", sa.Float(), nullable=False, server_default="0"),
        sa.Column("credibility_p2p", sa.Float(), nullable=False, server_default="0"),
        sa.Column("credibility_s3", sa.Float(), nullable=False, server_default="0"),
        sa.Column("credibility_od", sa.Float(), nullable=False, server_default="0"),
        sa.Column("rank", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_chain_state_hotkey_ts", "chain_state", ["hotkey", "ts"])


def downgrade() -> None:
    for table in (
        "chain_state",
        "dd_jobs",
        "metrics_snapshots",
        "task_events",
        "tasks",
        "workers",
        "accounts",
        "proxies",
    ):
        op.drop_table(table)
