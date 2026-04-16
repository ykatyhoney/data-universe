"""Pure-function tests for storage.dedup canonicalisation + content_hash."""

from __future__ import annotations

import pytest

from datastore.dedup import CanonicalizationError, canonical_uri, content_hash
from shared.schemas import Source

# ---------- X ---------- #


@pytest.mark.parametrize(
    "uri",
    [
        "https://x.com/elonmusk/status/1234567890",
        "https://twitter.com/elonmusk/status/1234567890",
        "https://www.x.com/elonmusk/status/1234567890",
        "https://mobile.x.com/elonmusk/status/1234567890",
        "https://x.com/elonmusk/status/1234567890?s=20&t=abc",
        "https://x.com/elonmusk/status/1234567890/photo/1",
        "https://x.com/elonmusk/status/1234567890/video/1",
    ],
)
def test_x_collapses_to_canonical(uri: str) -> None:
    assert canonical_uri(Source.X, uri) == "https://x.com/elonmusk/status/1234567890"


def test_x_rejects_non_status_path() -> None:
    with pytest.raises(CanonicalizationError):
        canonical_uri(Source.X, "https://x.com/elonmusk")


def test_x_rejects_alien_host() -> None:
    with pytest.raises(CanonicalizationError):
        canonical_uri(Source.X, "https://example.com/elonmusk/status/1")


# ---------- Reddit ---------- #


@pytest.mark.parametrize(
    "uri",
    [
        "https://reddit.com/r/Bitcoin/comments/abc123/hello-world/",
        "https://www.reddit.com/r/Bitcoin/comments/abc123/different_slug",
        "https://old.reddit.com/r/Bitcoin/comments/abc123/",
        "https://reddit.com/r/Bitcoin/comments/abc123?utm_source=share&utm_medium=ios",
    ],
)
def test_reddit_collapses_subdomains_and_slug(uri: str) -> None:
    assert canonical_uri(Source.REDDIT, uri) == "https://reddit.com/r/Bitcoin/comments/abc123"


def test_reddit_rejects_non_comment_path() -> None:
    with pytest.raises(CanonicalizationError):
        canonical_uri(Source.REDDIT, "https://reddit.com/r/Bitcoin")


@pytest.mark.parametrize(
    "uri,expected",
    [
        (
            "https://www.reddit.com/r/Bitcoin/comments/abc123/hello/xyz789/",
            "https://reddit.com/r/Bitcoin/comments/abc123/_/xyz789",
        ),
        (
            "https://old.reddit.com/r/Bitcoin/comments/abc123/different_slug/xyz789",
            "https://reddit.com/r/Bitcoin/comments/abc123/_/xyz789",
        ),
    ],
)
def test_reddit_comments_keep_distinct_canonical(uri: str, expected: str) -> None:
    """Each comment must have its own canonical URI, not collapse onto the
    parent post (which would make dedup suppress every comment after the
    first)."""
    assert canonical_uri(Source.REDDIT, uri) == expected


# ---------- YouTube ---------- #


@pytest.mark.parametrize(
    "uri,expected",
    [
        ("https://youtube.com/watch?v=dQw4w9WgXcQ", "https://youtube.com/watch?v=dQw4w9WgXcQ"),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s", "https://youtube.com/watch?v=dQw4w9WgXcQ"),
        ("https://m.youtube.com/watch?v=dQw4w9WgXcQ&list=ABC", "https://youtube.com/watch?v=dQw4w9WgXcQ"),
        ("https://youtu.be/dQw4w9WgXcQ", "https://youtube.com/watch?v=dQw4w9WgXcQ"),
        ("https://youtu.be/dQw4w9WgXcQ?t=10", "https://youtube.com/watch?v=dQw4w9WgXcQ"),
        ("https://youtube.com/shorts/dQw4w9WgXcQ", "https://youtube.com/watch?v=dQw4w9WgXcQ"),
    ],
)
def test_youtube_collapses_to_watch(uri: str, expected: str) -> None:
    assert canonical_uri(Source.YOUTUBE, uri) == expected


def test_youtube_rejects_missing_id() -> None:
    with pytest.raises(CanonicalizationError):
        canonical_uri(Source.YOUTUBE, "https://youtube.com/playlist?list=ABC")


# ---------- content_hash ---------- #


def test_content_hash_changes_with_text() -> None:
    uri = "https://x.com/u/status/1"
    a = content_hash(Source.X, uri, "hello")
    b = content_hash(Source.X, uri, "hello!")
    assert a != b


def test_content_hash_collapses_uri_variants() -> None:
    """Same canonical URI + text → identical hash regardless of input form."""
    a = content_hash(Source.X, "https://twitter.com/u/status/1?s=20", "hello")
    b = content_hash(Source.X, "https://x.com/u/status/1", "hello")
    assert a == b


def test_content_hash_is_64_hex_chars() -> None:
    h = content_hash(Source.X, "https://x.com/u/status/1", "hello")
    assert len(h) == 64
    int(h, 16)  # raises if non-hex
