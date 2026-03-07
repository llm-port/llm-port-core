/**
 * Admin RAG API client — typed wrappers for /api/admin/rag endpoints.
 */

const BASE = "/api/admin/rag";

export type RagEmbeddingProvider = "openai_compat" | "local" | "huggingface";
export type RagSearchMode = "vector" | "keyword" | "hybrid";

export interface RagChunkingPolicy {
  max_tokens: number;
  overlap: number;
  by_headings: boolean;
}

export interface RagRuntimeConfigPayload {
  embedding_provider: RagEmbeddingProvider;
  embedding_model: string;
  embedding_base_url: string | null;
  embedding_api_key_ref: string | null;
  embedding_dim: number;
  chunking_policy: RagChunkingPolicy;
}

export interface RagRuntimeConfigResponse {
  updated_at: string;
  payload: RagRuntimeConfigPayload;
}

export interface RagRuntimeConfigUpdateRequest {
  payload: RagRuntimeConfigPayload;
  embedding_api_key?: string | null;
}

export interface RagSearchPrincipals {
  user_id: string;
  group_ids: string[];
}

export interface RagSearchFilters {
  sources: string[];
  tags: string[];
  doc_types: string[];
  container_ids: string[];
  include_descendants: boolean;
  source_kind: string | null;
  asset_ids: string[];
  time_from: string | null;
  time_to: string | null;
}

export interface RagKnowledgeSearchRequest {
  tenant_id: string;
  workspace_id: string | null;
  query: string;
  principals: RagSearchPrincipals;
  filters: RagSearchFilters;
  top_k: number;
  mode: RagSearchMode;
  debug: boolean;
}

export interface RagSearchResult {
  chunk_text: string;
  doc_title: string | null;
  source_uri: string;
  section: string | null;
  score: number;
  metadata: Record<string, unknown>;
}

export interface RagKnowledgeSearchResponse {
  results: RagSearchResult[];
  debug: Record<string, unknown> | null;
}

export interface RagCollectorSummary {
  id: string;
  type: string;
  enabled: boolean;
  schedule: string;
  tenant_id: string;
  workspace_id: string | null;
  config: Record<string, unknown>;
}

export interface RagCollectorListResponse {
  collectors: RagCollectorSummary[];
}

export interface RagRunCollectorResponse {
  job_id: string;
  source_id: string;
  status: string;
}

export interface RagIngestJobEvent {
  event_type: string;
  message: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface RagIngestJob {
  job_id: string;
  collector_id: string;
  job_type: string;
  publish_id: string | null;
  container_id: string | null;
  source_id: string | null;
  tenant_id: string;
  workspace_id: string | null;
  status: string;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  events: RagIngestJobEvent[];
}

export interface RagJobListResponse {
  jobs: RagIngestJob[];
}

export interface RagContainerPayload {
  tenant_id: string;
  workspace_id: string | null;
  parent_id: string | null;
  name: string;
  sort_order: number;
  acl_principals: string[];
}

export interface RagContainer {
  id: string;
  tenant_id: string;
  workspace_id: string | null;
  parent_id: string | null;
  name: string;
  slug: string;
  path: string;
  depth: number;
  sort_order: number;
  acl_principals: string[];
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface RagContainerTreeResponse {
  containers: RagContainer[];
}

export interface RagUploadPresignRequest {
  tenant_id: string;
  workspace_id: string | null;
  container_id: string;
  filename: string;
  size_bytes: number;
  content_type: string;
  sha256?: string | null;
}

export interface RagUploadPresignResponse {
  object_key: string;
  upload_url: string;
  required_headers: Record<string, string>;
  expires_at: string;
}

export interface RagUploadCompleteRequest {
  object_key: string;
  tenant_id: string;
  workspace_id: string | null;
  container_id: string;
  filename: string;
  size_bytes: number;
  content_type: string;
  sha256: string;
  draft_id: string | null;
  tags: string[];
  acl_principals: string[];
  created_by: string | null;
}

export interface RagUploadCompleteResponse {
  draft_id: string;
  operation_id: number;
  status: string;
}

export interface RagDraftCreateRequest {
  tenant_id: string;
  workspace_id: string | null;
  container_id: string;
  created_by: string | null;
}

export interface RagDraftOperationPayload {
  op_type:
    | "upload"
    | "replace"
    | "delete"
    | "move"
    | "retag"
    | "set_acl"
    | "rename";
  asset_id: string | null;
  target_container_id: string | null;
  payload: Record<string, unknown>;
}

export interface RagDraftUpdateRequest {
  operations?: RagDraftOperationPayload[] | null;
  status?: "open" | "saved" | "published" | "cancelled" | null;
}

export interface RagDraftOperation {
  id: number;
  op_type: string;
  asset_id: string | null;
  target_container_id: string | null;
  payload: Record<string, unknown>;
  status: string;
  error: string | null;
  created_at: string;
  updated_at: string;
}

export interface RagDraft {
  id: string;
  tenant_id: string;
  workspace_id: string | null;
  container_id: string;
  status: string;
  created_by: string | null;
  created_at: string;
  updated_at: string;
  operations: RagDraftOperation[];
}

export interface RagPublishTriggerRequest {
  scheduled_for: string | null;
  triggered_by: string | null;
}

export interface RagPublishTriggerResponse {
  publish_id: string;
  job_id: string | null;
  status: string;
}

export interface RagPublish {
  id: string;
  draft_id: string;
  tenant_id: string;
  workspace_id: string | null;
  container_id: string;
  scheduled_for: string | null;
  status: string;
  triggered_by: string;
  stats: Record<string, unknown>;
  error: string | null;
  created_at: string;
  updated_at: string;
}

export interface RagPublishListResponse {
  publishes: RagPublish[];
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const isFormData = init.body instanceof FormData;
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      ...(isFormData ? {} : { "Content-Type": "application/json" }),
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

export const ragRuntime = {
  health() {
    return request<{ status: string; mode?: "lite" | "pro" }>("/health");
  },
  getConfig() {
    return request<RagRuntimeConfigResponse>("/runtime-config");
  },
  updateConfig(body: RagRuntimeConfigUpdateRequest) {
    return request<RagRuntimeConfigResponse>("/runtime-config", {
      method: "POST",
      body: JSON.stringify(body),
    });
  },
};

export const ragKnowledge = {
  search(body: RagKnowledgeSearchRequest) {
    return request<RagKnowledgeSearchResponse>("/knowledge/search", {
      method: "POST",
      body: JSON.stringify(body),
    });
  },
};

export const ragCollectors = {
  list() {
    return request<RagCollectorListResponse>("/collectors");
  },
  run(collectorId: string) {
    return request<RagRunCollectorResponse>(`/collectors/${collectorId}/run`, {
      method: "POST",
    });
  },
};

export const ragContainers = {
  create(body: RagContainerPayload) {
    return request<RagContainer>("/containers", {
      method: "POST",
      body: JSON.stringify(body),
    });
  },
  tree(tenantId?: string, workspaceId?: string) {
    const params = new URLSearchParams();
    if (tenantId) params.set("tenant_id", tenantId);
    if (workspaceId) params.set("workspace_id", workspaceId);
    const suffix = params.toString() ? `?${params.toString()}` : "";
    return request<RagContainerTreeResponse>(`/containers/tree${suffix}`);
  },
  update(containerId: string, body: RagContainerPayload) {
    return request<RagContainer>(`/containers/${containerId}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    });
  },
  remove(containerId: string) {
    return request<RagContainer>(`/containers/${containerId}`, {
      method: "DELETE",
    });
  },
};

export const ragUploads = {
  presign(body: RagUploadPresignRequest) {
    return request<RagUploadPresignResponse>("/uploads/presign", {
      method: "POST",
      body: JSON.stringify(body),
    });
  },
  complete(body: RagUploadCompleteRequest) {
    return request<RagUploadCompleteResponse>("/uploads/complete", {
      method: "POST",
      body: JSON.stringify(body),
    });
  },
};

export const ragDrafts = {
  create(body: RagDraftCreateRequest) {
    return request<RagDraft>("/drafts", {
      method: "POST",
      body: JSON.stringify(body),
    });
  },
  get(draftId: string) {
    return request<RagDraft>(`/drafts/${draftId}`);
  },
  patch(draftId: string, body: RagDraftUpdateRequest) {
    return request<RagDraft>(`/drafts/${draftId}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    });
  },
  publish(draftId: string, body: RagPublishTriggerRequest) {
    return request<RagPublishTriggerResponse>(`/drafts/${draftId}/publish`, {
      method: "POST",
      body: JSON.stringify(body),
    });
  },
};

export const ragPublishes = {
  list(limit = 100) {
    return request<RagPublishListResponse>(`/publishes?limit=${limit}`);
  },
  get(publishId: string) {
    return request<RagPublish>(`/publishes/${publishId}`);
  },
};

export const ragJobs = {
  list(limit = 50) {
    return request<RagJobListResponse>(`/jobs?limit=${limit}`);
  },
  get(jobId: string) {
    return request<RagIngestJob>(`/jobs/${jobId}`);
  },
};

// ── RAG Lite types & API ─────────────────────────────────────────────

export interface RagLiteConfigDTO {
  embedding_provider_id: string;
  embedding_model: string;
  embedding_dim: number;
  chunk_max_tokens: number;
  chunk_overlap_tokens: number;
  file_store_root: string;
  upload_max_file_mb: number;
}

export interface RagLiteDocumentDTO {
  id: string;
  filename: string;
  doc_type: string;
  collection_id: string | null;
  size_bytes: number;
  chunk_count: number;
  status: string;
  created_at: string;
}

export interface RagLiteSearchRequest {
  query: string;
  top_k?: number;
  collection_ids?: string[] | null;
}

export interface RagLiteSearchResult {
  chunk_text: string;
  document_id: string;
  filename: string;
  chunk_index: number;
  score: number;
}

export interface RagLiteSearchResponse {
  results: RagLiteSearchResult[];
  query: string;
}

export interface RagLiteCollectionDTO {
  id: string;
  name: string;
  description: string | null;
  created_at: string;
  updated_at: string;
}

export interface RagLiteUploadResponse {
  document_id: string;
  job_id: string;
  filename: string;
  doc_type: string;
  status: string;
  message: string;
}

export interface RagLiteJobDTO {
  id: string;
  document_id: string;
  status: string;
  error_message: string | null;
  stats_json: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
  events: {
    id: string;
    event_type: string;
    message: string;
    created_at: string;
  }[];
}

export const ragLite = {
  config() {
    return request<RagLiteConfigDTO>("/config");
  },
  listDocuments(collectionId?: string, limit = 100, offset = 0) {
    const params = new URLSearchParams({
      limit: String(limit),
      offset: String(offset),
    });
    if (collectionId) params.set("collection_id", collectionId);
    return request<RagLiteDocumentDTO[]>(`/documents?${params}`);
  },
  deleteDocument(documentId: string) {
    return request<void>(`/documents/${documentId}`, { method: "DELETE" });
  },
  upload(file: File, collectionId?: string) {
    const form = new FormData();
    form.append("file", file);
    const params = collectionId ? `?collection_id=${collectionId}` : "";
    return request<RagLiteUploadResponse>(`/upload${params}`, {
      method: "POST",
      body: form,
    });
  },
  search(body: RagLiteSearchRequest) {
    return request<RagLiteSearchResponse>("/search", {
      method: "POST",
      body: JSON.stringify(body),
    });
  },
  listCollections() {
    return request<RagLiteCollectionDTO[]>("/collections");
  },
  createCollection(name: string, description?: string | null) {
    return request<RagLiteCollectionDTO>("/collections", {
      method: "POST",
      body: JSON.stringify({ name, description }),
    });
  },
  deleteCollection(collectionId: string) {
    return request<void>(`/collections/${collectionId}`, { method: "DELETE" });
  },
  listJobs(limit = 50) {
    return request<RagLiteJobDTO[]>(`/jobs?limit=${limit}`);
  },
};
