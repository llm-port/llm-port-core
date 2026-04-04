/**
 * Skills Registry API — manages skills, versions, and assignments.
 * Proxied through the backend admin API.
 */

const BASE = "/api/admin/skills";

// ── Types ──────────────────────────────────────────────────────────────────

export type SkillScope =
  | "global"
  | "tenant"
  | "workspace"
  | "assistant"
  | "user";
export type SkillStatus = "draft" | "published" | "archived";

export interface SkillSummary {
  id: string;
  tenant_id: string;
  name: string;
  slug: string;
  scope: SkillScope;
  status: SkillStatus;
  priority: number;
  tags: string[];
  current_version: number;
  created_at: string;
  updated_at: string;
  created_by: string | null;
}

export interface SkillDetail extends SkillSummary {
  description: string | null;
  body_markdown: string | null;
  frontmatter_yaml: string | null;
  allowed_tools: string[] | null;
  forbidden_tools: string[] | null;
  preferred_tools: string[] | null;
  knowledge_sources: string[] | null;
  trigger_rules: Record<string, unknown> | null;
}

export interface SkillVersion {
  id: string;
  skill_id: string;
  version: number;
  body_markdown: string;
  frontmatter_yaml: string;
  change_note: string | null;
  created_at: string;
  created_by: string | null;
}

export type AssignmentTargetType =
  | "tenant"
  | "global"
  | "workspace"
  | "project"
  | "assistant";

export interface SkillAssignment {
  id: string;
  skill_id: string;
  target_type: AssignmentTargetType;
  target_id: string;
  priority_override: number | null;
  created_at: string;
}

export interface CreateSkillPayload {
  name: string;
  scope?: SkillScope;
  description?: string;
  body_markdown?: string;
  tags?: string[];
  allowed_tools?: string[];
  forbidden_tools?: string[];
  preferred_tools?: string[];
  knowledge_sources?: string[];
  trigger_rules?: Record<string, unknown>;
  priority?: number;
}

export interface UpdateSkillPayload {
  name?: string;
  description?: string;
  body_markdown?: string;
  scope?: SkillScope;
  tags?: string[];
  allowed_tools?: string[];
  forbidden_tools?: string[];
  preferred_tools?: string[];
  knowledge_sources?: string[];
  trigger_rules?: Record<string, unknown>;
  priority?: number;
  change_note?: string;
}

export interface CreateAssignmentPayload {
  target_type: AssignmentTargetType;
  target_id: string;
  priority_override?: number | null;
}

export interface SkillExport {
  name: string;
  slug: string;
  scope: SkillScope;
  description: string | null;
  body_markdown: string;
  frontmatter_yaml: string;
  tags: string[];
  tools: string[];
  forbidden_tools: string[];
  preferred_tools: string[];
  knowledge_sources: string[];
  trigger_rules: Record<string, unknown>;
  priority: number;
}

export interface CompletionsResponse {
  field: string;
  items: string[];
}

// ── Helpers ────────────────────────────────────────────────────────────────

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
    const text = await res.text().catch(() => "");
    throw new Error(text || `Skills API failed: ${res.status}`);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

// ── Skills CRUD ────────────────────────────────────────────────────────────

export async function listSkills(params?: {
  status?: SkillStatus;
  scope?: SkillScope;
  tag?: string;
}): Promise<SkillSummary[]> {
  const qs = new URLSearchParams();
  if (params?.status) qs.set("status", params.status);
  if (params?.scope) qs.set("scope", params.scope);
  if (params?.tag) qs.set("tag", params.tag);
  const query = qs.toString();
  return request<SkillSummary[]>(`${query ? `?${query}` : ""}`);
}

export async function getSkill(id: string): Promise<SkillDetail> {
  return request<SkillDetail>(`/${encodeURIComponent(id)}`);
}

export async function createSkill(
  payload: CreateSkillPayload,
): Promise<SkillDetail> {
  return request<SkillDetail>("", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function updateSkill(
  id: string,
  payload: UpdateSkillPayload,
): Promise<SkillDetail> {
  return request<SkillDetail>(`/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function deleteSkill(id: string): Promise<void> {
  return request<void>(`/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

// ── Lifecycle ──────────────────────────────────────────────────────────────

export async function publishSkill(id: string): Promise<SkillDetail> {
  return request<SkillDetail>(`/${encodeURIComponent(id)}/publish`, {
    method: "POST",
  });
}

export async function archiveSkill(id: string): Promise<SkillDetail> {
  return request<SkillDetail>(`/${encodeURIComponent(id)}/archive`, {
    method: "POST",
  });
}

// ── Versions ───────────────────────────────────────────────────────────────

export async function listVersions(skillId: string): Promise<SkillVersion[]> {
  return request<SkillVersion[]>(`/${encodeURIComponent(skillId)}/versions`);
}

export async function getVersion(
  skillId: string,
  version: number,
): Promise<SkillVersion> {
  return request<SkillVersion>(
    `/${encodeURIComponent(skillId)}/versions/${version}`,
  );
}

// ── Assignments ────────────────────────────────────────────────────────────

export async function listAssignments(
  skillId: string,
): Promise<SkillAssignment[]> {
  return request<SkillAssignment[]>(
    `/${encodeURIComponent(skillId)}/assignments`,
  );
}

export async function createAssignment(
  skillId: string,
  payload: CreateAssignmentPayload,
): Promise<SkillAssignment> {
  return request<SkillAssignment>(
    `/${encodeURIComponent(skillId)}/assignments`,
    { method: "POST", body: JSON.stringify(payload) },
  );
}

export async function deleteAssignment(
  skillId: string,
  assignmentId: string,
): Promise<void> {
  return request<void>(
    `/${encodeURIComponent(skillId)}/assignments/${encodeURIComponent(assignmentId)}`,
    { method: "DELETE" },
  );
}

// ── Import / Export ────────────────────────────────────────────────────────

export async function exportSkill(id: string): Promise<SkillExport> {
  return request<SkillExport>(`/${encodeURIComponent(id)}/export`);
}

export async function importSkill(payload: SkillExport): Promise<SkillDetail> {
  return request<SkillDetail>("/import", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

// ── Completions (intellisense) ─────────────────────────────────────────────

export async function getCompletions(
  field: string,
  prefix?: string,
): Promise<CompletionsResponse> {
  const qs = new URLSearchParams({ field });
  if (prefix) qs.set("prefix", prefix);
  return request<CompletionsResponse>(`/completions?${qs}`);
}
