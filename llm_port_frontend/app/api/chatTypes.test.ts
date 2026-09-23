import { describe, expect, it } from "vitest";

import { isChatModel, type ModelAlias } from "./chatTypes";

const model = (alias: string, kind?: string | null): ModelAlias => ({ alias, description: null, enabled: true, kind });

describe("the chat model picker", () => {
  it("offers chat models, and routes that do not say what they are", () => {
    expect(isChatModel(model("qwen2.5-0.5b-instruct", "chat"))).toBe(true);
    expect(isChatModel(model("qwen2.5-0.5b-instruct", null))).toBe(true);
    expect(isChatModel(model("older-route"))).toBe(true);
  });

  it("leaves out embedding and rerank models, which fail on the first message", () => {
    expect(isChatModel(model("qwen3-embedding-0.6b", "embeddings"))).toBe(false);
    expect(isChatModel(model("qwen3-reranker", "scoring"))).toBe(false);
  });
});
