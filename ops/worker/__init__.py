"""Playwright worker framework (M5).

Scrapers plug in by implementing :class:`Scraper` and registering via
:func:`register_scraper`. The runtime handles everything else: reading
tasks from Redis Streams, leasing proxy + account atomically, spawning a
Playwright context with cookies + UA + proxy injected, recording HAR +
screenshot on failure, emitting events.

Per-source scrapers land in M6-M8.
"""
