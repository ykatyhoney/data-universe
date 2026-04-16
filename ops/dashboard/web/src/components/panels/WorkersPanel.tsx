import { Download, Image } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useWorkers } from "@/hooks/useQueries";
import { relativeTime } from "@/lib/utils";
import type { WorkerDTO } from "@/types";

function variantFor(state: string) {
  if (state === "busy") return "ok" as const;
  if (state === "idle") return "muted" as const;
  if (state === "draining") return "warn" as const;
  if (state === "offline") return "err" as const;
  return "muted" as const;
}

function heartbeatVariant(lastHeartbeatAt: string | null) {
  if (!lastHeartbeatAt) return "err" as const;
  const age = Date.now() - new Date(lastHeartbeatAt).getTime();
  if (age > 60_000) return "err" as const;
  if (age > 30_000) return "warn" as const;
  return "ok" as const;
}

export function WorkersPanel() {
  const { data, isLoading } = useWorkers();
  const workers: WorkerDTO[] = data ?? [];
  const busy = workers.filter((w) => w.state === "busy").length;
  const idle = workers.filter((w) => w.state === "idle").length;
  const offline = workers.filter((w) => w.state === "offline").length;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Workers</CardTitle>
        <div className="flex gap-1.5 flex-wrap pt-1">
          <Badge variant="ok">busy · {busy}</Badge>
          <Badge variant="muted">idle · {idle}</Badge>
          <Badge variant="err">offline · {offline}</Badge>
          <Badge variant="default">total · {workers.length}</Badge>
        </div>
      </CardHeader>
      <CardContent>
        {isLoading && <Skeleton className="h-24 w-full" />}
        {!isLoading && workers.length === 0 && (
          <div className="text-xs italic text-muted-foreground py-6 text-center">
            no workers connected — run <code className="font-mono">pm2 start ecosystem.config.js --only worker</code>
            {" "}(or <code className="font-mono">pm2 scale worker 10</code>)
          </div>
        )}
        {!isLoading && workers.length > 0 && (
          <div className="max-h-[320px] overflow-y-auto">
            <table className="w-full text-xs">
              <thead className="text-[10px] uppercase tracking-wider text-muted-foreground">
                <tr>
                  <th className="text-left pb-1">id</th>
                  <th className="text-left pb-1">host</th>
                  <th className="text-left pb-1">state</th>
                  <th className="text-left pb-1">task</th>
                  <th className="text-left pb-1">ctx</th>
                  <th className="text-left pb-1">mem</th>
                  <th className="text-left pb-1">heartbeat</th>
                  <th className="text-right pb-1">debug</th>
                </tr>
              </thead>
              <tbody>
                {workers.map((w) => (
                  <tr key={w.id} className="border-t border-border/40">
                    <td className="py-1 pr-2 font-mono text-[10px]">{w.id}</td>
                    <td className="py-1 pr-2 text-muted-foreground">{w.host}</td>
                    <td className="py-1 pr-2">
                      <Badge variant={variantFor(w.state)}>{w.state}</Badge>
                    </td>
                    <td className="py-1 pr-2 font-mono text-[10px] text-muted-foreground">
                      {w.current_task_id
                        ? w.current_task_id.slice(0, 8)
                        : "—"}
                    </td>
                    <td className="py-1 pr-2 tabular-nums">
                      {w.browser_context_count}
                    </td>
                    <td className="py-1 pr-2 tabular-nums text-muted-foreground">
                      {w.memory_mb.toFixed(0)}M
                    </td>
                    <td className="py-1 pr-2">
                      <Badge variant={heartbeatVariant(w.last_heartbeat_at)}>
                        {relativeTime(w.last_heartbeat_at)}
                      </Badge>
                    </td>
                    <td className="py-1 text-right">
                      {w.current_task_id && (
                        <div className="flex justify-end gap-1">
                          <Button
                            size="sm"
                            variant="ghost"
                            title="download HAR"
                            onClick={() =>
                              window.open(
                                `/api/worker/debug/${w.current_task_id}/har`,
                                "_blank",
                              )
                            }
                          >
                            <Download className="h-3 w-3" />
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            title="view screenshot"
                            onClick={() =>
                              window.open(
                                `/api/worker/debug/${w.current_task_id}/screenshot`,
                                "_blank",
                              )
                            }
                          >
                            <Image aria-label="screenshot" className="h-3 w-3" />
                          </Button>
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
