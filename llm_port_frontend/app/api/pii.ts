/**
 * PII Dashboard API — fetches aggregated PII processing stats and event logs.
 * Proxied through the backend admin API (no raw PII data exposed).
 */

const BASE = "/api/admin/pii";

// ── Types ──────────────────────────────────────────────────────────────────

export interface DailyVolume {
  date: string;
  count: number;
  pii_count: number;
}

export interface PIIStats {
  total_scans: number;
  total_with_pii: number;
  total_entities: number;
  detection_rate: number;
  entity_type_breakdown: Record<string, number>;
  operation_breakdown: Record<string, number>;
  source_breakdown: Record<string, number>;
  daily_volume: DailyVolume[];
}

export interface PIIEvent {
  id: string;
  created_at: string;
  operation: string;
  mode: string | null;
  language: string;
  score_threshold: number;
  pii_detected: boolean;
  entities_found: number;
  entity_type_counts: Record<string, number> | null;
  source: string;
  request_id: string | null;
}

export interface PIIEventsPage {
  items: PIIEvent[];
  total: number;
}

export interface PIIPolicyConfig {
  telemetry: {
    enabled: boolean;
    mode: "sanitized" | "metrics_only";
    store_raw: boolean;
  };
  egress: {
    enabled_for_cloud: boolean;
    enabled_for_local: boolean;
    mode: "redact" | "tokenize_reversible";
    fail_action: "block" | "allow" | "fallback_to_local";
  };
  presidio: {
    language: string;
    threshold: number;
    entities: string[];
  };
}

export interface PIIPolicyOptionsResponse {
  source: "pii_service" | "fallback";
  supported_entities: string[];
  supported_languages: string[];
  supported_sanitize_modes: string[];
  telemetry_modes: Array<"sanitized" | "metrics_only">;
  egress_modes: Array<"redact" | "tokenize_reversible">;
  fail_actions: Array<"block" | "allow" | "fallback_to_local">;
  default_language: string;
  default_score_threshold: number;
}

export interface PIITenantPolicyPayload {
  tenant_id: string;
  has_override: boolean;
  override_policy: PIIPolicyConfig | null;
  default_policy: PIIPolicyConfig | null;
  effective_policy: PIIPolicyConfig | null;
}

export interface PIITenantListResponse {
  items: string[];
}

function defaultPolicyFromOptions(
  options?: PIIPolicyOptionsResponse,
): PIIPolicyConfig {
  const defaultLanguage = options?.default_language || "en";
  const defaultThreshold = Number.isFinite(options?.default_score_threshold)
    ? Number(options?.default_score_threshold)
    : 0.6;
  return {
    telemetry: {
      enabled: false,
      mode: "sanitized",
      store_raw: false,
    },
    egress: {
      enabled_for_cloud: true,
      enabled_for_local: false,
      mode: "redact",
      fail_action: "block",
    },
    presidio: {
      language: defaultLanguage,
      threshold: Math.max(0, Math.min(1, defaultThreshold)),
      entities: [],
    },
  };
}

export function normalizePIIPolicy(
  value: unknown,
  options?: PIIPolicyOptionsResponse,
): PIIPolicyConfig {
  const defaults = defaultPolicyFromOptions(options);
  if (!value || typeof value !== "object") return defaults;
  const raw = value as Partial<PIIPolicyConfig>;
  const telemetryRaw = raw.telemetry ?? {};
  const egressRaw = raw.egress ?? {};
  const presidioRaw = raw.presidio ?? {};

  const thresholdRaw = (presidioRaw as { threshold?: unknown }).threshold;
  const thresholdNum =
    typeof thresholdRaw === "number"
      ? thresholdRaw
      : Number(thresholdRaw ?? defaults.presidio.threshold);
  const entitiesRaw = (presidioRaw as { entities?: unknown }).entities;
  const entities = Array.isArray(entitiesRaw)
    ? entitiesRaw.filter((item): item is string => typeof item === "string")
    : [];

  return {
    telemetry: {
      enabled: Boolean((telemetryRaw as { enabled?: unknown }).enabled),
      mode:
        (telemetryRaw as { mode?: "sanitized" | "metrics_only" }).mode ===
        "metrics_only"
          ? "metrics_only"
          : "sanitized",
      store_raw: Boolean((telemetryRaw as { store_raw?: unknown }).store_raw),
    },
    egress: {
      enabled_for_cloud: Boolean(
        (egressRaw as { enabled_for_cloud?: unknown }).enabled_for_cloud,
      ),
      enabled_for_local: Boolean(
        (egressRaw as { enabled_for_local?: unknown }).enabled_for_local,
      ),
      mode:
        (egressRaw as { mode?: "redact" | "tokenize_reversible" }).mode ===
        "tokenize_reversible"
          ? "tokenize_reversible"
          : "redact",
      fail_action: (() => {
        const candidate = (
          egressRaw as { fail_action?: "block" | "allow" | "fallback_to_local" }
        ).fail_action;
        if (candidate === "allow" || candidate === "fallback_to_local")
          return candidate;
        return "block";
      })(),
    },
    presidio: {
      language:
        typeof (presidioRaw as { language?: unknown }).language === "string" &&
        (presidioRaw as { language?: string }).language
          ? (presidioRaw as { language: string }).language
          : defaults.presidio.language,
      threshold: Number.isFinite(thresholdNum)
        ? Math.max(0, Math.min(1, thresholdNum))
        : defaults.presidio.threshold,
      entities,
    },
  };
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init.headers ?? {}),
    },
    credentials: "include",
  });
  if (!res.ok) throw new Error(`PII API failed: ${res.status}`);
  return res.json() as Promise<T>;
}

// ── Fetchers ───────────────────────────────────────────────────────────────

export async function fetchPIIStats(params?: {
  since?: string;
  until?: string;
}): Promise<PIIStats> {
  const qs = new URLSearchParams();
  if (params?.since) qs.set("since", params.since);
  if (params?.until) qs.set("until", params.until);
  const url = `${BASE}/stats${qs.toString() ? `?${qs}` : ""}`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`PII stats failed: ${res.status}`);
  return res.json();
}

export async function fetchPIIEvents(params?: {
  operation?: string;
  source?: string;
  pii_only?: boolean;
  since?: string;
  limit?: number;
  offset?: number;
}): Promise<PIIEventsPage> {
  const qs = new URLSearchParams();
  if (params?.operation) qs.set("operation", params.operation);
  if (params?.source) qs.set("source", params.source);
  if (params?.pii_only) qs.set("pii_only", "true");
  if (params?.since) qs.set("since", params.since);
  if (params?.limit) qs.set("limit", String(params.limit));
  if (params?.offset) qs.set("offset", String(params.offset));
  const url = `${BASE}/events${qs.toString() ? `?${qs}` : ""}`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`PII events failed: ${res.status}`);
  return res.json();
}

export function fetchPIIPolicyOptions(): Promise<PIIPolicyOptionsResponse> {
  return request<PIIPolicyOptionsResponse>("/config/options");
}

export function listPIIPolicyTenants(
  query: string,
  limit = 50,
): Promise<PIITenantListResponse> {
  const qs = new URLSearchParams();
  if (query) qs.set("query", query);
  qs.set("limit", String(limit));
  return request<PIITenantListResponse>(`/policies/tenants?${qs.toString()}`);
}

export function getPIITenantPolicy(
  tenantId: string,
): Promise<PIITenantPolicyPayload> {
  return request<PIITenantPolicyPayload>(
    `/policies/tenants/${encodeURIComponent(tenantId)}`,
  );
}

export function upsertPIITenantPolicy(
  tenantId: string,
  piiConfig: PIIPolicyConfig,
): Promise<PIITenantPolicyPayload> {
  return request<PIITenantPolicyPayload>(
    `/policies/tenants/${encodeURIComponent(tenantId)}`,
    {
      method: "PUT",
      body: JSON.stringify({ pii_config: piiConfig }),
    },
  );
}

export function clearPIITenantPolicy(
  tenantId: string,
): Promise<PIITenantPolicyPayload> {
  return request<PIITenantPolicyPayload>(
    `/policies/tenants/${encodeURIComponent(tenantId)}`,
    {
      method: "DELETE",
    },
  );
}

// ── Session PII Policy (chat-scoped, via backend proxy) ────────────────────

const CHAT_BASE = "/api/chat";

export interface SessionPIIOverride {
  pii_enabled?: boolean | null;
  egress_enabled_for_cloud?: boolean | null;
  egress_enabled_for_local?: boolean | null;
  egress_mode?: "redact" | "tokenize_reversible" | null;
  egress_fail_action?: "block" | "allow" | "fallback_to_local" | null;
  telemetry_enabled?: boolean | null;
  telemetry_mode?: "sanitized" | "metrics_only" | null;
  presidio_threshold?: number | null;
  presidio_entities_add?: string[] | null;
}

export interface SessionPIIPolicy {
  session_id: string;
  has_override: boolean;
  override: SessionPIIOverride | null;
  floor: PIIPolicyConfig | null;
  effective: PIIPolicyConfig | null;
}

async function chatRequest<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const res = await fetch(`${CHAT_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init.headers ?? {}),
    },
    credentials: "include",
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(text || `PII session API failed: ${res.status}`);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export function getSessionPiiPolicy(
  sessionId: string,
): Promise<SessionPIIPolicy> {
  return chatRequest<SessionPIIPolicy>(
    `/sessions/${encodeURIComponent(sessionId)}/pii-policy`,
  );
}

export function patchSessionPiiPolicy(
  sessionId: string,
  patch: SessionPIIOverride,
): Promise<SessionPIIPolicy> {
  return chatRequest<SessionPIIPolicy>(
    `/sessions/${encodeURIComponent(sessionId)}/pii-policy`,
    { method: "PATCH", body: JSON.stringify(patch) },
  );
}

export function clearSessionPiiPolicy(sessionId: string): Promise<void> {
  return chatRequest<void>(
    `/sessions/${encodeURIComponent(sessionId)}/pii-policy`,
    { method: "DELETE" },
  );
}
