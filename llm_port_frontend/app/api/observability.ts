/**
 * Observability API client — thin fetch wrappers for /api/admin/observability.
 */

import { clearCachedAccess } from "~/lib/adminConstants";

const BASE = "/api/admin/observability";

// ── Types ────────────────────────────────────────────────────────────────────

export interface ProviderBreakdown {
  provider_instance_id: string;
  total_requests: number;
  total_tokens: number;
  estimated_total_cost: number | null;
}

export interface ModelBreakdown {
  model_alias: string;
  total_requests: number;
  total_tokens: number;
  estimated_total_cost: number | null;
}

export interface TopUser {
  user_id: string;
  total_requests: number;
  total_tokens: number;
  estimated_total_cost: number | null;
}

export interface Summary {
  total_requests: number;
  total_prompt_tokens: number;
  total_completion_tokens: number;
  total_tokens: number;
  estimated_total_cost: number | null;
  error_count: number;
  avg_latency_ms: number | null;
  by_provider: ProviderBreakdown[];
  by_model: ModelBreakdown[];
  top_users: TopUser[];
}

export interface TimeseriesBucket {
  bucket: string;
  total_requests: number;
  total_tokens: number;
  estimated_total_cost: number | null;
  error_count: number;
  avg_latency_ms: number | null;
}

export interface Performance {
  p50_latency_ms: number | null;
  p95_latency_ms: number | null;
  p99_latency_ms: number | null;
  p50_ttft_ms: number | null;
  p95_ttft_ms: number | null;
  p99_ttft_ms: number | null;
  avg_latency_ms: number | null;
  total_requests: number;
  error_count: number;
  error_rate: number | null;
}

export interface RequestLog {
  id: string;
  request_id: string;
  trace_id: string | null;
  tenant_id: string;
  user_id: string;
  model_alias: string | null;
  provider_instance_id: string | null;
  endpoint: string;
  status_code: number;
  latency_ms: number;
  ttft_ms: number | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  total_tokens: number | null;
  error_code: string | null;
  estimated_input_cost: number | null;
  estimated_output_cost: number | null;
  estimated_total_cost: number | null;
  currency: string | null;
  cost_estimate_status: string | null;
  cached_tokens: number | null;
  stream: boolean | null;
  session_id: string | null;
  finish_reason: string | null;
  retry_count: number | null;
  skills_used: Array<{
    skill_id: string;
    name: string;
    slug: string;
    version: string;
  }> | null;
  rag_context: {
    chunk_count: number;
    collection_ids?: string[];
    top_k: number;
  } | null;
  mcp_tool_call_count: number | null;
  mcp_tool_loop_iterations: number | null;
  created_at: string;
}

export interface ToolCallLog {
  id: string;
  request_id: string;
  iteration: number;
  tool_name: string;
  mcp_server: string | null;
  latency_ms: number;
  is_error: boolean;
  error_message: string | null;
  created_at: string;
}

export interface PaginatedRequests {
  items: RequestLog[];
  total: number;
  page: number;
  limit: number;
}

export interface SessionCost {
  session_id: string;
  total_requests: number;
  total_tokens: number;
  estimated_total_cost: number | null;
}

export interface PricingEntry {
  id: string;
  provider: string;
  model: string;
  input_price_per_1k: string;
  output_price_per_1k: string;
  currency: string;
  effective_from: string;
  active: boolean;
  source: string | null;
  notes: string | null;
  created_at: string;
  updated_at: string;
}

export interface PricingCreate {
  provider: string;
  model: string;
  input_price_per_1k: number;
  output_price_per_1k: number;
  currency?: string;
  notes?: string;
}

export interface PricingUpdate {
  input_price_per_1k: number;
  output_price_per_1k: number;
  currency?: string;
  notes?: string;
}

// ── Helpers ──────────────────────────────────────────────────────────────────

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    cache: "no-store",
    headers: {
      "Content-Type": "application/json",
      "Cache-Control": "no-store",
      Pragma: "no-cache",
      ...(init.headers ?? {}),
    },
    credentials: "include",
  });
  if (!res.ok) {
    if (res.status === 401 || res.status === 403) clearCachedAccess();
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`API ${res.status}: ${text}`);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

function qs(
  params: Record<string, string | number | boolean | undefined | null>,
): string {
  const parts: string[] = [];
  for (const [k, v] of Object.entries(params)) {
    if (v != null && v !== "")
      parts.push(`${k}=${encodeURIComponent(String(v))}`);
  }
  return parts.length ? `?${parts.join("&")}` : "";
}

// ── API ──────────────────────────────────────────────────────────────────────

export const observability = {
  summary(start: string, end: string) {
    return request<Summary>(`/summary${qs({ start, end })}`);
  },

  timeseries(start: string, end: string, granularity = "day") {
    return request<TimeseriesBucket[]>(
      `/timeseries${qs({ start, end, granularity })}`,
    );
  },

  performance(start: string, end: string) {
    return request<Performance>(`/performance${qs({ start, end })}`);
  },

  requests(
    start: string,
    end: string,
    opts?: {
      page?: number;
      limit?: number;
      model_alias?: string;
      user_id?: string;
      status_code?: number;
    },
  ) {
    return request<PaginatedRequests>(
      `/requests${qs({ start, end, ...opts })}`,
    );
  },

  requestDetail(requestId: string) {
    return request<RequestLog>(`/requests/${encodeURIComponent(requestId)}`);
  },

  requestByTrace(traceId: string) {
    return request<RequestLog>(
      `/requests/by-trace/${encodeURIComponent(traceId)}`,
    );
  },

  toolCalls(requestId: string) {
    return request<ToolCallLog[]>(
      `/requests/${encodeURIComponent(requestId)}/tool-calls`,
    );
  },

  sessionCost(sessionId: string) {
    return request<SessionCost>(`/sessions/${encodeURIComponent(sessionId)}`);
  },

  exportCsvUrl(start: string, end: string) {
    return `${BASE}/export.csv${qs({ start, end })}`;
  },

  listPricing() {
    return request<PricingEntry[]>("/pricing");
  },

  modelNames(q = "") {
    return request<string[]>(`/model-names${qs({ q })}`);
  },

  providerNames(q = "") {
    return request<string[]>(`/provider-names${qs({ q })}`);
  },

  createPricing(data: PricingCreate) {
    return request<PricingEntry>("/pricing", {
      method: "POST",
      body: JSON.stringify(data),
    });
  },

  updatePricing(id: string, data: PricingUpdate) {
    return request<PricingEntry>(`/pricing/${encodeURIComponent(id)}`, {
      method: "PUT",
      body: JSON.stringify(data),
    });
  },

  deactivatePricing(id: string) {
    return request<void>(`/pricing/${encodeURIComponent(id)}`, {
      method: "DELETE",
    });
  },

  pricingHistory(provider: string, model: string) {
    return request<PricingEntry[]>(
      `/pricing/${encodeURIComponent(provider)}/${encodeURIComponent(model)}/history`,
    );
  },
};
