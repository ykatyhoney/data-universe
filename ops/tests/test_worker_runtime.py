"""Worker runtime — state machine tested with a fake scraper + fake
Playwright.

We don't install Chromium in CI. Instead we patch ``_ensure_browser`` and
``new_context`` so the runtime thinks it has a page; the fake scraper
either returns canned items or raises. This exercises:
- Scraper plugin invocation
- ScrapeResultEnvelope publish to scrape:results
- Account lease → release with outcome mapping
- Failure path: exception → recording.keep_har (mocked) + error in result
- Heartbeat transitions (BUSY during task → IDLE after)
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import fakeredis.aioredis
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet

from account_pool.crypto import CookieSealer
from account_pool.schemas import AccountImport
from account_pool.service import AccountPoolService
from datastore import streams
from shared.pipeline import (
    ConsumerGroup,
    ScrapeResultEnvelope,
    ScrapeTaskEnvelope,
    StreamName,
)
from shared.schemas import ScrapeOutcome, ScrapeTaskMode, Source, WorkerState
from worker import browser as browser_mod
from worker import recording as recording_mod
from worker import runtime as runtime_mod
from worker.runtime import WorkerRuntime
from worker.scraper_api import ScrapeContext, clear_registry, register_scraper

# ---------- fakes ---------- #


class _FakePage:
    async def goto(self, *_a: Any, **_k: Any) -> None: ...
    async def screenshot(self, *_a: Any, **_k: Any) -> None: ...
    def set_default_timeout(self, *_a: Any) -> None: ...
    def set_default_navigation_timeout(self, *_a: Any) -> None: ...


class _FakeContext:
    async def new_page(self) -> _FakePage:
        return _FakePage()

    async def add_cookies(self, *_a: Any) -> None: ...
    async def add_init_script(self, *_a: Any) -> None: ...
    async def close(self) -> None: ...


class _FakeBrowser:
    async def new_context(self, **_kw: Any) -> _FakeContext:
        return _FakeContext()

    async def close(self) -> None: ...


class _PassingScraper:
    source = Source.X

    def supports(self, _mode: ScrapeTaskMode) -> bool:
        return True

    async def run(self, ctx: ScrapeContext) -> list[dict[str, Any]]:
        return [{"hello": "world", "task_id": ctx.task.task_id}]


class _FailingScraper:
    source = Source.REDDIT

    def supports(self, _mode: ScrapeTaskMode) -> bool:
        return True

    async def run(self, _ctx: ScrapeContext) -> list[dict[str, Any]]:
        raise RuntimeError("simulated 403 block")


# ---------- fixtures ---------- #


@pytest_asyncio.fixture
async def fake_redis(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> AsyncIterator[fakeredis.aioredis.FakeRedis]:
    from account_pool import service as acct_svc
    from proxy_pool import service as proxy_svc
    from shared import infra

    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(acct_svc, "get_redis", lambda: client)
    monkeypatch.setattr(proxy_svc, "get_redis", lambda: client)
    monkeypatch.setattr(infra, "get_redis", lambda: client)
    monkeypatch.setattr(streams, "get_redis", lambda: client)
    # Isolate recording root per-test.
    root = tmp_path / "worker"
    monkeypatch.setattr(recording_mod, "_ROOT", root)
    monkeypatch.setattr(recording_mod, "_HAR_DIR", root / "har")
    monkeypatch.setattr(recording_mod, "_SCREENSHOT_DIR", root / "screenshot")
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture(autouse=True)
def _fresh_registry() -> None:
    clear_registry()


async def _make_account_service() -> AccountPoolService:
    svc = AccountPoolService(sealer=CookieSealer(Fernet.generate_key().decode()), proxy_pool=None)
    await svc.import_account(
        AccountImport(
            source="x",
            user_agent="Mozilla/5.0 (test)",
            cookies=[{"name": "auth_token", "value": "t", "domain": ".x.com"}],
        )
    )
    return svc


async def _drain_scrape_results() -> list[ScrapeResultEnvelope]:
    """Read everything currently on scrape:results (no consumer group)."""
    import json

    r = streams.get_redis()
    entries = await r.xrange(StreamName.SCRAPE_RESULTS.value, min="-", max="+")
    out: list[ScrapeResultEnvelope] = []
    for _msg_id, fields in entries:
        payload = fields.get("payload")
        if payload:
            out.append(ScrapeResultEnvelope.model_validate(json.loads(payload)))
    return out


# ---------- tests ---------- #


@pytest.mark.asyncio
async def test_happy_path_publishes_scrape_result(
    fake_redis: fakeredis.aioredis.FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ = fake_redis
    register_scraper(_PassingScraper())
    # Replace Playwright with our fake browser.
    fake_browser = _FakeBrowser()

    async def _fake_ensure_browser(self: WorkerRuntime) -> _FakeBrowser:
        return fake_browser

    async def _fake_new_context(*_a: Any, **_kw: Any) -> _FakeContext:
        return _FakeContext()

    monkeypatch.setattr(WorkerRuntime, "_ensure_browser", _fake_ensure_browser)
    monkeypatch.setattr(browser_mod, "new_context", _fake_new_context)
    monkeypatch.setattr(runtime_mod, "_TASK_TIMEOUT_SECONDS", 10)

    acct = await _make_account_service()
    runtime = WorkerRuntime(worker_id="w-test", account_pool=acct, proxy_pool=None)

    # Seed a task + run ONE handle; no need to spin the consumer loop.
    task = ScrapeTaskEnvelope(
        task_id="task-happy",
        source=Source.X,
        mode=ScrapeTaskMode.SEARCH,
        label="#bitcoin",
    )
    await runtime._handle_one(task)  # type: ignore[arg-type]

    results = await _drain_scrape_results()
    assert len(results) == 1
    assert results[0].task_id == "task-happy"
    assert results[0].outcome == ScrapeOutcome.OK
    assert len(results[0].items) == 1
    assert results[0].items[0]["hello"] == "world"
    # HAR dropped on success.
    assert not recording_mod.har_path("task-happy").is_file()


@pytest.mark.asyncio
async def test_failure_path_captures_har_and_records_error(
    fake_redis: fakeredis.aioredis.FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ = fake_redis
    register_scraper(_FailingScraper())

    fake_browser = _FakeBrowser()

    async def _fake_ensure_browser(self: WorkerRuntime) -> _FakeBrowser:
        return fake_browser

    async def _fake_new_context(*_a: Any, **_kw: Any) -> _FakeContext:
        return _FakeContext()

    # Force ``keep_har`` to succeed by writing a dummy HAR before close.
    def _fake_keep_har(task_id: str) -> object:
        path = recording_mod.har_path(task_id)
        recording_mod.ensure_dirs()
        path.write_text("{}")
        return path

    monkeypatch.setattr(WorkerRuntime, "_ensure_browser", _fake_ensure_browser)
    monkeypatch.setattr(browser_mod, "new_context", _fake_new_context)
    monkeypatch.setattr(recording_mod, "keep_har", _fake_keep_har)
    monkeypatch.setattr(runtime_mod, "_TASK_TIMEOUT_SECONDS", 10)

    runtime = WorkerRuntime(worker_id="w-test", account_pool=None, proxy_pool=None)

    task = ScrapeTaskEnvelope(
        task_id="task-fail",
        source=Source.REDDIT,
        mode=ScrapeTaskMode.SEARCH,
        label="r/bitcoin",
    )
    await runtime._handle_one(task)  # type: ignore[arg-type]

    results = await _drain_scrape_results()
    assert len(results) == 1
    assert results[0].task_id == "task-fail"
    assert results[0].outcome == ScrapeOutcome.ERROR
    assert results[0].error is not None
    assert "simulated 403 block" in results[0].error
    assert recording_mod.har_path("task-fail").is_file()


@pytest.mark.asyncio
async def test_no_scraper_for_source_is_silent_noop(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    _ = fake_redis
    # No scrapers registered → handle_one logs + returns.
    runtime = WorkerRuntime(worker_id="w-test", account_pool=None, proxy_pool=None)
    task = ScrapeTaskEnvelope(
        task_id="task-none",
        source=Source.YOUTUBE,
        mode=ScrapeTaskMode.SEARCH,
        label="#",
    )
    await runtime._handle_one(task)  # type: ignore[arg-type]
    assert len(await _drain_scrape_results()) == 0


@pytest.mark.asyncio
async def test_heartbeat_state_busy_during_task(
    fake_redis: fakeredis.aioredis.FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ = fake_redis
    observed: list[WorkerState] = []

    class _ObservingScraper(_PassingScraper):
        async def run(self, ctx: ScrapeContext) -> list[dict[str, Any]]:
            # Record the live state the heartbeat reports DURING the task.
            from worker.runtime import WorkerRuntime  # noqa: F401

            # The runtime's heartbeat info is accessible via the ctx's worker_id;
            # but we have the runtime directly in the closure of this test.
            observed.append(runtime._info.state)
            return await super().run(ctx)

    register_scraper(_ObservingScraper())

    fake_browser = _FakeBrowser()

    async def _fake_ensure_browser(self: WorkerRuntime) -> _FakeBrowser:
        return fake_browser

    async def _fake_new_context(*_a: Any, **_kw: Any) -> _FakeContext:
        return _FakeContext()

    monkeypatch.setattr(WorkerRuntime, "_ensure_browser", _fake_ensure_browser)
    monkeypatch.setattr(browser_mod, "new_context", _fake_new_context)

    runtime = WorkerRuntime(worker_id="w-hb", account_pool=None, proxy_pool=None)
    task = ScrapeTaskEnvelope(
        task_id="task-hb",
        source=Source.X,
        mode=ScrapeTaskMode.SEARCH,
        label="#x",
    )
    await runtime._handle_one(task)  # type: ignore[arg-type]

    assert observed == [WorkerState.BUSY]
    # And after the task completes we're back to IDLE.
    assert runtime._info.state == WorkerState.IDLE


@pytest.mark.asyncio
async def test_start_creates_consumer_group(
    fake_redis: fakeredis.aioredis.FakeRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ = fake_redis

    called: list[tuple[StreamName, str]] = []

    async def _fake_ensure_group(stream: StreamName, group: str) -> None:
        called.append((stream, group))

    monkeypatch.setattr(streams, "ensure_group", _fake_ensure_group)

    runtime = WorkerRuntime(worker_id="w-init", account_pool=None, proxy_pool=None)
    await runtime.start()
    try:
        assert (StreamName.SCRAPE_TASKS, ConsumerGroup.WORKERS) in called
    finally:
        await runtime.stop()
