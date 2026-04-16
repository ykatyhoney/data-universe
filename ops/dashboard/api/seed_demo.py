"""Emit a stream of fake events so the dashboard lights up without real services.

Used by the M1 acceptance test: CLI publishes fake events → dashboard updates
within 1s via WebSocket.

Usage:
    python -m dashboard.api.seed_demo              # stream forever, ~1 event/sec
    python -m dashboard.api.seed_demo --burst 50   # 50 events then exit
    python -m dashboard.api.seed_demo --rate 20    # 20 events/sec
"""

from __future__ import annotations

import argparse
import asyncio
import random
from uuid import uuid4

from dashboard.api.ws import publish
from shared.events import (
    AccountStateChanged,
    AnyEvent,
    MetricTick,
    ProxyStateChanged,
    TaskFinished,
    TaskStarted,
    WorkerHeartbeat,
)
from shared.infra import dispose_redis
from shared.logging import configure_logging, get_logger
from shared.schemas import (
    AccountState,
    ProxyState,
    ScrapeOutcome,
    ScrapeTaskMode,
    Source,
    TaskState,
    WorkerState,
)

configure_logging()
log = get_logger(__name__)

_SOURCES = list(Source)
_WORKERS = ["worker-x-1", "worker-x-2", "worker-reddit-1", "worker-youtube-1"]
_LABELS = ["#bitcoin", "#crypto", "#tao", "r/Bitcoin", "r/Cryptocurrency"]


def _random_event() -> AnyEvent:
    kind = random.random()
    if kind < 0.15:
        return ProxyStateChanged(
            proxy_id=uuid4(),
            from_state=ProxyState.HEALTHY,
            to_state=random.choice(list(ProxyState)),
            reason="demo",
        )
    if kind < 0.3:
        return AccountStateChanged(
            account_id=uuid4(),
            source=random.choice(_SOURCES),
            from_state=AccountState.ACTIVE,
            to_state=random.choice(list(AccountState)),
            reason="demo",
        )
    if kind < 0.55:
        wid = random.choice(_WORKERS)
        return WorkerHeartbeat(
            worker_id=wid,
            host="local",
            state=random.choice(list(WorkerState)),
            memory_mb=random.uniform(120, 480),
            browser_context_count=random.randint(0, 4),
        )
    if kind < 0.7:
        return TaskStarted(
            task_id=uuid4(),
            source=random.choice(_SOURCES),
            mode=ScrapeTaskMode.SEARCH,
            label=random.choice(_LABELS),
            worker_id=random.choice(_WORKERS),
        )
    if kind < 0.85:
        return TaskFinished(
            task_id=uuid4(),
            source=random.choice(_SOURCES),
            outcome=random.choice(list(ScrapeOutcome)),
            state=TaskState.SUCCEEDED,
            item_count=random.randint(0, 200),
            duration_seconds=random.uniform(0.5, 30.0),
        )
    return MetricTick(
        metric=random.choice(
            [
                "scrape_tasks_total",
                "scrape_items_total",
                "worker_busy",
                "self_validation_pass_ratio",
            ]
        ),
        labels={"source": random.choice(_SOURCES).value},
        value=random.uniform(0, 100),
    )


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rate", type=float, default=1.0, help="events per second")
    parser.add_argument("--burst", type=int, default=0, help="emit exactly N events then exit (0 = forever)")
    args = parser.parse_args()

    interval = 1.0 / max(args.rate, 0.01)
    emitted = 0
    try:
        while args.burst == 0 or emitted < args.burst:
            event = _random_event()
            subs = await publish(event)
            log.info("seed.publish", kind=event.kind, subscribers=subs)
            emitted += 1
            await asyncio.sleep(interval)
    except KeyboardInterrupt:
        pass
    finally:
        await dispose_redis()


if __name__ == "__main__":
    asyncio.run(main())
