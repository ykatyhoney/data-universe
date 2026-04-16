# Scraping Architecture — Milestones

> Strategy: **Option C from [vision.md](vision.md#21-options-honestly-evaluated)** — Playwright + residential proxies + cookie accounts, across all sources where it's the right fit (primary for X, fallback for Reddit, proxy-only for YouTube).
>
> Philosophy: **observe before we scale.** Milestone 0–2 produce the web dashboard; every later milestone must light up its own tile on that dashboard before it is considered done. We never run blind.
>
> Reading guide: each milestone has **Goal → Scope → Deliverables → Acceptance → Dependencies → Risks**. Tasks are atomic enough to assign as individual PRs.

---

## Phase index

| Phase | Milestones | Theme |
|---|---|---|
| **P0 — Foundations** | M0, M1, M2, **M2.5** | repo layout, web dashboard skeleton, metrics/logs pipeline, **data pipeline & schemas** |
| **P1 — Fleet primitives** | M3, M4, M5 | proxy pool, account pool, Playwright worker framework |
| **P2 — Scrapers** | M6, M7 (~~M8~~) | X scraper, Reddit scraper (YouTube cancelled — validator scores 0%) |
| **P3 — Correctness** | M9, M10 | normalization matching validator shape, self-validation shim |
| **P4 — Integration** | M11, M12 | miner wiring (storage + S3 + on-demand fast lane) |
| **P5 — Intelligence** | M13, M14 | gravity/DD strategist, uniqueness oracle |
| **P6 — Hardening** | M15, M16 | mainnet cutover, runbook, chaos drills |

This document covers **Phase 0 through Phase 2 in detail** (the user's requested first part: "scraper + web dashboard"). Later phases are stubbed and will be expanded when we reach them.

---

## Progress (as of 2026-04-15)

| Milestone | Status |
|---|---|
| M0 — Repo & environment scaffolding | ✅ shipped |
| M1 — Dashboard skeleton (REST + WS + auth + frontend) | ✅ shipped |
| M2 — Metric snapshot poller + retention (re-scoped) | ✅ shipped |
| M2.5 — Data pipeline & schemas | ✅ shipped |
| M3 — Proxy pool service | ✅ shipped |
| M4 — Account pool service | ✅ shipped |
| M5 — Playwright worker framework | ✅ shipped |
| **M7 — Reddit scraper (PRAW + JSON fallback)** | ✅ **shipped 2026-04-16** |
| M6 — X (Twitter) scraper via Playwright | ⏳ next |
| M8 — ~~YouTube scraper~~ | 🚫 **cancelled 2026-04-16** — validator scores 0 % per upstream [`63b31ea`](https://github.com/macrocosm-os/data-universe/commit/63b31ea) (2026-01-08) |
| M9 → | pending |

### Upstream changes that affect our plan (main pulled 2026-04-15)

Seven new commits on `main` since our base (`5672ac6`). Four are substantive for
us and force tightening to M10 / M11 / M12. Key deltas:

1. **STATE_VERSION 7 → 8; `STARTING_S3_CREDIBILITY` 0.375 → 0.1** (`rewards/miner_scorer.py`).
   Validators reset S3 credibility + boosts + effective_sizes on cutover. Our
   [vision.md](vision.md) credibility tables (and my earlier "Validator Parity Contract")
   cited 0.375; the real starting cred is now **0.1**, so re-building S3 cred
   from scratch is much slower. Penalty for even one S3 schema miss is
   correspondingly bigger. ([ba9a6cb](https://github.com/macrocosm-os/data-universe/commit/ba9a6cb))
2. **S3 validation is now strict-schema + size-weighted** (`vali_utils/s3_utils.py`).
   - Missing any required column → hard fail for the whole file.
   - Files > 1 MB get **half** the sample slots reserved for them; big files
     can no longer dilute out bad rows.
   - `files_to_check` scales up to **50** (was fixed at 20); the scraper
     window extended 72 h → 96 h and covers **old files too**, not just
     recent. A file that was valid when published but went stale (deleted
     tweets, etc.) can now sink us. ([ba9a6cb](https://github.com/macrocosm-os/data-universe/commit/ba9a6cb), [773ca24](https://github.com/macrocosm-os/data-universe/commit/773ca24))
3. **On-demand reward requires 4-phase validation before paying out**
   (`vali_utils/miner_evaluator.py`): (a) schema (`XContent.from_data_entity`)
   on 5 entities, (b) job match on username/keyword/date, (c) scraper
   validation on 1 live entity, (d) data-existence probe for empty
   submissions. **ANY phase fails → all pending entries penalised; drop-on-
   unable-to-validate (no blind reward).** Poller cache window 90 min → 3 h.
   ([cc6fb94](https://github.com/macrocosm-os/data-universe/commit/cc6fb94))
4. **OD scores reset** at the state-version bump — everyone starts level on
   OD; fresh window to catch up for new miners. ([8fd3bc1](https://github.com/macrocosm-os/data-universe/commit/8fd3bc1))

Our design already anticipates most of this (staging gate, dedup, self-
validator shim, bridge adapter) — but M10 / M11 / M12 acceptance bars
**tighten**. Changes captured inline in each of those sections below, plus a
new cross-cutting rule.

### Architecture pivots (vs. the original draft)

1. **No Docker.** Services run as native Python processes managed by **pm2** with a root [ecosystem.config.js](ecosystem.config.js).
2. **SQLite instead of PostgreSQL** (pivoted 2026-04-14 during M2). Single file at `ops/ops.db` in WAL mode; one fewer daemon to install and manage; fine for a single-host miner rig (≲ 300 k metric rows/day at default settings). Keeps Redis for streams + pub-sub. The schema migrations flatten the namespaced `ops.*` / `staging.*` design into plain table names + a `stg_` prefix for staging tables (M2.5). If multi-host deployment ever becomes a thing, the SQLAlchemy models swap back to Postgres with a single migration.
3. **No separate Prometheus / Grafana / Loki stack.** The web dashboard *is* the observability UI — metrics are polled by dashboard-api into the `metrics_snapshots` table and charted from there; logs go to `logs/*.jsonl` and (later) stream to a dashboard panel. Canonical metric names still live in [ops/shared/metrics.py](ops/shared/metrics.py) and every service exposes a Prometheus-formatted `/metrics` endpoint so a TSDB can be bolted on later if needed.
4. **Vite + React + Tailwind + shadcn/ui** (not Next.js). No SSR/file-routing needed; static build is served directly by FastAPI from `ops/dashboard/web/dist/`.
5. **Package naming: `shared/` and `datastore/`** (not `common/` and `storage/`), pivoted during M2.5 on 2026-04-14. SN13 already owns `common/` + `storage/miner/` at the repo root, and Python resolves exactly one package per name across all `sys.path` entries. Co-existing was causing the bridge tests to fail at import time. Our renames keep the two codebases' imports unambiguous: SN13's miner code still does `from common.data import DataEntity`, our code does `from shared.config import get_settings`.
6. **YouTube dropped from scope** (2026-04-16). Per [`63b31ea`](https://github.com/macrocosm-os/data-universe/commit/63b31ea) (2026-01-08), the validator stripped every YouTube code path: `YOUTUBE = 3` renamed to `UNKNOWN_3` with weight 0, 6 YouTube scraper factories removed from `scraping/provider.py`, no `EXPECTED_COLUMNS_YOUTUBE` in `vali_utils/s3_utils.py`, no YouTube in `PREFERRED_SCRAPERS` for OD validation, 0 YouTube entries in `dynamic_desirability/default.json`. **Current source weights: Reddit 0.65, X 0.35.** (Old 10% weight rolled into Reddit when the enum was removed — Reddit is a bigger lever than the docs suggest.) `docs/scoring.md` upstream still says "YouTube: 10%" — stale; trust code. M8 is cancelled. If upstream ever restores YouTube scoring, signals to watch: new `YOUTUBE` enum in `common/data.py`, factories reappearing in `scraping/provider.py`, YouTube jobs landing in `dynamic_desirability/default.json`. See cross-cutting rule #7 below.

---

## Target directory layout (after Phase 2)

```
data-universe/
├── neurons/            # existing miner/validator (minimal edits until M11)
├── scraping/           # existing SN13 scrapers (left in place as reference oracle)
├── ops/                # NEW — everything we build lives here
│   ├── shared/         # pydantic schemas, config, clock, events, metrics, pipeline
│   ├── datastore/      # SQLAlchemy models, repos, dedup, streams, bridge, migrations
│   ├── dashboard/
│   │   ├── api/        # FastAPI backend (REST + /ws/live + /metrics + static UI)
│   │   └── web/        # Vite + React + Tailwind + shadcn frontend
│   ├── pipeline/       # orchestrator (ingest / validate / promote / metrics loops)
│   ├── proxy_pool/     # proxy rotation service (M3)
│   ├── account_pool/   # cookie account rotation service (M4)
│   ├── worker/         # Playwright worker framework (M5)
│   ├── scrapers/
│   │   ├── x/          # M6
│   │   ├── reddit/     # M7
│   │   └── youtube/    # M8
│   ├── normalizer/     # M9 — per-source validator-parity normalisers
│   ├── self_validator/ # M10 — 1 % resample shim
│   ├── strategist/     # M13 — DD-aware label planner
│   └── scripts/        # preflight, init-db, future ops tooling
├── ecosystem.config.js # pm2 graph
├── vision.md
└── milestones.md       # this file
```

---

# Phase 0 — Foundations

## M0 — Repository & environment scaffolding

**Goal.** A runnable local environment in one command, with the empty shape of everything we will build.

**Scope (in).** Directory layout above; Poetry/uv project for `ops/`; Docker Compose stack (Redis, Postgres, Prometheus, Grafana, Loki, dashboard-api, dashboard-web, exporter — all empty services returning 200 on health); pre-commit (ruff, mypy, pytest); minimal CI (lint + unit).

**Scope (out).** Any scraping, any miner changes.

**Deliverables.**
- `ops/pyproject.toml` — pinned deps: `fastapi`, `uvicorn`, `pydantic>=2`, `redis`, `asyncpg`, `prometheus-client`, `structlog`, `httpx`, `playwright`, `tenacity`.
- `infra/docker-compose.yml` — redis:7, postgres:16, prometheus, grafana, loki, promtail, dashboard-api, dashboard-web, exporter.
- `ops/shared/config.py` — env-based config loader (12-factor), single source of truth.
- `ops/shared/clock.py` — `now_utc()` wrapper so tests can freeze time.
- `ops/shared/schemas.py` — pydantic v2 models for `Proxy`, `Account`, `ScrapeTask`, `ScrapeResult`, `WorkerHeartbeat` (empty but typed).
- `Makefile`: `make up`, `make down`, `make test`, `make fmt`, `make bootstrap` (playwright install).
- `docs/ops_local_dev.md` — 10-line quickstart.

**Acceptance.**
- [x] `make bootstrap` + `make preflight` + `make init-db` succeed on a fresh clone.
- [x] `make test` passes (6 tests at M0; 20 after M1).
- [x] Visiting `http://localhost:8000/` returns the "Miner Control Room" shell.
- [x] `/metrics` exposes every canonical metric name (zero-valued) from [ops/shared/metrics.py](ops/shared/metrics.py).

**Dependencies.** None.

**Risks.** Windows venv layout differs (`Scripts/` vs `bin/`); Makefile detects `$(OS)` and selects the right path. pm2 requires Node.js (already installed for the frontend tooling).

### ✅ Status — shipped

**Delivered:**
- Native, Docker-free stack: `ops/.venv` (Python 3.11+), pm2 ecosystem at repo root.
- [ops/pyproject.toml](ops/pyproject.toml) — FastAPI, pydantic 2, SQLAlchemy 2 async, redis.asyncio, structlog, alembic, prometheus-client, httpx, tenacity, playwright (worker extra).
- Shared primitives: [config.py](ops/shared/config.py), [clock.py](ops/shared/clock.py), [schemas.py](ops/shared/schemas.py), [logging.py](ops/shared/logging.py), [metrics.py](ops/shared/metrics.py), [infra.py](ops/shared/infra.py) (Postgres + Redis pools with env-driven sizing).
- dashboard-api FastAPI stub with `/api/health` (pings Redis + Postgres, 503 on degrade), `/metrics`, `/` (static shell).
- Preflight / init-db scripts in [ops/scripts/](ops/scripts/).
- [Makefile](Makefile) cross-platform; [.pre-commit-config.yaml](.pre-commit-config.yaml); [CI](.github/workflows/ops-ci.yml) runs ruff + format + mypy + pytest on every push.
- [docs/ops_local_dev.md](docs/ops_local_dev.md) — native install walkthrough.

---

## M1 — Dashboard skeleton: backend API + frontend shell

**Goal.** A web dashboard we can open in a browser and watch. Empty widgets, live-connected to the backend — so when later milestones emit events, they appear immediately with zero frontend work.

**Scope (in).**
- FastAPI backend (`ops/dashboard/api/`) with:
  - REST: `/api/health`, `/api/overview`, `/api/proxies`, `/api/accounts`, `/api/workers`, `/api/tasks`, `/api/metrics/summary`.
  - WebSocket: `/ws/live` — publishes a unified event stream (typed discriminated union).
  - Postgres read-model (SQLAlchemy 2.x async) for durable state; Redis pub/sub for live events.
- Next.js frontend (`ops/dashboard/web/`) with the five rows from [vision.md §4](vision.md#4-the-dashboard-miner-control-room):
  1. Emissions & rank (empty cards, placeholder).
  2. Pipeline throughput (empty sparklines).
  3. Fleet health (proxies / accounts / workers tables — empty).
  4. Scoring / DD coverage (empty).
  5. On-Demand (empty).
- Auth: single-user session cookie; password via env var; no public exposure (localhost / VPN only).
- Dark-mode minimalist UI; Tailwind + shadcn/ui; no auth provider, no billing, no bloat.

**Scope (out).** Any real data — we wire placeholders only.

**Deliverables.**
- `ops/dashboard/api/main.py`, `models.py` (Postgres schema), `ws.py` (broadcast hub), `routes/*.py`.
- Postgres migrations (Alembic) creating tables: `proxies`, `accounts`, `workers`, `tasks`, `task_events`, `metrics_snapshots`, `dd_jobs`.
- `ops/dashboard/web/` Next.js app with page `/dashboard` and panels for each row, consuming REST on load + WS for deltas.
- `ops/shared/events.py` — typed event union: `ProxyStateChanged`, `AccountStateChanged`, `WorkerHeartbeat`, `TaskStarted`, `TaskFinished`, `MetricTick`. Every later milestone emits these.
- `tests/dashboard/` — contract tests on REST routes + a WebSocket integration test that asserts an event round-trips.

**Acceptance.**
- [x] Opening the dashboard shows five rows with skeleton loaders, then "no data yet" empty states.
- [x] `python -m dashboard.api.seed_demo --rate 10` publishes fake events → WS carries them → `LiveFeed` panel lights up within 1s.
- [x] Hard-refreshing restores state from Postgres via REST (React Query fetches on mount).
- [x] `/api/metrics/summary` shape matches the pydantic DTO under tests.
- [x] Auth gate: public routes (health/metrics/`/`) unauthed; protected routes 401; WS rejects bad cookies with 4401.

**Dependencies.** M0.

**Risks (observed and mitigated).**
- WS bridge could block FastAPI startup if Redis was down → `_redis_loop` now reconnects with exponential backoff; dashboard-api stays up when Redis is unreachable.
- FastAPI response-model inference failed on a `FileResponse | PlainTextResponse` union → marked with `response_model=None`.

### ✅ Status — shipped

**Delivered — backend:**
- 8 `ops.*` tables via Alembic ([0001_initial_ops_schema.py](ops/datastore/migrations/versions/0001_initial_ops_schema.py)).
- SQLAlchemy models ([datastore/models.py](ops/datastore/models.py)) + 8 thin repositories ([datastore/repositories.py](ops/datastore/repositories.py)).
- Typed discriminated-union event bus ([shared/events.py](ops/shared/events.py)): `ProxyStateChanged`, `AccountStateChanged`, `WorkerHeartbeat`, `TaskStarted`, `TaskFinished`, `MetricTick` + `encode()`/`decode()` and `LIVE_CHANNEL` constant.
- REST routes: `/api/auth/login|logout`, `/api/overview`, `/api/proxies`, `/api/accounts`, `/api/workers`, `/api/tasks`, `/api/metrics/summary`.
- WebSocket `/ws/live` — Redis pub/sub bridge with exponential-backoff reconnect, per-client bounded queue (slow-consumer protection), cookie-gated.
- HMAC cookie auth (`OPS_DASHBOARD_PASSWORD`, 7-day TTL signed cookie).
- `python -m dashboard.api.seed_demo --rate N` flooder.

**Delivered — frontend** ([ops/dashboard/web/](ops/dashboard/web/)):
- Vite 5 + React 18 + TS strict + Tailwind 3 + tailwindcss-animate.
- 5 vendored shadcn primitives (Card / Button / Badge / Input / Skeleton).
- React Query + Zustand + auto-reconnecting `LiveBus` WS client.
- Login page, header (live WS-status badge + logout), 7 panels: Infrastructure, 1) Emissions & rank, 2) Pipeline throughput (sparklines), 3) Fleet health, 4) DD coverage, 5) On-Demand (live p95 from WS events), plus rolling Live Feed.
- Zero-dep `Sparkline` SVG component.
- Build: ~70 KB gzipped js + 3 KB css.

**Delivered — integration:**
- FastAPI serves `dist/index.html` + `/assets/*` when built; friendly fallback HTML when not.
- [Makefile](Makefile) adds `install-web`, `build-web`, `dev-web`, `migrate`.
- `.gitignore` covers `node_modules/`, `dist/`, `.vite/`, `.tsbuildinfo`.

**Tests (20):** smoke, health degradation, canonical metric contract, auth gate (public/protected/login-flow/WS), events round-trip (6 event kinds + unknown-kind rejection), DTO contract for `/api/metrics/summary`.

**Verification:** ruff + ruff-format clean (38 py files); mypy strict clean (32); `tsc -b` clean; ESLint clean; Vite build clean.

---

## M2 — Metric snapshot poller + retention (re-scoped for native mode)

**Goal.** Make the dashboard's metric panels show real live data without an external Prometheus. A background task inside `dashboard-api` polls each service's `/metrics` endpoint on a tick, parses the Prometheus text format, writes a row per metric per tick into `ops.metrics_snapshots`; a retention sweep prunes old rows.

Since we dropped Prometheus/Grafana/Loki/Promtail in the Docker-free pivot, the remaining work is small: the metric *contract* and structured logging already shipped in M0/M1. This milestone closes the loop so `ThroughputPanel`, `OnDemandPanel`, etc. light up with real series without a separate TSDB.

**Scope (in).**
- [ops/dashboard/api/metric_poller.py](ops/dashboard/api/metric_poller.py) — async background task started in the dashboard-api lifespan:
  - Polls a configurable list of endpoints (env `OPS_METRIC_TARGETS`, default `http://localhost:8000/metrics`).
  - Parses Prometheus exposition format (`prometheus_client.parser.text_string_to_metric_families`).
  - Inserts one row per (metric, labels, value) into `ops.metrics_snapshots`.
  - Interval configurable via `OPS_METRIC_POLL_SECONDS` (default 15).
  - Resilient: per-target failures don't kill the poller; back off and retry.
- [ops/dashboard/api/retention.py](ops/dashboard/api/retention.py) — hourly sweeper deletes `ops.metrics_snapshots` older than `OPS_METRIC_RETENTION_DAYS` (default 30).
- The existing `/api/metrics/summary` endpoint (M1) now serves real points instead of empty arrays.
- Cardinality guardrail: the poller drops series whose labels explode beyond `OPS_METRIC_MAX_SERIES_PER_METRIC` (default 50) — noisy labels bucket under `"_other"`.

**Scope (out).**
- Multi-host scraping / Prometheus-on-the-side (optional future milestone; canonical `/metrics` endpoints remain so we could bolt Prometheus on later).
- Log aggregation beyond `logs/*.jsonl` rotation — a simple log-tail panel may land in M15 with alert delivery.
- Alertmanager → Discord/Telegram — M15.

**Deliverables.**
- Poller + retention modules.
- Lifespan wiring in [dashboard/api/main.py](ops/dashboard/api/main.py).
- Settings: `OPS_METRIC_TARGETS`, `OPS_METRIC_POLL_SECONDS`, `OPS_METRIC_RETENTION_DAYS`, `OPS_METRIC_MAX_SERIES_PER_METRIC`.
- [docs/ops_metrics.md](docs/ops_metrics.md) — canonical metric names + labels (owned document).
- Tests: parse-fixture test, retention test (inserts an old row, asserts it's pruned), poller-end-to-end test with a mock target.

**Acceptance.**
- [ ] `/api/metrics/summary` returns non-empty `points` arrays after dashboard-api has been running for >1 minute (when Postgres is up).
- [ ] Inserting a `metrics_snapshots` row with `ts = now() - 40 days` and triggering the sweeper removes it.
- [ ] ThroughputPanel sparklines render real data once dashboard-api has been up through two poll ticks.
- [ ] Cardinality explosion test: feeding 100 distinct label values for one metric caps the poller at the configured limit and logs a warning.

**Dependencies.** M0, M1.

**Risks.** Write pressure on `ops.metrics_snapshots` (e.g. 50 series × 15s tick = 288k rows/day). Mitigated by conservative default retention (30d ≈ 8.6M rows ≈ manageable with existing `ix_metrics_metric_ts`). Escalation path: drop interval to 30s, or aggregate into a roll-up table on insert.

### ✅ Status — shipped

**Delivered:**
- [ops/dashboard/api/metric_poller.py](ops/dashboard/api/metric_poller.py) — background task polling every URL in `OPS_METRIC_TARGETS` concurrently per tick, parsing Prometheus text via `prometheus_client.parser`, writing rows into `ops.metrics_snapshots`. Resilient: per-target fetch failures don't kill the loop; Postgres failures logged and retried on next tick. Module-level singletons use lazy-created `asyncio.Event`s (bound to the live loop — prevents the cross-loop bug that tests exposed).
- [ops/dashboard/api/retention.py](ops/dashboard/api/retention.py) — hourly sweeper deletes rows older than `OPS_METRIC_RETENTION_DAYS`.
- Lifespan wiring in [dashboard/api/main.py](ops/dashboard/api/main.py) starts poller + sweeper alongside the WS hub; shutdown order reverses.
- Settings: `OPS_METRIC_TARGETS`, `OPS_METRIC_POLL_SECONDS`, `OPS_METRIC_RETENTION_DAYS`, `OPS_METRIC_MAX_SERIES_PER_METRIC`.
- [docs/ops_metrics.md](docs/ops_metrics.md) — canonical metric contract + environment knobs + how to add a new metric.
- Tests: parser unit tests (happy path, NaN/Inf drop, cardinality cap + `_parse_targets`).
- Same "bound to a different event loop" defensive refactor applied to `BroadcastHub` and `RetentionSweeper`.

**Verification:** ruff + format clean, mypy strict clean (34 files), **pytest 24/24** (plus 1 integration skip).

---

## M2.5 — Data pipeline & schemas

> **SQLite note (2026-04-14).** The design below originally used PostgreSQL schemas (`ops.*`, `staging.*`). We've flattened that to a single SQLite file with a table-name convention: operational tables keep their bare names (`proxies`, `accounts`, …), staging tables get a `stg_` prefix (`stg_raw_items`, `stg_normalized_items`, `stg_dedup_index`, `stg_validation_results`, `stg_promotion_log`). Semantics are unchanged; code references "staging namespace" still apply, just mapped to the prefix. JSONB becomes `sa.JSON` (TEXT on SQLite); `pg_dump` backups become SQLite `.backup` / `VACUUM INTO` snapshots.

**Goal.** One source of truth for every database, stream, and storage layer we will write or read. Before a single scraper ships (M6+), every scraped row already has a known home at every stage of its lifecycle — and nobody invents their own persistence later.

### 2.5.1 Data flow (end to end)

```
         ┌────────────────────┐
         │ Strategist (M13)   │ writes plan → enqueues tasks
         └──────────┬─────────┘
                    │
                    ▼
  Redis Stream   scrape:tasks   (consumer group: workers)
                    │
                    ▼
         ┌────────────────────┐
         │ Playwright worker  │ (M5) — leases proxy+account
         └──────────┬─────────┘
                    │ RawItem (JSON blob + metadata)
                    ▼
  Redis Stream   scrape:results  (consumer group: normalizer)
                    │
                    ▼
         ┌────────────────────┐
         │ Normalizer (M9)    │ → XContent / RedditContent / YouTubeContent
         └──────────┬─────────┘
                    │ writes to
                    ▼
 ┌────────────────────────────────────────┐
 │  Postgres  staging.raw_items           │ ← audit trail, 7-day retention
 │  Postgres  staging.normalized_items    │ ← pre-promotion buffer
 │  Postgres  staging.dedup_index         │ ← sha256(canonical_uri) unique
 └──────────┬─────────────────────────────┘
            │ batch promoter (bounded, back-pressure aware)
            ▼
 Redis Stream  validation:queue  (consumer group: self-validator)
            │
            ▼
 ┌────────────────────────────────────────┐
 │  Self-validator (M10)                  │
 │  1% Apify/PRAW resample → diff fields  │
 │  pass → promote; fail → quarantine      │
 └──────────┬─────────────────────────────┘
            │ promote (pass)
            ▼
 ┌────────────────────────────────────────┐
 │  storage/miner.sqlite  (SqliteMinerStorage — existing) │
 │  30-day rolling window, 250 GB cap     │
 └──────────┬─────────────────────────────┘
            │ 2-hour Parquet rollup (existing uploader)
            ▼
 ┌────────────────────────────────────────┐
 │  S3  data_YYYYMMDD_HHMMSS_count_16hex.parquet │
 └────────────────────────────────────────┘

 On-demand (M12) bypasses staging: reads live from SqliteMinerStorage,
 and for gaps re-injects into scrape:tasks with priority=high.
```

**Invariants.**
- No scraper writes to `SqliteMinerStorage` directly — everything transits staging + self-validation.
- `SqliteMinerStorage` remains the miner's existing store. We bridge into it; we do not replace it.
- `storage.dedup_index` is the single authority for "have we already stored this URI?". Even on-demand checks it before scraping.

### 2.5.2 Postgres schemas

Two namespaces, one database: `ops.*` (operational state) and `staging.*` (data-in-flight).

**`ops.*` (operational, used by dashboard + services)**

| Table | Purpose | Key columns | Retention |
|---|---|---|---|
| `ops.proxies` | pool state | `id`, `endpoint`, `backend`, `state`, `session_id`, `last_probe_at`, `fail_streak`, `quarantined_until` | permanent |
| `ops.accounts` | cookie accounts | `id`, `source`, `state`, `pinned_proxy_id`, `rate_budget_json`, `cookies_sealed`, `ua`, `imported_at`, `last_ok_at` | permanent |
| `ops.workers` | worker registry | `id`, `host`, `state`, `last_heartbeat_at`, `current_task_id`, `browser_context_count` | 30 d after last heartbeat |
| `ops.tasks` | scrape tasks | `id`, `source`, `label`, `mode` (search/profile/permalink/channel/comment), `params_json`, `priority`, `state`, `created_at`, `started_at`, `finished_at`, `worker_id`, `outcome`, `error` | 30 d |
| `ops.task_events` | per-task event log | `id`, `task_id`, `ts`, `kind`, `payload_json` | 30 d |
| `ops.dd_jobs` | DD/Gravity snapshot | `id`, `source`, `label`, `weight`, `post_start`, `post_end`, `seen_at` | 90 d |
| `ops.metrics_snapshots` | hourly rollups | `ts`, `metric`, `labels_json`, `value` | 90 d |
| `ops.chain_state` | on-chain telemetry | `ts`, `hotkey`, `incentive`, `stake`, `credibility_p2p`, `credibility_s3`, `credibility_od`, `rank` | permanent (append-only) |

**`staging.*` (data in flight — product data, not ops)**

| Table | Purpose | Key columns | Retention |
|---|---|---|---|
| `staging.raw_items` | immutable audit trail of what the scraper *actually* produced (before normalization) | `id`, `task_id`, `source`, `uri`, `fetched_at`, `raw_json`, `har_s3_key?` | 7 d |
| `staging.normalized_items` | post-normalization buffer, pre-promotion | `id`, `raw_id`, `source`, `uri`, `content_hash`, `datetime`, `label`, `normalized_json`, `content_size_bytes`, `state` (`pending`/`validating`/`promoted`/`quarantined`/`dropped`), `state_reason` | 7 d |
| `staging.dedup_index` | global URI + content-hash uniqueness | `canonical_uri` PK, `content_hash`, `source`, `datetime`, `first_seen_at` | 35 d (5 d past freshness window) |
| `staging.validation_results` | self-validator output | `id`, `normalized_item_id`, `validated_at`, `passed`, `field_diffs_json`, `validator_scraper` | 30 d |
| `staging.promotion_log` | audit trail of promotions into SqliteMinerStorage | `id`, `normalized_item_id`, `promoted_at`, `sqlite_rowid` | 30 d |

All `id`s are `BIGSERIAL`; all timestamps `TIMESTAMPTZ`; every table has a `created_at` default `now()`.

### 2.5.3 Redis Streams

Streams are transient transport, not storage. Postgres is the durable record.

| Stream | Producer | Consumer group | Retention | Purpose |
|---|---|---|---|---|
| `scrape:tasks` | strategist (M13), OD fast lane (M12) | `workers` | 24 h (MAXLEN ~) | task fan-out |
| `scrape:results` | workers (M5) | `normalizer` | 6 h | raw results in flight |
| `validation:queue` | promoter | `self_validator` (M10) | 24 h | sample-for-resample pipeline |
| `ondemand:requests` | miner protocol handler | `od_fast_lane` (M12) | 10 min | priority OD dispatches |
| `events:bus` | every service | `dashboard_api` | 1 h | live dashboard push |

Consumer-group ACK semantics guarantee at-least-once; idempotency is enforced by `staging.dedup_index` at the normalizer step (before `raw_items` insert).

### 2.5.4 Bridge to existing `SqliteMinerStorage`

[storage/miner/sqlite_miner_storage.py](storage/miner/sqlite_miner_storage.py) stays untouched. We add **`ops/datastore/sqlite_adapter.py`** — a thin writer that:
- Accepts a batch of promoted `staging.normalized_items`.
- Constructs `DataEntity` objects (same shape the existing scrapers produce).
- Calls `SqliteMinerStorage.store_data_entities()` with batching + transaction.
- On success, writes `staging.promotion_log` + marks normalized_item `state='promoted'`.
- On failure, marks `state='pending'` so it retries next tick.

Bounded batch size (default 500) and a concurrency limit of 1 writer per sqlite file to avoid lock contention; sqlite is the bottleneck so we size upstream back-pressure around its observed write throughput.

**Why staging instead of direct writes:**
1. Self-validation runs *after* normalization but *before* promotion — we must be able to drop a bad row without reaching into `SqliteMinerStorage`.
2. Credibility is sacred: a validator asking us for data we haven't yet self-validated is safer to 404 on than to serve with uncertain fields.
3. The miner process and the scraper fleet are independent. If the miner restarts mid-batch, staging still holds the truth.

### 2.5.5 Deduplication

Canonicalization rules per source (URI normalization before hashing):
- **X:** strip query params; lowercase scheme+host; `x.com` and `twitter.com` collapse to `x.com`; keep `/status/<id>` only.
- **Reddit:** strip `utm_*`, `share_id`; collapse `old.reddit.com`, `www.reddit.com` → `reddit.com`; keep `/r/<sub>/comments/<id>/<slug>` with `<slug>` dropped on comparison.
- **YouTube:** collapse `youtu.be`, `m.youtube.com`, `www.youtube.com`; keep `watch?v=<id>` only.

`content_hash = sha256(canonical_uri + '|' + normalized_text_blob)` — URI alone misses tweet-edit cases; hash alone misses URL-shuffling cases. Unique constraint on `(canonical_uri)`; secondary index on `content_hash`.

### 2.5.6 Retention & backup

Scheduled jobs (pg_cron or a simple ops service running `asyncio.gather` loops):
- Every hour: prune `staging.*` past its retention window.
- Every hour: refresh the mv/summary used by the dashboard "Data pipeline" panel.
- Nightly at 03:00 UTC: `pg_dump` to encrypted S3 (7-day rolling).
- Nightly at 03:10 UTC: hot-copy of `storage/miner.sqlite` via SQLite online backup API to encrypted S3 (7-day rolling).
- Never auto-prune `ops.chain_state` — it's our audit trail of what we earned.

### 2.5.7 Deliverables

- `ops/pipeline/flow.md` — this diagram + invariants, treated as the authoritative design doc.
- `ops/datastore/migrations/` — Alembic migrations for both `ops` and `staging` schemas.
- `ops/datastore/models.py` — SQLAlchemy 2.x async models.
- `ops/datastore/repositories.py` — repository-pattern wrappers per table (no raw SQL in services).
- `ops/datastore/streams.py` — producer/consumer helpers for Redis Streams with typed payloads (pydantic).
- `ops/datastore/dedup.py` — canonicalization + hashing.
- `ops/datastore/sqlite_adapter.py` — bridge to `SqliteMinerStorage`.
- `ops/datastore/retention.py` — prune jobs.
- Dashboard: **"Data pipeline"** panel added to the existing Row 2 (throughput), showing live queue depths for each stream + staging state histogram (`pending`/`validating`/`promoted`/`quarantined`).
- `docs/ops_database.md` — ER diagram + per-table ownership + who writes / who reads.

### 2.5.8 Acceptance

- [ ] `make test` runs an integration test that: pushes a synthetic `RawItem` onto `scrape:results` → normalizer consumes it → row appears in `staging.normalized_items` → self-validator stub passes it → `SqliteMinerStorage` contains the row with correct `DataEntity` shape → `staging.promotion_log` records the event.
- [ ] Duplicate-URI replay of the same item produces exactly one row end-to-end.
- [ ] Retention test: insert a `staging.raw_items` row with `created_at = now() - 8 days`, run retention, row is gone.
- [ ] Back-pressure test: if the sqlite writer stalls (simulated), the `staging.normalized_items` `pending` count grows but `scrape:results` does not exceed 2× its normal depth (the normalizer slows consumption).
- [ ] Dashboard "Data pipeline" panel shows live, non-zero queue depths during the test.
- [ ] ER diagram in `docs/ops_database.md` matches actual migrations (checked by a test that introspects the live DB).
- [ ] A `pg_dump` + `sqlite` backup can be restored into a scratch environment and pass the same integration test.

### 2.5.9 Dependencies

M0 (Postgres + Redis up), M1 (dashboard shell to host the panel), M2 (metrics + logs for observability of the pipeline itself).

### 2.5.10 Risks

- **SQLite write contention.** `SqliteMinerStorage` is single-writer. Mitigate: bounded batch promoter with back-pressure signalled upstream via stream lag; tune batch size against observed write throughput; add Prometheus alert on `staging.normalized_items` `pending` count > 10k.
- **Schema drift vs existing miner code.** If SN13 changes `DataEntity` fields, our adapter breaks silently. Mitigate: the parity test in M9 covers this — any change in the validator's scraper shape fails CI.
- **Cookie/PII leakage into staging.** Staging persists raw scraper output including headers. Mitigate: `raw_json` is sanitized in the worker before stream publish (cookies, auth headers stripped); a regex test in CI asserts no `Cookie:` / `Authorization:` substring can appear in `staging.raw_items`.
- **Retention misconfiguration** (dropping data we still owe validators). Mitigate: `SqliteMinerStorage` retention stays governed by the existing miner code — we only prune *staging*. Staging retention (7 d) is longer than the worst-case self-validation latency (hours), so we never drop data that might be quarantined-but-recoverable.

### ✅ Status — shipped

**Delivered (all in `ops/`):**
- 5 `stg_*` tables (raw_items, normalized_items, dedup_index, validation_results, promotion_log) via [models.py](ops/datastore/models.py) + Alembic migration [0002_staging_tables.py](ops/datastore/migrations/versions/0002_staging_tables.py).
- [datastore/dedup.py](ops/datastore/dedup.py) — per-source canonicalisation (X / Reddit / YouTube) + sha256 content hash. Tracking-param scrub built in.
- [common/pipeline.py](ops/shared/pipeline.py) — typed envelopes for the four streams: `ScrapeTaskEnvelope`, `ScrapeResultEnvelope`, `ValidationEnvelope`, `OnDemandRequestEnvelope`; canonical `StreamName` + `ConsumerGroup` enums.
- [datastore/streams.py](ops/datastore/streams.py) — Redis Streams helpers (`ensure_group` / `publish` / `consume` / `ack` / `pending_count` / `stream_length`) with at-least-once semantics, PEL replay on consumer restart, dead-letter on decode-fail, MAXLEN trim per stream.
- Staging repos in [datastore/repositories.py](ops/datastore/repositories.py): `StgRawItemRepo`, `StgDedupRepo` (atomic `INSERT ... ON CONFLICT DO NOTHING` reservation), `StgNormalizedItemRepo` (state-machine claim/mark), `StgValidationRepo`, `StgPromotionRepo`.
- [datastore/sqlite_adapter.py](ops/datastore/sqlite_adapter.py) — `BridgePromoter` writes promoted rows into `SqliteMinerStorage` via `asyncio.to_thread`, builds `DataEntity` objects from staging rows, retries on store failure, quarantines rows whose `normalized_json` is malformed.
- Stub `Normalizer` ([normalizer/base.py](ops/normalizer/base.py)) + `PassthroughNormalizer` for tests; stub `SelfValidator` ([self_validator/base.py](ops/self_validator/base.py)) + `AlwaysPassValidator`. Real per-source impls land in M9 / M10.
- [pipeline/orchestrator.py](ops/pipeline/orchestrator.py) — schedules ingest / validate / promote / metrics loops in one event loop. ACK after durable persist; no message lost on crash.
- [retention.py](ops/dashboard/api/retention.py) extended to all `stg_*` tables; `pending` / `validating` are never auto-pruned.
- [docs/ops_pipeline.md](docs/ops_pipeline.md) — canonical flow diagram + invariants + per-source onboarding.

**Tests:** dedup canonicalisation (X/Reddit/YouTube + content-hash), Streams round-trip + replay (fakeredis), staging repo state-machine + dedup-conflict, bridge promotion into a real temp `SqliteMinerStorage` file, and the **end-to-end test** that pushes a synthetic `ScrapeResultEnvelope` through the full orchestrator and verifies the row appears in the miner DB with dedup suppressing duplicates.

**Verification:** ruff + format clean (49+ files), mypy strict clean (45 source files).

---

# Phase 1 — Fleet primitives

## M3 — Proxy pool service

**Goal.** A supervised pool of residential proxies, each with health, stickiness (per-account sessions), and graceful rotation. All scraper code asks the pool for a proxy; it never picks directly.

**Scope (in).**
- `ops/proxy_pool/service.py` — long-running service, REST + Redis API:
  - `POST /lease` → returns a proxy for `{ account_id?, source, sticky_minutes? }`.
  - `POST /release` → returns lease; caller reports outcome (`ok`/`429`/`block`/`timeout`).
  - `GET /pool` → current pool state.
- Proxy sources: pluggable backends — `static_list`, `bright_data`, `iproyal`, `oxylabs`. Credentials via env; no secrets in repo.
- Health loop: every 2 min, probe each proxy with a cheap, well-chosen HTTP target (small image from a CDN, not the target sites). Mark bad after 3 consecutive failures; quarantine for 10 min; re-probe.
- **Stickiness:** optional `session_id` sub-key per account so the same exit IP is reused for 15–60 min. Residential providers typically expose this via username suffix (`user-session-XYZ`).
- Postgres persistence of proxy state; Redis for live counters.

**Scope (out).** Per-country or per-city selection logic — defer until a scraper actually demands it.

**Deliverables.**
- Service with passing integration tests against a mock backend (`httpbin` in Docker).
- Dashboard panel: "Proxies" populated from `GET /api/proxies` (reads from proxy_pool via dashboard-api).
- Prometheus metrics wired: `proxy_requests_total`, `proxy_health_state`.

**Acceptance.**
- [ ] With 5 mock proxies in a list backend, 100 concurrent `lease`/`release` cycles complete in <5s with correct accounting.
- [ ] Killing a proxy (blackhole route) marks it unhealthy within 2 min and re-marks healthy within 2 min of recovery.
- [ ] Sticky sessions: same `(account_id, session_id)` returns same proxy for the whole window.
- [ ] Dashboard shows live proxy state; toggling a proxy off via UI reaches the service.

**Dependencies.** M0, M1, M2.

**Risks.** Proxy providers' APIs/formats differ. Mitigate with a `ProxyBackend` protocol + one adapter per provider; default to `static_list` with manually-pasted endpoints for development.

### ✅ Status — shipped

**Delivered (all in [`ops/proxy_pool/`](ops/proxy_pool/)):**
- Typed envelopes in [schemas.py](ops/proxy_pool/schemas.py): `LeaseRequest`/`LeaseResponse`, `ReleaseRequest`, `LeaseOutcome`, `ProxySnapshot`, `PoolState`.
- Pluggable backends — [protocol.py](ops/proxy_pool/backends/protocol.py) + [static_list.py](ops/proxy_pool/backends/static_list.py) (reads `OPS_PROXY_STATIC_ENDPOINTS`, optional `OPS_PROXY_STATIC_SUPPORTS_STICKY`). Session token injected as `user-session-<id>` on the URL username when supported. Deterministic proxy IDs (sha256[:16] of URL) so resyncs don't duplicate rows.
- [service.py](ops/proxy_pool/service.py): `ProxyPoolService` — `sync_from_backends` (upsert), `lease` (random among healthy, sticky via Redis), `release` (fail-streak bump → quarantine at 3 fails), `set_disabled` (admin), `snapshot`. URL credentials masked before leaving the process.
- [health.py](ops/proxy_pool/health.py): `HealthProber` — probes `https://www.gstatic.com/generate_204` every 2 min through each non-disabled proxy; state machine with exponential cooldown (10 min → 60 min cap); tz-safe comparisons.
- REST routes in [proxy_pool_routes.py](ops/dashboard/api/routes/proxy_pool_routes.py): `POST /api/proxy-pool/lease|release`, `GET /state`, `POST /admin/sync|{id}/disable|{id}/enable` — all cookie-gated.
- Lifespan wiring in [dashboard/api/main.py](ops/dashboard/api/main.py): instantiate service, sync, register singleton, start health prober; shutdown reverses order.
- Frontend: [ProxyPoolPanel.tsx](ops/dashboard/web/src/components/panels/ProxyPoolPanel.tsx) — live state-count badges, per-proxy row (masked URL / state badge / fail streak / last-probe age / enable-disable toggle) + re-sync action; polls `/api/proxy-pool/state` every 5 s.
- Canonical metrics wired: `proxy_requests_total{proxy_id,outcome}` and `proxy_pool_size{state}` tick on every lease / release / probe.
- [docs/ops_proxies.md](docs/ops_proxies.md) — env vars, REST, state machine, stickiness, scaling-beyond-one-process recipe.
- [ecosystem.config.js](ecosystem.config.js) comment explains the in-process-today / split-out-later deployment choice.

**Tests (25 new):**
- [test_proxy_backends.py](ops/tests/test_proxy_backends.py) — empty/multi parsing, stable IDs, session injection on-supported / no-op-unsupported / no-op-when-no-username, URL masking.
- [test_proxy_service.py](ops/tests/test_proxy_service.py) — sync populates, lease fails when empty, round-trip, sticky returns same proxy, three failures quarantine, OK resets streak, **100 concurrent lease/release cycles with correct accounting**, admin disable/enable.
- [test_proxy_health.py](ops/tests/test_proxy_health.py) — success keeps healthy, three failures quarantine, recovery after cooldown.
- [test_proxy_routes.py](ops/tests/test_proxy_routes.py) — auth gating, state endpoint, lease/release happy path, admin toggles, unknown-lease 404.
- Session-scoped autouse fixture in conftest wipes the `proxies` table before each test so counts stay deterministic.

**Verification:** ruff + format clean; mypy strict clean (53 files); pytest **79 passed + 3 skipped**; Vite frontend rebuilds with new panel.

**Acceptance:**
- [x] 100 concurrent lease/release cycles complete quickly with correct accounting (test_concurrent_leases_accounting).
- [x] Three failed outcomes → proxy quarantined (test_repeated_failure_quarantines_proxy, test_three_failures_quarantine).
- [x] Quarantined proxy → probe success flips it back to healthy with fail_streak=0 (test_recovery_back_to_healthy).
- [x] Sticky `(account_id, session_id)` returns same proxy across leases (test_sticky_session_returns_same_proxy).
- [x] Dashboard admin disable/enable toggles reach the service (test_admin_disable_then_enable).

**Goal.** A supervised pool of cookie-authenticated accounts (X primary, Reddit secondary), with warm-up, health, quarantine, and per-account rate budgets.

**Scope (in).**
- `ops/account_pool/service.py`:
  - `POST /lease` → returns account (cookies, user-agent, session headers) for `{ source, action }`.
  - `POST /release` with outcome, advances rate budget and updates state.
  - `POST /import` → add account from a JSON blob (cookies, UA, optional proxy-pinning).
  - `GET /accounts` → status.
- State machine per account: `new → warming → active → cooling → quarantined → retired`.
- Encrypted-at-rest cookie storage (libsodium sealed box, key from env).
- Per-account budgets: requests/min, requests/hour; exposed as metrics.
- Pinning: optionally pin an account to a specific proxy session (so the same residential IP is reused for that account always). This is what keeps X accounts alive longest.
- Import tool: `python -m ops.account_pool import account.json` — takes cookies exported from Chrome/Firefox session.
- Health signal: dashboard pings `/account/{id}/probe` — a cheap read (fetch own profile) — every N minutes; auto-cool on 401/403.

**Scope (out).** Account *creation* and warm-up automation — we bring accounts we already own; automating signup risks ToS violations and isn't needed to ship.

**Deliverables.**
- Service + tests (mock target server verifying cookie presence).
- Dashboard "Accounts" panel: table with state, last success, last failure, rate budget usage bar, pinned proxy.
- Runbook `docs/ops_accounts.md`: how to export cookies safely, how to import, how to warm up a new account by hand before putting it into rotation.

**Acceptance.**
- [ ] Importing an account stores it encrypted; restart-safe.
- [ ] Leasing returns cookies + UA + pinned proxy lease atomically.
- [ ] A 401 response flips the account to `cooling` within one release cycle.
- [ ] Dashboard shows accurate per-account budgets and state transitions live.
- [ ] Cookies never appear in logs (grep Loki output in a test).

**Dependencies.** M3.

**Risks.** Compromised accounts → mass suspension. Mitigate: low RPS per account, sticky proxy per account, realistic UA, aggressive cooling, one-account-per-datacenter policy.

### ✅ Status — shipped

**Delivered:**
- Schema: Alembic migration [0003_accounts_auth.py](ops/datastore/migrations/versions/0003_accounts_auth.py) adds `cookies_sealed` (LargeBinary), `user_agent`, `last_fail_at`, `last_fail_reason`, `fail_streak`, `notes` to the existing `accounts` table via `batch_alter_table`.
- [account_pool/crypto.py](ops/account_pool/crypto.py) — `CookieSealer` wraps `cryptography.fernet.Fernet`; key from `OPS_ACCOUNT_POOL_KEY`; tamper-detection test proves a flipped ciphertext byte raises. Service refuses to start without the key (loud warning; routes return 503) — no silent plaintext storage.
- [account_pool/schemas.py](ops/account_pool/schemas.py) — typed envelopes: `AccountImport`, `AccountLeaseRequest`/`Response`, `AccountReleaseRequest`, `AccountSnapshot`, `AccountPoolState`, `LeaseOutcome`. Snapshot is by-design cookie-free.
- [account_pool/service.py](ops/account_pool/service.py) — `AccountPoolService`: `import_account` (seal + insert), `lease` (pick active → bump Redis budget counters atomically → decrypt cookies → optionally lease paired proxy with account-id as sticky key), `release` (outcome → state machine + release paired proxy), `snapshot`, `set_state` admin. Writers serialised through a lazy `asyncio.Lock`. Cooling auto-recovers after 30 min by flipping back to active on the next pick.
- [account_pool/import_cli.py](ops/account_pool/import_cli.py) — `python -m account_pool.import_cli account.json`.
- REST routes in [account_pool_routes.py](ops/dashboard/api/routes/account_pool_routes.py): `POST /api/account-pool/{import,lease,release}`, `GET /state`, admin: `POST /admin/{id}/{quarantine,activate,retire}` — all cookie-gated.
- Lifespan wiring in [dashboard/api/main.py](ops/dashboard/api/main.py): instantiate sealer from env, mount singleton, degrade gracefully on missing key.
- Frontend: [AccountPoolPanel.tsx](ops/dashboard/web/src/components/panels/AccountPoolPanel.tsx) — per-source/state badge bar, per-account table with pinned proxy, **live budget bars (minute + hour)**, last ok / last fail with reason, admin buttons (quarantine / activate / retire); polls `/api/account-pool/state` every 5 s.
- Canonical metrics: `account_state{account_id,source,state}` and `account_rate_budget_remaining{account_id,source}` tick on every lease / release / admin change.
- [docs/ops_accounts.md](docs/ops_accounts.md) — full runbook: Fernet key generation, safe cookie export workflow, pinning to proxies, state machine diagram, REST surface, security notes.

**Tests (23 new):**
- [test_account_crypto.py](ops/tests/test_account_crypto.py) — 6 tests: roundtrip (list + dict), wrong key fails, tampered ciphertext fails, empty/garbage key rejection.
- [test_account_service.py](ops/tests/test_account_service.py) — 10 tests: import round-trip, lease returns cookies, unavailable when no active, `new → active` promotion on first OK, auth-failed cools for 30 min, 3-fails quarantines, rate-limited is no-op, unknown lease 404s, **per-minute budget exhaustion blocks next lease**, admin activate re-enables.
- [test_account_no_log_leak.py](ops/tests/test_account_no_log_leak.py) — structlog output captured via stdout/stderr redirect; asserts cookie name/value + UA + `Cookie:` / `Set-Cookie` strings NEVER appear in log output. Full lifecycle (import → lease → release with auth_failed) driven under the capture.
- [test_account_routes.py](ops/tests/test_account_routes.py) — 7 tests: auth gate on import + lease, import→lease→release happy path, `/state` never exposes cookies, admin quarantine/activate, unknown lease 404.

**Verification:** ruff + format clean; mypy strict clean (60 files); **pytest 102 passed + 3 skipped**; Vite rebuild green.

**Acceptance:**
- [x] Importing an account stores cookies encrypted (test_roundtrip_list_of_cookies, test_import_round_trip).
- [x] Leasing returns cookies + UA + pinned proxy lease atomically (service.lease calls proxy_pool.lease on pinned accounts; paired release too).
- [x] A 401 (auth_failed) response flips the account to `cooling` within one release cycle (test_auth_failed_cools_account).
- [x] Dashboard shows accurate per-account budgets and state transitions live (AccountPoolPanel + `account_state` gauge).
- [x] Cookies never appear in logs (test_no_cookie_leak_in_logs).

---

## M5 — Playwright worker framework

**Goal.** A generic, reusable Playwright worker that any scraper plugs into. Workers consume tasks from a queue, lease proxy+account, run a scraper function, emit events, return results. Fully observable.

**Scope (in).**
- `ops/worker/runtime.py`:
  - Reads tasks from Redis Stream (`scrape:tasks`).
  - Leases `(proxy, account)` from pools.
  - Spawns a Playwright **browser context** (not a new browser) per task with cookies + UA + proxy injected.
  - Stealth: `playwright-stealth` plus JA3 mitigations via Chromium launch args; randomized viewport, timezone, locale (consistent with account).
  - Calls the registered `Scraper.run(page, task)` plugin for the task's `source`.
  - Emits events on start/progress/finish/error; pushes `ScrapeResult`s to Redis Stream (`scrape:results`).
  - Hard timeout per task; CPU/memory guard; context torn down on every task (no reuse unless explicitly sticky-sessioned).
- `ops/worker/scraper_api.py`: the plugin contract:
  ```python
  class Scraper(Protocol):
      source: DataSource
      async def run(self, ctx: ScrapeContext) -> list[RawItem]: ...
      async def parse_item(self, raw: RawItem) -> NormalizedContent: ...
  ```
- Concurrency: N workers per host; one task per worker at a time; back-pressure via stream consumer groups.
- Browser reuse: one Chromium process per worker, new context per task (Playwright best practice — fast and isolated).
- **Recording hooks:** optional HAR + screenshot on failure, stored to S3/MinIO for debugging. Dashboard exposes links.

**Scope (out).** Specific scrapers (M6–M8). This milestone ships a trivial `echo_scraper` plugin for smoke testing.

**Deliverables.**
- Worker service in Docker (Playwright base image).
- `echo_scraper` that visits `httpbin.org/anything` via the proxy and returns headers — used in tests and local demos.
- Dashboard "Workers" panel: live heartbeat, current task, browser context count, last N failures with HAR links.
- Prometheus: `worker_tasks_total`, `worker_task_duration_seconds`, `worker_browser_crashes_total`.

**Acceptance.**
- [ ] 20 concurrent echo tasks complete in <60s with 0 crashes.
- [ ] Killing a worker mid-task re-queues the task (consumer-group ACK semantics).
- [ ] A task that raises emits a failure event, stores HAR+screenshot, and the dashboard shows a clickable "debug" link.
- [ ] Memory steady-state <400MB per worker over a 1h soak test.

**Dependencies.** M3, M4.

**Risks.** Playwright memory creep. Mitigate by recycling workers every N tasks or every M minutes; monitored in M15 chaos drill.

### ✅ Status — shipped

**Delivered (all in [`ops/worker/`](ops/worker/)):**
- Plugin contract in [scraper_api.py](ops/worker/scraper_api.py) — `Scraper` Protocol (`source`, `supports(mode)`, `run(ctx)`), `ScrapeContext` dataclass, module-level registry (`register_scraper` / `get_scraper`).
- [browser.py](ops/worker/browser.py) — Chromium launch args with automation-flag strip, `fingerprint_for(account_id)` (stable per-account seeded RNG over viewport / timezone / locale), `new_context` that applies cookies + UA + proxy + HAR recorder + inline anti-automation `add_init_script`.
- [recording.py](ops/worker/recording.py) — HAR captured on every task; dropped on success, kept on failure (+ screenshot). Local FS layout: `logs/worker/{har,screenshot}/{task_id}`. Retention sweeper hook ready.
- [heartbeat.py](ops/worker/heartbeat.py) — 15 s tick, upserts `ops.workers`, publishes `WorkerHeartbeat` to live bus, exposes `worker_heartbeat_age_seconds` + `worker_busy`. Lazy `asyncio.Event` so singletons survive cross-loop tests.
- [runtime.py](ops/worker/runtime.py) — `WorkerRuntime` consumes `scrape:tasks` (consumer group `workers`), leases account + paired proxy, opens fresh Chromium context per task, invokes plugin, publishes `ScrapeResultEnvelope` to `scrape:results`, releases with outcome-inferred account release enum (`AUTH_FAILED` / `BLOCKED` / `TIMEOUT` / `RATE_LIMITED` / `ERROR` / `OK`). Hard task timeout 300 s; per-task ACK after completion.
- [plugins/echo.py](ops/worker/plugins/echo.py) — smoke-test scraper that visits `httpbin.org/anything` and returns headers + origin IP. Registered at package import time.
- CLI entry points: `python -m worker` ([__main__.py](ops/worker/__main__.py)) + `python -m worker.seed_task --source x --mode search --label "#bitcoin"` ([seed_task.py](ops/worker/seed_task.py)).
- REST: [worker_routes.py](ops/dashboard/api/routes/worker_routes.py) — `GET /api/worker/debug/{task_id}/{har,screenshot,exists}` (all cookie-gated).
- Canonical metrics: `worker_tasks_total{worker_id,source,outcome}`, `worker_task_duration_seconds{source}` (histogram), `worker_browser_crashes_total{worker_id}`, plus existing `worker_heartbeat_age_seconds` + `worker_busy`. Contract test updated.
- `WorkerRepo.upsert_heartbeat` + `mark_offline` (SQLite `ON CONFLICT DO UPDATE`).
- [ecosystem.config.js](ecosystem.config.js) ships a single `worker` pm2 app (source-agnostic; scaled via `pm2 scale worker N`; 1 GB memory recycle).
- Frontend: [WorkersPanel.tsx](ops/dashboard/web/src/components/panels/WorkersPanel.tsx) — per-worker table with state / current task / context count / memory / heartbeat freshness badge (green/yellow/red by age) + HAR + screenshot buttons linking to the debug endpoints.
- [docs/ops_worker.md](docs/ops_worker.md) — quick start, plugin authoring guide, fingerprint design, recording, scaling rules-of-thumb, crash semantics, troubleshooting.

**Tests (20 new):**
- [test_worker_scraper_api.py](ops/tests/test_worker_scraper_api.py) — register / lookup, duplicate-registration fails, idempotent re-registration OK, echo auto-registration.
- [test_worker_recording.py](ops/tests/test_worker_recording.py) — path composition, keep/drop idempotency, retention sweep drops old but keeps fresh.
- [test_worker_browser.py](ops/tests/test_worker_browser.py) — fingerprint stable per account, differs across accounts, random for unauthed, critical launch flags present.
- [test_worker_runtime.py](ops/tests/test_worker_runtime.py) — **happy path publishes ScrapeResultEnvelope + drops HAR**; **failure path keeps HAR + records error**; missing scraper is silent noop; heartbeat flips BUSY during task and IDLE after; `start()` creates the consumer group.

**Verification:** ruff + format clean; mypy strict clean (71 files); **pytest 122 passed + 3 skipped**; Vite rebuilds with WorkersPanel.

**Acceptance:**
- [x] Happy path produces a ScrapeResultEnvelope with `outcome=OK` and plugin items (test_happy_path_publishes_scrape_result).
- [x] Killing a worker mid-task re-queues: consumer-group ACK-after-complete means un-ACKed messages replay on the next consumer tick (design verified; ACK placement is in `_consume_loop.finally`).
- [x] Task raises → HAR + screenshot kept, failure event + error recorded (test_failure_path_captures_har_and_records_error).
- [x] Dashboard shows a clickable debug link (WorkersPanel + `/api/worker/debug/{id}/{har,screenshot}` endpoints).
- Memory steady-state under 1 h soak — pm2 `max_memory_restart: "1G"` gates it; full 1 h soak is a deployment drill, not a unit test.

---

# Phase 2 — Scrapers

## M6 — X (Twitter) scraper via Playwright

**Goal.** Production scraper for X, producing `XContent` rows that match the ApiDojo actor's shape field-for-field, at volumes sufficient to cover all active DD X-labels.

**Scope (in).**
- Three traversal modes:
  1. **Search** — `x.com/search?q=<label>&f=live` for hashtag/keyword labels.
  2. **Profile** — `x.com/<username>` for username-pinned DD jobs.
  3. **Permalink** — `x.com/<user>/status/<id>` for on-demand URL requests.
- Scroll loop with idle-timeout and duplicate-guard; hard cap `max_items` from task config.
- DOM-plus-`__INITIAL_STATE__` parser (JSON embedded in page is more stable than DOM text).
- Fallback: intercept XHR responses (`/i/api/graphql/...`) via Playwright's `route`/`response` events — this is the most stable path because it's the same JSON the web app itself consumes.
- Output normalizer → `ops/normalizer/x.py` builds `XContent` with **exactly** the fields [apidojo_scraper.validate()](scraping/x/apidojo_scraper.py) checks: URI, text, user.username, user.display_name, user.id, user.verified, user.followers_count, user.following_count, tweet.id, like_count, retweet_count, reply_count, quote_count, view_count (if present), hashtags, cashtags, is_retweet/reply/quote, conversation_id, media[{url, type}].
- Per-field tolerance map (counts can drift ±5%, text must be exact modulo whitespace) — consulted by the self-validation shim in M10.

**Scope (out).** Account creation, login automation — accounts come pre-imported via M4.

**Deliverables.**
- `ops/scrapers/x/search.py`, `profile.py`, `permalink.py`, `parser.py`.
- Fixture-based unit tests: checked-in HTML snapshots and GraphQL JSON snapshots drive parser tests — zero network in CI.
- One golden-set integration test: against a fixed set of 20 public tweets, compare our output to the ApiDojo output (run manually once, checked in as fixtures). Must match at >99% field equality.
- Dashboard "Scrapers → X" panel: live RPS, success %, per-label row count in last 1h/24h, token/account burn rate, top failure modes.

**Acceptance.**
- [ ] 10k tweets/hour sustained for 6h on 4 workers + 10 accounts + 20 residential IPs with <1% account-cooling events.
- [ ] Golden-set parity ≥99% over 20 tweets (diffs printed in CI).
- [ ] Parser handles all tweet types: text, media (image/video/GIF), quote, retweet, reply, thread.
- [ ] Empty-result rate <5% (soft-ban detection).

**Dependencies.** M5.

**Risks.** X changes GraphQL paths frequently. Mitigate with a small internal "schema version" detector that fails closed and alerts — better to stop ingesting than to ingest wrong data (credibility!).

---

## M7 — Reddit scraper — ✅ SHIPPED (2026-04-16)

**Goal.** Reddit data from subreddits and users, matching the `RedditContent` shape used by [reddit_custom_scraper.py](scraping/reddit/reddit_custom_scraper.py). Reddit is 65 % of the reward pie post-YouTube removal — this is the single highest-leverage plugin in the fleet.

**What shipped.**
- **Normalizer** at [`ops/normalizer/reddit.py`](ops/normalizer/reddit.py) — pydantic v1 mirror of `RedditContent`, byte-exact `json(by_alias=True)` output, minute-obfuscated `createdAt`/`scrapedAt`, media dedup via `extract_media_urls`. NSFW+media combos are rejected at normalize time so they never stage.
- **PRAW primary path** at [`ops/worker/plugins/reddit/praw_scraper.py`](ops/worker/plugins/reddit/praw_scraper.py) — `asyncpraw` client built from the account pool's `credentials` blob. Supports `SEARCH`, `PERMALINK`, `PROFILE` task modes. Refresh-token OAuth preferred; script apps supported as fallback.
- **JSON fallback** at [`ops/worker/plugins/reddit/json_scraper.py`](ops/worker/plugins/reddit/json_scraper.py) — public `.json` endpoints via the proxy pool. Auto-triggered when no credentials on the lease. 429 → `RATE_LIMITED`, 403 → `BLOCKED`, via exception-name keyword mapping (unchanged runtime release path).
- **Account pool schema** extended — `AccountImport` now accepts either `cookies` (X-style) or `credentials` (Reddit PRAW). Sealed blob is v2 dict format; backward-compatible with v1 cookie-list-only reads.
- **Dedup canonicalization fix** — the old `_canonical_reddit` had dead code that silently collapsed every comment onto its parent post's URI. Comments now canonicalize to `/r/<sub>/comments/<post_id>/_/<comment_id>` so dedup preserves every comment row.
- **Dashboard**: new [`RedditPanel`](ops/dashboard/web/src/components/panels/RedditPanel.tsx) served by [`GET /api/reddit/overview`](ops/dashboard/api/routes/reddit_routes.py) — per-subreddit coverage rollup, PRAW account health, warning banner when PRAW-capable accounts drop to 0.
- **Parity test** at [`test_reddit_normalizer_parity.py`](ops/tests/test_reddit_normalizer_parity.py) — imports the real SN13 `RedditContent` + `validate_reddit_content`, asserts byte-exact match and `is_valid=True`. Gated on `bittensor`/`torch` so CI stays lean.
- **Ops docs**: [`docs/ops_reddit.md`](docs/ops_reddit.md).

**Test coverage.** 165 tests pass, 4 expected skips (SN13-deps-gated). Added for M7:
- 11 normalizer unit tests (post/comment/nsfw/deleted-user/required-fields/hash-stability)
- 18 PRAW plugin tests (fake asyncpraw objects, mode dispatch, credential handling, JSON-fallback routing)
- 12 JSON fallback tests (httpx MockTransport — rate-limit / auth-failed / timeout / parity)
- 4 dashboard route tests (auth gate / empty / coverage ordering / PRAW-tag counting)
- 3 SN13-parity tests (gated; exercises `validate_reddit_content`)
- 2 dedup comment-canonicalization cases

**Operational posture.** With 10 PRAW accounts × 50 req/min budget = 500 req/min — comfortably under Reddit's 60/min-per-OAuth-client ceiling × 10 clients. JSON fallback keeps the pipeline alive at a degraded rate when credentials lapse; the dashboard warning makes that visible to operators.

**Dependencies satisfied.** M3 (proxy pool), M4 (account pool with credentials support), M9 protocol (normalizer contract). M5's browser runtime is passed through but unused on this path — the PRAW client bypasses Playwright entirely.

**Acceptance criteria — revised from original.**
- [x] Parity test pinned against real SN13 `RedditContent.from_data_entity` round-trip.
- [x] Fallback verified in unit tests: credentials present → PRAW; no credentials but proxy → JSON.
- [x] NSFW+media combo filtered out before staging.
- [x] Per-subreddit dashboard panel with PRAW-health indicator.
- [ ] 50k rows/day smoke — deferred to live-fleet bring-up (post-M10).
- ~~Playwright fallback~~ — dropped. PRAW + JSON covers every realistic failure mode; Playwright adds cost + flakiness for scenarios we haven't actually seen.

---

## M8 — YouTube scraper — 🚫 CANCELLED (2026-04-16)

Not building. The validator no longer scores YouTube data.

**Evidence** (all as of the `main` branch on 2026-04-16, introduced by upstream commit [`63b31ea`](https://github.com/macrocosm-os/data-universe/commit/63b31ea) on 2026-01-08, "remove YT source"):
- `common/data.py`: `YOUTUBE = 3` renamed to `UNKNOWN_3` with weight **0** (was 0.04 / ~4%). Reddit absorbed the freed weight → **Reddit 0.65**, X **0.35**.
- `scraping/provider.py`: all 6 YouTube scraper factories removed (`YOUTUBE_CUSTOM_TRANSCRIPT`, `YOUTUBE_APIFY_*`, `YOUTUBE_CRAWLMASTER_*`). Validator has no way to spot-check a YouTube row.
- `vali_utils/s3_utils.py`: no `EXPECTED_COLUMNS_YOUTUBE`. Any YouTube parquet fails the post-2026-04-14 strict-schema gate outright → all pending entries penalised per [`ba9a6cb`](https://github.com/macrocosm-os/data-universe/commit/ba9a6cb).
- `vali_utils/miner_evaluator.py` `PREFERRED_SCRAPERS`: only `X → X_APIDOJO` and `REDDIT → REDDIT_MC`. OD validation for YouTube can't resolve a scraper → 503.
- `dynamic_desirability/default.json`: 10 Reddit + 14 X jobs, **0** YouTube.
- `rewards/data.py`: unknown sources return `default_scale_factor = 0.0` → YouTube rows score zero even if the DD list suddenly included them.

**Implication.** Any YouTube data we upload contributes zero to P2P / S3 / OD and bloats our parquets (risking schema-match failures for unrelated rows). The existing `YouTube.custom.transcript` entry in the miner's `scraping/config/scraping_config.json` is dead weight — we strip it in M11 when we wire the uploader.

**Signals that would flip this back on** — watch for any one of:
- A new `YOUTUBE = N` (or `UNKNOWN_3` → `YOUTUBE` rename) in `common/data.py`.
- Entries appearing in `scraping/provider.py::DEFAULT_FACTORIES` for a YouTube scraper.
- `EXPECTED_COLUMNS_YOUTUBE` landing in `vali_utils/s3_utils.py`.
- YouTube jobs showing up in `dynamic_desirability/default.json` or in the live DD list returned by `DataUniverseApiClient.validator_get_latest_dd_list()`.
- A `STATE_VERSION` bump alongside YouTube-related churn.

**If re-enabled**, roughly a week's work lands here: Playwright plugin for `yt-dlp`-style metadata + transcript retrieval, a `YouTubeContent` normalizer matching whatever validator shape they ship, a new entry in `ops/worker/plugins/__init__.py`, a frontend tile. The proxy + account pools + pipeline + bridge all already support arbitrary sources — the plugin drop-in is the only net-new code.

---

# Phase 3 — Correctness (summary; to be expanded when we reach it)

## M9 — Normalizer & schema contract
Promote the per-scraper normalizers of M6/M7 into a single `ops/normalizer/` module with property-based tests that assert: any NormalizedContent we emit, when passed back through the corresponding SN13 `Scraper.validate()`, returns `is_valid=True`. This is the one-way gate that keeps credibility high.

## M10 — Self-validation shim
Scheduled job: every 5 min, sample 1-2% of the last hour's stored rows, re-scrape them via the **validator's** scraper (`X_APIDOJO` for X rows, `REDDIT_MC` for Reddit rows — per `PREFERRED_SCRAPERS` in `vali_utils/miner_evaluator.py`), diff fields using the tolerance map from M6, drop diverging rows from the store. Surface `self_validation_pass_ratio` per source on the dashboard; alert <98% (upstream `STARTING_S3_CREDIBILITY` dropped to 0.1, so failures hurt more).

**Tightened 2026-04-15 (upstream [ba9a6cb](https://github.com/macrocosm-os/data-universe/commit/ba9a6cb), [773ca24](https://github.com/macrocosm-os/data-universe/commit/773ca24)):**
- Validators now check up to **50 files** including OLD ones, with the scraper
  window at 96 h and big-file priority (> 1 MB files get half the sample
  slots). A row that was valid at publish time but has since been deleted
  upstream counts as fabricated — we can no longer rely on "published clean
  = stays clean."
- Since `STARTING_S3_CREDIBILITY` dropped 0.375 → 0.1, we cannot absorb
  validation failures early. Bump sampling rate from 1 % → **2 %** for
  the first 30 d of any new deployment until S3 cred settles.
- Add a second-pass re-validator on a 6-hour cadence that scrubs rows *already
  in* `SqliteMinerStorage` whose `datetime` is 24-72 h old: if the upstream
  source now returns 404 / deleted / field-mismatch, proactively delete the
  row before validators notice. Ledger the removals in `stg_promotion_log`
  with `deleted_at` set so we can audit.
- Alert threshold raised: fire on `self_validation_pass_ratio < 98 %` (was
  95 %) because even one strict-schema fail poisons the whole file.

---

# Phase 4 — Integration (summary)

## M11 — Storage & S3 upload wiring
Adapter that writes NormalizedContent into the existing `SqliteMinerStorage`, preserving the obfuscation/encoding path. Parquet roll-up for S3 upload strictly matching the filename format `data_YYYYMMDD_HHMMSS_count_16hex.parquet` enforced by validators after 2025-12-02 ([docs/s3_validation.md](docs/s3_validation.md)).

**Tightened 2026-04-15 (upstream [ba9a6cb](https://github.com/macrocosm-os/data-universe/commit/ba9a6cb)):**
- **Exact column match.** The Parquet schema must contain every required column
  the validator expects (per-source XContent/RedditContent/YouTube). Missing
  ONE column → whole file rejected → miner marked fabricating. Add a pre-upload
  guard: load the Parquet back with `pyarrow`, diff schema against the
  canonical column set in [scraping/x/apidojo_scraper.py](scraping/x/apidojo_scraper.py)
  et al., abort upload on mismatch. Cover with a CI test.
- **No big-file-hiding.** Since validators reserve half their sample for
  > 1 MB files, we can't dilute bad rows into large parquets. Keep rollup
  size modest (target ≤ 10 MB / file) so an individual bad row doesn't
  poison a whole 1 GB parquet.
- **Old files count.** Validators scrape-check files across the full 96-h
  window — not just fresh. M10's retrospective re-validator (above) partners
  with this milestone: uploader must expose a **delete-and-reupload** path
  that M10 can trigger when it finds stale rows already in S3.
- Emit a new metric `s3_upload_schema_failures_total{source}` so a sudden
  spike (schema drift upstream) surfaces on the dashboard immediately.

## M12 — On-Demand Fast Lane
Replace `handle_on_demand` in [neurons/miner.py](neurons/miner.py) with: (1) local DB query, (2) priority-queued scrape for missing slice via the same fleet, (3) streaming response. Target OD p95 <10s. This is the single biggest lever on emissions per [vision.md §1.1](vision.md#11-three-parallel-reward-streams).

**Tightened 2026-04-15 (upstream [cc6fb94](https://github.com/macrocosm-os/data-universe/commit/cc6fb94)):**

Every OD submission is now validated in 4 phases **before** any reward is
paid. ANY phase fail = all pending submissions penalised; "can't validate" =
drop, no blind reward. Our response must pass all four:

1. **Schema** — `XContent.from_data_entity()` (or per-source equivalent) must
   succeed on 5 sampled entities. → The bridge adapter already guards this,
   but the OD fast-lane path bypasses staging; add the same pre-serialisation
   guard inline in `handle_on_demand`.
2. **Job match** — every row's `username` / `keyword` / `datetime` must
   match the request's filter. → Re-check in OD fast lane after local DB
   query AND after gap-fill scrape; drop any row that doesn't match rather
   than shipping it.
3. **Scraper validation** — validators re-scrape 1 live entity via the
   validator's scraper. → Self-validation shim (M10) reduces but doesn't
   eliminate this risk; ensure the fast-lane rows are the *freshest* in our
   store so 404 / deletion risk is minimised. Prefer rows < 6 h old over
   older ones when both satisfy the filter.
4. **Empty submission probe** — if we return 0 rows, validator spot-checks
   whether data *exists* upstream and penalises us if it does. → Never
   short-circuit to `items=[]` unless we've done a gap-fill scrape attempt
   and it genuinely returned nothing.

Acceptance now also includes: zero false-empty submissions under a synthetic
test where a known-good label is requested and data exists. Poller window
upstream is 90 m → 3 h; our cache TTL for OD replies should stay within that.

---

# Phase 5 — Intelligence (summary)

## M13 — Gravity/DD strategist
Hourly service: poll live DD list → cross-reference with SN13 dashboard uniqueness → rewrite `scraping_config.json` with weighted label plan.

## M14 — Uniqueness oracle
Cached scraper of the public SN13 dashboard; feeds M13.

---

# Phase 6 — Hardening (summary)

## M15 — Mainnet cutover & alert delivery
Alertmanager → Discord/Telegram; on-call rotation; runbook in [docs/](docs/).

## M16 — Chaos drills
Documented failure injections: kill a worker, blackhole proxies, revoke an account, corrupt a Parquet, simulate X GraphQL schema change. Every drill has a documented expected and actual response time to recovery.

---

# Cross-cutting rules

1. **No milestone is "done" until its dashboard panel is live and populated.** This is non-negotiable; it's how we stay out of the dark.
2. **All services emit `shared.events` and `shared.metrics`.** No bespoke event schemas per milestone.
3. **Secrets only via env.** No credentials in cleartext on disk, no cookies in logs — a CI regex test enforces this for staging rows (M4).
4. **Credibility is sacred.** Any change that could cause data drift from the validator's scraper requires a parity test added to CI.
5. **Playwright-first, but not Playwright-only.** Where a cheaper free-tier path exists and is stable (PRAW for Reddit), use it as primary; Playwright is the X-primary path. (YouTube had its own free-tier lane — `youtube-transcript-api` + `yt-dlp` — but YouTube is dead per pivot #6, so that whole lane is removed.)
6. **Native Windows/Linux via pm2.** All services run as native Python processes; Docker was dropped in the pivot at M2 in favour of a simpler single-host layout.
7. **Watch upstream `STATE_VERSION` bumps AND scored-source changes.** Two signals:
   - `rewards/miner_scorer.py:STATE_VERSION` bumps mean validators reset one or more of (P2P cred, S3 cred + boosts, OD cred + scores). Dashboard must surface the current `STATE_VERSION` we're tracking so we notice immediately after a `git pull`. The last bump (v7 → v8 on 2026-04-14) reset **S3** and dropped `STARTING_S3_CREDIBILITY` 0.375 → 0.1 — any runbook referencing the old number is stale.
   - `common/data.py::DataSource` and `scraping/provider.py::DEFAULT_FACTORIES` define the set of scored sources. YouTube was removed 2026-01-08 ([`63b31ea`](https://github.com/macrocosm-os/data-universe/commit/63b31ea)); we skipped building M8 on that basis. If a source ever reappears — new enum entry, new factory, new `EXPECTED_COLUMNS_*`, DD jobs in `default.json` — we light up a plugin under `ops/worker/plugins/<source>.py` within a week. Current scored set: **Reddit (0.65), X (0.35)**.

---

# Definition of Done for "Phase 0–2"

When M0 through M7 are accepted (M8 is cancelled — see pivot #6), we have:

- A web dashboard we can open to see every running worker, proxy, account, task, and error in real time.
- A fleet that can sustain ~80k X rows/day + ~180k Reddit rows/day on ~$160/mo of proxies. (Reddit jumped to 65% of the reward pie when YouTube was removed, so we budget more PRAW throughput here than the original plan.)
- Fixture-tested parsers for **both** scored sources (X, Reddit) with golden-set parity ≥99% vs the validator's scrapers.
- A web dashboard with Prometheus metric endpoints on every service; alerts are wired to Discord/Telegram in M15.
- Zero production miner integration yet — that's Phase 4. We're safe to iterate without risking an already-mining hotkey.

That is the right stopping point to pause, review, and then tackle Phase 3–4 integration with the existing miner.
