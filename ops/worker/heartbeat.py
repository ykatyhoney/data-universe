"""Background task that writes worker state into ``ops.workers`` + emits
``WorkerHeartbeat`` events on the shared live bus every
:data:`HEARTBEAT_INTERVAL_SECONDS`. Dashboard picks both up automatically.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket
from dataclasses import dataclass
from typing import Any

from datastore.repositories import WorkerRepo
from shared.clock import now_utc
from shared.events import WorkerHeartbeat
from shared.infra import get_session_factory
from shared.logging import get_logger
from shared.metrics import worker_busy, worker_heartbeat_age_seconds
from shared.schemas import WorkerState

log = get_logger(__name__)

HEARTBEAT_INTERVAL_SECONDS = 15


@dataclass
class WorkerInfo:
    """Live state the heartbeat writer snapshots on each tick."""

    worker_id: str
    host: str = socket.gethostname()
    state: WorkerState = WorkerState.IDLE
    current_task_id: str | None = None
    browser_context_count: int = 0
    memory_mb: float = 0.0


def _current_memory_mb() -> float:
    """Best-effort RSS reading. Uses psutil if available; falls back to
    POSIX ``resource.getrusage``. Returns 0.0 on Windows-without-psutil
    (the dashboard's memory column just shows 0)."""
    try:
        import psutil

        return float(psutil.Process(os.getpid()).memory_info().rss) / (1024 * 1024)
    except ImportError:
        pass
    try:
        import resource  # POSIX-only; Windows stub omits getrusage

        return (
            float(
                resource.getrusage(resource.RUSAGE_SELF).ru_maxrss  # type: ignore[attr-defined]
            )
            / 1024
        )
    except Exception:
        return 0.0


class Heartbeat:
    """Periodically flushes :class:`WorkerInfo` to DB + live bus."""

    def __init__(self, info: WorkerInfo, publish: Any | None = None) -> None:
        """``publish`` is the async ``ws.publish`` function; None disables."""
        self._info = info
        self._publish = publish
        self._task: asyncio.Task[None] | None = None
        self._stopping: asyncio.Event | None = None

    @property
    def info(self) -> WorkerInfo:
        return self._info

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stopping = asyncio.Event()
        # First tick immediately — dashboard shouldn't wait 15s to see a new worker.
        await self._tick()
        self._task = asyncio.create_task(self._loop(), name=f"worker.heartbeat.{self._info.worker_id}")
        log.info("worker.heartbeat.start", worker_id=self._info.worker_id)

    async def stop(self) -> None:
        if self._stopping is not None:
            self._stopping.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        # Final mark-offline so the dashboard shows the right state immediately.
        factory = get_session_factory()
        try:
            async with factory() as s, s.begin():
                await WorkerRepo.mark_offline(s, self._info.worker_id)
        except Exception as e:
            log.warning("worker.heartbeat.mark_offline_failed", error=str(e))
        self._stopping = None
        log.info("worker.heartbeat.stop", worker_id=self._info.worker_id)

    async def _loop(self) -> None:
        assert self._stopping is not None
        stopping = self._stopping
        while not stopping.is_set():
            try:
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.warning("worker.heartbeat.tick_failed", error=str(e))
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stopping.wait(), timeout=HEARTBEAT_INTERVAL_SECONDS)

    async def _tick(self) -> None:
        self._info.memory_mb = _current_memory_mb()
        now = now_utc()
        factory = get_session_factory()
        async with factory() as s, s.begin():
            await WorkerRepo.upsert_heartbeat(
                s,
                worker_id=self._info.worker_id,
                host=self._info.host,
                state=self._info.state.value,
                current_task_id=self._info.current_task_id,
                browser_context_count=self._info.browser_context_count,
                memory_mb=self._info.memory_mb,
                last_heartbeat_at=now,
            )
        worker_heartbeat_age_seconds.labels(worker_id=self._info.worker_id).set(0)
        worker_busy.labels(worker_id=self._info.worker_id).set(
            1 if self._info.state == WorkerState.BUSY else 0
        )

        # Publish event so WS clients see the update without polling.
        if self._publish is not None:
            from uuid import UUID

            try:
                current_task_uuid = UUID(self._info.current_task_id) if self._info.current_task_id else None
            except ValueError:
                current_task_uuid = None
            ev = WorkerHeartbeat(
                worker_id=self._info.worker_id,
                host=self._info.host,
                state=self._info.state,
                current_task_id=current_task_uuid,
                browser_context_count=self._info.browser_context_count,
                memory_mb=self._info.memory_mb,
            )
            try:
                await self._publish(ev)
            except Exception as e:
                log.warning("worker.heartbeat.publish_failed", error=str(e))

    # ---------- state transitions used by the runtime ---------- #

    def set_state(self, state: WorkerState) -> None:
        self._info.state = state
        worker_busy.labels(worker_id=self._info.worker_id).set(1 if state == WorkerState.BUSY else 0)

    def set_current_task(self, task_id: str | None) -> None:
        self._info.current_task_id = task_id
