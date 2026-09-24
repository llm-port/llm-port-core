/**
 * Putting a model on a cluster: the spec, the names, the sizes a copy can take.
 *
 * Shared by the marketplace's host dialog and every page that deploys a model
 * the server already keeps, so both build the same document the backend's
 * `services/marketplace/host.py` builds.
 */
import i18n from "i18next";

import type { Fit, MarketCluster } from "~/api/marketplace";
import type { Model } from "~/api/llm";
import { CLUSTER_SHAPE_KEYS, formatBytes as formatGb, type EngineConfig } from "~/lib/engine";

export interface SpecOptions {
  copies: number;
  /** Accelerators one copy spans, or a fraction of one it shares. */
  gpusPerCopy: number;
  /** The name offered in chat and at the gateway; blank serves by endpoint only. */
  chatName?: string;
  engineConfig?: EngineConfig;
  revision?: string | null;
}

/**
 * The deployment document for these choices.
 *
 * ``chatName`` becomes ``service.alias``: the backend never makes one up, so a
 * deployment without one serves and never appears in chat.
 */
export function buildSpec({ copies, gpusPerCopy, chatName = "", engineConfig = {}, revision }: SpecOptions) {
  const alias = chatName.trim();
  const engine: EngineConfig = {};
  for (const [key, value] of Object.entries(engineConfig)) {
    if (!CLUSTER_SHAPE_KEYS.has(key) && value !== undefined && value !== null) engine[key] = value;
  }
  const sharing = gpusPerCopy < 1;
  if (sharing) {
    // A copy that may use more of the card than it reserved crowds the others on it.
    const share = Math.round(gpusPerCopy * 100) / 100;
    const asked = Number(engine.gpu_memory_utilization);
    engine.gpu_memory_utilization = Number.isFinite(asked) && asked > 0 ? Math.min(asked, share) : share;
  }
  const tp = sharing ? 1 : Math.max(1, Math.round(gpusPerCopy));
  return {
    api_version: "inference.llmport.ai/v1alpha1",
    engine: { name: "vllm", config: engine },
    scale: { replicas: Math.max(1, Math.round(copies)) },
    resources: { replica: { gpus: sharing ? Math.round(gpusPerCopy * 100) / 100 : tp } },
    service: { path: "/v1", openai: true, ...(alias ? { alias } : {}) },
    ...(tp > 1 ? { topology: { tensor_parallel_size: tp } } : {}),
    ...(revision ? { artifacts: { source: "sync", revision } } : {}),
  };
}

function baseName(source: Model | string | undefined | null): string {
  if (!source) return "";
  const name = typeof source === "string" ? source : source.hf_repo_id || source.display_name || "";
  return (name.split("/").pop() ?? "").trim();
}

/**
 * The name to offer a model under in chat: the repository's own, lower-cased.
 * Two deployments of one model on two clusters then share it, and chat routes across both.
 */
export function suggestChatName(source: Model | string | undefined | null): string {
  return baseName(source).toLowerCase();
}

/** A deployment name the backend accepts. */
export function suggestDeploymentName(source: Model | string | undefined | null): string {
  return baseName(source)
    .toLowerCase()
    .replace(/[^a-z0-9-]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 120);
}

export const DEPLOYMENT_NAME = /^[a-z0-9][a-z0-9-]*$/;

/**
 * The kept models worth offering, each with what tells it apart.
 *
 * A failed download has no files, so it is left out. One still downloading is
 * offered -- the deployment waits for it -- but says so. Where two records share
 * a name, the date added is the difference.
 */
export function deployableModels(models: Model[]): { model: Model; detail: string }[] {
  const usable = models.filter((m) => m.status === "available" || m.status === "downloading");
  const named = new Map<string, number>();
  for (const m of usable) named.set(m.display_name, (named.get(m.display_name) ?? 0) + 1);
  return usable.map((model) => {
    const parts: string[] = [];
    if (model.hf_repo_id && model.hf_repo_id !== model.display_name) parts.push(model.hf_repo_id);
    if (model.status === "downloading") parts.push(i18n.t("hosting.still_downloading"));
    if ((named.get(model.display_name) ?? 0) > 1) {
      parts.push(i18n.t("hosting.added_on", { date: new Date(model.created_at).toLocaleDateString(i18n.language) }));
    }
    return { model, detail: parts.join(" · ") };
  });
}

// ── How big a copy is ──────────────────────────────────────────────────

export interface GpuChoice {
  /** Accelerators per copy: a fraction when copies share one. */
  value: number;
  /** How many copies the cluster holds at this size. */
  maxCopies: number;
}

const TP_SIZES = [1, 2, 4, 8, 16];

/**
 * The sizes a copy can take on *cluster*, smallest first.
 *
 * A model that fits one accelerator can also share one when the fit check
 * says it is small enough; a larger one spans the smallest power of two that
 * holds it, or more for speed. With no fit (a local model with no config)
 * every whole size the cluster has is offered; with no accelerator reported,
 * one copy of the smallest whole size.
 */
export function gpuChoices(fit: Fit | null | undefined, cluster: MarketCluster | null | undefined): GpuChoice[] {
  const count = cluster?.gpu_count ?? 0;
  const planned = fit?.status === "fits" ? fit.gpus_per_copy : null;
  const smallest = fit?.status === "fits" ? Math.max(1, fit.tensor_parallel ?? 1) : 1;
  if (count === 0) {
    // Nothing reported: a machine that has only just joined, or none with an
    // accelerator. The size is the operator's call, and the scheduler's to refuse.
    return [{ value: smallest, maxCopies: 1 }];
  }
  const choices: GpuChoice[] = [];
  if (planned !== null && planned !== undefined && planned < 1) {
    // The fit check's own count: it keeps each card below the most vLLM may
    // take (0.9), so four shares of 0.2 fit a card, not five.
    choices.push({ value: planned, maxCopies: Math.max(1, fit?.copies ?? 1) });
  }
  for (const size of TP_SIZES) {
    if (size >= smallest && size <= count) choices.push({ value: size, maxCopies: Math.floor(count / size) });
  }
  return choices;
}

/** The size the fit check planned, or the smallest there is. */
export function defaultGpuChoice(fit: Fit | null | undefined, choices: GpuChoice[]): number {
  const planned = fit?.status === "fits" ? fit.gpus_per_copy : null;
  const match = choices.find((c) => planned !== null && planned !== undefined && Math.abs(c.value - planned) < 1e-6);
  return (match ?? choices[0])?.value ?? 1;
}

/** "a quarter of one", "1", "2" -- how much of the cluster one copy takes. */
export function gpuChoiceLabel(value: number, accelerator?: string | null): string {
  if (value < 1) {
    return i18n.t("hosting.gpu_share", { percent: Math.round(value * 100), accelerator: accelerator || i18n.t("hosting.an_accelerator") });
  }
  return i18n.t("hosting.gpu_whole", { count: value });
}

// ── Words for a model's size and fit ───────────────────────────────────

/** "8B", "30B · 3B active", "600M". */
export function formatParams(paramsB: number | null | undefined, activeB?: number | null): string | null {
  if (!paramsB) return null;
  const one = (b: number) => (b >= 1 ? `${Number(b.toFixed(b >= 10 ? 0 : 1))}B` : `${Math.round(b * 1000)}M`);
  return activeB && activeB < paramsB ? i18n.t("marketplace.params_active", { total: one(paramsB), active: one(activeB) }) : one(paramsB);
}

export type FitTone = "success" | "info" | "warning" | "error" | "default";

/** A fit in a few words, and how loudly to say it. */
export function fitSummary(fit: Fit | null | undefined): { label: string; tone: FitTone } {
  if (!fit) return { label: i18n.t("marketplace.fit.unknown"), tone: "default" };
  switch (fit.status) {
    case "fits": {
      if (fit.copies_now === 0) return { label: i18n.t("marketplace.fit.busy_now"), tone: "warning" };
      if ((fit.gpus_per_copy ?? 1) < 1) return { label: i18n.t("marketplace.fit.shares"), tone: "success" };
      return { label: i18n.t("marketplace.fit.fits", { count: fit.gpus_per_copy ?? 1 }), tone: "success" };
    }
    case "too_large":
      return { label: i18n.t("marketplace.fit.too_large"), tone: "error" };
    case "no_accelerators":
      return { label: i18n.t("marketplace.fit.no_accelerators"), tone: "default" };
    default:
      return { label: i18n.t("marketplace.fit.unknown"), tone: "default" };
  }
}

/** The sentence under a fit: what it takes, and what is in the way. */
export function fitDetail(fit: Fit | null | undefined, cluster?: MarketCluster | null): string {
  if (!fit) return "";
  const parts: string[] = [];
  if (fit.status === "fits") {
    if (fit.needed_bytes_per_gpu && fit.gpu_bytes) {
      parts.push(i18n.t("marketplace.fit.detail_needs", {
        needed: formatGb(fit.needed_bytes_per_gpu),
        total: formatGb(fit.gpu_bytes),
      }));
    }
    parts.push(i18n.t("marketplace.fit.detail_copies", { count: fit.copies }));
    if (fit.copies_now < fit.copies) parts.push(i18n.t("marketplace.fit.detail_copies_now", { count: fit.copies_now }));
  } else if (fit.status === "too_large" && fit.needed_bytes_per_gpu) {
    parts.push(i18n.t("marketplace.fit.detail_too_large", {
      needed: formatGb(fit.weights_bytes ?? fit.needed_bytes_per_gpu),
      total: formatGb((cluster?.gpu_bytes ?? fit.gpu_bytes ?? 0) * (cluster?.gpu_count ?? 1)),
    }));
  }
  for (const note of fit.notes) {
    if (note === "busy_now" || note === "no_accelerators" || note === "size_unknown") continue;
    parts.push(i18n.t(`marketplace.fit.note.${note}`, { defaultValue: "" }));
  }
  return parts.filter(Boolean).join(" ");
}

/** 1234567 → "1.2M". */
export function formatCount(value: number | null | undefined): string {
  if (!value) return "0";
  return new Intl.NumberFormat(i18n.language || "en", { notation: "compact", maximumFractionDigits: 1 }).format(value);
}
