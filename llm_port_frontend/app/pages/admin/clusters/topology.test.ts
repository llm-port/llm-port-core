/**
 * Topology geometry. Rendering is checked in the page test; here the concern
 * is that the picture describes the real cluster — the right machine at the
 * centre, one edge per member, and no invented links.
 */
import { describe, expect, it } from "vitest";

import { layoutTopology, nodeTone } from "./topology";
import {
  HEAD_NODE_ID,
  WORKER_NODE_ID,
  environment,
  environmentNodes,
  managedNodes,
} from "~/test/inferenceFixtures";

describe("layoutTopology", () => {
  it("puts the head at the centre", () => {
    const topology = layoutTopology(environment, environmentNodes, managedNodes);
    const head = topology.nodes.find((n) => n.isHead);
    expect(head?.nodeId).toBe(HEAD_NODE_ID);
    expect(head?.x).toBe(topology.width / 2);
    expect(head?.y).toBe(topology.height / 2);
  });

  it("draws every member exactly once", () => {
    const topology = layoutTopology(environment, environmentNodes, managedNodes);
    expect(topology.nodes).toHaveLength(environmentNodes.length);
    expect(new Set(topology.nodes.map((n) => n.nodeId)).size).toBe(
      environmentNodes.length,
    );
  });

  it("draws one edge from the head to each worker and no others", () => {
    const topology = layoutTopology(environment, environmentNodes, managedNodes);
    expect(topology.edges).toHaveLength(environmentNodes.length - 1);
    expect(topology.edges.every((e) => e.from === HEAD_NODE_ID)).toBe(true);
    expect(topology.edges[0].to).toBe(WORKER_NODE_ID);
  });

  it("labels the edge with the link that actually carries traffic", () => {
    const topology = layoutTopology(environment, environmentNodes, managedNodes);
    expect(topology.edges[0].label).toContain("roce");
    expect(topology.edges[0].label).toContain("200 Gb/s");
  });

  it("resolves host names rather than showing raw ids", () => {
    const topology = layoutTopology(environment, environmentNodes, managedNodes);
    expect(topology.nodes.map((n) => n.host)).toContain("spark-ts3202");
  });

  it("leaves the edge unlabelled when no network has been applied", () => {
    const bare = { ...environment, observed_status: {} };
    const topology = layoutTopology(bare, environmentNodes, managedNodes);
    expect(topology.edges[0].label).toBe("");
    expect(topology.nodes.every((n) => n.link === null)).toBe(true);
  });

  it("returns an empty layout for a cluster with no machines", () => {
    const topology = layoutTopology(environment, [], managedNodes);
    expect(topology.nodes).toEqual([]);
    expect(topology.edges).toEqual([]);
  });

  it("falls back to the head role when no head node is bound", () => {
    const unbound = { ...environment, head_node_id: null };
    const topology = layoutTopology(unbound, environmentNodes, managedNodes);
    expect(topology.nodes.find((n) => n.isHead)?.nodeId).toBe(HEAD_NODE_ID);
  });
});

describe("nodeTone", () => {
  const base = {
    nodeId: "n",
    host: "h",
    address: "10.0.0.1",
    role: "worker",
    isHead: false,
    x: 0,
    y: 0,
    r: 10,
    link: null,
    activity: null,
  };

  it("is idle when the runtime has not reported on the machine", () => {
    expect(nodeTone({ ...base, memberStatus: null, allocation: null })).toBe("idle");
  });

  it("is bad when the runtime says the machine is gone", () => {
    expect(nodeTone({ ...base, memberStatus: "dead", allocation: 0.1 })).toBe("bad");
  });

  it("warns only when there is no room left for anything else", () => {
    expect(nodeTone({ ...base, memberStatus: "alive", allocation: 0.96 })).toBe(
      "warn",
    );
  });

  it("does not warn about a machine that is simply serving a model", () => {
    // A vLLM replica reserves most of its card's memory the moment it starts.
    // Warning at that point would paint every working cluster amber for as
    // long as it was working, and a warning that is always on is not one.
    expect(nodeTone({ ...base, memberStatus: "alive", allocation: 0.88 })).toBe(
      "good",
    );
  });

  it("is good for a live machine with room left", () => {
    expect(nodeTone({ ...base, memberStatus: "alive", allocation: 0.3 })).toBe("good");
  });
});
