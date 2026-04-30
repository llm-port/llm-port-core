/**
 * TaskFlowTour — Joyride overlay for task-flow sub-process guides.
 *
 * Unlike the main guided-setup tour (which only highlights sidebar items),
 * task flows walk users through interactive workflows that may involve
 * dialog/drawer elements that don't exist in the DOM until the user clicks.
 *
 * Features:
 *   - Navigates to the correct route before starting
 *   - Waits for target elements to appear (MutationObserver) when step
 *     has `waitForTarget: true`
 *   - MUI-themed tooltip (reuses same styles as GuidedSetupTour)
 *   - Saves progress to preferences API
 */
import { useEffect, useMemo, useRef, useState, useCallback } from "react";
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

import {
  getTaskFlow,
  resolveTaskFlowSteps,
  saveTourProgress,
  completeTour,
  type ResolvedTaskFlowStep,
} from "~/lib/tourEngine";

// ── Props ────────────────────────────────────────────────────────────

export interface TaskFlowTourProps {
  /** Whether the tour is currently running. */
  run: boolean;
  /** The task flow ID to execute. */
  flowId: string;
  /** Called when the tour finishes or is closed. */
  onFinish: () => void;
}

// ── Tooltip ──────────────────────────────────────────────────────────

function TaskFlowTooltip({
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
      <LinearProgress
        variant="determinate"
        value={progress}
        sx={{ height: 3 }}
      />

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

      <Box sx={{ px: 2, pb: 1.5 }}>
        <Typography variant="body2" color="text.secondary">
          {step.content as string}
        </Typography>
      </Box>

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
          {t("ui.skip", { ns: "tour", defaultValue: "Skip" })}
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

// ── Wait-for-target helper ───────────────────────────────────────────

/**
 * Returns a Promise that resolves when `selector` matches an element in
 * the DOM, or rejects after `timeoutMs`.
 */
function waitForElement(
  selector: string,
  timeoutMs = 10_000,
): Promise<Element> {
  return new Promise((resolve, reject) => {
    const existing = document.querySelector(selector);
    if (existing) {
      resolve(existing);
      return;
    }

    const observer = new MutationObserver(() => {
      const el = document.querySelector(selector);
      if (el) {
        observer.disconnect();
        clearTimeout(timer);
        resolve(el);
      }
    });

    observer.observe(document.body, { childList: true, subtree: true });

    const timer = setTimeout(() => {
      observer.disconnect();
      reject(new Error(`Timeout waiting for ${selector}`));
    }, timeoutMs);
  });
}

// ── Main component ───────────────────────────────────────────────────

export function TaskFlowTour({ run, flowId, onFinish }: TaskFlowTourProps) {
  const navigate = useNavigate();
  const location = useLocation();
  const { t } = useTranslation();
  const theme = useTheme();

  const [stepIndex, setStepIndex] = useState(0);
  const [waiting, setWaiting] = useState(false);
  const [hasNavigated, setHasNavigated] = useState(false);
  const waitingRef = useRef(false);
  /** Tracks the auto-advance observer so we can disconnect on cleanup. */
  const autoAdvanceObserverRef = useRef<MutationObserver | null>(null);
  /** Tracks the target-disappearance observer. */
  const disappearObserverRef = useRef<MutationObserver | null>(null);

  const flow = useMemo(() => getTaskFlow(flowId), [flowId]);
  const steps: ResolvedTaskFlowStep[] = useMemo(() => {
    if (!flow) return [];
    return resolveTaskFlowSteps(flow, t);
  }, [flow, t]);

  const tourId = `taskflow_${flowId}`;

  /** Disconnect any outstanding auto-advance / disappear observers. */
  const cleanupObservers = useCallback(() => {
    autoAdvanceObserverRef.current?.disconnect();
    autoAdvanceObserverRef.current = null;
    disappearObserverRef.current?.disconnect();
    disappearObserverRef.current = null;
  }, []);

  // Navigate to flow's starting route when tour begins
  useEffect(() => {
    if (!run || !flow) return;
    if (flow.route && !location.pathname.startsWith(flow.route)) {
      navigate(flow.route);
    }
    setStepIndex(0);
    setHasNavigated(true);
  }, [run, flow, navigate, location.pathname]);

  // Wait for target element when step has waitForTarget
  const advanceToStep = useCallback(
    (nextIndex: number) => {
      if (nextIndex < 0 || nextIndex >= steps.length) return;

      const nextStep = steps[nextIndex];
      if (nextStep.waitForTarget) {
        // Check if target already exists
        const existing = document.querySelector(nextStep.target as string);
        if (existing) {
          setStepIndex(nextIndex);
          return;
        }

        // Wait for target to appear
        setWaiting(true);
        waitingRef.current = true;
        waitForElement(nextStep.target as string, 15_000)
          .then(() => {
            if (waitingRef.current) {
              // Small delay for CSS transitions
              setTimeout(() => {
                setStepIndex(nextIndex);
                setWaiting(false);
                waitingRef.current = false;
              }, 200);
            }
          })
          .catch(() => {
            // Target never appeared — skip to next or finish
            setWaiting(false);
            waitingRef.current = false;
            if (nextIndex + 1 < steps.length) {
              advanceToStep(nextIndex + 1);
            } else {
              void completeTour(tourId, 1);
              onFinish();
            }
          });
      } else {
        setStepIndex(nextIndex);
      }
    },
    [steps, tourId, onFinish],
  );

  // ── Auto-advance: watch for the NEXT step's target while showing
  //    the current step. When the user clicks the target element (e.g.
  //    "Add Provider"), the dialog opens and the observer fires,
  //    advancing the tour without needing the "Next" button.
  useEffect(() => {
    if (!run || steps.length === 0) return;
    cleanupObservers();

    const nextStep = steps[stepIndex + 1];
    if (!nextStep?.waitForTarget) return;

    const nextTarget = nextStep.target as string;

    // If target already exists, advance immediately
    if (document.querySelector(nextTarget)) {
      setTimeout(() => advanceToStep(stepIndex + 1), 150);
      return;
    }

    const observer = new MutationObserver(() => {
      if (document.querySelector(nextTarget)) {
        observer.disconnect();
        autoAdvanceObserverRef.current = null;
        setTimeout(() => {
          advanceToStep(stepIndex + 1);
          void saveTourProgress(tourId, stepIndex + 1, 1);
        }, 250);
      }
    });
    observer.observe(document.body, { childList: true, subtree: true });
    autoAdvanceObserverRef.current = observer;

    return () => {
      observer.disconnect();
      autoAdvanceObserverRef.current = null;
    };
  }, [run, stepIndex, steps, advanceToStep, cleanupObservers, tourId]);

  // ── Target disappearance: if the current step targets a dialog/drawer
  //    and the user closes it, stop the tour gracefully instead of
  //    rendering a ghost tooltip at the screen edge.
  useEffect(() => {
    if (!run || steps.length === 0) return;
    const currentStep = steps[stepIndex];
    if (!currentStep?.waitForTarget) return;

    const selector = currentStep.target as string;

    // Only watch for removal if target currently exists
    if (!document.querySelector(selector)) return;

    const observer = new MutationObserver(() => {
      if (!document.querySelector(selector)) {
        observer.disconnect();
        disappearObserverRef.current = null;
        // Target disappeared (dialog/drawer closed) — stop tour
        onFinish();
      }
    });
    observer.observe(document.body, { childList: true, subtree: true });
    disappearObserverRef.current = observer;

    return () => {
      observer.disconnect();
      disappearObserverRef.current = null;
    };
  }, [run, stepIndex, steps, onFinish]);

  // Highlight ring
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

  // Cleanup on stop
  useEffect(() => {
    if (!run) {
      waitingRef.current = false;
      setWaiting(false);
      cleanupObservers();
      sweepJoyrideDOM();
    }
    return () => {
      waitingRef.current = false;
      cleanupObservers();
      sweepJoyrideDOM();
    };
  }, [run, cleanupObservers, sweepJoyrideDOM]);

  /** Forcibly remove Joyride portal elements and highlight classes. */
  const sweepJoyrideDOM = useCallback(() => {
    document.querySelectorAll(".tour-active-target").forEach((el) => {
      el.classList.remove("tour-active-target");
    });
    // Joyride may not tear down its portal synchronously after
    // controls.close(), so sweep twice — once now and once after
    // the next frame to catch late renders.
    const remove = () =>
      document
        .querySelectorAll(
          "#react-joyride-portal, .react-joyride__overlay, .react-joyride__spotlight, .react-joyride__floater",
        )
        .forEach((el) => el.remove());
    remove();
    requestAnimationFrame(remove);
    setTimeout(remove, 100);
  }, []);

  function handleEvent(data: EventData, controls: Controls) {
    const { action, index, status, type } = data;

    if (status === STATUS.FINISHED || status === STATUS.SKIPPED) {
      if (status === STATUS.FINISHED) {
        void completeTour(tourId, 1);
      }
      controls.close();
      cleanupObservers();
      sweepJoyrideDOM();
      onFinish();
      return;
    }

    // Target element not found (e.g. dialog was closed) — stop gracefully
    if (type === EVENTS.TARGET_NOT_FOUND) {
      controls.close();
      cleanupObservers();
      sweepJoyrideDOM();
      onFinish();
      return;
    }

    if (type === EVENTS.STEP_AFTER) {
      const nextIndex = action === ACTIONS.PREV ? index - 1 : index + 1;

      if (nextIndex < 0 || nextIndex >= steps.length) return;

      advanceToStep(nextIndex);
      void saveTourProgress(tourId, nextIndex, 1);
    }
  }

  if (!run || steps.length === 0 || !hasNavigated) return null;

  return (
    <>
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
        run={run && !waiting}
        continuous
        onEvent={handleEvent}
        tooltipComponent={TaskFlowTooltip}
      />
    </>
  );
}
