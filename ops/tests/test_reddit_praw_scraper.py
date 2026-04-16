"""Reddit PRAW plugin tests with fake asyncpraw objects.

We don't hit live Reddit in CI. Instead we fake the surface asyncpraw
exposes — ``Submission``, ``Comment``, ``Subreddit``, ``Redditor`` — and
verify:
    1. Mode dispatch (SEARCH / PERMALINK / PROFILE).
    2. Credentials → Reddit kwargs (refresh_token vs password flow).
    3. The parse helpers produce normalizer-shaped raw dicts.
    4. Missing-credentials short-circuit returns empty (lets JSON fallback
       take over without errors propagating up).

The end-to-end normalizer parity test covers the raw → validator-blob path.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import pytest

from normalizer.reddit import RedditNormalizer
from shared.pipeline import ScrapeTaskEnvelope
from shared.schemas import ScrapeTaskMode, Source
from worker.plugins.reddit import praw_scraper
from worker.plugins.reddit.parse import (
    build_comment_raw,
    build_post_raw,
    extract_media_urls_from_submission,
)
from worker.plugins.reddit.praw_scraper import (
    PRAWRedditScraper,
    _looks_like_comment_url,
    _open_reddit,
    _strip_r_prefix,
)
from worker.scraper_api import ScrapeContext

# ---------- fakes ---------- #


@dataclass
class _FakeAuthor:
    name: str


@dataclass
class _FakeSubreddit:
    display_name: str = "bittensor_"
    over18: bool = False

    async def load(self) -> None: ...


@dataclass
class _FakeSubmission:
    name: str = "t3_abc123"
    permalink: str = "/r/bittensor_/comments/abc123/slug/"
    subreddit_name_prefixed: str = "r/bittensor_"
    selftext: str = "body text"
    title: str = "Hello"
    over_18: bool = False
    created_utc: float = 1_744_718_000.0  # 2025-04-15T09:53:20Z
    score: int = 42
    upvote_ratio: float = 0.95
    num_comments: int = 3
    url: str | None = "https://i.redd.it/abc.jpg"
    author: _FakeAuthor | None = field(default_factory=lambda: _FakeAuthor("alice"))
    preview: dict[str, Any] | None = None
    media_metadata: dict[str, Any] | None = None
    subreddit: _FakeSubreddit = field(default_factory=_FakeSubreddit)

    async def load(self) -> None: ...


@dataclass
class _FakeComment:
    name: str = "t1_xyz789"
    permalink: str = "/r/bittensor_/comments/abc123/slug/xyz789/"
    subreddit_name_prefixed: str = "r/bittensor_"
    body: str = "nice post"
    created_utc: float = 1_744_718_120.0
    score: int = 5
    parent_id: str = "t3_abc123"
    author: _FakeAuthor | None = field(default_factory=lambda: _FakeAuthor("bob"))
    submission: _FakeSubmission = field(default_factory=_FakeSubmission)
    subreddit: _FakeSubreddit = field(default_factory=_FakeSubreddit)

    async def load(self) -> None: ...


class _AsyncList:
    """Small helper — makes ``async for`` iterate a regular list."""

    def __init__(self, items: list[Any]) -> None:
        self._items = items

    def __aiter__(self) -> AsyncIterator[Any]:
        return self._gen()

    async def _gen(self) -> AsyncIterator[Any]:
        for item in self._items:
            yield item


class _FakeRedditSubreddit:
    def __init__(self, posts: list[Any], comments: list[Any]) -> None:
        self._posts = posts
        self._comments = comments

    def new(self, *, limit: int) -> _AsyncList:
        return _AsyncList(self._posts[:limit])

    def hot(self, *, limit: int) -> _AsyncList:
        return _AsyncList(self._posts[:limit])

    def top(self, *, time_filter: str, limit: int) -> _AsyncList:
        return _AsyncList(self._posts[:limit])

    def comments(self, *, limit: int) -> _AsyncList:
        return _AsyncList(self._comments[:limit])


@dataclass
class _FakeUserStream:
    items: list[Any]

    def new(self, *, limit: int) -> _AsyncList:
        return _AsyncList(self.items[:limit])


@dataclass
class _FakeRedditor:
    submissions: _FakeUserStream
    comments: _FakeUserStream


class _FakeReddit:
    """Minimal async-praw stand-in that captures the surface our plugin uses."""

    def __init__(
        self,
        *,
        sub_posts: list[Any] | None = None,
        sub_comments: list[Any] | None = None,
        permalink_submission: _FakeSubmission | None = None,
        permalink_comment: _FakeComment | None = None,
        redditor_posts: list[Any] | None = None,
        redditor_comments: list[Any] | None = None,
    ) -> None:
        self._sub = _FakeRedditSubreddit(sub_posts or [], sub_comments or [])
        self._permalink_submission = permalink_submission
        self._permalink_comment = permalink_comment
        self._redditor = _FakeRedditor(
            submissions=_FakeUserStream(redditor_posts or []),
            comments=_FakeUserStream(redditor_comments or []),
        )
        self.closed = False

    async def subreddit(self, _name: str) -> _FakeRedditSubreddit:
        return self._sub

    async def submission(self, *, url: str) -> _FakeSubmission:
        assert self._permalink_submission is not None
        return self._permalink_submission

    async def comment(self, *, url: str) -> _FakeComment:
        assert self._permalink_comment is not None
        return self._permalink_comment

    async def redditor(self, _name: str) -> _FakeRedditor:
        return self._redditor

    async def close(self) -> None:
        self.closed = True


# ---------- parse helpers ---------- #


def test_build_post_raw_mirrors_sn13_shape() -> None:
    raw = build_post_raw(_FakeSubmission())
    assert raw["kind"] == "post"
    assert raw["id"] == "t3_abc123"
    assert raw["permalink"] == "/r/bittensor_/comments/abc123/slug/"
    assert raw["author"] == "alice"
    assert raw["subreddit_prefixed"] == "r/bittensor_"
    assert raw["title"] == "Hello"
    assert raw["parent_id"] is None
    assert raw["media"] == ["https://i.redd.it/abc.jpg"]
    assert raw["is_nsfw"] is False
    assert raw["score"] == 42


def test_build_post_raw_handles_deleted_author() -> None:
    raw = build_post_raw(_FakeSubmission(author=None))
    assert raw["author"] is None  # normalizer coalesces to [deleted]


def test_build_comment_raw_inherits_parent_nsfw() -> None:
    parent_nsfw = _FakeSubmission(over_18=True)
    comment = _FakeComment(submission=parent_nsfw)
    raw = build_comment_raw(comment)
    assert raw["is_nsfw"] is True
    assert raw["kind"] == "comment"
    assert raw["parent_id"] == "t3_abc123"


def test_build_comment_raw_inherits_subreddit_nsfw() -> None:
    nsfw_sub = _FakeSubreddit(over18=True)
    comment = _FakeComment(subreddit=nsfw_sub)
    assert build_comment_raw(comment)["is_nsfw"] is True


def test_extract_media_handles_preview_and_gallery() -> None:
    submission = _FakeSubmission(
        url="https://reddit.com/r/x/comments/1/",  # not media URL
        preview={
            "images": [
                {"source": {"url": "https://preview.redd.it/foo.png?auto=webp&s=abc"}},
            ]
        },
        media_metadata={
            "m1": {"s": {"u": "https://preview.redd.it/gallery1.jpg?auto=webp&s=xyz"}},
        },
    )
    urls = extract_media_urls_from_submission(submission)
    # Preview URL is rewritten preview.redd.it → i.redd.it.
    assert "https://i.redd.it/foo.png" in urls
    assert "https://i.redd.it/gallery1.jpg" in urls


def test_extract_media_dedupes_same_url_via_query_strip() -> None:
    submission = _FakeSubmission(
        url="https://i.redd.it/abc.jpg?auto=webp&s=deadbeef",
        preview={
            "images": [
                {"source": {"url": "https://i.redd.it/abc.jpg?auto=webp&s=cafef00d"}},
            ]
        },
    )
    assert extract_media_urls_from_submission(submission) == ["https://i.redd.it/abc.jpg"]


# ---------- dispatch / mode routing ---------- #


def _task(mode: ScrapeTaskMode, label: str, **params: Any) -> ScrapeTaskEnvelope:
    return ScrapeTaskEnvelope(
        task_id="00000000-0000-0000-0000-000000000001",
        source=Source.REDDIT,
        mode=mode,
        label=label,
        params=params,
    )


def _ctx(task: ScrapeTaskEnvelope, credentials: dict[str, Any] | None) -> ScrapeContext:
    # The PRAW plugin ignores page/context; None is fine for this unit test.
    return ScrapeContext(
        task=task,
        page=None,
        browser_context=None,
        worker_id="w-test",
        trace_id=task.task_id,
        credentials=credentials,
    )


@pytest.mark.asyncio
async def test_run_short_circuits_without_auth_or_proxy() -> None:
    scraper = PRAWRedditScraper()
    ctx = _ctx(_task(ScrapeTaskMode.SEARCH, "r/bittensor_"), credentials=None)
    # No credentials AND no proxy → silent empty (not an error; task is OK/empty).
    assert await scraper.run(ctx) == []


@pytest.mark.asyncio
async def test_run_falls_back_to_json_when_proxy_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """No credentials + proxy → JSON fallback. Verifies the dispatch seam
    without actually hitting reddit.com (json_scraper.run_json is patched)."""
    from proxy_pool.schemas import LeaseResponse as ProxyLeaseResponse
    from shared.clock import now_utc
    from worker.plugins.reddit import praw_scraper as ps

    called: dict[str, Any] = {}

    async def _fake_run_json(ctx: ScrapeContext, limit: int) -> list[dict[str, Any]]:
        called["ctx"] = ctx
        called["limit"] = limit
        return [{"kind": "post", "stub": True}]

    from worker.plugins.reddit import json_scraper as js

    monkeypatch.setattr(js, "run_json", _fake_run_json)
    _ = ps  # keep the import alive for the comment above

    task = _task(ScrapeTaskMode.SEARCH, "r/bittensor_", limit=7)
    proxy = ProxyLeaseResponse(
        lease_id="pl-1",
        proxy_id="p-1",
        url="http://proxy.example:8000",
        session_id="s-1",
        expires_at=now_utc(),
    )
    ctx = ScrapeContext(
        task=task,
        page=None,
        browser_context=None,
        worker_id="w-test",
        trace_id=task.task_id,
        credentials=None,
        proxy=proxy,
    )
    items = await PRAWRedditScraper().run(ctx)
    assert called["limit"] == 7
    assert items == [{"kind": "post", "stub": True}]


@pytest.mark.asyncio
async def test_search_mode_returns_posts(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeReddit(sub_posts=[_FakeSubmission()])

    async def _fake_open(_creds: dict[str, Any]) -> _FakeReddit:
        return fake

    monkeypatch.setattr(praw_scraper, "_open_reddit", _fake_open)

    scraper = PRAWRedditScraper()
    ctx = _ctx(
        _task(ScrapeTaskMode.SEARCH, "r/bittensor_", limit=5, fetch="posts"),
        credentials={"client_id": "x", "client_secret": "y", "refresh_token": "z"},
    )
    items = await scraper.run(ctx)
    assert len(items) == 1
    assert items[0]["kind"] == "post"
    assert fake.closed is True  # client is always closed


@pytest.mark.asyncio
async def test_search_mode_comments_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeReddit(sub_comments=[_FakeComment()])

    async def _fake_open(_c: dict[str, Any]) -> _FakeReddit:
        return fake

    monkeypatch.setattr(praw_scraper, "_open_reddit", _fake_open)
    scraper = PRAWRedditScraper()
    ctx = _ctx(
        _task(ScrapeTaskMode.SEARCH, "r/bittensor_", limit=5, fetch="comments"),
        credentials={"client_id": "x", "client_secret": "y", "refresh_token": "z"},
    )
    items = await scraper.run(ctx)
    assert len(items) == 1
    assert items[0]["kind"] == "comment"


@pytest.mark.asyncio
async def test_permalink_mode_post(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeReddit(permalink_submission=_FakeSubmission())

    async def _fake_open(_c: dict[str, Any]) -> _FakeReddit:
        return fake

    monkeypatch.setattr(praw_scraper, "_open_reddit", _fake_open)
    scraper = PRAWRedditScraper()
    url = "https://reddit.com/r/bittensor_/comments/abc123/slug/"
    ctx = _ctx(
        _task(ScrapeTaskMode.PERMALINK, url),
        credentials={"client_id": "x", "client_secret": "y", "refresh_token": "z"},
    )
    items = await scraper.run(ctx)
    assert items[0]["kind"] == "post"


@pytest.mark.asyncio
async def test_permalink_mode_comment(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeReddit(permalink_comment=_FakeComment())

    async def _fake_open(_c: dict[str, Any]) -> _FakeReddit:
        return fake

    monkeypatch.setattr(praw_scraper, "_open_reddit", _fake_open)
    scraper = PRAWRedditScraper()
    url = "https://reddit.com/r/bittensor_/comments/abc123/slug/xyz789/"
    ctx = _ctx(
        _task(ScrapeTaskMode.PERMALINK, url),
        credentials={"client_id": "x", "client_secret": "y", "refresh_token": "z"},
    )
    items = await scraper.run(ctx)
    assert items[0]["kind"] == "comment"


@pytest.mark.asyncio
async def test_profile_mode_yields_posts_and_comments(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeReddit(
        redditor_posts=[_FakeSubmission()],
        redditor_comments=[_FakeComment()],
    )

    async def _fake_open(_c: dict[str, Any]) -> _FakeReddit:
        return fake

    monkeypatch.setattr(praw_scraper, "_open_reddit", _fake_open)
    scraper = PRAWRedditScraper()
    ctx = _ctx(
        _task(ScrapeTaskMode.PROFILE, "spez"),
        credentials={"client_id": "x", "client_secret": "y", "refresh_token": "z"},
    )
    items = await scraper.run(ctx)
    kinds = sorted(i["kind"] for i in items)
    assert kinds == ["comment", "post"]


# ---------- small utilities ---------- #


def test_strip_r_prefix() -> None:
    assert _strip_r_prefix("r/bittensor_") == "bittensor_"
    assert _strip_r_prefix("/r/bittensor_") == "bittensor_"
    assert _strip_r_prefix("bittensor_") == "bittensor_"


def test_looks_like_comment_url() -> None:
    post = "https://reddit.com/r/x/comments/abc/slug/"
    comment = "https://reddit.com/r/x/comments/abc/slug/xyz/"
    assert not _looks_like_comment_url(post)
    assert _looks_like_comment_url(comment)


@pytest.mark.asyncio
async def test_open_reddit_prefers_refresh_token(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class _Spy:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    import asyncpraw

    monkeypatch.setattr(asyncpraw, "Reddit", _Spy)

    await _open_reddit(
        {
            "client_id": "cid",
            "client_secret": "csec",
            "refresh_token": "rtok",
            "username": "alice",
            "password": "should-be-ignored",
        }
    )
    assert captured["refresh_token"] == "rtok"
    assert "username" not in captured
    assert "password" not in captured
    assert captured["user_agent"].startswith("data-universe-ops:cid:")


@pytest.mark.asyncio
async def test_open_reddit_falls_back_to_password(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class _Spy:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    import asyncpraw

    monkeypatch.setattr(asyncpraw, "Reddit", _Spy)

    await _open_reddit(
        {
            "client_id": "cid",
            "client_secret": "csec",
            "username": "alice",
            "password": "hunter2",
            "user_agent": "custom/1.0",
        }
    )
    assert captured["username"] == "alice"
    assert captured["password"] == "hunter2"
    assert "refresh_token" not in captured
    assert captured["user_agent"] == "custom/1.0"


# ---------- normalizer round-trip ---------- #


def test_raw_items_feed_through_normalizer_cleanly() -> None:
    """End-to-end from fake asyncpraw objects → parse.py → normalizer.

    Catches any shape drift between the plugin output and the normalizer
    input contract without needing SN13 deps loaded.
    """
    post_raw = build_post_raw(_FakeSubmission())
    comment_raw = build_comment_raw(_FakeComment())

    normalizer = RedditNormalizer()
    p = normalizer.normalize(post_raw)
    c = normalizer.normalize(comment_raw)

    assert p.source == Source.REDDIT
    assert p.label == "r/bittensor_"
    assert c.uri.endswith("/_/xyz789")
