"""CLI: push a single ScrapeTaskEnvelope onto the ``scrape:tasks`` stream.

Used for smoke tests + debugging. Production task flow is strategist (M13)
→ stream → workers.

Usage::

    python -m worker.seed_task --source x --mode search --label "#bitcoin"
    python -m worker.seed_task --source reddit --mode profile --label "/u/spez" --task-id "fixed-id-123"
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid

from datastore.streams import ensure_group, publish
from shared.logging import configure_logging, get_logger
from shared.pipeline import ConsumerGroup, ScrapeTaskEnvelope, StreamName
from shared.schemas import ScrapeTaskMode, Source

configure_logging()
log = get_logger(__name__)


async def _run(args: argparse.Namespace) -> int:
    task_id = args.task_id or str(uuid.uuid4())
    env = ScrapeTaskEnvelope(
        task_id=task_id,
        source=Source(args.source),
        mode=ScrapeTaskMode(args.mode),
        label=args.label,
        params={},
        priority=args.priority,
    )
    # Ensure the consumer group exists so the worker can pick it up.
    await ensure_group(StreamName.SCRAPE_TASKS, ConsumerGroup.WORKERS)
    msg_id = await publish(StreamName.SCRAPE_TASKS, env)
    print(f"seed_task: ok (task_id={task_id}, msg_id={msg_id})")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="worker.seed_task")
    parser.add_argument("--source", required=True, choices=[s.value for s in Source])
    parser.add_argument("--mode", required=True, choices=[m.value for m in ScrapeTaskMode])
    parser.add_argument("--label", required=True)
    parser.add_argument("--task-id", help="stable id (default: uuid4)")
    parser.add_argument("--priority", type=int, default=0)
    args = parser.parse_args()
    sys.exit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
