"""Reddit plugin package.

Importing this module registers the Reddit scraper with the plugin registry
(see ``worker.scraper_api.register_scraper``). The JSON fallback lands in
``json_scraper`` and is registered there in a chain-of-responsibility pattern
— whichever plugin ``supports`` + has the right auth wins at dispatch time.

The runtime calls exactly one scraper per source per task; see
``worker.runtime._handle_one``. The PRAW scraper short-circuits to an empty
result when ``ctx.credentials`` is absent, which lets the JSON fallback (in
M7.E) live alongside it without a dispatch change.
"""

from . import praw_scraper  # noqa: F401 — import registers the scraper
