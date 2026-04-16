"""Create the SQLite database at ``OPS_DATABASE_URL`` and apply all migrations.

For a fresh clone this is just ``alembic upgrade head``; we invoke it via the
Python API so users don't have to remember the full CLI. Use ``make migrate``
instead once you're comfortable with Alembic.
"""

from __future__ import annotations

import sys
from pathlib import Path

from alembic import command
from alembic.config import Config

from shared.config import get_settings


def main() -> int:
    ops_root = Path(__file__).resolve().parents[1]
    alembic_ini = ops_root / "alembic.ini"
    cfg = Config(str(alembic_ini))
    cfg.set_main_option("script_location", str(ops_root / "datastore" / "migrations"))
    cfg.set_main_option("sqlalchemy.url", get_settings().database_url)
    try:
        command.upgrade(cfg, "head")
        print(f"init_db: ok ({get_settings().database_url})")
        return 0
    except Exception as e:
        print(f"init_db: FAILED — {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
