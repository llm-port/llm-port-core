import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import type { EngineConfig } from "~/lib/engine";

import { EngineSettingsEditor, type EngineSuggestion } from "./EngineSettingsEditor";

const SUGGESTED: EngineSuggestion = {
  config: { enable_auto_tool_choice: true, tool_call_parser: "hermes", reasoning_parser: "qwen3", max_model_len: 32768 },
  reasons: { tool_call_parser: "model_family", reasoning_parser: "model_family", max_model_len: "default_cap" },
};

function Harness({ initial = {} as EngineConfig, onValue }: { initial?: EngineConfig; onValue?: (v: EngineConfig) => void }) {
  const [value, setValue] = useState<EngineConfig>(initial);
  const [extra, setExtra] = useState("");
  return (
    <EngineSettingsEditor
      value={value}
      extra={extra}
      target="cluster"
      model={{ repoId: "Qwen/Qwen3-8B", maxContext: 40960, capabilities: ["tools", "reasoning"], kvBytesPerToken: 147456 }}
      hardware={{ gpuBytes: 130_663_055_360, tensorParallel: 1, maxContextThatFits: 40960 }}
      suggested={SUGGESTED}
      onChange={(v, e) => {
        setValue(v);
        setExtra(e);
        onValue?.(v);
      }}
    />
  );
}

describe("EngineSettingsEditor", () => {
  it("shows the suggested settings in words, and the command they make", () => {
    render(<Harness initial={{ ...SUGGESTED.config }} />);
    expect(screen.getByText("Tool calling")).toBeInTheDocument();
    expect(screen.getByLabelText("Tool calling")).toBeChecked();
    expect(screen.getByTestId("engine-preview")).toHaveTextContent(
      "vllm serve Qwen/Qwen3-8B --enable-auto-tool-choice --tool-call-parser hermes --reasoning-parser qwen3 --max-model-len 32768",
    );
    // The cost of the chosen context, from the model's own KV size.
    expect(screen.getByText(/One conversation this long takes about 4\.8 GB/)).toBeInTheDocument();
  });

  it("turning tools off removes both settings", async () => {
    let last: EngineConfig = {};
    render(<Harness initial={{ ...SUGGESTED.config }} onValue={(v) => (last = v)} />);
    await userEvent.click(screen.getByLabelText("Tool calling"));
    expect(last.enable_auto_tool_choice).toBeUndefined();
    expect(last.tool_call_parser).toBeUndefined();
    expect(last.reasoning_parser).toBe("qwen3");
  });

  it("a preset changes what it is for and says so", async () => {
    let last: EngineConfig = {};
    render(<Harness initial={{ ...SUGGESTED.config }} onValue={(v) => (last = v)} />);
    await userEvent.click(screen.getByTestId("engine-preset-many_users"));
    expect(last).toMatchObject({ max_num_seqs: 256, enable_prefix_caching: true, tool_call_parser: "hermes" });
  });

  it("warns before a setting that will not start", async () => {
    render(<Harness initial={{ max_model_len: 65536 }} />);
    expect(screen.getByTestId("engine-warning-context_over_model")).toHaveTextContent("40K");
  });

  it("every vLLM flag is reachable by search", async () => {
    let last: EngineConfig = {};
    render(<Harness onValue={(v) => (last = v)} />);
    await userEvent.click(screen.getByText(/All vLLM settings/));
    fireEvent.change(screen.getByPlaceholderText(/Search by flag/), { target: { value: "async-scheduling" } });
    const row = await screen.findByTestId("engine-flag-async-scheduling");
    await userEvent.click(within(row).getByRole("combobox"));
    await userEvent.click(screen.getByRole("option", { name: "On" }));
    expect(last).toEqual({ async_scheduling: true });
  });
});
