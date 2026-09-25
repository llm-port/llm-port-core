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
  const total = providers.length;
  const pct = (n: number) => (total > 0 ? Math.round((n / total) * 100) : 0);
  const externalPct = pct(external.length);
  const unknownPct = pct(unknown.length);
  return {
    inside,
    external,
    unknown,
    badge: badgeFor(inside.length, external.length, unknown.length),
    // The remainder, so the three always add up to 100 despite rounding.
    insidePct: total > 0 ? 100 - externalPct - unknownPct : 100,
    externalPct,
    unknownPct,
  };
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
