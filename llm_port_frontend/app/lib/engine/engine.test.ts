import { describe, expect, it } from "vitest";

import {
  CATALOG,
  applyPreset,
  checkSettings,
  commandLine,
  fromContainerFields,
  parseExtraFlags,
  toClusterConfig,
  toContainerFields,
  toFlag,
  toKey,
} from ".";

describe("the flag catalog", () => {
  it("is vLLM's own, with the parsers the runtime registers", () => {
    expect(CATALOG.vllmVersion).toBe("0.27.1");
    expect(CATALOG.toolParsers).toContain("hermes");
    expect(CATALOG.toolParsers).toContain("qwen3_coder");
    expect(CATALOG.reasoningParsers).toContain("qwen3");
    const context = CATALOG.flags.find((f) => f.key === "max_model_len");
    expect(context?.type).toBe("integer");
    expect(CATALOG.flags.some((f) => f.flag === "port" || f.flag === "api-key")).toBe(false);
  });

  it("maps flag and key names both ways", () => {
    expect(toFlag("max_model_len")).toBe("max-model-len");
    expect(toKey("--max-model-len")).toBe("max_model_len");
  });
});

describe("extra flags typed as text", () => {
  it("becomes typed settings", () => {
    const { config, issues } = parseExtraFlags("--max-num-seqs 64 --async-scheduling --kv-cache-dtype=fp8");
    expect(issues).toEqual([]);
    expect(config).toEqual({ max_num_seqs: 64, async_scheduling: true, kv_cache_dtype: "fp8" });
  });

  it("refuses anything that could escape the command", () => {
    const { issues } = parseExtraFlags("--chat-template $(rm -rf /) ; echo hi");
    expect(issues.length).toBeGreaterThan(0);
  });
});

describe("storage", () => {
  it("a cluster gets engine.config without the copy's shape", () => {
    const { config } = toClusterConfig({ max_model_len: 8192, tensor_parallel_size: 4 }, "--max-num-seqs 32");
    expect(config).toEqual({ max_model_len: 8192, max_num_seqs: 32 });
  });

  it("a container gets flags, and reads them back", () => {
    const fields = toContainerFields({ max_model_len: 8192, enable_prefix_caching: true }, "--seed 7");
    expect(fields.engine_args).toEqual({ "max-model-len": 8192, "enable-prefix-caching": true });
    expect(fields.extra_args).toEqual(["--seed", "7"]);
    const back = fromContainerFields(fields);
    expect(back.config).toEqual({ max_model_len: 8192, enable_prefix_caching: true });
    expect(back.extra).toBe("--seed 7");
  });

  it("the preview is the command vLLM would run", () => {
    expect(commandLine("Qwen/Qwen3-8B", { max_model_len: 32768, enable_auto_tool_choice: true }))
      .toBe("vllm serve Qwen/Qwen3-8B --max-model-len 32768 --enable-auto-tool-choice");
  });
});

describe("presets", () => {
  it("replace only what presets set, and keep the rest", () => {
    const current = { tool_call_parser: "hermes", enable_auto_tool_choice: true, max_num_seqs: 8 };
    const next = applyPreset(current, "many_users", { max_model_len: 32768 }, { maxContext: 40960 });
    expect(next).toMatchObject({ tool_call_parser: "hermes", max_num_seqs: 256, max_model_len: 32768 });
    const long = applyPreset(next, "long_context", {}, { maxContext: 40960 }, { maxContextThatFits: 35000 });
    expect(long.max_model_len).toBe(35000);
    expect(long.max_num_seqs).toBeUndefined();
  });
});

describe("checks", () => {
  it("warn about what will not start or will not work", () => {
    const codes = checkSettings(
      { max_model_len: 65536, tool_call_parser: "not-a-parser", gpu_memory_utilization: 0.99, trust_remote_code: true },
      "--x `bad`",
      { maxContext: 40960 },
    ).map((w) => w.code);
    expect(codes).toEqual(
      expect.arrayContaining([
        "context_over_model",
        "memory_share_high",
        "tool_parser_without_auto",
        "unknown_tool_parser",
        "remote_code",
        "extra_invalid",
      ]),
    );
    expect(checkSettings({}, "", {})).toEqual([]);
  });
});
