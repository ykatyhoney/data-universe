import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { useHealth } from "@/hooks/useQueries";

function variantFor(state?: "ok" | "down") {
  if (state === "ok") return "ok" as const;
  if (state === "down") return "err" as const;
  return "muted" as const;
}

export function InfraPanel() {
  const { data, isLoading } = useHealth();
  const rows: [string, "ok" | "down" | undefined][] = [
    ["dashboard-api", data?.status === "ok" ? "ok" : data ? "down" : undefined],
    ["sqlite", data?.database],
    ["redis", data?.redis],
  ];

  return (
    <Card>
      <CardHeader>
        <CardTitle>Infrastructure</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="flex flex-col gap-2 text-sm">
          {rows.map(([name, state]) => (
            <div key={name} className="flex items-center justify-between">
              <span className="text-muted-foreground">{name}</span>
              <Badge variant={variantFor(state)}>
                {isLoading && !state ? "…" : (state ?? "unknown")}
              </Badge>
            </div>
          ))}
          <div className="pt-2 border-t flex justify-between text-xs text-muted-foreground">
            <span>milestone</span>
            <span>{data?.milestone ?? "—"}</span>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
