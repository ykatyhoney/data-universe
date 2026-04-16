"""Redis Streams helpers — typed producer / consumer-group pumps.

One stream per logical queue (see :class:`common.pipeline.StreamName`).
Consumers identify themselves with a stable ``consumer_name``; Redis routes
each message to exactly one consumer in the group, so N worker processes in
the same group coordinate naturally via XREADGROUP.

Lifecycle:
  1. ``ensure_group(stream, group)`` — call on service start (idempotent).
  2. ``publish(stream, envelope)`` — producer emits an event (XADD).
  3. ``consume(stream, group, consumer_name)`` — async-iterator loop:
       • XREADGROUP blocks up to a timeout
       • yields (message_id, envelope)
       • caller MUST ``ack(stream, group, message_id)`` after durable persist
       • unacked messages resurface via XAUTOCLAIM on the next consumer start
         so a crashed worker does not lose its in-flight batch.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TypeVar

from shared.infra import get_redis
from shared.logging import get_logger
from shared.pipeline import StreamName, envelope_class
from shared.pipeline import _Envelope as Envelope

log = get_logger(__name__)

_STREAM_MAXLEN: dict[StreamName, int] = {
    StreamName.SCRAPE_TASKS: 100_000,
    StreamName.SCRAPE_RESULTS: 50_000,
    StreamName.VALIDATION_QUEUE: 10_000,
    StreamName.ONDEMAND_REQUESTS: 5_000,
}

T = TypeVar("T", bound=Envelope)


async def ensure_group(stream: StreamName, group: str) -> None:
    """Create a consumer group on ``stream`` if it doesn't already exist.

    Safe to call on every service start — the BUSYGROUP error is swallowed.
    Creates the stream as a side-effect (``MKSTREAM``) so callers don't need
    to pre-seed it.
    """
    r = get_redis()
    try:
        await r.xgroup_create(name=str(stream), groupname=group, id="$", mkstream=True)
        log.info("stream.group_created", stream=str(stream), group=group)
    except Exception as e:
        if "BUSYGROUP" in str(e):
            return
        raise


async def publish(stream: StreamName, envelope: Envelope) -> str:
    """XADD an envelope onto ``stream``. Returns the Redis message ID.

    Applies an approximate MAXLEN trim so streams can't grow unbounded.
    """
    r = get_redis()
    maxlen = _STREAM_MAXLEN.get(stream, 50_000)
    msg_id = await r.xadd(
        name=str(stream),
        fields={"payload": envelope.model_dump_json()},
        maxlen=maxlen,
        approximate=True,
    )
    return str(msg_id)


async def consume(
    stream: StreamName,
    group: str,
    consumer_name: str,
    block_ms: int = 1_000,
    count: int = 10,
) -> AsyncIterator[tuple[str, Envelope]]:
    """Async-iterate over messages from ``stream``, one consumer in ``group``.

    Starts by replaying any messages that this consumer previously took but
    never ACK'd (XREADGROUP with id=``0``), then switches to the live tail
    (id=``>``). Pending-claim from OTHER consumers isn't done here — use
    :func:`claim_stale` from a supervisor if you need that.

    Caller is responsible for calling :func:`ack` once the message is
    durably persisted (staging table insert).
    """
    r = get_redis()
    klass = envelope_class(stream)
    last_id = "0"  # first replay pending, then switch to live tail

    while True:
        try:
            resp = await r.xreadgroup(
                groupname=group,
                consumername=consumer_name,
                streams={str(stream): last_id},
                count=count,
                block=block_ms,
            )
        except Exception as e:
            log.warning("stream.xreadgroup_failed", stream=str(stream), group=group, error=str(e))
            last_id = "0"
            continue

        if not resp:
            # Drained replay; switch to live tail.
            if last_id != ">":
                last_id = ">"
            continue

        # resp = [(stream_name, [(msg_id, {"payload": "<json>"}), ...])]
        for _sname, messages in resp:
            if not messages:
                # Finished replaying pending for this stream → live tail.
                last_id = ">"
                continue
            for msg_id, fields in messages:
                payload = fields.get("payload")
                if not payload:
                    log.warning("stream.malformed_message", stream=str(stream), msg_id=msg_id)
                    await ack(stream, group, msg_id)
                    continue
                try:
                    envelope = klass.model_validate_json(payload)
                except Exception as e:
                    log.warning(
                        "stream.decode_failed",
                        stream=str(stream),
                        msg_id=msg_id,
                        error=str(e),
                    )
                    await ack(stream, group, msg_id)  # dead-letter it — data is unrecoverable
                    continue
                yield msg_id, envelope


async def ack(stream: StreamName, group: str, msg_id: str) -> None:
    """Mark a message as processed. MUST be called after the work completes."""
    await get_redis().xack(str(stream), group, msg_id)  # type: ignore[no-untyped-call]


async def pending_count(stream: StreamName, group: str) -> int:
    """Number of in-flight messages (XREADGROUP'd but not ACK'd) in ``group``."""
    try:
        pending = await get_redis().xpending(str(stream), group)  # type: ignore[no-untyped-call]
    except Exception:
        return 0
    if isinstance(pending, dict):
        return int(pending.get("pending", 0) or 0)
    return 0


async def stream_length(stream: StreamName) -> int:
    try:
        return int(await get_redis().xlen(str(stream)))
    except Exception:
        return 0
