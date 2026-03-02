/**
 * LLM Graph API client.
 */

const BASE = "/api/llm/graph";

export type GraphNodeType = "provider" | "runtime" | "model" | "trace";

export interface GraphNode {
  id: string;
  type: GraphNodeType | string;
  label: string;
  status: string | null;
  meta: Record<string, unknown> | null;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  type: string;
}

export interface TopologyResponse {
  generated_at: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface TraceEvent {
  event_id: number;
  ts: string;
  request_id: string;
  trace_id: string | null;
  tenant_id: string;
  user_id: string;
  model_alias: string | null;
  provider_instance_id: string | null;
  status: number;
  latency_ms: number;
  ttft_ms: number | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  total_tokens: number | null;
  error_code: string | null;
}

export interface TraceSnapshotResponse {
  items: TraceEvent[];
  next_cursor: string | null;
}

export interface DataUsagePerInstance {
  provider_instance_id: string;
  total_requests: number;
  total_prompt_tokens: number;
  total_completion_tokens: number;
  total_tokens: number;
  error_count: number;
}

export interface DataUsageSummary {
  generated_at: string;
  instances: DataUsagePerInstance[];
  grand_total_requests: number;
  grand_total_tokens: number;
}

async function request<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    credentials: "include",
    headers: { "Content-Type": "application/json" },
  });
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`API ${res.status}: ${text}`);
  }
  return res.json() as Promise<T>;
}

export function getLlmGraphTopology(): Promise<TopologyResponse> {
  return request<TopologyResponse>("/topology");
}

export function getLlmGraphTraces(
  limit = 100,
  afterEventId?: number,
): Promise<TraceSnapshotResponse> {
  const params = new URLSearchParams();
  params.set("limit", String(limit));
  if (afterEventId !== undefined) {
    params.set("after_event_id", String(afterEventId));
  }
  return request<TraceSnapshotResponse>(`/traces?${params.toString()}`);
}

export function getLlmDataUsage(): Promise<DataUsageSummary> {
  return request<DataUsageSummary>("/data-usage");
}

export function openLlmGraphTraceStream(
  onEvent: (event: TraceEvent) => void,
  onError: (error: Event) => void,
  cursor?: number,
): EventSource {
  const params = new URLSearchParams();
  if (cursor !== undefined) {
    params.set("cursor", String(cursor));
  }
  const url = `${BASE}/traces/stream${params.toString() ? `?${params.toString()}` : ""}`;
  const source = new EventSource(url, { withCredentials: true });
  source.addEventListener("trace", (raw) => {
    const data = JSON.parse((raw as MessageEvent<string>).data) as TraceEvent;
    onEvent(data);
  });
  source.addEventListener("ping", () => {
    // keep-alive event
  });
  source.onerror = onError;
  return source;
}
