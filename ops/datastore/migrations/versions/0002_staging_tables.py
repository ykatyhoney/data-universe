"""M2.5 staging tables.

Adds the data-pipeline buffer tables: stg_raw_items, stg_normalized_items,
stg_dedup_index, stg_validation_results, stg_promotion_log.

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "stg_raw_items",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("task_id", sa.String(length=36)),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("uri", sa.String(length=1024), nullable=False),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("raw_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("har_s3_key", sa.String(length=512)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index(
        "ix_stg_raw_items_source_fetched_at",
        "stg_raw_items",
        ["source", "fetched_at"],
    )

    op.create_table(
        "stg_normalized_items",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "raw_id",
            sa.Integer(),
            sa.ForeignKey("stg_raw_items.id", ondelete="SET NULL"),
        ),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("uri", sa.String(length=1024), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("item_datetime", sa.DateTime(timezone=True), nullable=False),
        sa.Column("label", sa.String(length=256)),
        sa.Column("normalized_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("content_size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("state_reason", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index("ix_stg_norm_state", "stg_normalized_items", ["state"])
    op.create_index("ix_stg_norm_source_state", "stg_normalized_items", ["source", "state"])
    op.create_index("ix_stg_norm_content_hash", "stg_normalized_items", ["content_hash"])

    op.create_table(
        "stg_dedup_index",
        sa.Column("canonical_uri", sa.String(length=1024), primary_key=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("item_datetime", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index("ix_stg_dedup_content_hash", "stg_dedup_index", ["content_hash"])
    op.create_index("ix_stg_dedup_first_seen", "stg_dedup_index", ["first_seen_at"])

    op.create_table(
        "stg_validation_results",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "normalized_item_id",
            sa.Integer(),
            sa.ForeignKey("stg_normalized_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "validated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("validator_scraper", sa.String(length=64), nullable=False),
        sa.Column("field_diffs", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.create_index("ix_stg_val_norm_id", "stg_validation_results", ["normalized_item_id"])
    op.create_index("ix_stg_val_passed", "stg_validation_results", ["passed"])

    op.create_table(
        "stg_promotion_log",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "normalized_item_id",
            sa.Integer(),
            sa.ForeignKey("stg_normalized_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "promoted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("miner_uri", sa.String(length=1024), nullable=False),
    )
    op.create_index("ix_stg_promo_norm_id", "stg_promotion_log", ["normalized_item_id"])


def downgrade() -> None:
    for table in (
        "stg_promotion_log",
        "stg_validation_results",
        "stg_dedup_index",
        "stg_normalized_items",
        "stg_raw_items",
    ):
        op.drop_table(table)
