import { describe, expect, it } from "vitest";

import type { Provider, Residency } from "~/api/llm";

import { badgeFor, residencyKind, splitByResidency } from "./residency";

function provider(target: Provider["target"], residency?: Partial<Residency> | null): Provider {
  return {
    id: Math.random().toString(36).slice(2),
    name: "p",
    type: "vllm",
    target,
    endpoint_url: null,
    capabilities: null,
    remote_model: null,
    litellm_provider: null,
    litellm_model: null,
    extra_params: null,
    source_kind: null,
    source_id: null,
    managed_by: null,
    residency:
      residency === null || residency === undefined
        ? residency
        : { kind: "unknown", source: "unresolved", host: null, addresses: [], machine: null, provider: null, ...residency },
    created_at: "",
    updated_at: "",
  };
}

describe("data residency", () => {
  it("counts a remote endpoint by where it is, not by how it was added", () => {
    const split = splitByResidency([
      provider("remote_endpoint", { kind: "machines", source: "machine" }), // a found vLLM on our machine
      provider("remote_endpoint", { kind: "private", source: "private_address" }), // self-hosted on the LAN
      provider("remote_endpoint", { kind: "external", source: "cloud_provider" }),
      provider("inference_cluster", { kind: "machines", source: "managed" }), // used to be counted nowhere
    ]);
    expect(split.inside).toHaveLength(3);
    expect(split.external).toHaveLength(1);
    expect(split.badge).toBe("hybrid");
    expect(split.insidePct).toBe(75);
  });

  it("does not count what it cannot place as leaving", () => {
    const split = splitByResidency([
      provider("inference_cluster", { kind: "machines", source: "managed" }),
      provider("remote_endpoint", { kind: "machines", source: "machine" }),
      provider("remote_endpoint", { kind: "external", source: "cloud_provider" }),
      provider("remote_endpoint", { kind: "unknown", source: "unresolved" }),
    ]);
    expect([split.insidePct, split.unknownPct, split.externalPct]).toEqual([50, 25, 25]);
    const thirds = splitByResidency([
      provider("local_docker", null),
      provider("remote_endpoint", { kind: "external", source: "public_address" }),
      provider("remote_endpoint", null),
    ]);
    expect(thirds.insidePct + thirds.externalPct + thirds.unknownPct).toBe(100);
  });

  it("never calls a setup air-gapped while a provider is unknown", () => {
    expect(badgeFor(2, 0, 0)).toBe("air_gapped");
    expect(badgeFor(2, 0, 1)).toBe("hybrid");
    expect(badgeFor(0, 2, 0)).toBe("cloud_only");
    expect(badgeFor(0, 0, 0)).toBe("none");
  });

  it("treats what we manage as ours even before the backend answers", () => {
    expect(residencyKind(provider("local_docker", null))).toBe("machines");
    expect(residencyKind(provider("inference_cluster", null))).toBe("machines");
    expect(residencyKind(provider("remote_endpoint", null))).toBe("unknown");
  });
});
