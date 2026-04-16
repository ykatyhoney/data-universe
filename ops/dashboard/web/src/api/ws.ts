/**
 * WebSocket client with exponential-backoff reconnect.
 *
 * Subscribers register callbacks with ``on(kind, cb)``; the connection is
 * opened lazily on the first subscription and closed when the subscriber
 * count drops to zero. Disconnects and reconnects are transparent.
 */

import type { AnyEvent, EventKind } from "@/types";

type Handler = (event: AnyEvent) => void;

export type ConnectionState = "idle" | "connecting" | "open" | "closed" | "error";

type StatusListener = (state: ConnectionState) => void;

function wsUrl(): string {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${location.host}/ws/live`;
}

class LiveBus {
  private sock: WebSocket | null = null;
  private handlers: Map<EventKind | "*", Set<Handler>> = new Map();
  private statusListeners: Set<StatusListener> = new Set();
  private state: ConnectionState = "idle";
  private backoff = 1000;
  private closed = false;

  on(kind: EventKind | "*", cb: Handler): () => void {
    let bucket = this.handlers.get(kind);
    if (!bucket) {
      bucket = new Set();
      this.handlers.set(kind, bucket);
    }
    bucket.add(cb);
    this.ensureOpen();
    return () => {
      bucket.delete(cb);
      if (this.subscriberCount() === 0) this.close();
    };
  }

  onStatus(cb: StatusListener): () => void {
    this.statusListeners.add(cb);
    cb(this.state);
    return () => this.statusListeners.delete(cb);
  }

  private subscriberCount(): number {
    let total = 0;
    for (const bucket of this.handlers.values()) total += bucket.size;
    return total;
  }

  private setState(next: ConnectionState): void {
    this.state = next;
    for (const l of this.statusListeners) l(next);
  }

  private ensureOpen(): void {
    if (this.sock && (this.sock.readyState === WebSocket.OPEN || this.sock.readyState === WebSocket.CONNECTING)) {
      return;
    }
    this.closed = false;
    this.connect();
  }

  private connect(): void {
    this.setState("connecting");
    try {
      this.sock = new WebSocket(wsUrl());
    } catch {
      this.setState("error");
      this.scheduleReconnect();
      return;
    }
    this.sock.onopen = () => {
      this.backoff = 1000;
      this.setState("open");
    };
    this.sock.onerror = () => {
      this.setState("error");
    };
    this.sock.onclose = () => {
      this.setState("closed");
      if (!this.closed) this.scheduleReconnect();
    };
    this.sock.onmessage = (msg) => {
      try {
        const event = JSON.parse(msg.data) as AnyEvent;
        this.dispatch(event);
      } catch {
        /* drop malformed */
      }
    };
  }

  private scheduleReconnect(): void {
    if (this.closed) return;
    if (this.subscriberCount() === 0) return;
    const delay = this.backoff;
    this.backoff = Math.min(this.backoff * 2, 30_000);
    setTimeout(() => {
      if (!this.closed && this.subscriberCount() > 0) this.connect();
    }, delay);
  }

  private dispatch(event: AnyEvent): void {
    const bucket = this.handlers.get(event.kind);
    if (bucket) for (const cb of bucket) cb(event);
    const wild = this.handlers.get("*");
    if (wild) for (const cb of wild) cb(event);
  }

  private close(): void {
    this.closed = true;
    if (this.sock) {
      this.sock.close();
      this.sock = null;
    }
    this.setState("closed");
  }
}

export const liveBus = new LiveBus();
