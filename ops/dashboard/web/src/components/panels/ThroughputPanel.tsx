import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Sparkline } from "@/components/panels/Sparkline";
import { useMetricsSummary } from "@/hooks/useQueries";

const DISPLAY = [
  "scrape_tasks_total",
  "scrape_items_total",
  "worker_busy",
  "self_validation_pass_ratio",
];

export function ThroughputPanel() {
  const { data, isLoading } = useMetricsSummary();
  const series = data?.series ?? [];

  return (
    <Card>
      <CardHeader>
        <CardTitle>2 · Pipeline throughput</CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading && <Skeleton className="h-24 w-full" />}
        {!isLoading && series.every((s) => s.points.length === 0) && (
          <div className="text-xs italic text-muted-foreground py-6 text-center">
            no metric snapshots yet — wired in M2 / M6
          </div>
        )}
        {!isLoading && series.some((s) => s.points.length > 0) && (
          <div className="grid grid-cols-2 gap-3">
            {DISPLAY.map((name) => {
              const s = series.find((x) => x.metric === name);
              const points = s?.points.map(([, v]) => v) ?? [];
              return (
                <div key={name} className="flex flex-col gap-1">
                  <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
                    {name}
                  </span>
                  <Sparkline points={points} />
                </div>
              );
            })}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
