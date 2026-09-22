/**
 * Small shared formatters for the cluster screens.
 *
 * Separate from `common.tsx` (which the Phase 6 pages still use) because
 * these speak the operator's vocabulary rather than the domain's — the whole
 * point of the rework. The colour helpers are re-exported from `common` so
 * there is still exactly one mapping per status enum.
 */
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
export function nodeLabel(node: ManagedNode | undefined, fallback = "unknown"): string {
  if (!node) return fallback;
  return node.agent_id?.trim() || node.host || fallback;
}

/** "2 machines · 1 leads" — what a membership list means, in one line. */
export function memberSummary(members: EnvironmentNode[]): string {
  if (members.length === 0) return "No machines yet";
  const heads = members.filter((m) => (m.role || "").toLowerCase() === "head").length;
  const machines = `${members.length} machine${members.length === 1 ? "" : "s"}`;
  return heads > 0 ? `${machines} · ${heads} leads` : machines;
}

/** Deployment phase in words an operator uses, not the state machine's. */
export function phaseLabel(phase: string): string {
  switch (phase) {
    case "pending":
      return "Queued";
    case "preparing":
      return "Copying the model";
    case "applying":
      return "Starting";
    case "running":
      return "Serving";
    case "degraded":
      return "Degraded";
    case "stopped":
      return "Stopped";
    case "failed":
      return "Failed";
    case "deleted":
      return "Removed";
    default:
      return phase;
  }
}

/** Cluster status in the same register. */
export function clusterStatusLabel(status: string): string {
  switch (status) {
    case "pending":
    case "preparing":
      return "Starting";
    case "ready":
    case "running":
      return "Running";
    case "degraded":
      return "Needs attention";
    case "failed":
      return "Failed";
    case "stopped":
      return "Stopped";
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
  const machines = `${pool.member_count} machine${pool.member_count === 1 ? "" : "s"}`;
  return `${hardware} · ${pool.cpu_architecture} · ${machines}`;
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
  return `${pools.length} kinds of machine`;
}
