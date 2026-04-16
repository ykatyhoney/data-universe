import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import type {
  AccountDTO,
  AccountPoolStateDTO,
  HealthResponse,
  MetricsSummaryDTO,
  OverviewDTO,
  PoolStateDTO,
  ProxyDTO,
  RedditOverviewDTO,
  TaskDTO,
  WorkerDTO,
} from "@/types";

/** /api/health is unauthenticated, so it can also double as a login probe. */
export function useHealth() {
  return useQuery({
    queryKey: ["health"],
    queryFn: () => api.get<HealthResponse>("/api/health"),
    refetchInterval: 5000,
    staleTime: 1000,
  });
}

export function useOverview() {
  return useQuery({
    queryKey: ["overview"],
    queryFn: () => api.get<OverviewDTO>("/api/overview"),
    refetchInterval: 10_000,
  });
}

export function useProxies() {
  return useQuery({
    queryKey: ["proxies"],
    queryFn: () => api.get<ProxyDTO[]>("/api/proxies"),
    refetchInterval: 15_000,
  });
}

export function useAccounts() {
  return useQuery({
    queryKey: ["accounts"],
    queryFn: () => api.get<AccountDTO[]>("/api/accounts"),
    refetchInterval: 15_000,
  });
}

export function useWorkers() {
  return useQuery({
    queryKey: ["workers"],
    queryFn: () => api.get<WorkerDTO[]>("/api/workers"),
    refetchInterval: 10_000,
  });
}

export function useTasks(limit = 100) {
  return useQuery({
    queryKey: ["tasks", limit],
    queryFn: () => api.get<TaskDTO[]>(`/api/tasks?limit=${limit}`),
    refetchInterval: 10_000,
  });
}

export function useMetricsSummary() {
  return useQuery({
    queryKey: ["metrics-summary"],
    queryFn: () => api.get<MetricsSummaryDTO>("/api/metrics/summary"),
    refetchInterval: 15_000,
  });
}

export function useProxyPoolState() {
  return useQuery({
    queryKey: ["proxy-pool-state"],
    queryFn: () => api.get<PoolStateDTO>("/api/proxy-pool/state"),
    refetchInterval: 5_000,
  });
}

export function useAccountPoolState() {
  return useQuery({
    queryKey: ["account-pool-state"],
    queryFn: () => api.get<AccountPoolStateDTO>("/api/account-pool/state"),
    refetchInterval: 5_000,
  });
}

export function useRedditOverview() {
  return useQuery({
    queryKey: ["reddit-overview"],
    queryFn: () => api.get<RedditOverviewDTO>("/api/reddit/overview"),
    refetchInterval: 15_000,
  });
}
