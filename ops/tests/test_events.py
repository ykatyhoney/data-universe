"""Events module: discriminated-union encode/decode round-trip."""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from shared.events import (
    AccountStateChanged,
    MetricTick,
    ProxyStateChanged,
    TaskFinished,
    TaskStarted,
    WorkerHeartbeat,
    decode,
    encode,
)
from shared.schemas import (
    AccountState,
    ProxyState,
    ScrapeOutcome,
    ScrapeTaskMode,
    Source,
    TaskState,
    WorkerState,
)


def test_proxy_state_changed_roundtrip() -> None:
    ev = ProxyStateChanged(
        proxy_id=uuid4(),
        from_state=ProxyState.HEALTHY,
        to_state=ProxyState.COOLING,
        reason="429",
    )
    decoded = decode(encode(ev))
    assert decoded == ev
    assert decoded.kind == "proxy.state_changed"


def test_account_state_changed_roundtrip() -> None:
    ev = AccountStateChanged(
        account_id=uuid4(),
        source=Source.X,
        from_state=AccountState.ACTIVE,
        to_state=AccountState.QUARANTINED,
        reason="401",
    )
    assert decode(encode(ev)) == ev


def test_worker_heartbeat_roundtrip() -> None:
    ev = WorkerHeartbeat(
        worker_id="worker-x-3",
        host="lab",
        state=WorkerState.BUSY,
        browser_context_count=2,
        memory_mb=312.5,
    )
    assert decode(encode(ev)) == ev


def test_task_start_finish_roundtrip() -> None:
    task_id = uuid4()
    start = TaskStarted(
        task_id=task_id,
        source=Source.REDDIT,
        mode=ScrapeTaskMode.SEARCH,
        label="r/Bitcoin",
        worker_id="worker-reddit-1",
    )
    finish = TaskFinished(
        task_id=task_id,
        source=Source.REDDIT,
        outcome=ScrapeOutcome.OK,
        state=TaskState.SUCCEEDED,
        item_count=42,
        duration_seconds=7.1,
    )
    assert decode(encode(start)) == start
    assert decode(encode(finish)) == finish


def test_metric_tick_roundtrip() -> None:
    ev = MetricTick(
        metric="scrape_items_total",
        labels={"source": "x", "label": "#bitcoin"},
        value=1234.0,
    )
    assert decode(encode(ev)) == ev


def test_decode_rejects_unknown_kind() -> None:
    payload = '{"kind": "unknown.bogus", "ts": "2025-01-01T00:00:00+00:00"}'
    with pytest.raises(ValidationError):
        decode(payload)


def test_decode_dispatches_on_kind() -> None:
    """Same JSON shape should land in the right class based on `kind`."""
    wh = WorkerHeartbeat(worker_id="w1", host="h", state=WorkerState.IDLE)
    mt = MetricTick(metric="x", value=1.0)
    assert isinstance(decode(encode(wh)), WorkerHeartbeat)
    assert isinstance(decode(encode(mt)), MetricTick)
