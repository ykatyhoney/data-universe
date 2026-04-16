"""Reddit JSON fallback tests.

We never hit live reddit.com. Responses are served by a fixture-provided
HTTPX ``MockTransport`` so we test the full path: route dispatch, JSON
shape adaptation, error-status propagation, and parity through the
normalizer.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from normalizer.reddit import RedditNormalizer
from proxy_pool.schemas import LeaseResponse as ProxyLeaseResponse
from shared.clock import now_utc
from shared.pipeline import ScrapeTaskEnvelope
from shared.schemas import ScrapeTaskMode, Source
from worker.plugins.reddit import json_scraper
from worker.plugins.reddit.json_scraper import (
    BASE,
    RedditJSONError,
    _permalink_path,
    _strip_r_prefix,
    run_json,
)
from worker.scraper_api import ScrapeContext

# ---------- fixtures ---------- #


def _task(mode: ScrapeTaskMode, label: str, **params: Any) -> ScrapeTaskEnvelope:
    return ScrapeTaskEnvelope(
        task_id="00000000-0000-0000-0000-000000000002",
        source=Source.REDDIT,
        mode=mode,
        label=label,
        params=params,
    )


def _ctx(task: ScrapeTaskEnvelope, *, with_proxy: bool = True) -> ScrapeContext:
    proxy = None
    if with_proxy:
        proxy = ProxyLeaseResponse(
            lease_id="pl-1",
            proxy_id="p-1",
            url="http://user:pass@proxy.example:8000",
            session_id="session-1",
            expires_at=now_utc(),
        )
    return ScrapeContext(
        task=task,
        page=None,
        browser_context=None,
        worker_id="w-test",
        trace_id=task.task_id,
        proxy=proxy,
    )


def _listing(kind: str, children: list[dict[str, Any]]) -> dict[str, Any]:
    return {"kind": "Listing", "data": {"children": [{"kind": kind, "data": c} for c in children]}}


def _sample_post(**over: Any) -> dict[str, Any]:
    base = {
        "id": "abc123",
        "permalink": "/r/bittensor_/comments/abc123/slug/",
        "author": "alice",
        "subreddit_name_prefixed": "r/bittensor_",
        "selftext": "body",
        "title": "Hello",
        "created_utc": 1_744_718_000.0,
        "over_18": False,
        "score": 42,
        "upvote_ratio": 0.95,
        "num_comments": 3,
        "url": "https://i.redd.it/abc.jpg",
    }
    base.update(over)
    return base


def _sample_comment(**over: Any) -> dict[str, Any]:
    base = {
        "id": "xyz789",
        "permalink": "/r/bittensor_/comments/abc123/slug/xyz789/",
        "author": "bob",
        "subreddit_name_prefixed": "r/bittensor_",
        "body": "nice post",
        "created_utc": 1_744_718_120.0,
        "parent_id": "t3_abc123",
        "score": 5,
    }
    base.update(over)
    return base


# Patch httpx.AsyncClient so the plugin's fresh-client-per-call pattern
# gets a mock transport. Simpler than patching _get_json directly —
# we stay one layer above httpx so proxy / timeout / headers still flow
# through the real client code.
@pytest.fixture
def mock_http(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"handler": None, "calls": []}

    original = httpx.AsyncClient

    def _patched(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        state["calls"].append(kwargs)
        # Replace transport with the test's handler.
        handler = state["handler"] or (lambda _r: httpx.Response(200, json={}))
        kwargs["transport"] = httpx.MockTransport(handler)
        kwargs.pop("proxy", None)  # MockTransport doesn't use proxies
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _patched)
    _ = json_scraper  # ensure module is loaded so the patched client is used
    return state


# ---------- tests ---------- #


@pytest.mark.asyncio
async def test_search_returns_posts(mock_http: dict[str, Any]) -> None:
    mock_http["handler"] = lambda _r: httpx.Response(
        200,
        json=_listing("t3", [_sample_post(), _sample_post(id="def456")]),
    )
    items = await run_json(_ctx(_task(ScrapeTaskMode.SEARCH, "r/bittensor_", limit=5, sort="new")), 5)
    assert len(items) == 2
    assert all(i["kind"] == "post" for i in items)
    # Plugin synthesises `name` from `id` (JSON endpoint omits the t3_ prefix).
    assert items[0]["id"] == "t3_abc123"


@pytest.mark.asyncio
async def test_search_top_mode_passes_time_filter(mock_http: dict[str, Any]) -> None:
    seen: dict[str, Any] = {}

    def _h(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json=_listing("t3", [_sample_post()]))

    mock_http["handler"] = _h
    await run_json(
        _ctx(_task(ScrapeTaskMode.SEARCH, "r/bittensor_", sort="top", time_filter="week")),
        5,
    )
    assert "/r/bittensor_/top.json" in seen["url"]
    assert "t=week" in seen["url"]


@pytest.mark.asyncio
async def test_permalink_mode_returns_post_plus_comments(mock_http: dict[str, Any]) -> None:
    post_listing = _listing("t3", [_sample_post(over_18=True)])  # NSFW post
    comment_listing = _listing("t1", [_sample_comment()])

    mock_http["handler"] = lambda _r: httpx.Response(200, json=[post_listing, comment_listing])

    items = await run_json(
        _ctx(_task(ScrapeTaskMode.PERMALINK, "https://www.reddit.com/r/x/comments/abc123/slug/")),
        25,
    )
    kinds = [i["kind"] for i in items]
    assert kinds == ["post", "comment"]
    # Comment inherits NSFW from parent post.
    assert items[1]["is_nsfw"] is True


@pytest.mark.asyncio
async def test_permalink_malformed_body_returns_empty(mock_http: dict[str, Any]) -> None:
    # Not a list → the response isn't a permalink listing.
    mock_http["handler"] = lambda _r: httpx.Response(200, json={"oops": "wrong-shape"})
    items = await run_json(
        _ctx(_task(ScrapeTaskMode.PERMALINK, "https://www.reddit.com/r/x/comments/abc/slug/")),
        25,
    )
    assert items == []


@pytest.mark.asyncio
async def test_rate_limited_raises(mock_http: dict[str, Any]) -> None:
    mock_http["handler"] = lambda _r: httpx.Response(429, text="slow down")
    with pytest.raises(RedditJSONError, match="rate_limited"):
        await run_json(_ctx(_task(ScrapeTaskMode.SEARCH, "r/bittensor_")), 5)


@pytest.mark.asyncio
async def test_auth_failed_raises(mock_http: dict[str, Any]) -> None:
    mock_http["handler"] = lambda _r: httpx.Response(403, text="blocked")
    with pytest.raises(RedditJSONError, match="auth_failed"):
        await run_json(_ctx(_task(ScrapeTaskMode.SEARCH, "r/bittensor_")), 5)


@pytest.mark.asyncio
async def test_timeout_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    original = httpx.AsyncClient

    class _ToutClient:
        def __init__(self, *_a: Any, **_k: Any) -> None: ...

        async def __aenter__(self) -> _ToutClient:
            return self

        async def __aexit__(self, *_a: Any) -> None: ...

        async def get(self, *_a: Any, **_k: Any) -> None:
            raise httpx.ConnectTimeout("simulated")

    monkeypatch.setattr(httpx, "AsyncClient", _ToutClient)

    with pytest.raises(RedditJSONError, match="timeout"):
        await run_json(_ctx(_task(ScrapeTaskMode.SEARCH, "r/bittensor_")), 5)
    _ = original  # silence "imported but unused" if timeouts fail to fire


@pytest.mark.asyncio
async def test_unsupported_mode_is_silent_noop(mock_http: dict[str, Any]) -> None:
    items = await run_json(_ctx(_task(ScrapeTaskMode.CHANNEL, "r/bittensor_")), 5)
    assert items == []


@pytest.mark.asyncio
async def test_json_items_feed_through_normalizer(mock_http: dict[str, Any]) -> None:
    mock_http["handler"] = lambda _r: httpx.Response(200, json=_listing("t3", [_sample_post()]))
    items = await run_json(_ctx(_task(ScrapeTaskMode.SEARCH, "r/bittensor_")), 5)
    normalized = RedditNormalizer().normalize(items[0])
    # The label must be lowercase r/sub and content must be valid JSON.
    assert normalized.label == "r/bittensor_"
    json.loads(normalized.normalized_json["content"])


# ---------- small helpers ---------- #


def test_permalink_path_adds_json_suffix() -> None:
    assert _permalink_path("https://www.reddit.com/r/x/comments/abc/slug/") == "/r/x/comments/abc/slug.json"
    assert _permalink_path("/r/x/comments/abc/slug") == "/r/x/comments/abc/slug.json"
    assert _permalink_path("/r/x/comments/abc/slug.json") == "/r/x/comments/abc/slug.json"


def test_strip_r_prefix_variants() -> None:
    assert _strip_r_prefix("r/bittensor_") == "bittensor_"
    assert _strip_r_prefix("/r/bittensor_") == "bittensor_"


def test_base_url_is_www() -> None:
    # Posts from `www.reddit.com` match the RedditContent URL format emitted
    # by the normalizer; using `old.` or bare `reddit.com` would diverge.
    assert BASE == "https://www.reddit.com"
