/**
 * tourEngine — loads step catalogs, resolves eligibility, manages progress.
 *
 * Tour flow:  orientation steps → profile-specific task steps
 * Eligibility: steps filtered by module availability + superuser access
 */

import type { Step as JoyrideStep, Placement } from "react-joyride";
import { preferences, type TourProgress } from "~/api/preferences";

import orientationData from "~/data/tours/orientation.json";
import privateData from "~/data/tours/guided-setup-private.json";
import teamData from "~/data/tours/guided-setup-team.json";
import enterpriseData from "~/data/tours/guided-setup-enterprise.json";

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
export function loadGuidedSetupSteps(profile: OnboardingProfile): TourStepDef[] {
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
      content: t(step.contentKey, { ns: "tour", defaultValue: step.contentKey }),
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
