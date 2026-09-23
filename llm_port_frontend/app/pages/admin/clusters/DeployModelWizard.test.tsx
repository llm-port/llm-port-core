import { describe, expect, it } from "vitest";

import type { Model } from "~/api/llm";

import { buildSpec, deployableModels, suggestChatName } from "./DeployModelWizard";

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
    // What the walkthrough found: the same repo, kept twice.
    const offered = deployableModels([
      model("old", { created_at: "2026-03-01T15:36:28Z" }),
      model("new", { created_at: "2026-09-21T10:24:55Z" }),
    ]);
    const [a, b] = offered.map((o) => o.detail);
    expect(a).toMatch(/^Qwen\/Qwen2\.5-0\.5B-Instruct · added /);
    expect(a).not.toEqual(b);
  });

  it("keeps a unique name plain", () => {
    const [only] = deployableModels([
      model("emb", { display_name: "Qwen/Qwen3-Embedding-0.6B", hf_repo_id: "Qwen/Qwen3-Embedding-0.6B" }),
    ]);
    expect(only.detail).toBe("");
  });
});

describe("offering the deployment in chat", () => {
  it("puts the chat name in the spec as the alias", () => {
    // Without it the deployment served and never appeared in chat: the
    // backend never invents an alias, and nothing here set one.
    expect(buildSpec(1, 1, "qwen2.5-0.5b-instruct").service).toEqual({
      path: "/v1",
      openai: true,
      alias: "qwen2.5-0.5b-instruct",
    });
  });

  it("leaves the alias out when the chat name is cleared", () => {
    expect(buildSpec(1, 1, "  ").service).toEqual({ path: "/v1", openai: true });
    expect(buildSpec(1, 1).service).not.toHaveProperty("alias");
  });

  it("proposes the repository's name, so one model shares one name across clusters", () => {
    expect(suggestChatName(model("m"))).toBe("qwen2.5-0.5b-instruct");
    expect(suggestChatName(undefined)).toBe("");
  });
});
