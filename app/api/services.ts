/**
 * Services manifest API client — discovers which optional modules
 * (RAG, PII, Auth, ...) are available on the backend and provides
 * enable / disable lifecycle operations.
 *
 * Endpoint: GET  /api/admin/services
 *           PUT  /api/admin/services/:name/enable
 *           PUT  /api/admin/services/:name/disable
 */

const BASE = "/api/admin";

export interface ContainerState {
  name: string;
  state: string; // "running" | "exited" | "paused" | "not_found" | …
}

export interface ServiceInfo {
  name: string;
  display_name: string;
  description: string;
  /** Whether the settings flag is set (env var configured). */
  configured: boolean;
  /** Whether module containers are actually running. */
  enabled: boolean;
  status: "disabled" | "configured" | "healthy" | "unhealthy";
  containers?: ContainerState[];
}

export interface ServicesManifest {
  services: ServiceInfo[];
}

export interface ModuleActionResult {
  module: string;
  action: "enable" | "disable";
  started?: string[];
  stopped?: string[];
  errors: string[];
}

async function assertOk(res: Response): Promise<void> {
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`API ${res.status}: ${text}`);
  }
}

export const servicesApi = {
  /**
   * Fetch the full services manifest.
   * The result includes every known optional module with its enabled
   * flag, health status, and container states.
   */
  async list(): Promise<ServicesManifest> {
    const res = await fetch(`${BASE}/services`, {
      method: "GET",
      credentials: "include",
    });
    await assertOk(res);
    return res.json() as Promise<ServicesManifest>;
  },

  /** Start all containers belonging to the named module. */
  async enable(name: string): Promise<ModuleActionResult> {
    const res = await fetch(`${BASE}/services/${encodeURIComponent(name)}/enable`, {
      method: "PUT",
      credentials: "include",
    });
    await assertOk(res);
    return res.json() as Promise<ModuleActionResult>;
  },

  /** Stop all containers belonging to the named module. */
  async disable(name: string): Promise<ModuleActionResult> {
    const res = await fetch(`${BASE}/services/${encodeURIComponent(name)}/disable`, {
      method: "PUT",
      credentials: "include",
    });
    await assertOk(res);
    return res.json() as Promise<ModuleActionResult>;
  },
};
