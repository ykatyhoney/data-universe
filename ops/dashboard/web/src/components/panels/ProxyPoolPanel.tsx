import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Ban, Play, RefreshCw } from "lucide-react";

import { api, ApiError } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useProxyPoolState } from "@/hooks/useQueries";
import { relativeTime } from "@/lib/utils";
import type { ProxySnapshotDTO } from "@/types";

function variantFor(state: string) {
  if (state === "healthy") return "ok" as const;
  if (state === "cooling") return "warn" as const;
  if (state === "quarantined" || state === "disabled") return "err" as const;
  return "muted" as const;
}

export function ProxyPoolPanel() {
  const { data, isLoading } = useProxyPoolState();
  const qc = useQueryClient();
  const [busyId, setBusyId] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function toggle(proxy: ProxySnapshotDTO) {
    setErr(null);
    setBusyId(proxy.id);
    try {
      const verb = proxy.state === "disabled" ? "enable" : "disable";
      await api.post(`/api/proxy-pool/admin/${proxy.id}/${verb}`);
      await qc.invalidateQueries({ queryKey: ["proxy-pool-state"] });
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "admin action failed");
    } finally {
      setBusyId(null);
    }
  }

  async function resync() {
    setErr(null);
    try {
      await api.post("/api/proxy-pool/admin/sync");
      await qc.invalidateQueries({ queryKey: ["proxy-pool-state"] });
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "sync failed");
    }
  }

  const counts = data?.counts_by_state ?? {};
  const proxies = data?.proxies ?? [];

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle>Proxy pool</CardTitle>
          <Button size="sm" variant="ghost" onClick={resync} title="re-sync from backends">
            <RefreshCw className="h-3.5 w-3.5" />
          </Button>
        </div>
        <div className="flex gap-2 flex-wrap pt-1">
          {(["healthy", "cooling", "quarantined", "disabled"] as const).map((s) => (
            <Badge key={s} variant={variantFor(s)}>
              {s} · {counts[s] ?? 0}
            </Badge>
          ))}
        </div>
      </CardHeader>
      <CardContent>
        {isLoading && <Skeleton className="h-24 w-full" />}
        {!isLoading && proxies.length === 0 && (
          <div className="text-xs italic text-muted-foreground py-6 text-center">
            no proxies registered — set <code className="font-mono">OPS_PROXY_STATIC_ENDPOINTS</code>{" "}
            and click the refresh button
          </div>
        )}
        {!isLoading && proxies.length > 0 && (
          <div className="max-h-[320px] overflow-y-auto">
            <table className="w-full text-xs">
              <thead className="text-[10px] uppercase tracking-wider text-muted-foreground">
                <tr>
                  <th className="text-left pb-1">endpoint</th>
                  <th className="text-left pb-1">state</th>
                  <th className="text-left pb-1">fail</th>
                  <th className="text-left pb-1">last probe</th>
                  <th className="text-right pb-1"></th>
                </tr>
              </thead>
              <tbody>
                {proxies.map((p) => (
                  <tr key={p.id} className="border-t border-border/40">
                    <td className="font-mono py-1 pr-2 truncate max-w-[220px]">
                      {p.url_masked}
                    </td>
                    <td className="py-1 pr-2">
                      <Badge variant={variantFor(p.state)}>{p.state}</Badge>
                    </td>
                    <td className="py-1 pr-2 text-muted-foreground">{p.fail_streak}</td>
                    <td className="py-1 pr-2 text-muted-foreground">
                      {relativeTime(p.last_probe_at)}
                    </td>
                    <td className="py-1 text-right">
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={busyId === p.id}
                        onClick={() => toggle(p)}
                        title={p.state === "disabled" ? "enable" : "disable"}
                      >
                        {p.state === "disabled" ? (
                          <Play className="h-3 w-3" />
                        ) : (
                          <Ban className="h-3 w-3" />
                        )}
                      </Button>
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
