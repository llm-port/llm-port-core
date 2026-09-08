/**
 * Resolves direct-to-service URLs for UI links (e.g. Swagger docs,
 * Grafana embed) in the dev/headless deployment.
 *
 * In the dev stack the services are exposed on distinct host ports
 * (API gateway on :8001, Grafana on :3001) — there is NO nginx
 * reverse proxy, so the production same-origin paths
 * (`/gateway-docs/docs`, `/grafana/...`) do not resolve from the
 * vite dev server. UI links must therefore target the exposed
 * service ports on the same host the app is served from.
 *
 * Host/port can be overridden for non-standard setups via env
 * (e.g. frontend running on a different machine than the services):
 *   VITE_LLM_PORT_API_HOST / VITE_LLM_PORT_API_PORT
 *   VITE_GRAFANA_HOST      / VITE_GRAFANA_PORT
 *   VITE_GRAFANA_DASHBOARD_URL (absolute URL, highest precedence)
 */

function portFromEnv(envName: string, fallback: number): number {
  const raw = (import.meta.env[envName] as string | undefined)?.trim();
  const parsed = raw ? Number.parseInt(raw, 10) : Number.NaN;
  return Number.isInteger(parsed) && parsed > 0 && parsed <= 65535 ? parsed : fallback;
}

function buildUrl(host: string | undefined, port: number, path: string): string {
  const finalHost =
    (host && host.trim()) ||
    (typeof window !== "undefined" ? window.location.hostname : "") ||
    "localhost";
  const proto =
    typeof window !== "undefined" && window.location.protocol
      ? window.location.protocol
      : "http:";
  return `${proto}//${finalHost}:${port}${path}`;
}

/**
 * Swagger UI for the llm_port_api gateway (`/api/docs`).
 * The gateway container exposes its port (default 8001) for dev.
 */
export function apiDocsUrl(): string {
  const host = (import.meta.env.VITE_LLM_PORT_API_HOST as string | undefined) ?? undefined;
  const port = portFromEnv("VITE_LLM_PORT_API_PORT", 8001);
  return buildUrl(host, port, "/api/docs");
}

const GRAFANA_OVERVIEW_PATH = "/grafana/d/llm-port-overview/llm-port-overview";

/**
 * Grafana "llm_port overview" dashboard URL.
 * In dev the service is served under the `/grafana` sub-path on its
 * exposed port (default 3001) with anonymous Viewer access, so the
 * URL embeds directly without authentication.
 */
export function grafanaOverviewUrl(theme: "light" | "dark"): string {
  const override = (import.meta.env.VITE_GRAFANA_DASHBOARD_URL as string | undefined)?.trim();
  if (override) {
    // Explicit absolute URL — legacy behavior, used as-is.
    const sep = override.includes("?") ? "&" : "?";
    return `${override}${sep}theme=${theme}`;
  }
  const host = (import.meta.env.VITE_GRAFANA_HOST as string | undefined) ?? undefined;
  const port = portFromEnv("VITE_GRAFANA_PORT", 3001);
  const url = new URL(buildUrl(host, port, GRAFANA_OVERVIEW_PATH));
  url.searchParams.set("orgId", "1");
  url.searchParams.set("from", "now-6h");
  url.searchParams.set("to", "now");
  url.searchParams.set("timezone", "browser");
  url.searchParams.set("refresh", "30s");
  url.searchParams.set("theme", theme);
  return url.toString();
}
