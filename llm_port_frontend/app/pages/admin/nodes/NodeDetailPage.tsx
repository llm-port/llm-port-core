import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router";
import { useTranslation } from "react-i18next";
import {
  nodesApi,
  type ManagedNode,
  type NodeCommand,
  type NodeCommandTimeline,
  type NodeEnrollmentToken,
  type NodeProfile,
} from "~/api/nodes";
import { logsApi, type LogStream } from "~/api/logs";
import { NodeDeploymentProgress } from "~/pages/admin/llm/RuntimeDetailPage";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import Collapse from "@mui/material/Collapse";
import Grid from "@mui/material/Grid";
import IconButton from "@mui/material/IconButton";
import MenuItem from "@mui/material/MenuItem";
import Select from "@mui/material/Select";
import Stack from "@mui/material/Stack";
import Switch from "@mui/material/Switch";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";

import TextField from "@mui/material/TextField";

import ArrowBackIcon from "@mui/icons-material/ArrowBack";
import BuildIcon from "@mui/icons-material/Build";
import BugReportIcon from "@mui/icons-material/BugReport";
import ContentCopyIcon from "@mui/icons-material/ContentCopy";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import InvertColorsOffIcon from "@mui/icons-material/InvertColorsOff";
import InventoryIcon from "@mui/icons-material/Inventory";
import PauseCircleOutlineIcon from "@mui/icons-material/PauseCircleOutline";
import RefreshIcon from "@mui/icons-material/Refresh";
import SystemUpdateAltIcon from "@mui/icons-material/SystemUpdateAlt";
import VpnKeyIcon from "@mui/icons-material/VpnKey";
import SystemUpdateDialog from "~/pages/admin/nodes/SystemUpdateDialog";

import GaugeCard from "~/components/GaugeCard";

/* ── helpers ────────────────────────────────────────────────────────────── */

const REFRESH_INTERVAL_MS = 10_000;

function statusColor(
  status: string,
): "success" | "warning" | "error" | "default" {
  if (status === "healthy") return "success";
  if (
    status === "maintenance" ||
    status === "draining" ||
    status === "degraded"
  )
    return "warning";
  if (status === "offline" || status === "error") return "error";
  return "default";
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

/* ── utilization / inventory shapes ─────────────────────────────────────── */

interface GpuDevice {
  index?: number;
  memory_total_mib?: number;
  memory_used_mib?: number;
  utilization_pct?: number;
  temperature_c?: number;
}

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
    devices?: GpuDevice[];
  };
  gpu_free_vram_bytes?: number;
  network?: {
    bytes_sent?: number;
    bytes_recv?: number;
    packets_sent?: number;
    packets_recv?: number;
  };
}

interface Inventory {
  cpu_count_logical?: number;
  cpu_count_physical?: number;
  memory_total_bytes?: number;
  disk_total_bytes?: number;
  gpu_count?: number;
  gpu?: {
    count?: number;
    devices?: GpuDevice[];
    total_vram_bytes?: number;
    used_vram_bytes?: number;
    free_vram_bytes?: number;
  };
  network_interfaces?: string[];
  static_capabilities?: {
    os?: string;
    machine?: string;
    hostname?: string;
    processor?: string;
    docker_available?: boolean;
    gpu_count?: number;
  };
}

/* ── component ──────────────────────────────────────────────────────────── */

export default function NodeDetailPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { id } = useParams<{ id: string }>();

  const [node, setNode] = useState<ManagedNode | null>(null);
  const [commands, setCommands] = useState<NodeCommand[]>([]);
  const [timeline, setTimeline] = useState<NodeCommandTimeline | null>(null);
  const [loading, setLoading] = useState(true);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [logStreams, setLogStreams] = useState<LogStream[]>([]);
  const [logsExpanded, setLogsExpanded] = useState(true);
  const [commandsExpanded, setCommandsExpanded] = useState(false);
  const [inventoryExpanded, setInventoryExpanded] = useState(false);
  const [containerLogStreams, setContainerLogStreams] = useState<LogStream[]>(
    [],
  );
  const [containerLogsExpanded, setContainerLogsExpanded] = useState(true);
  const [deployTimeline, setDeployTimeline] =
    useState<NodeCommandTimeline | null>(null);
  const [enrollToken, setEnrollToken] = useState<NodeEnrollmentToken | null>(
    null,
  );
  const [enrollBusy, setEnrollBusy] = useState(false);
  const [updateDialogOpen, setUpdateDialogOpen] = useState(false);
  const [enrollCopied, setEnrollCopied] = useState(false);
  const [profiles, setProfiles] = useState<NodeProfile[]>([]);
  const [profileBusy, setProfileBusy] = useState(false);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const sortedCommands = useMemo(
    () =>
      [...commands].sort(
        (a, b) => Date.parse(b.issued_at) - Date.parse(a.issued_at),
      ),
    [commands],
  );

  /* ── data loading ──────────────────────────────────────────────── */

  const load = useCallback(async () => {
    if (!id) return;
    setLoading(true);
    setError(null);
    try {
      const [nodeRes, commandRes, profilesRes] = await Promise.all([
        nodesApi.get(id),
        nodesApi.listCommands(id),
        nodesApi.listProfiles().catch(() => [] as NodeProfile[]),
      ]);
      setNode(nodeRes);
      setCommands(commandRes);
      setProfiles(profilesRes);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load node.");
    } finally {
      setLoading(false);
    }
  }, [id]);

  const silentRefresh = useCallback(async () => {
    if (!id) return;
    try {
      const nodeRes = await nodesApi.get(id);
      setNode(nodeRes);
    } catch {
      /* silent */
    }
  }, [id]);

  const loadLogs = useCallback(async (host: string) => {
    try {
      const end = new Date();
      const start = new Date(end);
      start.setMinutes(start.getMinutes() - 15);
      const res = await logsApi.queryRange({
        query: `{host="${host}"}`,
        start: start.toISOString(),
        end: end.toISOString(),
        limit: 100,
        direction: "BACKWARD",
      });
      setLogStreams(res.streams);
    } catch {
      /* logs are best-effort */
    }
  }, []);

  const loadContainerLogs = useCallback(async (host: string) => {
    try {
      const end = new Date();
      const start = new Date(end);
      start.setMinutes(start.getMinutes() - 15);
      const res = await logsApi.queryRange({
        query: `{job="node-container", host="${host}"}`,
        start: start.toISOString(),
        end: end.toISOString(),
        limit: 200,
        direction: "BACKWARD",
      });
      setContainerLogStreams(res.streams);
    } catch {
      /* container logs are best-effort */
    }
  }, []);

  const loadDeployTimeline = useCallback(
    async (nodeId: string, cmds: NodeCommand[]) => {
      // Find the most recent DEPLOY_WORKLOAD command
      const deployCmds = cmds
        .filter((c) => c.command_type === "deploy_workload")
        .sort((a, b) => Date.parse(b.issued_at) - Date.parse(a.issued_at));
      if (deployCmds.length === 0) {
        setDeployTimeline(null);
        return;
      }
      try {
        const tl = await nodesApi.commandTimeline(nodeId, deployCmds[0].id);
        setDeployTimeline(tl);
      } catch {
        /* ignore */
      }
    },
    [],
  );

  async function runAction(key: string, action: () => Promise<void>) {
    setBusyKey(key);
    try {
      await action();
      await load();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Action failed.");
    } finally {
      setBusyKey(null);
    }
  }

  async function generateEnrollToken() {
    setEnrollBusy(true);
    setEnrollCopied(false);
    try {
      const tok = await nodesApi.createEnrollmentToken(
        `re-enroll ${node?.host ?? id}`,
      );
      setEnrollToken(tok);
    } catch (err: unknown) {
      setError(
        err instanceof Error
          ? err.message
          : "Failed to create enrollment token.",
      );
    } finally {
      setEnrollBusy(false);
    }
  }

  async function copyEnrollToken() {
    if (!enrollToken) return;
    try {
      await navigator.clipboard.writeText(enrollToken.token);
      setEnrollCopied(true);
    } catch {
      setError("Failed to copy token to clipboard.");
    }
  }

  async function handleProfileChange(profileId: string) {
    if (!id) return;
    setProfileBusy(true);
    try {
      if (profileId === "") {
        await nodesApi.unassignProfile(id);
      } else {
        await nodesApi.assignProfile(id, profileId);
      }
      await silentRefresh();
    } catch (err: unknown) {
      setError(
        err instanceof Error ? err.message : "Failed to change profile.",
      );
    } finally {
      setProfileBusy(false);
    }
  }

  async function loadTimeline(commandId: string) {
    if (!id) return;
    setBusyKey(`timeline:${commandId}`);
    try {
      setTimeline(await nodesApi.commandTimeline(id, commandId));
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load timeline.");
    } finally {
      setBusyKey(null);
    }
  }

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (node?.host) void loadLogs(node.host);
    if (node?.host) void loadContainerLogs(node.host);
  }, [node?.host, loadLogs, loadContainerLogs]);

  useEffect(() => {
    if (id && commands.length > 0) void loadDeployTimeline(id, commands);
  }, [id, commands, loadDeployTimeline]);

  useEffect(() => {
    if (intervalRef.current) clearInterval(intervalRef.current);
    if (autoRefresh && id) {
      intervalRef.current = setInterval(() => {
        void silentRefresh();
        if (node?.host) {
          void loadLogs(node.host);
          void loadContainerLogs(node.host);
        }
        if (id && commands.length > 0) void loadDeployTimeline(id, commands);
      }, REFRESH_INTERVAL_MS);
    }
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [
    autoRefresh,
    id,
    silentRefresh,
    node?.host,
    loadLogs,
    loadContainerLogs,
    loadDeployTimeline,
    commands,
  ]);

  /* ── derived data ──────────────────────────────────────────────── */

  const util = (node?.latest_utilization ?? {}) as Utilization;
  const inv = (node?.latest_inventory ?? {}) as Inventory;
  const caps = inv.static_capabilities ?? {};

  const cpuPct = util.cpu_percent ?? null;
  const ramPct = util.memory_percent ?? null;
  const diskPct = util.disk_percent ?? null;
  const gpu = util.gpu ?? inv.gpu;
  const gpuVramPct =
    gpu && gpu.total_vram_bytes && gpu.total_vram_bytes > 0
      ? ((gpu.used_vram_bytes ?? 0) / gpu.total_vram_bytes) * 100
      : null;
  const hasGpu =
    (inv.gpu_count ?? gpu?.count ?? 0) > 0 &&
    (gpuVramPct != null || (gpu?.devices?.length ?? 0) > 0);

  const flatLogs = useMemo(() => {
    const lines: { ts: string; line: string; tsMs: number }[] = [];
    for (const stream of logStreams) {
      for (const entry of stream.entries) {
        const ms = new Date(entry.ts).getTime();
        lines.push({
          ts: entry.ts,
          line: entry.line,
          tsMs: Number.isNaN(ms) ? 0 : ms,
        });
      }
    }
    return lines.sort((a, b) => b.tsMs - a.tsMs).slice(0, 100);
  }, [logStreams]);

  const flatContainerLogs = useMemo(() => {
    const lines: {
      ts: string;
      line: string;
      tsMs: number;
      container: string;
    }[] = [];
    for (const stream of containerLogStreams) {
      const container = stream.labels?.container ?? "";
      for (const entry of stream.entries) {
        const ms = new Date(entry.ts).getTime();
        lines.push({
          ts: entry.ts,
          line: entry.line,
          tsMs: Number.isNaN(ms) ? 0 : ms,
          container,
        });
      }
    }
    return lines.sort((a, b) => a.tsMs - b.tsMs).slice(-200);
  }, [containerLogStreams]);

  /* ── render ────────────────────────────────────────────────────── */

  if (loading) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", py: 6 }}>
        <CircularProgress size={28} />
      </Box>
    );
  }

  if (error && !node) {
    return <Alert severity="error">{error}</Alert>;
  }

  if (!node || !id) {
    return <Alert severity="error">{t("nodes.node_not_found")}</Alert>;
  }

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
      {/* ── Header ───────────────────────────────────────────────── */}
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="center"
        flexWrap="wrap"
        useFlexGap
        spacing={1}
      >
        <Stack direction="row" spacing={1} alignItems="center">
          <Tooltip title={t("nodes.back_to_fleet")}>
            <IconButton size="small" onClick={() => navigate("/admin/nodes")}>
              <ArrowBackIcon fontSize="small" />
            </IconButton>
          </Tooltip>
          <Box>
            <Stack direction="row" spacing={1} alignItems="center">
              <Typography variant="h5">{node.host}</Typography>
              <Chip
                size="small"
                color={statusColor(node.status)}
                label={node.status}
              />
              {node.maintenance_mode && (
                <Chip
                  size="small"
                  color="warning"
                  variant="outlined"
                  label={t("nodes.maintenance")}
                />
              )}
              {node.draining && (
                <Chip
                  size="small"
                  color="warning"
                  variant="outlined"
                  label={t("nodes.draining")}
                />
              )}
            </Stack>
            <Typography
              variant="caption"
              color="text.secondary"
              fontFamily="monospace"
            >
              {node.agent_id}
              {node.version ? ` v${node.version}` : ""}
              {" \u00B7 "}seen {relativeTime(node.last_seen)}
            </Typography>
          </Box>
        </Stack>
        <Stack direction="row" spacing={0.5} alignItems="center">
          <Switch
            size="small"
            checked={autoRefresh}
            onChange={(e) => setAutoRefresh(e.target.checked)}
          />
          <Typography variant="caption" color="text.secondary">
            {t("nodes.live")}
          </Typography>
        </Stack>
      </Stack>

      {error && <Alert severity="error">{error}</Alert>}

      {/* ── Controls ─────────────────────────────────────────────── */}
      <Stack
        direction="row"
        spacing={1}
        flexWrap="wrap"
        useFlexGap
        alignItems="center"
      >
        <Tooltip title={t("nodes.maintenance_tooltip")}>
          <span>
            <Button
              size="small"
              variant={node.maintenance_mode ? "contained" : "outlined"}
              color={node.maintenance_mode ? "warning" : "inherit"}
              startIcon={<BuildIcon />}
              disabled={busyKey === "maintenance"}
              onClick={() =>
                runAction("maintenance", async () => {
                  await nodesApi.setMaintenance(id, !node.maintenance_mode);
                })
              }
            >
              {node.maintenance_mode
                ? t("nodes.exit_maintenance")
                : t("nodes.maintenance")}
            </Button>
          </span>
        </Tooltip>
        <Tooltip title={t("nodes.drain_tooltip")}>
          <span>
            <Button
              size="small"
              variant={node.draining ? "contained" : "outlined"}
              color={node.draining ? "warning" : "inherit"}
              startIcon={<InvertColorsOffIcon />}
              disabled={busyKey === "drain"}
              onClick={() =>
                runAction("drain", async () => {
                  await nodesApi.setDrain(id, !node.draining);
                })
              }
            >
              {node.draining ? t("nodes.stop_drain") : t("nodes.drain")}
            </Button>
          </span>
        </Tooltip>
        <Tooltip title={t("nodes.refresh_inventory_tooltip")}>
          <span>
            <Button
              size="small"
              variant="outlined"
              startIcon={<InventoryIcon />}
              disabled={busyKey === "refresh_inventory"}
              onClick={() =>
                runAction("refresh_inventory", async () => {
                  await nodesApi.issueCommand(id, {
                    command_type: "refresh_inventory",
                  });
                })
              }
            >
              {t("nodes.refresh_inventory")}
            </Button>
          </span>
        </Tooltip>
        <Tooltip title={t("nodes.diagnostics_tooltip")}>
          <span>
            <Button
              size="small"
              variant="outlined"
              startIcon={<BugReportIcon />}
              disabled={busyKey === "collect_diagnostics"}
              onClick={() =>
                runAction("collect_diagnostics", async () => {
                  await nodesApi.issueCommand(id, {
                    command_type: "collect_diagnostics",
                  });
                })
              }
            >
              {t("nodes.diagnostics")}
            </Button>
          </span>
        </Tooltip>
        <Tooltip title={t("nodes.manual_refresh_tooltip")}>
          <IconButton size="small" onClick={() => void load()}>
            <RefreshIcon fontSize="small" />
          </IconButton>
        </Tooltip>
        <Tooltip title={t("system_updates.tooltip")}>
          <span>
            <Button
              size="small"
              variant="outlined"
              startIcon={<SystemUpdateAltIcon />}
              onClick={() => setUpdateDialogOpen(true)}
            >
              {t("system_updates.button")}
            </Button>
          </span>
        </Tooltip>
        <Tooltip title="Generate a one-time enrollment token for this node">
          <span>
            <Button
              size="small"
              variant="outlined"
              startIcon={<VpnKeyIcon />}
              disabled={enrollBusy}
              onClick={() => void generateEnrollToken()}
            >
              {enrollBusy ? "Generating..." : "Enrollment Token"}
            </Button>
          </span>
        </Tooltip>
      </Stack>

      {/* ── Profile Assignment ───────────────────────────────────── */}
      <Card variant="outlined">
        <CardContent sx={{ py: 1.5, "&:last-child": { pb: 1.5 } }}>
          <Stack direction="row" spacing={2} alignItems="center">
            <Typography variant="subtitle2" sx={{ minWidth: 100 }}>
              {t("node_profiles.profile")}
            </Typography>
            <Select
              size="small"
              value={node.profile_id ?? ""}
              onChange={(e) => void handleProfileChange(e.target.value)}
              disabled={profileBusy}
              displayEmpty
              sx={{ minWidth: 240 }}
            >
              <MenuItem value="">
                <em>{t("node_profiles.none")}</em>
              </MenuItem>
              {profiles.map((p) => (
                <MenuItem key={p.id} value={p.id}>
                  {p.name}
                  {p.is_default ? ` (${t("node_profiles.default")})` : ""}
                </MenuItem>
              ))}
            </Select>
            {profileBusy && <CircularProgress size={18} />}
            {node.profile_id && (
              <Typography variant="caption" color="text.secondary">
                {profiles.find((p) => p.id === node.profile_id)?.description ??
                  ""}
              </Typography>
            )}
          </Stack>
        </CardContent>
      </Card>

      {/* ── Enrollment Token (shown after generation) ────────────── */}
      <Collapse in={!!enrollToken}>
        {enrollToken && (
          <Card variant="outlined">
            <CardContent sx={{ py: 1.5, "&:last-child": { pb: 1.5 } }}>
              <Stack spacing={1}>
                <Stack direction="row" spacing={1} alignItems="center">
                  <VpnKeyIcon fontSize="small" color="warning" />
                  <Typography variant="subtitle2">Enrollment Token</Typography>
                  <Typography variant="caption" color="text.secondary">
                    expires {new Date(enrollToken.expires_at).toLocaleString()}
                  </Typography>
                </Stack>
                <Alert severity="warning" variant="outlined" sx={{ py: 0.25 }}>
                  Shown once. Copy it now and set it in the agent&apos;s env
                  file.
                </Alert>
                <Stack direction="row" spacing={1} alignItems="center">
                  <TextField
                    value={enrollToken.token}
                    fullWidth
                    size="small"
                    slotProps={{
                      input: {
                        readOnly: true,
                        sx: { fontFamily: "monospace", fontSize: "0.85rem" },
                      },
                    }}
                  />
                  <Tooltip title={enrollCopied ? "Copied!" : "Copy token"}>
                    <IconButton
                      size="small"
                      onClick={() => void copyEnrollToken()}
                    >
                      <ContentCopyIcon fontSize="small" />
                    </IconButton>
                  </Tooltip>
                </Stack>
                <Box
                  component="pre"
                  sx={{
                    fontFamily: "monospace",
                    fontSize: "0.75rem",
                    m: 0,
                    whiteSpace: "pre-wrap",
                    color: "text.secondary",
                  }}
                >
                  {`# On the remote node:
rm ~/.local/share/llmport-agent/state.json
# Add to ~/.config/llmport-agent/agent.env:
LLM_PORT_NODE_AGENT_ENROLLMENT_TOKEN=${enrollToken.token}
# Then restart:
llmport-agent run`}
                </Box>
              </Stack>
            </CardContent>
          </Card>
        )}
      </Collapse>

      {/* ── Performance Gauges ───────────────────────────────────── */}
      <Grid container spacing={2}>
        <Grid size={{ xs: 6, sm: hasGpu ? 3 : 4 }}>
          <GaugeCard
            label={t("nodes.cpu")}
            value={cpuPct}
            detail={t("nodes.cores_logical", {
              physical: inv.cpu_count_physical ?? "?",
              logical: inv.cpu_count_logical ?? "?",
            })}
          />
        </Grid>
        <Grid size={{ xs: 6, sm: hasGpu ? 3 : 4 }}>
          <GaugeCard
            label={t("nodes.memory")}
            value={ramPct}
            detail={`${fmtBytes(util.memory_used_bytes)} / ${fmtBytes(inv.memory_total_bytes)}`}
            secondaryDetail={t("nodes.bytes_free", {
              bytes: fmtBytes(util.memory_available_bytes),
            })}
          />
        </Grid>
        <Grid size={{ xs: 6, sm: hasGpu ? 3 : 4 }}>
          <GaugeCard
            label={t("nodes.disk")}
            value={diskPct}
            detail={`${fmtBytes(util.disk_used_bytes)} / ${fmtBytes(inv.disk_total_bytes)}`}
            secondaryDetail={t("nodes.bytes_free", {
              bytes: fmtBytes(util.disk_free_bytes),
            })}
          />
        </Grid>
        {hasGpu && (
          <Grid size={{ xs: 6, sm: 3 }}>
            <GaugeCard
              label={`${t("nodes.gpu")}${(gpu?.count ?? 1) > 1 ? ` (${gpu?.count})` : ""}`}
              value={gpuVramPct}
              detail={
                gpuVramPct != null
                  ? `${fmtBytes(gpu?.used_vram_bytes)} / ${fmtBytes(gpu?.total_vram_bytes)}`
                  : undefined
              }
              secondaryDetail={
                gpu?.devices?.[0]?.temperature_c != null
                  ? `${gpu.devices[0].temperature_c}\u00B0C`
                  : undefined
              }
            />
          </Grid>
        )}
      </Grid>

      {/* ── Network + GPU Devices ────────────────────────────────── */}
      <Stack direction={{ xs: "column", md: "row" }} spacing={2}>
        {util.network && (
          <Card variant="outlined" sx={{ flex: 1 }}>
            <CardContent sx={{ py: 1.5, "&:last-child": { pb: 1.5 } }}>
              <Typography variant="subtitle2" sx={{ mb: 0.5 }}>
                {t("nodes.network")}
              </Typography>
              <InfoRow
                label={t("nodes.rx")}
                value={fmtBytes(util.network.bytes_recv)}
              />
              <InfoRow
                label={t("nodes.tx")}
                value={fmtBytes(util.network.bytes_sent)}
              />
              {util.network.packets_recv != null && (
                <InfoRow
                  label={t("nodes.packets")}
                  value={`${t("nodes.rx")} ${util.network.packets_recv.toLocaleString()} / ${t("nodes.tx")} ${(util.network.packets_sent ?? 0).toLocaleString()}`}
                />
              )}
            </CardContent>
          </Card>
        )}
        {hasGpu && gpu?.devices && gpu.devices.length > 0 && (
          <Card variant="outlined" sx={{ flex: 1 }}>
            <CardContent sx={{ py: 1.5, "&:last-child": { pb: 1.5 } }}>
              <Typography variant="subtitle2" sx={{ mb: 0.5 }}>
                {t("nodes.gpu_devices")}
              </Typography>
              {gpu.devices.map((dev, i) => (
                <InfoRow
                  key={i}
                  label={`#${dev.index ?? i}`}
                  value={`${dev.utilization_pct ?? 0}% util \u00B7 ${dev.memory_used_mib ?? 0}/${dev.memory_total_mib ?? 0} MiB${dev.temperature_c != null ? ` \u00B7 ${dev.temperature_c}\u00B0C` : ""}`}
                />
              ))}
            </CardContent>
          </Card>
        )}
        <Card variant="outlined" sx={{ flex: 1 }}>
          <CardContent sx={{ py: 1.5, "&:last-child": { pb: 1.5 } }}>
            <Typography variant="subtitle2" sx={{ mb: 0.5 }}>
              {t("nodes.system")}
            </Typography>
            {caps.os && <InfoRow label={t("nodes.os")} value={caps.os} />}
            {caps.machine && (
              <InfoRow
                label={t("nodes.arch")}
                value={`${caps.machine} (${caps.processor ?? ""})`}
              />
            )}
            <InfoRow
              label={t("nodes.docker")}
              value={
                caps.docker_available
                  ? t("nodes.docker_available")
                  : t("nodes.docker_not_detected")
              }
            />
            {(inv.network_interfaces?.length ?? 0) > 0 && (
              <InfoRow
                label={t("nodes.nics")}
                value={
                  inv
                    .network_interfaces!.filter(
                      (n) => !n.startsWith("veth") && !n.startsWith("br-"),
                    )
                    .join(", ") ||
                  t("nodes.interfaces_count", {
                    count: inv.network_interfaces!.length,
                  })
                }
              />
            )}
          </CardContent>
        </Card>
      </Stack>

      {/* ── System Logs ──────────────────────────────────────────── */}
      <Card variant="outlined">
        <CardContent sx={{ py: 1.5, "&:last-child": { pb: 1.5 } }}>
          <Stack
            direction="row"
            justifyContent="space-between"
            alignItems="center"
          >
            <Stack direction="row" spacing={1} alignItems="center">
              <Typography variant="subtitle2">
                {t("nodes.system_logs")}
              </Typography>
              <Typography variant="caption" color="text.secondary">
                {t("nodes.logs_last_15_min")} \u00B7{" "}
                {t("nodes.logs_entries", { count: flatLogs.length })}
              </Typography>
            </Stack>
            <IconButton
              size="small"
              onClick={() => setLogsExpanded((v) => !v)}
              sx={{
                transform: logsExpanded ? "rotate(180deg)" : "rotate(0deg)",
                transition: "transform 0.2s",
              }}
            >
              <ExpandMoreIcon fontSize="small" />
            </IconButton>
          </Stack>
          <Collapse in={logsExpanded}>
            {flatLogs.length === 0 ? (
              <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
                {t("nodes.no_logs_for_host", { host: node.host })}
              </Typography>
            ) : (
              <Box
                sx={{
                  mt: 1,
                  maxHeight: 320,
                  overflow: "auto",
                  fontFamily: "monospace",
                  fontSize: "0.75rem",
                  lineHeight: 1.6,
                  bgcolor: "action.hover",
                  borderRadius: 1,
                  p: 1,
                }}
              >
                {flatLogs.map((entry, i) => (
                  <Box
                    key={i}
                    sx={{ whiteSpace: "pre-wrap", wordBreak: "break-all" }}
                  >
                    <Typography
                      component="span"
                      sx={{
                        fontSize: "inherit",
                        fontFamily: "inherit",
                        color: "text.secondary",
                        mr: 1,
                      }}
                    >
                      {new Date(entry.ts).toLocaleTimeString()}
                    </Typography>
                    {entry.line}
                  </Box>
                ))}
              </Box>
            )}
          </Collapse>
        </CardContent>
      </Card>

      {/* ── Latest Deployment Progress ──────────────────────────── */}
      {deployTimeline && (
        <Card variant="outlined">
          <CardContent sx={{ py: 1.5, "&:last-child": { pb: 1.5 } }}>
            <Typography variant="subtitle2" sx={{ mb: 1 }}>
              Deployment Progress
              <Typography
                component="span"
                variant="caption"
                color="text.secondary"
                sx={{ ml: 1 }}
              >
                {deployTimeline.command.command_type} &middot;{" "}
                {deployTimeline.command.status}
              </Typography>
            </Typography>
            <NodeDeploymentProgress timeline={deployTimeline} />
          </CardContent>
        </Card>
      )}

      {/* ── Container Logs ──────────────────────────────────────── */}
      <Card variant="outlined">
        <CardContent sx={{ py: 1.5, "&:last-child": { pb: 1.5 } }}>
          <Stack
            direction="row"
            justifyContent="space-between"
            alignItems="center"
          >
            <Stack direction="row" spacing={1} alignItems="center">
              <Typography variant="subtitle2">Container Logs</Typography>
              <Typography variant="caption" color="text.secondary">
                last 15 min &middot; {flatContainerLogs.length} entries
              </Typography>
            </Stack>
            <IconButton
              size="small"
              onClick={() => setContainerLogsExpanded((v) => !v)}
              sx={{
                transform: containerLogsExpanded
                  ? "rotate(180deg)"
                  : "rotate(0deg)",
                transition: "transform 0.2s",
              }}
            >
              <ExpandMoreIcon fontSize="small" />
            </IconButton>
          </Stack>
          <Collapse in={containerLogsExpanded}>
            {flatContainerLogs.length === 0 ? (
              <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
                No container logs found for host {node.host}
              </Typography>
            ) : (
              <Box
                sx={{
                  mt: 1,
                  maxHeight: 400,
                  overflow: "auto",
                  fontFamily: "monospace",
                  fontSize: "0.75rem",
                  lineHeight: 1.6,
                  bgcolor: "grey.900",
                  color: "grey.100",
                  borderRadius: 1,
                  p: 1,
                }}
              >
                {flatContainerLogs.map((entry, i) => (
                  <Box
                    key={i}
                    sx={{ whiteSpace: "pre-wrap", wordBreak: "break-all" }}
                  >
                    <Typography
                      component="span"
                      sx={{
                        fontSize: "inherit",
                        fontFamily: "inherit",
                        color: "grey.500",
                        mr: 1,
                      }}
                    >
                      {new Date(entry.ts).toLocaleTimeString()}
                    </Typography>
                    {entry.container && (
                      <Typography
                        component="span"
                        sx={{
                          fontSize: "inherit",
                          fontFamily: "inherit",
                          color: "info.main",
                          mr: 1,
                        }}
                      >
                        [{entry.container}]
                      </Typography>
                    )}
                    {entry.line}
                  </Box>
                ))}
              </Box>
            )}
          </Collapse>
        </CardContent>
      </Card>

      {/* ── Commands ─────────────────────────────────────────────── */}
      <Card variant="outlined">
        <CardContent sx={{ py: 1.5, "&:last-child": { pb: 1.5 } }}>
          <Stack
            direction="row"
            justifyContent="space-between"
            alignItems="center"
          >
            <Stack direction="row" spacing={1} alignItems="center">
              <Typography variant="subtitle2">{t("nodes.commands")}</Typography>
              <Typography variant="caption" color="text.secondary">
                {t("nodes.commands_total", { count: sortedCommands.length })}
              </Typography>
            </Stack>
            <IconButton
              size="small"
              onClick={() =>
                setCommandsExpanded((v) => {
                  if (v) setTimeline(null);
                  return !v;
                })
              }
              sx={{
                transform: commandsExpanded ? "rotate(180deg)" : "rotate(0deg)",
                transition: "transform 0.2s",
              }}
            >
              <ExpandMoreIcon fontSize="small" />
            </IconButton>
          </Stack>
          <Collapse in={commandsExpanded}>
            {sortedCommands.length === 0 ? (
              <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
                {t("nodes.no_commands_yet")}
              </Typography>
            ) : (
              <Stack spacing={0.5} sx={{ mt: 1 }}>
                {sortedCommands.map((command) => (
                  <Box
                    key={command.id}
                    sx={{
                      border: 1,
                      borderColor: "divider",
                      borderRadius: 1,
                      px: 1.5,
                      py: 0.75,
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      gap: 1,
                    }}
                  >
                    <Box sx={{ minWidth: 0 }}>
                      <Typography variant="body2" fontWeight={600}>
                        {command.command_type}
                      </Typography>
                      <Typography variant="caption" color="text.secondary">
                        {new Date(command.issued_at).toLocaleString()}
                        {" \u00B7 "}
                        {command.status}
                      </Typography>
                    </Box>
                    <Button
                      size="small"
                      variant="text"
                      disabled={busyKey === `timeline:${command.id}`}
                      onClick={() => loadTimeline(command.id)}
                    >
                      {t("nodes.timeline")}
                    </Button>
                  </Box>
                ))}
              </Stack>
            )}

            {timeline && (
              <Box sx={{ mt: 1.5 }}>
                <Typography variant="subtitle2" sx={{ mb: 0.5 }}>
                  {t("nodes.timeline")}: {timeline.command.command_type}
                </Typography>
                <Stack spacing={0.5}>
                  {timeline.events.map((event) => (
                    <Box
                      key={`${event.seq}:${event.ts}`}
                      sx={{ borderLeft: 2, borderColor: "divider", pl: 1.5 }}
                    >
                      <Typography variant="body2" fontWeight={600}>
                        #{event.seq} {event.phase}
                      </Typography>
                      <Typography variant="caption" color="text.secondary">
                        {new Date(event.ts).toLocaleString()}
                      </Typography>
                      <Typography variant="body2">{event.message}</Typography>
                    </Box>
                  ))}
                </Stack>
              </Box>
            )}
          </Collapse>
        </CardContent>
      </Card>

      {/* ── Raw Inventory (collapsible) ──────────────────────────── */}
      <Card variant="outlined">
        <CardContent sx={{ py: 1.5, "&:last-child": { pb: 1.5 } }}>
          <Stack
            direction="row"
            justifyContent="space-between"
            alignItems="center"
          >
            <Typography variant="subtitle2">
              {t("nodes.raw_inventory_utilization")}
            </Typography>
            <IconButton
              size="small"
              onClick={() => setInventoryExpanded((v) => !v)}
              sx={{
                transform: inventoryExpanded
                  ? "rotate(180deg)"
                  : "rotate(0deg)",
                transition: "transform 0.2s",
              }}
            >
              <ExpandMoreIcon fontSize="small" />
            </IconButton>
          </Stack>
          <Collapse in={inventoryExpanded}>
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ mt: 1, display: "block" }}
            >
              {t("nodes.inventory")}
            </Typography>
            <Typography
              component="pre"
              sx={{
                m: 0,
                fontSize: "0.72rem",
                fontFamily: "monospace",
                whiteSpace: "pre-wrap",
                bgcolor: "action.hover",
                borderRadius: 1,
                p: 1,
              }}
            >
              {JSON.stringify(node.latest_inventory ?? {}, null, 2)}
            </Typography>
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ mt: 1, display: "block" }}
            >
              {t("nodes.utilization")}
            </Typography>
            <Typography
              component="pre"
              sx={{
                m: 0,
                fontSize: "0.72rem",
                fontFamily: "monospace",
                whiteSpace: "pre-wrap",
                bgcolor: "action.hover",
                borderRadius: 1,
                p: 1,
              }}
            >
              {JSON.stringify(node.latest_utilization ?? {}, null, 2)}
            </Typography>
          </Collapse>
        </CardContent>
      </Card>

      {/* ── System Update Dialog ──────────────────────────────── */}
      <SystemUpdateDialog
        open={updateDialogOpen}
        nodeId={id}
        onClose={() => setUpdateDialogOpen(false)}
      />
    </Box>
  );
}

/* ── sub-components ─────────────────────────────────────────────────────── */

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <Stack direction="row" spacing={1}>
      <Typography
        variant="caption"
        color="text.secondary"
        sx={{ width: 56, flexShrink: 0, fontWeight: 600 }}
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
