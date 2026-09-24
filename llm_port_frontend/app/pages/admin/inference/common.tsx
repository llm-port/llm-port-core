/**
 * Shared presentation helpers for the inference screens (Phase 6, WI-5).
 *
 * The honest-degradation rule lives here: a tier the backend could not report
 * comes back as a `MetricsPartial`, and `PartialsNotice` renders it as a
 * labelled gap. Rendering it as 0 would read as "no GPUs", and rendering it as
 * an error would hide the tiers that did report.
 */
import { useState } from "react";
import i18n from "i18next";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import AlertTitle from "@mui/material/AlertTitle";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";

import type { MetricsPartial } from "~/api/inference";

export type ChipColor =
  | "success"
  | "warning"
  | "error"
  | "info"
  | "default"
  | "primary";

/** Environment status -> chip colour. */
export function environmentStatusColor(status: string): ChipColor {
  switch (status) {
    case "ready":
    case "running":
      return "success";
    case "preparing":
    case "pending":
      return "info";
    case "degraded":
      return "warning";
    case "failed":
      return "error";
    case "stopped":
      return "default";
    default:
      return "default";
  }
}

/** Deployment phase -> chip colour. */
export function deploymentPhaseColor(phase: string): ChipColor {
  switch (phase) {
    case "running":
      return "success";
    case "pending":
    case "preparing":
    case "applying":
      return "info";
    case "degraded":
      return "warning";
    case "failed":
      return "error";
    case "stopped":
    case "deleted":
      return "default";
    default:
      return "default";
  }
}

/** Endpoint status -> chip colour. */
export function endpointStatusColor(status: string): ChipColor {
  switch (status) {
    case "published":
      return "success";
    case "pending":
    case "publishing":
    case "unpublishing":
      return "info";
    case "degraded":
      return "warning";
    case "failed":
      return "error";
    default:
      return "default";
  }
}

export function formatTimestamp(iso: string | null | undefined): string {
  if (!iso) return i18n.t("inference.never");
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return iso;
  // The reader's language, not the browser's default locale.
  return parsed.toLocaleString(i18n.language || undefined);
}

/** Short id form for tables. Full ids stay available in tooltips. */
export function shortId(id: string | null | undefined): string {
  if (!id) return "-";
  return id.length > 12 ? `${id.slice(0, 8)}...` : id;
}

/**
 * Reconcile state as text.
 *
 * A row whose generation is ahead of its observed generation has a pending
 * pass; saying so is more useful than a timestamp that has not moved yet.
 */
export function reconcileSummary(row: {
  generation: number;
  observed_generation: number;
  updated_at: string;
}): string {
  // Not "pending (gen 3, observed 2)": the operator's question is whether the
  // change they just made is being acted on, not the bookkeeping behind it.
  if (row.observed_generation < row.generation) {
    return i18n.t("inference.checking_now");
  }
  return formatTimestamp(row.updated_at);
}

/**
 * The copies a deployment is asked to run.
 *
 * Read from the spec, which is what the operator changes: it is right the
 * moment Scale is applied, where the stored count waits for the reconciler.
 */
export function copiesWanted(deployment: {
  spec?: Record<string, unknown> | null;
  total_replicas: number;
}): number {
  const scale = (deployment.spec?.scale ?? {}) as {
    replicas?: number;
    autoscale?: { min_replicas?: number };
  };
  if (typeof scale.replicas === "number") return scale.replicas;
  if (typeof scale.autoscale?.min_replicas === "number") return scale.autoscale.min_replicas;
  return deployment.total_replicas;
}

/** Accelerators one copy of a deployment asks for (the spec's default is 1). */
export function acceleratorsPerCopy(deployment: { spec?: Record<string, unknown> | null }): number {
  const resources = (deployment.spec?.resources ?? {}) as { replica?: { gpus?: number } };
  const gpus = resources.replica?.gpus;
  return typeof gpus === "number" ? gpus : 1;
}

export interface PartialsNoticeProps {
  partials: MetricsPartial[];
  title?: string;
}

/**
 * Render the tiers that could not be reported, with their reasons.
 *
 * This is the "metrics degrade honestly" requirement: the operator sees which
 * tier is missing and why, next to the tiers that did report.
 *
 * Severity is taken from the partials rather than fixed at "warning". Not
 * every absence is a fault: "these counts are from the last cluster check"
 * describes the normal steady state, and painting it orange put a warning on
 * a deployment that was serving two of two replicas — the one screen that
 * says whether a deployment is healthy, contradicting itself. A notice that
 * cries wolf on every healthy page is one an operator learns to scroll past,
 * which costs them the real ones.
 */
export function PartialsNotice({
  partials,
  title,
}: PartialsNoticeProps) {
  const { t } = useTranslation();
  if (!partials.length) return null;
  // One warning is enough to make the whole notice a warning; otherwise this
  // is information.
  const severity = partials.some((p) => (p.severity ?? "warning") === "warning")
    ? "warning"
    : "info";
  const heading =
    title ?? (severity === "warning" ? t("inference.partial_metrics") : t("inference.about_figures"));
  return (
    <Alert severity={severity} variant="outlined" sx={{ mt: 1 }}>
      <AlertTitle>{heading}</AlertTitle>
      <Stack spacing={0.5}>
        {partials.map((partial, index) => (
          <Box key={`${partial.tier}-${index}`}>
            <Chip
              size="small"
              label={partial.tier}
              sx={{ mr: 1, fontFamily: "monospace" }}
            />
            <Typography variant="body2" component="span">
              {partial.reason}
            </Typography>
          </Box>
        ))}
      </Stack>
    </Alert>
  );
}

export interface LabeledValueProps {
  label: string;
  value: React.ReactNode;
  mono?: boolean;
}

export function LabeledValue({ label, value, mono }: LabeledValueProps) {
  return (
    <Box>
      <Typography variant="caption" color="text.secondary" display="block">
        {label}
      </Typography>
      <Typography
        variant="body2"
        sx={mono ? { fontFamily: "monospace", wordBreak: "break-all" } : undefined}
      >
        {value}
      </Typography>
    </Box>
  );
}

export interface JsonBlockProps {
  value: unknown;
  /** Collapsed by default: raw provider status is an advanced view. */
  label?: string;
  maxHeight?: number;
}

/** Collapsed raw JSON view for advanced provider status. */
export function JsonBlock({
  value,
  label,
  maxHeight = 420,
}: JsonBlockProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  return (
    <Box>
      <Button size="small" onClick={() => setOpen((prev) => !prev)}>
        {open ? t("inference.hide_json") : (label ?? t("inference.show_json"))}
      </Button>
      {open && (
        <Box
          component="pre"
          sx={{
            mt: 1,
            p: 1.5,
            maxHeight,
            overflow: "auto",
            bgcolor: "action.hover",
            borderRadius: 1,
            fontFamily: "monospace",
            fontSize: 12,
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
          }}
        >
          {JSON.stringify(value ?? {}, null, 2)}
        </Box>
      )}
    </Box>
  );
}

/** Narrow an opaque `observed_status` blob to a record without throwing. */
export function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

/** Narrow an opaque JSON value to an array of records. */
export function asRecordArray(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value)
    ? value.filter(
        (item): item is Record<string, unknown> =>
          !!item && typeof item === "object" && !Array.isArray(item),
      )
    : [];
}

export function asText(value: unknown): string | null {
  if (value === null || value === undefined) return null;
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return null;
}
