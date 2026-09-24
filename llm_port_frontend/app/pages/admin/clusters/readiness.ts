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
import i18n from "i18next";

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
  /** How far along a step in progress is, when the machine says. */
  progressPct?: number | null;
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
      title: i18n.t("clusters.next.no_nodes.title"),
      detail: i18n.t("clusters.next.no_nodes.detail"),
      actionLabel: i18n.t("clusters.next.no_nodes.action"),
      tone: "action",
    };
  }
  if (clusters.length === 0) {
    return {
      stage: "no-cluster",
      title: i18n.t("clusters.next.no_cluster.title"),
      detail: i18n.t("clusters.next.no_cluster.detail", { count: nodes.length }),
      actionLabel: i18n.t("clusters.next.no_cluster.action"),
      tone: "action",
    };
  }
  return {
    stage: "serving",
    title: i18n.t("clusters.next.fleet_ready.title"),
    detail: i18n.t("clusters.next.fleet_ready.detail"),
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
      title: i18n.t("clusters.next.empty.title"),
      detail: i18n.t("clusters.next.empty.detail"),
      actionLabel: i18n.t("clusters.next.empty.action"),
      tone: "action",
    };
  }

  if (!hasNetwork(cluster)) {
    return {
      stage: "no-network",
      title: i18n.t("clusters.next.network.title"),
      detail:
        members.length > 1
          ? i18n.t("clusters.next.network.detail_many")
          : i18n.t("clusters.next.network.detail_one"),
      actionLabel: i18n.t("clusters.next.network.action"),
      tone: "action",
    };
  }

  if (cluster.desired_state === "stopped") {
    return {
      stage: "stopped",
      title: i18n.t("clusters.next.stopped.title"),
      detail: i18n.t("clusters.next.stopped.detail"),
      actionLabel: i18n.t("clusters.next.stopped.action"),
      tone: "warning",
    };
  }

  if (cluster.status === "failed" || cluster.status === "degraded") {
    return {
      stage: "degraded",
      title: i18n.t("clusters.next.degraded.title"),
      detail: cluster.status_message ?? i18n.t("clusters.next.degraded.detail"),
      // A failed start is retried on a backoff, and not at all while nothing
      // it depends on has changed. The operator who has just fixed it should
      // not have to wait for the next scheduled look.
      actionLabel: cluster.status === "failed" ? i18n.t("clusters.next.degraded.action") : undefined,
      tone: "warning",
    };
  }

  if (IN_PROGRESS.has(cluster.status)) {
    // The first start moves a ~12 GB runtime image to every machine. What the
    // machine reports about it is the only honest answer to "is anything
    // happening?", so show that rather than a fixed sentence.
    const reported = cluster.progress?.message;
    return {
      stage: "starting",
      title: i18n.t("clusters.next.starting.title"),
      detail: reported ?? i18n.t("clusters.next.starting.detail", { count: members.length }),
      tone: "progress",
      progressPct: cluster.progress?.progress_pct ?? null,
    };
  }

  const active = deployments.filter((d) => d.desired_state !== "deleted");
  if (active.length === 0) {
    return {
      stage: "ready",
      title: i18n.t("clusters.next.ready.title"),
      detail: i18n.t("clusters.next.ready.detail"),
      actionLabel: i18n.t("clusters.next.ready.action"),
      tone: "action",
    };
  }

  const pending = active.filter((d) => DEPLOYING.has(d.phase));
  if (pending.length > 0) {
    const names = pending.map((d) => d.name).join(", ");
    return {
      stage: "deploying",
      title:
        pending.length === 1
          ? i18n.t("clusters.next.deploying.title_one", { name: names })
          : i18n.t("clusters.next.deploying.title_many", { count: pending.length }),
      detail: i18n.t("clusters.next.deploying.detail"),
      tone: "progress",
    };
  }

  const failed = active.filter((d) => d.phase === "failed");
  if (failed.length > 0) {
    return {
      stage: "degraded",
      title: i18n.t("clusters.next.failed.title", { count: failed.length }),
      detail: failed[0].phase_message ?? i18n.t("clusters.next.failed.detail"),
      tone: "warning",
    };
  }

  const ready = active.reduce((sum, d) => sum + d.ready_replicas, 0);
  return {
    stage: "serving",
    title: i18n.t("clusters.next.serving.title"),
    detail: i18n.t("clusters.next.serving.detail", {
      deployments: i18n.t("clusters.deployment_count", { count: active.length }),
      replicas: i18n.t("clusters.replica_count", { count: ready }),
    }),
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
