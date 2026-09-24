/**
 * "On this server": downloads followed and controlled, models added, deleted with care.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { jobs, models as modelsApi, type DownloadJob, type Model } from "~/api/llm";
import type { KeptModel } from "~/api/marketplace";

import { KeptModels } from "./KeptModels";

function kept(overrides: Partial<KeptModel>): KeptModel {
  return {
    model_id: "m1",
    display_name: "Qwen3-0.6B",
    hf_repo_id: "Qwen/Qwen3-0.6B",
    hf_revision: null,
    source: "huggingface",
    status: "available",
    created_at: "2026-09-24T10:00:00Z",
    size_bytes: 1_503_300_328,
    download: null,
    deployments: [],
    runtimes: [],
    ...overrides,
  };
}

function renderKept(items: KeptModel[], onChanged = vi.fn()) {
  render(
    <MemoryRouter>
      <KeptModels items={items} loading={false} error={null} onChanged={onChanged} onHost={vi.fn()}
                  onOpen={vi.fn()} can={() => true} />
    </MemoryRouter>,
  );
  return { onChanged };
}

afterEach(() => vi.restoreAllMocks());

describe("KeptModels", () => {
  it("shows a download's progress and cancels it", async () => {
    const cancel = vi.spyOn(jobs, "cancel").mockResolvedValue({} as DownloadJob);
    const { onChanged } = renderKept([
      kept({
        model_id: "m2", display_name: "Qwen3-8B", hf_repo_id: "Qwen/Qwen3-8B", status: "downloading", size_bytes: null,
        download: { job_id: "j2", status: "running", progress: 42, error: null, updated_at: null },
      }),
    ]);
    const row = screen.getByTestId("download-m2");
    expect(row).toHaveTextContent("Qwen/Qwen3-8B");
    expect(row).toHaveTextContent("42%");
    expect(within(row).getByRole("progressbar")).toHaveAttribute("aria-valuenow", "42");
    expect(screen.getByText(/Leave this page and come back/)).toBeInTheDocument();

    await userEvent.click(screen.getByTestId("download-cancel-m2"));
    await waitFor(() => expect(cancel).toHaveBeenCalledWith("j2"));
    expect(onChanged).toHaveBeenCalled();
  });

  it("offers a failed download again", async () => {
    const retry = vi.spyOn(jobs, "retry").mockResolvedValue({} as DownloadJob);
    renderKept([
      kept({
        status: "failed",
        download: { job_id: "j1", status: "failed", progress: 10, error: "401 gated repo", updated_at: null },
      }),
    ]);
    expect(screen.getByTestId("download-failed-m1")).toHaveTextContent("Failed: 401 gated repo");
    await userEvent.click(screen.getByTestId("download-retry-m1"));
    await waitFor(() => expect(retry).toHaveBeenCalledWith("j1"));
  });

  it("will not delete a model a deployment uses, and says which", async () => {
    const del = vi.spyOn(modelsApi, "delete");
    renderKept([kept({ deployments: [{ id: "d1", name: "qwen3-0-6b", cluster: "dgx-pair", phase: "running",
                                       desired_state: "active" }] })]);
    expect(screen.getByText("qwen3-0-6b on dgx-pair")).toBeInTheDocument();
    await userEvent.click(screen.getByTestId("kept-delete-m1"));
    expect(screen.getByTestId("delete-blocked")).toHaveTextContent("qwen3-0-6b");
    expect(screen.queryByTestId("delete-confirm")).not.toBeInTheDocument();
    expect(del).not.toHaveBeenCalled();
  });

  it("deletes a model and its files when asked", async () => {
    const del = vi.spyOn(modelsApi, "delete").mockResolvedValue(undefined);
    renderKept([kept({})]);
    await userEvent.click(screen.getByTestId("kept-delete-m1"));
    expect(screen.getByText(/frees 1\.5 GB/)).toBeInTheDocument();
    await userEvent.click(screen.getByTestId("delete-confirm"));
    await waitFor(() => expect(del).toHaveBeenCalledWith("m1", { files: true }));
    expect(await screen.findByText("Deleted Qwen3-0.6B and its files.")).toBeInTheDocument();
  });

  it("never offers to delete the files of a model added from a path", async () => {
    const del = vi.spyOn(modelsApi, "delete").mockResolvedValue(undefined);
    renderKept([kept({ source: "local_path", hf_repo_id: null, display_name: "my-finetune" })]);
    expect(screen.getByText("From a path on this server")).toBeInTheDocument();
    await userEvent.click(screen.getByTestId("kept-delete-m1"));
    expect(screen.queryByTestId("delete-files")).not.toBeInTheDocument();
    await userEvent.click(screen.getByTestId("delete-confirm"));
    await waitFor(() => expect(del).toHaveBeenCalledWith("m1", { files: false }));
  });

  it("adds a model from a path on the server", async () => {
    const register = vi.spyOn(modelsApi, "register").mockResolvedValue({} as Model);
    renderKept([]);
    await userEvent.click(screen.getByText("Add from a path"));
    fireEvent.change(screen.getByTestId("register-path"), { target: { value: "/srv/models/my-finetune" } });
    fireEvent.change(screen.getByTestId("register-name"), { target: { value: "my-finetune" } });
    await userEvent.click(screen.getByTestId("register-submit"));
    await waitFor(() =>
      expect(register).toHaveBeenCalledWith({ display_name: "my-finetune", path: "/srv/models/my-finetune" }),
    );
  });
});
