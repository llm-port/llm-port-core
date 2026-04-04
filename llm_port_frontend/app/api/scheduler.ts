/**
 * Scheduler API client — unified background job management.
 */

const BASE = "/api/admin/scheduler";

// ─────────────────────────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────────────────────────

export type JobStatus =
  | "queued"
  | "running"
  | "success"
  | "failed"
  | "canceled";
export type JobType = "model_download" | "rag_ingest";

export interface UnifiedJob {
  id: string;
  job_type: JobType;
  status: JobStatus;
  label: string;
  progress: number; // 0-100 or -1 for indeterminate
  error_message: string | null;
  created_at: string;
  updated_at: string;
  meta: Record<string, unknown> | null;
}

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init.headers ?? {}),
    },
    credentials: "include",
  });
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`API ${res.status}: ${text}`);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

// ─────────────────────────────────────────────────────────────────────────────
// API
// ─────────────────────────────────────────────────────────────────────────────

export const scheduler = {
  list(jobType?: JobType, status?: JobStatus) {
    const params = new URLSearchParams();
    if (jobType) params.set("job_type", jobType);
    if (status) params.set("status_filter", status);
    const qs = params.toString() ? `?${params}` : "";
    return request<UnifiedJob[]>(`/jobs${qs}`);
  },
  cancel(id: string) {
    return request<UnifiedJob>(`/jobs/${id}/cancel`, { method: "POST" });
  },
  retry(id: string) {
    return request<UnifiedJob>(`/jobs/${id}/retry`, { method: "POST" });
  },
};
