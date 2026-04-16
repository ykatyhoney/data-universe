# ops/ — Data Universe miner operational stack

Everything built per [milestones.md](../milestones.md) lands here. Existing
SN13 code under `neurons/`, `scraping/`, `storage/` (at repo root), `rewards/`
is intentionally untouched until Phase 4 (M11+).

Runtime: native Python processes managed by pm2 (see [ecosystem.config.js](../ecosystem.config.js)).
State: **SQLite** (ops + staging tables in `ops.db`) + **Redis** (streams + pub/sub),
installed natively on the host. No Docker, no PostgreSQL.

> **Naming note.** Our packages are `shared/` and `datastore/` — NOT `common/`
> or `storage/` — because SN13 already owns those names at the repo root
> (`common/` = SN13 shared utilities, `storage/miner/` = SN13's miner DB).
> Python only gets one `common` and one `storage` per process, so we named
> around the collision.

## Layout (grows per milestone)

| Path | Introduced in | Purpose |
|---|---|---|
| `shared/config.py` | M0 | env-driven settings (`OPS_*` prefix) |
| `shared/clock.py` | M0 | freezable time for tests |
| `shared/schemas.py` | M0 | wire/event pydantic models (frozen) |
| `shared/logging.py` | M0 | structlog → JSON → stdout |
| `shared/metrics.py` | M0 | canonical Prometheus metric contract |
| `shared/events.py` | M1 | live-dashboard event bus (discriminated union) |
| `shared/pipeline.py` | M2.5 | Redis Streams envelope schemas + stream/group names |
| `shared/infra.py` | M0 | SQLite engine + Redis client (singletons per process) |
| `dashboard/api/` | M0 → M2 | FastAPI: REST + `/ws/live` + `/metrics` + metric poller + retention |
| `dashboard/web/` | M0 → M1 | Vite + React + Tailwind SPA (built into `dist/`) |
| `datastore/models.py` | M1/M2.5 | SQLAlchemy ORM (`ops.*` + `stg_*` tables) |
| `datastore/repositories.py` | M1/M2.5 | repos, incl. dedup upsert + state-machine claim |
| `datastore/dedup.py` | M2.5 | per-source URI canonicalisation + content hash |
| `datastore/streams.py` | M2.5 | Redis Streams publish / consume / ack helpers |
| `datastore/sqlite_adapter.py` | M2.5 | bridge from `stg_*` into SN13 SqliteMinerStorage |
| `datastore/migrations/` | M1/M2.5 | Alembic |
| `pipeline/orchestrator.py` | M2.5 | ingest / validate / promote / metrics loops |
| `normalizer/` | M2.5 → M9 | protocol + Passthrough stub today; real X/Reddit/YT in M9 |
| `self_validator/` | M2.5 → M10 | protocol + AlwaysPass stub today; real resampler in M10 |
| `scripts/` | M0 | preflight + init-db (alembic upgrade head) |
| `proxy_pool/` | M3 | residential proxy rotation service |
| `account_pool/` | M4 | cookie-account pool (X/Reddit) |
| `worker/` | M5 | Playwright worker framework |
| `scrapers/{x,reddit,youtube}/` | M6/M7/M8 | per-source scraper plugins |
| `strategist/` | M13 | DD-aware label planner |

## Scaling

Each Phase-1+ service is a separate pm2 app in `ecosystem.config.js`. Workers
scale horizontally via `pm2 scale worker-x <N>` — they consume from the same
Redis Streams consumer group, so N workers coordinate naturally. When running
more than ~20 total workers, raise `OPS_REDIS_MAX_CONNECTIONS` in the
ecosystem file. SQLite writes funnel through the bridge promoter with bounded
batching (default 200 rows per tick).

See [../docs/ops_local_dev.md](../docs/ops_local_dev.md) to boot the stack.
