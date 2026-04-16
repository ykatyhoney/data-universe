# Proxy pool (M3)

Supervised pool of residential proxies with health probes, sticky sessions,
and graceful rotation. Every scraper leases from here; nothing picks raw
endpoints.

## Quick start

```bash
# 1. Configure endpoints (residential-style URLs)
export OPS_PROXY_STATIC_ENDPOINTS="http://user:pass@gate1.provider:8080,http://user:pass@gate2.provider:8080"
export OPS_PROXY_STATIC_SUPPORTS_STICKY=true   # enable session injection

# 2. Boot dashboard-api — lifespan syncs the pool + starts the prober
make start

# 3. Open the dashboard; the "Proxy pool" panel lights up
```

Leased URLs go to workers (M5+); the scraper never handles raw endpoints.

## Environment variables

| Env var | Default | Purpose |
|---|---|---|
| `OPS_PROXY_STATIC_ENDPOINTS` | `""` | comma-separated proxy URLs (`http://user:pass@host:port`) |
| `OPS_PROXY_STATIC_SUPPORTS_STICKY` | `false` | set `true` if your provider supports `user-session-<id>` |

## REST surface

All cookie-gated (see [ops_local_dev.md](ops_local_dev.md)):

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/proxy-pool/lease` | worker asks for a proxy; returns `{ lease_id, proxy_id, url, session_id, expires_at }` |
| `POST` | `/api/proxy-pool/release` | worker reports outcome (`ok` / `rate_limited` / `blocked` / `timeout` / `error`) |
| `GET`  | `/api/proxy-pool/state` | dashboard overview (all proxies + counts by state) |
| `POST` | `/api/proxy-pool/admin/sync` | force re-load from backends |
| `POST` | `/api/proxy-pool/admin/{id}/disable` | take a proxy out of rotation |
| `POST` | `/api/proxy-pool/admin/{id}/enable` | put it back |

## State machine

```
    healthy ──(3 fails)──► quarantined ──(probe ok)──► healthy
       │                         │
       │                         └──(probe fails)──► quarantined (cooldown x2, capped 60m)
       │
       └──(1-2 fails)──► cooling ──(ok)──► healthy
                              │
                              └──(3rd fail)──► quarantined

    disabled ← admin toggle → healthy
```

- **Quarantine:** starts at **10 min**, doubles on each re-probe failure,
  capped at **60 min**. Reset to zero on successful probe.
- **Cooling:** transient state between `healthy` and `quarantined`; doesn't
  remove the proxy from the lease pool — the caller (who saw the failure) may
  retry with a different proxy, but a later OK resets the streak.
- **Disabled:** explicit admin action; proxy is ignored by both lease and
  prober until re-enabled.

## Stickiness

Residential providers expose a per-session IP by appending a token to the
username (`user-session-XYZ`). When the caller passes `sticky_minutes > 0`
and a `session_id` (plus an `account_id`), the pool:

1. Looks up Redis key `proxy:sticky:{account_id}:{session_id}` with TTL.
2. If mapped → returns the pinned proxy; else picks a fresh one and writes
   the mapping.
3. Injects `-session-<session_id>` into the URL's username if the backend
   supports it; otherwise returns the raw URL (binding still pins exit IP).

## Health prober

Runs in-process alongside dashboard-api. Every 2 minutes:

1. Iterates non-disabled proxies; skips `quarantined` whose cooldown hasn't
   elapsed.
2. Sends `HEAD https://www.gstatic.com/generate_204` through the proxy
   with a 5 s timeout.
3. Applies the state machine above.

The probe URL is deliberately neutral — we don't hit target sites (no
signal to the data source about our proxy set).

## Adding a new backend

1. `ops/proxy_pool/backends/<name>.py` — implement
   :class:`ProxyBackendAdapter` (two methods: `load_endpoints`,
   `inject_session`).
2. Register its enum variant in `ops/proxy_pool/schemas.py::ProxyBackend`.
3. Add an entry to the lifespan wiring in
   [ops/dashboard/api/main.py](../ops/dashboard/api/main.py) so the new
   backend is constructed alongside `StaticListBackend`.
4. Ship a unit test against a mocked provider response.

## Scaling beyond one process

The service is in-process today for simplicity. When scraper workers move
to separate hosts, split it out:

1. Uncomment the `proxy-pool` app in [ecosystem.config.js](../ecosystem.config.js).
2. Configure the worker host's `OPS_REDIS_URL` to point at the proxy-pool
   host's Redis (SQLite is already accessible via the same path on a shared
   volume, or migrate to Postgres per the pivot note in M2).
3. Worker code calls `POST /api/proxy-pool/lease` on the proxy-pool host
   instead of using the in-process singleton.

No code change to the service itself — it's already a network service
today, just co-located with dashboard-api.
