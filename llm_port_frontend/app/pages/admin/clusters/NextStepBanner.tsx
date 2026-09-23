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
import Typography from "@mui/material/Typography";

import type { NextStep, ReadinessTone } from "./readiness";

const SEVERITY: Record<ReadinessTone, "info" | "success" | "warning"> = {
  action: "info",
  progress: "info",
  warning: "warning",
  success: "success",
};

/** One machine's line in a multi-machine transfer. */
export interface ProgressRow {
  label: string;
  pct: number | null;
  message: string;
}

export interface NextStepBannerProps {
  step: NextStep;
  onAction?: () => void;
  busy?: boolean;
  /** Per-machine progress; drawn as the Jobs page draws a model download. */
  rows?: ProgressRow[];
}

/**
 * A bar with its percentage beside it, as the Jobs page shows a model
 * download: thick enough to read at a glance, with the number next to it so
 * nobody has to estimate the fill.
 */
function PercentBar({ pct }: { pct: number | null }) {
  return (
    <Stack direction="row" spacing={1} alignItems="center" sx={{ minWidth: 140 }}>
      {typeof pct === "number" ? (
        <LinearProgress
          variant="determinate"
          value={pct}
          sx={{ flexGrow: 1, height: 8, borderRadius: 4 }}
        />
      ) : (
        <LinearProgress sx={{ flexGrow: 1, height: 8, borderRadius: 4 }} />
      )}
      <Typography variant="caption" color="text.secondary" sx={{ minWidth: 36 }}>
        {typeof pct === "number" ? `${Math.round(pct)}%` : "—"}
      </Typography>
    </Stack>
  );
}

export function NextStepBanner({ step, onAction, busy, rows }: NextStepBannerProps) {
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
      {step.tone === "progress" &&
        (rows && rows.length > 0 ? (
          // One line per machine: a two-machine cluster receives its image on
          // both at once, and each moves at its own speed.
          <Stack spacing={1} sx={{ mt: 1 }} data-testid="machine-progress">
            {rows.map((row) => (
              <Stack key={row.label} spacing={0.25}>
                <Typography variant="caption" sx={{ fontWeight: 600 }}>
                  {row.label}
                </Typography>
                <PercentBar pct={row.pct} />
                <Typography variant="caption" color="text.secondary">
                  {row.message}
                </Typography>
              </Stack>
            ))}
          </Stack>
        ) : (
          <Stack sx={{ mt: 1 }}>
            <PercentBar pct={step.progressPct ?? null} />
          </Stack>
        ))}
    </Alert>
  );
}
