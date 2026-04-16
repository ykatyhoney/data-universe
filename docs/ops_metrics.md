# Metric contract

Canonical Prometheus metric names exposed by every ops service. Owned by
[ops/shared/metrics.py](../ops/shared/metrics.py). Downstream milestones import
these — they do **not** redefine metrics with different names. Renaming a
metric is a breaking change; it must be coordinated with the dashboard
frontend (where names appear in [ThroughputPanel.tsx](../ops/dashboard/web/src/components/panels/ThroughputPanel.tsx))
and any external alert rules.

## Pipeline

Every ops service is a Python process that imports `shared.metrics` and thus
exposes `/metrics` on its HTTP port (dashboard-api `:8000`, workers on
allocated ports as they come online). The **metric poller** inside
dashboard-api (see [metric_poller.py](../ops/dashboard/api/metric_poller.py))
scrapes each URL in `OPS_METRIC_TARGETS` every `OPS_METRIC_POLL_SECONDS` and
writes one row per `(metric, labels, value)` into the `metrics_snapshots`
table in the SQLite ops DB. The retention sweeper prunes rows older than
`OPS_METRIC_RETENTION_DAYS` hourly.

## Metrics

### Scrape pipeline
| Name | Type | Labels | Introduced | Notes |
|---|---|---|---|---|
| `scrape_tasks_total` | Counter | `source`, `label`, `outcome` | M0 | canonical scrape-task outcome counter |
| `scrape_task_duration_seconds` | Histogram | `source` | M0 | buckets: 0.1, 0.5, 1, 2, 5, 10, 30, 60, 120, 300 |
| `scrape_items_total` | Counter | `source`, `label` | M0 | rows produced post-normalization |

### Proxy pool (M3)
| Name | Type | Labels | Notes |
|---|---|---|---|
| `proxy_requests_total` | Counter | `proxy_id`, `outcome` | ok / 429 / block / timeout |
| `proxy_pool_size` | Gauge | `state` | healthy / cooling / quarantined / disabled |

### Account pool (M4)
| Name | Type | Labels | Notes |
|---|---|---|---|
| `account_state` | Gauge | `account_id`, `source`, `state` | 1 when account is in labelled state |
| `account_rate_budget_remaining` | Gauge | `account_id`, `source` | requests remaining in current window |

### Worker fleet (M5)
| Name | Type | Labels | Notes |
|---|---|---|---|
| `worker_heartbeat_age_seconds` | Gauge | `worker_id` | stale if > 30s |
| `worker_busy` | Gauge | `worker_id` | 1 while executing a task |

### On-demand (M12)
| Name | Type | Labels | Notes |
|---|---|---|---|
| `ondemand_request_duration_seconds` | Histogram | — | buckets: 0.5, 1, 2, 5, 10, 15, 30, 60, 120 |
| `ondemand_requests_total` | Counter | `outcome` | |

### Self-validation (M10)
| Name | Type | Labels | Notes |
|---|---|---|---|
| `self_validation_pass_ratio` | Gauge | `source` | alert < 0.95 |

### Storage pipeline (M2.5)
| Name | Type | Labels | Notes |
|---|---|---|---|
| `staging_rows` | Gauge | `source`, `state` | pending / validating / promoted / quarantined |
| `stream_lag_messages` | Gauge | `stream`, `group` | Redis consumer-group backlog |

## Cardinality guardrail

The poller caps series count per metric name at
`OPS_METRIC_MAX_SERIES_PER_METRIC` (default **50**). When a metric exceeds the
cap in one tick, extra series are dropped and a `metric_poller.cardinality_capped`
warning is logged. High-cardinality labels (per-UUID proxy_id,
per-account_id) should bucket under `"_other"` in the producing service, not
rely on the cap.

## Adding a new metric

1. Add the name and labels to [ops/shared/metrics.py](../ops/shared/metrics.py).
2. Update the table above.
3. If the dashboard should chart it, add the name to the `DISPLAY` list in
   [ThroughputPanel.tsx](../ops/dashboard/web/src/components/panels/ThroughputPanel.tsx)
   or another panel, and extend [DEFAULT_SUMMARY_METRICS](../ops/dashboard/api/routes/metrics_routes.py)
   so `/api/metrics/summary` returns it.
4. Add a row to [tests/test_smoke.py](../ops/tests/test_smoke.py)
   (`test_metric_contract_registered`) so renames break CI.

## Environment knobs

| Env var | Default | Purpose |
|---|---|---|
| `OPS_METRIC_TARGETS` | `http://localhost:8000/metrics` | comma-separated URLs to poll |
| `OPS_METRIC_POLL_SECONDS` | `15` | tick interval |
| `OPS_METRIC_RETENTION_DAYS` | `30` | `metrics_snapshots` retention window |
| `OPS_METRIC_MAX_SERIES_PER_METRIC` | `50` | cardinality cap per tick |

## Why SQLite for time-series?

A single-host miner rig writes ≲ 300 k metric rows/day at the default 15s
tick. SQLite in WAL mode handles that comfortably with the `ix_metrics_metric_ts`
composite index. Backups are a file copy (`.backup` or `VACUUM INTO`). If/when
we move multi-host, we'll swap the poller's sink for a real TSDB — the metric
*names* (which the frontend and alerts key off) remain stable.
