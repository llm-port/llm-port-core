/**
 * Where providers' prompts go, for the data residency map and its dashboard card.
 *
 * The backend decides it from where each endpoint is (services/llm/residency.py)
 * and says why. The map used to decide from how a provider was added --
 * `remote_endpoint` meant "cloud" -- so a vLLM on one of our own machines read
 * as cloud, and providers served by our clusters were counted nowhere.
 */
import type { Provider, Residency, ResidencyKind } from "~/api/llm";

export type ResidencyBadge = "air_gapped" | "hybrid" | "cloud_only" | "none";

/** The provider's residency kind; managed targets are ours even without an answer. */
export function residencyKind(provider: Provider): ResidencyKind {
  if (provider.residency?.kind) return provider.residency.kind;
  if (provider.target === "local_docker" || provider.target === "inference_cluster") return "machines";
  return "unknown";
}

/** Stays on the organisation's own machines or network. */
export function staysInside(kind: ResidencyKind): boolean {
  return kind === "machines" || kind === "private";
}

export interface ResidencySplit {
  inside: Provider[];
  external: Provider[];
  unknown: Provider[];
  badge: ResidencyBadge;
  /** Share of providers known to stay inside, 0-100. */
  insidePct: number;
  /** Share known to leave; what is unknown is in neither. */
  externalPct: number;
  unknownPct: number;
}

export function splitByResidency(providers: Provider[]): ResidencySplit {
  const inside: Provider[] = [];
  const external: Provider[] = [];
  const unknown: Provider[] = [];
  for (const p of providers) {
    const kind = residencyKind(p);
    if (staysInside(kind)) inside.push(p);
    else if (kind === "external") external.push(p);
    else unknown.push(p);
  }
  return {
    inside,
    external,
    unknown,
    badge: badgeFor(inside.length, external.length, unknown.length),
    ...shares(inside.length, unknown.length, external.length),
  };
}

export interface Shares {
  insidePct: number;
  unknownPct: number;
  externalPct: number;
}

/**
 * Percent shares of what stays inside, what is unknown and what leaves --
 * providers or tokens. They always add up to 100 despite rounding; with
 * nothing at all, everything is inside.
 */
export function shares(inside: number, unknown: number, external: number): Shares {
  const total = inside + unknown + external;
  if (total <= 0) return { insidePct: 100, unknownPct: 0, externalPct: 0 };
  const externalPct = Math.round((external / total) * 100);
  const unknownPct = Math.min(Math.round((unknown / total) * 100), 100 - externalPct);
  return { insidePct: 100 - externalPct - unknownPct, unknownPct, externalPct };
}

/**
 * Air-gapped only when nothing leaves *and* nothing is unknown: a provider we
 * could not place may be sending prompts anywhere, and the badge is a claim.
 */
export function badgeFor(inside: number, external: number, unknown: number): ResidencyBadge {
  if (inside + external + unknown === 0) return "none";
  if (external === 0 && unknown === 0) return "air_gapped";
  if (inside === 0 && unknown === 0) return "cloud_only";
  return "hybrid";
}

type T = (key: string, opts?: Record<string, unknown>) => string;

/** Why a provider is where it is, in words. */
export function residencyReason(t: T, residency: Residency | null | undefined): string {
  if (!residency) return "";
  const params = {
    host: residency.host ?? "",
    addresses: residency.addresses.join(", "),
    machine: residency.machine ?? "",
    provider: residency.provider ?? "",
  };
  return t(`security_map.residency_source.${residency.source}`, { ...params, defaultValue: "" });
}
