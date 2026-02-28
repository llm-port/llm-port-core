/**
 * LLM API client — typed fetch wrappers for all /api/llm endpoints.
 */

const BASE = "/api/llm";

// ─────────────────────────────────────────────────────────────────────────────
// Types (mirror backend schemas)
// ─────────────────────────────────────────────────────────────────────────────

export type ProviderType = "vllm" | "llamacpp" | "tgi" | "ollama";
export type ProviderTarget = "local_docker" | "remote_endpoint";
export type ModelSource = "huggingface" | "local_path" | "archive_import";
export type ModelStatus = "available" | "downloading" | "failed" | "deleting";
export type ArtifactFormat = "safetensors" | "gguf" | "other";
export type RuntimeStatus = "creating" | "starting" | "running" | "stopping" | "stopped" | "error";
export type DownloadJobStatus = "queued" | "running" | "success" | "failed" | "canceled";

export interface Provider {
  id: string;
  name: string;
  type: ProviderType;
  target: ProviderTarget;
  endpoint_url: string | null;
  capabilities: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}

export interface Model {
  id: string;
  display_name: string;
  source: ModelSource;
  hf_repo_id: string | null;
  hf_revision: string | null;
  license_ack_required: boolean;
  tags: string[] | null;
  status: ModelStatus;
  created_at: string;
  updated_at: string;
}

export interface Artifact {
  id: string;
  model_id: string;
  format: ArtifactFormat;
  path: string;
  size_bytes: number;
  sha256: string | null;
  engine_compat: string[] | null;
  created_at: string;
}

export interface Runtime {
  id: string;
  name: string;
  provider_id: string;
  model_id: string;
  status: RuntimeStatus;
  endpoint_url: string | null;
  openai_compat: boolean;
  generic_config: Record<string, unknown> | null;
  provider_config: Record<string, unknown> | null;
  container_ref: string | null;
  created_at: string;
  updated_at: string;
}

export interface RuntimeHealth {
  healthy: boolean;
  detail: string;
}

export interface DownloadJob {
  id: string;
  model_id: string;
  status: DownloadJobStatus;
  progress: number;
  log_ref: string | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
}

export interface HFTokenStatus {
  configured: boolean;
}

// ─────────────────────────────────────────────────────────────────────────────
// Request payloads
// ─────────────────────────────────────────────────────────────────────────────

export interface CreateProviderPayload {
  name: string;
  type: ProviderType;
  target?: ProviderTarget;
  endpoint_url?: string;
  api_key?: string;
}

export interface UpdateProviderPayload {
  name?: string;
  capabilities?: Record<string, unknown>;
  endpoint_url?: string;
  api_key?: string;
}

export interface DownloadModelPayload {
  hf_repo_id: string;
  hf_revision?: string;
  display_name?: string;
  tags?: string[];
}

export interface RegisterModelPayload {
  display_name: string;
  path: string;
  tags?: string[];
}

export interface DownloadResponse {
  model: Model;
  job: DownloadJob;
  dispatched: boolean;
  dispatch_error: string | null;
}

export interface CreateRuntimePayload {
  name: string;
  provider_id: string;
  model_id: string;
  generic_config?: Record<string, unknown>;
  provider_config?: Record<string, unknown>;
  openai_compat?: boolean;
}

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

async function request<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
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
// Providers
// ─────────────────────────────────────────────────────────────────────────────

export const providers = {
  list() {
    return request<Provider[]>("/providers/");
  },
  get(id: string) {
    return request<Provider>(`/providers/${id}`);
  },
  create(payload: CreateProviderPayload) {
    return request<Provider>("/providers/", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  update(id: string, payload: UpdateProviderPayload) {
    return request<Provider>(`/providers/${id}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
  },
  delete(id: string) {
    return request<void>(`/providers/${id}`, { method: "DELETE" });
  },
};

// ─────────────────────────────────────────────────────────────────────────────
// Models
// ─────────────────────────────────────────────────────────────────────────────

export const models = {
  list() {
    return request<Model[]>("/models/");
  },
  get(id: string) {
    return request<Model>(`/models/${id}`);
  },
  download(payload: DownloadModelPayload) {
    return request<DownloadResponse>("/models/download", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  register(payload: RegisterModelPayload) {
    return request<Model>("/models/register", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  delete(id: string) {
    return request<void>(`/models/${id}`, { method: "DELETE" });
  },
  artifacts(id: string) {
    return request<Artifact[]>(`/models/${id}/artifacts`);
  },
};

// ─────────────────────────────────────────────────────────────────────────────
// Runtimes
// ─────────────────────────────────────────────────────────────────────────────

export const runtimes = {
  list() {
    return request<Runtime[]>("/runtimes/");
  },
  get(id: string) {
    return request<Runtime>(`/runtimes/${id}`);
  },
  create(payload: CreateRuntimePayload) {
    return request<Runtime>("/runtimes/", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  start(id: string) {
    return request<Runtime>(`/runtimes/${id}/start`, { method: "POST" });
  },
  stop(id: string) {
    return request<Runtime>(`/runtimes/${id}/stop`, { method: "POST" });
  },
  restart(id: string) {
    return request<Runtime>(`/runtimes/${id}/restart`, { method: "POST" });
  },
  delete(id: string) {
    return request<void>(`/runtimes/${id}`, { method: "DELETE" });
  },
  health(id: string) {
    return request<RuntimeHealth>(`/runtimes/${id}/health`);
  },
  fetchLogs(id: string, tail = 200): Promise<Response> {
    return fetch(`${BASE}/runtimes/${id}/logs?tail=${tail}`, {
      credentials: "include",
    });
  },
};

// ─────────────────────────────────────────────────────────────────────────────
// Jobs
// ─────────────────────────────────────────────────────────────────────────────

export const jobs = {
  list(status?: DownloadJobStatus, modelId?: string) {
    const params = new URLSearchParams();
    if (status) params.set("status_filter", status);
    if (modelId) params.set("model_id", modelId);
    const qs = params.toString() ? `?${params}` : "";
    return request<DownloadJob[]>(`/jobs/${qs}`);
  },
  get(id: string) {
    return request<DownloadJob>(`/jobs/${id}`);
  },
  cancel(id: string) {
    return request<DownloadJob>(`/jobs/${id}/cancel`, { method: "POST" });
  },
  retry(id: string) {
    return request<DownloadJob>(`/jobs/${id}/retry`, { method: "POST" });
  },
};

// ─────────────────────────────────────────────────────────────────────────────
// Settings
// ─────────────────────────────────────────────────────────────────────────────

export const llmSettings = {
  getHFToken() {
    return request<HFTokenStatus>("/settings/hf-token");
  },
  setHFToken(token: string) {
    return request<HFTokenStatus>("/settings/hf-token", {
      method: "PUT",
      body: JSON.stringify({ token }),
    });
  },
};

// ─────────────────────────────────────────────────────────────────────────────
// HF Search
// ─────────────────────────────────────────────────────────────────────────────

export interface HFModelHit {
  id: string;
  downloads: number;
  likes: number;
  pipeline_tag: string | null;
}

export const search = {
  hfModels(q: string, limit = 10) {
    return request<HFModelHit[]>(
      `/search/hf-search?q=${encodeURIComponent(q)}&limit=${limit}`,
    );
  },
};
