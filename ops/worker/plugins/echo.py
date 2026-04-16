"""Smoke-test scraper.

Visits a neutral HTTP endpoint via the lease's proxy and returns the echoed
headers + origin IP as a single "item". Used to prove the framework works
end-to-end without touching real targets.

Register by importing this module (``worker.plugins`` does that for you).
"""

from __future__ import annotations

import json
from typing import Any

from shared.schemas import ScrapeTaskMode, Source
from worker.scraper_api import ScrapeContext, Scraper, register_scraper

# Neutral endpoint: returns a JSON body reflecting request headers + source IP.
ECHO_URL = "https://httpbin.org/anything"


class EchoScraper(Scraper):
    source = Source.X  # arbitrary — EchoScraper runs against whatever source is requested

    # Claim every mode. Lets you target any kind of task at the worker
    # during smoke tests.
    _SUPPORTED_MODES = frozenset(m for m in ScrapeTaskMode)

    def supports(self, mode: ScrapeTaskMode) -> bool:
        return mode in self._SUPPORTED_MODES

    async def run(self, ctx: ScrapeContext) -> list[dict[str, Any]]:
        await ctx.page.goto(ECHO_URL, wait_until="domcontentloaded")
        body = await ctx.page.evaluate("() => document.body.innerText")
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            data = {"raw": body[:1024]}
        return [
            {
                "uri": ECHO_URL,
                "source": "echo",
                "headers": data.get("headers", {}),
                "origin_ip": data.get("origin"),
                "task_id": ctx.task.task_id,
                "worker_id": ctx.worker_id,
            }
        ]


register_scraper(EchoScraper())
