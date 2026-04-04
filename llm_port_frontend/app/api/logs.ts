const BASE = "/api/logs";

export type LogsDirection = "BACKWARD" | "FORWARD";

export interface LogsQueryParams {
  query: string;
  start?: string;
  end?: string;
  limit?: number;
  direction?: LogsDirection;
}

export interface LogEntry {
  ts: string;
  line: string;
  structured?: Record<string, unknown>;
}

export interface LogStream {
  labels: Record<string, string>;
  entries: LogEntry[];
}

export interface QueryRangeResponse {
  streams: LogStream[];
  stats?: Record<string, unknown>;
}

async function request<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    credentials: "include",
  });
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`API ${res.status}: ${text}`);
  }
  return res.json() as Promise<T>;
}

export const logsApi = {
  getLabels() {
    return request<{ labels: string[] }>("/labels");
  },
  getLabelValues(name: string) {
    return request<{ label: string; values: string[] }>(`/label/${encodeURIComponent(name)}/values`);
  },
  queryRange(params: LogsQueryParams) {
    const qs = new URLSearchParams();
    qs.set("query", params.query);
    if (params.start) qs.set("start", params.start);
    if (params.end) qs.set("end", params.end);
    if (params.limit !== undefined) qs.set("limit", String(params.limit));
    if (params.direction) qs.set("direction", params.direction);
    return request<QueryRangeResponse>(`/query_range?${qs.toString()}`);
  },
  tailSocketUrl(query: string): string {
    const protocol = window.location.protocol === "https:" ? "wss" : "ws";
    return `${protocol}://${window.location.host}${BASE}/tail?query=${encodeURIComponent(query)}`;
  },
};
