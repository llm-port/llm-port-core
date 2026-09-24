/**
 * Hosting a model: the fit sizes the copy, the suggested settings fill the
 * engine form, and what is sent is what the operator saw.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { inferenceApi, type InferenceDeployment } from "~/api/inference";
import type { Model } from "~/api/llm";
import { marketplaceApi, type Fit, type MarketCluster, type MarketDetail } from "~/api/marketplace";

import { HostModelDialog } from "./HostModelDialog";

const PAIR: MarketCluster = {
  environment_id: "11111111-1111-1111-1111-111111111111",
  name: "dgx-pair",
  status: "ready",
  machines: [],
  vllm_version: "0.27.1",
  gpu_count: 2,
  gpu_bytes: 128e9,
  accelerator: "NVIDIA GB10",
};

const SMALL: MarketCluster = {
  ...PAIR,
  environment_id: "22222222-2222-2222-2222-222222222222",
  name: "workstation",
  gpu_count: 1,
  gpu_bytes: 24e9,
  accelerator: "TITAN RTX",
};

function fit(overrides: Partial<Fit> = {}): Fit {
  return {
    status: "fits",
    gpus_per_copy: 0.25,
    tensor_parallel: 1,
    copies: 8,
    copies_now: 6,
    context: 32768,
    max_context: 40960,
    needed_bytes_per_gpu: 22e9,
    gpu_bytes: 128e9,
    weights_bytes: 16.4e9,
    kv_bytes_per_token: 147456,
    suggested_gpu_memory_utilization: 0.25,
    shareable: true,
    notes: [],
    ...overrides,
  };
}

const DETAIL: MarketDetail = {
  hub: "online",
  model: {
    repo_id: "Qwen/Qwen3-8B",
    name: "Qwen3-8B",
    author: "Qwen",
    params_b: 8.2,
    weights_bytes: 16.4e9,
    format: "safetensors",
    capabilities: ["tools", "reasoning"],
    task: "chat",
    runnable: true,
    max_context: 40960,
    kv_bytes_per_token: 147456,
  },
  clusters: [PAIR, SMALL],
  cluster_id: PAIR.environment_id,
  fits: {
    [PAIR.environment_id]: fit(),
    [SMALL.environment_id]: fit({ status: "too_large", gpus_per_copy: null, copies: 0, copies_now: 0 }),
  },
  suggested: {
    config: {
      enable_auto_tool_choice: true,
      tool_call_parser: "hermes",
      reasoning_parser: "qwen3",
      max_model_len: 32768,
      gpu_memory_utilization: 0.25,
    },
    reasons: { tool_call_parser: "model_family" },
  },
  local: null,
};

function renderDialog(props: Partial<React.ComponentProps<typeof HostModelDialog>> = {}) {
  const onHosted = vi.fn();
  render(
    <MemoryRouter>
      <HostModelDialog open onClose={() => {}} onHosted={onHosted} {...props} />
    </MemoryRouter>,
  );
  return { onHosted };
}

afterEach(() => vi.restoreAllMocks());

describe("HostModelDialog", () => {
  it("hosts a Hugging Face model at the size the fit planned, with the suggested settings", async () => {
    vi.spyOn(marketplaceApi, "clusters").mockResolvedValue([PAIR, SMALL]);
    vi.spyOn(marketplaceApi, "detail").mockResolvedValue(DETAIL);
    const host = vi.spyOn(marketplaceApi, "host").mockResolvedValue({
      deployment_id: "dep-1", model_id: "m-1", download: "started", download_error: null,
    });
    const reconcile = vi.spyOn(inferenceApi, "reconcileDeployment").mockResolvedValue({} as InferenceDeployment);
    const { onHosted } = renderDialog({ repoId: "Qwen/Qwen3-8B" });

    // The cluster it fits is chosen; the one it does not fit cannot be.
    expect(await screen.findByText("Shares a GPU")).toBeInTheDocument();
    expect(screen.getByText("Too large")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("host-gpus-0.25")).toHaveAttribute("aria-pressed", "true"));
    expect(screen.getByText(/The cluster has room for 8 copies/)).toBeInTheDocument();

    await userEvent.click(screen.getByTestId("host-next"));
    // The engine step starts from the suggested settings.
    expect(screen.getByLabelText("Tool calling")).toBeChecked();
    expect(screen.getByTestId("engine-preview")).toHaveTextContent("--tool-call-parser hermes");

    await userEvent.click(screen.getByTestId("host-next"));
    expect(screen.getByTestId("host-name")).toHaveValue("qwen3-8b");
    expect(screen.getByTestId("host-chat-name")).toHaveValue("qwen3-8b");
    expect(screen.getByTestId("host-download-note")).toHaveTextContent("16.4 GB");

    await userEvent.click(screen.getByTestId("host-submit"));
    await waitFor(() => expect(onHosted).toHaveBeenCalledWith("dep-1"));
    expect(host).toHaveBeenCalledWith({
      repo_id: "Qwen/Qwen3-8B",
      environment_id: PAIR.environment_id,
      name: "qwen3-8b",
      alias: "qwen3-8b",
      copies: 1,
      gpus_per_copy: 0.25,
      engine_config: DETAIL.suggested.config,
    });
    expect(reconcile).toHaveBeenCalledWith("dep-1");
  });

  it("taking a whole card gives back the share set for sharing", async () => {
    vi.spyOn(marketplaceApi, "clusters").mockResolvedValue([PAIR]);
    vi.spyOn(marketplaceApi, "detail").mockResolvedValue({ ...DETAIL, clusters: [PAIR] });
    const host = vi.spyOn(marketplaceApi, "host").mockResolvedValue({
      deployment_id: "dep-2", model_id: "m-1", download: "kept", download_error: null,
    });
    vi.spyOn(inferenceApi, "reconcileDeployment").mockResolvedValue({} as InferenceDeployment);
    renderDialog({ repoId: "Qwen/Qwen3-8B" });

    await userEvent.click(await screen.findByTestId("host-gpus-1"));
    await userEvent.click(screen.getByTestId("host-next"));
    await userEvent.click(screen.getByTestId("host-next"));
    await userEvent.click(screen.getByTestId("host-submit"));
    await waitFor(() => expect(host).toHaveBeenCalled());
    const sent = host.mock.calls[0][0];
    expect(sent.gpus_per_copy).toBe(1);
    expect(sent.engine_config.gpu_memory_utilization).toBeUndefined();
  });

  it("deploys a model the server keeps from a local path, without asking the Hub", async () => {
    const local: Model = {
      id: "local-1",
      display_name: "my-finetune",
      source: "local_path",
      hf_repo_id: null,
      hf_revision: null,
      license_ack_required: false,
      tags: null,
      status: "available",
      instances: [],
      created_at: "2026-09-21T10:24:55Z",
      updated_at: "2026-09-21T10:24:55Z",
    };
    vi.spyOn(marketplaceApi, "clusters").mockResolvedValue([PAIR]);
    const detail = vi.spyOn(marketplaceApi, "detail");
    const create = vi.spyOn(inferenceApi, "createDeployment").mockResolvedValue({ id: "dep-3" } as InferenceDeployment);
    vi.spyOn(inferenceApi, "reconcileDeployment").mockResolvedValue({} as InferenceDeployment);
    const { onHosted } = renderDialog({ models: [local], clusterId: PAIR.environment_id, lockCluster: true });

    await userEvent.click(screen.getByTestId("host-kept-local-1"));
    await userEvent.click(screen.getByTestId("host-next"));
    expect(await screen.findByText(/added from a local path/)).toBeInTheDocument();
    await userEvent.click(screen.getByTestId("host-gpus-2"));
    await userEvent.click(screen.getByTestId("host-next"));
    await userEvent.click(screen.getByTestId("host-next"));
    await userEvent.click(screen.getByTestId("host-submit"));

    await waitFor(() => expect(onHosted).toHaveBeenCalledWith("dep-3"));
    expect(detail).not.toHaveBeenCalled();
    const payload = create.mock.calls[0][0];
    expect(payload).toMatchObject({ environment_id: PAIR.environment_id, model_id: "local-1", name: "my-finetune" });
    expect(payload.spec).toMatchObject({
      resources: { replica: { gpus: 2 } },
      topology: { tensor_parallel_size: 2 },
      service: { alias: "my-finetune" },
    });
  });
});
