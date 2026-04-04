import { useState } from "react";
import { useTranslation } from "react-i18next";
import Box from "@mui/material/Box";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import Collapse from "@mui/material/Collapse";
import IconButton from "@mui/material/IconButton";
import LinearProgress from "@mui/material/LinearProgress";
import Stack from "@mui/material/Stack";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import { useTheme } from "@mui/material/styles";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import RefreshIcon from "@mui/icons-material/Refresh";
import { Link as RouterLink } from "react-router";
import type { ManagedNode } from "~/api/nodes";

/* ---------- helpers ---------- */

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

function barColor(
  value: number,
  theme: ReturnType<typeof useTheme>,
): "success" | "warning" | "error" {
  if (value <= 60) return "success";
  if (value <= 85) return "warning";
  return "error";
}

function statusColor(
  status: string,
): "success" | "warning" | "error" | "default" {
  if (status === "healthy") return "success";
  if (status === "maintenance") return "warning";
  if (status === "offline" || status === "unhealthy") return "error";
  return "default";
}

function relativeTime(iso: string | null | undefined): string {
  if (!iso) return "never";
  const diff = Date.now() - new Date(iso).getTime();
  const sec = Math.floor(diff / 1000);
  if (sec < 60) return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  return `${Math.floor(hr / 24)}d ago`;
}

/* ---------- types ---------- */

interface Utilization {
  cpu_percent?: number | null;
  memory_percent?: number | null;
  memory_used_bytes?: number | null;
  memory_available_bytes?: number | null;
  disk_percent?: number | null;
  disk_used_bytes?: number | null;
  disk_free_bytes?: number | null;
  gpu?: {
    count?: number;
    total_vram_bytes?: number;
    used_vram_bytes?: number;
    free_vram_bytes?: number;
    devices?: Array<{
      index?: number;
      memory_total_mib?: number;
      memory_used_mib?: number;
      utilization_pct?: number;
      temperature_c?: number;
    }>;
  };
  network?: {
    bytes_sent?: number;
    bytes_recv?: number;
  };
}

/* ---------- component ---------- */

interface NodePerformanceCardProps {
  node: ManagedNode;
  onRefresh: (updated: ManagedNode) => void;
  refreshNode: (nodeId: string) => Promise<ManagedNode>;
}

export default function NodePerformanceCard({
  node,
  onRefresh,
  refreshNode,
}: NodePerformanceCardProps) {
  const { t } = useTranslation();
  const theme = useTheme();
  const [expanded, setExpanded] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  const util = (node.latest_utilization ?? {}) as Utilization;
  const cpu = util.cpu_percent ?? null;
  const ram = util.memory_percent ?? null;
  const gpu = util.gpu;
  const gpuVramPct =
    gpu && gpu.total_vram_bytes && gpu.total_vram_bytes > 0
      ? ((gpu.used_vram_bytes ?? 0) / gpu.total_vram_bytes) * 100
      : null;
  const hasGpu = gpu && (gpu.count ?? 0) > 0;

  async function handleRefresh() {
    setRefreshing(true);
    try {
      const updated = await refreshNode(node.id);
      onRefresh(updated);
    } catch {
      // silent — card stays with stale data
    } finally {
      setRefreshing(false);
    }
  }

  return (
    <Card variant="outlined" sx={{ height: "100%", position: "relative" }}>
      {/* Refresh button */}
      <Tooltip
        title={t("dashboard.node_refresh", {
          defaultValue: "Refresh this node",
        })}
      >
        <IconButton
          size="small"
          onClick={handleRefresh}
          disabled={refreshing}
          sx={{
            position: "absolute",
            top: 4,
            right: 4,
            opacity: 0.6,
            "&:hover": { opacity: 1 },
          }}
        >
          {refreshing ? (
            <CircularProgress size={14} />
          ) : (
            <RefreshIcon sx={{ fontSize: 16 }} />
          )}
        </IconButton>
      </Tooltip>

      <CardContent sx={{ pb: "8px !important" }}>
        {/* Header */}
        <Stack
          direction="row"
          alignItems="center"
          spacing={1}
          sx={{ mb: 1, pr: 3 }}
        >
          <Typography
            variant="body2"
            sx={{
              fontWeight: 700,
              fontFamily: "monospace",
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
              flexGrow: 1,
            }}
            component={RouterLink}
            to={`/admin/nodes/${node.id}`}
            color="inherit"
          >
            {node.host}
          </Typography>
          <Chip
            label={node.maintenance_mode ? t("nodes.maintenance") : node.status}
            color={node.maintenance_mode ? "warning" : statusColor(node.status)}
            size="small"
            variant="outlined"
            sx={{ fontSize: "0.65rem", height: 20 }}
          />
        </Stack>

        {/* Last seen */}
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ mb: 1, display: "block" }}
        >
          {t("dashboard.node_last_seen", { defaultValue: "Seen" })}{" "}
          {relativeTime(node.last_seen)}
        </Typography>

        {/* Performance bars */}
        <Stack spacing={0.75}>
          <ProgressBar
            label={t("dashboard.node_cpu", { defaultValue: "CPU" })}
            value={cpu}
            theme={theme}
          />
          <ProgressBar
            label={t("dashboard.node_ram", { defaultValue: "RAM" })}
            value={ram}
            theme={theme}
          />
          {hasGpu && (
            <ProgressBar
              label={t("dashboard.node_gpu_vram", {
                defaultValue: "GPU VRAM",
              })}
              value={gpuVramPct}
              theme={theme}
            />
          )}
        </Stack>

        {/* Expand toggle */}
        <Box sx={{ display: "flex", justifyContent: "center", mt: 0.5 }}>
          <IconButton
            size="small"
            onClick={() => setExpanded((v) => !v)}
            sx={{
              transform: expanded ? "rotate(180deg)" : "rotate(0deg)",
              transition: "transform 0.2s",
            }}
          >
            <ExpandMoreIcon sx={{ fontSize: 18 }} />
          </IconButton>
        </Box>

        {/* Expanded details */}
        <Collapse in={expanded}>
          <Stack spacing={0.5} sx={{ mt: 0.5 }}>
            {util.disk_percent != null && (
              <DetailRow
                label={t("nodes.disk")}
                value={`${util.disk_percent.toFixed(1)}% — ${fmtBytes(util.disk_used_bytes)} used, ${fmtBytes(util.disk_free_bytes)} free`}
              />
            )}
            {util.network && (
              <DetailRow
                label={t("nodes.network")}
                value={`RX ${fmtBytes(util.network.bytes_recv)} / TX ${fmtBytes(util.network.bytes_sent)}`}
              />
            )}
            {hasGpu && gpu?.devices && gpu.devices.length > 0 && (
              <>
                {gpu.devices.map((dev, i) => (
                  <DetailRow
                    key={i}
                    label={`${t("nodes.gpu")} #${dev.index ?? i}`}
                    value={`${dev.utilization_pct ?? 0}% util, ${dev.memory_used_mib ?? 0}/${dev.memory_total_mib ?? 0} MiB${dev.temperature_c != null ? `, ${dev.temperature_c}°C` : ""}`}
                  />
                ))}
              </>
            )}
            {node.version && (
              <DetailRow label={t("nodes.agent")} value={node.version} />
            )}
            {Object.keys(node.labels).length > 0 && (
              <DetailRow
                label={t("nodes.labels")}
                value={Object.entries(node.labels)
                  .map(([k, v]) => `${k}=${v}`)
                  .join(", ")}
              />
            )}
          </Stack>
        </Collapse>
      </CardContent>
    </Card>
  );
}

/* ---------- sub-components ---------- */

function ProgressBar({
  label,
  value,
  theme,
}: {
  label: string;
  value: number | null;
  theme: ReturnType<typeof useTheme>;
}) {
  return (
    <Stack direction="row" alignItems="center" spacing={1}>
      <Typography
        variant="caption"
        sx={{ width: 64, flexShrink: 0, fontWeight: 600 }}
      >
        {label}
      </Typography>
      {value != null ? (
        <>
          <LinearProgress
            variant="determinate"
            value={Math.min(value, 100)}
            color={barColor(value, theme)}
            sx={{ flexGrow: 1, height: 6, borderRadius: 3 }}
          />
          <Typography
            variant="caption"
            sx={{ width: 40, textAlign: "right", flexShrink: 0 }}
          >
            {value.toFixed(1)}%
          </Typography>
        </>
      ) : (
        <Typography variant="caption" color="text.disabled">
          N/A
        </Typography>
      )}
    </Stack>
  );
}

function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <Stack direction="row" spacing={1}>
      <Typography
        variant="caption"
        color="text.secondary"
        sx={{ width: 64, flexShrink: 0, fontWeight: 600 }}
      >
        {label}
      </Typography>
      <Typography
        variant="caption"
        sx={{ fontFamily: "monospace", wordBreak: "break-all" }}
      >
        {value}
      </Typography>
    </Stack>
  );
}
