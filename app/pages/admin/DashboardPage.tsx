import { useEffect, useMemo, useState } from "react";
import { Link as RouterLink } from "react-router";
import { useTranslation } from "react-i18next";
import {
  dashboard,
  type DashboardHealth,
  type DashboardOverview,
} from "~/api/admin";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import Grid from "@mui/material/Grid";
import List from "@mui/material/List";
import ListItem from "@mui/material/ListItem";
import ListItemText from "@mui/material/ListItemText";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import { useTheme } from "@mui/material/styles";
import GaugeCard from "~/components/GaugeCard";

function fmtPct(value: number | null | undefined): string {
  if (value == null) return "N/A";
  return `${value.toFixed(1)}%`;
}

function fmtBytes(value: number | null | undefined): string {
  if (value == null) return "N/A";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let n = value;
  let i = 0;
  while (n >= 1024 && i < units.length - 1) {
    n /= 1024;
    i += 1;
  }
  return `${n.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

function fmtRatio(used: number | null | undefined, total: number | null | undefined): string {
  if (used == null || total == null || total <= 0) return "N/A";
  return `${fmtBytes(used)} / ${fmtBytes(total)}`;
}

function healthColor(status: string): "success" | "warning" | "error" | "default" {
  const value = status.toLowerCase();
  if (value === "up" || value === "healthy") return "success";
  if (value === "degraded" || value === "unknown") return "warning";
  if (value === "down") return "error";
  return "default";
}

function systemBadgeColor(badge: string): "success" | "warning" | "error" | "default" {
  if (badge === "green") return "success";
  if (badge === "yellow") return "warning";
  if (badge === "red") return "error";
  return "default";
}

interface StatCardProps {
  label: string;
  value: string;
  detail?: string;
}

function StatCard({ label, value, detail }: StatCardProps) {
  return (
    <Card variant="outlined" sx={{ height: "100%" }}>
      <CardContent>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 0.5 }}>
          {label}
        </Typography>
        <Typography variant="h6" sx={{ fontWeight: 700 }}>
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

export default function DashboardPage() {
  const { t } = useTranslation();
  const theme = useTheme();
  const grafanaDashboardBaseUrl =
    (import.meta.env.VITE_GRAFANA_DASHBOARD_URL as string | undefined) ??
    "http://localhost:3001/d/llm-port-overview/llm-port-overview?orgId=1&from=now-6h&to=now&timezone=browser&refresh=30s";

  const grafanaDashboardUrl = useMemo(() => {
    try {
      const url = new URL(grafanaDashboardBaseUrl);
      url.searchParams.set("theme", theme.palette.mode === "dark" ? "dark" : "light");
      return url.toString();
    } catch {
      const separator = grafanaDashboardBaseUrl.includes("?") ? "&" : "?";
      return `${grafanaDashboardBaseUrl}${separator}theme=${theme.palette.mode === "dark" ? "dark" : "light"}`;
    }
  }, [grafanaDashboardBaseUrl, theme.palette.mode]);

  const [overview, setOverview] = useState<DashboardOverview | null>(null);
  const [health, setHealth] = useState<DashboardHealth | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const [o, h] = await Promise.all([
        dashboard.overview(),
        dashboard.health(),
      ]);
      setOverview(o);
      setHealth(h);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t("dashboard.failed_load"));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  /* Remaining flat stat cards (non-gauge metrics) */
  const cards = useMemo(() => {
    if (!overview) return [];
    return [
      {
        label: t("dashboard.cards.network_rx_tx"),
        value: `${fmtBytes(overview.network_rx_bytes)} / ${fmtBytes(overview.network_tx_bytes)}`,
      },
      {
        label: t("dashboard.cards.containers"),
        value: `${overview.containers_running} / ${overview.containers_total}`,
        detail: t("dashboard.cards.restarting", { count: overview.containers_restarting }),
      },
      {
        label: t("dashboard.cards.restarts"),
        value: `${overview.restart_rate_1h} / ${overview.restart_rate_24h}`,
      },
      {
        label: t("dashboard.cards.api_5xx"),
        value: fmtPct(overview.api_error_rate_5xx),
      },
      {
        label: t("dashboard.cards.postgres_connections"),
        value: overview.postgres_connections == null ? "N/A" : String(overview.postgres_connections),
        detail:
          overview.postgres_max_connections == null
            ? undefined
            : t("dashboard.cards.max", { count: overview.postgres_max_connections }),
      },
    ];
  }, [overview, t]);

  /** RAM usage as a 0-100 percentage */
  const ramPercent = useMemo(() => {
    if (!overview?.ram_used_bytes || !overview?.ram_total_bytes || overview.ram_total_bytes <= 0) return null;
    return (overview.ram_used_bytes / overview.ram_total_bytes) * 100;
  }, [overview]);

  /** Disk usage (inverse of free) as 0-100 */
  const diskUsedPercent = useMemo(() => {
    if (overview?.disk_free_percent == null) return null;
    return 100 - overview.disk_free_percent;
  }, [overview]);

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        gap: 2,
        flexShrink: 0,
        pr: 0.5,
      }}
    >
      <Stack direction="row" justifyContent="space-between" alignItems="center" flexWrap="wrap" gap={1}>
        <Typography variant="h5">{t("dashboard.title")}</Typography>
        <Stack direction="row" spacing={1} alignItems="center">
          {loading && <CircularProgress size={20} />}
          <Button variant="outlined" onClick={load} disabled={loading}>{t("dashboard.refresh")}</Button>
        </Stack>
      </Stack>

      {error && <Alert severity="error">{error}</Alert>}

      {overview ? (
        <Box>
          <Stack direction="row" spacing={1} sx={{ mb: 1 }}>
            <Chip
              label={t("dashboard.system", { status: overview.system_status })}
              color={systemBadgeColor(overview.system_badge)}
              size="small"
            />
            {health && (
              <Chip
                label={t("dashboard.dependencies", { status: health.overall_status })}
                color={healthColor(health.overall_status)}
                size="small"
              />
            )}
          </Stack>

          {/* Gauge row — CPU, RAM, Disk, GPU */}
          <Grid container spacing={1.5} sx={{ mb: 1.5 }}>
            <Grid size={{ xs: 6, sm: 3 }}>
              <GaugeCard
                label={t("dashboard.gauges.cpu")}
                value={overview.cpu_percent}
                detail={t("dashboard.gauges.load", {
                  one: overview.load_1m ?? "-",
                  five: overview.load_5m ?? "-",
                  fifteen: overview.load_15m ?? "-",
                })}
              />
            </Grid>
            <Grid size={{ xs: 6, sm: 3 }}>
              <GaugeCard
                label={t("dashboard.gauges.ram")}
                value={ramPercent}
                detail={fmtRatio(overview.ram_used_bytes, overview.ram_total_bytes)}
                secondaryDetail={t("dashboard.gauges.swap", {
                  value: fmtRatio(overview.swap_used_bytes, overview.swap_total_bytes),
                })}
              />
            </Grid>
            <Grid size={{ xs: 6, sm: 3 }}>
              <GaugeCard
                label={t("dashboard.gauges.disk")}
                value={diskUsedPercent}
                detail={t("dashboard.gauges.disk_free_of", {
                  free: fmtBytes(overview.disk_free_bytes),
                  total: fmtBytes(overview.disk_total_bytes),
                })}
              />
            </Grid>
            <Grid size={{ xs: 6, sm: 3 }}>
              <GaugeCard
                label={t("dashboard.gauges.gpu")}
                value={overview.gpu_util_percent}
                detail={overview.gpu_util_percent != null ? fmtRatio(overview.gpu_vram_used_bytes, overview.gpu_vram_total_bytes) : undefined}
                secondaryDetail={overview.gpu_util_percent != null ? t("dashboard.gauges.vram") : undefined}
              />
            </Grid>
          </Grid>

          {/* Remaining stat cards */}
          <Grid container spacing={1.5} columns={{ xs: 12, lg: 10 }}>
            {cards.map((card) => (
              <Grid key={card.label} size={{ xs: 12, sm: 6, md: 4, lg: 2 }}>
                <StatCard label={card.label} value={card.value} detail={card.detail} />
              </Grid>
            ))}
          </Grid>
        </Box>
      ) : loading ? (
        <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
          <CircularProgress />
        </Box>
      ) : null}


      <Card variant="outlined">
        <CardContent>
          <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 1.5 }}>
            <Typography variant="h6">{t("dashboard.grafana_title")}</Typography>
            <Button component="a" href={grafanaDashboardUrl} target="_blank" rel="noreferrer">
              {t("dashboard.open_full")}
            </Button>
          </Stack>

          <Box sx={{ position: "relative", borderRadius: 1, overflow: "hidden" }}>
            <Box
              component="iframe"
              src={`${grafanaDashboardUrl}&kiosk`}
              title={t("dashboard.grafana_title")}
              sx={{
                display: "block",
                width: "100%",
                minHeight: 720,
                height: 720,
                border: 0,
                bgcolor: "background.default",
              }}
            />
            <Box
              component="a"
              href={grafanaDashboardUrl}
              target="_blank"
              rel="noreferrer"
              aria-label={t("dashboard.open_grafana_aria")}
              sx={{
                position: "absolute",
                inset: 0,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                textDecoration: "none",
                color: "common.white",
                bgcolor: "rgba(0,0,0,0.06)",
                opacity: 0,
                transition: "opacity 120ms ease-in-out",
                "&:hover": { opacity: 1, bgcolor: "rgba(0,0,0,0.28)" },
              }}
            >
              <Typography variant="h6" sx={{ fontWeight: 700 }}>
                {t("dashboard.open_grafana")}
              </Typography>
            </Box>
          </Box>
        </CardContent>
      </Card>

      {health && (
        <Card variant="outlined">
          <CardContent>
            <Typography variant="h6" sx={{ mb: 1.5 }}>{t("dashboard.dependency_health")}</Typography>
            <Stack direction="row" gap={1} flexWrap="wrap" useFlexGap>
              {health.items.map((item) => (
                <Chip
                  key={item.name}
                  label={item.detail ? `${item.name}: ${item.status} (${item.detail})` : `${item.name}: ${item.status}`}
                  color={healthColor(item.status)}
                  variant="outlined"
                  size="small"
                />
              ))}
            </Stack>
          </CardContent>
        </Card>
      )}
      
      {overview && (
        <Grid container spacing={1.5}>
          <Grid size={{ xs: 12, md: 6 }}>
            <Card variant="outlined">
              <CardContent>
                <Typography variant="h6" sx={{ mb: 1 }}>{t("dashboard.top_cpu")}</Typography>
                {overview.top_cpu_containers.length === 0 ? (
                  <Typography variant="body2" color="text.secondary">{t("dashboard.no_running_stats")}</Typography>
                ) : (
                  <List dense disablePadding>
                    {overview.top_cpu_containers.map((item) => (
                      <ListItem key={item.container_id} disableGutters>
                        <ListItemText
                          primary={item.name}
                          secondary={`${item.value.toFixed(2)} ${item.unit}`}
                          primaryTypographyProps={{ fontFamily: "monospace", fontSize: "0.85rem" }}
                        />
                      </ListItem>
                    ))}
                  </List>
                )}
              </CardContent>
            </Card>
          </Grid>
          <Grid size={{ xs: 12, md: 6 }}>
            <Card variant="outlined">
              <CardContent>
                <Typography variant="h6" sx={{ mb: 1 }}>{t("dashboard.top_memory")}</Typography>
                {overview.top_memory_containers.length === 0 ? (
                  <Typography variant="body2" color="text.secondary">{t("dashboard.no_running_stats")}</Typography>
                ) : (
                  <List dense disablePadding>
                    {overview.top_memory_containers.map((item) => (
                      <ListItem key={item.container_id} disableGutters>
                        <ListItemText
                          primary={item.name}
                          secondary={fmtBytes(item.value)}
                          primaryTypographyProps={{ fontFamily: "monospace", fontSize: "0.85rem" }}
                        />
                      </ListItem>
                    ))}
                  </List>
                )}
              </CardContent>
            </Card>
          </Grid>
        </Grid>
      )}

      <Card variant="outlined">
        <CardContent>
          <Typography variant="h6" sx={{ mb: 1.5 }}>{t("dashboard.drill_down")}</Typography>
          <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
            <Button component={RouterLink} to="/admin/containers" variant="outlined">{t("dashboard.links.containers")}</Button>
            <Button component={RouterLink} to="/admin/logs" variant="outlined">{t("dashboard.links.logs_audit")}</Button>
            <Button component={RouterLink} to="/admin/stacks" variant="outlined">{t("dashboard.links.services_stacks")}</Button>
            <Button component={RouterLink} to="/admin/settings?tab=users" variant="outlined">{t("dashboard.links.db_users")}</Button>
            <Button component={RouterLink} to="/admin/llm/runtimes" variant="outlined">{t("dashboard.links.gpu_runtimes")}</Button>
          </Stack>
        </CardContent>
      </Card>
    </Box>
  );
}
