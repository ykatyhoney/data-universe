"""Preflight: verify Redis is reachable and SQLite's path is writable.

Run after installing Redis (Memurai on Windows) and before ``pm2 start``.
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from shared.config import get_settings
from shared.infra import get_redis

HINTS = {
    "redis": (
        "Install Redis (or Memurai on Windows: https://www.memurai.com/) and make sure "
        "it is listening at OPS_REDIS_URL. Default: redis://localhost:6379/0."
    ),
    "database": (
        "Default OPS_DATABASE_URL is sqlite+aiosqlite:///./ops.db. Make sure the CWD is "
        "writable (pm2 sets CWD to ops/; running directly: `cd ops && ...`). Override the "
        "URL if you want the file elsewhere (absolute paths use four slashes: sqlite+aiosqlite:////abs/path/ops.db)."
    ),
}


async def _check_redis() -> tuple[bool, str]:
    try:
        pong = await get_redis().ping()
        return (bool(pong), "redis: ok" if pong else "redis: ping returned falsy")
    except Exception as e:
        return (False, f"redis: {e!s}")


async def _check_database() -> tuple[bool, str]:
    url = get_settings().database_url
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return (True, f"database: ok ({url})")
    except Exception as e:
        return (False, f"database: {e!s}")
    finally:
        await engine.dispose()


async def main() -> int:
    checks = await asyncio.gather(_check_redis(), _check_database())
    failed: list[str] = []
    for ok, msg in checks:
        print(("OK  " if ok else "FAIL") + "  " + msg)
        if not ok:
            failed.append(msg.split(":", 1)[0])

    if failed:
        print("\nHints:")
        for svc in failed:
            print(f"\n[{svc}]\n{HINTS.get(svc, 'no hint available')}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
