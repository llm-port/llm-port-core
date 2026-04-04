/**
 * Shared TypeScript types for chat entities.
 *
 * Both the admin client (`chat.ts`) and the user-facing client
 * (`chatClient.ts`) import from here to avoid duplication.
 */

export interface ChatProject {
  id: string;
  tenant_id: string;
  user_id: string;
  name: string;
  description: string | null;
  system_instructions?: string | null;
  model_alias: string | null;
  metadata_json?: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}

export interface ChatSession {
  id: string;
  tenant_id: string;
  user_id: string;
  project_id: string | null;
  title: string | null;
  status: string;
  metadata_json?: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}

export interface ChatMessage {
  id: string;
  session_id: string;
  role: "user" | "assistant" | "system";
  content: string;
  content_parts: unknown[] | null;
  tool_call_json: unknown | null;
  model_alias: string | null;
  provider_instance_id: string | null;
  token_estimate: number | null;
  trace_id: string | null;
  created_at: string;
}

export interface ChatAttachment {
  id: string;
  tenant_id: string;
  user_id: string;
  session_id: string | null;
  project_id: string | null;
  filename: string;
  content_type: string;
  size_bytes: number;
  extraction_status: string;
  scope: string;
  page_count: number | null;
  truncated: boolean;
  created_at: string;
}

export interface ChatStats {
  total_projects: number;
  total_sessions: number;
  total_attachments: number;
  total_attachment_bytes: number;
}

export interface ModelAlias {
  alias: string;
  description: string | null;
  enabled: boolean;
}

export interface Capacity {
  projects: {
    current: number;
    limit: number | null;
  };
}

export interface TokenUsage {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
}

/** A single SSE delta parsed from a streaming response. */
export interface StreamDelta {
  content?: string;
  finish_reason?: string | null;
  usage?: TokenUsage;
}
