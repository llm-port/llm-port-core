import { describe, expect, it } from "vitest";

import type { FoundEntry } from "~/api/inference";
import { ownerPath } from "~/api/llm";

import { suggestName } from "./FoundVllmCard";

function entry(served: string[], model: string | null = "Qwen/Qwen3-Embedding-0.6B"): FoundEntry {
  return {
    node: { id: "n1", name: "workstation", host: "10.88.10.220", status: "healthy" },
    reported_at: null,
    container: {
      name: "Qwen3-Embed", id: "abc", image: "vllm/vllm-openai:v0.8.5", state: "running",
      started_at: null, finished_at: null, model, served_model_names: served, port: 8000, host_port: 7997,
      task: "embeddings", task_from: "flags", api_key_required: false, managed_by: null, gpus: "all",
      settings: {}, args: [],
    },
    base_url: "http://10.88.10.220:7997/v1",
    adoption: null,
    can_route: true,
    reason: null,
    check: null,
  };
}

describe("the name suggested for a found container", () => {
  it("is the name it answers to", () => {
    expect(suggestName(entry(["qwen3-embedding"]))).toBe("qwen3-embedding");
  });

  it("drops the organisation when it answers to its repository name", () => {
    expect(suggestName(entry(["Qwen/Qwen3.8-27B-FP8"]))).toBe("qwen3.8-27b-fp8");
  });
});

describe("where an owned provider is managed", () => {
  it("is the machine's page for a found container", () => {
    expect(
      ownerPath({ kind: "found_container", id: "a1", name: "Qwen3-Embed on ws", state: "running", model_name: null, node_id: "n1" }),
    ).toBe("/admin/nodes/n1");
  });

  it("is the deployment's page for a deployment", () => {
    expect(ownerPath({ kind: "inference_deployment", id: "d1", name: "qwen-chat", state: "running", model_name: null })).toBe(
      "/admin/deployments/d1",
    );
  });
});
