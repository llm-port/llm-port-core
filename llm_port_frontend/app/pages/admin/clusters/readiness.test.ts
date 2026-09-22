/**
 * The readiness ladder is the thread through the whole UX, so every rung is
 * pinned. A wrong stage sends an operator to the wrong screen, which is the
 * exact failure the rework exists to fix.
 */
import { describe, expect, it } from "vitest";

import {
  clusterReadiness,
  fleetReadiness,
  gpuCount,
  hasNetwork,
  suggestHeadNode,
} from "./readiness";
import {
  deployment,
  environment,
  environmentNodes,
  managedNodes,
} from "~/test/inferenceFixtures";
import type { InferenceEnvironment } from "~/api/inference";

const noNetwork: InferenceEnvironment = {
  ...environment,
  observed_status: { ...environment.observed_status, resolved_fabric: {} },
};

describe("fleetReadiness", () => {
  it("sends an operator with no machines to onboarding", () => {
    const step = fleetReadiness([], []);
    expect(step.stage).toBe("no-nodes");
    expect(step.actionLabel).toBe("Add a machine");
  });

  it("offers to create a cluster once machines exist", () => {
    const step = fleetReadiness(managedNodes, []);
    expect(step.stage).toBe("no-cluster");
    expect(step.detail).toContain("2 machine");
  });

  it("stops nagging once a cluster exists", () => {
    expect(fleetReadiness(managedNodes, [environment]).stage).toBe("serving");
  });
});

describe("clusterReadiness", () => {
  it("asks for machines before anything else", () => {
    expect(clusterReadiness(environment, [], []).stage).toBe("cluster-empty");
  });

  it("asks for the network once machines are in", () => {
    const step = clusterReadiness(noNetwork, environmentNodes, []);
    expect(step.stage).toBe("no-network");
    expect(step.actionLabel).toBe("Choose the network");
  });

  it("offers to start a stopped cluster", () => {
    const stopped = { ...environment, desired_state: "stopped" };
    expect(clusterReadiness(stopped, environmentNodes, []).stage).toBe("stopped");
  });

  it("reports a converging cluster as progress, not as an action", () => {
    const starting = { ...environment, status: "preparing" };
    const step = clusterReadiness(starting, environmentNodes, []);
    expect(step.stage).toBe("starting");
    expect(step.tone).toBe("progress");
    expect(step.actionLabel).toBeUndefined();
  });

  it("surfaces a degraded cluster with its own message", () => {
    const degraded = {
      ...environment,
      status: "degraded",
      status_message: "worker stopped reporting",
    };
    const step = clusterReadiness(degraded, environmentNodes, []);
    expect(step.stage).toBe("degraded");
    expect(step.detail).toBe("worker stopped reporting");
  });

  it("offers a deployment once the cluster is ready and empty", () => {
    const step = clusterReadiness(environment, environmentNodes, []);
    expect(step.stage).toBe("ready");
    expect(step.actionLabel).toBe("Deploy a model");
  });

  it("describes a starting deployment as copying, not as a phase name", () => {
    const preparing = { ...deployment, phase: "preparing" };
    const step = clusterReadiness(environment, environmentNodes, [preparing]);
    expect(step.stage).toBe("deploying");
    expect(step.detail).toContain("Copying the model");
  });

  it("reports a failed deployment with its own message", () => {
    const failed = {
      ...deployment,
      phase: "failed",
      phase_message: "engine out of memory",
    };
    const step = clusterReadiness(environment, environmentNodes, [failed]);
    expect(step.stage).toBe("degraded");
    expect(step.detail).toBe("engine out of memory");
  });

  it("settles on serving when everything is running", () => {
    const step = clusterReadiness(environment, environmentNodes, [deployment]);
    expect(step.stage).toBe("serving");
    expect(step.tone).toBe("success");
  });

  it("ignores deleted deployments when deciding what is next", () => {
    const removed = { ...deployment, desired_state: "deleted" };
    expect(clusterReadiness(environment, environmentNodes, [removed]).stage).toBe(
      "ready",
    );
  });
});

describe("hasNetwork", () => {
  it("is false for a cluster whose fabric was never applied", () => {
    expect(hasNetwork(noNetwork)).toBe(false);
  });

  it("is true once a fabric is resolved", () => {
    expect(hasNetwork(environment)).toBe(true);
  });
});

describe("suggestHeadNode", () => {
  it("returns null for an empty selection", () => {
    expect(suggestHeadNode([])).toBeNull();
  });

  it("prefers the machine with the most accelerators", () => {
    const [a, b] = managedNodes;
    const big = { ...a, capabilities: { gpu_count: 4 } };
    const small = { ...b, capabilities: { gpu_count: 1 } };
    expect(suggestHeadNode([small, big])?.id).toBe(big.id);
  });

  it("breaks ties by host name so the choice is stable", () => {
    const [a, b] = managedNodes;
    const first = { ...a, host: "alpha", capabilities: { gpu_count: 2 } };
    const second = { ...b, host: "beta", capabilities: { gpu_count: 2 } };
    expect(suggestHeadNode([second, first])?.host).toBe("alpha");
  });
});

describe("gpuCount", () => {
  it("reads the flat capability shape", () => {
    expect(gpuCount({ ...managedNodes[0], capabilities: { gpu_count: 3 } })).toBe(3);
  });

  it("falls back to the nested capability shape", () => {
    expect(gpuCount({ ...managedNodes[0], capabilities: { gpu: { count: 2 } } })).toBe(2);
  });

  it("falls back to the inventory snapshot", () => {
    expect(
      gpuCount({
        ...managedNodes[0],
        capabilities: {},
        latest_inventory: { gpu: { count: 5 } },
      }),
    ).toBe(5);
  });

  it("returns zero rather than guessing when nothing reported one", () => {
    expect(gpuCount({ ...managedNodes[0], capabilities: {} })).toBe(0);
  });
});
