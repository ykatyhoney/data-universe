"""Pipeline orchestrator — the seam between Redis Streams and durable state.

One coroutine per stage; ``run()`` schedules them and returns when cancelled.

Stages:
    1. ``ingest_loop``    : consume ``scrape:results`` → normalize → stage
                            (stg_raw_items + stg_dedup_index + stg_normalized_items)
                            → enqueue ``validation:queue``
    2. ``validate_loop``  : consume ``validation:queue`` → run SelfValidator
                            → write stg_validation_results → flip normalized
                            row to ``validating`` (pass) or ``quarantined`` (fail)
    3. ``promote_loop``   : poll for ``state=validating`` rows → bridge into
                            SqliteMinerStorage (M11 wires this against the
                            real miner storage; M2.5 ships the adapter shape)

Workers / scrapers don't talk to staging directly — they emit ``scrape:results``
envelopes and forget. Everything past that is the orchestrator's job.
"""

from __future__ import annotations

import asyncio
import contextlib

from datastore.dedup import CanonicalizationError
from datastore.repositories import (
    StgDedupRepo,
    StgNormalizedItemRepo,
    StgRawItemRepo,
    StgValidationRepo,
)
from datastore.sqlite_adapter import BridgePromoter
from datastore.streams import (
    ack,
    consume,
    ensure_group,
    pending_count,
    publish,
    stream_length,
)
from normalizer.base import Normalizer
from self_validator.base import SelfValidator
from shared.clock import now_utc
from shared.infra import get_session_factory
from shared.logging import get_logger
from shared.metrics import staging_rows, stream_lag_messages
from shared.pipeline import (
    ConsumerGroup,
    ScrapeResultEnvelope,
    StreamName,
    ValidationEnvelope,
)

log = get_logger(__name__)


class PipelineOrchestrator:
    """Schedules ingest / validate / promote loops + a metrics tick.

    Workers/scrapers (M5+) emit ``scrape:results``; this class is the only
    consumer of that stream and is the only writer to staging tables.
    """

    def __init__(
        self,
        *,
        normalizers: dict[str, Normalizer],
        validator: SelfValidator,
        promoter: BridgePromoter | None = None,
        consumer_name: str = "orchestrator-1",
        promote_interval_s: float = 1.0,
        metrics_interval_s: float = 5.0,
    ) -> None:
        self._normalizers = normalizers
        self._validator = validator
        self._promoter = promoter
        self._consumer_name = consumer_name
        self._promote_interval = promote_interval_s
        self._metrics_interval = metrics_interval_s

        self._tasks: list[asyncio.Task[None]] = []
        self._stopping: asyncio.Event | None = None

    async def start(self) -> None:
        if self._tasks:
            return
        self._stopping = asyncio.Event()
        await ensure_group(StreamName.SCRAPE_RESULTS, ConsumerGroup.NORMALIZER)
        await ensure_group(StreamName.VALIDATION_QUEUE, ConsumerGroup.SELF_VALIDATOR)
        self._tasks = [
            asyncio.create_task(self._ingest_loop(), name="pipeline.ingest"),
            asyncio.create_task(self._validate_loop(), name="pipeline.validate"),
            asyncio.create_task(self._promote_loop(), name="pipeline.promote"),
            asyncio.create_task(self._metrics_loop(), name="pipeline.metrics"),
        ]
        log.info("pipeline.start", consumer_name=self._consumer_name)

    async def stop(self) -> None:
        if self._stopping is not None:
            self._stopping.set()
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await t
        self._tasks.clear()
        self._stopping = None
        log.info("pipeline.stop")

    # ---------- ingest ---------- #

    async def _ingest_loop(self) -> None:
        async for msg_id, env in consume(
            StreamName.SCRAPE_RESULTS,
            ConsumerGroup.NORMALIZER,
            self._consumer_name,
        ):
            assert isinstance(env, ScrapeResultEnvelope)
            try:
                await self._handle_result(env)
            except Exception as e:
                log.warning(
                    "pipeline.ingest_failed",
                    task_id=env.task_id,
                    error=str(e),
                )
            finally:
                # ACK either way; the row is recorded in stg_raw_items if we
                # got that far, and we don't want a poison pill stuck in the
                # consumer-group PEL.
                await ack(StreamName.SCRAPE_RESULTS, ConsumerGroup.NORMALIZER, msg_id)

    async def _handle_result(self, env: ScrapeResultEnvelope) -> None:
        normalizer = self._normalizers.get(env.source.value)
        if normalizer is None:
            log.warning("pipeline.no_normalizer", source=env.source.value)
            return

        validation_payloads: list[ValidationEnvelope] = []
        factory = get_session_factory()
        async with factory() as session, session.begin():
            for raw in env.items:
                # Audit trail FIRST so we can reproduce parser bugs even on
                # rows that fail normalisation downstream.
                raw_id = await StgRawItemRepo.insert(
                    session,
                    task_id=env.task_id,
                    source=env.source.value,
                    uri=str(raw.get("uri", "")),
                    raw_json=raw,
                    fetched_at=env.fetched_at,
                )
                try:
                    normalized = normalizer.normalize(raw)
                except CanonicalizationError as e:
                    log.warning("pipeline.dedup_canonicalize_failed", uri=e.uri, reason=e.reason)
                    continue
                except Exception as e:
                    log.warning("pipeline.normalize_failed", error=str(e))
                    continue

                # Dedup — ON CONFLICT DO NOTHING; ``False`` means duplicate.
                fresh = await StgDedupRepo.reserve(
                    session,
                    canonical_uri=normalized.uri,
                    content_hash=normalized.content_hash,
                    source=normalized.source.value,
                    item_datetime=normalized.item_datetime,
                )
                if not fresh:
                    continue

                norm_id = await StgNormalizedItemRepo.insert_pending(
                    session,
                    raw_id=raw_id,
                    source=normalized.source.value,
                    uri=normalized.uri,
                    content_hash=normalized.content_hash,
                    item_datetime=normalized.item_datetime,
                    label=normalized.label,
                    normalized_json=normalized.normalized_json,
                    content_size_bytes=normalized.content_size_bytes,
                )
                validation_payloads.append(
                    ValidationEnvelope(
                        normalized_item_id=norm_id,
                        source=normalized.source,
                        uri=normalized.uri,
                    )
                )

        # Publish OUTSIDE the DB transaction to avoid holding the writer lock
        # over network I/O.
        for v in validation_payloads:
            await publish(StreamName.VALIDATION_QUEUE, v)

    # ---------- validate ---------- #

    async def _validate_loop(self) -> None:
        async for msg_id, env in consume(
            StreamName.VALIDATION_QUEUE,
            ConsumerGroup.SELF_VALIDATOR,
            self._consumer_name,
        ):
            assert isinstance(env, ValidationEnvelope)
            try:
                outcome = await self._validator.validate(env)
            except Exception as e:
                log.warning(
                    "pipeline.validate_failed",
                    norm_id=env.normalized_item_id,
                    error=str(e),
                )
                # Treat orchestrator-side errors as "leave for retry".
                continue
            finally:
                await ack(StreamName.VALIDATION_QUEUE, ConsumerGroup.SELF_VALIDATOR, msg_id)

            factory = get_session_factory()
            async with factory() as session, session.begin():
                await StgValidationRepo.record(
                    session,
                    normalized_item_id=env.normalized_item_id,
                    passed=outcome.passed,
                    validator_scraper=outcome.validator_scraper,
                    field_diffs=outcome.field_diffs,
                )
                if not outcome.passed:
                    await StgNormalizedItemRepo.mark(
                        session,
                        ids=[env.normalized_item_id],
                        state="quarantined",
                        reason=f"validation:{outcome.validator_scraper}",
                    )
                # On pass: leave row at ``pending`` so the promoter claims it.

    # ---------- promote ---------- #

    async def _promote_loop(self) -> None:
        assert self._stopping is not None
        stopping = self._stopping
        if self._promoter is None:
            log.info("pipeline.promote_disabled")
            return
        while not stopping.is_set():
            try:
                factory = get_session_factory()
                async with factory() as session:
                    result = await self._promoter.promote_once(session)
                if result.attempted:
                    log.debug(
                        "pipeline.promote_tick",
                        attempted=result.attempted,
                        promoted=result.promoted,
                        failed=result.failed,
                    )
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.warning("pipeline.promote_failed", error=str(e))
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stopping.wait(), timeout=self._promote_interval)

    # ---------- metrics ---------- #

    async def _metrics_loop(self) -> None:
        assert self._stopping is not None
        stopping = self._stopping
        while not stopping.is_set():
            try:
                # Stream backlogs.
                for stream, group in (
                    (StreamName.SCRAPE_RESULTS, ConsumerGroup.NORMALIZER),
                    (StreamName.VALIDATION_QUEUE, ConsumerGroup.SELF_VALIDATOR),
                ):
                    pending = await pending_count(stream, group)
                    stream_lag_messages.labels(stream=str(stream), group=group).set(pending)
                    _ = await stream_length(stream)  # cardinality budget reserved
                # Staging counts.
                factory = get_session_factory()
                async with factory() as session:
                    counts = await StgNormalizedItemRepo.counts_by_state(session)
                # Reset all known states so dropped-to-zero shows correctly.
                for state in ("pending", "validating", "promoted", "quarantined", "dropped"):
                    staging_rows.labels(source="all", state=state).set(counts.get(state, 0))
                _ = now_utc()  # touch clock so tests can freeze it
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.warning("pipeline.metrics_failed", error=str(e))
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stopping.wait(), timeout=self._metrics_interval)
