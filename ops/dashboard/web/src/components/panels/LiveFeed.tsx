import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { liveBus } from "@/api/ws";
import type { AnyEvent } from "@/types";

const MAX = 50;

export function LiveFeed() {
  const [events, setEvents] = useState<AnyEvent[]>([]);

  useEffect(() => {
    return liveBus.on("*", (ev) => {
      setEvents((prev) => [ev, ...prev].slice(0, MAX));
    });
  }, []);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Live feed</CardTitle>
        <div className="text-xs text-muted-foreground">
          {events.length === 0
            ? "waiting for events…"
            : `last ${events.length} of rolling ${MAX}`}
        </div>
      </CardHeader>
      <CardContent>
        {events.length === 0 ? (
          <div className="text-xs italic text-muted-foreground py-6 text-center">
            publish with:&nbsp;
            <code className="font-mono">python -m dashboard.api.seed_demo --rate 5</code>
          </div>
        ) : (
          <div className="flex flex-col gap-1 max-h-[320px] overflow-y-auto text-xs">
            {events.map((ev, i) => (
              <Row key={i} event={ev} />
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function Row({ event }: { event: AnyEvent }) {
  const ts = event.ts?.substring(11, 19) ?? "--:--:--";
  return (
    <div className="flex items-center gap-2 border-b border-border/40 pb-1">
      <span className="font-mono text-muted-foreground">{ts}</span>
      <Badge variant={variantFor(event.kind)}>{event.kind}</Badge>
      <span className="truncate text-muted-foreground">{describe(event)}</span>
    </div>
  );
}

function variantFor(kind: AnyEvent["kind"]) {
  if (kind.startsWith("task.")) return "default" as const;
  if (kind === "worker.heartbeat") return "muted" as const;
  if (kind === "metric.tick") return "muted" as const;
  return "warn" as const;
}

function describe(event: AnyEvent): string {
  switch (event.kind) {
    case "proxy.state_changed":
      return `${event.from_state ?? "?"} → ${event.to_state}${event.reason ? ` (${event.reason})` : ""}`;
    case "account.state_changed":
      return `${event.source} · ${event.from_state ?? "?"} → ${event.to_state}`;
    case "worker.heartbeat":
      return `${event.worker_id} · ${event.state} · ctx=${event.browser_context_count} · ${event.memory_mb.toFixed(0)}MB`;
    case "task.started":
      return `${event.source} · ${event.mode} · ${event.label} · ${event.worker_id}`;
    case "task.finished":
      return `${event.source} · ${event.outcome} · ${event.item_count} items · ${event.duration_seconds.toFixed(1)}s`;
    case "metric.tick":
      return `${event.metric} = ${event.value.toFixed(2)}`;
  }
}
