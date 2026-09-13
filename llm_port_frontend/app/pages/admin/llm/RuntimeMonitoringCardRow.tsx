/**
 * RuntimeMonitoringCardRow — a compact row of "stat cards" for a single
 * runtime's vLLM engine, rendered on the Providers page.
 *
 * - Visible only when the runtime is a scraped vLLM workload
 *   (`runtime.monitoring` is non-null, set by the backend list endpoint).
 * - Values are fetched lazily via `GET /runtimes/{id}/monitoring-stats`
 *   so the providers table itself stays a single DB round-trip.
 * - Clicking the row (or any card) opens the runtime's full Grafana
 *   dashboard in a new tab.
 *
 * Semantics (keep-on-stop): a stopped/crashed runtime still shows the
 * row but in a muted "no data" state — its dashboard + scrape target
 * are intentionally retained so history stays browsable (up=0).
 */
import { useEffect, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { runtimes, type Runtime, type StatKey } from "~/api/llm";
import { useAsyncData } from "~/lib/useAsyncData";

import Box from "@mui/material/Box";
import Card from "@mui/material/Card";
import Skeleton from "@mui/material/Skeleton";
import Stack from "@mui/material/Stack";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";

import MonitorIcon from "@mui/icons-material/Monitor";

interface StatDescriptor {
  key: StatKey;
  i18nKey: string;
  /** How to format a numeric value. */
  format: (v: number) => string;
}

const fmtInt = (v: number) => Math.round(v).toString();
const fmtFixed1 = (v: number) => v.toFixed(1);
const fmtPct = (v: number) => `${v.toFixed(1)}%`;

const STAT_DESCRIPTORS: StatDescriptor[] = [
  { key: "running_requests", i18nKey: "llm_monitoring.stat.running_requests", format: fmtInt },
  { key: "waiting_requests", i18nKey: "llm_monitoring.stat.waiting_requests", format: fmtInt },
  { key: "kv_cache_usage", i18nKey: "llm_monitoring.stat.kv_cache_usage", format: fmtPct },
  { key: "prefix_cache_hit_rate", i18nKey: "llm_monitoring.stat.prefix_cache_hit_rate", format: fmtPct },
  { key: "mtp_acceptance", i18nKey: "llm_monitoring.stat.mtp_acceptance", format: fmtPct },
  { key: "generation_tokens_per_sec", i18nKey: "llm_monitoring.stat.generation_tokens_per_sec", format: fmtFixed1 },
  { key: "preemption_rate", i18nKey: "llm_monitoring.stat.preemption_rate", format: fmtFixed1 },
];

export interface RuntimeMonitoringCardRowProps {
  runtime: Runtime;
}

export default function RuntimeMonitoringCardRow({
  runtime,
}: RuntimeMonitoringCardRowProps) {
  const { t } = useTranslation();

  // Non-vLLM runtimes have no monitoring summary → render nothing at all.
  const monitoring = runtime.monitoring;
  const enabled = monitoring?.enabled ?? false;
  const stale = monitoring?.stale ?? true;
  const dashboardUrl = monitoring?.dashboard_url ?? null;

  // Fetch live stat values only when this runtime is scraped. We also poll
  // lightly so the cards track a live engine; a short interval keeps the
  // providers view responsive without hammering the proxy.
  const statsReq = useAsyncData(
    async () => (enabled ? runtimes.monitoringStats(runtime.id) : null),
    [runtime.id, enabled],
    { initialValue: null as Awaited<ReturnType<typeof runtimes.monitoringStats>> | null },
  );

  // Light polling so the cards track a live engine without touching the
  // shared useAsyncData hook. Skipped when monitoring is disabled.
  useEffect(() => {
    if (!enabled) return;
    const id = window.setInterval(() => {
      void statsReq.refresh();
    }, 30_000);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, runtime.id]);

  const values = statsReq.data?.stats ?? {};
  const liveStale = statsReq.data ? statsReq.data.stale : stale;
  const busy = statsReq.loading;

  const anyValue = useMemo(
    () => Object.values(values).some((v) => v != null),
    [values],
  );

  if (!enabled) return null;

  const openDashboard = () => {
    if (dashboardUrl) window.open(dashboardUrl, "_blank", "noopener,noreferrer");
  };

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        gap: 1,
        px: 1,
        py: 0.5,
        mt: 1,
        borderRadius: 2,
        bgcolor: "action.hover",
        border: "1px solid",
        borderColor: "divider",
        cursor: dashboardUrl ? "pointer" : "default",
        "&:hover": dashboardUrl ? { bgcolor: "action.selected" } : undefined,
      }}
      onClick={openDashboard}
      role={dashboardUrl ? "button" : undefined}
      title={dashboardUrl ? t("llm_monitoring.section_hint") : undefined}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.75}
        sx={{ color: "text.secondary" }}
      >
        <MonitorIcon sx={{ fontSize: 16 }} />
        <Typography variant="caption" sx={{ fontWeight: 600 }}>
          {t("llm_monitoring.section")}
        </Typography>
        {dashboardUrl && (
          <Typography
            variant="caption"
            color="text.disabled"
            sx={{ ml: "auto", display: "flex", alignItems: "center", gap: 0.5 }}
          >
            {t("llm_monitoring.dashboard")}
          </Typography>
        )}
      </Stack>

      <Stack direction="row" flexWrap="wrap" spacing={1}>
        {STAT_DESCRIPTORS.map((d) => {
          const raw = values[d.key];
          const hasValue = raw != null;
          const cardStale = liveStale && !hasValue;
          const display = hasValue ? d.format(raw) : t("llm_monitoring.no_data");
          return (
            <Tooltip
              key={d.key}
              title={
                hasValue
                  ? undefined
                  : t(
                      liveStale
                        ? "llm_monitoring.stale_hint"
                        : "llm_monitoring.no_data_hint",
                    )
              }
              enterDelay={500}
            >
              <Card
                variant="outlined"
                sx={{
                  minWidth: 96,
                  flex: "1 1 96px",
                  opacity: hasValue ? 1 : 0.6,
                  "&:hover": {
                    bgcolor: "action.hover",
                    borderColor: "primary.main",
                  },
                }}
              >
                <Box sx={{ px: 1.25, py: 1 }}>
                  <Typography
                    variant="caption"
                    color="text.secondary"
                    sx={{ display: "block", mb: 0.25 }}
                  >
                    {t(d.i18nKey)}
                  </Typography>
                  {busy && !hasValue ? (
                    <Skeleton width={56} height={22} />
                  ) : (
                    <Typography
                      variant="subtitle1"
                      sx={{
                        fontWeight: 700,
                        fontSize: "1.05rem",
                        color: hasValue ? "text.primary" : "text.disabled",
                        fontVariantNumeric: "tabular-nums",
                      }}
                    >
                      {display}
                    </Typography>
                  )}
                </Box>
              </Card>
            </Tooltip>
          );
        })}
      </Stack>
    </Box>
  );
}
