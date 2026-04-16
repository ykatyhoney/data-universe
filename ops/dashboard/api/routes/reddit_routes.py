"""Reddit-specific dashboard routes — per-subreddit coverage + PRAW health.

These read from ``stg_normalized_items`` plus the account-pool snapshot. The
pipeline is the sole writer; this route only reads. All endpoints are
cookie-gated via :class:`AuthDep`.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from account_pool.service import get_service as get_account_service
from dashboard.api.auth import AuthDep
from datastore.repositories import StgNormalizedItemRepo
from shared.infra import get_session_factory

router = APIRouter(prefix="/api/reddit", tags=["reddit"])


class SubredditCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str  # lowercase "r/subreddit"
    total: int
    promoted: int
    quarantined: int
    last_seen: datetime | None


class RedditAccountHealth(BaseModel):
    """Tight slice of the account-pool snapshot restricted to source=reddit.
    The full account table is still served by ``/api/account-pool/state`` —
    this field set is the minimum the RedditPanel needs."""

    model_config = ConfigDict(extra="forbid")

    total: int
    active: int
    cooling: int
    quarantined: int
    # Count of accounts carrying PRAW credentials (vs. cookie-only, which
    # cannot run the primary path). When this hits 0 the JSON fallback is
    # the only route left — the panel surfaces that as a warning.
    with_praw_credentials: int


class RedditOverview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    coverage: list[SubredditCoverage]
    accounts: RedditAccountHealth


@router.get("/overview", response_model=RedditOverview)
async def overview(_: AuthDep, limit: int = 50) -> RedditOverview:
    factory = get_session_factory()
    async with factory() as session:
        rows = await StgNormalizedItemRepo.coverage_by_label(session, source="reddit", limit=limit)

    snap = await get_account_service().snapshot()
    reddit_accounts = [a for a in snap.accounts if a.source == "reddit"]
    by_state = {
        "active": sum(1 for a in reddit_accounts if a.state == "active"),
        "cooling": sum(1 for a in reddit_accounts if a.state == "cooling"),
        "quarantined": sum(1 for a in reddit_accounts if a.state == "quarantined"),
    }
    # The PRAW credentials counter is an inference: accounts imported with a
    # ``credentials`` blob will have been sealed as a v2 record. We don't
    # decrypt here (the sealer is workers-only); instead a future admin
    # import pipeline can stamp a ``has_credentials`` column. For now,
    # approximate via ``notes`` carrying the literal "praw" as a tag.
    with_praw = sum(1 for a in reddit_accounts if (a.notes or "").lower().find("praw") >= 0)

    return RedditOverview(
        coverage=[SubredditCoverage(**r) for r in rows],
        accounts=RedditAccountHealth(
            total=len(reddit_accounts),
            active=by_state["active"],
            cooling=by_state["cooling"],
            quarantined=by_state["quarantined"],
            with_praw_credentials=with_praw,
        ),
    )
