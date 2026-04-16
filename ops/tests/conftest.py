from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

# Make `common`, `dashboard`, `storage` importable without installing the package.
OPS_ROOT = Path(__file__).resolve().parents[1]
if str(OPS_ROOT) not in sys.path:
    sys.path.insert(0, str(OPS_ROOT))

# Isolate tests from any developer .env file.
os.environ.setdefault("OPS_SERVICE_NAME", "test")
os.environ.setdefault("OPS_LOG_LEVEL", "WARNING")

# Route test DB to a tempfile so poller/sweeper don't error on missing tables
# and so successive test runs don't contaminate each other. The path is set
# before any module touches `common.config` at import time.
_TMP_DIR = Path(tempfile.mkdtemp(prefix="ops-test-"))
_TEST_DB = _TMP_DIR / "ops.db"
os.environ["OPS_DATABASE_URL"] = f"sqlite+aiosqlite:///{_TEST_DB.as_posix()}"

# Stable Fernet key for tests that exercise the account pool. Generated once
# per session; services built from ``CookieSealer.from_env()`` get this.
try:
    from cryptography.fernet import Fernet

    os.environ.setdefault("OPS_ACCOUNT_POOL_KEY", Fernet.generate_key().decode())
except Exception:
    pass


@pytest.fixture(scope="session", autouse=True)
def _prepare_test_database() -> Iterator[None]:
    """Run alembic migrations once per test session so every DB-touching
    module (metric poller, retention sweep, repositories) sees a populated
    schema rather than `no such table` errors."""
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(OPS_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(OPS_ROOT / "datastore" / "migrations"))
    cfg.set_main_option("sqlalchemy.url", os.environ["OPS_DATABASE_URL"])
    command.upgrade(cfg, "head")
    yield


@pytest.fixture(autouse=True)
def _isolate_pool_tables() -> Iterator[None]:
    """Wipe pool-ish tables before each test so cross-test pollution doesn't
    break count assertions in M3/M4 tests. Cheap: these tables are small.
    Accounts are cleared BEFORE proxies because accounts.pinned_proxy_id FKs
    there (ON DELETE SET NULL, but we wipe in dependency order anyway)."""
    import asyncio

    async def _wipe() -> None:
        from sqlalchemy import delete

        from datastore.models import Account, Proxy
        from shared.infra import get_session_factory

        factory = get_session_factory()
        async with factory() as s, s.begin():
            await s.execute(delete(Account))
            await s.execute(delete(Proxy))

    try:
        asyncio.get_event_loop().run_until_complete(_wipe())
    except RuntimeError:
        asyncio.new_event_loop().run_until_complete(_wipe())
    yield
