/**
 * Small shared formatters for the cluster screens.
 *
 * Separate from `common.tsx` (which the Phase 6 pages still use) because
 * these speak the operator's vocabulary rather than the domain's — the whole
 * point of the rework. The colour helpers are re-exported from `common` so
 * there is still exactly one mapping per status enum.
 */
import i18n from "i18next";

import type { ComputePool, EnvironmentNode } from "~/api/inference";
import type { ManagedNode } from "~/api/nodes";

export {
  deploymentPhaseColor,
  endpointStatusColor,
  environmentStatusColor as clusterStatusColor,
  formatTimestamp,
  shortId,
} from "../inference/common";

/**
 * What to call a machine.
 *
 * The agent enrols with `advertise_host` as its `host`, so that field is the
 * address it answers on -- an IP in practice. `agent_id` is the name the
 * operator gave it. Lead with the name; the address is secondary.
 */
export function nodeLabel(node: ManagedNode | undefined, fallback?: string): string {
  const unknown = fallback ?? i18n.t("clusters.unknown_machine");
  if (!node) return unknown;
  return node.agent_id?.trim() || node.host || unknown;
}

/** "3 machines" -- the count every cluster screen repeats. */
export function machineCount(count: number): string {
  return i18n.t("clusters.machine_count", { count });
}

/** "2 machines · 1 leads" — what a membership list means, in one line. */
export function memberSummary(members: EnvironmentNode[]): string {
  if (members.length === 0) return i18n.t("clusters.no_machines_yet");
  const heads = members.filter((m) => (m.role || "").toLowerCase() === "head").length;
  const machines = machineCount(members.length);
  return heads > 0 ? i18n.t("clusters.machines_with_leads", { machines, count: heads }) : machines;
}

const MACHINE_STATUSES = new Set([
  "healthy",
  "degraded",
  "unhealthy",
  "offline",
  "maintenance",
  "draining",
  "pending",
]);

/** A machine's status as a word in the reader's language, not the enum. */
export function machineStatusLabel(status: string): string {
  return MACHINE_STATUSES.has(status) ? i18n.t(`clusters.machine_status.${status}`) : status;
}

/** Deployment phase in words an operator uses, not the state machine's. */
export function phaseLabel(phase: string): string {
  switch (phase) {
    case "pending":
    case "preparing":
    case "applying":
    case "running":
    case "degraded":
    case "stopped":
    case "failed":
    case "deleted":
      return i18n.t(`clusters.phase.${phase}`);
    default:
      return phase;
  }
}

/** Cluster status in the same register. */
export function clusterStatusLabel(status: string): string {
  switch (status) {
    case "pending":
    case "preparing":
      return i18n.t("clusters.status.starting");
    case "ready":
    case "running":
      return i18n.t("clusters.status.running");
    case "degraded":
      return i18n.t("clusters.status.degraded");
    case "failed":
      return i18n.t("clusters.status.failed");
    case "stopped":
      return i18n.t("clusters.status.stopped");
    default:
      return status;
  }
}

/**
 * What a compute pool is, in one line: "GB10 · aarch64 · 2 machines".
 *
 * The signature (`nvidia/aarch64/gb10`) is the identity the backend matches
 * on and is deliberately not what is shown -- it is a key, not a sentence.
 */
export function poolLabel(pool: ComputePool): string {
  const hardware = pool.accelerator_family?.trim() || pool.accelerator_vendor;
  return `${hardware} · ${pool.cpu_architecture} · ${machineCount(pool.member_count)}`;
}

/**
 * Whether the pools are worth showing at all.
 *
 * A cluster of identical machines derives exactly one pool, and surfacing a
 * grouping with one group in it is pure noise -- the operator never asked for
 * a pool and does not need to learn the word. It earns its place the moment
 * a cluster holds more than one kind of machine, because then "which machines
 * can run this model" stops having one answer.
 */
export function poolsWorthShowing(pools: ComputePool[]): boolean {
  return pools.length > 1;
}

/** "2 kinds of machine" / "" — the headline for a mixed cluster. */
export function poolMixSummary(pools: ComputePool[]): string {
  if (pools.length <= 1) return "";
  return i18n.t("clusters.machine_kinds", { count: pools.length });
}
