"""Canonical Prometheus metric contract.

Every ops service imports from this module — metric names and labels are a
cross-service contract. Adding a metric here is fine; renaming one is a
breaking change and requires updating every consumer + the dashboard.

The dashboard-api polls each service's ``/metrics`` endpoint on a timer and
rolls values into Postgres ``ops.metrics_snapshots`` so the web UI can chart
them without an external Prometheus (M2).
"""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

# Shared, process-local registry. Services mount their own /metrics endpoint
# backed by this registry (see dashboard/api/main.py for the pattern).
registry = CollectorRegistry()

# ---- Scrape pipeline ---- #

scrape_tasks_total = Counter(
    "scrape_tasks_total",
    "Scrape tasks processed.",
    labelnames=("source", "label", "outcome"),
    registry=registry,
)

scrape_task_duration_seconds = Histogram(
    "scrape_task_duration_seconds",
    "Scrape task duration.",
    labelnames=("source",),
    buckets=(0.1, 0.5, 1, 2, 5, 10, 30, 60, 120, 300),
    registry=registry,
)

scrape_items_total = Counter(
    "scrape_items_total",
    "Items produced by scrapers (post-normalization).",
    labelnames=("source", "label"),
    registry=registry,
)

# ---- Proxy pool (M3) ---- #

proxy_requests_total = Counter(
    "proxy_requests_total",
    "Proxy lease outcomes.",
    labelnames=("proxy_id", "outcome"),
    registry=registry,
)

proxy_pool_size = Gauge(
    "proxy_pool_size",
    "Proxies in the pool by state.",
    labelnames=("state",),
    registry=registry,
)

# ---- Account pool (M4) ---- #

account_state = Gauge(
    "account_state",
    "1 where account is in the labelled state, 0 otherwise.",
    labelnames=("account_id", "source", "state"),
    registry=registry,
)

account_rate_budget_remaining = Gauge(
    "account_rate_budget_remaining",
    "Remaining requests-per-minute budget per account.",
    labelnames=("account_id", "source"),
    registry=registry,
)

# ---- Worker fleet (M5) ---- #

worker_heartbeat_age_seconds = Gauge(
    "worker_heartbeat_age_seconds",
    "Age of the last heartbeat received from each worker.",
    labelnames=("worker_id",),
    registry=registry,
)

worker_busy = Gauge(
    "worker_busy",
    "1 while a worker is executing a task, 0 while idle.",
    labelnames=("worker_id",),
    registry=registry,
)

worker_tasks_total = Counter(
    "worker_tasks_total",
    "Scrape tasks processed by a worker, by outcome.",
    labelnames=("worker_id", "source", "outcome"),
    registry=registry,
)

worker_task_duration_seconds = Histogram(
    "worker_task_duration_seconds",
    "Wall-clock task runtime inside the worker runtime.",
    labelnames=("source",),
    buckets=(0.5, 1, 2, 5, 10, 30, 60, 120, 300, 600),
    registry=registry,
)

worker_browser_crashes_total = Counter(
    "worker_browser_crashes_total",
    "Playwright browser/context crashes observed by the runtime.",
    labelnames=("worker_id",),
    registry=registry,
)

# ---- On-demand (M12) ---- #

ondemand_request_duration_seconds = Histogram(
    "ondemand_request_duration_seconds",
    "End-to-end latency of on-demand requests served by this miner.",
    buckets=(0.5, 1, 2, 5, 10, 15, 30, 60, 120),
    registry=registry,
)

ondemand_requests_total = Counter(
    "ondemand_requests_total",
    "On-demand requests handled, by outcome.",
    labelnames=("outcome",),
    registry=registry,
)

# ---- Self-validation (M10) ---- #

self_validation_pass_ratio = Gauge(
    "self_validation_pass_ratio",
    "Rolling pass ratio of the 1% self-validation shim.",
    labelnames=("source",),
    registry=registry,
)

# ---- Storage pipeline (M2.5) ---- #

staging_rows = Gauge(
    "staging_rows",
    "Rows currently in each staging state.",
    labelnames=("source", "state"),
    registry=registry,
)

stream_lag_messages = Gauge(
    "stream_lag_messages",
    "Redis Stream consumer-group backlog.",
    labelnames=("stream", "group"),
    registry=registry,
)
