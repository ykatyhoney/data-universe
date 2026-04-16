"""Worker entrypoint. Run with::

    python -m worker

or via pm2 (see :file:`ecosystem.config.js`).

Constructs service singletons locally (own proxy/account pools backed by
the same SQLite + Redis as dashboard-api), registers plugins, and blocks
on the runtime until interrupted.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import sys

import worker.plugins  # noqa: F401 — register scrapers on import
from account_pool.crypto import CookieSealer, CookieSealError
from account_pool.service import AccountPoolService
from proxy_pool.backends.static_list import StaticListBackend
from proxy_pool.service import ProxyPoolService
from shared.logging import configure_logging, get_logger
from worker.runtime import WorkerRuntime

configure_logging()
log = get_logger(__name__)


async def _amain() -> int:
    # Proxy pool.
    proxy_pool = ProxyPoolService(backends=[StaticListBackend()])
    await proxy_pool.sync_from_backends()

    # Account pool (degrades gracefully without a key).
    try:
        sealer = CookieSealer.from_env()
        account_pool: AccountPoolService | None = AccountPoolService(sealer, proxy_pool=proxy_pool)
    except CookieSealError as e:
        log.warning("worker.account_pool_disabled", error=str(e))
        account_pool = None

    runtime = WorkerRuntime(
        worker_id=os.environ.get("OPS_WORKER_ID"),
        account_pool=account_pool,
        proxy_pool=proxy_pool,
    )

    stop_event = asyncio.Event()

    def _on_signal(*_: object) -> None:
        log.info("worker.signal_received")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        # Windows doesn't support add_signal_handler for SIGTERM; pm2 sends
        # the process an exit and the main coroutine wakes via KeyboardInterrupt.
        with contextlib.suppress(NotImplementedError):
            asyncio.get_event_loop().add_signal_handler(sig, _on_signal)

    await runtime.start()
    try:
        await stop_event.wait()
    except KeyboardInterrupt:
        pass
    finally:
        await runtime.stop()
    return 0


def main() -> None:
    sys.exit(asyncio.run(_amain()))


if __name__ == "__main__":
    main()
