/**
 * Admin → Networks page.
 * Lists Docker networks, allows creating/deleting user networks, and
 * shows system networks as read-only (stats only).
 */
import { useState, useEffect } from "react";
import {
  networks,
  type NetworkSummary,
  type NetworkDetail,
  type CreateNetworkPayload,
} from "~/api/admin";

import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import Divider from "@mui/material/Divider";
import FormControlLabel from "@mui/material/FormControlLabel";
import IconButton from "@mui/material/IconButton";
import List from "@mui/material/List";
import ListItem from "@mui/material/ListItem";
import ListItemText from "@mui/material/ListItemText";
import MenuItem from "@mui/material/MenuItem";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import Switch from "@mui/material/Switch";
import Table from "@mui/material/Table";
import TableBody from "@mui/material/TableBody";
import TableCell from "@mui/material/TableCell";
import TableContainer from "@mui/material/TableContainer";
import TableHead from "@mui/material/TableHead";
import TableRow from "@mui/material/TableRow";
import TextField from "@mui/material/TextField";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import Alert from "@mui/material/Alert";
import CircularProgress from "@mui/material/CircularProgress";

import RefreshIcon from "@mui/icons-material/Refresh";
import AddIcon from "@mui/icons-material/Add";
import DeleteIcon from "@mui/icons-material/Delete";
import InfoOutlinedIcon from "@mui/icons-material/InfoOutlined";
import LockIcon from "@mui/icons-material/Lock";
import HubIcon from "@mui/icons-material/Hub";

export default function NetworksPage() {
  const [data, setData] = useState<NetworkSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Create dialog
  const [showCreate, setShowCreate] = useState(false);
  const [createForm, setCreateForm] = useState<CreateNetworkPayload>({
    name: "",
    driver: "bridge",
    internal: false,
    subnet: null,
    gateway: null,
    labels: {},
  });
  const [createError, setCreateError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  // Inspect dialog
  const [inspectNet, setInspectNet] = useState<NetworkDetail | null>(null);
  const [inspectLoading, setInspectLoading] = useState(false);

  // Delete state
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      setData(await networks.list());
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load networks.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setCreateError(null);
    setCreating(true);
    try {
      const payload: CreateNetworkPayload = {
        name: createForm.name.trim(),
        driver: createForm.driver || "bridge",
        internal: createForm.internal,
      };
      if (createForm.subnet) payload.subnet = createForm.subnet;
      if (createForm.gateway) payload.gateway = createForm.gateway;
      await networks.create(payload);
      setShowCreate(false);
      setCreateForm({ name: "", driver: "bridge", internal: false, subnet: null, gateway: null, labels: {} });
      await load();
    } catch (err: unknown) {
      setCreateError(err instanceof Error ? err.message : "Create failed.");
    } finally {
      setCreating(false);
    }
  }

  async function handleDelete(id: string, name: string) {
    if (!confirm(`Delete network "${name}"?`)) return;
    setActionLoading(id);
    try {
      await networks.delete(id);
      await load();
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : "Delete failed.");
    } finally {
      setActionLoading(null);
    }
  }

  async function handleInspect(id: string) {
    setInspectLoading(true);
    try {
      const detail = await networks.get(id);
      setInspectNet(detail);
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : "Failed to inspect network.");
    } finally {
      setInspectLoading(false);
    }
  }

  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}>
      {/* Fixed header */}
      <Box sx={{ flexShrink: 0 }}>
        <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ mb: 2 }}>
          <Stack direction="row" alignItems="center" spacing={1}>
            <HubIcon color="primary" />
            <Typography variant="h5">Networks</Typography>
          </Stack>
          <Stack direction="row" spacing={1.5} alignItems="center">
            <Button
              variant="contained"
              size="small"
              startIcon={<AddIcon />}
              onClick={() => setShowCreate(true)}
            >
              Create Network
            </Button>
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
                <TableCell>Driver</TableCell>
                <TableCell>Scope</TableCell>
                <TableCell align="center">Internal</TableCell>
                <TableCell align="center">Containers</TableCell>
                <TableCell align="center">Type</TableCell>
                <TableCell align="right">Actions</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {data.length === 0 && (
                <TableRow>
                  <TableCell colSpan={7} align="center" sx={{ py: 4, color: "text.secondary" }}>
                    No networks found.
                  </TableCell>
                </TableRow>
              )}
              {data.map((n) => {
                const busy = actionLoading === n.id;
                return (
                  <TableRow key={n.id} hover>
                    <TableCell>
                      <Typography
                        variant="body2"
                        fontWeight={600}
                        fontFamily="monospace"
                        fontSize="0.85rem"
                        sx={{ color: "primary.light" }}
                      >
                        {n.name}
                      </Typography>
                      <Typography variant="caption" color="text.secondary" fontFamily="monospace">
                        {n.id.slice(0, 12)}
                      </Typography>
                    </TableCell>
                    <TableCell>
                      <Chip label={n.driver || "—"} size="small" variant="outlined" />
                    </TableCell>
                    <TableCell>
                      <Typography variant="body2" fontSize="0.85rem">{n.scope}</Typography>
                    </TableCell>
                    <TableCell align="center">
                      {n.internal ? (
                        <Chip label="Yes" size="small" color="info" variant="outlined" />
                      ) : (
                        <Typography variant="body2" color="text.secondary">—</Typography>
                      )}
                    </TableCell>
                    <TableCell align="center">
                      <Chip
                        label={n.container_count}
                        size="small"
                        color={n.container_count > 0 ? "primary" : "default"}
                        variant="outlined"
                      />
                    </TableCell>
                    <TableCell align="center">
                      {n.is_system ? (
                        <Chip
                          icon={<LockIcon />}
                          label="System"
                          size="small"
                          color="warning"
                          variant="filled"
                        />
                      ) : (
                        <Chip label="User" size="small" color="success" variant="outlined" />
                      )}
                    </TableCell>
                    <TableCell align="right">
                      <Stack direction="row" spacing={0.5} justifyContent="flex-end">
                        <Tooltip title="Inspect">
                          <IconButton
                            size="small"
                            onClick={() => handleInspect(n.id)}
                            disabled={inspectLoading}
                          >
                            <InfoOutlinedIcon fontSize="small" />
                          </IconButton>
                        </Tooltip>
                        {!n.is_system && (
                          <Tooltip title="Delete">
                            <IconButton
                              size="small"
                              color="error"
                              onClick={() => handleDelete(n.id, n.name)}
                              disabled={busy}
                            >
                              <DeleteIcon fontSize="small" />
                            </IconButton>
                          </Tooltip>
                        )}
                      </Stack>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </TableContainer>
      )}

      {/* ── Create Network Dialog ────────────────────────────────── */}
      <Dialog
        open={showCreate}
        onClose={() => { setShowCreate(false); setCreateError(null); }}
        maxWidth="sm"
        fullWidth
      >
        <form onSubmit={handleCreate}>
          <DialogTitle>Create Network</DialogTitle>
          <DialogContent sx={{ display: "flex", flexDirection: "column", gap: 2, pt: "8px !important" }}>
            <TextField
              label="Network Name"
              required
              fullWidth
              value={createForm.name}
              onChange={(e) => setCreateForm((f) => ({ ...f, name: e.target.value }))}
              inputProps={{ minLength: 1, maxLength: 256 }}
              autoFocus
            />
            <TextField
              select
              label="Driver"
              fullWidth
              value={createForm.driver}
              onChange={(e) => setCreateForm((f) => ({ ...f, driver: e.target.value }))}
            >
              <MenuItem value="bridge">bridge</MenuItem>
              <MenuItem value="overlay">overlay</MenuItem>
              <MenuItem value="macvlan">macvlan</MenuItem>
              <MenuItem value="ipvlan">ipvlan</MenuItem>
            </TextField>
            <FormControlLabel
              control={
                <Switch
                  checked={createForm.internal}
                  onChange={(e) => setCreateForm((f) => ({ ...f, internal: e.target.checked }))}
                />
              }
              label="Internal (isolated, no outbound access)"
            />
            <Divider sx={{ my: 0.5 }} />
            <Typography variant="subtitle2" color="text.secondary">
              IPAM Configuration (optional)
            </Typography>
            <TextField
              label="Subnet"
              fullWidth
              placeholder="e.g. 172.28.0.0/16"
              value={createForm.subnet ?? ""}
              onChange={(e) => setCreateForm((f) => ({ ...f, subnet: e.target.value || null }))}
            />
            <TextField
              label="Gateway"
              fullWidth
              placeholder="e.g. 172.28.0.1"
              value={createForm.gateway ?? ""}
              onChange={(e) => setCreateForm((f) => ({ ...f, gateway: e.target.value || null }))}
            />
            {createError && <Alert severity="error">{createError}</Alert>}
          </DialogContent>
          <DialogActions>
            <Button onClick={() => { setShowCreate(false); setCreateError(null); }}>Cancel</Button>
            <Button type="submit" variant="contained" disabled={creating || !createForm.name.trim()}>
              {creating ? "Creating…" : "Create"}
            </Button>
          </DialogActions>
        </form>
      </Dialog>

      {/* ── Inspect Network Dialog ───────────────────────────────── */}
      <Dialog
        open={!!inspectNet}
        onClose={() => setInspectNet(null)}
        maxWidth="md"
        fullWidth
      >
        {inspectNet && (
          <>
            <DialogTitle sx={{ display: "flex", alignItems: "center", gap: 1 }}>
              <HubIcon color="primary" />
              {inspectNet.name}
              {inspectNet.is_system && (
                <Chip icon={<LockIcon />} label="System" size="small" color="warning" sx={{ ml: 1 }} />
              )}
            </DialogTitle>
            <DialogContent>
              <Stack spacing={2}>
                {/* Key/value info */}
                <Paper variant="outlined" sx={{ p: 2 }}>
                  <Typography variant="subtitle2" color="text.secondary" gutterBottom>
                    Details
                  </Typography>
                  <Stack spacing={0.5}>
                    {[
                      ["ID", inspectNet.id],
                      ["Driver", inspectNet.driver],
                      ["Scope", inspectNet.scope],
                      ["Subnet", inspectNet.subnet || "—"],
                      ["Gateway", inspectNet.gateway || "—"],
                      ["Internal", inspectNet.internal ? "Yes" : "No"],
                      ["Created", inspectNet.created],
                    ].map(([k, v]) => (
                      <Stack key={k} direction="row" spacing={2}>
                        <Typography
                          variant="body2"
                          fontWeight={600}
                          sx={{ minWidth: 100, color: "text.secondary" }}
                        >
                          {k}
                        </Typography>
                        <Typography variant="body2" fontFamily="monospace" fontSize="0.85rem">
                          {v}
                        </Typography>
                      </Stack>
                    ))}
                  </Stack>
                </Paper>

                {/* Labels */}
                {Object.keys(inspectNet.labels).length > 0 && (
                  <Paper variant="outlined" sx={{ p: 2 }}>
                    <Typography variant="subtitle2" color="text.secondary" gutterBottom>
                      Labels
                    </Typography>
                    <Stack spacing={0.25}>
                      {Object.entries(inspectNet.labels).map(([k, v]) => (
                        <Typography key={k} variant="body2" fontFamily="monospace" fontSize="0.8rem">
                          {k} = {v}
                        </Typography>
                      ))}
                    </Stack>
                  </Paper>
                )}

                {/* Connected containers */}
                <Paper variant="outlined" sx={{ p: 2 }}>
                  <Typography variant="subtitle2" color="text.secondary" gutterBottom>
                    Connected Containers ({inspectNet.containers.length})
                  </Typography>
                  {inspectNet.containers.length === 0 ? (
                    <Typography variant="body2" color="text.secondary">
                      No containers connected.
                    </Typography>
                  ) : (
                    <List dense disablePadding>
                      {inspectNet.containers.map((c) => (
                        <ListItem key={c.id} disableGutters>
                          <ListItemText
                            primary={c.name || c.id.slice(0, 12)}
                            secondary={`${c.ipv4_address || "no IP"} · ${c.mac_address || "no MAC"}`}
                            primaryTypographyProps={{ fontFamily: "monospace", fontSize: "0.85rem" }}
                            secondaryTypographyProps={{ fontFamily: "monospace", fontSize: "0.75rem" }}
                          />
                        </ListItem>
                      ))}
                    </List>
                  )}
                </Paper>

                {/* Options */}
                {Object.keys(inspectNet.options).length > 0 && (
                  <Paper variant="outlined" sx={{ p: 2 }}>
                    <Typography variant="subtitle2" color="text.secondary" gutterBottom>
                      Driver Options
                    </Typography>
                    <Stack spacing={0.25}>
                      {Object.entries(inspectNet.options).map(([k, v]) => (
                        <Typography key={k} variant="body2" fontFamily="monospace" fontSize="0.8rem">
                          {k} = {v}
                        </Typography>
                      ))}
                    </Stack>
                  </Paper>
                )}
              </Stack>
            </DialogContent>
            <DialogActions>
              <Button onClick={() => setInspectNet(null)}>Close</Button>
            </DialogActions>
          </>
        )}
      </Dialog>
    </Box>
  );
}
