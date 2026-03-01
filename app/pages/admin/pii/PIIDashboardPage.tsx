/**
 * PII Dashboard — aggregate statistics about PII processing.
 *
 * Shows total scans, detection rate, entity-type breakdown, daily volume,
 * and operation/source distribution.  **No raw PII data is displayed.**
 */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { fetchPIIStats, type PIIStats } from "~/api/pii";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import CircularProgress from "@mui/material/CircularProgress";
import Grid from "@mui/material/Grid";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import { useTheme } from "@mui/material/styles";

import { BarChart } from "@mui/x-charts/BarChart";
import { PieChart } from "@mui/x-charts/PieChart";
import { LineChart } from "@mui/x-charts/LineChart";

/* ── Stat card ─────────────────────────────────────────────────────── */

interface StatCardProps {
  label: string;
  value: string | number;
  detail?: string;
  color?: string;
}

function StatCard({ label, value, detail, color }: StatCardProps) {
  return (
    <Card variant="outlined" sx={{ height: "100%" }}>
      <CardContent>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 0.5 }}>
          {label}
        </Typography>
        <Typography variant="h5" sx={{ fontWeight: 700, color: color ?? "inherit" }}>
          {value}
        </Typography>
        {detail && (
          <Typography variant="caption" color="text.secondary">
            {detail}
          </Typography>
        )}
      </CardContent>
    </Card>
  );
}

/* ── Main component ────────────────────────────────────────────────── */

export default function PIIDashboardPage() {
  const { t } = useTranslation();
  const theme = useTheme();
  const [stats, setStats] = useState<PIIStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchPIIStats()
      .then((d) => { if (!cancelled) setStats(d); })
      .catch((e) => { if (!cancelled) setError(e.message); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  if (loading) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", mt: 8 }}>
        <CircularProgress />
      </Box>
    );
  }

  if (error) {
    return <Alert severity="error" sx={{ m: 2 }}>{error}</Alert>;
  }

  if (!stats) return null;

  /* ── Derived chart data ──────────────────────────────────────────── */

  const entityPieData = Object.entries(stats.entity_type_breakdown)
    .sort((a, b) => b[1] - a[1])
    .map(([label, value], i) => ({ id: i, value, label }));

  const opBarLabels = Object.keys(stats.operation_breakdown);
  const opBarValues = Object.values(stats.operation_breakdown);

  const srcBarLabels = Object.keys(stats.source_breakdown);
  const srcBarValues = Object.values(stats.source_breakdown);

  const dailyDates = stats.daily_volume.map((d) => d.date);
  const dailyCounts = stats.daily_volume.map((d) => d.count);
  const dailyPII = stats.daily_volume.map((d) => d.pii_count);

  const detectionPct = (stats.detection_rate * 100).toFixed(1);

  return (
    <Box sx={{ p: 0 }}>
      {/* ── Top-line KPI cards ──────────────────────────────────────── */}
      <Grid container spacing={2} sx={{ mb: 3 }}>
        <Grid size={{ xs: 6, sm: 3 }}>
          <StatCard
            label={t("pii_dashboard.total_scans", "Total Scans")}
            value={stats.total_scans.toLocaleString()}
          />
        </Grid>
        <Grid size={{ xs: 6, sm: 3 }}>
          <StatCard
            label={t("pii_dashboard.with_pii", "With PII Detected")}
            value={stats.total_with_pii.toLocaleString()}
            color={stats.total_with_pii > 0 ? theme.palette.warning.main : undefined}
          />
        </Grid>
        <Grid size={{ xs: 6, sm: 3 }}>
          <StatCard
            label={t("pii_dashboard.total_entities", "Entities Found")}
            value={stats.total_entities.toLocaleString()}
          />
        </Grid>
        <Grid size={{ xs: 6, sm: 3 }}>
          <StatCard
            label={t("pii_dashboard.detection_rate", "Detection Rate")}
            value={`${detectionPct}%`}
            color={Number(detectionPct) > 50 ? theme.palette.error.main : undefined}
          />
        </Grid>
      </Grid>

      {/* ── Daily volume line chart ────────────────────────────────── */}
      {stats.daily_volume.length > 0 && (
        <Card variant="outlined" sx={{ mb: 3 }}>
          <CardContent>
            <Typography variant="subtitle1" sx={{ mb: 1, fontWeight: 600 }}>
              {t("pii_dashboard.daily_volume", "Daily Scan Volume")}
            </Typography>
            <LineChart
              xAxis={[{ scaleType: "band", data: dailyDates, label: "Date" }]}
              series={[
                { data: dailyCounts, label: t("pii_dashboard.total", "Total"), color: theme.palette.primary.main },
                { data: dailyPII, label: t("pii_dashboard.pii_detected_label", "PII Detected"), color: theme.palette.warning.main },
              ]}
              height={280}
            />
          </CardContent>
        </Card>
      )}

      {/* ── Entity-type breakdown + operation/source bars ──────────── */}
      <Grid container spacing={2}>
        {/* Pie chart: entity types */}
        {entityPieData.length > 0 && (
          <Grid size={{ xs: 12, md: 6 }}>
            <Card variant="outlined" sx={{ height: "100%" }}>
              <CardContent>
                <Typography variant="subtitle1" sx={{ mb: 1, fontWeight: 600 }}>
                  {t("pii_dashboard.entity_breakdown", "Entity Type Breakdown")}
                </Typography>
                <PieChart
                  series={[
                    {
                      data: entityPieData,
                      innerRadius: 40,
                      highlightScope: { fade: "global", highlight: "item" },
                    },
                  ]}
                  height={280}
                />
              </CardContent>
            </Card>
          </Grid>
        )}

        {/* Bar chart: operations */}
        {opBarLabels.length > 0 && (
          <Grid size={{ xs: 12, md: 3 }}>
            <Card variant="outlined" sx={{ height: "100%" }}>
              <CardContent>
                <Typography variant="subtitle1" sx={{ mb: 1, fontWeight: 600 }}>
                  {t("pii_dashboard.by_operation", "By Operation")}
                </Typography>
                <BarChart
                  xAxis={[{ scaleType: "band", data: opBarLabels }]}
                  series={[{ data: opBarValues, color: theme.palette.info.main }]}
                  height={240}
                />
              </CardContent>
            </Card>
          </Grid>
        )}

        {/* Bar chart: source */}
        {srcBarLabels.length > 0 && (
          <Grid size={{ xs: 12, md: 3 }}>
            <Card variant="outlined" sx={{ height: "100%" }}>
              <CardContent>
                <Typography variant="subtitle1" sx={{ mb: 1, fontWeight: 600 }}>
                  {t("pii_dashboard.by_source", "By Source")}
                </Typography>
                <BarChart
                  xAxis={[{ scaleType: "band", data: srcBarLabels }]}
                  series={[{ data: srcBarValues, color: theme.palette.secondary.main }]}
                  height={240}
                />
              </CardContent>
            </Card>
          </Grid>
        )}
      </Grid>

      {/* ── Empty state ────────────────────────────────────────────── */}
      {stats.total_scans === 0 && (
        <Alert severity="info" sx={{ mt: 3 }}>
          {t("pii_dashboard.no_data", "No PII scan data yet. Events will appear as the gateway processes requests with PII policies enabled.")}
        </Alert>
      )}
    </Box>
  );
}
