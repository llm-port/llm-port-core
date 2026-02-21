/**
 * Admin → Containers list page.
 * MUI-based table with class badges and quick-action buttons.
 */
import { useState, useEffect } from "react";
import { Link as RouterLink, useOutletContext } from "react-router";
import {
  containers,
  canStop,
  canDelete,
  canPause,
  type ContainerSummary,
  type ContainerClass,
} from "~/api/admin";

import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import FormControl from "@mui/material/FormControl";
import IconButton from "@mui/material/IconButton";
import InputLabel from "@mui/material/InputLabel";
import Link from "@mui/material/Link";
import MenuItem from "@mui/material/MenuItem";
import Paper from "@mui/material/Paper";
import Select from "@mui/material/Select";
import Stack from "@mui/material/Stack";
import Table from "@mui/material/Table";
import TableBody from "@mui/material/TableBody";
import TableCell from "@mui/material/TableCell";
import TableContainer from "@mui/material/TableContainer";
import TableHead from "@mui/material/TableHead";
import TableRow from "@mui/material/TableRow";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import Alert from "@mui/material/Alert";
import CircularProgress from "@mui/material/CircularProgress";

import RefreshIcon from "@mui/icons-material/Refresh";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import StopIcon from "@mui/icons-material/Stop";
import RestartAltIcon from "@mui/icons-material/RestartAlt";
import PauseIcon from "@mui/icons-material/Pause";
import PlayCircleOutlineIcon from "@mui/icons-material/PlayCircleOutline";
import DeleteIcon from "@mui/icons-material/Delete";
import OpenInNewIcon from "@mui/icons-material/OpenInNew";

interface AdminContext {
  rootModeActive: boolean;
}

const CLASS_CHIP: Record<ContainerClass, { color: "error" | "warning" | "success" | "default"; label: string }> = {
  SYSTEM_CORE: { color: "error", label: "SYSTEM_CORE" },
  SYSTEM_AUX: { color: "warning", label: "SYSTEM_AUX" },
  TENANT_APP: { color: "success", label: "TENANT_APP" },
  UNTRUSTED: { color: "default", label: "UNTRUSTED" },
};

function stateColor(state: string): "success" | "default" | "warning" | "info" {
  const s = state.toLowerCase();
  if (s === "running") return "success";
  if (s === "exited") return "default";
  if (s === "paused") return "warning";
  return "info";
}

export default function ContainersPage() {
  const { rootModeActive } = useOutletContext<AdminContext>();
  const [data, setData] = useState<ContainerSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filterClass, setFilterClass] = useState<ContainerClass | "">("");
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      setData(await containers.list(filterClass || undefined));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load containers.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, [filterClass]);

  async function handleAction(id: string, action: "start" | "stop" | "restart" | "pause" | "unpause") {
    setActionLoading(`${id}-${action}`);
    try {
      await containers.lifecycle(id, action);
      await load();
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : "Action failed.");
    } finally {
      setActionLoading(null);
    }
  }

  async function handleDelete(id: string) {
    if (!confirm("Delete this container?")) return;
    setActionLoading(`${id}-delete`);
    try {
      await containers.delete(id);
      await load();
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : "Delete failed.");
    } finally {
      setActionLoading(null);
    }
  }

  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}>
      {/* Fixed header area */}
      <Box sx={{ flexShrink: 0 }}>
        <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ mb: 2 }}>
          <Typography variant="h5">Containers</Typography>
          <Stack direction="row" spacing={1.5} alignItems="center">
            <FormControl size="small" sx={{ minWidth: 160 }}>
              <InputLabel>Class</InputLabel>
              <Select
                value={filterClass}
                label="Class"
                onChange={(e) => setFilterClass(e.target.value as ContainerClass | "")}
              >
                <MenuItem value="">All classes</MenuItem>
                <MenuItem value="SYSTEM_CORE">SYSTEM_CORE</MenuItem>
                <MenuItem value="SYSTEM_AUX">SYSTEM_AUX</MenuItem>
                <MenuItem value="TENANT_APP">TENANT_APP</MenuItem>
                <MenuItem value="UNTRUSTED">UNTRUSTED</MenuItem>
              </Select>
            </FormControl>
            <Tooltip title="Refresh">
              <IconButton onClick={load} size="small">
                <RefreshIcon />
              </IconButton>
            </Tooltip>
          </Stack>
        </Stack>
      </Box>

      {loading && (
        <Box sx={{ display: "flex", justifyContent: "center", py: 6 }}>
          <CircularProgress size={32} />
        </Box>
      )}
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}

      {!loading && !error && (
        <TableContainer component={Paper} variant="outlined" sx={{ flexGrow: 1, overflow: "auto" }}>
          <Table size="small" stickyHeader>
            <TableHead>
              <TableRow>
                <TableCell>Name</TableCell>
                <TableCell>Image</TableCell>
                <TableCell>State</TableCell>
                <TableCell>Class</TableCell>
                <TableCell>Scope</TableCell>
                <TableCell align="right">Actions</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {data.length === 0 && (
                <TableRow>
                  <TableCell colSpan={6} align="center" sx={{ py: 4, color: "text.secondary" }}>
                    No containers found.
                  </TableCell>
                </TableRow>
              )}
              {data.map((c) => {
                const busy = !!actionLoading?.startsWith(c.id);
                const isRunning = c.state.toLowerCase() === "running";
                const isPaused = c.state.toLowerCase() === "paused";
                const chipConf = CLASS_CHIP[c.container_class];
                return (
                  <TableRow key={c.id}>
                    <TableCell>
                      <Link
                        component={RouterLink}
                        to={`/admin/containers/${c.id}`}
                        underline="hover"
                        color="primary.light"
                        fontWeight={600}
                        sx={{ fontFamily: "monospace", fontSize: "0.8rem" }}
                      >
                        {c.name}
                      </Link>
                      <Typography variant="caption" display="block" color="text.secondary" fontFamily="monospace">
                        {c.id.slice(0, 12)}
                      </Typography>
                    </TableCell>
                    <TableCell>
                      <Typography variant="body2" fontFamily="monospace" fontSize="0.8rem">
                        {c.image}
                      </Typography>
                    </TableCell>
                    <TableCell>
                      <Chip label={c.state} size="small" color={stateColor(c.state)} variant="outlined" />
                      <Typography variant="caption" display="block" color="text.secondary">
                        {c.status}
                      </Typography>
                    </TableCell>
                    <TableCell>
                      <Chip label={chipConf.label} size="small" color={chipConf.color} />
                    </TableCell>
                    <TableCell>
                      <Typography variant="body2" color="text.secondary" fontSize="0.8rem">
                        {c.owner_scope}
                      </Typography>
                    </TableCell>
                    <TableCell align="right">
                      <Stack direction="row" spacing={0.5} justifyContent="flex-end">
                        {!isRunning && !isPaused && (
                          <Tooltip title="Start">
                            <IconButton size="small" color="success" disabled={busy} onClick={() => handleAction(c.id, "start")}>
                              <PlayArrowIcon fontSize="small" />
                            </IconButton>
                          </Tooltip>
                        )}
                        {(isRunning || isPaused) && canStop(c.container_class, rootModeActive) && (
                          <Tooltip title="Stop">
                            <IconButton size="small" color="warning" disabled={busy} onClick={() => handleAction(c.id, "stop")}>
                              <StopIcon fontSize="small" />
                            </IconButton>
                          </Tooltip>
                        )}
                        {isRunning && canPause(c.container_class, rootModeActive) && (
                          <Tooltip title="Pause">
                            <IconButton size="small" color="info" disabled={busy} onClick={() => handleAction(c.id, "pause")}>
                              <PauseIcon fontSize="small" />
                            </IconButton>
                          </Tooltip>
                        )}
                        {isPaused && (
                          <Tooltip title="Unpause">
                            <IconButton size="small" color="success" disabled={busy} onClick={() => handleAction(c.id, "unpause")}>
                              <PlayCircleOutlineIcon fontSize="small" />
                            </IconButton>
                          </Tooltip>
                        )}
                        {isRunning && (
                          <Tooltip title="Restart">
                            <IconButton size="small" disabled={busy} onClick={() => handleAction(c.id, "restart")}>
                              <RestartAltIcon fontSize="small" />
                            </IconButton>
                          </Tooltip>
                        )}
                        {canDelete(c.container_class, rootModeActive) && (
                          <Tooltip title="Delete">
                            <IconButton size="small" color="error" disabled={busy} onClick={() => handleDelete(c.id)}>
                              <DeleteIcon fontSize="small" />
                            </IconButton>
                          </Tooltip>
                        )}
                        <Tooltip title="Details">
                          <IconButton size="small" component={RouterLink} to={`/admin/containers/${c.id}`}>
                            <OpenInNewIcon fontSize="small" />
                          </IconButton>
                        </Tooltip>
                      </Stack>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </TableContainer>
      )}
    </Box>
  );
}
