import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Archive, Ban, Play } from "lucide-react";

import { api, ApiError } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useAccountPoolState } from "@/hooks/useQueries";
import { relativeTime } from "@/lib/utils";
import type { AccountSnapshotDTO } from "@/types";

function variantFor(state: string) {
  if (state === "active") return "ok" as const;
  if (state === "new" || state === "cooling") return "warn" as const;
  if (state === "quarantined" || state === "retired") return "err" as const;
  return "muted" as const;
}

function budgetBar(used: number, max: number) {
  const pct = Math.min(100, Math.round((used / Math.max(1, max)) * 100));
  const tone = pct > 90 ? "bg-err" : pct > 70 ? "bg-warn" : "bg-primary";
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-16 rounded bg-muted overflow-hidden">
        <div className={`h-full ${tone}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="tabular-nums text-[10px] text-muted-foreground">
        {used}/{max}
      </span>
    </div>
  );
}

export function AccountPoolPanel() {
  const { data, isLoading } = useAccountPoolState();
  const qc = useQueryClient();
  const [busyId, setBusyId] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function action(id: string, verb: "quarantine" | "activate" | "retire") {
    setErr(null);
    setBusyId(id);
    try {
      await api.post(`/api/account-pool/admin/${id}/${verb}`);
      await qc.invalidateQueries({ queryKey: ["account-pool-state"] });
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "admin action failed");
    } finally {
      setBusyId(null);
    }
  }

  const accounts = data?.accounts ?? [];
  const counts = data?.counts_by_source_state ?? [];

  return (
    <Card>
      <CardHeader>
        <CardTitle>Account pool</CardTitle>
        <div className="flex gap-1.5 flex-wrap pt-1">
          {counts.length === 0 ? (
            <Badge variant="muted">none</Badge>
          ) : (
            counts.map(([source, state, n], i) => (
              <Badge key={i} variant={variantFor(state)}>
                {source} · {state} · {n}
              </Badge>
            ))
          )}
        </div>
      </CardHeader>
      <CardContent>
        {isLoading && <Skeleton className="h-24 w-full" />}
        {!isLoading && accounts.length === 0 && (
          <div className="text-xs italic text-muted-foreground py-6 text-center">
            no accounts imported — use{" "}
            <code className="font-mono">python -m account_pool.import_cli account.json</code>
          </div>
        )}
        {!isLoading && accounts.length > 0 && (
          <div className="max-h-[360px] overflow-y-auto">
            <table className="w-full text-xs">
              <thead className="text-[10px] uppercase tracking-wider text-muted-foreground">
                <tr>
                  <th className="text-left pb-1">source</th>
                  <th className="text-left pb-1">state</th>
                  <th className="text-left pb-1">pinned</th>
                  <th className="text-left pb-1">budget/min</th>
                  <th className="text-left pb-1">budget/hr</th>
                  <th className="text-left pb-1">last ok</th>
                  <th className="text-left pb-1">last fail</th>
                  <th className="text-right pb-1"></th>
                </tr>
              </thead>
              <tbody>
                {accounts.map((a: AccountSnapshotDTO) => (
                  <tr key={a.id} className="border-t border-border/40">
                    <td className="py-1 pr-2">
                      <Badge variant="muted">{a.source}</Badge>
                    </td>
                    <td className="py-1 pr-2">
                      <Badge variant={variantFor(a.state)}>{a.state}</Badge>
                    </td>
                    <td className="py-1 pr-2 text-muted-foreground font-mono text-[10px]">
                      {a.pinned_proxy_id ? a.pinned_proxy_id.slice(0, 8) : "—"}
                    </td>
                    <td className="py-1 pr-2">
                      {budgetBar(a.budget_minute_used, a.budget_minute_max)}
                    </td>
                    <td className="py-1 pr-2">
                      {budgetBar(a.budget_hour_used, a.budget_hour_max)}
                    </td>
                    <td className="py-1 pr-2 text-muted-foreground">
                      {relativeTime(a.last_ok_at)}
                    </td>
                    <td className="py-1 pr-2 text-muted-foreground">
                      {a.last_fail_reason ? (
                        <span title={a.last_fail_reason}>
                          {relativeTime(a.last_fail_at)} · {a.last_fail_reason}
                        </span>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className="py-1 text-right">
                      <div className="flex justify-end gap-1">
                        {a.state !== "quarantined" ? (
                          <Button
                            size="sm"
                            variant="ghost"
                            disabled={busyId === a.id}
                            onClick={() => action(a.id, "quarantine")}
                            title="quarantine"
                          >
                            <Ban className="h-3 w-3" />
                          </Button>
                        ) : (
                          <Button
                            size="sm"
                            variant="ghost"
                            disabled={busyId === a.id}
                            onClick={() => action(a.id, "activate")}
                            title="activate"
                          >
                            <Play className="h-3 w-3" />
                          </Button>
                        )}
                        {a.state !== "retired" && (
                          <Button
                            size="sm"
                            variant="ghost"
                            disabled={busyId === a.id}
                            onClick={() => action(a.id, "retire")}
                            title="retire"
                          >
                            <Archive className="h-3 w-3" />
                          </Button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {err && <div className="text-xs text-err pt-2">{err}</div>}
      </CardContent>
    </Card>
  );
}
