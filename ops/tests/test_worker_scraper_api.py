"""Scraper plugin contract — registry behaviour."""

from __future__ import annotations

from typing import Any

import pytest

from shared.schemas import ScrapeTaskMode, Source
from worker.scraper_api import (
    ScraperAlreadyRegistered,
    clear_registry,
    get_scraper,
    register_scraper,
    registered_sources,
)


class _Stub:
    def __init__(self, source: Source) -> None:
        self.source = source

    def supports(self, _mode: ScrapeTaskMode) -> bool:
        return True

    async def run(self, _ctx: Any) -> list[dict[str, Any]]:
        return []


def setup_function(_fn: object) -> None:
    clear_registry()


def teardown_function(_fn: object) -> None:
    # Reset so later tests (or the echo plugin's auto-registration) aren't
    # polluted by any stubs.
    clear_registry()


def test_register_and_lookup() -> None:
    s = _Stub(Source.X)
    register_scraper(s)
    assert get_scraper(Source.X) is s
    assert get_scraper(Source.REDDIT) is None
    assert registered_sources() == ["x"]


def test_duplicate_registration_raises() -> None:
    register_scraper(_Stub(Source.X))
    with pytest.raises(ScraperAlreadyRegistered):
        register_scraper(_Stub(Source.X))


def test_idempotent_registration_ok() -> None:
    s = _Stub(Source.X)
    register_scraper(s)
    register_scraper(s)  # same instance — fine
    assert get_scraper(Source.X) is s


def test_plugins_register_on_import() -> None:
    import importlib

    import worker.plugins

    clear_registry()
    # Force a re-run of the package's side-effect imports so registration
    # fires again post-clear. ``import worker.plugins`` alone is a no-op
    # once the module is cached.
    importlib.reload(worker.plugins.echo)
    importlib.reload(worker.plugins.reddit.praw_scraper)

    assert get_scraper(Source.X) is not None
    assert get_scraper(Source.REDDIT) is not None
    clear_registry()
