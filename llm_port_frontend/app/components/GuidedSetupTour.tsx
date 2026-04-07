/**
 * GuidedSetupTour — Joyride v3 wrapper for the guided-setup tour.
 *
 * Renders a `<Joyride>` overlay that:
 *   1. Walks through UI orientation steps (nav groups)
 *   2. Then profile-specific task steps
 *   3. Navigates to the correct route before highlighting a target
 *   4. Persists progress to the backend preferences API
 *   5. Uses MUI-themed tooltip styling
 */
import { useEffect, useMemo, useRef, useState } from "react";
import {
  Joyride,
  type EventData,
  type Controls,
  type TooltipRenderProps,
  STATUS,
  ACTIONS,
  EVENTS,
} from "react-joyride";
import { useNavigate, useLocation } from "react-router";
import { useTranslation } from "react-i18next";
import { useTheme } from "@mui/material/styles";

import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import IconButton from "@mui/material/IconButton";
import LinearProgress from "@mui/material/LinearProgress";
import Typography from "@mui/material/Typography";

import CloseIcon from "@mui/icons-material/Close";

import { useServices } from "~/lib/ServicesContext";
import {
  loadGuidedSetupSteps,
  resolveEligibleSteps,
  saveTourProgress,
  completeTour,
  guidedSetupTourId,
  type OnboardingProfile,
  type ResolvedStep,
} from "~/lib/tourEngine";

// ── Props ────────────────────────────────────────────────────────────

export interface GuidedSetupTourProps {
  /** Whether the tour is currently running. */
  run: boolean;
  /** The user's selected onboarding profile. */
  profile: OnboardingProfile;
  /** Whether the current user is a superuser. */
  isSuperuser: boolean;
  /** Called when the tour finishes or is closed. */
  onFinish: () => void;
  /** Optional: resume from a specific step index. */
  initialStep?: number;
}

// ── Custom tooltip component (MUI-themed) ────────────────────────────

function TourTooltip({
  index,
  step,
  size,
  isLastStep,
  backProps,
  primaryProps,
  skipProps,
  tooltipProps,
}: TooltipRenderProps) {
  const theme = useTheme();
  const { t } = useTranslation();
  const progress = size > 0 ? ((index + 1) / size) * 100 : 0;

  return (
    <Box
      {...tooltipProps}
      sx={{
        bgcolor: "background.paper",
        color: "text.primary",
        borderRadius: 2,
        boxShadow: theme.shadows[8],
        maxWidth: 380,
        minWidth: 280,
        p: 0,
        overflow: "hidden",
      }}
    >
      {/* Progress bar */}
      <LinearProgress
        variant="determinate"
        value={progress}
        sx={{ height: 3 }}
      />

      {/* Header */}
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          px: 2,
          pt: 1.5,
          pb: 0.5,
        }}
      >
        <Typography variant="subtitle2" sx={{ fontWeight: 600 }}>
          {step.title as string}
        </Typography>
        <IconButton
          size="small"
          aria-label={skipProps["aria-label"]}
          onClick={skipProps.onClick}
        >
          <CloseIcon fontSize="small" />
        </IconButton>
      </Box>

      {/* Content */}
      <Box sx={{ px: 2, pb: 1.5 }}>
        <Typography variant="body2" color="text.secondary">
          {step.content as string}
        </Typography>
      </Box>

      {/* Step counter */}
      <Box sx={{ px: 2, pb: 0.5 }}>
        <Typography variant="caption" color="text.disabled">
          {t("ui.step_counter", {
            ns: "tour",
            current: String(index + 1),
            total: String(size),
            defaultValue: "Step {{current}} of {{total}}",
          })}
        </Typography>
      </Box>

      {/* Actions */}
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          px: 2,
          pb: 1.5,
          pt: 0.5,
        }}
      >
        <Button
          size="small"
          variant="text"
          aria-label={skipProps["aria-label"]}
          onClick={skipProps.onClick}
        >
          {t("ui.skip", { ns: "tour", defaultValue: "Skip tour" })}
        </Button>
        <Box sx={{ display: "flex", gap: 1 }}>
          {index > 0 && (
            <Button
              size="small"
              variant="outlined"
              aria-label={backProps["aria-label"]}
              onClick={backProps.onClick}
            >
              {t("ui.back", { ns: "tour", defaultValue: "Back" })}
            </Button>
          )}
          <Button
            size="small"
            variant="contained"
            aria-label={primaryProps["aria-label"]}
            onClick={primaryProps.onClick}
          >
            {isLastStep
              ? t("ui.finish", { ns: "tour", defaultValue: "Finish" })
              : t("ui.next", { ns: "tour", defaultValue: "Next" })}
          </Button>
        </Box>
      </Box>
    </Box>
  );
}

// ── Main component ───────────────────────────────────────────────────

export function GuidedSetupTour({
  run,
  profile,
  isSuperuser,
  onFinish,
  initialStep = 0,
}: GuidedSetupTourProps) {
  const navigate = useNavigate();
  const location = useLocation();
  const { t } = useTranslation();
  const { isModuleEnabled } = useServices();
  const theme = useTheme();

  const [stepIndex, setStepIndex] = useState(initialStep);
  const [isNavigating, setIsNavigating] = useState(false);
  const pendingStepRef = useRef<number | null>(null);

  const tourId = guidedSetupTourId(profile);

  // Build eligible steps
  const steps: ResolvedStep[] = useMemo(() => {
    const allSteps = loadGuidedSetupSteps(profile);
    return resolveEligibleSteps(allSteps, { isModuleEnabled, isSuperuser }, t);
  }, [profile, isModuleEnabled, isSuperuser, t]);

  // After navigation completes, resume to pending step
  useEffect(() => {
    if (pendingStepRef.current !== null && !isNavigating) {
      const pending = pendingStepRef.current;
      pendingStepRef.current = null;
      // Small delay to let the DOM render the target element
      const timer = setTimeout(() => setStepIndex(pending), 300);
      return () => clearTimeout(timer);
    }
  }, [location.pathname, isNavigating]);

  // ── Highlight ring on the active step's target element ──────────
  useEffect(() => {
    if (!run || steps.length === 0) return;
    const currentStep = steps[stepIndex];
    if (!currentStep) return;

    const el = document.querySelector(currentStep.target as string);
    if (el) {
      (el as HTMLElement).classList.add("tour-active-target");
      return () => {
        (el as HTMLElement).classList.remove("tour-active-target");
      };
    }
  }, [run, stepIndex, steps]);

  function handleEvent(data: EventData, controls: Controls) {
    const { action, index, status, type } = data;

    // Tour finished or skipped
    if (status === STATUS.FINISHED || status === STATUS.SKIPPED) {
      if (status === STATUS.FINISHED) {
        void completeTour(tourId, 1);
      }
      controls.close();
      onFinish();
      return;
    }

    // Handle step transitions
    if (type === EVENTS.STEP_AFTER) {
      const nextIndex = action === ACTIONS.PREV ? index - 1 : index + 1;

      if (nextIndex < 0 || nextIndex >= steps.length) {
        return;
      }

      const nextStep = steps[nextIndex];

      // Cross-route navigation: if next step targets a different route
      if (nextStep.route && !location.pathname.startsWith(nextStep.route)) {
        setIsNavigating(true);
        pendingStepRef.current = nextIndex;
        navigate(nextStep.route);
        // Navigation effect will resume the tour
        setTimeout(() => setIsNavigating(false), 100);
      } else {
        setStepIndex(nextIndex);
      }

      // Persist progress (best-effort, don't block UI)
      void saveTourProgress(tourId, nextIndex, 1);
    }
  }

  if (!run || steps.length === 0) return null;

  return (
    <>
      {/* Highlight ring CSS for active step target */}
      <style>{`
        .tour-active-target {
          outline: 2px solid ${theme.palette.primary.main} !important;
          outline-offset: 4px;
          border-radius: 8px;
          animation: tour-pulse 1.5s ease-in-out infinite;
        }
        @keyframes tour-pulse {
          0%, 100% { outline-color: ${theme.palette.primary.main}; }
          50% { outline-color: ${theme.palette.primary.light}; }
        }
      `}</style>
      <Joyride
        steps={steps}
        stepIndex={stepIndex}
        run={run && !isNavigating}
        continuous
        onEvent={handleEvent}
        tooltipComponent={TourTooltip}
      />
    </>
  );
}
