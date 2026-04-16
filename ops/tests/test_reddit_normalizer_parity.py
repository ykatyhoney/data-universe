"""Byte-exact parity: our Reddit normalizer output must equal what the SN13
``RedditContent.to_data_entity`` path would produce for the same underlying
fields. Gated on SN13 miner deps (``bittensor`` / ``common.*`` imports pull
torch) so CI stays lean when those aren't installed.

The ``validate_reddit_content`` check compares the decoded entity's JSON
fields one-by-one; if our bytes round-trip through ``RedditContent.from_data_entity``
to the same field values, the validator will accept them.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

# Add the SN13 repo root to sys.path so ``scraping.*`` / ``common.*`` resolve.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("bittensor", reason="SN13 miner deps required for parity tests")
pytest.importorskip("torch", reason="SN13 miner deps required for parity tests")

from datetime import UTC  # noqa: E402

from common.data import DataSource  # noqa: E402
from scraping.reddit.model import RedditContent as SN13RedditContent  # noqa: E402
from scraping.reddit.model import RedditDataType  # noqa: E402
from scraping.reddit.utils import validate_reddit_content  # noqa: E402

from normalizer.reddit import RedditNormalizer  # noqa: E402


def _build_post_raw() -> dict[str, Any]:
    return {
        "kind": "post",
        "id": "t3_abc123",
        "permalink": "/r/bittensor_/comments/abc123/how_to_tao/",
        "author": "alice",
        "subreddit_prefixed": "r/bittensor_",
        "body": "hello world",
        "created_at": "2025-04-15T12:30:45+00:00",
        "scraped_at": "2025-04-15T12:35:20+00:00",
        "title": "How to TAO?",
        "parent_id": None,
        "media": None,
        "is_nsfw": False,
        "score": 42,
        "upvote_ratio": 0.97,
        "num_comments": 3,
    }


def test_ops_blob_roundtrips_through_sn13_model() -> None:
    """Parse our output through SN13's model; all fields must match."""
    raw = _build_post_raw()
    item = RedditNormalizer().normalize(raw)
    blob = item.normalized_json["content"]

    parsed = SN13RedditContent.parse_raw(blob)
    assert parsed.id == "t3_abc123"
    assert parsed.url == "https://www.reddit.com/r/bittensor_/comments/abc123/how_to_tao/"
    assert parsed.username == "alice"
    assert parsed.community == "r/bittensor_"
    assert parsed.body == "hello world"
    assert parsed.data_type == RedditDataType.POST
    assert parsed.title == "How to TAO?"
    assert parsed.parent_id is None
    # Timestamps obfuscated to the minute.
    assert parsed.created_at.second == 0 and parsed.created_at.microsecond == 0
    assert parsed.scraped_at.second == 0 and parsed.scraped_at.microsecond == 0


def test_validator_accepts_normalized_entity() -> None:
    """The full validator path must return ``is_valid=True`` when the
    miner-submitted blob matches the live content."""
    raw = _build_post_raw()
    item = RedditNormalizer().normalize(raw)
    blob_bytes = item.normalized_json["content"].encode("utf-8")

    actual_content = SN13RedditContent.parse_raw(blob_bytes)

    # Mimic a DataEntity built from our blob (no on-the-fly obfuscation —
    # the SN13 ``to_data_entity`` would mutate timestamps in place, but
    # our normalizer already emitted them minute-obfuscated).
    from common.data import DataEntity, DataLabel

    entity = DataEntity(
        uri=actual_content.url,
        datetime=actual_content.created_at,
        source=DataSource.REDDIT,
        label=DataLabel(value=actual_content.community.lower()),
        content=blob_bytes,
        content_size_bytes=len(blob_bytes),
    )
    result = validate_reddit_content(actual_content=actual_content, entity_to_validate=entity)
    assert result.is_valid, result.reason


def test_byte_exact_match_with_sn13_model() -> None:
    """Construct an SN13 RedditContent directly and compare JSON bytes."""
    from datetime import datetime

    sn13 = SN13RedditContent(
        id="t3_abc123",
        url="https://www.reddit.com/r/bittensor_/comments/abc123/how_to_tao/",
        username="alice",
        communityName="r/bittensor_",
        body="hello world",
        createdAt=datetime(2025, 4, 15, 12, 30, 0, tzinfo=UTC),
        dataType=RedditDataType.POST,
        title="How to TAO?",
        parentId=None,
        media=None,
        is_nsfw=False,
        score=42,
        upvote_ratio=0.97,
        num_comments=3,
        scrapedAt=datetime(2025, 4, 15, 12, 35, 0, tzinfo=UTC),
    )
    sn13_bytes = sn13.json(by_alias=True).encode("utf-8")

    item = RedditNormalizer().normalize(_build_post_raw())
    ours_bytes = item.normalized_json["content"].encode("utf-8")

    # Same keys, same values, same order.
    assert json.loads(ours_bytes) == json.loads(sn13_bytes)
    assert ours_bytes == sn13_bytes
