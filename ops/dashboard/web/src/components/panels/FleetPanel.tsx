import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useOverview } from "@/hooks/useQueries";

function rowFromRecord(obj: Record<string, number> | undefined): [string, number][] {
  if (!obj) return [];
  return Object.entries(obj).sort((a, b) => b[1] - a[1]);
}

export function FleetPanel() {
  const { data, isLoading } = useOverview();
  const proxies = rowFromRecord(data?.proxies_by_state);
  const workers = rowFromRecord(data?.workers_by_state);
  const accounts = data?.accounts_by_source_state ?? [];

  const empty =
    proxies.length === 0 && workers.length === 0 && accounts.length === 0;

  return (
    <Card>
      <CardHeader>
        <CardTitle>3 · Fleet health</CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading && <Skeleton className="h-28 w-full" />}
        {!isLoading && empty && (
          <div className="text-xs italic text-muted-foreground py-6 text-center">
            no fleet rows yet — wired in M3 / M4 / M5
          </div>
        )}
        {!isLoading && !empty && (
          <div className="grid grid-cols-3 gap-4 text-sm">
            <Group title="proxies" rows={proxies} />
            <Group title="workers" rows={workers} />
            <AccountsGroup rows={accounts} />
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function Group({ title, rows }: { title: string; rows: [string, number][] }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-muted-foreground mb-1">
        {title}
      </div>
      <div className="flex flex-col gap-1">
        {rows.length === 0 && <span className="text-xs text-muted-foreground">—</span>}
        {rows.map(([state, n]) => (
          <div key={state} className="flex justify-between text-xs">
            <Badge variant="muted">{state}</Badge>
            <span>{n}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function AccountsGroup({ rows }: { rows: [string, string, number][] }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-muted-foreground mb-1">
        accounts
      </div>
      <div className="flex flex-col gap-1">
        {rows.length === 0 && <span className="text-xs text-muted-foreground">—</span>}
        {rows.map(([source, state, n], i) => (
          <div key={i} className="flex justify-between text-xs">
            <span>
              <Badge variant="muted">{source}</Badge>{" "}
              <Badge variant="default">{state}</Badge>
            </span>
            <span>{n}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
