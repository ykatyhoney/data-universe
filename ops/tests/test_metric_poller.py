"""Unit tests for the metric poller's Prometheus-format flattener and
cardinality cap. Database-backed behaviour is covered by the integration
suite when OPS_RUN_INTEGRATION=1.
"""

from __future__ import annotations

from dashboard.api.metric_poller import _flatten, _parse_targets

SAMPLE_METRICS_TEXT = """\
# HELP scrape_tasks_total Scrape tasks processed.
# TYPE scrape_tasks_total counter
scrape_tasks_total{source="x",label="#bitcoin",outcome="ok"} 42.0
scrape_tasks_total{source="reddit",label="r/Bitcoin",outcome="ok"} 17.0
# HELP scrape_task_duration_seconds Scrape task duration.
# TYPE scrape_task_duration_seconds histogram
scrape_task_duration_seconds_bucket{source="x",le="0.1"} 0.0
scrape_task_duration_seconds_bucket{source="x",le="+Inf"} 42.0
scrape_task_duration_seconds_count{source="x"} 42.0
scrape_task_duration_seconds_sum{source="x"} 12.5
# HELP self_validation_pass_ratio Rolling pass ratio of the 1% self-validation shim.
# TYPE self_validation_pass_ratio gauge
self_validation_pass_ratio{source="x"} 0.987
"""


def test_parse_targets_splits_and_trims() -> None:
    assert _parse_targets("http://a:8000/metrics, http://b:9000/metrics") == [
        "http://a:8000/metrics",
        "http://b:9000/metrics",
    ]
    assert _parse_targets("") == []
    assert _parse_targets("  , , ") == []


def test_flatten_yields_expected_triples() -> None:
    rows = list(_flatten(SAMPLE_METRICS_TEXT, cap_per_metric=100))
    names = {name for name, _labels, _v in rows}
    assert "scrape_tasks_total" in names
    # Histograms surface as _bucket / _count / _sum (we don't try to rebuild).
    assert any("_bucket" in n for n, _, _ in rows)
    assert any(n == "scrape_task_duration_seconds_count" for n, _, _ in rows)
    # self_validation_pass_ratio is a plain gauge.
    pass_rows = [r for r in rows if r[0] == "self_validation_pass_ratio"]
    assert len(pass_rows) == 1
    _, labels, value = pass_rows[0]
    assert labels == {"source": "x"}
    assert abs(value - 0.987) < 1e-9


def test_flatten_drops_inf_and_nan() -> None:
    text = """\
# HELP demo gauge
# TYPE demo gauge
demo{k="nan"} NaN
demo{k="inf"} +Inf
demo{k="ok"} 1.0
"""
    rows = list(_flatten(text, cap_per_metric=100))
    assert len(rows) == 1
    assert rows[0][1] == {"k": "ok"}


def test_flatten_caps_cardinality() -> None:
    lines = ["# TYPE demo counter"]
    for i in range(120):
        lines.append(f'demo_total{{id="x{i}"}} {i}.0')
    text = "\n".join(lines) + "\n"
    rows = list(_flatten(text, cap_per_metric=50))
    # Exactly `cap_per_metric` rows for `demo_total`; the rest are dropped.
    assert len([r for r in rows if r[0] == "demo_total"]) == 50
