import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link as RouterLink } from "react-router";
import { useTranslation } from "react-i18next";
import {
  dashboard,
  hardware,
  type DashboardHealth,
  type DashboardOverview,
  type HardwareInfo,
} from "~/api/admin";
import { providers as providersApi, type Provider } from "~/api/llm";
import { nodesApi, type ManagedNode } from "~/api/nodes";
import DataResidencyCard from "~/components/DataResidencyCard";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import Grid from "@mui/material/Grid";
import Skeleton from "@mui/material/Skeleton";
import IconButton from "@mui/material/IconButton";
import List from "@mui/material/List";
import ListItem from "@mui/material/ListItem";
import ListItemText from "@mui/material/ListItemText";
import Stack from "@mui/material/Stack";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import { useTheme } from "@mui/material/styles";
import RefreshIcon from "@mui/icons-material/Refresh";
import TuneIcon from "@mui/icons-material/Tune";
import RestartAltIcon from "@mui/icons-material/RestartAlt";
import GaugeCard from "~/components/GaugeCard";
import StatCard from "~/components/dashboard/StatCard";
import ModuleStatusSection from "~/components/dashboard/ModuleStatusSection";
import NodeFleetRow from "~/components/dashboard/NodeFleetRow";
import DashboardSection from "~/components/dashboard/DashboardSection";
import { useDashboardLayout, type SectionId } from "~/lib/useDashboardLayout";
import {
  DndContext,
  DragOverlay,
  closestCenter,
  PointerSensor,
  KeyboardSensor,
  useSensor,
  useSensors,
  type DragStartEvent,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  SortableContext,
  sortableKeyboardCoordinates,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";

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

function fmtRatio(
  used: number | null | undefined,
  total: number | null | undefined,
): string {
  if (used == null || total == null || total <= 0) return "N/A";
  return `${fmtBytes(used)} / ${fmtBytes(total)}`;
}

function healthColor(
  status: string,
): "success" | "warning" | "error" | "default" {
  const value = status.toLowerCase();
  if (value === "up" || value === "healthy") return "success";
  if (value === "degraded" || value === "unknown") return "warning";
  if (value === "down") return "error";
  return "default";
}

function systemBadgeColor(
  badge: string,
): "success" | "warning" | "error" | "default" {
  if (badge === "green") return "success";
  if (badge === "yellow") return "warning";
  if (badge === "red") return "error";
  return "default";
}

export default function DashboardPage() {
  const { t } = useTranslation();
  const theme = useTheme();

  /* --- Dashboard layout (DnD + visibility) --- */
  const { layout, reorder, toggleVisibility, resetLayout } =
    useDashboardLayout();
  const [editMode, setEditMode] = useState(false);
  const [activeId, setActiveId] = useState<string | null>(null);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 8 } }),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    }),
  );

  function handleDragStart(event: DragStartEvent) {
    setActiveId(event.active.id as string);
  }

  function handleDragEnd(event: DragEndEvent) {
    setActiveId(null);
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    reorder(active.id as string, over.id as string);
  }

  const SECTION_TITLES: Record<SectionId, string> = {
    gauges: t("dashboard.section_gauges", { defaultValue: "System Gauges" }),
    node_fleet: t("dashboard.section_node_fleet", {
      defaultValue: "Node Fleet",
    }),
    stat_cards: t("dashboard.section_stat_cards", {
      defaultValue: "Statistics",
    }),
    grafana: t("dashboard.section_grafana", { defaultValue: "Grafana" }),
    dependency_health: t("dashboard.section_dependency_health", {
      defaultValue: "Dependency Health",
    }),
    module_status: t("dashboard.section_module_status", {
      defaultValue: "Module Status",
    }),
    data_residency: t("dashboard.section_data_residency", {
      defaultValue: "Data Residency",
    }),
    top_containers: t("dashboard.section_top_containers", {
      defaultValue: "Top Containers",
    }),
    quick_links: t("dashboard.section_quick_links", {
      defaultValue: "Quick Links",
    }),
  };

  /* --- Data --- */
  const grafanaDashboardBaseUrl =
    (import.meta.env.VITE_GRAFANA_DASHBOARD_URL as string | undefined) ??
    "/grafana/d/llm-port-overview/llm-port-overview?orgId=1&from=now-6h&to=now&timezone=browser&refresh=30s";

  const grafanaDashboardUrl = useMemo(() => {
    try {
      const base = grafanaDashboardBaseUrl.startsWith("/")
        ? window.location.origin
        : undefined;
      const url = new URL(grafanaDashboardBaseUrl, base);
      url.searchParams.set(
        "theme",
        theme.palette.mode === "dark" ? "dark" : "light",
      );
      // Return path-only for same-origin to avoid cross-origin iframe issues
      return base ? `${url.pathname}${url.search}` : url.toString();
    } catch {
      const separator = grafanaDashboardBaseUrl.includes("?") ? "&" : "?";
      return `${grafanaDashboardBaseUrl}${separator}theme=${theme.palette.mode === "dark" ? "dark" : "light"}`;
    }
  }, [grafanaDashboardBaseUrl, theme.palette.mode]);

  const [overview, setOverview] = useState<DashboardOverview | null>(null);
  const [health, setHealth] = useState<DashboardHealth | null>(null);
  const [hw, setHw] = useState<HardwareInfo | null>(null);
  const [llmProviders, setLlmProviders] = useState<Provider[]>([]);
  const [nodes, setNodes] = useState<ManagedNode[]>([]);
  const [refreshing, setRefreshing] = useState(true);
  const [rescanning, setRescanning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  /** Fire every API independently — sections render as each resolves. */
  function loadAll() {
    setRefreshing(true);
    setError(null);

    const overviewP = dashboard
      .overview()
      .then(setOverview)
      .catch((err: unknown) => {
        setError(
          err instanceof Error ? err.message : t("dashboard.failed_load"),
        );
      });

    const rest = [
      dashboard
        .health()
        .then(setHealth)
        .catch(() => {}),
      hardware
        .info()
        .then(setHw)
        .catch(() => {}),
      providersApi
        .list()
        .then(setLlmProviders)
        .catch(() => {}),
      nodesApi
        .list()
        .then(setNodes)
        .catch(() => {}),
    ];

    void Promise.allSettled([overviewP, ...rest]).then(() =>
      setRefreshing(false),
    );
  }

  async function rescanGpu() {
    setRescanning(true);
    try {
      const hwInfo = await hardware.rescan();
      setHw(hwInfo);
    } catch (err: unknown) {
      setError(
        err instanceof Error
          ? err.message
          : t("dashboard.gpu_rescan_failed", {
              defaultValue: "GPU rescan failed",
            }),
      );
    } finally {
      setRescanning(false);
    }
  }

  const handleRefreshNode = useCallback((updated: ManagedNode) => {
    setNodes((prev) => prev.map((n) => (n.id === updated.id ? updated : n)));
  }, []);

  const refreshSingleNode = useCallback(
    (nodeId: string) => nodesApi.get(nodeId),
    [],
  );

  useEffect(() => {
    loadAll();
  }, []);

  // After the first node list arrives, immediately fetch each node
  // individually to ensure fresh utilization data is available.
  const initialNodeFetchDone = useRef(false);
  useEffect(() => {
    if (nodes.length === 0 || initialNodeFetchDone.current) return;
    initialNodeFetchDone.current = true;
    for (const node of nodes) {
      nodesApi
        .get(node.id)
        .then((updated) => {
          setNodes((prev) =>
            prev.map((n) => (n.id === updated.id ? updated : n)),
          );
        })
        .catch(() => {});
    }
  }, [nodes]);

  // Auto-refresh node utilization every 30s (only when node_fleet section is visible)
  useEffect(() => {
    if (layout.hidden.includes("node_fleet")) return;
    const interval = setInterval(() => {
      nodesApi
        .list()
        .then(setNodes)
        .catch(() => {});
    }, 30_000);
    return () => clearInterval(interval);
  }, [layout.hidden]);

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
        detail: t("dashboard.cards.restarting", {
          count: overview.containers_restarting,
        }),
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
        value:
          overview.postgres_connections == null
            ? "N/A"
            : String(overview.postgres_connections),
        detail:
          overview.postgres_max_connections == null
            ? undefined
            : t("dashboard.cards.max", {
                count: overview.postgres_max_connections,
              }),
      },
    ];
  }, [overview, t]);

  /** RAM usage as a 0-100 percentage */
  const ramPercent = useMemo(() => {
    if (
      !overview?.ram_used_bytes ||
      !overview?.ram_total_bytes ||
      overview.ram_total_bytes <= 0
    )
      return null;
    return (overview.ram_used_bytes / overview.ram_total_bytes) * 100;
  }, [overview]);

  /** Disk usage (inverse of free) as 0-100 */
  const diskUsedPercent = useMemo(() => {
    if (overview?.disk_free_percent == null) return null;
    return 100 - overview.disk_free_percent;
  }, [overview]);

  /** GPU VRAM usage as 0-100, used as gauge fallback when util is null */
  const gpuVramPercent = useMemo(() => {
    if (
      !overview?.gpu_vram_used_bytes ||
      !overview?.gpu_vram_total_bytes ||
      overview.gpu_vram_total_bytes <= 0
    )
      return null;
    return (overview.gpu_vram_used_bytes / overview.gpu_vram_total_bytes) * 100;
  }, [overview]);

  /* --- Section content renderer --- */
  function renderSection(id: SectionId) {
    switch (id) {
      case "gauges": {
        // Always render the grid shell so cards appear instantly.
        // Individual cards show "N/A" until overview resolves;
        // GPU label upgrades once hw resolves (separate call).
        const cpuLabel = t("dashboard.gauges.cpu");
        const ramLabel = t("dashboard.gauges.ram");
        const diskLabel = t("dashboard.gauges.disk");
        const gpuLabel = hw?.gpu.has_gpu
          ? `GPU (${hw.gpu.primary_vendor.toUpperCase()})`
          : t("dashboard.gauges.gpu");
        const hasGpu =
          hw?.gpu.has_gpu ||
          overview?.gpu_util_percent != null ||
          gpuVramPercent != null;
        const gaugeSm = hasGpu ? 3 : 4;

        return (
          <Box>
            <Stack direction="row" spacing={1} sx={{ mb: 1 }}>
              {overview ? (
                <Chip
                  label={t("dashboard.system", {
                    status: overview.system_status,
                  })}
                  color={systemBadgeColor(overview.system_badge)}
                  size="small"
                />
              ) : (
                <Skeleton variant="rounded" width={120} height={24} />
              )}
              {health ? (
                <Chip
                  label={t("dashboard.dependencies", {
                    status: health.overall_status,
                  })}
                  color={healthColor(health.overall_status)}
                  size="small"
                />
              ) : (
                <Skeleton variant="rounded" width={160} height={24} />
              )}
            </Stack>
            <Grid container spacing={1.5}>
              <Grid size={{ xs: 6, sm: gaugeSm }}>
                <GaugeCard
                  label={cpuLabel}
                  value={overview?.cpu_percent ?? null}
                  detail={
                    overview
                      ? t("dashboard.gauges.load", {
                          one: overview.load_1m?.toFixed(2) ?? "-",
                          five: overview.load_5m?.toFixed(2) ?? "-",
                          fifteen: overview.load_15m?.toFixed(2) ?? "-",
                        })
                      : undefined
                  }
                />
              </Grid>
              <Grid size={{ xs: 6, sm: gaugeSm }}>
                <GaugeCard
                  label={ramLabel}
                  value={ramPercent}
                  detail={
                    overview
                      ? fmtRatio(
                          overview.ram_used_bytes,
                          overview.ram_total_bytes,
                        )
                      : undefined
                  }
                  secondaryDetail={
                    overview
                      ? t("dashboard.gauges.swap", {
                          value: fmtRatio(
                            overview.swap_used_bytes,
                            overview.swap_total_bytes,
                          ),
                        })
                      : undefined
                  }
                />
              </Grid>
              <Grid size={{ xs: 6, sm: gaugeSm }}>
                <GaugeCard
                  label={diskLabel}
                  value={diskUsedPercent}
                  detail={
                    overview
                      ? t("dashboard.gauges.disk_free_of", {
                          free: fmtBytes(overview.disk_free_bytes),
                          total: fmtBytes(overview.disk_total_bytes),
                        })
                      : undefined
                  }
                />
              </Grid>
              {hasGpu && (
                <Grid size={{ xs: 6, sm: 3 }}>
                  <Box sx={{ position: "relative", height: "100%" }}>
                    <GaugeCard
                      label={gpuLabel}
                      value={overview?.gpu_util_percent ?? gpuVramPercent}
                      innerText={
                        overview?.gpu_util_percent == null &&
                        gpuVramPercent != null
                          ? `${gpuVramPercent.toFixed(1)}%`
                          : undefined
                      }
                      detail={
                        overview?.gpu_vram_total_bytes != null
                          ? fmtRatio(
                              overview.gpu_vram_used_bytes,
                              overview.gpu_vram_total_bytes,
                            )
                          : undefined
                      }
                      secondaryDetail={
                        hw?.gpu.has_gpu
                          ? `${hw.gpu.devices.find((d) => d.vendor === hw.gpu.primary_vendor)?.model ?? hw.gpu.devices[0]?.model ?? ""} · ${hw.gpu.primary_compute_api.toUpperCase()}`
                          : overview?.gpu_util_percent != null ||
                              gpuVramPercent != null
                            ? t("dashboard.gauges.vram")
                            : undefined
                      }
                    />
                    <Tooltip
                      title={t("dashboard.gpu_rescan", {
                        defaultValue: "Re-detect GPUs",
                      })}
                    >
                      <IconButton
                        size="small"
                        onClick={rescanGpu}
                        disabled={rescanning}
                        sx={{
                          position: "absolute",
                          top: 4,
                          right: 4,
                          opacity: 0.7,
                          "&:hover": { opacity: 1 },
                        }}
                      >
                        {rescanning ? (
                          <CircularProgress size={16} />
                        ) : (
                          <RefreshIcon fontSize="small" />
                        )}
                      </IconButton>
                    </Tooltip>
                    {hw?.gpu.has_gpu && hw.gpu.device_count > 1 && (
                      <Tooltip
                        title={hw.gpu.devices
                          .map(
                            (d) =>
                              `#${d.index} ${d.vendor.toUpperCase()} ${d.model} (${fmtBytes(d.vram_bytes)})`,
                          )
                          .join("\n")}
                      >
                        <Chip
                          label={`${hw.gpu.device_count} GPUs`}
                          size="small"
                          variant="outlined"
                          sx={{
                            position: "absolute",
                            bottom: 6,
                            right: 6,
                            fontSize: "0.65rem",
                            height: 20,
                          }}
                        />
                      </Tooltip>
                    )}
                  </Box>
                </Grid>
              )}
            </Grid>
          </Box>
        );
      }

      case "node_fleet":
        return (
          <NodeFleetRow
            nodes={nodes}
            onRefreshNode={handleRefreshNode}
            refreshNode={refreshSingleNode}
          />
        );

      case "stat_cards":
        return overview ? (
          <Grid container spacing={1.5} columns={{ xs: 12, lg: 10 }}>
            {cards.map((card) => (
              <Grid key={card.label} size={{ xs: 12, sm: 6, md: 4, lg: 2 }}>
                <StatCard
                  label={card.label}
                  value={card.value}
                  detail={card.detail}
                />
              </Grid>
            ))}
          </Grid>
        ) : (
          <Grid container spacing={1.5} columns={{ xs: 12, lg: 10 }}>
            {Array.from({ length: 5 }).map((_, i) => (
              <Grid key={i} size={{ xs: 12, sm: 6, md: 4, lg: 2 }}>
                <Skeleton variant="rounded" height={80} />
              </Grid>
            ))}
          </Grid>
        );

      case "grafana":
        return (
          <Card variant="outlined">
            <CardContent>
              <Stack
                direction="row"
                justifyContent="space-between"
                alignItems="center"
                sx={{ mb: 1.5 }}
              >
                <Typography variant="h6">
                  {t("dashboard.grafana_title")}
                </Typography>
                <Button
                  component="a"
                  href={grafanaDashboardUrl}
                  target="_blank"
                  rel="noreferrer"
                >
                  {t("dashboard.open_full")}
                </Button>
              </Stack>
              <Box
                sx={{
                  position: "relative",
                  borderRadius: 1,
                  overflow: "hidden",
                }}
              >
                <Box
                  component="iframe"
                  loading="lazy"
                  src={`${grafanaDashboardUrl}&kiosk`}
                  title={t("dashboard.grafana_title")}
                  sx={{
                    display: "block",
                    width: "100%",
                    minHeight: { xs: 300, md: 720 },
                    height: { xs: 300, md: 720 },
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
                    "&:hover": {
                      opacity: 1,
                      bgcolor: "rgba(0,0,0,0.28)",
                    },
                  }}
                >
                  <Typography variant="h6" sx={{ fontWeight: 700 }}>
                    {t("dashboard.open_grafana")}
                  </Typography>
                </Box>
              </Box>
            </CardContent>
          </Card>
        );

      case "dependency_health":
        return health ? (
          <Card variant="outlined">
            <CardContent>
              <Typography variant="h6" sx={{ mb: 1.5 }}>
                {t("dashboard.dependency_health")}
              </Typography>
              <Stack direction="row" gap={1} flexWrap="wrap" useFlexGap>
                {health.items.map((item) => (
                  <Chip
                    key={item.name}
                    label={
                      item.detail
                        ? `${item.name}: ${item.status} (${item.detail})`
                        : `${item.name}: ${item.status}`
                    }
                    color={healthColor(item.status)}
                    variant="outlined"
                    size="small"
                  />
                ))}
              </Stack>
            </CardContent>
          </Card>
        ) : (
          <Card variant="outlined">
            <CardContent>
              <Skeleton variant="text" width={180} height={32} sx={{ mb: 1 }} />
              <Stack direction="row" gap={1} flexWrap="wrap">
                {Array.from({ length: 6 }).map((_, i) => (
                  <Skeleton key={i} variant="rounded" width={120} height={24} />
                ))}
              </Stack>
            </CardContent>
          </Card>
        );

      case "module_status":
        return <ModuleStatusSection />;

      case "data_residency":
        return <DataResidencyCard providers={llmProviders} />;

      case "top_containers":
        return overview ? (
          <Grid container spacing={1.5}>
            <Grid size={{ xs: 12, md: 6 }}>
              <Card variant="outlined">
                <CardContent>
                  <Typography variant="h6" sx={{ mb: 1 }}>
                    {t("dashboard.top_cpu")}
                  </Typography>
                  {overview.top_cpu_containers.length === 0 ? (
                    <Typography variant="body2" color="text.secondary">
                      {t("dashboard.no_running_stats")}
                    </Typography>
                  ) : (
                    <List dense disablePadding>
                      {overview.top_cpu_containers.map((item) => (
                        <ListItem key={item.container_id} disableGutters>
                          <ListItemText
                            primary={item.name}
                            secondary={`${item.value.toFixed(2)} ${item.unit}`}
                            primaryTypographyProps={{
                              fontFamily: "monospace",
                              fontSize: "0.85rem",
                            }}
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
                  <Typography variant="h6" sx={{ mb: 1 }}>
                    {t("dashboard.top_memory")}
                  </Typography>
                  {overview.top_memory_containers.length === 0 ? (
                    <Typography variant="body2" color="text.secondary">
                      {t("dashboard.no_running_stats")}
                    </Typography>
                  ) : (
                    <List dense disablePadding>
                      {overview.top_memory_containers.map((item) => (
                        <ListItem key={item.container_id} disableGutters>
                          <ListItemText
                            primary={item.name}
                            secondary={fmtBytes(item.value)}
                            primaryTypographyProps={{
                              fontFamily: "monospace",
                              fontSize: "0.85rem",
                            }}
                          />
                        </ListItem>
                      ))}
                    </List>
                  )}
                </CardContent>
              </Card>
            </Grid>
          </Grid>
        ) : (
          <Grid container spacing={1.5}>
            {[0, 1].map((i) => (
              <Grid key={i} size={{ xs: 12, md: 6 }}>
                <Card variant="outlined">
                  <CardContent>
                    <Skeleton
                      variant="text"
                      width={140}
                      height={32}
                      sx={{ mb: 1 }}
                    />
                    {Array.from({ length: 3 }).map((_, j) => (
                      <Skeleton
                        key={j}
                        variant="text"
                        height={28}
                        sx={{ mb: 0.5 }}
                      />
                    ))}
                  </CardContent>
                </Card>
              </Grid>
            ))}
          </Grid>
        );

      case "quick_links":
        return (
          <Card variant="outlined">
            <CardContent>
              <Typography variant="h6" sx={{ mb: 1.5 }}>
                {t("dashboard.drill_down")}
              </Typography>
              <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
                <Button
                  component={RouterLink}
                  to="/admin/containers"
                  variant="outlined"
                >
                  {t("dashboard.links.containers")}
                </Button>
                <Button
                  component={RouterLink}
                  to="/admin/logs"
                  variant="outlined"
                >
                  {t("dashboard.links.logs_audit")}
                </Button>
                <Button
                  component={RouterLink}
                  to="/admin/stacks"
                  variant="outlined"
                >
                  {t("dashboard.links.services_stacks")}
                </Button>
                <Button
                  component={RouterLink}
                  to="/admin/settings?tab=users"
                  variant="outlined"
                >
                  {t("dashboard.links.db_users")}
                </Button>
                <Button
                  component={RouterLink}
                  to="/admin/llm/runtimes"
                  variant="outlined"
                >
                  {t("dashboard.links.gpu_runtimes")}
                </Button>
              </Stack>
            </CardContent>
          </Card>
        );

      default:
        return null;
    }
  }

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
      {/* Toolbar */}
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="center"
        flexWrap="wrap"
        gap={1}
      >
        <Typography variant="h5">{t("dashboard.title")}</Typography>
        <Stack direction="row" spacing={1} alignItems="center">
          {refreshing && <CircularProgress size={20} />}
          <Tooltip
            title={t("dashboard.customize", { defaultValue: "Customize" })}
          >
            <IconButton
              size="small"
              onClick={() => setEditMode((v) => !v)}
              color={editMode ? "primary" : "default"}
            >
              <TuneIcon />
            </IconButton>
          </Tooltip>
          {editMode && (
            <Tooltip
              title={t("dashboard.reset_layout", {
                defaultValue: "Reset layout",
              })}
            >
              <IconButton size="small" onClick={resetLayout}>
                <RestartAltIcon />
              </IconButton>
            </Tooltip>
          )}
          <Button variant="outlined" onClick={loadAll} disabled={refreshing}>
            {t("dashboard.refresh")}
          </Button>
        </Stack>
      </Stack>

      {error && <Alert severity="error">{error}</Alert>}

      <DndContext
        sensors={sensors}
        collisionDetection={closestCenter}
        onDragStart={handleDragStart}
        onDragEnd={handleDragEnd}
      >
        <SortableContext
          items={layout.order}
          strategy={verticalListSortingStrategy}
        >
          {layout.order.map((sectionId) => (
            <DashboardSection
              key={sectionId}
              id={sectionId}
              title={SECTION_TITLES[sectionId]}
              hidden={layout.hidden.includes(sectionId)}
              editMode={editMode}
              onToggleVisibility={toggleVisibility}
            >
              {renderSection(sectionId)}
            </DashboardSection>
          ))}
        </SortableContext>
        <DragOverlay dropAnimation={{ duration: 200, easing: "ease" }}>
          {activeId ? (
            <Box
              sx={{
                bgcolor: "background.paper",
                boxShadow: 4,
                borderRadius: 1,
                px: 2,
                py: 1,
                opacity: 0.92,
              }}
            >
              <Typography sx={{ fontSize: "0.875rem", fontWeight: 600 }}>
                {SECTION_TITLES[activeId as SectionId]}
              </Typography>
            </Box>
          ) : null}
        </DragOverlay>
      </DndContext>
    </Box>
  );
}
