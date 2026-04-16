// Wire types — keep in sync with ops/dashboard/api/dto.py.
// M1.E will add a contract test that asserts the OpenAPI schema matches.

export interface ProxyDTO {
  id: string;
  endpoint: string;
  backend: string;
  state: string;
  session_id: string | null;
  last_probe_at: string | null;
  fail_streak: number;
  quarantined_until: string | null;
  created_at: string;
}

/** Dashboard-facing view from GET /api/proxy-pool/state. */
export interface ProxySnapshotDTO {
  id: string;
  url_masked: string;
  backend: string;
  state: string;
  session_id: string | null;
  last_probe_at: string | null;
  fail_streak: number;
  quarantined_until: string | null;
  created_at: string;
}

export interface PoolStateDTO {
  proxies: ProxySnapshotDTO[];
  counts_by_state: Record<string, number>;
  ts: string;
}

/** /api/account-pool/state — NO cookies field by design. */
export interface AccountSnapshotDTO {
  id: string;
  source: string;
  state: string;
  pinned_proxy_id: string | null;
  user_agent_preview: string | null;
  imported_at: string;
  last_ok_at: string | null;
  last_fail_at: string | null;
  last_fail_reason: string | null;
  cooling_until: string | null;
  fail_streak: number;
  budget_minute_used: number;
  budget_minute_max: number;
  budget_hour_used: number;
  budget_hour_max: number;
  notes: string | null;
}

export interface AccountPoolStateDTO {
  accounts: AccountSnapshotDTO[];
  counts_by_source_state: [string, string, number][];
  ts: string;
}

export interface AccountDTO {
  id: string;
  source: string;
  state: string;
  pinned_proxy_id: string | null;
  imported_at: string;
  last_ok_at: string | null;
  cooling_until: string | null;
  created_at: string;
}

export interface WorkerDTO {
  id: string;
  host: string;
  state: string;
  current_task_id: string | null;
  browser_context_count: number;
  memory_mb: number;
  last_heartbeat_at: string | null;
  created_at: string;
}

export interface TaskDTO {
  id: string;
  source: string;
  mode: string;
  label: string;
  priority: number;
  state: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  worker_id: string | null;
  outcome: string | null;
  error: string | null;
}

export interface ChainStateDTO {
  hotkey: string;
  ts: string;
  incentive: number;
  stake: number;
  credibility_p2p: number;
  credibility_s3: number;
  credibility_od: number;
  rank: number;
}

export interface OverviewDTO {
  proxies_by_state: Record<string, number>;
  accounts_by_source_state: [string, string, number][];
  workers_by_state: Record<string, number>;
  tasks_by_state: Record<string, number>;
  active_dd_jobs: number;
  latest_chain_state: ChainStateDTO | null;
}

export interface MetricSeries {
  metric: string;
  points: [string, number][];
}

export interface MetricsSummaryDTO {
  series: MetricSeries[];
}

export interface HealthResponse {
  status: "ok" | "degraded";
  service: string;
  ts: string;
  milestone: string;
  database: "ok" | "down";
  redis: "ok" | "down";
}

// ---------- Live event envelope (mirrors common/events.py) ---------- //

export type EventKind =
  | "proxy.state_changed"
  | "account.state_changed"
  | "worker.heartbeat"
  | "task.started"
  | "task.finished"
  | "metric.tick";

interface EventBase {
  ts: string;
  trace_id: string | null;
}

export interface ProxyStateChangedEvent extends EventBase {
  kind: "proxy.state_changed";
  proxy_id: string;
  from_state: string | null;
  to_state: string;
  reason: string | null;
}

export interface AccountStateChangedEvent extends EventBase {
  kind: "account.state_changed";
  account_id: string;
  source: string;
  from_state: string | null;
  to_state: string;
  reason: string | null;
}

export interface WorkerHeartbeatEvent extends EventBase {
  kind: "worker.heartbeat";
  worker_id: string;
  host: string;
  state: string;
  current_task_id: string | null;
  browser_context_count: number;
  memory_mb: number;
}

export interface TaskStartedEvent extends EventBase {
  kind: "task.started";
  task_id: string;
  source: string;
  mode: string;
  label: string;
  worker_id: string;
}

export interface TaskFinishedEvent extends EventBase {
  kind: "task.finished";
  task_id: string;
  source: string;
  outcome: string;
  state: string;
  item_count: number;
  duration_seconds: number;
  error: string | null;
}

export interface MetricTickEvent extends EventBase {
  kind: "metric.tick";
  metric: string;
  labels: Record<string, string>;
  value: number;
}

export type AnyEvent =
  | ProxyStateChangedEvent
  | AccountStateChangedEvent
  | WorkerHeartbeatEvent
  | TaskStartedEvent
  | TaskFinishedEvent
  | MetricTickEvent;

/** /api/reddit/overview — per-subreddit coverage + PRAW-account health. */
export interface SubredditCoverageDTO {
  label: string;
  total: number;
  promoted: number;
  quarantined: number;
  last_seen: string | null;
}

export interface RedditAccountHealthDTO {
  total: number;
  active: number;
  cooling: number;
  quarantined: number;
  with_praw_credentials: number;
}

export interface RedditOverviewDTO {
  coverage: SubredditCoverageDTO[];
  accounts: RedditAccountHealthDTO;
}
