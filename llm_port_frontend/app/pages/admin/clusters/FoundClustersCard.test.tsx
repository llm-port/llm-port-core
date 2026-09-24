/**
 * Taking over a cluster the machines still run: what the operator sees and
 * what is sent. The server-side checks (nothing restarts) are the backend's
 * tests; here, that the card stays out of the way until there is something,
 * that a blocked cluster cannot be taken, and that a refusal is shown.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { inferenceApi, type FoundCluster } from "~/api/inference";

import { FoundClustersCard } from "./FoundClustersCard";

const HEAD = "11111111-1111-1111-1111-111111111111";

const CLUSTER: FoundCluster = {
  address: "10.100.0.2:6379",
  runtime_version: "2.58.0",
  image: "llmport/ray-vllm-gb10:ray2.58-nv26.08",
  head: {
    ip: "10.100.0.2", hostname: "spark-3201", role: "head", gpus: 1,
    runtime_node_id: "ray-head", node_id: HEAD, name: "spark-3201",
  },
  members: [
    { ip: "10.100.0.2", hostname: "spark-3201", role: "head", gpus: 1,
      runtime_node_id: "ray-head", node_id: HEAD, name: "spark-3201" },
    { ip: "10.100.0.1", hostname: "spark-ts3202", role: "worker", gpus: 1,
      runtime_node_id: "ray-worker", node_id: "22222222-2222-2222-2222-222222222222", name: "spark-ts3202" },
  ],
  apps: [{
    app_name: "llmport-e783c0c2-cde1-4385-9d2e-08ea4bd2a52e",
    deployment_id: "e783c0c2-cde1-4385-9d2e-08ea4bd2a52e",
    model_id: "Qwen2.5-0.5B-Instruct",
    model_source: "/root/.cache/huggingface/models--Qwen--Qwen2.5-0.5B-Instruct/snapshots/7ae5576",
    hf_repo_id: "Qwen/Qwen2.5-0.5B-Instruct",
    copies: 1,
    gpus_per_copy: 1,
    engine: {},
    status: "RUNNING",
    running_copies: 1,
    suggested_alias: "qwen2.5-0.5b-instruct",
    notes: [],
    already_known: false,
  }],
  other_apps: [],
  can_take_over: true,
  blockers: [],
  errors: [],
  described_by: HEAD,
};

afterEach(() => vi.restoreAllMocks());

describe("FoundClustersCard", () => {
  it("shows nothing when the machines run no cluster this server lost", async () => {
    const spy = vi.spyOn(inferenceApi, "foundClusters").mockResolvedValue({ clusters: [], unreadable: [] });
    const { container } = render(<FoundClustersCard onTakenOver={() => {}} />);
    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it("takes a cluster over under the names given", async () => {
    vi.spyOn(inferenceApi, "foundClusters").mockResolvedValue({ clusters: [CLUSTER], unreadable: [] });
    const take = vi.spyOn(inferenceApi, "takeOverCluster").mockResolvedValue({
      environment_id: "env-1", name: "dgx-pair", control_plane: "Default-Ray", members: [], deployments: [],
    });
    const done = vi.fn();
    render(<FoundClustersCard onTakenOver={done} />);

    const row = await screen.findByTestId(`found-cluster-${HEAD}`);
    expect(within(row).getByText("Qwen2.5-0.5B-Instruct")).toBeInTheDocument();
    expect(within(row).getByText("1 of 1 running")).toBeInTheDocument();

    await userEvent.click(within(row).getByTestId("takeover-open"));
    const name = screen.getByTestId("takeover-name");
    expect(name).toHaveValue("spark-3201-cluster");
    await userEvent.clear(name);
    await userEvent.type(name, "dgx-pair");
    const alias = screen.getByTestId(`takeover-alias-${CLUSTER.apps[0].deployment_id}`);
    expect(alias).toHaveValue("qwen2.5-0.5b-instruct");
    await userEvent.clear(alias);
    await userEvent.type(alias, "qwen-chat");
    await userEvent.click(screen.getByTestId("takeover-confirm"));

    await waitFor(() => expect(done).toHaveBeenCalledWith("env-1"));
    expect(take).toHaveBeenCalledWith({
      node_id: HEAD,
      name: "dgx-pair",
      aliases: { [CLUSTER.apps[0].app_name]: "qwen-chat" },
    });
  });

  it("shows why the server refused, and stays open", async () => {
    vi.spyOn(inferenceApi, "foundClusters").mockResolvedValue({ clusters: [CLUSTER], unreadable: [] });
    vi.spyOn(inferenceApi, "takeOverCluster").mockRejectedValue(
      new Error("Taking over would restart Qwen2.5-0.5B-Instruct (...). Nothing was changed."),
    );
    const done = vi.fn();
    render(<FoundClustersCard onTakenOver={done} />);
    await userEvent.click(await screen.findByTestId("takeover-open"));
    await userEvent.click(screen.getByTestId("takeover-confirm"));

    expect(await screen.findByTestId("takeover-error")).toHaveTextContent("Nothing was changed.");
    expect(done).not.toHaveBeenCalled();
  });

  it("does not offer a cluster that cannot be taken over, and says why", async () => {
    vi.spyOn(inferenceApi, "foundClusters").mockResolvedValue({
      clusters: [{
        ...CLUSTER,
        can_take_over: false,
        blockers: ["spark-9 is in the cluster but not in this fleet. Approve the machine first."],
      }],
      unreadable: [{ node_id: "n3", name: "old-agent", error: "The machine did not answer (its agent may be older than 0.1.12)." }],
    });
    render(<FoundClustersCard onTakenOver={() => {}} />);

    expect(await screen.findByTestId("takeover-open")).toBeDisabled();
    expect(screen.getByTestId("takeover-blockers")).toHaveTextContent("Approve the machine first.");
    expect(screen.getByText(/old-agent runs the cluster runtime/)).toBeInTheDocument();
  });
});
