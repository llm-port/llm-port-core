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
