"""Self-validator — resamples 1% of normalised rows through the SN13
validator's scraper and diffs fields before promotion.

Real code lands in M10. M2.5 ships an AlwaysPassValidator used by the
pipeline orchestrator's smoke path so the end-to-end wiring is testable.
"""
