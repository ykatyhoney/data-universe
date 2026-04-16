import { AlertCircle, ExternalLink } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useRedditOverview } from "@/hooks/useQueries";
import { relativeTime } from "@/lib/utils";
import type { SubredditCoverageDTO } from "@/types";

function promotedRatio(row: SubredditCoverageDTO): number {
  if (row.total === 0) return 0;
  return row.promoted / row.total;
}

function promotedTone(ratio: number) {
  if (ratio >= 0.8) return "ok" as const;
  if (ratio >= 0.5) return "warn" as const;
  return "err" as const;
}

export function RedditPanel() {
  const { data, isLoading } = useRedditOverview();
  const coverage = data?.coverage ?? [];
  const accounts = data?.accounts;
  const primaryPathDown = accounts && accounts.with_praw_credentials === 0;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Reddit</CardTitle>
        <div className="flex gap-1.5 flex-wrap pt-1">
          {accounts ? (
            <>
              <Badge variant="ok">active · {accounts.active}</Badge>
              <Badge variant="warn">cooling · {accounts.cooling}</Badge>
              <Badge variant="err">quarantined · {accounts.quarantined}</Badge>
              <Badge variant="muted">
                PRAW accounts · {accounts.with_praw_credentials}/{accounts.total}
              </Badge>
            </>
          ) : (
            <Badge variant="muted">no account data</Badge>
          )}
        </div>
      </CardHeader>
      <CardContent>
        {primaryPathDown && (
          <div className="mb-3 flex items-start gap-2 rounded-md border border-warn/40 bg-warn/5 px-3 py-2 text-xs">
            <AlertCircle className="h-3 w-3 mt-0.5 text-warn" aria-label="warning" />
            <span>
              no accounts with PRAW credentials — scraping is running on the
              JSON fallback, which is IP-rate-limited and slower. Import a
              Reddit OAuth app under{" "}
              <code className="font-mono">account_pool.import_cli</code>.
            </span>
          </div>
        )}
        {isLoading && <Skeleton className="h-24 w-full" />}
        {!isLoading && coverage.length === 0 && (
          <div className="text-xs italic text-muted-foreground py-6 text-center">
            no reddit items collected yet — queue a task via{" "}
            <code className="font-mono">scrape:tasks</code> with{" "}
            <code className="font-mono">source=reddit mode=search label=r/...</code>
          </div>
        )}
        {!isLoading && coverage.length > 0 && (
          <div className="max-h-[360px] overflow-y-auto">
            <table className="w-full text-xs">
              <thead className="text-[10px] uppercase tracking-wider text-muted-foreground">
                <tr>
                  <th className="text-left pb-1">subreddit</th>
                  <th className="text-right pb-1">total</th>
                  <th className="text-right pb-1">promoted</th>
                  <th className="text-right pb-1">quarantined</th>
                  <th className="text-right pb-1">promoted %</th>
                  <th className="text-left pb-1 pl-4">last seen</th>
                  <th className="text-right pb-1"></th>
                </tr>
              </thead>
              <tbody>
                {coverage.map((row) => {
                  const ratio = promotedRatio(row);
                  return (
                    <tr key={row.label} className="border-t border-border/40">
                      <td className="py-1 pr-2 font-mono text-[11px]">
                        {row.label}
                      </td>
                      <td className="py-1 pr-2 text-right tabular-nums">{row.total}</td>
                      <td className="py-1 pr-2 text-right tabular-nums text-muted-foreground">
                        {row.promoted}
                      </td>
                      <td className="py-1 pr-2 text-right tabular-nums text-muted-foreground">
                        {row.quarantined}
                      </td>
                      <td className="py-1 pr-2 text-right">
                        <Badge variant={promotedTone(ratio)}>
                          {Math.round(ratio * 100)}%
                        </Badge>
                      </td>
                      <td className="py-1 pl-4 text-muted-foreground">
                        {relativeTime(row.last_seen)}
                      </td>
                      <td className="py-1 text-right">
                        <a
                          href={`https://www.reddit.com/${row.label}/`}
                          target="_blank"
                          rel="noreferrer"
                          className="text-muted-foreground hover:text-foreground inline-flex items-center"
                          title="open on reddit.com"
                        >
                          <ExternalLink className="h-3 w-3" />
                        </a>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
