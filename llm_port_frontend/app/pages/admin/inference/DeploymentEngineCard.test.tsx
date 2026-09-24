/**
 * Changing how vLLM runs a deployment: the summary says what differs from
 * vLLM's defaults, and saving replaces only the engine settings.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { inferenceApi, type InferenceDeployment } from "~/api/inference";
import { marketplaceApi } from "~/api/marketplace";

import { DeploymentEngineCard } from "./DeploymentEngineCard";

const DEPLOYMENT = {
  id: "dep-1",
  environment_id: "env-1",
  model_id: "m-1",
  name: "qwen3-8b",
  description: null,
  spec: {
    api_version: "inference.llmport.ai/v1alpha1",
    engine: { name: "vllm", config: { max_model_len: 32768, enable_auto_tool_choice: true, tool_call_parser: "hermes" } },
    scale: { replicas: 2 },
    resources: { replica: { gpus: 1 } },
    service: { path: "/v1", openai: true, alias: "qwen3-8b" },
  },
  desired_state: "active",
  phase: "running",
  generation: 3,
  observed_generation: 3,
  observed_status: {},
  phase_message: null,
  ready_replicas: 2,
  total_replicas: 2,
  created_at: "2026-09-24T10:00:00Z",
  updated_at: "2026-09-24T10:00:00Z",
} as InferenceDeployment;

afterEach(() => vi.restoreAllMocks());

describe("DeploymentEngineCard", () => {
  it("shows what differs from vLLM's defaults, as flags", () => {
    render(<DeploymentEngineCard deployment={DEPLOYMENT} repoId={null} onSaved={() => {}} />);
    const summary = screen.getByTestId("engine-summary");
    expect(summary).toHaveTextContent("--max-model-len 32768");
    expect(summary).toHaveTextContent("--enable-auto-tool-choice");
  });

  it("saves the engine settings and nothing else, then asks for a restart", async () => {
    vi.spyOn(marketplaceApi, "detail").mockRejectedValue(new Error("offline"));
    const update = vi.spyOn(inferenceApi, "updateDeployment").mockResolvedValue(DEPLOYMENT);
    const reconcile = vi.spyOn(inferenceApi, "reconcileDeployment").mockResolvedValue(DEPLOYMENT);
    const onSaved = vi.fn();
    render(<DeploymentEngineCard deployment={DEPLOYMENT} repoId="Qwen/Qwen3-8B" onSaved={onSaved} />);

    await userEvent.click(screen.getByTestId("engine-edit"));
    expect(screen.getByText(/restarts every copy/)).toBeInTheDocument();
    expect(screen.getByTestId("engine-save")).toBeDisabled();
    await userEvent.click(screen.getByLabelText("Tool calling"));
    await userEvent.click(screen.getByTestId("engine-save"));

    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    const spec = update.mock.calls[0][1].spec as typeof DEPLOYMENT.spec;
    expect(spec.engine).toEqual({ name: "vllm", config: { max_model_len: 32768 } });
    expect(spec.scale).toEqual({ replicas: 2 });
    expect(spec.service).toEqual(DEPLOYMENT.spec.service);
    expect(reconcile).toHaveBeenCalledWith("dep-1");
  });
});
