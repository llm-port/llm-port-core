/**
 * "What should I do next?" — derived, never stored.
 *
 * The old screens answered *what can I do* (four equal-weight icon buttons)
 * but never *what should I do*, so an operator had to know the order of a
 * four-screen journey that nothing described. This module is that order,
 * expressed once as a pure function over DTOs the API already returns.
 *
 * Pure on purpose: every stage is unit-testable without rendering anything,
 * and the same answer drives the fleet page, the cluster page and the empty
 * states.
 */
import type {
  InferenceDeployment,
  InferenceEnvironment,
  EnvironmentNode,
} from "~/api/inference";
import type { ManagedNode } from "~/api/nodes";

export type ReadinessStage =
  | "no-nodes"
  | "no-cluster"
  | "cluster-empty"
  | "no-network"
  | "starting"
  | "degraded"
  | "stopped"
  | "ready"
  | "deploying"
  | "serving";

export type ReadinessTone = "action" | "progress" | "warning" | "success";

export interface NextStep {
  stage: ReadinessStage;
  /** One short sentence naming the action, in the operator's words. */
  title: string;
  /** Why this is next, or what is being waited on. */
  detail: string;
  /** Label for the button, when there is something to press. */
  actionLabel?: string;
  tone: ReadinessTone;
}

/** Cluster statuses that mean "converging, nothing for the operator to do". */
const IN_PROGRESS = new Set(["pending", "preparing"]);
/** Deployment phases that mean the same. */
const DEPLOYING = new Set(["pending", "preparing", "applying"]);

/** True once a network has been chosen and written to the cluster. */
export function hasNetwork(cluster: InferenceEnvironment): boolean {
  const observed = cluster.observed_status as Record<string, unknown> | undefined;
  const fabric = observed?.resolved_fabric;
  return !!fabric && typeof fabric === "object" && Object.keys(fabric).length > 0;
}

/**
 * The next step for the fleet as a whole (the Clusters list page).
 */
export function fleetReadiness(
  nodes: ManagedNode[],
  clusters: InferenceEnvironment[],
): NextStep {
  if (nodes.length === 0) {
    return {
      stage: "no-nodes",
      title: "Add your first machine",
      detail:
        "A cluster is built from machines running the LLM.Port agent. Onboard one to get started.",
      actionLabel: "Add a machine",
      tone: "action",
    };
  }
  if (clusters.length === 0) {
    return {
      stage: "no-cluster",
      title: "Create a cluster",
      detail: `${nodes.length} machine${nodes.length === 1 ? " is" : "s are"} ready to be grouped into a cluster that can serve models.`,
      actionLabel: "Create a cluster",
      tone: "action",
    };
  }
  return {
    stage: "serving",
    title: "Your clusters are set up",
    detail: "Open a cluster to see its machines, or deploy a model to one.",
    tone: "success",
  };
}

/**
 * The next step for one cluster (the cluster detail page).
 *
 * Order matters: each check assumes the ones above it passed, which is
 * exactly the sequence the operator has to walk.
 */
export function clusterReadiness(
  cluster: InferenceEnvironment,
  members: EnvironmentNode[],
  deployments: InferenceDeployment[],
): NextStep {
  if (members.length === 0) {
    return {
      stage: "cluster-empty",
      title: "Add machines to this cluster",
      detail: "A cluster needs at least one machine before it can start.",
      actionLabel: "Add machines",
      tone: "action",
    };
  }

  if (!hasNetwork(cluster)) {
    return {
      stage: "no-network",
      title: "Choose the network",
      detail:
        members.length > 1
          ? "We can see which networks these machines share. Pick one and the cluster starts on it."
          : "Confirm the network this machine will serve on, and the cluster starts.",
      actionLabel: "Choose the network",
      tone: "action",
    };
  }

  if (cluster.desired_state === "stopped") {
    return {
      stage: "stopped",
      title: "This cluster is stopped",
      detail: "Start it to serve models again. Its machines and network are kept.",
      actionLabel: "Start cluster",
      tone: "warning",
    };
  }

  if (cluster.status === "failed" || cluster.status === "degraded") {
    return {
      stage: "degraded",
      title: "The cluster needs attention",
      detail:
        cluster.status_message ??
        "Some machines are not reporting. Open Advanced below for the full status.",
      tone: "warning",
    };
  }

  if (IN_PROGRESS.has(cluster.status)) {
    return {
      stage: "starting",
      title: "Starting the cluster",
      detail: `Preparing ${members.length} machine${members.length === 1 ? "" : "s"}: fetching the runtime, starting the head, joining the others.`,
      tone: "progress",
    };
  }

  const active = deployments.filter((d) => d.desired_state !== "deleted");
  if (active.length === 0) {
    return {
      stage: "ready",
      title: "Deploy a model",
      detail: "The cluster is running and has nothing to serve yet.",
      actionLabel: "Deploy a model",
      tone: "action",
    };
  }

  const pending = active.filter((d) => DEPLOYING.has(d.phase));
  if (pending.length > 0) {
    const names = pending.map((d) => d.name).join(", ");
    return {
      stage: "deploying",
      title: `Starting ${pending.length === 1 ? names : `${pending.length} deployments`}`,
      detail:
        "Copying the model to the machines that need it, then starting the replicas.",
      tone: "progress",
    };
  }

  const failed = active.filter((d) => d.phase === "failed");
  if (failed.length > 0) {
    return {
      stage: "degraded",
      title: `${failed.length} deployment${failed.length === 1 ? "" : "s"} failed to start`,
      detail:
        failed[0].phase_message ?? "Open the deployment to read its logs.",
      tone: "warning",
    };
  }

  const ready = active.reduce((sum, d) => sum + d.ready_replicas, 0);
  return {
    stage: "serving",
    title: "Serving",
    detail: `${active.length} deployment${active.length === 1 ? "" : "s"} running across ${ready} replica${ready === 1 ? "" : "s"}.`,
    tone: "success",
  };
}

/** Pick the machine that should lead the cluster.
 *
 * Most accelerators first, because the head also schedules; host name breaks
 * the tie so the choice is stable between runs rather than list-order
 * dependent. Exposed because the wizard states the choice rather than asking
 * the operator to make it.
 */
export function suggestHeadNode(nodes: ManagedNode[]): ManagedNode | null {
  if (nodes.length === 0) return null;
  return [...nodes].sort((a, b) => {
    const diff = gpuCount(b) - gpuCount(a);
    return diff !== 0 ? diff : a.host.localeCompare(b.host);
  })[0];
}

/** Accelerators on a node, from whichever shape the agent reported. */
export function gpuCount(node: ManagedNode): number {
  const caps = (node.capabilities ?? {}) as Record<string, unknown>;
  const flat = Number(caps.gpu_count ?? 0);
  if (Number.isFinite(flat) && flat > 0) return flat;
  const nested = caps.gpu as Record<string, unknown> | undefined;
  if (nested && typeof nested === "object") {
    const count = Number(nested.count ?? 0);
    if (Number.isFinite(count) && count > 0) return count;
  }
  const inventory = (node.latest_inventory ?? {}) as Record<string, unknown>;
  const invGpu = inventory.gpu as Record<string, unknown> | undefined;
  if (invGpu && typeof invGpu === "object") {
    const count = Number(invGpu.count ?? 0);
    if (Number.isFinite(count) && count > 0) return count;
  }
  return 0;
}
