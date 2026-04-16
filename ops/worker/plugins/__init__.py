"""Scraper plugins. Register by importing this package — each submodule
should call :func:`worker.scraper_api.register_scraper` at import time.

* ``echo`` — smoke-test scraper (M5).
* ``reddit`` — PRAW primary + JSON fallback (M7).
* X scraper lands in M6.
"""

from . import echo, reddit  # noqa: F401 — import registers the scrapers
