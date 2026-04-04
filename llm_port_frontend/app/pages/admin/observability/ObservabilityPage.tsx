/**
 * Observability Dashboard — cost, usage, and performance overview.
 *
 * Tabs: Overview (charts + summary), Requests (paginated log), Pricing (editor).
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  observability,
  type Summary,
  type TimeseriesBucket,
  type Performance,
  type PaginatedRequests,
  type PricingEntry,
} from "~/api/observability";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import Chip from "@mui/material/Chip";
import Grid from "@mui/material/Grid";
import IconButton from "@mui/material/IconButton";
import Skeleton from "@mui/material/Skeleton";
import Stack from "@mui/material/Stack";
import Tab from "@mui/material/Tab";
import Tabs from "@mui/material/Tabs";
import TextField from "@mui/material/TextField";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import { useTheme } from "@mui/material/styles";
import RefreshIcon from "@mui/icons-material/Refresh";
import DownloadIcon from "@mui/icons-material/Download";
import StatCard from "~/components/dashboard/StatCard";
import { LineChart } from "@mui/x-charts/LineChart";
import { BarChart } from "@mui/x-charts/BarChart";

import RequestsTab from "./RequestsTab";
import PricingTab from "./PricingTab";

// ── Helpers ──────────────────────────────────────────────────────────────────

function daysAgo(n: number): string {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d.toISOString();
}

function fmtCost(v: number | null | undefined): string {
  if (v == null) return "$0.00";
  return `$${Number(v).toFixed(4)}`;
}

function fmtNum(v: number | null | undefined): string {
  if (v == null) return "0";
  return v.toLocaleString();
}

function fmtMs(v: number | null | undefined): string {
  if (v == null) return "N/A";
  return `${Math.round(v)} ms`;
}

function fmtPct(v: number | null | undefined): string {
  if (v == null) return "N/A";
  return `${(v * 100).toFixed(2)}%`;
}

type RangeKey = "7d" | "14d" | "30d" | "90d";
const RANGES: Record<RangeKey, number> = {
  "7d": 7,
  "14d": 14,
  "30d": 30,
  "90d": 90,
};

// ── Component ────────────────────────────────────────────────────────────────

export default function ObservabilityPage() {
  const { t } = useTranslation();
  const theme = useTheme();
  const [tab, setTab] = useState(0);
  const [range, setRange] = useState<RangeKey>("7d");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [summary, setSummary] = useState<Summary | null>(null);
  const [timeseries, setTimeseries] = useState<TimeseriesBucket[]>([]);
  const [perf, setPerf] = useState<Performance | null>(null);

  const start = useMemo(() => daysAgo(RANGES[range]), [range]);
  const end = useMemo(() => new Date().toISOString(), []);

  const loadOverview = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [s, ts, p] = await Promise.all([
        observability.summary(start, end),
        observability.timeseries(
          start,
          end,
          RANGES[range] <= 7 ? "hour" : "day",
        ),
        observability.performance(start, end),
      ]);
      setSummary(s);
      setTimeseries(ts);
      setPerf(p);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load data");
    } finally {
      setLoading(false);
    }
  }, [start, end, range]);

  useEffect(() => {
    loadOverview();
  }, [loadOverview]);

  // ── Overview tab ─────────────────────────────────────────────────

  const overviewContent = (
    <>
      {/* Summary cards */}
      <Grid container spacing={2} sx={{ mb: 3 }}>
        <Grid size={{ xs: 6, sm: 4, md: 2.4 }}>
          {loading ? (
            <Skeleton variant="rounded" height={100} />
          ) : (
            <StatCard
              label={t("observability.estimated_spend")}
              value={fmtCost(summary?.estimated_total_cost)}
            />
          )}
        </Grid>
        <Grid size={{ xs: 6, sm: 4, md: 2.4 }}>
          {loading ? (
            <Skeleton variant="rounded" height={100} />
          ) : (
            <StatCard
              label={t("observability.total_requests")}
              value={fmtNum(summary?.total_requests)}
            />
          )}
        </Grid>
        <Grid size={{ xs: 6, sm: 4, md: 2.4 }}>
          {loading ? (
            <Skeleton variant="rounded" height={100} />
          ) : (
            <StatCard
              label={t("observability.total_tokens")}
              value={fmtNum(summary?.total_tokens)}
            />
          )}
        </Grid>
        <Grid size={{ xs: 6, sm: 4, md: 2.4 }}>
          {loading ? (
            <Skeleton variant="rounded" height={100} />
          ) : (
            <StatCard
              label={t("observability.avg_latency")}
              value={fmtMs(perf?.avg_latency_ms)}
            />
          )}
        </Grid>
        <Grid size={{ xs: 6, sm: 4, md: 2.4 }}>
          {loading ? (
            <Skeleton variant="rounded" height={100} />
          ) : (
            <StatCard
              label={t("observability.error_rate")}
              value={fmtPct(perf?.error_rate)}
            />
          )}
        </Grid>
      </Grid>

      {/* Charts */}
      <Grid container spacing={2} sx={{ mb: 3 }}>
        {/* Cost over time */}
        <Grid size={{ xs: 12, md: 6 }}>
          <Card>
            <CardContent>
              <Typography variant="subtitle2" gutterBottom>
                {t("observability.cost_over_time")}
              </Typography>
              {loading ? (
                <Skeleton variant="rounded" height={250} />
              ) : timeseries.length === 0 ? (
                <Typography
                  color="text.secondary"
                  sx={{ py: 8, textAlign: "center" }}
                >
                  {t("observability.no_data")}
                </Typography>
              ) : (
                <LineChart
                  height={250}
                  xAxis={[
                    {
                      data: timeseries.map((b) => new Date(b.bucket)),
                      scaleType: "time",
                    },
                  ]}
                  series={[
                    {
                      data: timeseries.map(
                        (b) => Number(b.estimated_total_cost) || 0,
                      ),
                      label: "Cost ($)",
                      color: theme.palette.primary.main,
                      area: true,
                    },
                  ]}
                />
              )}
            </CardContent>
          </Card>
        </Grid>

        {/* Request throughput */}
        <Grid size={{ xs: 12, md: 6 }}>
          <Card>
            <CardContent>
              <Typography variant="subtitle2" gutterBottom>
                {t("observability.request_throughput")}
              </Typography>
              {loading ? (
                <Skeleton variant="rounded" height={250} />
              ) : timeseries.length === 0 ? (
                <Typography
                  color="text.secondary"
                  sx={{ py: 8, textAlign: "center" }}
                >
                  {t("observability.no_data")}
                </Typography>
              ) : (
                <LineChart
                  height={250}
                  xAxis={[
                    {
                      data: timeseries.map((b) => new Date(b.bucket)),
                      scaleType: "time",
                    },
                  ]}
                  series={[
                    {
                      data: timeseries.map((b) => b.total_requests),
                      label: "Requests",
                      color: theme.palette.secondary.main,
                    },
                  ]}
                />
              )}
            </CardContent>
          </Card>
        </Grid>

        {/* Latency percentiles */}
        <Grid size={{ xs: 12, md: 6 }}>
          <Card>
            <CardContent>
              <Typography variant="subtitle2" gutterBottom>
                {t("observability.latency_percentiles")}
              </Typography>
              {loading || !perf ? (
                <Skeleton variant="rounded" height={200} />
              ) : (
                <BarChart
                  height={200}
                  xAxis={[{ data: ["p50", "p95", "p99"], scaleType: "band" }]}
                  series={[
                    {
                      data: [
                        perf.p50_latency_ms ?? 0,
                        perf.p95_latency_ms ?? 0,
                        perf.p99_latency_ms ?? 0,
                      ],
                      label: "Latency (ms)",
                      color: theme.palette.warning.main,
                    },
                  ]}
                />
              )}
            </CardContent>
          </Card>
        </Grid>

        {/* Spend by model */}
        <Grid size={{ xs: 12, md: 6 }}>
          <Card>
            <CardContent>
              <Typography variant="subtitle2" gutterBottom>
                {t("observability.spend_by_model")}
              </Typography>
              {loading || !summary ? (
                <Skeleton variant="rounded" height={200} />
              ) : summary.by_model.length === 0 ? (
                <Typography
                  color="text.secondary"
                  sx={{ py: 6, textAlign: "center" }}
                >
                  {t("observability.no_data_short")}
                </Typography>
              ) : (
                <BarChart
                  height={200}
                  xAxis={[
                    {
                      data: summary.by_model
                        .slice(0, 10)
                        .map((m) => m.model_alias),
                      scaleType: "band",
                    },
                  ]}
                  series={[
                    {
                      data: summary.by_model
                        .slice(0, 10)
                        .map((m) => Number(m.estimated_total_cost) || 0),
                      label: "Cost ($)",
                      color: theme.palette.info.main,
                    },
                  ]}
                />
              )}
            </CardContent>
          </Card>
        </Grid>
      </Grid>

      {/* Top users table */}
      {summary && summary.top_users.length > 0 && (
        <Card sx={{ mb: 3 }}>
          <CardContent>
            <Typography variant="subtitle2" gutterBottom>
              {t("observability.top_users")}
            </Typography>
            <Box sx={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse" }}>
                <thead>
                  <tr>
                    <th style={{ textAlign: "left", padding: 8 }}>
                      {t("observability.col_user")}
                    </th>
                    <th style={{ textAlign: "right", padding: 8 }}>
                      {t("observability.col_requests")}
                    </th>
                    <th style={{ textAlign: "right", padding: 8 }}>
                      {t("observability.col_tokens")}
                    </th>
                    <th style={{ textAlign: "right", padding: 8 }}>
                      {t("observability.col_est_cost")}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {summary.top_users.slice(0, 10).map((u) => (
                    <tr key={u.user_id}>
                      <td style={{ padding: 8 }}>{u.user_id}</td>
                      <td style={{ textAlign: "right", padding: 8 }}>
                        {fmtNum(u.total_requests)}
                      </td>
                      <td style={{ textAlign: "right", padding: 8 }}>
                        {fmtNum(u.total_tokens)}
                      </td>
                      <td style={{ textAlign: "right", padding: 8 }}>
                        {fmtCost(u.estimated_total_cost)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Box>
          </CardContent>
        </Card>
      )}
    </>
  );

  return (
    <Box sx={{ p: { xs: 1, sm: 2 } }}>
      {/* Header */}
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="space-between"
        sx={{ mb: 2 }}
      >
        <Typography variant="h5">{t("observability.title")}</Typography>
        <Stack direction="row" spacing={1} alignItems="center">
          {/* Range selector */}
          {(Object.keys(RANGES) as RangeKey[]).map((k) => (
            <Chip
              key={k}
              label={k}
              size="small"
              variant={range === k ? "filled" : "outlined"}
              color={range === k ? "primary" : "default"}
              onClick={() => setRange(k)}
            />
          ))}
          <Tooltip title={t("observability.refresh")}>
            <IconButton onClick={loadOverview} size="small">
              <RefreshIcon />
            </IconButton>
          </Tooltip>
          <Tooltip title={t("observability.export_csv")}>
            <IconButton
              size="small"
              component="a"
              href={observability.exportCsvUrl(start, end)}
              target="_blank"
            >
              <DownloadIcon />
            </IconButton>
          </Tooltip>
        </Stack>
      </Stack>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}

      {/* Tabs */}
      <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ mb: 2 }}>
        <Tab label={t("observability.tab_overview")} />
        <Tab label={t("observability.tab_requests")} />
        <Tab label={t("observability.tab_pricing")} />
      </Tabs>

      {tab === 0 && overviewContent}
      {tab === 1 && <RequestsTab start={start} end={end} />}
      {tab === 2 && <PricingTab />}
    </Box>
  );
}
