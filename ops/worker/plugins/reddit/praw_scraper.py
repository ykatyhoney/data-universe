"""Reddit scraper — PRAW primary path via account-pool credentials.

The lease hands us ``ctx.credentials`` (one of the two AccountImport paths):

    {"client_id", "client_secret", "refresh_token"}           # OAuth install
    {"client_id", "client_secret", "username", "password"}    # script app

We spin up a short-lived asyncpraw.Reddit client per task. Reddit rate-limits
OAuth clients at 60 req/min per client-id, which is plenty — one account
running at the account-pool's ``BUDGET_PER_MINUTE=50`` is comfortably below
the ceiling, and failures here get mapped to ``AUTH_FAILED`` / ``BLOCKED``
through the runtime's outcome-mapping helper so quarantine kicks in without
bespoke error handling.

The JSON fallback (``reddit_json_scraper.py``) covers the gap when no
credentials are leased — the runtime picks whichever scraper is registered
for the source, so this module must tolerate being bypassed entirely.

Task modes handled (others → empty, logged):
    * SEARCH    — ``label`` is ``"r/<sub>"``; fetch recent posts + a slice
                  of comments.
    * PERMALINK — ``label`` is a full reddit URL; fetch that single
                  post/comment.
    * PROFILE   — ``label`` is a username; fetch that user's recent posts
                  and comments.
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from shared.logging import get_logger
from shared.schemas import ScrapeTaskMode, Source
from worker.plugins.reddit import json_scraper
from worker.plugins.reddit.parse import build_comment_raw, build_post_raw
from worker.scraper_api import ScrapeContext, Scraper, register_scraper

if TYPE_CHECKING:
    import asyncpraw

log = get_logger(__name__)

# Default page size per task. Strategist can override via task.params["limit"];
# kept modest so a minute of 50 leases yields 50 * 25 = 1250 items/min
# which clears the validator's sampling rate.
DEFAULT_LIMIT = 25


class PRAWScraperError(Exception):
    """Raised for PRAW-specific failures. The runtime maps exception-name/
    message keywords into the account pool's release outcome — see
    ``worker.runtime._account_outcome_from_exception``."""


class PRAWRedditScraper(Scraper):
    source = Source.REDDIT

    _SUPPORTED = frozenset({ScrapeTaskMode.SEARCH, ScrapeTaskMode.PERMALINK, ScrapeTaskMode.PROFILE})

    def supports(self, mode: ScrapeTaskMode) -> bool:
        return mode in self._SUPPORTED

    async def run(self, ctx: ScrapeContext) -> list[dict[str, Any]]:
        if not ctx.credentials:
            # No creds → JSON fallback over the lease's proxy.
            # Empty-return if neither credentials nor proxy are available
            # (some runtimes don't attach a proxy for pure-smoke-test tasks).
            if ctx.proxy is None:
                log.info("reddit.praw.no_auth_no_proxy", task_id=ctx.task.task_id)
                return []
            log.info("reddit.praw.falling_back_to_json", task_id=ctx.task.task_id)
            limit = int(ctx.task.params.get("limit", DEFAULT_LIMIT))
            return await json_scraper.run_json(ctx, limit)

        reddit = await _open_reddit(ctx.credentials)
        try:
            limit = int(ctx.task.params.get("limit", DEFAULT_LIMIT))
            if ctx.task.mode == ScrapeTaskMode.SEARCH:
                return await self._run_search(reddit, ctx, limit)
            if ctx.task.mode == ScrapeTaskMode.PERMALINK:
                return await self._run_permalink(reddit, ctx)
            if ctx.task.mode == ScrapeTaskMode.PROFILE:
                return await self._run_profile(reddit, ctx, limit)
            log.warning(
                "reddit.praw.unsupported_mode",
                mode=ctx.task.mode.value,
                task_id=ctx.task.task_id,
            )
            return []
        finally:
            # asyncpraw.Reddit is an async-context-manager; manual close
            # avoids the need to keep the caller inside a ``with``.
            await reddit.close()

    # ---------- per-mode handlers ---------- #

    async def _run_search(
        self,
        reddit: asyncpraw.Reddit,
        ctx: ScrapeContext,
        limit: int,
    ) -> list[dict[str, Any]]:
        subreddit_name = _strip_r_prefix(ctx.task.label)
        sub = await reddit.subreddit(subreddit_name)
        sort = ctx.task.params.get("sort") or random.choice(["new", "hot", "top"])
        time_filter = ctx.task.params.get("time_filter") or "day"

        # 50/50: fetch submissions OR comments for the period. Reddit's
        # endpoints can't filter on datetime so ``sort`` + ``time_filter``
        # are the best we can do. This randomisation spreads load across
        # both domains naturally — and matches SN13's own strategy.
        fetch_submissions = ctx.task.params.get("fetch", random.choice(["posts", "comments"])) == "posts"
        raw: list[dict[str, Any]] = []
        if fetch_submissions:
            if sort == "top":
                iterator = sub.top(time_filter=time_filter, limit=limit)
            elif sort == "hot":
                iterator = sub.hot(limit=limit)
            else:
                iterator = sub.new(limit=limit)
            async for submission in iterator:
                try:
                    raw.append(build_post_raw(submission))
                except Exception as e:
                    log.warning("reddit.praw.parse_post_failed", error=str(e))
        else:
            async for comment in sub.comments(limit=limit):
                try:
                    raw.append(build_comment_raw(comment))
                except Exception as e:
                    log.warning("reddit.praw.parse_comment_failed", error=str(e))
        return raw

    async def _run_permalink(
        self,
        reddit: asyncpraw.Reddit,
        ctx: ScrapeContext,
    ) -> list[dict[str, Any]]:
        url = ctx.task.label
        if _looks_like_comment_url(url):
            comment = await reddit.comment(url=url)
            await comment.load()
            parent = comment.submission
            await parent.load()
            sub = comment.subreddit
            await sub.load()
            return [build_comment_raw(comment)]
        submission = await reddit.submission(url=url)
        await submission.load()
        return [build_post_raw(submission)]

    async def _run_profile(
        self,
        reddit: asyncpraw.Reddit,
        ctx: ScrapeContext,
        limit: int,
    ) -> list[dict[str, Any]]:
        username = ctx.task.label.lstrip("u/").lstrip("/")
        user = await reddit.redditor(username)
        raw: list[dict[str, Any]] = []
        async for submission in user.submissions.new(limit=limit):
            try:
                raw.append(build_post_raw(submission))
            except Exception as e:
                log.warning("reddit.praw.parse_post_failed", error=str(e))
        async for comment in user.comments.new(limit=limit):
            try:
                raw.append(build_comment_raw(comment))
            except Exception as e:
                log.warning("reddit.praw.parse_comment_failed", error=str(e))
        return raw


# ---------- helpers ---------- #


async def _open_reddit(credentials: dict[str, Any]) -> asyncpraw.Reddit:
    """Build an asyncpraw.Reddit client from a credentials blob.

    Both script apps (``username``+``password``) and installed apps
    (``refresh_token``) are supported — presence of ``refresh_token`` wins.
    ``user_agent`` is required by Reddit's API TOS; we synthesise one from
    the client id if the blob doesn't carry one.
    """
    import asyncpraw

    kwargs: dict[str, Any] = {
        "client_id": credentials["client_id"],
        "client_secret": credentials["client_secret"],
        "user_agent": credentials.get("user_agent") or f"data-universe-ops:{credentials['client_id']}:v1",
    }
    if credentials.get("refresh_token"):
        kwargs["refresh_token"] = credentials["refresh_token"]
    else:
        kwargs["username"] = credentials["username"]
        kwargs["password"] = credentials["password"]
    return asyncpraw.Reddit(**kwargs)


def _strip_r_prefix(label: str) -> str:
    s = label.strip()
    if s.startswith("r/"):
        return s[2:]
    if s.startswith("/r/"):
        return s[3:]
    return s


def _looks_like_comment_url(url: str) -> bool:
    """Reddit URLs have exactly 6 path segments for comment permalinks:
    ``/r/<sub>/comments/<post_id>/<slug>/<comment_id>``. Anything ≤5 is a
    post URL."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    segs = [s for s in parts.path.split("/") if s]
    return len(segs) >= 6 and segs[0] == "r" and segs[2] == "comments"


# Register at import time.
register_scraper(PRAWRedditScraper())
