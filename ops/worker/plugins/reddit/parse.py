"""Reddit raw-item builders + media extraction.

Both the PRAW plugin (primary path) and the JSON fallback feed output through
these helpers so the normalizer sees a consistent shape regardless of which
path produced the item. Media URL extraction mirrors SN13's
``scraping.reddit.utils.extract_media_urls`` byte-for-byte — any deviation
would show up as a validator ``media URLs don't match`` failure under M7.F.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from shared.clock import now_utc


def extract_media_urls_from_submission(submission: Any) -> list[str]:
    """Port of ``scraping.reddit.utils.extract_media_urls`` for a live PRAW
    submission. Keep in sync with that function — the parity test pins it.

    Priority:
        1. Direct URL if it's a media extension or Reddit media CDN.
        2. Preview images (``preview.images[*].source.url``) — strip params,
           rewrite ``preview.redd.it`` → ``i.redd.it``.
        3. Gallery media (``media_metadata[*].s.u``) — same strip/rewrite.
    """
    urls: list[str] = []

    url = getattr(submission, "url", None)
    if url:
        clean = url.split("?", 1)[0]
        media_exts = (".jpg", ".jpeg", ".png", ".gif", ".mp4", ".webm")
        media_domains = ("i.redd.it", "v.redd.it")
        if clean.endswith(media_exts) or any(d in url for d in media_domains):
            urls.append(clean)

    preview = getattr(submission, "preview", None)
    if isinstance(preview, dict):
        for image in preview.get("images") or []:
            src_url = ((image.get("source") or {}).get("url") or "") if isinstance(image, dict) else ""
            if not src_url:
                continue
            clean = src_url.split("?", 1)[0]
            if "preview.redd.it" in clean:
                clean = clean.replace("preview.redd.it", "i.redd.it")
            urls.append(clean)

    media_metadata = getattr(submission, "media_metadata", None)
    if isinstance(media_metadata, dict):
        for data in media_metadata.values():
            if not isinstance(data, dict):
                continue
            s = data.get("s") or {}
            u = s.get("u") if isinstance(s, dict) else None
            if not u:
                continue
            clean = u.replace("&amp;", "&").split("?", 1)[0]
            if "preview.redd.it" in clean:
                clean = clean.replace("preview.redd.it", "i.redd.it")
            urls.append(clean)

    # Dedup preserving order — matches the SN13 helper's output contract.
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        if u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out


def build_post_raw(submission: Any) -> dict[str, Any]:
    """Convert a live PRAW submission into the normalizer's raw dict shape.

    Fields mirror ``RedditCustomScraper._best_effort_parse_submission`` — any
    drift here fails M7.F parity.
    """
    author = submission.author.name if getattr(submission, "author", None) else None
    media = extract_media_urls_from_submission(submission)
    return {
        "kind": "post",
        "id": submission.name,  # t3_xxx
        "permalink": submission.permalink,
        "author": author,
        "subreddit_prefixed": submission.subreddit_name_prefixed,
        "body": submission.selftext or "",
        "created_at": _utc_from_epoch(submission.created_utc).isoformat(),
        "scraped_at": now_utc().isoformat(),
        "title": submission.title,
        "parent_id": None,
        "media": media if media else None,
        "is_nsfw": bool(submission.over_18),
        "score": getattr(submission, "score", None),
        "upvote_ratio": getattr(submission, "upvote_ratio", None),
        "num_comments": getattr(submission, "num_comments", None),
    }


def build_comment_raw(comment: Any) -> dict[str, Any]:
    """Convert a live PRAW comment into the normalizer's raw dict shape.

    NSFW is inherited from parent submission OR subreddit — matches SN13.
    Caller must have awaited ``comment.submission.load()`` + ``subreddit.load()``
    before passing in, so the over_18 / over18 attributes are populated.
    """
    author = comment.author.name if getattr(comment, "author", None) else None
    parent_nsfw = bool(getattr(getattr(comment, "submission", None), "over_18", False))
    sub_nsfw = bool(getattr(getattr(comment, "subreddit", None), "over18", False))
    return {
        "kind": "comment",
        "id": comment.name,  # t1_xxx
        "permalink": comment.permalink,
        "author": author,
        "subreddit_prefixed": comment.subreddit_name_prefixed,
        "body": comment.body or "",
        "created_at": _utc_from_epoch(comment.created_utc).isoformat(),
        "scraped_at": now_utc().isoformat(),
        "parent_id": comment.parent_id,
        "is_nsfw": parent_nsfw or sub_nsfw,
        "score": getattr(comment, "score", None),
    }


def _utc_from_epoch(epoch: float) -> datetime:
    return datetime.fromtimestamp(epoch, tz=UTC)
