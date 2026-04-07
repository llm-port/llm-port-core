/**
 * Tool Availability API — session-scoped tool catalog and policy.
 * Calls the gateway API directly (not backend admin proxy).
 */

// ── Types ──────────────────────────────────────────────────────────────────

export type ExecutionMode = "local_only" | "server_only" | "hybrid";
export type ToolRealm =
  | "server_managed"
  | "mcp_remote"
  | "client_local"
  | "client_proxied";
export type ToolSource = "core" | "skills" | "mcp" | "local_agent" | "plugin";

export interface ToolAvailabilityEntry {
  tool_id: string;
  display_name: string | null;
  description: string | null;
  realm: ToolRealm;
  source: ToolSource;
  effective_enabled: boolean;
  policy_allowed: boolean;
  user_enabled: boolean;
  available: boolean;
  availability_reason: string | null;
}

export interface ToolAvailabilityResponse {
  session_id: string;
  execution_mode: ExecutionMode;
  effective_catalog_version: number;
  tools: ToolAvailabilityEntry[];
}

export interface SessionToolPolicy {
  session_id: string;
  execution_mode: ExecutionMode;
  hybrid_preference: string | null;
  effective_catalog_version: number;
}

export interface SessionToolOverride {
  tool_id: string;
  enabled: boolean;
}

export interface SessionToolPolicyPatch {
  execution_mode?: ExecutionMode;
  hybrid_preference?: string | null;
  tool_overrides?: SessionToolOverride[];
}

// ── Helpers ────────────────────────────────────────────────────────────────

const GATEWAY_BASE = "/api/v1";

async function gatewayRequest<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const res = await fetch(`${GATEWAY_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init.headers ?? {}),
    },
    credentials: "include",
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(text || `Tool API failed: ${res.status}`);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

// ── Tool Availability ──────────────────────────────────────────────────────

export async function getAvailableTools(
  sessionId: string,
  opts?: {
    includeDisabled?: boolean;
    includeUnavailable?: boolean;
  },
): Promise<ToolAvailabilityResponse> {
  const params = new URLSearchParams({ session_id: sessionId });
  if (opts?.includeDisabled !== undefined) {
    params.set("include_disabled", String(opts.includeDisabled));
  }
  if (opts?.includeUnavailable !== undefined) {
    params.set("include_unavailable", String(opts.includeUnavailable));
  }
  return gatewayRequest<ToolAvailabilityResponse>(
    `/tools/available?${params.toString()}`,
  );
}
