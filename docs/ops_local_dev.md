# Local dev — ops stack (M2, native Windows/Linux)

No Docker. Services run as native Python processes managed by **pm2**. State
lives in one **SQLite** file (`ops/ops.db`) plus **Redis** (natively installed).

## Prerequisites

| Tool | Version | Notes |
|---|---|---|
| Python | 3.11 + | Python 3.13 also works. |
| Node.js + npm | 18 + | For the frontend + pm2. |
| pm2 | latest | `npm i -g pm2`. Already endorsed in [miner.md](miner.md#running-the-miner). |
| Redis | 7 (or Memurai on Windows) | Memurai is a free drop-in: https://www.memurai.com/ |

That's it — no Postgres, no Docker.

## One-time setup

### 1. Install Redis

Windows: install **Memurai** — it registers as a Windows service and listens on `6379` automatically.

Linux / macOS: `apt/brew install redis` → start the service.

### 2. Python venv + dependencies

```bash
make bootstrap
```

### 3. Preflight + DB creation

```bash
make preflight   # verifies redis is reachable and SQLite path is writable
make init-db     # creates ops.db at OPS_DATABASE_URL and runs alembic upgrade head
```

Default DB URL is `sqlite+aiosqlite:///./ops.db` (relative to `ops/`). Override
via `OPS_DATABASE_URL`; absolute POSIX paths take four slashes
(`sqlite+aiosqlite:////var/lib/dataverse/ops.db`).

### 4. Build the frontend

```bash
make install-web  # npm ci
make build-web    # tsc -b && vite build → ops/dashboard/web/dist/
```

### 5. Start the stack

```bash
export OPS_DASHBOARD_PASSWORD=your-password
make start       # pm2 start ecosystem.config.js
make status      # pm2 ls
make logs        # tail all services
```

Then open:
- Dashboard: http://localhost:8000 — sign in → five panels + Live Feed.
- Health JSON: http://localhost:8000/api/health
- Metrics: http://localhost:8000/metrics

Light up the live feed with:
```bash
cd ops && ../ops/.venv/Scripts/python.exe -m dashboard.api.seed_demo --rate 10
```

### Scaling scraper workers (M5+)

The ecosystem file has disabled slots for workers. Once M5 ships:

```bash
# edit ecosystem.config.js → uncomment worker-x / worker-reddit / worker-youtube
make reload
pm2 scale worker-x 20       # ramp live
```

All N workers share one Redis Streams consumer group (natural work-stealing).
Remember to raise `OPS_REDIS_MAX_CONNECTIONS` when running >20 workers. SQLite
is the single-writer bottleneck: writes funnel through the bridge adapter
(M2.5) with bounded batching.

## Stop / reset

```bash
make stop        # pm2 stop all
make nuke        # pm2 delete all + wipe venv

# Wipe DB contents (dangerous — loses miner state):
rm ops/ops.db ops/ops.db-wal ops/ops.db-shm
make init-db
```

Redis is an OS service — flush its contents with `redis-cli FLUSHALL`.

## Dev workflow

```bash
make test        # pytest
make lint        # ruff check
make fmt         # ruff format + --fix
make typecheck   # mypy --strict
make dev-web     # Vite hot-reload on :5173, proxies /api + /ws to :8000
```

## What's shipped today

- `ops/` Python package: [common/config](../ops/common/config.py), [clock](../ops/common/clock.py), [schemas](../ops/common/schemas.py), [events](../ops/common/events.py), [logging](../ops/common/logging.py), [metrics](../ops/common/metrics.py), [infra](../ops/common/infra.py) (SQLite engine + Redis pool).
- dashboard-api (FastAPI): REST + `/ws/live` + cookie auth + `/metrics` + static UI.
- dashboard web UI (Vite + React + Tailwind + shadcn primitives): 7 panels, live feed, dark.
- Metric poller + retention sweep (M2) — `ThroughputPanel` sparklines are populated once the stack has been up for a couple of polls.
- Canonical Prometheus metric names owned by [common/metrics.py](../ops/common/metrics.py); service contracts in [docs/ops_metrics.md](ops_metrics.md).

## What's not yet wired

- No scrapers / proxies / accounts yet — M3 + M4 + M5.
- No auth beyond the shared password — stay on localhost / VPN.
- Alertmanager → Discord/Telegram — M15.
