/**
 * API client for the Chat & Sessions admin endpoints.
 *
 * Mirrors the backend routes at /api/admin/chat/*.
 */

import type {
  ChatProject,
  ChatSession,
  ChatAttachment,
  ChatStats,
} from "./chatTypes";

export type { ChatProject, ChatSession, ChatAttachment, ChatStats };

const BASE = "/api/admin/chat";

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    cache: "no-store",
    headers: {
      "Content-Type": "application/json",
      "Cache-Control": "no-store",
      Pragma: "no-cache",
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

// ── API ──────────────────────────────────────────────────────────

export const chatAdmin = {
  listProjects: () =>
    request<{ data: ChatProject[] }>("/projects").then((r) => r.data),

  deleteProject: (id: string) =>
    request<void>(`/projects/${id}`, { method: "DELETE" }),

  listSessions: (projectId?: string) => {
    const qs = projectId ? `?project_id=${projectId}` : "";
    return request<{ data: ChatSession[] }>(`/sessions${qs}`).then(
      (r) => r.data,
    );
  },

  deleteSession: (id: string) =>
    request<void>(`/sessions/${id}`, { method: "DELETE" }),

  listAttachments: (params?: { session_id?: string; project_id?: string }) => {
    const qs = new URLSearchParams();
    if (params?.session_id) qs.set("session_id", params.session_id);
    if (params?.project_id) qs.set("project_id", params.project_id);
    const q = qs.toString();
    return request<{ data: ChatAttachment[] }>(
      `/attachments${q ? `?${q}` : ""}`,
    ).then((r) => r.data);
  },

  deleteAttachment: (id: string) =>
    request<void>(`/attachments/${id}`, { method: "DELETE" }),

  stats: () => request<ChatStats>("/stats"),
};
