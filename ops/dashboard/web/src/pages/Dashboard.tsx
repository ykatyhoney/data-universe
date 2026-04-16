import { Header } from "@/components/Header";
import { InfraPanel } from "@/components/panels/InfraPanel";
import { EmissionsPanel } from "@/components/panels/EmissionsPanel";
import { ThroughputPanel } from "@/components/panels/ThroughputPanel";
import { FleetPanel } from "@/components/panels/FleetPanel";
import { CoveragePanel } from "@/components/panels/CoveragePanel";
import { OnDemandPanel } from "@/components/panels/OnDemandPanel";
import { ProxyPoolPanel } from "@/components/panels/ProxyPoolPanel";
import { AccountPoolPanel } from "@/components/panels/AccountPoolPanel";
import { RedditPanel } from "@/components/panels/RedditPanel";
import { WorkersPanel } from "@/components/panels/WorkersPanel";
import { LiveFeed } from "@/components/panels/LiveFeed";

export function Dashboard() {
  return (
    <div className="min-h-screen flex flex-col">
      <Header />
      <main className="flex-1 p-6 grid gap-4 grid-cols-1 md:grid-cols-2 xl:grid-cols-3">
        <InfraPanel />
        <EmissionsPanel />
        <ThroughputPanel />
        <FleetPanel />
        <CoveragePanel />
        <OnDemandPanel />
        <div className="md:col-span-2 xl:col-span-3">
          <ProxyPoolPanel />
        </div>
        <div className="md:col-span-2 xl:col-span-3">
          <AccountPoolPanel />
        </div>
        <div className="md:col-span-2 xl:col-span-3">
          <RedditPanel />
        </div>
        <div className="md:col-span-2 xl:col-span-3">
          <WorkersPanel />
        </div>
        <div className="md:col-span-2 xl:col-span-3">
          <LiveFeed />
        </div>
      </main>
      <footer className="border-t px-6 py-3 text-[11px] text-muted-foreground flex justify-between">
        <span>Subnet 13 · Data Universe · ops dashboard</span>
        <span className="flex gap-3">
          <a className="hover:text-foreground" href="/api/health" target="_blank" rel="noreferrer">
            health
          </a>
          <a className="hover:text-foreground" href="/metrics" target="_blank" rel="noreferrer">
            metrics
          </a>
        </span>
      </footer>
    </div>
  );
}
