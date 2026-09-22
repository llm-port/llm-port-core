/**
 * Topology layout — geometry only, no rendering.
 *
 * A cluster *is* a topology, and the membership table could never show which
 * machine leads, which link carries traffic, or which machine is hot. We
 * already resolve all three; this turns them into coordinates.
 *
 * Hand-rolled rather than pulled from a graph library: the shape is fixed
 * (one head, workers around it), so a layout engine would be a dependency
 * that buys nothing and a CSP allowance we do not need.
 */
import type { EnvironmentNode, InferenceEnvironment } from "~/api/inference";
import type { ManagedNode } from "~/api/nodes";
import { nodeLabel } from "./presentation";

export interface TopologyNode {
  nodeId: string;
  /** What to show: the operator's name for the machine. */
  host: string;
  /** Where it answers, shown underneath the name. */
  address: string;
  role: string;
  isHead: boolean;
  x: number;
  y: number;
  r: number;
  /** Ray's view of the machine, when the cluster has been observed. */
  memberStatus: string | null;
  /** 0..1 of accelerator memory committed, or null when nothing reported it. */
  allocation: number | null;
  /** 0..1 of accelerators actively computing, or null when not reported. */
  activity: number | null;
  /** Link type and speed, when a network has been applied. */
  link: { interface: string; ip: string; type: string; speed: number | null } | null;
}

export interface TopologyEdge {
  from: string;
  to: string;
  /** Drawn solid once Ray reports the worker alive. */
  active: boolean;
  label: string;
}

export interface Topology {
  width: number;
  height: number;
  nodes: TopologyNode[];
  edges: TopologyEdge[];
}

const WIDTH = 760;
const HEIGHT = 420;
const HEAD_R = 50;
const WORKER_R = 36;

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

/**
 * Fraction of this machine's accelerator memory that is committed.
 *
 * Read from the shape the agent actually sends. The previous version looked
 * for `gpu.used_percent`, `gpu.utilization` and `gpu_percent`, none of which
 * the agent has ever emitted, so this returned null for every machine and the
 * allocation arc never drew on any cluster.
 *
 * Memory rather than compute, because this is the number that answers "can
 * another model fit here". Compute is a different question and is answered
 * separately by `activityOf`.
 */
function allocationOf(node: ManagedNode | undefined): number | null {
  if (!node) return null;
  const gpu = asRecord(asRecord(node.latest_utilization).gpu);

  const used = Number(gpu.used_vram_bytes);
  const total = Number(gpu.total_vram_bytes);
  if (Number.isFinite(used) && Number.isFinite(total) && total > 0) {
    return Math.min(1, Math.max(0, used / total));
  }

  // Older agents reported only per-device mebibytes.
  const devices = Array.isArray(gpu.devices) ? gpu.devices : [];
  let usedMib = 0;
  let totalMib = 0;
  for (const entry of devices) {
    const device = asRecord(entry);
    usedMib += Number(device.memory_used_mib) || 0;
    totalMib += Number(device.memory_total_mib) || 0;
  }
  if (totalMib > 0) return Math.min(1, usedMib / totalMib);

  return null;
}

/**
 * Fraction of this machine's accelerators actively computing, if reported.
 *
 * Separate from allocation on purpose. A vLLM replica reserves most of the
 * card's memory the moment it starts and holds it whether or not a request is
 * in flight, so memory says "a model lives here" and this says "it is working
 * right now". Conflating them made a loaded-but-idle node look saturated.
 */
export function activityOf(node: ManagedNode | undefined): number | null {
  if (!node) return null;
  const gpu = asRecord(asRecord(node.latest_utilization).gpu);
  const devices = Array.isArray(gpu.devices) ? gpu.devices : [];
  const values = devices
    .map((entry) => Number(asRecord(entry).utilization_pct))
    .filter((value) => Number.isFinite(value) && value >= 0);
  if (values.length === 0) return null;
  const mean = values.reduce((a, b) => a + b, 0) / values.length;
  return Math.min(1, mean > 1 ? mean / 100 : mean);
}

/**
 * Place the head at the centre and the workers evenly around it.
 *
 * Starting at -90° puts the first worker at the top, so a two-machine pair
 * reads as a vertical link rather than an arbitrary diagonal.
 */
export function layoutTopology(
  cluster: InferenceEnvironment,
  members: EnvironmentNode[],
  nodes: ManagedNode[],
): Topology {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const fabric = asRecord(asRecord(cluster.observed_status).resolved_fabric);
  const boundByNode = asRecord(fabric.node_bindings);
  // The planner records the link speed on the fabric and, when the interface
  // reported one, on each binding too. Prefer the binding; fall back to the
  // fabric rather than dropping the number the operator came for.
  const fabricSpeed = Number(fabric.speed_gbps);

  const headId = cluster.head_node_id;
  const head =
    members.find((m) => m.node_id === headId) ??
    members.find((m) => (m.role || "").toLowerCase() === "head") ??
    members[0];
  const workers = members.filter((m) => m !== head);

  const cx = WIDTH / 2;
  const cy = HEIGHT / 2;
  const radius = Math.min(WIDTH, HEIGHT) / 2 - WORKER_R - 34;

  const describe = (member: EnvironmentNode, isHead: boolean, x: number, y: number): TopologyNode => {
    const managed = byId.get(member.node_id);
    const binding = asRecord(boundByNode[member.node_id]);
    const bindingSpeed = Number(binding.speed_gbps);
    const speed = Number.isFinite(bindingSpeed) && bindingSpeed > 0
      ? bindingSpeed
      : fabricSpeed;
    return {
      nodeId: member.node_id,
      host: nodeLabel(managed, member.node_id.slice(0, 8)),
      address: managed?.host ?? "",
      role: member.role,
      isHead,
      x,
      y,
      r: isHead ? HEAD_R : WORKER_R,
      memberStatus: member.member_status ?? null,
      allocation: allocationOf(managed),
      activity: activityOf(managed),
      link: binding.interface
        ? {
            interface: String(binding.interface),
            ip: String(binding.ip ?? ""),
            type: String(binding.link_type ?? "ethernet"),
            speed: Number.isFinite(speed) ? speed : null,
          }
        : null,
    };
  };

  const topologyNodes: TopologyNode[] = [];
  const edges: TopologyEdge[] = [];

  if (!head) return { width: WIDTH, height: HEIGHT, nodes: [], edges: [] };

  topologyNodes.push(describe(head, true, cx, cy));

  workers.forEach((member, index) => {
    const angle = (index / workers.length) * Math.PI * 2 - Math.PI / 2;
    const placed = describe(
      member,
      false,
      cx + Math.cos(angle) * radius,
      cy + Math.sin(angle) * radius,
    );
    topologyNodes.push(placed);
    edges.push({
      from: head.node_id,
      to: member.node_id,
      active: (member.member_status ?? "").toLowerCase() === "alive",
      label: placed.link
        ? `${placed.link.type}${placed.link.speed ? ` · ${placed.link.speed} Gb/s` : ""}`
        : "",
    });
  });

  return { width: WIDTH, height: HEIGHT, nodes: topologyNodes, edges };
}

/**
 * Ring colour for a machine: the cluster's view of it first, capacity second.
 *
 * The warning threshold is "there is no room for anything else", not "this
 * machine is busy". A serving replica reserves most of its card's memory by
 * design, so warning at 85% would paint every working cluster amber
 * permanently -- and a warning that is always on is not a warning.
 */
export function nodeTone(node: TopologyNode): "good" | "warn" | "bad" | "idle" {
  const status = (node.memberStatus ?? "").toLowerCase();
  if (status === "dead" || status === "failed") return "bad";
  if (!status || status === "unknown") return "idle";
  if (node.allocation !== null && node.allocation >= 0.95) return "warn";
  return "good";
}
