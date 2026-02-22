const BASE = "/api/admin/system";

export interface SystemSettingSchemaItem {
  key: string;
  type: "string" | "int" | "bool" | "secret" | "json" | "enum";
  category: string;
  group: string;
  label: string;
  description: string;
  is_secret: boolean;
  default: unknown;
  apply_scope: "live_reload" | "service_restart" | "stack_recreate";
  service_targets: string[];
  protected: boolean;
  enum_values: string[];
}

export interface SystemSettingValueItem {
  is_secret: boolean;
  value?: unknown;
  configured?: boolean;
  masked?: string;
}

export interface SettingUpdateResult {
  key: string;
  apply_status: string;
  apply_scope: string;
  apply_job_id: string | null;
  messages: string[];
}

export interface ApplyJob {
  id: string;
  status: string;
  target_host: string;
  triggered_by: string | null;
  change_set: Record<string, unknown>;
  error: string | null;
  started_at: string;
  ended_at: string | null;
  events: Array<{
    seq: number;
    service: string;
    action: string;
    result: string;
    message: string;
    ts: string;
  }>;
}

export interface WizardStep {
  id: string;
  title: string;
  description: string;
  setting_keys: string[];
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
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`API ${res.status}: ${text}`);
  }
  return res.json() as Promise<T>;
}

export const systemSettingsApi = {
  schema() {
    return request<SystemSettingSchemaItem[]>("/settings/schema");
  },
  values() {
    return request<{ items: Record<string, SystemSettingValueItem> }>("/settings/values");
  },
  update(key: string, value: unknown, targetHost = "local") {
    return request<SettingUpdateResult>(`/settings/values/${encodeURIComponent(key)}`, {
      method: "PUT",
      body: JSON.stringify({ value, target_host: targetHost }),
    });
  },
  applyJob(jobId: string) {
    return request<ApplyJob>(`/apply/${encodeURIComponent(jobId)}`);
  },
  wizardSteps() {
    return request<{ steps: WizardStep[] }>("/wizard/steps");
  },
  wizardApply(values: Record<string, unknown>, targetHost = "local") {
    return request<{ results: SettingUpdateResult[] }>("/wizard/apply", {
      method: "POST",
      body: JSON.stringify({ values, target_host: targetHost }),
    });
  },
  listAgents() {
    return request<Array<{ id: string; host: string; status: string }>>("/agents");
  },
};
