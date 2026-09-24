/**
 * Model marketplace API: Hugging Face models, their fit on each cluster, hosting.
 *
 * Every list answers with `hub`: "online", or "offline" when this server
 * cannot reach Hugging Face -- a normal installation, shown as such.
 */
import type { EngineConfig } from "~/lib/engine";

const BASE = "/api/llm/marketplace";

export type HubState = "online" | "offline";
export type FitStatus = "fits" | "too_large" | "unknown" | "no_accelerators";
export type ModelTask = "chat" | "embedding" | "vision" | "other";
export type MarketSort = "trending" | "downloads" | "likes" | "recent";

export interface Fit {
  status: FitStatus;
  /** Accelerators one copy spans, or a fraction of one when it shares a card. */
  gpus_per_copy: number | null;
  tensor_parallel: number | null;
  copies: number;
  copies_now: number;
  context: number | null;
  max_context: number | null;
  needed_bytes_per_gpu: number | null;
  gpu_bytes: number | null;
  weights_bytes: number | null;
  kv_bytes_per_token: number | null;
  suggested_gpu_memory_utilization: number | null;
  shareable: boolean;
  notes: string[];
}

export interface CuratedInfo {
  group: string;
  /** Translation key for why it is on the list. */
  blurb: string;
  verified: { on: string; date: string } | null;
}

export interface LocalCopy {
  model_id: string;
  status: "available" | "downloading" | "failed" | string;
  deployments: number;
}

export interface MarketModel {
  repo_id: string;
  name: string;
  author: string | null;
  downloads?: number;
  likes?: number;
  trending_score?: number | null;
  created_at?: string | null;
  last_modified?: string | null;
  pipeline_tag?: string | null;
  license?: string | null;
  gated?: boolean;
  params_b: number | null;
  active_params_b?: number | null;
  weights_bytes: number | null;
  dtype?: string | null;
  format: string;
  quantization?: string | null;
  capabilities: string[];
  task: ModelTask;
  runnable: boolean;
  not_runnable_reason?: string | null;
  architecture?: string | null;
  base_model?: string | null;
  curated?: CuratedInfo;
  fit?: Fit | null;
  local?: LocalCopy | null;
}

export interface MarketModelDetail extends MarketModel {
  summary?: string | null;
  max_context?: number | null;
  kv_bytes_per_token?: number | null;
  needs_remote_code?: boolean;
  files?: { name: string; size: number | null }[];
  total_bytes?: number | null;
  architecture_facts?: Record<string, unknown>;
}

export interface MarketGpu {
  name: string;
  total_bytes: number;
  free_bytes: number | null;
}

export interface MarketCluster {
  environment_id: string;
  name: string;
  status: string;
  machines: { node_id: string; name: string; gpus: MarketGpu[] }[];
  vllm_version: string | null;
  gpu_count: number;
  gpu_bytes: number | null;
  accelerator: string | null;
}

export interface MarketList {
  hub: HubState;
  cluster_id: string | null;
  items: MarketModel[];
  groups?: string[];
}

export interface MarketDetail {
  hub: HubState;
  model: MarketModelDetail;
  clusters: MarketCluster[];
  cluster_id: string | null;
  fits: Record<string, Fit>;
  suggested: { config: EngineConfig; reasons: Record<string, string> };
  local: LocalCopy | null;
}

export interface HostPayload {
  repo_id: string;
  environment_id: string;
  name: string;
  alias: string | null;
  copies: number;
  gpus_per_copy: number;
  engine_config: EngineConfig;
  revision?: string | null;
}

export interface HostResult {
  deployment_id: string;
  model_id: string;
  download: "kept" | "started" | "none";
  download_error: string | null;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init.headers ?? {}) },
    credentials: "include",
  });
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    let message = text;
    try {
      const parsed = JSON.parse(text) as { detail?: unknown };
      if (typeof parsed.detail === "string") message = parsed.detail;
      else if (Array.isArray(parsed.detail)) {
        message = parsed.detail.map((d: { msg?: string }) => d.msg ?? "").filter(Boolean).join("; ");
      }
    } catch {
      // not JSON
    }
    const error = new Error(message) as Error & { status?: number };
    error.status = res.status;
    throw error;
  }
  return res.json() as Promise<T>;
}

function query(params: Record<string, string | number | null | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== null && v !== undefined && v !== "") q.set(k, String(v));
  const s = q.toString();
  return s ? `?${s}` : "";
}

export const marketplaceApi = {
  clusters() {
    return request<MarketCluster[]>("/clusters");
  },
  recommended(clusterId?: string | null) {
    return request<MarketList>(`/recommended${query({ cluster_id: clusterId })}`);
  },
  search(params: { q?: string; sort?: MarketSort; task?: ModelTask; clusterId?: string | null; limit?: number }) {
    return request<MarketList>(
      `/search${query({ q: params.q, sort: params.sort, task: params.task, cluster_id: params.clusterId, limit: params.limit })}`,
    );
  },
  local(clusterId?: string | null) {
    return request<MarketList>(`/local${query({ cluster_id: clusterId })}`);
  },
  detail(repoId: string, clusterId?: string | null, context?: number | null) {
    return request<MarketDetail>(`/models/${repoId}${query({ cluster_id: clusterId, context })}`);
  },
  host(payload: HostPayload) {
    return request<HostResult>("/host", { method: "POST", body: JSON.stringify(payload) });
  },
};
