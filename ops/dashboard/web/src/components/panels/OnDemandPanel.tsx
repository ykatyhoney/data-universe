import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Sparkline } from "@/components/panels/Sparkline";
import { liveBus } from "@/api/ws";
import type { MetricTickEvent } from "@/types";

const BUFFER = 60;

export function OnDemandPanel() {
  const [latencies, setLatencies] = useState<number[]>([]);

  useEffect(() => {
    return liveBus.on("metric.tick", (ev) => {
      const m = ev as MetricTickEvent;
      if (m.metric === "ondemand_request_duration_seconds") {
        setLatencies((prev) => [...prev.slice(-(BUFFER - 1)), m.value]);
      }
    });
  }, []);

  return (
    <Card>
      <CardHeader>
        <CardTitle>5 · On-Demand</CardTitle>
      </CardHeader>
      <CardContent>
        {latencies.length === 0 ? (
          <div className="text-xs italic text-muted-foreground py-6 text-center">
            no OD jobs yet — wired in M12
          </div>
        ) : (
          <div className="flex flex-col gap-2">
            <div className="flex justify-between text-xs">
              <span className="text-muted-foreground">p95 latency</span>
              <span className="font-medium">
                {p95(latencies).toFixed(2)}s
              </span>
            </div>
            <Sparkline points={latencies} />
            <div className="text-[11px] text-muted-foreground">
              target: {"<"} 10s · linear reward curve 0–120s
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function p95(values: number[]): number {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const idx = Math.min(sorted.length - 1, Math.floor(sorted.length * 0.95));
  return sorted[idx]!;
}
