"""Reddit scraper — public JSON-endpoint fallback.

Reddit exposes ``.json``-suffixed read-only endpoints that require no
auth but are IP-rate-limited. When PRAW credentials aren't available
(e.g. an account was quarantined, or a task targets a scope the OAuth
app can't reach) the JSON path still produces validator-parity content.

Endpoints used:
    * ``/r/<sub>/new.json?limit=N``         — listing
    * ``/r/<sub>/top.json?t=<window>&limit=N``
    * ``/r/<sub>/comments/<id>.json``       — single post + comment tree

Rate-limit discipline: exactly one outstanding HTTP call per lease. The
runtime routes every call through the proxy pool — a single thread per
worker, with a session-sticky proxy — so we never exceed Reddit's per-IP
ceilings.

The JSON field names differ slightly from the PRAW object attribute names
(``over_18`` vs ``over_18``, ``subreddit_name_prefixed`` same, etc.).
``_wrap`` adapts a JSON dict into the duck-type expected by the shared
``parse.build_post_raw`` / ``build_comment_raw`` helpers.
"""

from __future__ import annotations

import random
from typing import Any

import httpx

from shared.logging import get_logger
from shared.schemas import ScrapeTaskMode
from worker.plugins.reddit.parse import build_comment_raw, build_post_raw
from worker.scraper_api import ScrapeContext

log = get_logger(__name__)

BASE = "https://www.reddit.com"
TIMEOUT_SECONDS = 15.0
DEFAULT_LIMIT = 25
# Reddit's public endpoints reject common default UAs. Use a believable
# one so a bare-IP request doesn't get auto-flagged.
DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; data-universe-ops/1.0)"


class RedditJSONError(Exception):
    """Propagated up so the runtime's exception-→-outcome mapping kicks in
    (``429`` → ``RATE_LIMITED``, ``403`` → ``BLOCKED``, etc.)."""


async def run_json(ctx: ScrapeContext, limit: int) -> list[dict[str, Any]]:
    """Dispatch ``ctx.task.mode`` over the public JSON endpoints."""
    if ctx.task.mode == ScrapeTaskMode.SEARCH:
        return await _search(ctx, limit)
    if ctx.task.mode == ScrapeTaskMode.PERMALINK:
        return await _permalink(ctx)
    log.warning("reddit.json.unsupported_mode", mode=ctx.task.mode.value)
    return []


# ---------- per-mode ---------- #


async def _search(ctx: ScrapeContext, limit: int) -> list[dict[str, Any]]:
    sub = _strip_r_prefix(ctx.task.label)
    sort = ctx.task.params.get("sort") or random.choice(["new", "hot", "top"])
    time_filter = ctx.task.params.get("time_filter") or "day"

    query = {"limit": str(limit), "raw_json": "1"}
    if sort == "top":
        path = f"/r/{sub}/top.json"
        query["t"] = time_filter
    elif sort == "hot":
        path = f"/r/{sub}/hot.json"
    else:
        path = f"/r/{sub}/new.json"

    payload = await _get_json(ctx, path, params=query)
    return [build_post_raw(_wrap_post(child["data"])) for child in _children(payload, "t3")]


async def _permalink(ctx: ScrapeContext) -> list[dict[str, Any]]:
    """Fetch a post's comment tree. Returns the post + every top-level
    comment. ``more`` placeholders are ignored — the pipeline can requeue
    them as separate tasks later if we decide to go deep."""
    path = _permalink_path(ctx.task.label)
    data = await _get_json(ctx, path, params={"raw_json": "1"})
    if not isinstance(data, list) or len(data) < 2:
        return []

    items: list[dict[str, Any]] = []
    # data[0] = post listing, data[1] = comment listing.
    for child in _children(data[0], "t3"):
        items.append(build_post_raw(_wrap_post(child["data"])))
    for child in _children(data[1], "t1"):
        items.append(build_comment_raw(_wrap_comment(child["data"], post_data=data[0])))
    return items


# ---------- HTTP ---------- #


async def _get_json(ctx: ScrapeContext, path: str, *, params: dict[str, str]) -> Any:
    """Fetch ``BASE + path`` via the lease's proxy (if present).

    Raises :class:`RedditJSONError` on non-2xx so the runtime's release
    path maps 401/403/429/timeout into an accurate account+proxy outcome.
    """
    proxy_url = ctx.proxy.url if ctx.proxy else None
    headers = {"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/json"}

    try:
        # ``proxy`` (not ``proxies``) — httpx 0.28+ accepts a single URL.
        async with httpx.AsyncClient(
            timeout=TIMEOUT_SECONDS,
            headers=headers,
            follow_redirects=True,
            proxy=proxy_url,
        ) as client:
            resp = await client.get(BASE + path, params=params)
    except httpx.TimeoutException as e:
        raise RedditJSONError(f"timeout: {e}") from e
    except httpx.HTTPError as e:
        raise RedditJSONError(f"http error: {e}") from e

    if resp.status_code == 429:
        raise RedditJSONError(f"rate_limited (429) for {path}")
    if resp.status_code in (401, 403):
        raise RedditJSONError(f"auth_failed ({resp.status_code}) for {path}")
    if resp.status_code >= 400:
        raise RedditJSONError(f"http {resp.status_code} for {path}")
    try:
        return resp.json()
    except ValueError as e:
        raise RedditJSONError(f"non-json body: {e}") from e


# ---------- shape adaptation ---------- #


def _children(payload: Any, kind: str) -> list[dict[str, Any]]:
    """Extract ``data.children`` of the given kind from a Reddit Listing."""
    if not isinstance(payload, dict):
        return []
    data = payload.get("data") or {}
    children = data.get("children") or []
    return [c for c in children if isinstance(c, dict) and c.get("kind") == kind]


class _DictProxy:
    """Exposes dict entries as attributes so ``build_post_raw`` /
    ``build_comment_raw`` can treat PRAW objects and JSON payloads
    uniformly. Unknown attrs return ``None``."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def __getattr__(self, item: str) -> Any:
        return self._data.get(item)


def _wrap_author(data: dict[str, Any]) -> dict[str, Any]:
    """Reddit's JSON gives ``author`` as a plain string (or ``"[deleted]"``);
    PRAW gives an object with a ``.name`` attribute. ``build_*_raw`` reads
    ``author.name``, so wrap the string behind a shim — and treat
    ``"[deleted]"`` as ``None`` so the normalizer coalesces to the sentinel
    once, not twice."""
    author = data.get("author")
    if author in (None, "", "[deleted]"):
        return {**data, "author": None}
    return {**data, "author": _DictProxy({"name": author})}


def _wrap_post(data: dict[str, Any]) -> _DictProxy:
    # Reddit's JSON gives ``id`` without the ``t3_`` prefix; PRAW's ``name``
    # has it. build_post_raw reads ``name``, so synthesise it.
    data = _wrap_author(data)
    if "name" not in data and "id" in data:
        data = {**data, "name": f"t3_{data['id']}"}
    return _DictProxy(data)


def _wrap_comment(data: dict[str, Any], *, post_data: Any) -> _DictProxy:
    data = _wrap_author(data)
    if "name" not in data and "id" in data:
        data = {**data, "name": f"t1_{data['id']}"}
    # Comments need a parent-submission handle so build_comment_raw can read
    # ``over_18`` for NSFW inheritance. Grab the post from the caller's
    # top-of-listing payload; subreddit NSFW is mirrored from the same.
    post_rows = _children(post_data, "t3")
    post_d = post_rows[0]["data"] if post_rows else {}
    submission = _DictProxy({"over_18": bool(post_d.get("over_18", False))})
    subreddit = _DictProxy({"over18": bool(post_d.get("over_18", False))})
    proxy = _DictProxy(data)
    # Attach after construction — __getattr__ only runs when a key is missing
    # from _data, so we don't need to stash these specially.
    proxy._data["submission"] = submission
    proxy._data["subreddit"] = subreddit
    return proxy


# ---------- helpers ---------- #


def _strip_r_prefix(label: str) -> str:
    s = label.strip()
    if s.startswith("/r/"):
        return s[3:]
    if s.startswith("r/"):
        return s[2:]
    return s


def _permalink_path(url: str) -> str:
    """Extract the path from a full Reddit URL + append ``.json``.

    Accepts both ``https://www.reddit.com/r/x/comments/.../`` and raw
    ``/r/x/comments/.../`` shapes.
    """
    if url.startswith("http"):
        from urllib.parse import urlsplit

        path = urlsplit(url).path
    else:
        path = url
    path = path.rstrip("/")
    if not path.endswith(".json"):
        path = path + ".json"
    return path
