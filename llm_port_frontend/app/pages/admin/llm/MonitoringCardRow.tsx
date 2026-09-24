/**
 * The engine's live figures for a provider, as a row of stat cards.
 *
 * Works for both kinds of provider, and deliberately looks the same for each:
 *
 * - a **local runtime**, scraped at its own `/metrics`;
 * - a **cluster-backed** provider, whose replicas run across machines and are
 *   labelled with the environment's name.
 *
 * It used to take a `Runtime`, which meant a cluster-backed provider got a
 * link to its deployment instead of any figures. That is a dead end for
 * somebody whose role reaches this page and not that one, and it makes the
 * simple question — is the hardware working — need two screens. Values come
 * from `GET /providers/{id}/monitoring-stats`, which resolves the difference
 * on the backend so this component has no branch in it.
 *
 * Semantics (keep-on-stop): a stopped or crashed workload still shows the row
 * in a muted "no data" state — its dashboard and scrape target are retained
 * on purpose so history stays browsable (up=0).
 *
 * The cards themselves are {@link StatCardRow}, shared with the deployment
 * page so the two screens describe the same cluster the same way.
 */
import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { providers, type Provider, type StatKey } from "~/api/llm";
import { useAsyncData } from "~/lib/useAsyncData";
import { StatCardRow, type StatCardItem } from "~/components/StatCardRow";

import Button from "@mui/material/Button";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";

import MonitorIcon from "@mui/icons-material/Monitor";
import OpenInNewIcon from "@mui/icons-material/OpenInNew";

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
  {
    key: "running_requests",
    i18nKey: "llm_monitoring.stat.running_requests",
    format: fmtInt,
  },
  {
    key: "waiting_requests",
    i18nKey: "llm_monitoring.stat.waiting_requests",
    format: fmtInt,
  },
  {
    key: "kv_cache_usage",
    i18nKey: "llm_monitoring.stat.kv_cache_usage",
    format: fmtPct,
  },
  {
    key: "prefix_cache_hit_rate",
    i18nKey: "llm_monitoring.stat.prefix_cache_hit_rate",
    format: fmtPct,
  },
  {
    key: "mtp_acceptance",
    i18nKey: "llm_monitoring.stat.mtp_acceptance",
    format: fmtPct,
  },
  {
    key: "generation_tokens_per_sec",
    i18nKey: "llm_monitoring.stat.generation_tokens_per_sec",
    format: fmtFixed1,
  },
  {
    key: "preemption_rate",
    i18nKey: "llm_monitoring.stat.preemption_rate",
    format: fmtFixed1,
  },
];

export interface MonitoringCardRowProps {
  provider: Provider;
  /**
   * Whether this provider is monitored at all.
   *
   * For a local runtime the list endpoint already says so, and passing it
   * avoids a request for a provider that has nothing to report. For a
   * cluster-backed one there is no runtime to ask, so the answer comes from
   * the fetch itself and this defaults to true.
   */
  known?: boolean;
  /**
   * Called when the operator wants the deployment behind a cluster-backed
   * provider. The cards answer "is it working"; this is for everything else
   * — copies, logs, scaling — which lives on the deployment and always will.
   */
  onOpenOwner?: () => void;
}

export default function MonitoringCardRow({
  provider,
  known = true,
  onOpenOwner,
}: MonitoringCardRowProps) {
  const { t } = useTranslation();

  // One request whatever is serving: the backend resolves a local runtime or
  // a cluster's environment behind the same route, so there is no branch here.
  const statsReq = useAsyncData(
    async () => (known ? providers.monitoringStats(provider.id) : null),
    [provider.id, known],
    {
      initialValue: null as Awaited<
        ReturnType<typeof providers.monitoringStats>
      > | null,
    },
  );

  const enabled = statsReq.data?.enabled ?? known;
  const dashboardUrl = statsReq.data?.dashboard_url ?? null;

  // Light polling so the cards track a live engine. A short interval keeps
  // the providers view responsive without hammering the proxy.
  useEffect(() => {
    if (!known) return;
    const id = window.setInterval(() => {
      void statsReq.refresh();
    }, 30_000);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [known, provider.id]);

  const values = statsReq.data?.stats ?? {};
  // Until the first answer arrives nothing is known, which is not the same as
  // stale; the skeleton covers that window.
  const liveStale = statsReq.data ? statsReq.data.stale : true;

  if (!enabled) return null;

  const openDashboard = () => {
    if (dashboardUrl)
      window.open(dashboardUrl, "_blank", "noopener,noreferrer");
  };

  const cards: StatCardItem[] = STAT_DESCRIPTORS.map((d) => {
    const raw = values[d.key];
    return {
      key: d.key,
      label: t(d.i18nKey),
      value: raw != null ? d.format(raw) : null,
      emptyHint: t(
        liveStale ? "llm_monitoring.stale_hint" : "llm_monitoring.no_data_hint",
      ),
    };
  });

  return (
    <StatCardRow
      cards={cards}
      loading={statsReq.loading}
      emptyLabel={t("llm_monitoring.no_data")}
      sx={{
        cursor: dashboardUrl ? "pointer" : "default",
        "&:hover": dashboardUrl ? { bgcolor: "action.selected" } : undefined,
      }}
      header={
        <Stack
          direction="row"
          alignItems="center"
          spacing={0.75}
          sx={{ color: "text.secondary" }}
          // The row opens Grafana; the buttons inside it stop propagation.
          onClick={openDashboard}
          role={dashboardUrl ? "button" : undefined}
          // Without an explicit label the accessible name of this button is
          // its entire contents — every card, every value — which is what a
          // screen reader would read out.
          aria-label={dashboardUrl ? t("llm_monitoring.dashboard") : undefined}
          title={dashboardUrl ? t("llm_monitoring.section_hint") : undefined}
        >
          <MonitorIcon sx={{ fontSize: 16 }} />
          <Typography variant="caption" sx={{ fontWeight: 600 }}>
            {t("llm_monitoring.section")}
          </Typography>
          {/* The way through to the deployment. The cards answer whether the
              hardware is working; copies, logs and scaling live on the
              deployment, and an operator reading these numbers is exactly who
              wants to go there next. */}
          {provider.managed_by && onOpenOwner && (
            <Button
              size="small"
              variant="text"
              startIcon={<OpenInNewIcon sx={{ fontSize: 14 }} />}
              sx={{ py: 0, minHeight: 0, textTransform: "none" }}
              onClick={(event) => {
                event.stopPropagation();
                onOpenOwner();
              }}
            >
              {t("llm_monitoring.open_deployment")}
            </Button>
          )}
          {dashboardUrl && (
            <Typography
              variant="caption"
              color="text.disabled"
              sx={{
                ml: "auto",
                display: "flex",
                alignItems: "center",
                gap: 0.5,
              }}
            >
              {t("llm_monitoring.dashboard")}
            </Typography>
          )}
        </Stack>
      }
    />
  );
}
