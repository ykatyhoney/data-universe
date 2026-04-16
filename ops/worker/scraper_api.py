"""Plugin contract every scraper implements.

The runtime hands a :class:`ScrapeContext` to the plugin's ``run`` method;
the plugin returns a list of raw items (provider-shape dicts — normalized
downstream by the pipeline in M2.5).

Contract:
    - ``source``  : matching :class:`shared.schemas.Source` value.
    - ``run(ctx)``: async, may raise. Timeouts + retries are the runtime's
      concern; the plugin just does its job.
    - ``supports(mode)``: cheap predicate so the runtime can skip plugins
      that don't handle a given task mode (search / profile / permalink /
      channel / comment).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from shared.pipeline import ScrapeTaskEnvelope
from shared.schemas import ScrapeTaskMode, Source

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext, Page

    from proxy_pool.schemas import LeaseResponse as ProxyLeaseResponse


@dataclass
class ScrapeContext:
    """Everything the plugin needs. Mutable so plugins can stash side-state
    for subsequent calls (rare — most plugins are stateless)."""

    task: ScrapeTaskEnvelope
    page: Page
    browser_context: BrowserContext
    worker_id: str
    trace_id: str
    # Set when account pool pairs a proxy; useful for logging.
    account_id: str | None = None
    proxy: ProxyLeaseResponse | None = None
    # Auth material from the account lease. Plugins pick whichever fits
    # their source: cookies for browser-authed (X), credentials for
    # API-authed (Reddit PRAW OAuth). See :class:`account_pool.schemas.AccountLeaseResponse`.
    cookies: list[dict[str, Any]] | None = None
    credentials: dict[str, Any] | None = None

    # Optional progress callback the plugin can call to emit live events
    # (e.g. "scrolled N pages"). Keep payloads small — they hit WS.
    emit_progress: Callable[[dict[str, Any]], Awaitable[None]] | None = None

    # Plugin-defined scratch state. Never serialised.
    scratch: dict[str, Any] = field(default_factory=dict)


class Scraper(Protocol):
    """Contract every source-specific scraper implements."""

    source: Source

    def supports(self, mode: ScrapeTaskMode) -> bool:
        """Return True if this plugin can handle ``mode`` for its source."""
        ...

    async def run(self, ctx: ScrapeContext) -> list[dict[str, Any]]:
        """Execute the scrape. Return raw items; empty list means "nothing
        found" (legitimate — not a failure)."""
        ...


# ---------- registry ---------- #

_REGISTRY: dict[str, Scraper] = {}


class ScraperAlreadyRegistered(Exception):
    """Raised if two plugins claim the same source."""


def register_scraper(scraper: Scraper) -> None:
    """Make ``scraper`` available to the runtime for ``scraper.source``.

    Called at import time from plugins/__init__.py. Idempotent re-registration
    raises — renames must be explicit.
    """
    key = scraper.source.value
    existing = _REGISTRY.get(key)
    if existing is not None and existing is not scraper:
        raise ScraperAlreadyRegistered(f"source {key!r} already bound to {existing!r}; new={scraper!r}")
    _REGISTRY[key] = scraper


def get_scraper(source: Source) -> Scraper | None:
    return _REGISTRY.get(source.value)


def registered_sources() -> list[str]:
    return sorted(_REGISTRY.keys())


def clear_registry() -> None:
    """Test helper. Never call from production code."""
    _REGISTRY.clear()
