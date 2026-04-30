/**
 * tourEngine — loads step catalogs, resolves eligibility, manages progress.
 *
 * Tour flow:  orientation steps → profile-specific task steps
 * Task flows: standalone sub-process guides (register endpoint, setup node, etc.)
 * Eligibility: steps filtered by module availability + superuser access
 */

import type { Step as JoyrideStep, Placement } from "react-joyride";
import { preferences, type TourProgress } from "~/api/preferences";

import orientationData from "~/data/tours/orientation.json";
import privateData from "~/data/tours/guided-setup-private.json";
import teamData from "~/data/tours/guided-setup-team.json";
import enterpriseData from "~/data/tours/guided-setup-enterprise.json";

import registerRemoteEndpoint from "~/data/tours/taskflows/register-remote-endpoint.json";
import setupRemoteNode from "~/data/tours/taskflows/setup-remote-node.json";
import runLocalModel from "~/data/tours/taskflows/run-local-model.json";

// ── Types ────────────────────────────────────────────────────────────

export interface TourStepDef {
  target: string;
  titleKey: string;
  contentKey: string;
  placement: string;
  route: string | null;
  requires: {
    module?: string;
    permission?: string;
    superuser?: boolean;
  };
  disableBeacon: boolean;
}

export interface TourCatalog {
  tourId: string;
  version: number;
  profile?: string;
  steps: TourStepDef[];
}

export type OnboardingProfile = "private" | "team" | "enterprise";

export interface EligibilityContext {
  isModuleEnabled: (module: string) => boolean;
  isSuperuser: boolean;
}

export interface ResolvedStep extends JoyrideStep {
  /** The original route this step targets (for cross-route navigation). */
  route: string | null;
  /** Index in the original combined catalog (orientation + profile). */
  originalIndex: number;
  /** Skip the beacon animation before showing the tooltip. */
  skipBeacon: boolean;
}

// ── Catalog loading ──────────────────────────────────────────────────

const PROFILE_CATALOGS: Record<OnboardingProfile, TourCatalog> = {
  private: privateData as TourCatalog,
  team: teamData as TourCatalog,
  enterprise: enterpriseData as TourCatalog,
};

const orientationCatalog = orientationData as TourCatalog;

/**
 * Returns the combined tour ID for a profile-based guided setup.
 */
export function guidedSetupTourId(profile: OnboardingProfile): string {
  return `guided_setup_${profile}`;
}

/**
 * Load combined steps: orientation + profile-specific tasks.
 */
export function loadGuidedSetupSteps(
  profile: OnboardingProfile,
): TourStepDef[] {
  const profileCatalog = PROFILE_CATALOGS[profile];
  return [...orientationCatalog.steps, ...profileCatalog.steps];
}

// ── Eligibility resolution ───────────────────────────────────────────

/**
 * Filter steps based on runtime eligibility (module enabled, superuser).
 * Returns Joyride-compatible steps with translations applied via `t()`.
 */
export function resolveEligibleSteps(
  steps: TourStepDef[],
  ctx: EligibilityContext,
  t: (key: string, opts?: Record<string, string>) => string,
): ResolvedStep[] {
  const resolved: ResolvedStep[] = [];

  for (let i = 0; i < steps.length; i++) {
    const step = steps[i];

    // Check module requirement
    if (step.requires.module && !ctx.isModuleEnabled(step.requires.module)) {
      continue;
    }

    // Check superuser requirement
    if (step.requires.superuser && !ctx.isSuperuser) {
      continue;
    }

    resolved.push({
      target: step.target,
      title: t(step.titleKey, { ns: "tour", defaultValue: step.titleKey }),
      content: t(step.contentKey, {
        ns: "tour",
        defaultValue: step.contentKey,
      }),
      placement: step.placement as Placement,
      skipBeacon: step.disableBeacon,
      route: step.route,
      originalIndex: i,
    });
  }

  return resolved;
}

// ── Progress persistence ─────────────────────────────────────────────

/**
 * Save current step index for a tour.
 */
export async function saveTourProgress(
  tourId: string,
  stepIndex: number,
  version: number,
): Promise<void> {
  await preferences.update({
    tour_progress: {
      [tourId]: {
        version,
        current_step: stepIndex,
        completed: false,
        skipped_steps: [],
        completed_at: null,
      },
    },
  });
}

/**
 * Mark a tour as completed.
 */
export async function completeTour(
  tourId: string,
  version: number,
): Promise<void> {
  await preferences.update({
    tour_progress: {
      [tourId]: {
        version,
        current_step: -1,
        completed: true,
        skipped_steps: [],
        completed_at: new Date().toISOString(),
      },
    },
  });
}

/**
 * Get saved progress for a tour, or null if none exists.
 */
export async function getTourProgress(
  tourId: string,
): Promise<TourProgress | null> {
  const res = await preferences.get();
  return res.preferences?.tour_progress?.[tourId] ?? null;
}

// ── Task-flow sub-process guides ─────────────────────────────────────

export interface TaskFlowStepDef {
  target: string;
  titleKey: string;
  contentKey: string;
  placement: string;
  /** If true, the tour will wait (MutationObserver) until the target appears in the DOM. */
  waitForTarget: boolean;
  disableBeacon: boolean;
}

export interface TaskFlowCatalog {
  flowId: string;
  titleKey: string;
  descriptionKey: string;
  /** MUI icon name (e.g. "Cloud", "Dns", "Memory"). */
  icon: string;
  /** The route to navigate to before starting the flow. */
  route: string;
  requires: {
    module?: string;
    superuser?: boolean;
  };
  steps: TaskFlowStepDef[];
}

export interface ResolvedTaskFlowStep extends JoyrideStep {
  /** Whether the tour should wait for this target to appear in the DOM. */
  waitForTarget: boolean;
  /** Skip the beacon animation. */
  skipBeacon: boolean;
  /** Index in the original catalog. */
  originalIndex: number;
}

const TASK_FLOW_CATALOGS: TaskFlowCatalog[] = [
  registerRemoteEndpoint as unknown as TaskFlowCatalog,
  setupRemoteNode as unknown as TaskFlowCatalog,
  runLocalModel as unknown as TaskFlowCatalog,
];

/**
 * Return all registered task flow catalogs.
 */
export function listTaskFlows(): TaskFlowCatalog[] {
  return TASK_FLOW_CATALOGS;
}

/**
 * Look up a task flow by its ID.
 */
export function getTaskFlow(flowId: string): TaskFlowCatalog | undefined {
  return TASK_FLOW_CATALOGS.find((f) => f.flowId === flowId);
}

/**
 * Filter task flows based on module eligibility.
 */
export function resolveEligibleTaskFlows(
  ctx: EligibilityContext,
): TaskFlowCatalog[] {
  return TASK_FLOW_CATALOGS.filter((flow) => {
    if (flow.requires.module && !ctx.isModuleEnabled(flow.requires.module)) {
      return false;
    }
    if (flow.requires.superuser && !ctx.isSuperuser) {
      return false;
    }
    return true;
  });
}

/**
 * Resolve task flow steps with translations.
 */
export function resolveTaskFlowSteps(
  flow: TaskFlowCatalog,
  t: (key: string, opts?: Record<string, string>) => string,
): ResolvedTaskFlowStep[] {
  return flow.steps.map((step, i) => ({
    target: step.target,
    title: t(step.titleKey, { ns: "tour", defaultValue: step.titleKey }),
    content: t(step.contentKey, {
      ns: "tour",
      defaultValue: step.contentKey,
    }),
    placement: step.placement as Placement,
    // Hide the overlay for dialog/drawer steps so MUI portals stay
    // interactive (Joyride's z-index is higher than MUI Dialog's).
    hideOverlay: step.waitForTarget,
    waitForTarget: step.waitForTarget,
    skipBeacon: step.disableBeacon,
    originalIndex: i,
  }));
}
