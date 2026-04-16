"""Normalizer protocol — contract every per-source implementation must meet.

M9 fills in the real X / Reddit / YouTube normalizers; they MUST round-trip
through the corresponding SN13 scraper's ``validate()`` with
``is_valid=True``. The parity test in M9 enforces that invariant in CI.

M2.5 ships a passthrough normalizer used for integration tests — it accepts
pre-normalised input (i.e. the test harness provides both URI + the final
text blob) and just hands it through.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from shared.schemas import Source


@dataclass(frozen=True)
class NormalizedItem:
    """Output of a :class:`Normalizer`.

    Mirrors ``stg_normalized_items`` columns so ``StgNormalizedItemRepo.insert_pending``
    can be called with minimal ceremony. ``normalized_json`` MUST contain a
    ``"content"`` string (the serialised validator-parity blob) — the bridge
    adapter requires it.
    """

    source: Source
    uri: str  # canonical
    content_hash: str
    item_datetime: datetime
    label: str | None
    normalized_json: dict[str, Any]
    content_size_bytes: int


class Normalizer(Protocol):
    """Transform a single raw item from ``scrape:results`` into a
    ``NormalizedItem``.

    Implementations are stateless and pure; errors are raised as exceptions
    (the pipeline orchestrator catches, logs, drops the row, and does NOT
    stage it).
    """

    source: Source

    def normalize(self, raw: dict[str, Any]) -> NormalizedItem: ...


class PassthroughNormalizer:
    """Test-only — expects ``raw`` already in canonical form.

    Required raw shape::

        {
            "uri":              <string>,
            "datetime":         <ISO string, UTC>,
            "label":            <string or null>,
            "content":          <string — the validator-parity blob>,
        }
    """

    def __init__(self, source: Source) -> None:
        self.source = source

    def normalize(self, raw: dict[str, Any]) -> NormalizedItem:
        # Lazy import to avoid a circular dep during datastore bootstrap.
        from datastore.dedup import canonical_uri, content_hash

        uri = raw["uri"]
        content = raw["content"]
        canon = canonical_uri(self.source, uri)
        item_dt = datetime.fromisoformat(raw["datetime"])
        return NormalizedItem(
            source=self.source,
            uri=canon,
            content_hash=content_hash(self.source, uri, content),
            item_datetime=item_dt,
            label=raw.get("label"),
            normalized_json={"content": content},
            content_size_bytes=len(content.encode("utf-8")),
        )
