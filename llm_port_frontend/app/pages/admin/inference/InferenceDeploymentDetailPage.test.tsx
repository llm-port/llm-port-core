/**
 * Deployment-detail component tests (Phase 6, WI-5 acceptance).
 *
 * Two things are under test and neither is cosmetic:
 *
 *  1. all ten elements the migration plan requires actually render;
 *  2. the two partial states render as labelled gaps -- not as zeros, not as
 *     errors, not as spinners. That is the difference between an operator
 *     seeing "the worker exports no metrics port" and seeing "0 GPUs".
 */
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { inferenceApi } from "~/api/inference";
import { models as modelsApi } from "~/api/llm";
import { nodesApi } from "~/api/nodes";
import {
  DEPLOYMENT_ID,
  MODEL_ID,
  WORKER_METRICS_REASON,
  artifactsPartial,
  deployment,
  deploymentMetricsPartial,
  endpoints,
  environment,
  environmentNodes,
  logPage,
  logPageUnreachable,
  managedNodes,
} from "~/test/inferenceFixtures";
import { renderPage } from "~/test/renderPage";

import InferenceDeploymentDetailPage from "./InferenceDeploymentDetailPage";

const ROUTE = "/admin/inference/deployments/:id";
const URL = `/admin/inference/deployments/${DEPLOYMENT_ID}`;

function mockAll() {
  vi.spyOn(inferenceApi, "getDeployment").mockResolvedValue(deployment);
  vi.spyOn(inferenceApi, "getEnvironment").mockResolvedValue(environment);
  vi.spyOn(inferenceApi, "listEnvironmentNodes").mockResolvedValue(environmentNodes);
  vi.spyOn(inferenceApi, "listEndpoints").mockResolvedValue(endpoints);
  vi.spyOn(inferenceApi, "artifactReadiness").mockResolvedValue(artifactsPartial);
  vi.spyOn(inferenceApi, "deploymentMetrics").mockResolvedValue(
    deploymentMetricsPartial,
  );
  vi.spyOn(inferenceApi, "deploymentLogs").mockResolvedValue(logPage);
  vi.spyOn(nodesApi, "list").mockResolvedValue(managedNodes);
  vi.spyOn(modelsApi, "list").mockResolvedValue([
    {
      id: MODEL_ID,
      display_name: "Qwen3-8B",
      source: "huggingface",
      hf_repo_id: "Qwen/Qwen3-8B",
      hf_revision: "main",
      license_ack_required: false,
      tags: null,
      status: "available",
      instances: [],
      created_at: "2026-09-01T00:00:00Z",
      updated_at: "2026-09-01T00:00:00Z",
    },
  ] as never);
}

async function renderDetail() {
  renderPage(<InferenceDeploymentDetailPage />, {
    path: ROUTE,
    initialPath: URL,
  });
  await screen.findByRole("heading", { name: "qwen-serve" });
}

describe("InferenceDeploymentDetailPage", () => {
  beforeEach(() => {
    mockAll();
  });

  it("renders all ten elements the plan requires", async () => {
    await renderDetail();

    // 1. normalized health
    expect(screen.getByText("Health")).toBeInTheDocument();
    expect(screen.getByText("Serving")).toBeInTheDocument();

    // 2. desired / ready replicas
    const replicaLabel = screen.getByText("Copies (ready / wanted)");
    expect(replicaLabel.parentElement).toHaveTextContent("1 / 2");

    // 3. environment
    expect(screen.getByText("Cluster")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "dgx-pair" })).toBeInTheDocument();

    // 4. participating nodes -- scoped, because the hosts also appear in the
    // artifact-readiness table.
    const nodesCard = screen
      .getByText("Machines")
      .closest(".MuiCardContent-root") as HTMLElement;
    expect(within(nodesCard).getByText("spark-ts3202")).toBeInTheDocument();
    expect(within(nodesCard).getByText("spark-3201")).toBeInTheDocument();
    expect(within(nodesCard).getByText("head")).toBeInTheDocument();
    expect(within(nodesCard).getByText("worker")).toBeInTheDocument();

    // 5. artifact readiness
    expect(screen.getByText("Model files")).toBeInTheDocument();

    // 6. endpoint
    expect(screen.getByText("Endpoints")).toBeInTheDocument();
    expect(screen.getByText("http://10.100.0.1:8000/v1")).toBeInTheDocument();

    // 7. logs
    expect(screen.getByText("Logs")).toBeInTheDocument();
    await screen.findByText(/Started LLMDeployment:qwen/);

    // 8. metrics
    expect(screen.getByText("Metrics")).toBeInTheDocument();
    expect(screen.getByText("llm-port-qwen-serve")).toBeInTheDocument();
    expect(screen.getByText("RUNNING")).toBeInTheDocument();
    expect(screen.getByText("LLMDeployment:qwen")).toBeInTheDocument();

    // 9. last reconcile / error
    expect(screen.getByText("Last checked")).toBeInTheDocument();
    expect(screen.getByText("Last message")).toBeInTheDocument();
    expect(screen.getByText("application running")).toBeInTheDocument();

    // 10. advanced raw provider status
    expect(
      screen.getByRole("button", { name: "Show raw provider status" }),
    ).toBeInTheDocument();
  });

  it("labels an unreported metrics tier instead of showing a zero", async () => {
    await renderDetail();

    expect(await screen.findByText("Partial metrics")).toBeInTheDocument();
    expect(screen.getByText("node_metrics")).toBeInTheDocument();
    expect(screen.getByText(WORKER_METRICS_REASON)).toBeInTheDocument();

    // The tiers that did report are still shown next to the gap.
    expect(screen.getByText("RUNNING")).toBeInTheDocument();
    expect(
      screen.getByText(/http:\/\/10\.100\.0\.1:38129\/metrics/),
    ).toBeInTheDocument();
  });

  it("shows artifact state per node rather than a spinner", async () => {
    await renderDetail();

    const table = screen
      .getByText("Model files")
      .closest(".MuiCardContent-root") as HTMLElement;

    // Artifact readiness is its own request now, so it lands independently of
    // the deployment itself. Waiting for it is the point of the split: the
    // page frame no longer blocks on the slowest call.
    await waitFor(() =>
      expect(within(table).getByText("ready")).toBeInTheDocument(),
    );

    // A failing node stays visible next to the one that succeeded.
    expect(within(table).getByText("failed")).toBeInTheDocument();
    expect(within(table).getByText("on 1 of 2 machines")).toBeInTheDocument();
    expect(
      within(table).getByText(/snapshot incomplete, retrying/),
    ).toBeInTheDocument();
    expect(within(table).queryByRole("progressbar")).not.toBeInTheDocument();
  });

  it("keeps an unparseable log line instead of dropping it", async () => {
    await renderDetail();

    // A traceback frame has no timestamp and no level; it is the line an
    // operator needs most.
    await screen.findByText(/File "\/opt\/vllm\/engine\.py", line 42/);
  });

  it("explains an empty log page rather than showing nothing", async () => {
    vi.spyOn(inferenceApi, "deploymentLogs").mockResolvedValue(logPageUnreachable);
    await renderDetail();

    await screen.findByText("could not reach the node: timeout");
  });

  it("surfaces a driver that cannot serve logs as a warning, not silence", async () => {
    vi.spyOn(inferenceApi, "deploymentLogs").mockRejectedValue(
      new Error("API 501: driver 'mute' does not support logs"),
    );
    await renderDetail();

    await screen.findByText(/does not support logs/);
  });

  it("scales through a full spec replacement", async () => {
    const update = vi
      .spyOn(inferenceApi, "updateDeployment")
      .mockResolvedValue(deployment);
    await renderDetail();

    await userEvent.click(screen.getByRole("button", { name: /Scale/ }));
    const input = await screen.findByLabelText("Replicas");
    fireEvent.change(input, { target: { value: "3" } });
    await userEvent.click(screen.getByRole("button", { name: "Apply" }));

    await waitFor(() => expect(update).toHaveBeenCalled());
    const [, payload] = update.mock.calls[0];
    // v1alpha1 is not partially patchable, and `replicas` / `autoscale` are
    // mutually exclusive, so the scale block must be replaced wholesale.
    expect(payload.spec).toMatchObject({
      api_version: "inference.llmport.ai/v1alpha1",
      scale: { replicas: 3 },
    });
  });

  it("stops a running deployment through desired state", async () => {
    const update = vi
      .spyOn(inferenceApi, "updateDeployment")
      .mockResolvedValue(deployment);
    await renderDetail();

    await userEvent.click(screen.getByRole("button", { name: "Stop" }));
    await waitFor(() =>
      expect(update).toHaveBeenCalledWith(DEPLOYMENT_ID, {
        desired_state: "stopped",
      }),
    );
  });
});
