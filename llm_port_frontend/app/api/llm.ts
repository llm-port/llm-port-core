/**
 * LLM API client — typed fetch wrappers for all /api/llm endpoints.
 */

const BASE = "/api/llm";

// ─────────────────────────────────────────────────────────────────────────────
// Types (mirror backend schemas)
// ─────────────────────────────────────────────────────────────────────────────

export type ProviderType = "vllm" | "llamacpp" | "tgi" | "ollama" | "cloud";
/**
 * Where a provider's engine runs.
 *
 * `inference_cluster` is served by one of our own deployments: it is started
 * by scaling that deployment, its logs are per-replica across machines, and
 * it has no container on this host to start or stop. The screen must not
 * offer controls that cannot work on it.
 */
export type ProviderTarget =
  | "local_docker"
  | "remote_endpoint"
  | "inference_cluster";

/** LiteLLM provider prefixes for remote endpoints. */
export type LiteLLMProvider =
  | "openai"
  | "anthropic"
  | "gemini"
  | "vertex_ai"
  | "bedrock"
  | "azure"
  | "azure_ai"
  | "mistral"
  | "groq"
  | "deepseek"
  | "cohere"
  | "openrouter"
  | string;
export type ModelSource =
  | "huggingface"
  | "local_path"
  | "archive_import"
  | "remote";
export type ModelStatus = "available" | "downloading" | "failed" | "deleting";
export type ArtifactFormat = "safetensors" | "gguf" | "other";
export type RuntimeStatus =
  | "creating"
  | "starting"
  | "running"
  | "stopping"
  | "stopped"
  | "error";
export type DownloadJobStatus =
  | "queued"
  | "running"
  | "success"
  | "failed"
  | "canceled";

/** The record that owns a derived provider. */
export interface ManagedBy {
  kind: string;
  id: string;
  name: string | null;
  /**
   * The owner's own state. A cluster-backed provider has no container of its
   * own, so this is the only honest thing to show in a status column.
   */
  state: string | null;
  /** What it serves: a derived provider has no runtime row to join through. */
  model_name: string | null;
  /** For a vLLM container found on a machine: the machine it runs on. */
  node_id?: string | null;
}

/**
 * Where an owned provider is managed: a deployment's page, or -- for a vLLM
 * container LLM.Port found and routes as it is -- the page of the machine
 * running it.
 */
export function ownerPath(owner: ManagedBy): string {
  if (owner.kind === "found_container") {
    return owner.node_id ? `/admin/nodes/${owner.node_id}` : "/admin/nodes";
  }
  return `/admin/deployments/${owner.id}`;
}

export interface Provider {
  id: string;
  name: string;
  type: ProviderType;
  target: ProviderTarget;
  endpoint_url: string | null;
  capabilities: Record<string, unknown> | null;
  remote_model: string | null;
  litellm_provider: string | null;
  litellm_model: string | null;
  extra_params: Record<string, unknown> | null;
  /**
   * What owns this provider, when something does. `"inference_deployment"`
   * means a deployment created it and will remove it with itself — it cannot
   * be edited or deleted from the providers screen, because the next
   * reconcile would undo it.
   */
  source_kind: string | null;
  /** The owning record's id: the deployment to send the operator to. */
  source_id: string | null;
  /** Resolved owner, when there is one: what to call it and how it is doing. */
  managed_by: ManagedBy | null;
  created_at: string;
  updated_at: string;
}

/** Whether a deployment owns this provider rather than a person. */
export function isDerivedProvider(provider: Provider): boolean {
  return Boolean(provider.source_kind);
}

export interface ModelInstance {
  runtime_id: string;
  runtime_name: string;
  runtime_status: RuntimeStatus;
  provider_id: string;
  provider_name: string;
  provider_type: ProviderType;
  execution_target: string;
  node_id: string | null;
  node_host: string | null;
  monitoring_url: string | null;
}

// ── Runtime monitoring (stat cards + Grafana deep-link) ────────────────────

/** Stat keys exposed by /runtimes/{id}/monitoring-stats (vLLM engine). */
export type StatKey =
  | "running_requests"
  | "waiting_requests"
  | "kv_cache_usage"
  | "prefix_cache_hit_rate"
  | "mtp_acceptance"
  | "generation_tokens_per_sec"
  | "preemption_rate";

export interface RuntimeMonitoring {
  enabled: boolean;
  /** True when Prometheus has no fresh data for this runtime. */
  stale: boolean;
  dashboard_url: string | null;
  /** Stat-keyed values; null for individual absent series (e.g. no MTP). */
  stats: Partial<Record<StatKey, number>>;
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
  instances: ModelInstance[];
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
  execution_target: string;
  assigned_node_id: string | null;
  desired_state: string;
  placement_explain_json: Record<string, unknown> | null;
  last_command_id: string | null;
  status_message: string | null;
  created_at: string;
  updated_at: string;
  monitoring: RuntimeMonitoring | null;
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

/** Whether this server has a Hugging Face token -- never the token itself. */
export interface HFTokenStatus {
  configured: boolean;
  /** "database": set in LLM.port; "environment": LLM_PORT_BACKEND_HF_TOKEN. */
  source: "database" | "environment" | null;
  /** False while the settings master key is the published default. */
  storage_safe: boolean;
  /** What Hugging Face said about it. */
  check: "ok" | "invalid" | "offline" | null;
  username: string | null;
  token_name: string | null;
  /** "read", "write" or "fineGrained". */
  role: string | null;
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
  remote_model?: string;
  litellm_provider?: string;
  litellm_model?: string;
  extra_params?: Record<string, unknown>;
}

export interface UpdateProviderPayload {
  name?: string;
  capabilities?: Record<string, unknown>;
  endpoint_url?: string;
  api_key?: string;
  remote_model?: string | null;
  litellm_provider?: string | null;
  litellm_model?: string | null;
  extra_params?: Record<string, unknown> | null;
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

export interface ScanLocalResult {
  imported_count: number;
  imported: Model[];
}

export interface DownloadResponse {
  model: Model;
  /** Null when the repo is already kept and available: nothing to fetch. */
  job: DownloadJob | null;
  dispatched: boolean;
  dispatch_error: string | null;
  /** The request was answered with a model that already existed. */
  already_kept?: boolean;
}

export interface CreateRuntimePayload {
  name: string;
  provider_id: string;
  model_id: string;
  generic_config?: Record<string, unknown>;
  provider_config?: Record<string, unknown>;
  openai_compat?: boolean;
  target_node_id?: string;
  placement_hints?: Record<string, unknown>;
  /** How the model reaches the remote node: sync from server or download from HF. */
  model_source?: "sync_from_server" | "download_from_hf";
  /** How the container image reaches the remote node. */
  image_source?: "pull_from_registry" | "transfer_from_server";
}

export interface UpdateRuntimePayload {
  name?: string;
  generic_config?: Record<string, unknown> | null;
  provider_config?: Record<string, unknown> | null;
  openai_compat?: boolean;
  target_node_id?: string;
  placement_hints?: Record<string, unknown>;
}

export interface TestEndpointPayload {
  endpoint_url?: string;
  api_key?: string;
  litellm_provider?: string;
  litellm_model?: string;
}

export interface TestEndpointResult {
  compatible: boolean;
  models: string[];
  error: string | null;
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
// Providers
// ─────────────────────────────────────────────────────────────────────────────

export const providers = {
  list() {
    return request<Provider[]>("/providers/");
  },
  /**
   * Live stat-card values and a dashboard link for a provider of either kind.
   *
   * One call whether the model runs in a local container or across a cluster:
   * the screen showing these cards does not care, and a person whose role
   * reaches this page but not the deployments page still gets the figures
   * rather than a link somewhere they cannot go.
   *
   * Returns `{ enabled: false }` when there is nothing to show — a muted
   * state, not an error.
   */
  monitoringStats(id: string) {
    return request<RuntimeMonitoring>(`/providers/${id}/monitoring-stats`);
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
  testEndpoint(payload: TestEndpointPayload) {
    return request<TestEndpointResult>("/providers/test-endpoint", {
      method: "POST",
      body: JSON.stringify(payload),
    });
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
  /** ``files``: also remove a downloaded model's files from this server's store. */
  delete(id: string, options: { files?: boolean } = {}) {
    return request<void>(`/models/${id}${options.files ? "?files=true" : ""}`, { method: "DELETE" });
  },
  artifacts(id: string) {
    return request<Artifact[]>(`/models/${id}/artifacts`);
  },
  scanLocal() {
    return request<ScanLocalResult>("/models/scan-local", {
      method: "POST",
    });
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
  update(id: string, payload: UpdateRuntimePayload) {
    return request<Runtime>(`/runtimes/${id}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
  },
  delete(id: string) {
    return request<void>(`/runtimes/${id}`, { method: "DELETE" });
  },
  health(id: string) {
    return request<RuntimeHealth>(`/runtimes/${id}/health`);
  },
  /**
   * Live stat-card values + dashboard URL (backend proxies Prometheus).
   * Returns `{ enabled: false, stats: {} }` for non-scraped runtimes / when
   * monitoring is disabled — treat as disabled, not as an error.
   */
  monitoringStats(id: string) {
    return request<RuntimeMonitoring>(`/runtimes/${id}/monitoring-stats`);
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
  removeHFToken() {
    return request<HFTokenStatus>("/settings/hf-token", { method: "DELETE" });
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
