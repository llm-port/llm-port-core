/**
 * Admin API client — thin fetch wrappers for all /api/admin endpoints.
 *
 * The backend is the authority on capabilities; the UI reads allowed actions
 * from the container's container_class and the root-mode status, exactly
 * mirroring the server-side matrix.
 */

const BASE = "/api/admin";

// ─────────────────────────────────────────────────────────────────────────────
// Types (mirror backend schemas)
// ─────────────────────────────────────────────────────────────────────────────

export type ContainerClass = "SYSTEM_CORE" | "SYSTEM_AUX" | "TENANT_APP" | "UNTRUSTED";
export type ContainerPolicy = "locked" | "restricted" | "free";

export interface ContainerSummary {
  id: string;
  name: string;
  image: string;
  status: string;
  state: string;
  created: string;
  ports: Record<string, unknown>[];
  networks: string[];
  container_class: ContainerClass;
  policy: ContainerPolicy;
  owner_scope: string;
}

export interface ContainerDetail extends ContainerSummary {
  raw: Record<string, unknown>;
}

export interface PortBinding {
  host_port: string;
  container_port: string;
}

export interface CreateContainerPayload {
  image: string;
  name?: string;
  container_class?: ContainerClass;
  owner_scope?: string;
  policy?: ContainerPolicy;
  auto_start?: boolean;
  ports?: PortBinding[];
  env?: string[];
  cmd?: string[];
  network?: string;
  volumes?: string[];
}

export interface ExecToken {
  exec_id: string;
}

export interface ImageSummary {
  id: string;
  repo_tags: string[];
  repo_digests: string[];
  size: number;
  created: string;
}

export interface PruneReport {
  deleted: string[];
  space_reclaimed: number;
  dry_run: boolean;
}

export interface StackRevision {
  id: string;
  stack_id: string;
  rev: number;
  compose_yaml: string;
  env_blob: string | null;
  image_digests: string | null;
  created_by: string | null;
  created_at: string;
}

export interface StackSummary {
  stack_id: string;
  latest_rev: number;
  created_at: string;
}

export interface StackDiff {
  stack_id: string;
  from_rev: number;
  to_rev: number;
  compose_yaml_from: string;
  compose_yaml_to: string;
  env_blob_from: string | null;
  env_blob_to: string | null;
  image_digests_from: string | null;
  image_digests_to: string | null;
}

export interface RootSession {
  id: string;
  actor_id: string;
  start_time: string;
  end_time: string | null;
  reason: string;
  scope: string;
  duration_seconds: number;
  active: boolean;
}

export interface RootModeStatus {
  active: boolean;
  session: RootSession | null;
}

export interface AuditEvent {
  id: string;
  time: string;
  actor_id: string | null;
  action: string;
  target_type: string;
  target_id: string;
  result: "allow" | "deny";
  severity: string;
  metadata_json: string | null;
}

export interface DashboardTopUser {
  container_id: string;
  name: string;
  value: number;
  unit: string;
}

export interface DashboardOverview {
  system_status: string;
  system_badge: string;

  cpu_percent: number | null;
  load_1m: number | null;
  load_5m: number | null;
  load_15m: number | null;

  ram_used_bytes: number | null;
  ram_total_bytes: number | null;
  swap_used_bytes: number | null;
  swap_total_bytes: number | null;

  disk_free_bytes: number;
  disk_total_bytes: number;
  disk_free_percent: number;

  network_rx_bytes: number | null;
  network_tx_bytes: number | null;

  containers_running: number;
  containers_total: number;
  containers_restarting: number;
  restart_rate_1h: number;
  restart_rate_24h: number;

  api_error_rate_5xx: number;

  postgres_connections: number | null;
  postgres_max_connections: number | null;

  gpu_util_percent: number | null;
  gpu_vram_used_bytes: number | null;
  gpu_vram_total_bytes: number | null;

  top_cpu_containers: DashboardTopUser[];
  top_memory_containers: DashboardTopUser[];
}

export interface DashboardHealthItem {
  name: string;
  status: string;
  detail: string | null;
}

export interface DashboardHealth {
  overall_status: string;
  items: DashboardHealthItem[];
}

export interface GrafanaPanel {
  panel_id: number;
  title: string;
  embed_url: string;
}

export interface GrafanaPanels {
  enabled: boolean;
  grafana_url: string | null;
  dashboard_uid: string | null;
  open_dashboard_url: string | null;
  panels: GrafanaPanel[];
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
// Containers
// ─────────────────────────────────────────────────────────────────────────────

export const containers = {
  list(filterClass?: ContainerClass) {
    const qs = filterClass ? `?class=${filterClass}` : "";
    return request<ContainerSummary[]>(`/containers/${qs}`);
  },
  create(payload: CreateContainerPayload) {
    return request<ContainerSummary>("/containers/", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  get(id: string) {
    return request<ContainerDetail>(`/containers/${id}`);
  },
  lifecycle(id: string, action: "start" | "stop" | "restart" | "pause" | "unpause") {
    return request<void>(`/containers/${id}/${action}`, { method: "POST" });
  },
  logs(id: string, tail = 200): EventSource {
    return new EventSource(`${BASE}/containers/${id}/logs?tail=${tail}`, {
      withCredentials: true,
    });
  },
  fetchLogs(id: string, tail = 200): Promise<Response> {
    return fetch(`${BASE}/containers/${id}/logs?tail=${tail}`, {
      credentials: "include",
    });
  },
  exec(id: string, cmd: string[], workdir = "/") {
    return request<ExecToken>(`/containers/${id}/exec`, {
      method: "POST",
      body: JSON.stringify({ cmd, workdir }),
    });
  },
  delete(id: string, force = false) {
    return request<void>(`/containers/${id}?force=${force}`, { method: "DELETE" });
  },
  register(
    id: string,
    classification: {
      container_class: ContainerClass;
      owner_scope: string;
      policy: ContainerPolicy;
    },
  ) {
    return request<ContainerSummary>(`/containers/${id}/register`, {
      method: "PUT",
      body: JSON.stringify(classification),
    });
  },
};

// ─────────────────────────────────────────────────────────────────────────────
// Images
// ─────────────────────────────────────────────────────────────────────────────

export const images = {
  list() {
    return request<ImageSummary[]>("/images/");
  },
  pull(image: string, tag = "latest") {
    return request<void>("/images/pull", {
      method: "POST",
      body: JSON.stringify({ image, tag }),
    });
  },
  prune(dry_run = false) {
    return request<PruneReport>("/images/prune", {
      method: "POST",
      body: JSON.stringify({ dry_run }),
    });
  },
};

// ─────────────────────────────────────────────────────────────────────────────
// Stacks
// ─────────────────────────────────────────────────────────────────────────────

export const stacks = {
  list() {
    return request<StackSummary[]>("/stacks/");
  },
  deploy(payload: {
    stack_id: string;
    compose_yaml: string;
    env_blob?: string | null;
    image_digests?: string | null;
  }) {
    return request<StackRevision>("/stacks/deploy", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  update(stackId: string, payload: {
    stack_id: string;
    compose_yaml: string;
    env_blob?: string | null;
    image_digests?: string | null;
  }) {
    return request<StackRevision>(`/stacks/${stackId}/update`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  rollback(stackId: string, rev: number) {
    return request<StackRevision>(`/stacks/${stackId}/rollback`, {
      method: "POST",
      body: JSON.stringify({ rev }),
    });
  },
  revisions(stackId: string) {
    return request<StackRevision[]>(`/stacks/${stackId}/revisions`);
  },
  diff(stackId: string, fromRev: number, toRev: number) {
    return request<StackDiff>(`/stacks/${stackId}/diff?from_rev=${fromRev}&to_rev=${toRev}`);
  },
};

// ─────────────────────────────────────────────────────────────────────────────
// Root Mode
// ─────────────────────────────────────────────────────────────────────────────

export const rootMode = {
  status() {
    return request<RootModeStatus>("/root-mode/status");
  },
  start(reason: string, scope = "all", duration_seconds = 600) {
    return request<RootSession>("/root-mode/start", {
      method: "POST",
      body: JSON.stringify({ reason, scope, duration_seconds }),
    });
  },
  stop() {
    return request<RootSession>("/root-mode/stop", { method: "POST" });
  },
};

export const dashboard = {
  overview() {
    return request<DashboardOverview>("/dashboard/overview");
  },
  health() {
    return request<DashboardHealth>("/dashboard/health");
  },
  grafanaPanels() {
    return request<GrafanaPanels>("/dashboard/grafana/panels");
  },
};

// ─────────────────────────────────────────────────────────────────────────────
// Networks
// ─────────────────────────────────────────────────────────────────────────────

export interface NetworkContainer {
  id: string;
  name: string;
  ipv4_address: string;
  mac_address: string;
}

export interface NetworkSummary {
  id: string;
  name: string;
  driver: string;
  scope: string;
  internal: boolean;
  created: string;
  is_system: boolean;
  container_count: number;
}

export interface NetworkDetail extends NetworkSummary {
  subnet: string;
  gateway: string;
  containers: NetworkContainer[];
  labels: Record<string, string>;
  options: Record<string, string>;
}

export interface CreateNetworkPayload {
  name: string;
  driver?: string;
  internal?: boolean;
  subnet?: string | null;
  gateway?: string | null;
  labels?: Record<string, string>;
}

export const networks = {
  list() {
    return request<NetworkSummary[]>("/networks/");
  },
  get(id: string) {
    return request<NetworkDetail>(`/networks/${id}`);
  },
  create(payload: CreateNetworkPayload) {
    return request<NetworkDetail>("/networks/", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  delete(id: string) {
    return request<void>(`/networks/${id}`, { method: "DELETE" });
  },
};

// ─────────────────────────────────────────────────────────────────────────────
// Audit log
// ─────────────────────────────────────────────────────────────────────────────

export const audit = {
  list(params?: {
    actor_id?: string;
    action?: string;
    target_id?: string;
    since?: string;
    limit?: number;
    offset?: number;
  }) {
    const qs = new URLSearchParams();
    if (params?.actor_id) qs.set("actor_id", params.actor_id);
    if (params?.action) qs.set("action", params.action);
    if (params?.target_id) qs.set("target_id", params.target_id);
    if (params?.since) qs.set("since", params.since);
    if (params?.limit !== undefined) qs.set("limit", String(params.limit));
    if (params?.offset !== undefined) qs.set("offset", String(params.offset));
    const q = qs.toString();
    return request<AuditEvent[]>(`/audit/${q ? `?${q}` : ""}`);
  },
};

export interface RbacPermission {
  id: string;
  resource: string;
  action: string;
}

export interface RbacRole {
  id: string;
  name: string;
  description: string | null;
  created_at: string;
  permissions: RbacPermission[];
}

export interface AdminUser {
  id: string;
  email: string;
  is_active: boolean;
  is_superuser: boolean;
  is_verified: boolean;
  roles: RbacRole[];
  permissions: RbacPermission[];
}

export interface MeAccess {
  id: string;
  email: string;
  is_superuser: boolean;
  roles: RbacRole[];
  permissions: RbacPermission[];
}

export const adminUsers = {
  meAccess() {
    return request<MeAccess>("/users/me/access");
  },
  list() {
    return request<AdminUser[]>("/users/");
  },
  listRoles() {
    return request<RbacRole[]>("/users/roles");
  },
  setUserRoles(userId: string, roleIds: string[]) {
    return request<AdminUser>(`/users/${userId}/roles`, {
      method: "PUT",
      body: JSON.stringify({ role_ids: roleIds }),
    });
  },
};

// ─────────────────────────────────────────────────────────────────────────────
// Capability helpers (mirror backend policy matrix client-side for UI gating)
// ─────────────────────────────────────────────────────────────────────────────

export function canExec(cls: ContainerClass, rootModeActive: boolean): boolean {
  if (cls === "TENANT_APP") return true;
  return rootModeActive;
}

export function canStop(cls: ContainerClass, rootModeActive: boolean): boolean {
  if (cls === "UNTRUSTED") return false;
  if (cls === "TENANT_APP" || cls === "SYSTEM_AUX") return true;
  if (cls === "SYSTEM_CORE") return rootModeActive;
  return false;
}

export function canDelete(cls: ContainerClass, rootModeActive: boolean): boolean {
  if (cls === "UNTRUSTED") return false;
  if (cls === "TENANT_APP") return true;
  return rootModeActive;
}

export function canPause(cls: ContainerClass, rootModeActive: boolean): boolean {
  if (cls === "UNTRUSTED") return false;
  if (cls === "TENANT_APP") return true;
  return rootModeActive;
}

export function canLogs(cls: ContainerClass, _rootModeActive: boolean): boolean {
  // All container classes can view logs
  return true;
}

export const CLASS_COLORS: Record<ContainerClass, string> = {
  SYSTEM_CORE: "bg-red-100 text-red-800 border-red-200",
  SYSTEM_AUX: "bg-amber-100 text-amber-800 border-amber-200",
  TENANT_APP: "bg-green-100 text-green-800 border-green-200",
  UNTRUSTED: "bg-gray-100 text-gray-700 border-gray-200",
};

// ─────────────────────────────────────────────────────────────────────────────
// Hardware detection
// ─────────────────────────────────────────────────────────────────────────────

export interface GpuDevice {
  index: number;
  vendor: string;
  model: string;
  vram_bytes: number;
  driver_version: string;
  compute_api: string;
}

export interface GpuInventory {
  devices: GpuDevice[];
  primary_vendor: string;
  primary_compute_api: string;
  has_gpu: boolean;
  device_count: number;
  total_vram_bytes: number;
}

export interface GpuMetrics {
  util_percent: number | null;
  vram_used_bytes: number | null;
  vram_total_bytes: number | null;
}

export interface HardwareInfo {
  gpu: GpuInventory;
  gpu_metrics: GpuMetrics;
  recommended_vllm_image: string | null;
}

export const hardware = {
  info() {
    return request<HardwareInfo>("/hardware");
  },
};
