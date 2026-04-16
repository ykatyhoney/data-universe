# Worker framework (M5)

Scraper-agnostic Playwright worker. Each worker process:
1. Consumes tasks from the Redis stream `scrape:tasks` (consumer group
   `workers`; N workers coordinate via XREADGROUP).
2. Leases an account + paired proxy via the local `AccountPoolService` +
   `ProxyPoolService` singletons.
3. Opens a fresh Chromium context (cookies + UA + proxy injected;
   fingerprint seeded from `account_id` so it stays stable across leases).
4. Invokes the registered scraper plugin for the task's `source`.
5. Publishes a `ScrapeResultEnvelope` on `scrape:results` (consumed by the
   pipeline orchestrator from M2.5 → staging → self-validator →
   SqliteMinerStorage bridge).
6. Releases the account + paired proxy with a mapped outcome.
7. ACKs the stream message.

Real per-source scrapers land in M6 (X), M7 (Reddit), M8 (YouTube). M5
ships the framework + an `echo` plugin for smoke-testing.

## Quick start

```bash
# 1. Install Chromium (one-time, ~400 MB download)
cd ops && .venv/Scripts/playwright install chromium

# 2. Set required env
export OPS_PROXY_STATIC_ENDPOINTS="http://user:pass@gate.provider:8080"
export OPS_ACCOUNT_POOL_KEY="<fernet key>"
export OPS_DASHBOARD_PASSWORD=hunter2

# 3. Start dashboard-api (proxy + account pools)
make start

# 4. Import an account (see docs/ops_accounts.md)
python -m account_pool.import_cli /path/to/account.json

# 5. Start worker process(es)
pm2 start ecosystem.config.js --only worker
pm2 scale worker 4    # ramp to 4 concurrent workers

# 6. Seed a task
python -m worker.seed_task --source x --mode search --label "#bitcoin"

# 7. Watch the dashboard → Workers panel lights up; LiveFeed shows
#    task.started / task.finished events
```

## Writing a scraper plugin

Two-step contract:

```python
# ops/worker/plugins/my_source.py
from shared.schemas import ScrapeTaskMode, Source
from worker.scraper_api import ScrapeContext, register_scraper


class MySourceScraper:
    source = Source.X  # or REDDIT / YOUTUBE

    _MODES = frozenset({ScrapeTaskMode.SEARCH, ScrapeTaskMode.PROFILE})

    def supports(self, mode: ScrapeTaskMode) -> bool:
        return mode in self._MODES

    async def run(self, ctx: ScrapeContext) -> list[dict]:
        await ctx.page.goto(f"https://example.com/search?q={ctx.task.label}")
        # ... parse DOM / intercept GraphQL XHR / etc.
        return [{"uri": "...", "text": "...", "label": ctx.task.label}]


register_scraper(MySourceScraper())
```

Then register the import in `ops/worker/plugins/__init__.py`:

```python
from . import echo, my_source  # noqa: F401
```

Rules (enforced by the runtime):
- **Raise to signal failure.** The runtime captures HAR + screenshot,
  publishes a TaskFinished event with `outcome=ERROR`, releases the
  account with an inferred outcome (auth_failed / blocked / timeout /
  error based on exception text).
- **Return `[]` for "nothing found".** Legit; `outcome=EMPTY`; no error
  recorded.
- **Don't touch the DB.** Workers only publish to Redis streams; the
  pipeline orchestrator owns staging writes.
- **Don't log cookies.** Same rule as M4 — a regex test in CI greps logs.

## Fingerprint

`fingerprint_for(account_id)` returns a deterministic
`(viewport, timezone, locale)` tuple. Same account always sees the same
fingerprint, across process restarts and worker hosts. The distribution
samples desktop sizes + major US/EU/APAC timezones so our traffic pattern
looks plausible.

If you need to force a specific fingerprint for testing, the dataclass is
immutable; construct one directly and pass it into
`browser.new_context`.

## Recording (HAR + screenshot)

HAR is recorded on **every** task (Playwright's built-in). On success the
runtime deletes it; on failure it's kept at
`logs/worker/har/{task_id}.har` alongside
`logs/worker/screenshot/{task_id}.png`.

Dashboard:
- `GET /api/worker/debug/{task_id}/har` — download the HAR
- `GET /api/worker/debug/{task_id}/screenshot` — view the screenshot
- `GET /api/worker/debug/{task_id}/exists` — cheap probe

The Workers panel surfaces these as buttons next to the task id.

Retention: files older than `OPS_WORKER_DEBUG_RETENTION_HOURS` (default
48) get pruned by `recording.prune_debug_artifacts_older_than`. Wire it
into the retention sweeper when you care about disk.

## Scaling

`pm2 scale worker N` runs N processes. Each owns one Chromium process
(rough budget: ~300 MB idle, up to 1 GB under load). Rules of thumb:

| Host RAM | Recommended N |
|---|---|
| 4 GB | 2–3 |
| 8 GB | 4–6 |
| 16 GB | 8–12 |
| 32 GB | 16–24 |

Workers share:
- one Redis instance (streams + pub-sub)
- one SQLite file (ops + staging tables)
- the proxy + account pools (each worker instantiates its own
  `ProxyPoolService` / `AccountPoolService`, backed by the shared
  persistence)

`max_memory_restart: "1G"` in `ecosystem.config.js` bounces any worker
that balloons past 1 GB — cheap insurance against Playwright creep.

## State + observability

Per worker:
- Heartbeats every 15 s → `ops.workers` + WS event `WorkerHeartbeat`.
- Dashboard Workers panel shows: id, host, state, current task id, browser
  context count, memory, last-heartbeat age (green < 30 s, yellow > 30 s,
  red > 60 s), HAR + screenshot buttons for the current task.

Canonical metrics:
- `worker_heartbeat_age_seconds{worker_id}`
- `worker_busy{worker_id}`
- `worker_tasks_total{worker_id,source,outcome}` — outcomes:
  `ok|empty|error|blocked|rate_limited|timeout|crash`
- `worker_task_duration_seconds{source}` — histogram
- `worker_browser_crashes_total{worker_id}`

Events on the live bus (`shared.events`):
- `WorkerHeartbeat` — every 15 s
- `TaskStarted` / `TaskFinished` — once per task

## Hard timeouts + crash recovery

- Per-task wall clock cap: 300 s. Blown timeout → worker logs, increments
  `worker_tasks_total{outcome="timeout"}`, releases the lease with
  `TIMEOUT`.
- Worker crash mid-task → the Redis stream message stays in the PEL for
  the `workers` group; next consumer to XREADGROUP with the same name
  replays it (at-least-once delivery).
- Chromium crash → exception bubbles to the runtime, task ends in ERROR,
  `worker_browser_crashes_total` ticks. The browser is NOT auto-bounced
  yet — M15 chaos drill will prove we handle repeated crashes; for now
  the pm2 `max_memory_restart` bounces the whole process on OOM.

## Troubleshooting

- **"no scraper for source=X"** — you forgot to import the plugin in
  `worker/plugins/__init__.py`.
- **"no active account for source=X"** — import an account via
  `account_pool.import_cli` first.
- **Playwright import fails** — `.venv/Scripts/pip install "data-universe-ops[worker]"`
  then `playwright install chromium`.
- **Tasks stuck in PEL** — a prior worker crashed. Start a consumer with
  the same name and it replays; or call `redis-cli XPENDING scrape:tasks
  workers` to inspect.
