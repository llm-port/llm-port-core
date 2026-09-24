/**
 * Engine settings: one model of vLLM's options, whatever stores them.
 *
 * Settings are held as vLLM's own argument names in snake_case
 * (`max_model_len`), the form a cluster deployment's `engine.config` takes.
 * A legacy container runtime stores the same settings as CLI flags
 * (`engine_args["max-model-len"]` plus `extra_args` tokens); the adapters
 * below convert, so one editor serves both.
 *
 * The flag catalog (`vllmFlags.json`) is read from the vLLM the cluster
 * runtime ships (0.27.1): every flag, type, default, choice and help text is
 * vLLM's own, not a hand-kept copy that drifts.
 */
import catalogJson from "./vllmFlags.json";

export type EngineValue = string | number | boolean;
export type EngineConfig = Record<string, EngineValue>;
export type EngineTarget = "cluster" | "container";

export interface FlagDef {
  flag: string;
  key: string;
  group: string;
  type: "boolean" | "integer" | "number" | "enum" | "string" | "json";
  default?: EngineValue | EngineValue[];
  choices?: string[];
  help?: string;
  /** Set from the copy's shape on a cluster, so only offered for containers. */
  containerOnly?: boolean;
}

interface Catalog {
  vllmVersion: string;
  toolParsers: string[];
  reasoningParsers: string[];
  flags: FlagDef[];
}

export const CATALOG = catalogJson as Catalog;

const BY_KEY = new Map(CATALOG.flags.map((f) => [f.key, f]));
const BY_FLAG = new Map(CATALOG.flags.map((f) => [f.flag, f]));

export function flagDef(key: string): FlagDef | undefined {
  return BY_KEY.get(key) ?? BY_FLAG.get(key.replace(/_/g, "-"));
}

/** `max_model_len` -> `max-model-len`. */
export function toFlag(key: string): string {
  return flagDef(key)?.flag ?? key.replace(/_/g, "-");
}

/** `max-model-len` -> `max_model_len`. */
export function toKey(flag: string): string {
  const clean = flag.replace(/^--/, "");
  return BY_FLAG.get(clean)?.key ?? clean.replace(/-/g, "_");
}

/** What the model and the machines tell the editor. All optional: unknown is fine. */
export interface ModelFacts {
  repoId?: string | null;
  /** The context the model was trained for. */
  maxContext?: number | null;
  capabilities?: string[];
  weightsBytes?: number | null;
  /** KV cache bytes one token occupies (per copy, before tensor parallel). */
  kvBytesPerToken?: number | null;
  needsRemoteCode?: boolean;
  architecture?: string | null;
}

export interface HardwareFacts {
  /** Memory of one accelerator, in bytes. */
  gpuBytes?: number | null;
  /** Accelerators one copy spans (tensor parallel). */
  tensorParallel?: number;
  /** The longest context a copy holds at the planned size. */
  maxContextThatFits?: number | null;
}

// ── Extra flags typed as text ──────────────────────────────────────────

const SAFE_VALUE = /^[\w.@/,=+:\\{}"'\[\]-]+$/;

export interface ParsedExtra {
  config: EngineConfig;
  issues: string[];
}

function coerce(raw: string, def?: FlagDef): EngineValue {
  if (def?.type === "boolean") return !/^(false|0|no|off)$/i.test(raw);
  if ((def?.type === "integer" || def?.type === "number") && raw.trim() !== "" && !Number.isNaN(Number(raw))) {
    return Number(raw);
  }
  if (raw === "true" || raw === "false") return raw === "true";
  return raw;
}

/**
 * Parse `--flag value --other --x=y` into settings.
 *
 * Refuses shell metacharacters, so the text can never escape the engine
 * command: this reaches a container's arguments on a legacy runtime.
 */
export function parseExtraFlags(text: string): ParsedExtra {
  const config: EngineConfig = {};
  const issues: string[] = [];
  const tokens = (text ?? "").split(/\s+/).filter(Boolean);
  for (let i = 0; i < tokens.length; i += 1) {
    const tok = tokens[i];
    if (!tok.startsWith("--")) {
      issues.push(tok);
      continue;
    }
    const body = tok.slice(2);
    const eq = body.indexOf("=");
    const name = eq >= 0 ? body.slice(0, eq) : body;
    if (!/^[A-Za-z0-9][A-Za-z0-9_-]*$/.test(name)) {
      issues.push(tok);
      continue;
    }
    const def = BY_FLAG.get(name);
    let value: string | undefined = eq >= 0 ? body.slice(eq + 1) : undefined;
    if (value === undefined && i + 1 < tokens.length && !tokens[i + 1].startsWith("--")) {
      value = tokens[i + 1];
      i += 1;
    }
    if (value !== undefined && !SAFE_VALUE.test(value)) {
      issues.push(`${tok} ${value}`);
      continue;
    }
    config[toKey(name)] = value === undefined ? true : coerce(value, def);
  }
  return { config, issues };
}

/** Settings back to `--flag value` text, for the command-line preview and legacy storage. */
export function toFlagTokens(config: EngineConfig): string[] {
  const tokens: string[] = [];
  for (const [key, value] of Object.entries(config)) {
    const flag = `--${toFlag(key)}`;
    if (value === true) tokens.push(flag);
    else if (value === false) {
      const def = flagDef(key);
      // A boolean vLLM enables by default is turned off with --no-<flag>.
      tokens.push(def?.default === true ? `--no-${toFlag(key)}` : `${flag}=false`);
    } else tokens.push(flag, String(value));
  }
  return tokens;
}

export function commandLine(model: string, config: EngineConfig): string {
  const parts = ["vllm serve", model || "<model>", ...toFlagTokens(config)];
  return parts.join(" ");
}

// ── Where settings are stored ───────────────────────────────────────────

/** Keys a cluster sets from the copy's shape; never written into engine settings there. */
export const CLUSTER_SHAPE_KEYS = new Set([
  "tensor_parallel_size",
  "pipeline_parallel_size",
  "served_model_name",
  "model",
  "host",
  "port",
]);

/** Settings + extra text -> a cluster deployment's `engine.config`. */
export function toClusterConfig(config: EngineConfig, extra = ""): { config: EngineConfig; issues: string[] } {
  const parsed = parseExtraFlags(extra);
  const merged: EngineConfig = { ...parsed.config, ...config };
  for (const key of CLUSTER_SHAPE_KEYS) delete merged[key];
  return { config: merged, issues: parsed.issues };
}

/** Legacy container runtime `provider_config` fields. */
export interface ContainerEngineFields {
  engine_args?: Record<string, EngineValue>;
  extra_args?: string[];
}

export function toContainerFields(config: EngineConfig, extra = ""): ContainerEngineFields & { issues: string[] } {
  const engine_args: Record<string, EngineValue> = {};
  for (const [key, value] of Object.entries(config)) engine_args[toFlag(key)] = value;
  const { issues } = parseExtraFlags(extra);
  const extra_args = (extra ?? "").split(/\s+/).filter(Boolean);
  return {
    ...(Object.keys(engine_args).length > 0 ? { engine_args } : {}),
    ...(extra_args.length > 0 && issues.length === 0 ? { extra_args } : {}),
    issues,
  };
}

export function fromContainerFields(fields: ContainerEngineFields | null | undefined): {
  config: EngineConfig;
  extra: string;
} {
  const config: EngineConfig = {};
  for (const [flag, value] of Object.entries(fields?.engine_args ?? {})) config[toKey(flag)] = value;
  return { config, extra: (fields?.extra_args ?? []).join(" ") };
}

// ── Presets, named by what they are for ────────────────────────────────

export type PresetId = "balanced" | "long_context" | "many_users" | "low_memory";

export const PRESETS: PresetId[] = ["balanced", "long_context", "many_users", "low_memory"];

/** What a preset sets, for this model on these machines. */
export function presetChanges(preset: PresetId, model?: ModelFacts, hardware?: HardwareFacts): EngineConfig {
  const trained = model?.maxContext ?? null;
  const fits = hardware?.maxContextThatFits ?? null;
  const longest = [trained, fits].filter((v): v is number => typeof v === "number" && v > 0);
  switch (preset) {
    case "long_context":
      return {
        ...(longest.length ? { max_model_len: Math.min(...longest) } : {}),
        enable_prefix_caching: true,
        kv_cache_dtype: "fp8",
      };
    case "many_users":
      return { max_num_seqs: 256, enable_prefix_caching: true, enable_chunked_prefill: true };
    case "low_memory":
      return {
        max_model_len: Math.min(8192, ...longest),
        kv_cache_dtype: "fp8",
        enforce_eager: true,
        max_num_seqs: 32,
      };
    case "balanced":
    default:
      return {};
  }
}

/** Keys every preset may set: switching presets clears these first. */
export const PRESET_KEYS = [
  "max_model_len",
  "enable_prefix_caching",
  "kv_cache_dtype",
  "max_num_seqs",
  "enable_chunked_prefill",
  "enforce_eager",
];

export function applyPreset(
  current: EngineConfig,
  preset: PresetId,
  suggested: EngineConfig,
  model?: ModelFacts,
  hardware?: HardwareFacts,
): EngineConfig {
  const next: EngineConfig = { ...current };
  for (const key of PRESET_KEYS) {
    if (key in suggested) next[key] = suggested[key];
    else delete next[key];
  }
  return { ...next, ...presetChanges(preset, model, hardware) };
}

// ── Checks ──────────────────────────────────────────────────────────────

export type EngineWarning =
  | { code: "context_over_model"; max: number }
  | { code: "context_over_memory"; max: number }
  | { code: "memory_share_high" }
  | { code: "memory_share_low" }
  | { code: "tool_parser_without_auto" }
  | { code: "unknown_tool_parser"; value: string }
  | { code: "unknown_reasoning_parser"; value: string }
  | { code: "remote_code" }
  | { code: "extra_invalid"; tokens: string[] };

export function checkSettings(
  config: EngineConfig,
  extra: string,
  model?: ModelFacts,
  hardware?: HardwareFacts,
): EngineWarning[] {
  const warnings: EngineWarning[] = [];
  const context = Number(config.max_model_len);
  if (Number.isFinite(context) && context > 0) {
    if (model?.maxContext && context > model.maxContext) {
      warnings.push({ code: "context_over_model", max: model.maxContext });
    } else if (hardware?.maxContextThatFits && context > hardware.maxContextThatFits) {
      warnings.push({ code: "context_over_memory", max: hardware.maxContextThatFits });
    }
  }
  const share = Number(config.gpu_memory_utilization);
  if (Number.isFinite(share) && share > 0.95) warnings.push({ code: "memory_share_high" });
  if (Number.isFinite(share) && share > 0 && share < 0.05) warnings.push({ code: "memory_share_low" });
  if (config.tool_call_parser && !config.enable_auto_tool_choice) warnings.push({ code: "tool_parser_without_auto" });
  if (typeof config.tool_call_parser === "string" && !CATALOG.toolParsers.includes(config.tool_call_parser)) {
    warnings.push({ code: "unknown_tool_parser", value: config.tool_call_parser });
  }
  if (typeof config.reasoning_parser === "string" && config.reasoning_parser
      && !CATALOG.reasoningParsers.includes(config.reasoning_parser)) {
    warnings.push({ code: "unknown_reasoning_parser", value: config.reasoning_parser });
  }
  if (config.trust_remote_code === true) warnings.push({ code: "remote_code" });
  const { issues } = parseExtraFlags(extra);
  if (issues.length) warnings.push({ code: "extra_invalid", tokens: issues });
  return warnings;
}

// ── Numbers people read ─────────────────────────────────────────────────

export function formatBytes(bytes: number | null | undefined, digits = 1): string {
  if (bytes == null || !Number.isFinite(bytes)) return "—";
  const gb = bytes / 1e9;
  if (gb >= 1) return `${gb.toFixed(gb >= 100 ? 0 : digits)} GB`;
  return `${Math.round(bytes / 1e6)} MB`;
}

export function formatTokens(tokens: number | null | undefined): string {
  if (tokens == null || !Number.isFinite(tokens)) return "—";
  if (tokens >= 1024 && tokens % 1024 === 0) return `${tokens / 1024}K`;
  if (tokens >= 1000) return `${Math.round(tokens / 100) / 10}K`;
  return String(tokens);
}
