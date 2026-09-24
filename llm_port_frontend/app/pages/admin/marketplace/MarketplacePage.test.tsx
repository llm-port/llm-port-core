/**
 * The marketplace: models grouped by what they are for, each saying whether it fits.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { llmSettings } from "~/api/llm";
import { marketplaceApi, type Fit, type MarketCluster, type MarketModel } from "~/api/marketplace";

import MarketplacePage from "./MarketplacePage";

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

function fit(overrides: Partial<Fit> = {}): Fit {
  return {
    status: "fits", gpus_per_copy: 1, tensor_parallel: 1, copies: 2, copies_now: 2, context: 32768,
    max_context: 40960, needed_bytes_per_gpu: 40e9, gpu_bytes: 128e9, weights_bytes: 16e9,
    kv_bytes_per_token: null, suggested_gpu_memory_utilization: null, shareable: false, notes: [],
    ...overrides,
  };
}

function model(repo: string, overrides: Partial<MarketModel> = {}): MarketModel {
  return {
    repo_id: repo,
    name: repo.split("/")[1],
    author: repo.split("/")[0],
    downloads: 1_200_000,
    likes: 800,
    params_b: 8.2,
    weights_bytes: 16.4e9,
    format: "safetensors",
    capabilities: ["tools"],
    task: "chat",
    runnable: true,
    fit: fit(),
    local: null,
    ...overrides,
  };
}

function renderPage(url = "/admin/marketplace") {
  return render(
    <MemoryRouter initialEntries={[url]}>
      <MarketplacePage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.spyOn(marketplaceApi, "clusters").mockResolvedValue([PAIR]);
  vi.spyOn(marketplaceApi, "kept").mockResolvedValue({ items: [] });
  vi.spyOn(llmSettings, "getHFToken").mockResolvedValue({
    configured: false, source: null, storage_safe: true, check: null, username: null, token_name: null, role: null,
  });
});
afterEach(() => vi.restoreAllMocks());

describe("MarketplacePage", () => {
  it("groups the recommended models and says how each fits the chosen cluster", async () => {
    const recommended = vi.spyOn(marketplaceApi, "recommended").mockResolvedValue({
      hub: "online",
      cluster_id: PAIR.environment_id,
      groups: ["start", "chat"],
      items: [
        model("Qwen/Qwen2.5-0.5B-Instruct", {
          params_b: 0.5,
          fit: fit({ gpus_per_copy: 0.1 }),
          curated: {
            group: "start",
            blurb: "marketplace.blurb.tiny_verified",
            verified: { on: "DGX Spark pair (2 × GB10)", date: "2026-09-21" },
          },
          local: { model_id: "m1", status: "available", deployments: 1 },
        }),
        model("openai/gpt-oss-120b", {
          params_b: 120,
          fit: fit({ status: "fits", gpus_per_copy: 2, copies_now: 0 }),
          curated: { group: "chat", blurb: "marketplace.blurb.gpt_oss_large", verified: null },
        }),
      ],
    });
    renderPage();

    const start = await screen.findByTestId("market-group-start");
    expect(within(start).getByText("To try things out")).toBeInTheDocument();
    expect(within(start).getByText(/tested on this setup/)).toBeInTheDocument();
    expect(within(start).getByText("Tested here")).toBeInTheDocument();
    expect(within(start).getByText("Deployed 1 time")).toBeInTheDocument();
    expect(screen.getByTestId("fit-Qwen/Qwen2.5-0.5B-Instruct")).toHaveTextContent("Shares a GPU");
    expect(screen.getByTestId("fit-openai/gpt-oss-120b")).toHaveTextContent("Fits, but not right now");
    await waitFor(() => expect(recommended).toHaveBeenCalledWith(PAIR.environment_id));
  });

  it("says so when Hugging Face cannot be reached", async () => {
    vi.spyOn(marketplaceApi, "recommended").mockResolvedValue({
      hub: "offline", cluster_id: PAIR.environment_id, groups: [], items: [],
    });
    renderPage();
    expect(await screen.findByTestId("market-offline")).toBeInTheDocument();
  });

  it("search keeps what vLLM cannot serve out of the way until asked", async () => {
    vi.spyOn(marketplaceApi, "recommended").mockResolvedValue({ hub: "online", cluster_id: null, groups: [], items: [] });
    const search = vi.spyOn(marketplaceApi, "search").mockResolvedValue({
      hub: "online",
      cluster_id: PAIR.environment_id,
      items: [
        model("Qwen/Qwen3-8B"),
        model("unsloth/Qwen3-8B-GGUF", { runnable: false, not_runnable_reason: "gguf", fit: null, format: "gguf" }),
      ],
    });
    renderPage("/admin/marketplace?tab=search");

    expect(await screen.findByTestId("model-card-Qwen/Qwen3-8B")).toBeInTheDocument();
    expect(screen.queryByTestId("model-card-unsloth/Qwen3-8B-GGUF")).not.toBeInTheDocument();
    await userEvent.click(screen.getByTestId("market-show-unrunnable"));
    const gguf = screen.getByTestId("model-card-unsloth/Qwen3-8B-GGUF");
    expect(within(gguf).getByText(/GGUF files are for llama.cpp/)).toBeInTheDocument();
    expect(within(gguf).getByTestId("host-unsloth/Qwen3-8B-GGUF")).toBeDisabled();

    await userEvent.click(screen.getByTestId("market-task-embedding"));
    await waitFor(() => expect(search).toHaveBeenLastCalledWith(expect.objectContaining({ task: "embedding" })));
  });

  it("searches with wildcards and an author, and never sends an author the Hub would refuse", async () => {
    vi.spyOn(marketplaceApi, "recommended").mockResolvedValue({ hub: "online", cluster_id: null, groups: [], items: [] });
    const search = vi.spyOn(marketplaceApi, "search").mockResolvedValue({
      hub: "online", cluster_id: PAIR.environment_id, items: [model("Qwen/Qwen3.8-27B-FP8")],
    });
    renderPage("/admin/marketplace?tab=search");
    await screen.findByTestId("model-card-Qwen/Qwen3.8-27B-FP8");
    expect(screen.getByText("* matches any text, ? one character")).toBeInTheDocument();

    await userEvent.type(screen.getByTestId("market-search"), "qwen3.8*fp8");
    await userEvent.type(screen.getByTestId("market-author"), "Qwen");
    await waitFor(() =>
      expect(search).toHaveBeenLastCalledWith(expect.objectContaining({ q: "qwen3.8*fp8", author: "Qwen" })),
    );

    await userEvent.clear(screen.getByTestId("market-author"));
    await userEvent.type(screen.getByTestId("market-author"), "meta llama");
    expect(await screen.findByText(/Letters, digits/)).toBeInTheDocument();
    await new Promise((resolve) => setTimeout(resolve, 600));
    expect(search).not.toHaveBeenCalledWith(expect.objectContaining({ author: "meta llama" }));
  });

  it("shows the owner's picture from the Hub, and the initial when there is none", async () => {
    vi.spyOn(marketplaceApi, "recommended").mockResolvedValue({
      hub: "online", cluster_id: PAIR.environment_id, groups: ["chat"],
      items: [model("Qwen/Qwen3.8-27B-FP8", { curated: { group: "chat", blurb: "marketplace.blurb.flagship_fp8", verified: null } })],
    });
    renderPage();

    const avatar = await screen.findByTestId("owner-avatar-Qwen");
    const picture = within(avatar).getByRole("img");
    expect(picture).toHaveAttribute("src", "/api/llm/marketplace/avatars/Qwen");
    fireEvent.error(picture);
    await waitFor(() => expect(within(avatar).queryByRole("img")).not.toBeInTheDocument());
    expect(avatar).toHaveTextContent("Q");
    expect(screen.getByText(/about half the memory/)).toBeInTheDocument();
  });

  it("says from any tab that something is downloading, and takes you to it", async () => {
    vi.spyOn(marketplaceApi, "recommended").mockResolvedValue({ hub: "online", cluster_id: null, groups: [], items: [] });
    vi.spyOn(marketplaceApi, "kept").mockResolvedValue({
      items: [{
        model_id: "m9", display_name: "Qwen3-8B", hf_repo_id: "Qwen/Qwen3-8B", hf_revision: null, source: "huggingface",
        status: "downloading", created_at: null, size_bytes: null, deployments: [], runtimes: [],
        download: { job_id: "j9", status: "running", progress: 63, error: null, updated_at: null },
      }],
    });
    renderPage();

    const chip = await screen.findByTestId("market-downloads-chip");
    expect(chip).toHaveTextContent("1 downloading");
    await userEvent.click(chip);
    expect(await screen.findByTestId("download-m9")).toHaveTextContent("63%");
  });
});
