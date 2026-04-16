import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useOverview } from "@/hooks/useQueries";

export function CoveragePanel() {
  const { data } = useOverview();

  return (
    <Card>
      <CardHeader>
        <CardTitle>4 · Scoring / DD coverage</CardTitle>
      </CardHeader>
      <CardContent>
        {data && data.active_dd_jobs > 0 ? (
          <div className="text-sm">
            <div className="flex justify-between">
              <span className="text-muted-foreground">active DD jobs</span>
              <span className="font-medium">{data.active_dd_jobs}</span>
            </div>
            <div className="mt-2 text-xs italic text-muted-foreground">
              per-job coverage charts land in M13 / M14
            </div>
          </div>
        ) : (
          <div className="text-xs italic text-muted-foreground py-6 text-center">
            no DD jobs tracked yet — wired in M13 / M14
          </div>
        )}
      </CardContent>
    </Card>
  );
}
