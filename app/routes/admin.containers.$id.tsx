/**
 * Admin → Container detail page.
 * Shows inspect data, live logs, and exec terminal token.
 */
import { useState, useEffect, useRef } from "react";
import { useParams, useOutletContext } from "react-router";
import {
  containers,
  canExec,
  canStop,
  canDelete,
  type ContainerDetail,
} from "~/api/admin";

import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import Grid from "@mui/material/Grid";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import Tab from "@mui/material/Tab";
import Tabs from "@mui/material/Tabs";
import Typography from "@mui/material/Typography";
import Alert from "@mui/material/Alert";
import CircularProgress from "@mui/material/CircularProgress";
import Divider from "@mui/material/Divider";

import RefreshIcon from "@mui/icons-material/Refresh";
import TerminalIcon from "@mui/icons-material/Terminal";

interface AdminContext {
  rootModeActive: boolean;
}

export default function ContainerDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { rootModeActive } = useOutletContext<AdminContext>();
  const [detail, setDetail] = useState<ContainerDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState(0);
  const [logs, setLogs] = useState<string>("");
  const [logLoading, setLogLoading] = useState(false);
  const [execId, setExecId] = useState<string | null>(null);
  const [execError, setExecError] = useState<string | null>(null);
  const logRef = useRef<HTMLPreElement>(null);

  async function load() {
    if (!id) return;
    setLoading(true);
    setError(null);
    try {
      setDetail(await containers.get(id));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load container.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, [id]);

  async function fetchLogs() {
    if (!id) return;
    setLogLoading(true);
    try {
      const res = await containers.fetchLogs(id, 300);
      const text = await res.text();
      setLogs(text);
      setTimeout(() => logRef.current?.scrollTo(0, logRef.current.scrollHeight), 50);
    } catch (e: unknown) {
      setLogs(`Error loading logs: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setLogLoading(false);
    }
  }

  useEffect(() => {
    if (tab === 1) fetchLogs();
  }, [tab]);

  async function handleCreateExec() {
    if (!id) return;
    setExecError(null);
    try {
      const token = await containers.exec(id, ["/bin/sh"]);
      setExecId(token.exec_id);
    } catch (e: unknown) {
      setExecError(e instanceof Error ? e.message : "Exec denied.");
    }
  }

  if (loading) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", py: 6 }}>
        <CircularProgress size={32} />
      </Box>
    );
  }
  if (error) return <Alert severity="error">{error}</Alert>;
  if (!detail) return null;

  const cls = detail.container_class;

  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}>
      {/* Fixed header */}
      <Box sx={{ flexShrink: 0 }}>
        <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ mb: 2 }}>
          <Box>
            <Stack direction="row" alignItems="center" spacing={1.5}>
              <Typography variant="h5">{detail.name}</Typography>
              <Chip
                label={cls}
                size="small"
                color={cls === "SYSTEM_CORE" ? "error" : cls === "SYSTEM_AUX" ? "warning" : cls === "TENANT_APP" ? "success" : "default"}
              />
            </Stack>
            <Typography variant="caption" color="text.secondary" fontFamily="monospace">
              {detail.id.slice(0, 64)}
            </Typography>
          </Box>
        </Stack>

        {/* Tabs */}
        <Paper variant="outlined" sx={{ mb: 2 }}>
          <Tabs value={tab} onChange={(_, v) => setTab(v)}>
            <Tab label="Overview" />
            <Tab label="Logs" />
            <Tab label="Exec" />
          </Tabs>
        </Paper>
      </Box>

      {/* Scrollable content area */}
      <Box sx={{ flexGrow: 1, overflow: "auto" }}>

      {/* Overview */}
      {tab === 0 && (
        <Grid container spacing={2}>
          <Grid size={{ xs: 12, md: 6 }}>
            <Paper variant="outlined" sx={{ p: 3 }}>
              <Typography variant="overline" color="text.secondary" gutterBottom>
                Status
              </Typography>
              <Divider sx={{ mb: 2 }} />
              {[
                ["State", detail.state],
                ["Image", detail.image],
                ["Created", new Date(detail.created).toLocaleString()],
                ["Policy", detail.policy],
                ["Owner Scope", detail.owner_scope],
              ].map(([label, value]) => (
                <Stack
                  key={label}
                  direction="row"
                  justifyContent="space-between"
                  sx={{ py: 0.5 }}
                >
                  <Typography variant="body2" color="text.secondary">
                    {label}
                  </Typography>
                  <Typography variant="body2" fontFamily={label === "Image" ? "monospace" : undefined} fontSize={label === "Image" ? "0.8rem" : undefined}>
                    {value}
                  </Typography>
                </Stack>
              ))}
            </Paper>
          </Grid>
          <Grid size={{ xs: 12, md: 6 }}>
            <Paper variant="outlined" sx={{ p: 3 }}>
              <Typography variant="overline" color="text.secondary" gutterBottom>
                Networks
              </Typography>
              <Divider sx={{ mb: 2 }} />
              {detail.networks.length === 0 ? (
                <Typography variant="body2" color="text.secondary">None</Typography>
              ) : (
                <Stack spacing={0.5}>
                  {detail.networks.map((n) => (
                    <Typography key={n} variant="body2" fontFamily="monospace" fontSize="0.8rem">
                      {n}
                    </Typography>
                  ))}
                </Stack>
              )}
            </Paper>
          </Grid>
        </Grid>
      )}

      {/* Logs */}
      {tab === 1 && (
        <Box>
          <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ mb: 1 }}>
            <Typography variant="caption" color="text.secondary">
              Last 300 lines
            </Typography>
            <Button
              size="small"
              variant="outlined"
              startIcon={<RefreshIcon />}
              onClick={fetchLogs}
              disabled={logLoading}
            >
              {logLoading ? "Loading…" : "Refresh"}
            </Button>
          </Stack>
          <Paper
            variant="outlined"
            sx={{
              bgcolor: "#0d1117",
              p: 2,
              height: 420,
              overflow: "auto",
              borderRadius: 2,
            }}
          >
            <Box
              component="pre"
              ref={logRef}
              sx={{
                m: 0,
                fontFamily: "monospace",
                fontSize: "0.75rem",
                color: "#69f0ae",
                whiteSpace: "pre-wrap",
                wordBreak: "break-all",
              }}
            >
              {logs || "No logs yet."}
            </Box>
          </Paper>
        </Box>
      )}

      {/* Exec */}
      {tab === 2 && (
        <Paper variant="outlined" sx={{ p: 4 }}>
          {!canExec(cls, rootModeActive) ? (
            <Alert severity="warning">
              Exec is not permitted for {cls} containers without Root Mode.
            </Alert>
          ) : execId ? (
            <Box>
              <Alert severity="success" sx={{ mb: 2 }}>
                Exec session created.
              </Alert>
              <Typography variant="caption" color="text.secondary">
                Exec ID (use with your websocket client):
              </Typography>
              <Paper
                variant="outlined"
                sx={{ p: 1.5, mt: 0.5, fontFamily: "monospace", fontSize: "0.8rem", wordBreak: "break-all" }}
              >
                {execId}
              </Paper>
            </Box>
          ) : (
            <Box>
              <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                Create an exec session into this container. The returned exec ID
                can be used with a terminal websocket client.
              </Typography>
              <Button
                variant="contained"
                startIcon={<TerminalIcon />}
                onClick={handleCreateExec}
              >
                Start Exec (/bin/sh)
              </Button>
              {execError && (
                <Alert severity="error" sx={{ mt: 2 }}>
                  {execError}
                </Alert>
              )}
            </Box>
          )}
        </Paper>
      )}
      </Box>
    </Box>
  );
}
