"""M4 — add cookie + auth fields to accounts.

Augments ``accounts`` with the columns the account pool (M4) populates on
import, lease, and release:
- ``cookies_sealed``  : Fernet-encrypted cookie bundle (bytes)
- ``user_agent``      : the UA string paired with those cookies
- ``last_fail_at``    : most recent failure timestamp
- ``last_fail_reason``: short reason code
- ``fail_streak``     : consecutive failures since last ok
- ``notes``           : free-form admin notes

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("accounts") as batch:
        batch.add_column(sa.Column("cookies_sealed", sa.LargeBinary()))
        batch.add_column(sa.Column("user_agent", sa.String(length=512)))
        batch.add_column(sa.Column("last_fail_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("last_fail_reason", sa.String(length=128)))
        batch.add_column(sa.Column("fail_streak", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("notes", sa.Text()))


def downgrade() -> None:
    with op.batch_alter_table("accounts") as batch:
        for col in (
            "notes",
            "fail_streak",
            "last_fail_reason",
            "last_fail_at",
            "user_agent",
            "cookies_sealed",
        ):
            batch.drop_column(col)
