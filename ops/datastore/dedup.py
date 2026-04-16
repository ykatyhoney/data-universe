"""URI canonicalisation + content hashing for the dedup index.

Two rows collide iff they refer to the same upstream post, regardless of
subdomain / tracking params / slug drift. That's the "same upstream" in two
senses — :func:`canonical_uri` gives us that — and :func:`content_hash`
(sha256 over canonical URI + text blob) catches edits / tweet-shuffle.

Called by the normalizer (M9) before inserting ``stg_normalized_items``. The
sole authority for "have we already stored this?" is
``stg_dedup_index.canonical_uri`` — even the on-demand fast lane consults it
before scraping.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from shared.schemas import Source

# Tracking / share params stripped from every URI regardless of source.
_COMMON_TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "utm_id",
    "utm_name",
    "utm_reader",
    "utm_brand",
    "utm_social-type",
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "ref_src",
    "ref_url",
    "share_id",
    "_r",
    "_t",
}


@dataclass(frozen=True)
class CanonicalizationError(Exception):
    """Raised when a URI cannot be canonicalised (bad host, wrong source)."""

    uri: str
    reason: str

    def __str__(self) -> str:  # pragma: no cover — trivial
        return f"cannot canonicalise {self.uri!r}: {self.reason}"


# ---------- Per-source rules ---------- #


def _canonical_x(uri: str) -> str:
    """x.com / twitter.com → https://x.com/<user>/status/<id>

    Drops query params, collapses host, strips trailing slash and
    ``/photo/N`` / ``/video/N`` sub-paths that the web UI appends.
    """
    parts = urlsplit(uri)
    host = (parts.hostname or "").lower()
    if host.endswith("twitter.com"):
        host = "x.com"
    if host not in {"x.com", "mobile.x.com", "www.x.com"}:
        raise CanonicalizationError(uri, f"unexpected host {host!r}")
    host = "x.com"

    # path: /<user>/status/<id>[/photo/1|/video/1|/...]
    m = re.match(r"^/([^/]+)/status/(\d+)", parts.path)
    if not m:
        raise CanonicalizationError(uri, "path does not match /<user>/status/<id>")
    user, status_id = m.group(1), m.group(2)
    return urlunsplit(("https", host, f"/{user}/status/{status_id}", "", ""))


def _canonical_reddit(uri: str) -> str:
    """reddit.com / old.reddit.com / www.reddit.com → https://reddit.com/...

    Post permalinks (``/r/<sub>/comments/<id>[/<slug>]``) collapse to
    ``https://reddit.com/r/<sub>/comments/<id>``. Comment permalinks
    (``/r/<sub>/comments/<post_id>/<slug>/<comment_id>``) collapse to
    ``https://reddit.com/r/<sub>/comments/<post_id>/_/<comment_id>`` so that
    every comment has its own dedup key, independent of the parent post.
    Slug drift is ignored either way.
    """
    parts = urlsplit(uri)
    host = (parts.hostname or "").lower()
    if not (host == "reddit.com" or host.endswith(".reddit.com")):
        raise CanonicalizationError(uri, f"unexpected host {host!r}")
    host = "reddit.com"

    segs = [s for s in parts.path.split("/") if s]
    if len(segs) < 4 or segs[0] != "r" or segs[2] != "comments":
        raise CanonicalizationError(uri, "path does not match /r/<sub>/comments/<id>")
    sub, post_id = segs[1], segs[3]
    # segs[4] is the slug (optional); segs[5] is the comment id (if any).
    comment_id = segs[5] if len(segs) > 5 else None
    if comment_id:
        return urlunsplit(("https", host, f"/r/{sub}/comments/{post_id}/_/{comment_id}", "", ""))
    return urlunsplit(("https", host, f"/r/{sub}/comments/{post_id}", "", ""))


def _canonical_youtube(uri: str) -> str:
    """youtu.be / youtube.com / m.youtube.com → https://youtube.com/watch?v=<id>

    Only the video ID survives; no ``&t=`` / ``&list=`` / ``&index=``.
    Short-URL (``youtu.be/<id>``) and Shorts (``/shorts/<id>``) both accepted.
    """
    parts = urlsplit(uri)
    host = (parts.hostname or "").lower()
    video_id: str | None = None

    if host == "youtu.be":
        vid = parts.path.lstrip("/")
        if vid:
            video_id = vid.split("/", 1)[0]
    elif host.endswith("youtube.com"):
        if parts.path.startswith("/watch"):
            q = dict(parse_qsl(parts.query))
            video_id = q.get("v")
        elif parts.path.startswith("/shorts/"):
            video_id = parts.path.split("/", 3)[2]
    if not video_id or not re.fullmatch(r"[A-Za-z0-9_-]{6,}", video_id):
        raise CanonicalizationError(uri, "no recognizable video id")
    return urlunsplit(("https", "youtube.com", "/watch", f"v={video_id}", ""))


_SOURCE_DISPATCH = {
    Source.X: _canonical_x,
    Source.REDDIT: _canonical_reddit,
    Source.YOUTUBE: _canonical_youtube,
}


def canonical_uri(source: Source, uri: str) -> str:
    """Return the canonical form of ``uri`` for ``source``.

    Raises :class:`CanonicalizationError` on malformed input. Callers should
    treat that as "drop the row, log warning" — NEVER as "store uncanonicalised".
    """
    fn = _SOURCE_DISPATCH.get(source)
    if fn is None:
        raise CanonicalizationError(uri, f"unknown source {source!r}")
    uri = uri.strip()
    if not uri:
        raise CanonicalizationError(uri, "empty")
    # Scrub tracking params first — cheap and makes the source-specific rule
    # smaller.
    parts = urlsplit(uri)
    if parts.query:
        q = [
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if k not in _COMMON_TRACKING_PARAMS
        ]
        uri = urlunsplit(
            (parts.scheme or "https", parts.netloc, parts.path, "&".join(f"{k}={v}" for k, v in q), "")
        )
    return fn(uri)


# ---------- Content hash ---------- #


def content_hash(source: Source, uri: str, text_blob: str) -> str:
    """SHA-256 over ``canonical_uri | text_blob``.

    Catches edits and shuffle-attacks that preserve URI. ``text_blob`` should
    be the normalised body text (post text for X, title+body for Reddit,
    transcript concatenation for YouTube). Whitespace is NOT collapsed here
    — the caller controls the exact form they want hashed.
    """
    canon = canonical_uri(source, uri)
    h = hashlib.sha256()
    h.update(canon.encode("utf-8"))
    h.update(b"|")
    h.update(text_blob.encode("utf-8"))
    return h.hexdigest()
