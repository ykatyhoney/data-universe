"""WebSocket broadcast hub + Redis pub/sub bridge.

Design:
- One background task per dashboard-api process subscribes to Redis channel
  ``events:live`` (see ``common.events.LIVE_CHANNEL``).
- Each connected WebSocket client sits in ``_clients``; incoming Redis
  messages are fanned out with a bounded per-client queue so a slow client
  cannot stall the bridge or other clients.
- Publishers call :func:`publish` (any service) — validates against the
  discriminated union then RPUSHes into Redis pub/sub.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import WebSocket
from redis.asyncio.client import PubSub

from shared.events import LIVE_CHANNEL, AnyEvent, decode, encode
from shared.infra import get_redis
from shared.logging import get_logger

log = get_logger(__name__)

_CLIENT_QUEUE_SIZE = 256


class Client:
    __slots__ = ("alive", "q", "ws")

    def __init__(self, ws: WebSocket) -> None:
        self.ws = ws
        self.q: asyncio.Queue[str] = asyncio.Queue(maxsize=_CLIENT_QUEUE_SIZE)
        self.alive = True


class BroadcastHub:
    def __init__(self) -> None:
        self._clients: set[Client] = set()
        self._task: asyncio.Task[None] | None = None
        # Bound to the running loop in start().
        self._stopping: asyncio.Event | None = None

    async def start(self) -> None:
        if self._task is None:
            self._stopping = asyncio.Event()
            self._task = asyncio.create_task(self._redis_loop(), name="ws.redis_bridge")
            log.info("ws_hub.start")

    async def stop(self) -> None:
        if self._stopping is not None:
            self._stopping.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        # Close any remaining clients.
        for c in list(self._clients):
            c.alive = False
            with contextlib.suppress(Exception):
                await c.ws.close()
        self._clients.clear()
        self._stopping = None
        log.info("ws_hub.stop")

    async def _redis_loop(self) -> None:
        """Outer loop: reconnect to Redis forever. Inner loop: pump messages.

        Dashboard-api stays up when Redis is unreachable; events are simply not
        fanned out until the bridge reconnects.
        """
        assert self._stopping is not None
        stopping = self._stopping
        backoff = 1.0
        while not stopping.is_set():
            pubsub: PubSub | None = None
            try:
                pubsub = get_redis().pubsub()
                await pubsub.subscribe(LIVE_CHANNEL)
                log.info("ws_hub.redis_connected")
                backoff = 1.0
                while not stopping.is_set():
                    msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                    if msg is None:
                        continue
                    data = msg.get("data")
                    if not isinstance(data, str | bytes):
                        continue
                    payload = data if isinstance(data, str) else data.decode("utf-8")
                    try:
                        decode(payload)
                    except Exception as e:
                        log.warning("ws_hub.decode_failed", error=str(e))
                        continue
                    self._fanout(payload)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.warning("ws_hub.redis_error", error=str(e), backoff_s=backoff)
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(stopping.wait(), timeout=backoff)
                backoff = min(backoff * 2, 30.0)
            finally:
                if pubsub is not None:
                    with contextlib.suppress(Exception):
                        await pubsub.unsubscribe(LIVE_CHANNEL)
                        await pubsub.aclose()  # type: ignore[attr-defined]

    def _fanout(self, payload: str) -> None:
        for c in list(self._clients):
            if not c.alive:
                continue
            try:
                c.q.put_nowait(payload)
            except asyncio.QueueFull:
                # Slow consumer — mark dead; the client loop will tear down.
                c.alive = False
                log.warning("ws_hub.slow_consumer")

    @asynccontextmanager
    async def register(self, ws: WebSocket) -> AsyncIterator[Client]:
        client = Client(ws)
        self._clients.add(client)
        try:
            yield client
        finally:
            client.alive = False
            self._clients.discard(client)

    async def client_loop(self, client: Client) -> None:
        """Pump from the per-client queue into the WS until it closes."""
        try:
            while client.alive:
                payload = await client.q.get()
                await client.ws.send_text(payload)
        except Exception as e:
            log.info("ws_hub.client_disconnected", error=str(e))
        finally:
            client.alive = False


hub = BroadcastHub()


async def publish(event: AnyEvent) -> int:
    """Publish an event onto the live channel. Returns the number of Redis
    subscribers that received it (mainly for debugging).
    """
    return int(await get_redis().publish(LIVE_CHANNEL, encode(event)))
