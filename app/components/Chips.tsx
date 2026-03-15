/**
 * Shared chip components used across multiple admin pages.
 * Centralises colour/label mappings so they only need updating once.
 */
import Chip from "@mui/material/Chip";
import type { ContainerClass, ContainerPolicy } from "~/api/admin";
import type {
  ModelStatus,
  RuntimeStatus,
  DownloadJobStatus,
  ArtifactFormat,
  ProviderType,
} from "~/api/llm";

// ─────────────────────────────────────────────────────────────────────────────
// Container class chip
// ─────────────────────────────────────────────────────────────────────────────

const CLASS_CONFIG: Record<
  ContainerClass,
  { color: "error" | "warning" | "success" | "default" | "secondary"; label: string }
> = {
  SYSTEM_CORE: { color: "error", label: "SYSTEM_CORE" },
  SYSTEM_AUX: { color: "warning", label: "SYSTEM_AUX" },
  MCP: { color: "secondary", label: "MCP" },
  TENANT_APP: { color: "success", label: "TENANT_APP" },
  UNTRUSTED: { color: "default", label: "UNTRUSTED" },
};

export function ClassChip({ value }: { value: ContainerClass }) {
  const cfg = CLASS_CONFIG[value] ?? {
    color: "default" as const,
    label: value,
  };
  return <Chip label={cfg.label} size="small" color={cfg.color} />;
}

// ─────────────────────────────────────────────────────────────────────────────
// Container state chip (running / paused / exited / etc.)
// ─────────────────────────────────────────────────────────────────────────────

function stateColor(state: string): "success" | "default" | "warning" | "info" {
  const s = state.toLowerCase();
  if (s === "running") return "success";
  if (s === "exited") return "default";
  if (s === "paused") return "warning";
  return "info";
}

export function StateChip({ value }: { value: string }) {
  return (
    <Chip
      label={value}
      size="small"
      color={stateColor(value)}
      variant="outlined"
    />
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Audit result chip
// ─────────────────────────────────────────────────────────────────────────────

export function ResultChip({ value }: { value: "allow" | "deny" }) {
  return (
    <Chip
      label={value}
      size="small"
      color={value === "allow" ? "success" : "error"}
      variant="outlined"
    />
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Severity chip
// ─────────────────────────────────────────────────────────────────────────────

export function SeverityChip({ value }: { value: string }) {
  return (
    <Chip
      label={value}
      size="small"
      color={value === "high" ? "error" : "default"}
      variant="filled"
    />
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Container policy chip
// ─────────────────────────────────────────────────────────────────────────────

const POLICY_COLOR: Record<ContainerPolicy, "error" | "warning" | "success"> = {
  locked: "error",
  restricted: "warning",
  free: "success",
};

export function PolicyChip({ value }: { value: ContainerPolicy }) {
  return (
    <Chip
      label={value}
      size="small"
      color={POLICY_COLOR[value] ?? "default"}
      variant="outlined"
    />
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// LLM — Model status chip
// ─────────────────────────────────────────────────────────────────────────────

const MODEL_STATUS_COLOR: Record<
  ModelStatus,
  "success" | "info" | "error" | "warning"
> = {
  available: "success",
  downloading: "info",
  failed: "error",
  deleting: "warning",
};

export function ModelStatusChip({
  value,
  onClick,
}: {
  value: ModelStatus;
  onClick?: () => void;
}) {
  return (
    <Chip
      label={value}
      size="small"
      color={MODEL_STATUS_COLOR[value] ?? "default"}
      onClick={onClick}
      sx={onClick ? { cursor: "pointer" } : undefined}
    />
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// LLM — Runtime status chip
// ─────────────────────────────────────────────────────────────────────────────

const RUNTIME_STATUS_COLOR: Record<
  RuntimeStatus,
  "success" | "info" | "error" | "warning" | "default"
> = {
  creating: "info",
  starting: "info",
  running: "success",
  stopping: "warning",
  stopped: "default",
  error: "error",
};

export function RuntimeStatusChip({ value }: { value: RuntimeStatus }) {
  return (
    <Chip
      label={value}
      size="small"
      color={RUNTIME_STATUS_COLOR[value] ?? "default"}
    />
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// LLM — Download job status chip
// ─────────────────────────────────────────────────────────────────────────────

const JOB_STATUS_COLOR: Record<
  DownloadJobStatus,
  "success" | "info" | "error" | "warning" | "default"
> = {
  queued: "default",
  running: "info",
  success: "success",
  failed: "error",
  canceled: "warning",
};

export function JobStatusChip({ value }: { value: DownloadJobStatus }) {
  return (
    <Chip
      label={value}
      size="small"
      color={JOB_STATUS_COLOR[value] ?? "default"}
      variant="outlined"
    />
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// LLM — Artifact format chip
// ─────────────────────────────────────────────────────────────────────────────

const FORMAT_COLOR: Record<
  ArtifactFormat,
  "primary" | "secondary" | "default"
> = {
  safetensors: "primary",
  gguf: "secondary",
  other: "default",
};

export function FormatChip({ value }: { value: ArtifactFormat }) {
  return (
    <Chip
      label={value}
      size="small"
      color={FORMAT_COLOR[value] ?? "default"}
      variant="outlined"
    />
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// LLM — Provider type chip
// ─────────────────────────────────────────────────────────────────────────────

const ENGINE_COLOR: Record<
  ProviderType,
  "primary" | "secondary" | "info" | "warning"
> = {
  vllm: "primary",
  llamacpp: "secondary",
  tgi: "info",
  ollama: "warning",
};

export function EngineChip({ value }: { value: ProviderType }) {
  return (
    <Chip
      label={value}
      size="small"
      color={ENGINE_COLOR[value] ?? "default"}
      variant="filled"
    />
  );
}
