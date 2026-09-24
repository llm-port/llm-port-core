/**
 * Inference API client (Phase 6, WI-4).
 *
 * Mirrors `app/api/nodes.ts`: the same `request<T>` helper, the same exported
 * object shape. Everything here is backend-neutral by contract -- the driver
 * abstraction on the server means no Ray-specific field reaches this file, so
 * a future driver needs no frontend change.
 *
 * The legacy Runtime API (`app/api/llm.ts`) stays exactly as it is: native
 * runtimes and inference environments are separate models and separate screens.
 */

// The one type shared with the legacy client: the stat-card payload. Both
// pages render the same cards, so they must agree on the shape, and
// duplicating it is how they would stop agreeing.
import type { RuntimeMonitoring } from "~/api/llm";

const BASE = "/api/inference";

// ---------------------------------------------------------------------------
// Control planes
// ---------------------------------------------------------------------------

export interface ControlPlane {
  id: string;
  name: string;
  driver: string;
  description: string | null;
  status: string;
  config: Record<string, unknown>;
  observed_status: Record<string, unknown>;
  credential_ref: string | null;
  generation: number;
  observed_generation: number;
  status_message: string | null;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface ControlPlaneCreatePayload {
  name: string;
  driver: string;
  description?: string | null;
  config?: Record<string, unknown> | null;
  credential_ref?: string | null;
  enabled?: boolean;
}

export interface ControlPlaneUpdatePayload {
  name?: string;
  description?: string | null;
  config?: Record<string, unknown> | null;
  credential_ref?: string | null;
  enabled?: boolean;
}

// ---------------------------------------------------------------------------
// Environments
// ---------------------------------------------------------------------------

export interface InferenceEnvironment {
  id: string;
  control_plane_id: string;
  name: string;
  description: string | null;
  status: string;
  desired_state: string;
  runtime_version: string | null;
  head_node_id: string | null;
  address: string | null;
  config: Record<string, unknown>;
  capabilities: Record<string, unknown>;
  observed_status: Record<string, unknown>;
  generation: number;
  observed_generation: number;
  status_message: string | null;
  created_at: string;
  updated_at: string;
  /** Latest progress a member machine reported while the cluster comes up. */
  progress?: EnvironmentProgress | null;
}

/** One progress report from a lifecycle step on a member machine. */
export interface MachineProgress {
  message: string;
  progress_pct: number | null;
  step: string;
  node_id: string;
  at: string | null;
}

/** The newest report overall, plus the newest from each machine. */
export interface EnvironmentProgress extends MachineProgress {
  machines?: MachineProgress[];
}

export interface EnvironmentCreatePayload {
  control_plane_id: string;
  name: string;
  description?: string | null;
  runtime_version?: string | null;
  head_node_id?: string | null;
  address?: string | null;
  config?: Record<string, unknown> | null;
}

export interface EnvironmentUpdatePayload {
  description?: string | null;
  /** "running" | "stopped" -- omit to keep the current desired state. */
  desired_state?: string;
  runtime_version?: string | null;
  head_node_id?: string | null;
  address?: string | null;
  config?: Record<string, unknown> | null;
}

/**
 * One node's observed status inside an environment.
 *
 * The driver writes these under `observed_status.members`, so every field
 * beyond the node id is optional.
 */
export interface EnvironmentMember {
  node_id: string;
  role?: string | null;
  status?: string | null;
  message?: string | null;
  address?: string | null;
  [key: string]: unknown;
}

/** A node registered as an environment member, with what reconcile observed. */
export interface EnvironmentNode {
  node_id: string;
  role: string;
  member_status: string | null;
  /** Which compatibility class this machine landed in. Derived, never asked for. */
  compute_pool_id: string | null;
  observed: Record<string, unknown>;
  joined_at: string | null;
}

/**
 * A group of machines that are interchangeable to the scheduler.
 *
 * Derived from what the nodes report, so a single-vendor cluster has exactly
 * one and nobody ever configures it. It only becomes visible when a cluster
 * mixes accelerators -- which is the case this exists for.
 */
export interface ComputePool {
  id: string;
  environment_id: string;
  name: string;
  /** `nvidia/aarch64/gb10`. Matching is on this, never on the name. */
  signature: string;
  accelerator_vendor: string;
  accelerator_family: string | null;
  cpu_architecture: string;
  labels: Record<string, unknown>;
  managed: boolean;
  member_count: number;
}

export interface EnvironmentCondition {
  type?: string;
  status?: string;
  reason?: string;
  message?: string;
  last_transition_at?: string;
  [key: string]: unknown;
}

// ---------------------------------------------------------------------------
// Fabric planning
// ---------------------------------------------------------------------------

export interface NodeFabricBinding {
  node_id: string;
  interface: string;
  ip: string;
  netmask?: string | null;
  speed_gbps?: number | null;
  speed_mbps?: number | null;
  link_type: string;
  rdma_device?: string | null;
  pci_address?: string | null;
  mtu: number;
  is_management: boolean;
  is_up: boolean;
}

export interface FabricValidation {
  performed: boolean;
  reachable: boolean | null;
  method: string;
  listener_node_id: string | null;
  probe_results: Record<string, unknown>[];
  detail: string;
}

export interface FabricCandidate {
  candidate_id: string;
  fabric_type: string;
  cidr: string;
  speed_gbps: number;
  mtu: number;
  is_management: boolean;
  isolation_level: string;
  confidence: string;
  score: number;
  recommended: boolean;
  recommendation_reason: string;
  reasons: string[];
  node_bindings: Record<string, NodeFabricBinding>;
  validation: FabricValidation;
}

export interface RejectedFabric {
  cidr: string;
  reason: string;
  interfaces: string[];
}

export interface RuntimeReadiness {
  /** The one image every member runs, or null on a mixed-platform cluster. */
  bundle_id: string | null;
  resolved: boolean;
  compatible: boolean | null;
  node_results: Record<string, string>;
  /** node id -> the image certified for that machine's platform. */
  node_bundles: Record<string, string>;
  detail: string;
}

export interface EnvironmentPlan {
  plan_id: string;
  environment_id: string;
  created_at: string;
  inventory_revisions: Record<string, string>;
  candidates: FabricCandidate[];
  rejected: RejectedFabric[];
  recommended_candidate_id: string | null;
  recommended_head_node_id: string | null;
  head_selection_reason: string;
  runtime: RuntimeReadiness;
  warnings: string[];
  blockers: string[];
}

export interface ApplyPlanPayload {
  /**
   * The approved plan, sent back as an approval receipt. The server re-derives
   * the plan from live inventory and uses this only to reject an approval made
   * against a topology that has since moved.
   */
  plan?: EnvironmentPlan | null;
  selected_candidate_id?: string | null;
}

/**
 * A certified runtime image a cluster can be pinned to.
 *
 * Unpinned, the Ray driver falls back to a host-installed Ray, which a
 * certified node does not have -- the cluster then fails to start.
 */
export interface RuntimeBundle {
  bundle_id: string;
  display_name: string;
  description: string;
  image: string;
  cpu_architecture: string;
  accelerator_vendor: string;
  runtime_version: string;
  vllm_version: string;
  certification_status: string;
  compatible_node_ids: string[];
  /** node id -> why this bundle will not run there. */
  incompatible: Record<string, string>;
}

// ---------------------------------------------------------------------------
// Model artifacts
// ---------------------------------------------------------------------------

export interface ArtifactReadiness {
  model_id: string;
  desired_revision: string | null;
  manifest_sha256: string | null;
  ready_node_ids: string[];
  pending_node_ids: string[];
  failed_node_ids: string[];
  blockers: string[];
  /** node_id -> host root path of the synced snapshot. */
  root_paths: Record<string, string>;
  all_ready: boolean;
}

// ---------------------------------------------------------------------------
// Deployments
// ---------------------------------------------------------------------------

export interface InferenceDeployment {
  id: string;
  environment_id: string;
  model_id: string;
  name: string;
  description: string | null;
  spec: Record<string, unknown>;
  desired_state: string;
  phase: string;
  generation: number;
  observed_generation: number;
  observed_status: Record<string, unknown>;
  phase_message: string | null;
  ready_replicas: number;
  total_replicas: number;
  created_at: string;
  updated_at: string;
}

export interface DeploymentCreatePayload {
  environment_id: string;
  model_id: string;
  name: string;
  spec: Record<string, unknown>;
  description?: string | null;
}

export interface DeploymentUpdatePayload {
  description?: string | null;
  spec?: Record<string, unknown> | null;
  /** "active" | "stopped" | "deleted" -- omit to keep the current state. */
  desired_state?: string;
}

export interface InferenceEndpoint {
  id: string;
  deployment_id: string;
  name: string;
  path: string;
  address: string;
  status: string;
  status_message: string | null;
  published: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

// ---------------------------------------------------------------------------
// Observability (mirrors the WI-1 DTOs)
// ---------------------------------------------------------------------------

export type LogSource = "runtime_container" | "serve_replica" | "agent";

export interface LogLine {
  ts: string | null;
  level: string | null;
  message: string;
}

export interface LogPage {
  source: LogSource;
  node_id: string | null;
  replica_id: string | null;
  lines: LogLine[];
  truncated: boolean;
  next_cursor: string | null;
  /** Why a page is empty. "Nothing logged yet" and "node unreachable" differ. */
  detail: string | null;
}

export interface LogQuery {
  source?: LogSource;
  node_id?: string;
  replica_id?: string;
  tail?: number;
  since?: string;
}

export interface ReplicaMetrics {
  deployment_name: string;
  status: string | null;
  replicas_ready: number;
  replicas_pending: number;
  message: string | null;
}

export interface ScrapeTarget {
  node_id: string | null;
  address: string;
  port: number;
  url: string;
}

/** A tier that could not be fully reported, and why. Never rendered as zero. */
export interface MetricsPartial {
  tier: string;
  reason: string;
  /**
   * How much the reader should care. Optional so an older backend, which
   * sends none, keeps the previous behaviour of treating everything as a
   * warning.
   */
  severity?: "info" | "warning";
}

/**
 * What the gateway measured for this deployment, over a recent window.
 *
 * Measured at the front door rather than asked of the cluster, so it is the
 * one tier that still answers when Ray is unreachable or Prometheus is down.
 * Everything that can honestly be unknown is nullable: a percentile over no
 * requests is not zero, and neither is an error rate.
 */
export interface GatewayTraffic {
  window_sec: number;
  requests: number;
  errors: number;
  error_rate: number | null;
  p50_ttft_ms: number | null;
  p95_ttft_ms: number | null;
  p50_latency_ms: number | null;
  p95_latency_ms: number | null;
  prompt_tokens: number;
  completion_tokens: number;
  /** Tokens per second *while generating* — the hardware's speed. */
  output_tokens_per_sec: number | null;
  last_request_at: string | null;
}

export interface DeploymentMetrics {
  deployment_id: string;
  app_name: string | null;
  app_status: string | null;
  replicas_ready: number;
  replicas_total: number;
  deployments: ReplicaMetrics[];
  scrape_targets: ScrapeTarget[];
  /** null when the gateway has no instance for this deployment at all. */
  traffic: GatewayTraffic | null;
  partials: MetricsPartial[];
  observed_at: string | null;
}

export interface EnvironmentMetrics {
  environment_id: string;
  alive: boolean;
  /** Where these numbers are drawn, or null when monitoring is switched off. */
  dashboard_url: string | null;
  version: string | null;
  nodes_total: number;
  nodes_alive: number;
  gpus_total: number;
  gpus_available: number;
  cpus_total: number;
  cpus_available: number;
  scrape_targets: ScrapeTarget[];
  partials: MetricsPartial[];
  raw: Record<string, unknown>;
  observed_at: string | null;
}

// ---------------------------------------------------------------------------
// vLLM the machines already run, found by their agents (Phase 8)
// ---------------------------------------------------------------------------

/** One vLLM container as the machine's agent described it. */
export interface FoundContainer {
  name: string;
  id: string;
  image: string;
  state: string;
  started_at: string | null;
  finished_at: string | null;
  model: string | null;
  served_model_names: string[];
  port: number;
  host_port: number | null;
  /** "chat" | "embeddings" | "scoring", or null when nothing says. */
  task: string | null;
  /** Whether the task came from its flags or was guessed from the model's name. */
  task_from: "flags" | "name" | null;
  api_key_required: boolean;
  /** The tool that started it, as its labels say: `compose:<project>`, `spark`, ... */
  managed_by: string | null;
  gpus: string | null;
  settings: Record<string, unknown>;
  args: string[];
}

export interface FoundAdoption {
  id: string;
  node_id: string | null;
  container: string;
  alias: string;
  served_model_name: string;
  base_url: string;
  task: string | null;
  state: string;
  container_state: string | null;
  provider_id: string | null;
  routed_at: string | null;
  released_at: string | null;
}

export interface FoundEntry {
  node: { id: string; name: string; host: string | null; status: string };
  reported_at: string | null;
  container: FoundContainer;
  base_url: string | null;
  adoption: FoundAdoption | null;
  can_route: boolean;
  reason: string | null;
  check: { ok: boolean; models: string[]; needs_key: boolean; error: string | null } | null;
}

// ---------------------------------------------------------------------------
// Clusters the machines still run that this server does not manage
// ---------------------------------------------------------------------------

/** A machine in a found cluster, matched to this fleet by its address. */
export interface FoundClusterMember {
  ip: string | null;
  hostname: string | null;
  role: "head" | "worker";
  gpus: number | null;
  runtime_node_id: string | null;
  /** The machine in this fleet, or null when none has that address. */
  node_id: string | null;
  name: string | null;
}

/** A model a found cluster serves, as it would be taken over. */
export interface FoundClusterApp {
  app_name: string;
  deployment_id: string;
  model_id: string;
  model_source: string;
  hf_repo_id: string | null;
  copies: number;
  gpus_per_copy: number | null;
  engine: Record<string, unknown>;
  status: string | null;
  running_copies: number;
  suggested_alias: string;
  /** What could not be carried over, in words. */
  notes: string[];
  already_known: boolean;
}

export interface FoundCluster {
  address: string | null;
  runtime_version: string | null;
  image: string | null;
  head: FoundClusterMember | null;
  members: FoundClusterMember[];
  apps: FoundClusterApp[];
  /** Apps LLM.Port did not deploy; they keep running and are not managed. */
  other_apps: string[];
  can_take_over: boolean;
  /** Why it cannot be taken over, in words. */
  blockers: string[];
  errors: string[];
  /** The machine that described it: the one to take it over through. */
  described_by: string;
}

export interface FoundClusters {
  clusters: FoundCluster[];
  /** Machines that run the runtime but could not say what. */
  unreadable: { node_id: string; name: string; error: string }[];
}

export interface TakeOverPayload {
  node_id: string;
  name: string;
  /** The gateway name for each model, by app name. */
  aliases: Record<string, string>;
}

export interface TakeOverResult {
  environment_id: string;
  name: string;
  control_plane: string;
  members: FoundClusterMember[];
  deployments: { deployment_id: string; name: string; alias: string; model: string; notes: string[] }[];
}

// ---------------------------------------------------------------------------
// Transport
// ---------------------------------------------------------------------------

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
    // FastAPI puts the human-readable message in `detail`; showing the raw
    // body meant operators read `API 409: {"detail":"Stale plan: ..."}`.
    let message = text;
    try {
      const parsed = JSON.parse(text) as { detail?: unknown };
      if (typeof parsed.detail === "string") message = parsed.detail;
    } catch {
      // Not JSON — keep the body as-is.
    }
    const error = new Error(message) as Error & { status?: number };
    error.status = res.status;
    throw error;
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

const enc = encodeURIComponent;

export const inferenceApi = {
  /**
   * Driver keys registered on the server.
   *
   * A control plane may name any driver, so this is what the UI offers rather
   * than what the server enforces: choosing from it avoids creating a control
   * plane whose driver answers 501 to every call.
   */
  listDrivers() {
    return request<string[]>("/drivers");
  },

  /** Certified runtime images, with compatibility against the given machines. */
  listRuntimeBundles(nodeIds: string[] = []) {
    const params = new URLSearchParams();
    for (const id of nodeIds) params.append("node_id", id);
    const suffix = params.toString() ? `?${params.toString()}` : "";
    return request<RuntimeBundle[]>(`/runtime-bundles${suffix}`);
  },

  // --- Control planes ------------------------------------------------------

  listControlPlanes() {
    return request<ControlPlane[]>("/control-planes");
  },

  getControlPlane(id: string) {
    return request<ControlPlane>(`/control-planes/${enc(id)}`);
  },

  createControlPlane(payload: ControlPlaneCreatePayload) {
    return request<ControlPlane>("/control-planes", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  updateControlPlane(id: string, payload: ControlPlaneUpdatePayload) {
    return request<ControlPlane>(`/control-planes/${enc(id)}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
  },

  deleteControlPlane(id: string) {
    return request<void>(`/control-planes/${enc(id)}`, { method: "DELETE" });
  },

  reconcileControlPlane(id: string) {
    return request<ControlPlane>(`/control-planes/${enc(id)}/reconcile`, {
      method: "POST",
    });
  },

  // --- Environments --------------------------------------------------------

  listEnvironments() {
    return request<InferenceEnvironment[]>("/environments");
  },

  getEnvironment(id: string) {
    return request<InferenceEnvironment>(`/environments/${enc(id)}`);
  },

  createEnvironment(payload: EnvironmentCreatePayload) {
    return request<InferenceEnvironment>("/environments", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  updateEnvironment(id: string, payload: EnvironmentUpdatePayload) {
    return request<InferenceEnvironment>(`/environments/${enc(id)}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
  },

  /** ``force`` deletes a running cluster whose machines are all offline. */
  deleteEnvironment(id: string, options: { force?: boolean } = {}) {
    const query = options.force ? "?force=true" : "";
    return request<void>(`/environments/${enc(id)}${query}`, { method: "DELETE" });
  },

  reconcileEnvironment(id: string) {
    return request<InferenceEnvironment>(`/environments/${enc(id)}/reconcile`, {
      method: "POST",
    });
  },

  listEnvironmentNodes(id: string) {
    return request<EnvironmentNode[]>(`/environments/${enc(id)}/nodes`);
  },

  listEnvironmentPools(id: string) {
    return request<ComputePool[]>(`/environments/${enc(id)}/pools`);
  },

  /** Desired state only: the reconciler converges on the next pass. */
  removeEnvironmentNode(id: string, nodeId: string) {
    return request<void>(
      `/environments/${enc(id)}/nodes/${enc(nodeId)}`,
      { method: "DELETE" },
    );
  },

  addEnvironmentNode(id: string, nodeId: string, role = "worker") {
    return request<InferenceEnvironment>(`/environments/${enc(id)}/nodes`, {
      method: "POST",
      body: JSON.stringify({ node_id: nodeId, role }),
    });
  },

  /**
   * Generate an interconnect plan. `validate` defaults to running the live
   * reachability challenge; pass `false` to plan from passive facts only.
   */
  planEnvironment(id: string, validate?: boolean) {
    const query = validate === undefined ? "" : `?validate=${validate}`;
    return request<EnvironmentPlan>(`/environments/${enc(id)}/plan${query}`, {
      method: "POST",
    });
  },

  applyEnvironmentPlan(id: string, payload: ApplyPlanPayload = {}) {
    return request<InferenceEnvironment>(
      `/environments/${enc(id)}/apply-plan`,
      { method: "POST", body: JSON.stringify(payload) },
    );
  },

  /** Read-only readiness: polling this never mutates availability state. */
  artifactReadiness(id: string, modelId: string) {
    return request<ArtifactReadiness>(
      `/environments/${enc(id)}/artifacts/${enc(modelId)}`,
    );
  },

  syncArtifact(id: string, modelId: string) {
    return request<ArtifactReadiness>(
      `/environments/${enc(id)}/artifacts/${enc(modelId)}/sync`,
      { method: "POST" },
    );
  },

  environmentMetrics(id: string) {
    return request<EnvironmentMetrics>(`/environments/${enc(id)}/metrics`);
  },

  // --- Deployments ---------------------------------------------------------

  listDeployments() {
    return request<InferenceDeployment[]>("/deployments");
  },

  getDeployment(id: string) {
    return request<InferenceDeployment>(`/deployments/${enc(id)}`);
  },

  createDeployment(payload: DeploymentCreatePayload) {
    return request<InferenceDeployment>("/deployments", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  updateDeployment(id: string, payload: DeploymentUpdatePayload) {
    return request<InferenceDeployment>(`/deployments/${enc(id)}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
  },

  deleteDeployment(id: string) {
    return request<void>(`/deployments/${enc(id)}`, { method: "DELETE" });
  },

  reconcileDeployment(id: string) {
    return request<InferenceDeployment>(`/deployments/${enc(id)}/reconcile`, {
      method: "POST",
    });
  },

  listEndpoints(id: string) {
    return request<InferenceEndpoint[]>(`/deployments/${enc(id)}/endpoints`);
  },

  deploymentLogs(id: string, query: LogQuery = {}) {
    const params = new URLSearchParams();
    if (query.source) params.set("source", query.source);
    if (query.node_id) params.set("node_id", query.node_id);
    if (query.replica_id) params.set("replica_id", query.replica_id);
    if (query.tail !== undefined) params.set("tail", String(query.tail));
    if (query.since) params.set("since", query.since);
    const suffix = params.toString() ? `?${params.toString()}` : "";
    return request<LogPage>(`/deployments/${enc(id)}/logs${suffix}`);
  },

  deploymentMetrics(id: string) {
    return request<DeploymentMetrics>(`/deployments/${enc(id)}/metrics`);
  },
  /**
   * The engine's live figures for a deployment, in the same shape the
   * providers page asks for — so one component renders both and the two
   * screens describe the same cluster identically.
   */
  deploymentMonitoringStats(id: string) {
    return request<RuntimeMonitoring>(
      `/deployments/${enc(id)}/monitoring-stats`,
    );
  },

  // -- vLLM the machines already run ---------------------------------------

  found(nodeId?: string, check = true) {
    const params = new URLSearchParams({ check: String(check) });
    if (nodeId) params.set("node_id", nodeId);
    return request<FoundEntry[]>(`/found?${params.toString()}`);
  },
  routeFound(nodeId: string, container: string, alias: string) {
    return request<FoundAdoption>("/found/route", {
      method: "POST",
      body: JSON.stringify({ node_id: nodeId, container, alias }),
    });
  },
  releaseFound(adoptionId: string) {
    return request<FoundAdoption>(`/found/${enc(adoptionId)}/release`, { method: "POST" });
  },

  // -- Clusters the machines run that this server lost --------------------

  /** Asks each machine that runs the runtime outside a cluster here; takes seconds. */
  foundClusters() {
    return request<FoundClusters>("/found-clusters");
  },
  takeOverCluster(payload: TakeOverPayload) {
    return request<TakeOverResult>("/found-clusters/take-over", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
};
