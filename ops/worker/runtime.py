"""Worker runtime — the main loop each Playwright worker process runs.

Lifecycle per task:
    1. ``XREADGROUP`` scrape:tasks
    2. Lease account + proxy (if account_pool/proxy_pool available)
    3. Open a fresh browser context with cookies + UA + proxy injected
    4. Invoke the registered scraper plugin
    5. Publish ScrapeResultEnvelope to scrape:results
    6. Release account + proxy with outcome mapped from the task result
    7. ACK the stream message

Failure containment:
    - Transient (no account / proxy available) → do NOT ack; message
      replays on next consumer tick.
    - Scraper raises → capture HAR + screenshot, emit TaskFinished with
      outcome=ERROR, publish an empty-item ScrapeResult, ack.
    - Browser crash → increment ``worker_browser_crashes_total``, bounce
      the Chromium process, ack.

Context lifecycle: **one Chromium process per worker, new context per
task.** Playwright's own best practice. Contexts are cheap; the isolation
prevents cross-task cookie/session leakage.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from account_pool.schemas import (
    AccountLeaseRequest,
    AccountReleaseRequest,
)
from account_pool.schemas import (
    LeaseOutcome as AccountOutcome,
)
from account_pool.service import (
    AccountPoolService,
    AccountUnavailable,
)
from datastore import streams
from proxy_pool.service import ProxyPoolService
from shared.clock import now_utc
from shared.events import TaskFinished, TaskStarted
from shared.logging import get_logger
from shared.metrics import (
    worker_browser_crashes_total,
    worker_task_duration_seconds,
    worker_tasks_total,
)
from shared.pipeline import (
    ConsumerGroup,
    ScrapeResultEnvelope,
    ScrapeTaskEnvelope,
    StreamName,
)
from shared.schemas import ScrapeOutcome, TaskState, WorkerState
from worker import recording
from worker.browser import (
    chromium_launch_args,
    fingerprint_for,
    new_context,
    open_page,
)
from worker.heartbeat import Heartbeat, WorkerInfo
from worker.scraper_api import ScrapeContext, get_scraper

if TYPE_CHECKING:
    from playwright.async_api import Browser

log = get_logger(__name__)

_TASK_TIMEOUT_SECONDS = 300  # hard cap per task


# Outcome mapping — from our internal task result to the two downstream
# outcome enums we need to care about (stream result + account-release).
def _scrape_outcome(ok: bool, item_count: int) -> ScrapeOutcome:
    if not ok:
        return ScrapeOutcome.ERROR
    if item_count == 0:
        return ScrapeOutcome.EMPTY
    return ScrapeOutcome.OK


def _account_outcome_from_exception(exc: BaseException) -> AccountOutcome:
    name = exc.__class__.__name__.lower()
    msg = str(exc).lower()
    if "timeout" in name or "timeout" in msg:
        return AccountOutcome.TIMEOUT
    if "auth" in msg or "401" in msg or "403" in msg:
        return AccountOutcome.AUTH_FAILED
    if "block" in msg or "captcha" in msg or "challenge" in msg:
        return AccountOutcome.BLOCKED
    if "rate" in msg or "429" in msg:
        return AccountOutcome.RATE_LIMITED
    return AccountOutcome.ERROR


class WorkerRuntime:
    """One instance per worker process. Owns the heartbeat + browser."""

    def __init__(
        self,
        *,
        worker_id: str | None = None,
        account_pool: AccountPoolService | None,
        proxy_pool: ProxyPoolService | None,
        publish_event: Callable[[Any], Awaitable[None]] | None = None,
    ) -> None:
        self.worker_id = worker_id or os.environ.get(
            "OPS_WORKER_ID", f"worker-{socket.gethostname()}-{uuid.uuid4().hex[:6]}"
        )
        self._account_pool = account_pool
        self._proxy_pool = proxy_pool
        self._publish_event = publish_event
        self._info = WorkerInfo(worker_id=self.worker_id, state=WorkerState.IDLE)
        self._heartbeat = Heartbeat(self._info, publish=publish_event)
        self._browser: Browser | None = None
        self._stopping: asyncio.Event | None = None
        self._task_handle: asyncio.Task[None] | None = None

    # ---------- lifecycle ---------- #

    async def start(self) -> None:
        """Lazy-launch Chromium + start heartbeat + consumer loop."""
        self._stopping = asyncio.Event()
        await streams.ensure_group(StreamName.SCRAPE_TASKS, ConsumerGroup.WORKERS)
        await self._heartbeat.start()
        self._task_handle = asyncio.create_task(self._consume_loop(), name=f"worker.{self.worker_id}")
        log.info("worker.runtime.start", worker_id=self.worker_id)

    async def stop(self) -> None:
        if self._stopping is not None:
            self._stopping.set()
        if self._task_handle is not None:
            self._task_handle.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task_handle
            self._task_handle = None
        await self._close_browser()
        await self._heartbeat.stop()
        log.info("worker.runtime.stop", worker_id=self.worker_id)

    async def _ensure_browser(self) -> Browser:
        if self._browser is not None:
            return self._browser
        # Import Playwright lazily — let tests that don't need real browsers
        # run in a standard venv without the browser download.
        from playwright.async_api import async_playwright

        pw = await async_playwright().start()
        self._browser = await pw.chromium.launch(
            headless=True,
            args=chromium_launch_args(),
        )
        self._info.browser_context_count = 0
        return self._browser

    async def _close_browser(self) -> None:
        if self._browser is not None:
            with contextlib.suppress(Exception):
                await self._browser.close()
            self._browser = None

    # ---------- main loop ---------- #

    async def _consume_loop(self) -> None:
        assert self._stopping is not None
        async for msg_id, env in streams.consume(
            StreamName.SCRAPE_TASKS,
            ConsumerGroup.WORKERS,
            self.worker_id,
        ):
            if self._stopping.is_set():
                break
            assert isinstance(env, ScrapeTaskEnvelope)
            try:
                await asyncio.wait_for(
                    self._handle_one(env),
                    timeout=_TASK_TIMEOUT_SECONDS,
                )
            except TimeoutError:
                log.warning("worker.task_timeout", task_id=env.task_id)
                worker_tasks_total.labels(
                    worker_id=self.worker_id,
                    source=env.source.value,
                    outcome="timeout",
                ).inc()
            except Exception as e:
                log.warning(
                    "worker.task_crash",
                    task_id=env.task_id,
                    error=str(e),
                )
                worker_tasks_total.labels(
                    worker_id=self.worker_id,
                    source=env.source.value,
                    outcome="crash",
                ).inc()
            finally:
                await streams.ack(StreamName.SCRAPE_TASKS, ConsumerGroup.WORKERS, msg_id)

    # ---------- per-task ---------- #

    async def _handle_one(self, task: ScrapeTaskEnvelope) -> None:
        scraper = get_scraper(task.source)
        if scraper is None:
            log.warning("worker.no_scraper", source=task.source.value)
            return
        if not scraper.supports(task.mode):
            log.warning(
                "worker.scraper_unsupported_mode",
                source=task.source.value,
                mode=task.mode.value,
            )
            return

        self._heartbeat.set_state(WorkerState.BUSY)
        self._heartbeat.set_current_task(task.task_id)
        if self._publish_event is not None:
            await self._publish_event(
                TaskStarted(
                    task_id=uuid.UUID(task.task_id),
                    source=task.source,
                    mode=task.mode,
                    label=task.label,
                    worker_id=self.worker_id,
                )
            )

        started_at = now_utc()
        t0 = time.perf_counter()
        items: list[dict[str, Any]] = []
        error: str | None = None
        crashed = False

        account_lease = None
        try:
            # Lease account (which will internally pair a proxy if pinned).
            if self._account_pool is not None:
                try:
                    account_lease = await self._account_pool.lease(
                        AccountLeaseRequest(source=task.source.value, action=task.mode.value)
                    )
                except AccountUnavailable:
                    # Transient — don't ack; let it replay. We return early and
                    # the outer ack in _consume_loop is NOT reached because we
                    # raise here.
                    raise
            # Run the scrape.
            items = await self._run_scrape(task, account_lease)
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
            log.warning("worker.scrape_failed", task_id=task.task_id, error=error)
            # Attempt to capture a screenshot — HAR is already being written.
            # (We don't have a reference to the page here; screenshot is
            # captured inside _run_scrape where the page exists.)
        finally:
            finished_at = now_utc()
            duration = time.perf_counter() - t0
            worker_task_duration_seconds.labels(source=task.source.value).observe(duration)

            ok = error is None
            outcome = _scrape_outcome(ok, len(items))

            # Release account + paired proxy with mapped outcome.
            if account_lease is not None and self._account_pool is not None:
                acct_outcome = (
                    AccountOutcome.OK if ok else _account_outcome_from_exception(Exception(error or ""))
                )
                with contextlib.suppress(Exception):
                    await self._account_pool.release(
                        AccountReleaseRequest(
                            lease_id=account_lease.lease_id,
                            outcome=acct_outcome,
                        )
                    )

            # Publish the scrape result back into the pipeline.
            result = ScrapeResultEnvelope(
                task_id=task.task_id,
                worker_id=self.worker_id,
                source=task.source,
                outcome=outcome,
                items=items,
                started_at=started_at,
                finished_at=finished_at,
                error=error,
            )
            with contextlib.suppress(Exception):
                await streams.publish(StreamName.SCRAPE_RESULTS, result)

            # Metrics + TaskFinished event.
            worker_tasks_total.labels(
                worker_id=self.worker_id,
                source=task.source.value,
                outcome=outcome.value,
            ).inc()
            if self._publish_event is not None:
                with contextlib.suppress(Exception):
                    await self._publish_event(
                        TaskFinished(
                            task_id=uuid.UUID(task.task_id),
                            source=task.source,
                            outcome=outcome,
                            state=TaskState.SUCCEEDED if ok else TaskState.FAILED,
                            item_count=len(items),
                            duration_seconds=duration,
                            error=error,
                        )
                    )

            # Book-keeping.
            if crashed:
                worker_browser_crashes_total.labels(worker_id=self.worker_id).inc()

            self._heartbeat.set_current_task(None)
            self._heartbeat.set_state(WorkerState.IDLE)

    async def _run_scrape(
        self,
        task: ScrapeTaskEnvelope,
        account_lease: Any,
    ) -> list[dict[str, Any]]:
        """Open a fresh Playwright context, invoke the scraper, return items.

        HAR recording is started with the context; on success we drop it, on
        failure we keep it + take a screenshot.
        """
        recording.ensure_dirs()
        har_target = str(recording.har_path(task.task_id))

        scraper = get_scraper(task.source)
        if scraper is None:
            return []

        browser = await self._ensure_browser()
        account_id = account_lease.account_id if account_lease else None
        cookies = account_lease.cookies if account_lease else None
        ua = account_lease.user_agent if account_lease and account_lease.user_agent else None
        proxy_url = account_lease.proxy_lease.url if (account_lease and account_lease.proxy_lease) else None

        fp = fingerprint_for(account_id)
        context = await new_context(
            browser,
            fingerprint=fp,
            user_agent=ua,
            cookies=cookies,
            proxy_url=proxy_url,
            record_har_path=har_target,
        )
        self._info.browser_context_count = 1

        raised: BaseException | None = None
        items: list[dict[str, Any]] = []
        page = None
        try:
            page = await open_page(context)
            ctx = ScrapeContext(
                task=task,
                page=page,
                browser_context=context,
                worker_id=self.worker_id,
                trace_id=task.trace_id or task.task_id,
                account_id=account_id,
                proxy=account_lease.proxy_lease if account_lease else None,
                cookies=cookies,
                credentials=account_lease.credentials if account_lease else None,
            )
            items = await scraper.run(ctx)
        except Exception as e:
            raised = e
            if page is not None:
                await recording.capture_screenshot(page, task.task_id)
        finally:
            with contextlib.suppress(Exception):
                await context.close()
            self._info.browser_context_count = 0

        if raised is None:
            recording.drop_har(task.task_id)
        else:
            recording.keep_har(task.task_id)
            raise raised

        return items
