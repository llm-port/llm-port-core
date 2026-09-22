/**
 * The topology has to look different when the cluster is running.
 *
 * It did not. Every ring was grey and every link dashed, on a cluster with two
 * live machines serving traffic, because the field the colour is chosen from
 * (`member_status`) had no writer on the backend and the field the allocation
 * arc is sized from (`latest_utilization`) was dropped by the fleet list. The
 * picture was correct about the shape of the cluster and silent about its
 * state -- which is the one thing it exists to show.
 *
 * So these check the reading, not the drawing: given a live cluster the
 * picture must be visibly alive, and given a cluster that is down it must not
 * be. A diagram that looks the same either way is worse than no diagram,
 * because it is believed.
 */
import { describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";

import { ClusterTopology } from "./ClusterTopology";
import {
  HEAD_NODE_ID,
  WORKER_NODE_ID,
  environment,
  environmentNodes,
  managedNodes,
} from "~/test/inferenceFixtures";
import type { EnvironmentNode } from "~/api/inference";
import type { ManagedNode } from "~/api/nodes";

function draw(
  members: EnvironmentNode[] = environmentNodes,
  nodes: ManagedNode[] = managedNodes,
) {
  const { container } = render(
    <ClusterTopology cluster={environment} members={members} nodes={nodes} />,
  );
  return container;
}

/** Ray has not been asked yet, or has not answered. */
const unobserved: EnvironmentNode[] = environmentNodes.map((m) => ({
  ...m,
  member_status: null,
}));

/** A machine that has dropped out of the cluster. */
const workerDown: EnvironmentNode[] = environmentNodes.map((m) =>
  m.node_id === WORKER_NODE_ID ? { ...m, member_status: "dead" } : m,
);

/**
 * The agent's real utilization payload, not an invented one.
 *
 * Worth spelling out: the previous reader looked for `gpu.used_percent`,
 * which the agent has never sent, so the arc silently never drew on any real
 * cluster while a fixture using that key made the tests pass.
 */
const busy: ManagedNode[] = managedNodes.map((n) => ({
  ...n,
  latest_utilization: {
    gpu: {
      count: 1,
      used_vram_bytes: 72,
      total_vram_bytes: 100,
      devices: [
        {
          name: "NVIDIA GB10",
          memory_used_mib: 72,
          memory_total_mib: 100,
          utilization_pct: 63,
        },
      ],
    },
  },
}));

describe("a running cluster is visibly running", () => {
  it("pulses a ring for every machine in the cluster", () => {
    const container = draw();
    const pulses = container.querySelectorAll(
      'circle > animate[attributeName="stroke-opacity"]',
    );
    expect(pulses.length).toBeGreaterThanOrEqual(environmentNodes.length);
  });

  it("runs a pulse along each live link", () => {
    const container = draw();
    // One moving dot per edge, animating along both axes.
    expect(
      container.querySelectorAll('animate[attributeName="cx"]').length,
    ).toBe(environmentNodes.length - 1);
  });

  it("haloes a machine that is actually working", () => {
    const container = draw(environmentNodes, busy);
    expect(
      container.querySelectorAll('animate[attributeName="r"]').length,
    ).toBe(environmentNodes.length);
  });

  it("leaves an idle machine calm", () => {
    // Every node is live but none is loaded: the ring still breathes, but
    // nothing haloes. A diagram that churns while nothing happens teaches
    // the operator to stop looking at it.
    const container = draw();
    expect(container.querySelectorAll('animate[attributeName="r"]')).toHaveLength(
      0,
    );
  });
});

describe("a cluster that is not running does not pretend to be", () => {
  it("does not pulse a machine the cluster has not reported on", () => {
    const container = draw(unobserved);
    expect(
      container.querySelectorAll('circle > animate[attributeName="stroke-opacity"]'),
    ).toHaveLength(0);
  });

  it("does not run a pulse along a link that is not up", () => {
    const container = draw(unobserved);
    expect(container.querySelectorAll('animate[attributeName="cx"]')).toHaveLength(
      0,
    );
  });

  it("stops pulsing the machine that dropped out, and only that one", () => {
    const container = draw(workerDown);
    const rings = container.querySelectorAll(
      'circle > animate[attributeName="stroke-opacity"]',
    );
    expect(rings).toHaveLength(1); // the head, still in
  });
});

describe("reduced motion", () => {
  it("draws the same cluster completely still", () => {
    // SVG's own <animate> cannot be reached by a stylesheet, so honouring the
    // setting means leaving the elements out entirely.
    const original = window.matchMedia;
    window.matchMedia = vi.fn().mockImplementation((query: string) => ({
      matches: query.includes("prefers-reduced-motion"),
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }));
    try {
      const container = draw(environmentNodes, busy);
      expect(container.querySelectorAll("animate")).toHaveLength(0);
      // Still a picture: the machines are all there, just not moving.
      expect(
        container.querySelectorAll("circle").length,
      ).toBeGreaterThanOrEqual(environmentNodes.length);
    } finally {
      window.matchMedia = original;
    }
  });
});

describe("what is drawn at all", () => {
  it("names every machine the way the operator named it", () => {
    // The label is the agent's name, not its address: the operator chose one
    // and not the other, and an address is what the machine answers on rather
    // than what it is called.
    const text = draw().textContent ?? "";
    expect(text).toContain("spark-ts3202");
    expect(text).toContain("spark-3201");
  });

  it("keeps each machine's address available without printing it", () => {
    const container = draw();
    const addresses = [...container.querySelectorAll("g[data-address]")].map(
      (g) => g.getAttribute("data-address"),
    );
    expect(addresses).toEqual(
      expect.arrayContaining(["10.88.10.49", "10.88.10.71"]),
    );
  });

  it("puts the head in the middle", () => {
    const container = draw();
    const head = container.querySelector(`[aria-label*="spark-ts3202"]`);
    expect(head?.getAttribute("transform")).toBe("translate(380,210)");
  });

  it("says how much of a busy machine is committed", () => {
    const container = draw(environmentNodes, busy);
    expect(container.textContent).toContain("72% in use");
  });

  it("says nothing about a machine that has not reported utilization", () => {
    // The gap is rendered as a gap, never as 0%.
    const container = draw();
    expect(container.textContent).not.toContain("0% in use");
  });

  it("explains the motion in the legend", () => {
    expect(draw().textContent).toContain("A ring pulses");
  });

  it("offers a colour for a machine that dropped out", () => {
    expect(draw(workerDown).textContent).toContain("Dropped out");
  });

  it("says so plainly when there are no machines", () => {
    const container = draw([], []);
    expect(container.textContent).toContain("No machines in this cluster yet.");
  });
});

it("uses the head from the cluster record, not the first member listed", () => {
  const reversed = [...environmentNodes].reverse();
  const container = render(
    <ClusterTopology
      cluster={environment}
      members={reversed}
      nodes={managedNodes}
    />,
  ).container;
  const head = container.querySelector(`[aria-label*="spark-ts3202"]`);
  expect(head?.getAttribute("transform")).toBe("translate(380,210)");
  expect(HEAD_NODE_ID).toBeTruthy();
});
