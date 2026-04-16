"""Playwright browser + context factory.

Keeps one Chromium process per worker (fast) and a fresh context per task
(isolated). Cookies, UA, and the proxy URL all land on the *context*, not
the browser — so two tasks with different accounts never share state.

Fingerprint randomisation (viewport, timezone, locale) is deterministic
per account: same account → same fingerprint across leases. Prevents the
"my account browsed from 8 different screens in 2 minutes" signal.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from shared.logging import get_logger

if TYPE_CHECKING:
    from playwright.async_api import Browser, BrowserContext, Page

log = get_logger(__name__)

# Reasonable defaults — mostly-desktop distribution so our fingerprint
# profile doesn't look out of place for X/Reddit consumers.
_VIEWPORTS: tuple[tuple[int, int], ...] = (
    (1920, 1080),
    (1680, 1050),
    (1536, 864),
    (1440, 900),
    (1366, 768),
    (1280, 800),
)
_TIMEZONES: tuple[str, ...] = (
    "America/Los_Angeles",
    "America/Denver",
    "America/Chicago",
    "America/New_York",
    "Europe/London",
    "Europe/Berlin",
    "Europe/Paris",
    "Asia/Tokyo",
    "Australia/Sydney",
)
_LOCALES: tuple[str, ...] = ("en-US", "en-GB", "en-CA", "en-AU")


@dataclass(frozen=True)
class Fingerprint:
    viewport_width: int
    viewport_height: int
    timezone: str
    locale: str


def fingerprint_for(account_id: str | None) -> Fingerprint:
    """Derive a stable fingerprint from ``account_id``. Same id → same
    fingerprint every time. If ``account_id`` is None we generate a random
    one-off (only for truly unauthed scrapes)."""
    rng: random.Random
    if account_id is None:
        rng = random.Random()
    else:
        # Seeded PRNG: stable across process restarts.
        digest = hashlib.sha256(account_id.encode("utf-8")).digest()
        seed = int.from_bytes(digest[:8], "big")
        rng = random.Random(seed)
    vw, vh = rng.choice(_VIEWPORTS)
    return Fingerprint(
        viewport_width=vw,
        viewport_height=vh,
        timezone=rng.choice(_TIMEZONES),
        locale=rng.choice(_LOCALES),
    )


def chromium_launch_args() -> list[str]:
    """Launch args that strip the obvious "automation" flags. Playwright's
    defaults already set most of these; we layer a few extras commonly
    recommended for residential-IP scraping."""
    return [
        "--disable-blink-features=AutomationControlled",
        "--disable-features=IsolateOrigins,site-per-process",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-infobars",
        "--disable-notifications",
        "--disable-popup-blocking",
    ]


async def new_context(
    browser: Browser,
    *,
    fingerprint: Fingerprint,
    user_agent: str | None,
    cookies: list[dict[str, Any]] | None,
    proxy_url: str | None,
    record_har_path: str | None,
) -> BrowserContext:
    """Fresh context with everything configured upfront.

    ``record_har_path`` is honoured by Playwright's built-in HAR recorder —
    the file is flushed on ``context.close()``. We always start recording;
    the runtime deletes the HAR on success and keeps it on failure.
    """
    options: dict[str, Any] = {
        "viewport": {
            "width": fingerprint.viewport_width,
            "height": fingerprint.viewport_height,
        },
        "timezone_id": fingerprint.timezone,
        "locale": fingerprint.locale,
    }
    if user_agent:
        options["user_agent"] = user_agent
    if proxy_url:
        options["proxy"] = {"server": proxy_url}
    if record_har_path:
        options["record_har_path"] = record_har_path
        options["record_har_mode"] = "minimal"  # no response bodies — keeps files small
    context = await browser.new_context(**options)

    if cookies:
        # Playwright expects ``expires`` (seconds since epoch, optional).
        # We forward the cookie dicts verbatim — shape matches Chrome export.
        await context.add_cookies(cookies)

    # Lightweight stealth: blank out the classic automation tells. For
    # harder targets M6+ layers ``playwright-stealth`` on top.
    await context.add_init_script(
        """
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
        Object.defineProperty(navigator, 'plugins', {
          get: () => [1, 2, 3, 4, 5],
        });
        """
    )
    return context


async def open_page(context: BrowserContext, *, default_timeout_ms: int = 30_000) -> Page:
    page = await context.new_page()
    page.set_default_timeout(default_timeout_ms)
    page.set_default_navigation_timeout(default_timeout_ms)
    return page
