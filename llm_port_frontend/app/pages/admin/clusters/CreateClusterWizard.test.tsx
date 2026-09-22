/**
 * The wizard's whole job is to run six calls in the right order while asking
 * three questions. These pin the order, and the head suggestion that the
 * browser test caught as broken.
 */
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";

import { inferenceApi } from "~/api/inference";
import {
  CONTROL_PLANE_ID,
  ENV_ID,
  HEAD_NODE_ID,
  WORKER_NODE_ID,
  controlPlane,
  environment,
  managedNodes,
  plan,
} from "~/test/inferenceFixtures";

import { CreateClusterWizard } from "./CreateClusterWizard";

const BUNDLE = {
  bundle_id: "bundle-dgx-spark-gb10-v1",
  display_name: "NVIDIA DGX Spark GB10 Runtime",
  description: "",
  image: "llmport/ray-vllm-gb10:ray2.58-nv26.08",
  cpu_architecture: "aarch64",
  accelerator_vendor: "nvidia",
  runtime_version: "2.58.0",
  vllm_version: "0.27.1",
  certification_status: "partial",
  compatible_node_ids: [HEAD_NODE_ID, WORKER_NODE_ID],
  incompatible: {},
};

function renderWizard(nodes = managedNodes) {
  const onCreated = vi.fn();
  render(
    <CreateClusterWizard
      open
      nodes={nodes}
      onClose={() => {}}
      onCreated={onCreated}
    />,
  );
  return { onCreated };
}

async function walkToNetworkStep(name = "dgx-pair") {
  await userEvent.type(screen.getByLabelText("Cluster name"), name);
  await userEvent.click(screen.getByRole("button", { name: "Next" }));
  await userEvent.click(await screen.findByLabelText("Use spark-ts3202"));
  await userEvent.click(screen.getByLabelText("Use spark-3201"));
}

describe("CreateClusterWizard", () => {
  beforeEach(() => {
    vi.spyOn(inferenceApi, "listControlPlanes").mockResolvedValue([controlPlane]);
    vi.spyOn(inferenceApi, "listDrivers").mockResolvedValue(["ray"]);
    vi.spyOn(inferenceApi, "listRuntimeBundles").mockResolvedValue([BUNDLE]);
    vi.spyOn(inferenceApi, "createEnvironment").mockResolvedValue(environment);
    vi.spyOn(inferenceApi, "addEnvironmentNode").mockResolvedValue(environment);
    vi.spyOn(inferenceApi, "planEnvironment").mockResolvedValue(plan);
    vi.spyOn(inferenceApi, "applyEnvironmentPlan").mockResolvedValue(environment);
    vi.spyOn(inferenceApi, "reconcileEnvironment").mockResolvedValue(environment);
  });

  it("names a machine as leading the cluster as soon as one is picked", async () => {
    renderWizard();
    await userEvent.type(screen.getByLabelText("Cluster name"), "dgx-pair");
    await userEvent.click(screen.getByRole("button", { name: "Next" }));

    await userEvent.click(await screen.findByLabelText("Use spark-ts3202"));
    // The regression the browser test found: the head was never assigned, so
    // nothing was shown as leading and the operator had no idea what would
    // happen.
    expect(await screen.findByText("leads the cluster")).toBeInTheDocument();
  });

  it("moves the suggestion when the suggested machine is unpicked", async () => {
    const nodes = [
      { ...managedNodes[0], capabilities: { gpu_count: 4 } },
      { ...managedNodes[1], capabilities: { gpu_count: 1 } },
    ];
    renderWizard(nodes);
    await userEvent.type(screen.getByLabelText("Cluster name"), "dgx-pair");
    await userEvent.click(screen.getByRole("button", { name: "Next" }));

    await userEvent.click(await screen.findByLabelText("Use spark-ts3202"));
    await userEvent.click(screen.getByLabelText("Use spark-3201"));
    // Most accelerators wins.
    let leader = screen.getByText("leads the cluster").closest("div");
    expect(leader?.parentElement).toHaveTextContent("spark-ts3202");

    await userEvent.click(screen.getByLabelText("Use spark-ts3202"));
    leader = screen.getByText("leads the cluster").closest("div");
    expect(leader?.parentElement).toHaveTextContent("spark-3201");
  });

  it("runs the six calls in order, none of them visible to the operator", async () => {
    const { onCreated } = renderWizard();
    await walkToNetworkStep();
    await userEvent.click(screen.getByRole("button", { name: "Next" }));

    // Control plane reused, environment created with the head already bound.
    await waitFor(() =>
      expect(inferenceApi.createEnvironment).toHaveBeenCalledWith({
        control_plane_id: CONTROL_PLANE_ID,
        name: "dgx-pair",
        head_node_id: HEAD_NODE_ID,
      }),
    );
    expect(inferenceApi.addEnvironmentNode).toHaveBeenCalledWith(
      ENV_ID,
      HEAD_NODE_ID,
      "head",
    );
    expect(inferenceApi.addEnvironmentNode).toHaveBeenCalledWith(
      ENV_ID,
      WORKER_NODE_ID,
      "worker",
    );
    expect(inferenceApi.planEnvironment).toHaveBeenCalledWith(ENV_ID);

    await userEvent.click(
      await screen.findByRole("button", { name: "Create cluster" }),
    );
    await waitFor(() =>
      expect(inferenceApi.applyEnvironmentPlan).toHaveBeenCalledWith(ENV_ID, {
        plan,
        selected_candidate_id: "cand-roce-1",
      }),
    );
    expect(inferenceApi.reconcileEnvironment).toHaveBeenCalledWith(ENV_ID);
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith(ENV_ID));
  });

  it("creates a control plane silently when none exists", async () => {
    vi.spyOn(inferenceApi, "listControlPlanes").mockResolvedValue([]);
    const create = vi
      .spyOn(inferenceApi, "createControlPlane")
      .mockResolvedValue(controlPlane);
    renderWizard();
    await walkToNetworkStep();
    await userEvent.click(screen.getByRole("button", { name: "Next" }));

    await waitFor(() =>
      expect(create).toHaveBeenCalledWith(
        expect.objectContaining({ driver: "ray" }),
      ),
    );
    // The operator was never asked about it.
    expect(screen.queryByText(/control plane/i)).not.toBeInTheDocument();
  });

  it("refuses to create a cluster the planner says cannot form", async () => {
    vi.spyOn(inferenceApi, "planEnvironment").mockResolvedValue({
      ...plan,
      blockers: ["no shared network between these machines"],
    });
    renderWizard();
    await walkToNetworkStep();
    await userEvent.click(screen.getByRole("button", { name: "Next" }));

    expect(
      await screen.findByText("no shared network between these machines"),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create cluster" })).toBeDisabled();
  });

  it("says so when the server has no driver to run a cluster with", async () => {
    vi.spyOn(inferenceApi, "listControlPlanes").mockResolvedValue([]);
    vi.spyOn(inferenceApi, "listDrivers").mockResolvedValue([]);
    renderWizard();
    await walkToNetworkStep();
    await userEvent.click(screen.getByRole("button", { name: "Next" }));

    expect(
      await screen.findByText(/no inference driver registered/i),
    ).toBeInTheDocument();
  });

  it("shows the address alongside the name so identical names stay separable", async () => {
    renderWizard();
    await userEvent.type(screen.getByLabelText("Cluster name"), "dgx-pair");
    await userEvent.click(screen.getByRole("button", { name: "Next" }));

    const row = (await screen.findByText("spark-ts3202")).closest("div")
      ?.parentElement as HTMLElement;
    expect(within(row).getByText(/10\.88\.10\.49/)).toBeInTheDocument();
  });

  it("says which image each machine will run, without asking", async () => {
    renderWizard();
    await userEvent.type(screen.getByLabelText("Cluster name"), "dgx-pair");
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    await userEvent.click(await screen.findByLabelText("Use spark-ts3202"));

    // A readout, not a question: the image follows from the machine, so
    // there is nothing here for an operator to pick.
    expect(
      await screen.findByText(/spark-ts3202 runs NVIDIA DGX Spark GB10 Runtime/),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText("Runtime image")).not.toBeInTheDocument();
  });

  it("names each machine's own image on a cluster spanning two platforms", async () => {
    vi.spyOn(inferenceApi, "listRuntimeBundles").mockResolvedValue([
      { ...BUNDLE, compatible_node_ids: [HEAD_NODE_ID] },
      {
        ...BUNDLE,
        bundle_id: "bundle-generic-x86-nvidia-v1",
        display_name: "Generic x86_64 NVIDIA Runtime",
        cpu_architecture: "x86_64",
        compatible_node_ids: [WORKER_NODE_ID],
      },
    ]);
    renderWizard();
    await walkToNetworkStep();

    // One image per machine.  Pinning one on the cluster is what sent an
    // aarch64 image to an x86 box.
    expect(
      await screen.findByText(/spark-ts3202 runs NVIDIA DGX Spark GB10 Runtime/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/spark-3201 runs Generic x86_64 NVIDIA Runtime/),
    ).toBeInTheDocument();
  });

  it("warns when no certified image runs on the chosen machines", async () => {
    vi.spyOn(inferenceApi, "listRuntimeBundles").mockResolvedValue([
      {
        ...BUNDLE,
        compatible_node_ids: [],
        incompatible: { [HEAD_NODE_ID]: "Incompatible GPU vendor: expected nvidia, found amd" },
      },
    ]);
    renderWizard();
    await userEvent.type(screen.getByLabelText("Cluster name"), "dgx-pair");
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    await userEvent.click(await screen.findByLabelText("Use spark-ts3202"));

    expect(
      await screen.findByText(/No certified runtime image runs on/),
    ).toBeInTheDocument();
    expect(screen.getByText(/Incompatible GPU vendor/)).toBeInTheDocument();
  });
});
