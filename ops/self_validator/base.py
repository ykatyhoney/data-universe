"""Self-validator protocol.

The pipeline orchestrator hands :class:`ValidationEnvelope` payloads to a
``SelfValidator``; pass/fail outcome flips the matching
``stg_normalized_items`` row to ``promoted`` (well, the bridge does that
once validation passes) or ``quarantined``.

Real implementation in M10:
- 1% sample of rows per source per hour
- Re-scrape via the SN13 scraper the validator uses
- Diff fields against the staged version using the per-source tolerance map
- Hard-fail on any text mismatch; soft-fail (warn-only) on engagement
  metric drift within tolerance
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from shared.pipeline import ValidationEnvelope


@dataclass(frozen=True)
class ValidationOutcome:
    passed: bool
    validator_scraper: str
    field_diffs: dict[str, Any]


class SelfValidator(Protocol):
    async def validate(self, envelope: ValidationEnvelope) -> ValidationOutcome: ...


class AlwaysPassValidator:
    """Smoke-test validator. Marks every row as passed; useful for
    integration tests that exercise the pipeline plumbing without paying
    the cost of a real validator scrape.
    """

    name: str = "always_pass"

    async def validate(self, envelope: ValidationEnvelope) -> ValidationOutcome:
        return ValidationOutcome(passed=True, validator_scraper=self.name, field_diffs={})
