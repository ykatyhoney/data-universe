"""Reddit normalizer unit tests.

Parity with SN13's ``RedditContent`` (byte-exact JSON serialisation) lives
in ``test_reddit_normalizer_parity.py``, gated on SN13 miner deps. This file
runs unconditionally on the plain ops test set.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from normalizer.reddit import DELETED_USER, RedditNormalizeError, RedditNormalizer
from shared.schemas import Source


def _base_post(**over: Any) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "kind": "post",
        "id": "t3_abc123",
        "permalink": "/r/bittensor_/comments/abc123/how_to_tao/",
        "author": "alice",
        "subreddit_prefixed": "r/bittensor_",
        "body": "hello world",
        "created_at": "2025-04-15T12:30:45.123456+00:00",
        "scraped_at": "2025-04-15T12:35:20+00:00",
        "title": "How to TAO?",
        "parent_id": None,
        "media": None,
        "is_nsfw": False,
        "score": 42,
        "upvote_ratio": 0.97,
        "num_comments": 3,
    }
    raw.update(over)
    return raw


def _base_comment(**over: Any) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "kind": "comment",
        "id": "t1_xyz789",
        "permalink": "/r/bittensor_/comments/abc123/how_to_tao/xyz789/",
        "author": "bob",
        "subreddit_prefixed": "r/bittensor_",
        "body": "nice",
        "created_at": "2025-04-15T12:35:20+00:00",
        "scraped_at": "2025-04-15T12:36:00+00:00",
        "parent_id": "t3_abc123",
        "is_nsfw": False,
        "score": 5,
    }
    raw.update(over)
    return raw


def test_post_produces_validator_shaped_content() -> None:
    item = RedditNormalizer().normalize(_base_post())
    payload = json.loads(item.normalized_json["content"])

    # Aliases — SN13 serialises these exact camelCase keys.
    assert set(payload.keys()) == {
        "id",
        "url",
        "username",
        "communityName",
        "body",
        "createdAt",
        "dataType",
        "title",
        "parentId",
        "media",
        "is_nsfw",
        "score",
        "upvote_ratio",
        "num_comments",
        "scrapedAt",
    }
    assert payload["dataType"] == "post"
    assert payload["url"] == "https://www.reddit.com/r/bittensor_/comments/abc123/how_to_tao/"
    assert payload["communityName"] == "r/bittensor_"
    # Timestamps obfuscated to the minute.
    assert payload["createdAt"] == "2025-04-15T12:30:00+00:00"
    assert payload["scrapedAt"] == "2025-04-15T12:35:00+00:00"
    # Post-only shape.
    assert payload["title"] == "How to TAO?"
    assert payload["parentId"] is None
    # NormalizedItem metadata.
    assert item.source == Source.REDDIT
    assert item.uri == "https://reddit.com/r/bittensor_/comments/abc123"
    assert item.label == "r/bittensor_"
    assert item.content_size_bytes == len(item.normalized_json["content"].encode("utf-8"))


def test_comment_has_null_title_and_preserves_parent_id() -> None:
    item = RedditNormalizer().normalize(_base_comment())
    payload = json.loads(item.normalized_json["content"])

    assert payload["dataType"] == "comment"
    assert payload["title"] is None
    assert payload["parentId"] == "t3_abc123"
    # Comments always null these post-only fields.
    assert payload["upvote_ratio"] is None
    assert payload["num_comments"] is None
    assert payload["media"] is None
    # Comment dedup URI differs from its parent post.
    assert item.uri.endswith("/_/xyz789")


def test_deleted_author_coalesces_to_sentinel() -> None:
    item = RedditNormalizer().normalize(_base_post(author=None))
    payload = json.loads(item.normalized_json["content"])
    assert payload["username"] == DELETED_USER


def test_nsfw_with_media_is_rejected() -> None:
    """NSFW+media combo is never valid for the subnet — SN13 rejects it at
    validation time, we drop it at normalize time so it never stages."""
    with pytest.raises(RedditNormalizeError, match="NSFW"):
        RedditNormalizer().normalize(_base_post(is_nsfw=True, media=["https://i.redd.it/abc.jpg"]))


def test_nsfw_without_media_is_allowed() -> None:
    item = RedditNormalizer().normalize(_base_post(is_nsfw=True, media=None))
    assert json.loads(item.normalized_json["content"])["is_nsfw"] is True


def test_media_urls_stripped_of_query_and_deduped() -> None:
    item = RedditNormalizer().normalize(
        _base_post(
            media=[
                "https://i.redd.it/abc.jpg?auto=webp&s=deadbeef",
                "https://i.redd.it/abc.jpg?auto=webp&s=cafef00d",  # dedup
                "https://i.redd.it/xyz.png",
            ]
        )
    )
    payload = json.loads(item.normalized_json["content"])
    assert payload["media"] == [
        "https://i.redd.it/abc.jpg",
        "https://i.redd.it/xyz.png",
    ]


def test_missing_required_field_raises() -> None:
    with pytest.raises(RedditNormalizeError, match="id"):
        RedditNormalizer().normalize(_base_post(id=""))
    with pytest.raises(RedditNormalizeError, match="permalink"):
        RedditNormalizer().normalize(_base_post(permalink=""))


def test_unknown_kind_raises() -> None:
    with pytest.raises(RedditNormalizeError, match="unknown kind"):
        RedditNormalizer().normalize(_base_post(kind="mystery"))


def test_permalink_normalisation_adds_leading_slash() -> None:
    item = RedditNormalizer().normalize(_base_post(permalink="r/bittensor_/comments/abc123/how_to_tao/"))
    payload = json.loads(item.normalized_json["content"])
    assert payload["url"].startswith("https://www.reddit.com/r/")


def test_item_datetime_and_content_hash_stable() -> None:
    """Two calls on identical raw must produce identical hash + datetime."""
    a = RedditNormalizer().normalize(_base_post())
    b = RedditNormalizer().normalize(_base_post())
    assert a.content_hash == b.content_hash
    assert a.item_datetime == b.item_datetime
    # And second-granularity change in scraped_at must NOT change the hash
    # (minute-obfuscated).
    c = RedditNormalizer().normalize(_base_post(scraped_at="2025-04-15T12:35:59+00:00"))
    assert a.content_hash == c.content_hash


def test_different_content_produces_different_hash() -> None:
    a = RedditNormalizer().normalize(_base_post(body="hello"))
    b = RedditNormalizer().normalize(_base_post(body="goodbye"))
    assert a.content_hash != b.content_hash
