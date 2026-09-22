/**
 * The thread the old screens were missing.
 *
 * One banner, at the top of every cluster screen, saying what to do next and
 * why. It is the same `NextStep` the readiness module derives, so the banner
 * and the page can never disagree about what state the cluster is in.
 */
import Alert from "@mui/material/Alert";
import AlertTitle from "@mui/material/AlertTitle";
import Button from "@mui/material/Button";
import LinearProgress from "@mui/material/LinearProgress";
import Stack from "@mui/material/Stack";

import type { NextStep, ReadinessTone } from "./readiness";

const SEVERITY: Record<ReadinessTone, "info" | "success" | "warning"> = {
  action: "info",
  progress: "info",
  warning: "warning",
  success: "success",
};

export interface NextStepBannerProps {
  step: NextStep;
  onAction?: () => void;
  busy?: boolean;
}

export function NextStepBanner({ step, onAction, busy }: NextStepBannerProps) {
  const actionable = Boolean(step.actionLabel && onAction);
  return (
    <Alert
      severity={SEVERITY[step.tone]}
      variant={step.tone === "action" ? "filled" : "outlined"}
      data-testid="next-step"
      data-stage={step.stage}
      action={
        actionable ? (
          <Button
            size="small"
            color="inherit"
            variant="outlined"
            disabled={busy}
            onClick={onAction}
          >
            {step.actionLabel}
          </Button>
        ) : undefined
      }
      sx={{ alignItems: "center" }}
    >
      <AlertTitle sx={{ mb: 0.25 }}>{step.title}</AlertTitle>
      {step.detail}
      {/* A converging cluster is doing something; say so rather than leaving
          the operator to guess whether they should press anything. */}
      {step.tone === "progress" && (
        <Stack sx={{ mt: 1 }}>
          <LinearProgress />
        </Stack>
      )}
    </Alert>
  );
}
