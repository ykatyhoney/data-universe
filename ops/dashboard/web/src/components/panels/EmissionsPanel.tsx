import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useOverview } from "@/hooks/useQueries";

function formatNum(n: number | null | undefined, digits = 3): string {
  if (n == null) return "—";
  return n.toFixed(digits);
}

export function EmissionsPanel() {
  const { data, isLoading } = useOverview();
  const c = data?.latest_chain_state;

  return (
    <Card>
      <CardHeader>
        <CardTitle>1 · Emissions &amp; rank</CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading && <Skeleton className="h-20 w-full" />}
        {!isLoading && !c && (
          <div className="text-xs italic text-muted-foreground py-6 text-center">
            no chain-state rows yet — wired in M2 / M15
          </div>
        )}
        {c && (
          <div className="grid grid-cols-2 gap-3 text-sm">
            <Stat label="hotkey" value={c.hotkey} mono />
            <Stat label="rank" value={String(c.rank)} />
            <Stat label="incentive" value={formatNum(c.incentive, 6)} />
            <Stat label="stake" value={formatNum(c.stake, 4)} />
            <Stat label="cred P2P" value={formatNum(c.credibility_p2p, 3)} />
            <Stat label="cred S3" value={formatNum(c.credibility_s3, 3)} />
            <Stat label="cred OD" value={formatNum(c.credibility_od, 3)} />
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function Stat({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[10px] uppercase tracking-wider text-muted-foreground">{label}</span>
      <span className={mono ? "font-mono text-xs break-all" : "font-medium"}>{value}</span>
    </div>
  );
}
