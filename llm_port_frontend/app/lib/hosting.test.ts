import { describe, expect, it } from "vitest";

import type { Model } from "~/api/llm";
import type { Fit, MarketCluster } from "~/api/marketplace";

import {
  buildSpec,
  defaultGpuChoice,
  deployableModels,
  fitSummary,
  formatParams,
  gpuChoices,
  suggestChatName,
  suggestDeploymentName,
} from "./hosting";

function model(id: string, overrides: Partial<Model> = {}): Model {
  return {
    id,
    display_name: "Qwen2.5-0.5B-Instruct",
    source: "huggingface",
    hf_repo_id: "Qwen/Qwen2.5-0.5B-Instruct",
    hf_revision: null,
    license_ack_required: false,
    tags: null,
    status: "available",
    instances: [],
    created_at: "2026-09-21T10:24:55Z",
    updated_at: "2026-09-21T10:24:55Z",
    ...overrides,
  };
}

function fit(overrides: Partial<Fit> = {}): Fit {
  return {
    status: "fits",
    gpus_per_copy: 1,
    tensor_parallel: 1,
    copies: 2,
    copies_now: 2,
    context: 32768,
    max_context: 40960,
    needed_bytes_per_gpu: 24e9,
    gpu_bytes: 128e9,
    weights_bytes: 16e9,
    kv_bytes_per_token: 147456,
    suggested_gpu_memory_utilization: null,
    shareable: false,
    notes: [],
    ...overrides,
  };
}

const PAIR: MarketCluster = {
  environment_id: "c1",
  name: "dgx-pair",
  status: "ready",
  machines: [],
  vllm_version: "0.27.1",
  gpu_count: 2,
  gpu_bytes: 128e9,
  accelerator: "NVIDIA GB10",
};

describe("deployableModels", () => {
  it("leaves out a failed download, which has nothing to deploy", () => {
    const offered = deployableModels([
      model("ok"),
      model("broken", { status: "failed" }),
      model("going", { status: "deleting" }),
    ]);
    expect(offered.map((o) => o.model.id)).toEqual(["ok"]);
  });

  it("offers one still downloading, and says so", () => {
    const [only] = deployableModels([model("fetching", { status: "downloading" })]);
    expect(only.detail).toContain("still downloading");
  });

  it("tells two records of the same name apart by the date added", () => {
    const offered = deployableModels([
      model("old", { created_at: "2026-03-01T15:36:28Z" }),
      model("new", { created_at: "2026-09-21T10:24:55Z" }),
    ]);
    const [a, b] = offered.map((o) => o.detail);
    expect(a).toMatch(/^Qwen\/Qwen2\.5-0\.5B-Instruct · added /);
    expect(a).not.toEqual(b);
  });
});

describe("the spec", () => {
  it("puts the chat name in as the alias, and leaves it out when cleared", () => {
    expect(buildSpec({ copies: 1, gpusPerCopy: 1, chatName: "qwen2.5-0.5b-instruct" }).service).toEqual({
      path: "/v1",
      openai: true,
      alias: "qwen2.5-0.5b-instruct",
    });
    expect(buildSpec({ copies: 1, gpusPerCopy: 1, chatName: "  " }).service).not.toHaveProperty("alias");
  });

  it("carries the engine settings, but not the copy's shape", () => {
    const spec = buildSpec({
      copies: 2,
      gpusPerCopy: 2,
      engineConfig: { max_model_len: 32768, tensor_parallel_size: 8 },
    });
    expect(spec.engine.config).toEqual({ max_model_len: 32768 });
    expect(spec.topology).toEqual({ tensor_parallel_size: 2 });
    expect(spec.resources.replica.gpus).toBe(2);
    expect(spec.scale.replicas).toBe(2);
  });

  it("a copy sharing a card claims its share and no more", () => {
    const spec = buildSpec({ copies: 1, gpusPerCopy: 0.25, engineConfig: {} });
    expect(spec.resources.replica.gpus).toBe(0.25);
    expect(spec.engine.config.gpu_memory_utilization).toBe(0.25);
    expect(spec).not.toHaveProperty("topology");
    const asked = buildSpec({ copies: 1, gpusPerCopy: 0.25, engineConfig: { gpu_memory_utilization: 0.9 } });
    expect(asked.engine.config.gpu_memory_utilization).toBe(0.25);
  });
});

describe("names", () => {
  it("proposes the repository's name, so one model shares one name across clusters", () => {
    expect(suggestChatName(model("m"))).toBe("qwen2.5-0.5b-instruct");
    expect(suggestChatName("Qwen/Qwen3-8B")).toBe("qwen3-8b");
    expect(suggestChatName(undefined)).toBe("");
  });

  it("makes a deployment name the backend accepts", () => {
    expect(suggestDeploymentName("Qwen/Qwen2.5-0.5B-Instruct")).toBe("qwen2-5-0-5b-instruct");
    expect(suggestDeploymentName("org/_Weird__Name_")).toBe("weird-name");
  });
});

describe("the sizes a copy can take", () => {
  it("offers sharing a card when the model is small enough, and plans it", () => {
    const f = fit({ gpus_per_copy: 0.2, copies: 8, shareable: true, suggested_gpu_memory_utilization: 0.2 });
    const choices = gpuChoices(f, PAIR);
    expect(choices.map((c) => c.value)).toEqual([0.2, 1, 2]);
    // The fit check's count (four 0.2 shares under 0.9 of a card), not 1/0.2 per card.
    expect(choices[0].maxCopies).toBe(8);
    expect(defaultGpuChoice(f, choices)).toBe(0.2);
  });

  it("starts a large model at the split it needs", () => {
    const f = fit({ gpus_per_copy: 2, tensor_parallel: 2, copies: 1, copies_now: 1 });
    const choices = gpuChoices(f, PAIR);
    expect(choices.map((c) => c.value)).toEqual([2]);
    expect(defaultGpuChoice(f, choices)).toBe(2);
  });

  it("offers every whole size when nothing is known", () => {
    expect(gpuChoices(null, PAIR).map((c) => c.value)).toEqual([1, 2]);
    // A cluster that has reported no accelerator still takes one copy of one whole size.
    expect(gpuChoices(null, { ...PAIR, gpu_count: 0 })).toEqual([{ value: 1, maxCopies: 1 }]);
  });
});

describe("words", () => {
  it("says what a fit means", () => {
    expect(fitSummary(fit()).label).toBe("Fits on 1 GPU");
    expect(fitSummary(fit({ gpus_per_copy: 2 })).label).toBe("Fits on 2 GPUs");
    expect(fitSummary(fit({ gpus_per_copy: 0.2 })).label).toBe("Shares a GPU");
    expect(fitSummary(fit({ copies_now: 0 })).tone).toBe("warning");
    expect(fitSummary(fit({ status: "too_large" })).tone).toBe("error");
  });

  it("sizes a model the way its name does", () => {
    expect(formatParams(8.2)).toBe("8.2B");
    expect(formatParams(30.5, 3.3)).toBe("31B · 3.3B active");
    expect(formatParams(0.6)).toBe("600M");
    expect(formatParams(null)).toBeNull();
  });
});
