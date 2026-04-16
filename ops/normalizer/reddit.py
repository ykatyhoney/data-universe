"""Reddit normalizer — byte-exact RedditContent parity.

Raw items arrive from the PRAW plugin (primary) or JSON fallback in this
shape::

    {
        "kind":                "post" | "comment",
        "id":                  "t3_xxx" | "t1_xxx",   # PRAW ``name`` field
        "permalink":           "/r/sub/comments/.../slug/",
        "author":              "username" | "[deleted]",
        "subreddit_prefixed":  "r/sub",               # includes r/ prefix
        "body":                "<selftext or comment body>",
        "created_at":          ISO-8601 UTC string,
        "scraped_at":          ISO-8601 UTC string,
        "title":               str | None,            # post only
        "parent_id":           "t1_..." | "t3_..." | None,  # comment only
        "media":               [str, ...] | None,     # post only, deduped+cleaned
        "is_nsfw":             bool | None,
        "score":               int | None,
        "upvote_ratio":        float | None,          # post only
        "num_comments":        int | None,            # post only
    }

The normalizer produces a :class:`NormalizedItem` whose ``normalized_json["content"]``
is exactly what :meth:`scraping.reddit.model.RedditContent.to_data_entity` would
emit — i.e. ``RedditContent.json(by_alias=True)`` over a minute-obfuscated
pydantic v1 blob. The golden-set parity test in
``tests/test_reddit_normalizer_parity.py`` pins that guarantee.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic.v1 import BaseModel, Field, ValidationError

from datastore.dedup import canonical_uri, content_hash
from normalizer.base import NormalizedItem
from shared.schemas import Source

# The username SN13's Apify lite scraper reports for deleted authors — mirror
# that literal so bridge-side validators see the same value.
DELETED_USER = "[deleted]"


class _RedditDataType(StrEnum):
    POST = "post"
    COMMENT = "comment"


class _RedditContent(BaseModel):
    """Byte-exact mirror of ``scraping.reddit.model.RedditContent``.

    Kept local (no SN13 import) so the ops package stays decoupled. The
    parity test imports the real SN13 model and diffs JSON output.
    """

    class Config:
        extra = "forbid"
        # ``allow_population_by_field_name`` lets us build the model with
        # snake_case keyword args; ``by_alias=True`` at serialise time emits
        # camelCase. Mirrors SN13.
        allow_population_by_field_name = True

    id: str
    url: str
    username: str
    community: str = Field(alias="communityName")
    body: str
    created_at: datetime = Field(alias="createdAt")
    data_type: _RedditDataType = Field(alias="dataType")
    title: str | None = Field(default=None)
    parent_id: str | None = Field(default=None, alias="parentId")
    media: list[str] | None = Field(default=None)
    is_nsfw: bool | None = Field(default=None)
    score: int | None = Field(default=None)
    upvote_ratio: float | None = Field(default=None)
    num_comments: int | None = Field(default=None)
    scraped_at: datetime | None = Field(default=None, alias="scrapedAt")


class RedditNormalizeError(Exception):
    """Raised when a raw item cannot be normalised."""


class RedditNormalizer:
    """Normalize PRAW/JSON raw items into SN13-shaped RedditContent blobs."""

    source = Source.REDDIT

    def normalize(self, raw: dict[str, Any]) -> NormalizedItem:
        kind = raw.get("kind")
        if kind == "post":
            content = self._build_post(raw)
        elif kind == "comment":
            content = self._build_comment(raw)
        else:
            raise RedditNormalizeError(f"unknown kind: {kind!r}")

        # NSFW+media combo is invalid for the subnet. SN13 filters it
        # pre-entity; we drop it here so the pipeline never stages it.
        if content.is_nsfw and content.media:
            raise RedditNormalizeError("NSFW content with media is not permitted")

        # Obfuscate timestamps to the minute BEFORE serialisation — this is
        # what to_data_entity does and what validate_reddit_content expects.
        content.created_at = _obfuscate_to_minute(content.created_at)
        if content.scraped_at is not None:
            content.scraped_at = _obfuscate_to_minute(content.scraped_at)

        # pydantic v1's .json() default is compact (no trailing spaces); SN13
        # relies on that byte-equality.
        blob = content.json(by_alias=True)
        canon = canonical_uri(Source.REDDIT, content.url)
        return NormalizedItem(
            source=Source.REDDIT,
            uri=canon,
            content_hash=content_hash(Source.REDDIT, content.url, blob),
            item_datetime=content.created_at,
            label=content.community.lower(),
            normalized_json={"content": blob},
            content_size_bytes=len(blob.encode("utf-8")),
        )

    # ---------- internals ---------- #

    def _build_post(self, raw: dict[str, Any]) -> _RedditContent:
        try:
            return _RedditContent(
                id=_require_str(raw, "id"),
                url=_build_url(_require_str(raw, "permalink")),
                username=_coalesce_author(raw.get("author")),
                communityName=_require_str(raw, "subreddit_prefixed"),
                body=raw.get("body") or "",
                createdAt=_parse_dt(_require_str(raw, "created_at")),
                dataType=_RedditDataType.POST,
                title=raw.get("title"),
                parentId=None,  # posts never have parents
                media=_clean_media(raw.get("media")),
                is_nsfw=_opt_bool(raw.get("is_nsfw")),
                score=_opt_int(raw.get("score")),
                upvote_ratio=_opt_float(raw.get("upvote_ratio")),
                num_comments=_opt_int(raw.get("num_comments")),
                scrapedAt=_parse_dt_opt(raw.get("scraped_at")),
            )
        except ValidationError as e:
            raise RedditNormalizeError(f"post schema failed: {e}") from e

    def _build_comment(self, raw: dict[str, Any]) -> _RedditContent:
        try:
            return _RedditContent(
                id=_require_str(raw, "id"),
                url=_build_url(_require_str(raw, "permalink")),
                username=_coalesce_author(raw.get("author")),
                communityName=_require_str(raw, "subreddit_prefixed"),
                body=raw.get("body") or "",
                createdAt=_parse_dt(_require_str(raw, "created_at")),
                dataType=_RedditDataType.COMMENT,
                title=None,  # comments never have titles
                parentId=_opt_str(raw.get("parent_id")),
                media=None,  # comments have no media on Reddit
                is_nsfw=_opt_bool(raw.get("is_nsfw")),
                score=_opt_int(raw.get("score")),
                upvote_ratio=None,  # not a thing on comments
                num_comments=None,  # not a thing on comments
                scrapedAt=_parse_dt_opt(raw.get("scraped_at")),
            )
        except ValidationError as e:
            raise RedditNormalizeError(f"comment schema failed: {e}") from e


# ---------- helpers ---------- #


def _require_str(raw: dict[str, Any], key: str) -> str:
    v = raw.get(key)
    if not isinstance(v, str) or not v:
        raise RedditNormalizeError(f"missing/invalid required field: {key}")
    return v


def _opt_str(v: Any) -> str | None:
    return v if isinstance(v, str) and v else None


def _opt_int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _opt_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _opt_bool(v: Any) -> bool | None:
    if v is None:
        return None
    return bool(v)


def _coalesce_author(v: Any) -> str:
    return v if isinstance(v, str) and v else DELETED_USER


def _build_url(permalink: str) -> str:
    """Build the exact URL SN13's custom scraper emits.

    SN13 normalises permalinks to ensure a leading slash then prepends
    ``https://www.reddit.com``. We mirror that verbatim so validate_reddit_content's
    ``url != url`` check passes on round-trip.
    """
    if not permalink.startswith("/"):
        permalink = "/" + permalink
    return f"https://www.reddit.com{permalink}"


def _parse_dt(s: str) -> datetime:
    """Accept ISO-8601 with or without ``Z`` suffix, always return tz-aware UTC."""
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _parse_dt_opt(s: Any) -> datetime | None:
    if not isinstance(s, str) or not s:
        return None
    return _parse_dt(s)


def _obfuscate_to_minute(dt: datetime) -> datetime:
    """Match ``scraping.utils.obfuscate_datetime_to_minute`` exactly."""
    return dt.replace(second=0, microsecond=0)


def _clean_media(v: Any) -> list[str] | None:
    """Defence-in-depth cleanup: the PRAW plugin already runs SN13's
    ``extract_media_urls`` but a JSON-fallback path may skip it. Strip query
    strings and dedupe while preserving the first-seen order, matching
    ``extract_media_urls``' output contract."""
    if not v:
        return None
    if not isinstance(v, list):
        return None
    out: list[str] = []
    seen: set[str] = set()
    for url in v:
        if not isinstance(url, str) or not url:
            continue
        clean = url.split("?", 1)[0]
        if clean in seen:
            continue
        seen.add(clean)
        out.append(clean)
    return out or None


__all__ = ["DELETED_USER", "RedditNormalizeError", "RedditNormalizer"]
