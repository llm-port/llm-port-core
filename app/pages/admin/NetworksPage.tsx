/**
 * Admin → Networks page.
 * Lists Docker networks, allows creating/deleting user networks, and
 * shows system networks as read-only (stats only). Table powered by DataTable.
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";
import {
  networks,
  type NetworkSummary,
  type NetworkDetail,
  type CreateNetworkPayload,
} from "~/api/admin";
import { DataTable, type ColumnDef, type ColumnFilter } from "~/components/DataTable";
import { useAsyncData } from "~/lib/useAsyncData";

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
import TextField from "@mui/material/TextField";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import Alert from "@mui/material/Alert";

import AddIcon from "@mui/icons-material/Add";
import DeleteIcon from "@mui/icons-material/Delete";
import InfoOutlinedIcon from "@mui/icons-material/InfoOutlined";
import LockIcon from "@mui/icons-material/Lock";

export default function NetworksPage() {
  const { t } = useTranslation();
  const {
    data,
    loading,
    error,
    refresh: load,
    setError,
  } = useAsyncData(
    () => networks.list(),
    [],
    { initialValue: [] as NetworkSummary[] },
  );

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

  // Type filter
  const [typeFilter, setTypeFilter] = useState<string[]>(["system", "user"]);

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
      setCreateError(err instanceof Error ? err.message : t("common.create_failed"));
    } finally {
      setCreating(false);
    }
  }

  async function handleDelete(id: string, name: string) {
    if (!confirm(t("networks.confirm_delete", { name }))) return;
    setActionLoading(id);
    try {
      await networks.delete(id);
      await load();
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : t("common.delete_failed"));
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
      alert(err instanceof Error ? err.message : t("networks.failed_inspect"));
    } finally {
      setInspectLoading(false);
    }
  }

  const columns: ColumnDef<NetworkSummary>[] = [
    {
      key: "name",
      label: t("common.name"),
      sortable: true,
      searchValue: (n) => n.name + " " + n.id,
      render: (n) => (
        <Box>
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
        </Box>
      ),
    },
    {
      key: "driver",
      label: t("networks.driver"),
      searchValue: (n) => n.driver ?? "",
      render: (n) => <Chip label={n.driver || "—"} size="small" variant="outlined" />,
    },
    {
      key: "scope",
      label: t("containers.scope"),
      sortable: true,
      searchValue: (n) => n.scope,
      render: (n) => (
        <Typography variant="body2" fontSize="0.85rem">
          {n.scope}
        </Typography>
      ),
    },
    {
      key: "internal",
      label: t("networks.internal"),
      align: "center",
      render: (n) =>
        n.internal ? (
          <Chip label={t("common.yes")} size="small" color="info" variant="outlined" />
        ) : (
          <Typography variant="body2" color="text.secondary">
            —
          </Typography>
        ),
    },
    {
      key: "containers",
      label: t("containers.title"),
      align: "center",
      sortable: true,
      sortValue: (n) => n.container_count,
      render: (n) => (
        <Chip
          label={n.container_count}
          size="small"
          color={n.container_count > 0 ? "primary" : "default"}
          variant="outlined"
        />
      ),
    },
    {
      key: "type",
      label: t("networks.type"),
      align: "center",
      render: (n) =>
        n.is_system ? (
          <Chip icon={<LockIcon />} label={t("networks.system")} size="small" color="warning" variant="filled" />
        ) : (
          <Chip label={t("networks.user")} size="small" color="success" variant="outlined" />
        ),
    },
    {
      key: "actions",
      label: t("common.actions"),
      align: "right",
      render: (n) => {
        const busy = actionLoading === n.id;
        return (
          <Stack direction="row" spacing={0.5} justifyContent="flex-end">
            <Tooltip title={t("networks.inspect")}>
              <IconButton
                size="small"
                onClick={(e) => {
                  e.stopPropagation();
                  handleInspect(n.id);
                }}
                disabled={inspectLoading}
              >
                <InfoOutlinedIcon fontSize="small" />
              </IconButton>
            </Tooltip>
            {!n.is_system && (
              <Tooltip title={t("common.delete")}>
                <IconButton
                  size="small"
                  color="error"
                  onClick={(e) => {
                    e.stopPropagation();
                    handleDelete(n.id, n.name);
                  }}
                  disabled={busy}
                >
                  <DeleteIcon fontSize="small" />
                </IconButton>
              </Tooltip>
            )}
          </Stack>
        );
      },
    },
  ];

  const columnFilters: ColumnFilter[] = [
    {
      label: t("networks.type"),
      value: "",
      multi: true,
      multiValue: typeFilter,
      onMultiChange: setTypeFilter,
      options: [
        { label: t("networks.system"), value: "system" },
        { label: t("networks.user"), value: "user" },
      ],
      minWidth: 140,
    },
  ];

  // Apply type filter client-side
  const filteredData = data.filter((n) =>
    typeFilter.includes(n.is_system ? "system" : "user"),
  );

  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}>
      <DataTable
        title={t("networks.title")}
        columns={columns}
        rows={filteredData}
        rowKey={(n) => n.id}
        loading={loading}
        error={error}
        emptyMessage={t("networks.empty")}
        onRefresh={load}
        searchPlaceholder={t("networks.search_placeholder")}
        columnFilters={columnFilters}
        toolbarActions={
          <Button
            variant="contained"
            size="small"
            startIcon={<AddIcon />}
            onClick={() => setShowCreate(true)}
          >
            {t("networks.create_network")}
          </Button>
        }
      />

      {/* ── Create Network Dialog ────────────────────────────────── */}
      <Dialog
        open={showCreate}
        onClose={() => {
          setShowCreate(false);
          setCreateError(null);
        }}
        maxWidth="sm"
        fullWidth
      >
        <form onSubmit={handleCreate}>
          <DialogTitle>{t("networks.create_network")}</DialogTitle>
          <DialogContent
            sx={{ display: "flex", flexDirection: "column", gap: 2, pt: "8px !important" }}
          >
            <TextField
              label={t("networks.network_name")}
              required
              fullWidth
              value={createForm.name}
              onChange={(e) => setCreateForm((f) => ({ ...f, name: e.target.value }))}
              inputProps={{ minLength: 1, maxLength: 256 }}
              autoFocus
            />
            <TextField
              select
              label={t("networks.driver")}
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
              label={t("networks.internal_help")}
            />
            <Divider sx={{ my: 0.5 }} />
            <Typography variant="subtitle2" color="text.secondary">
              {t("networks.ipam_optional")}
            </Typography>
            <TextField
              label={t("networks.subnet")}
              fullWidth
              placeholder="e.g. 172.28.0.0/16"
              value={createForm.subnet ?? ""}
              onChange={(e) => setCreateForm((f) => ({ ...f, subnet: e.target.value || null }))}
            />
            <TextField
              label={t("networks.gateway")}
              fullWidth
              placeholder="e.g. 172.28.0.1"
              value={createForm.gateway ?? ""}
              onChange={(e) => setCreateForm((f) => ({ ...f, gateway: e.target.value || null }))}
            />
            {createError && <Alert severity="error">{createError}</Alert>}
          </DialogContent>
          <DialogActions>
            <Button
              onClick={() => {
                setShowCreate(false);
                setCreateError(null);
              }}
            >
              {t("common.cancel")}
            </Button>
            <Button
              type="submit"
              variant="contained"
              disabled={creating || !createForm.name.trim()}
            >
              {creating ? t("common.creating") : t("common.create")}
            </Button>
          </DialogActions>
        </form>
      </Dialog>

      {/* ── Inspect Network Dialog ───────────────────────────────── */}
      <Dialog open={!!inspectNet} onClose={() => setInspectNet(null)} maxWidth="md" fullWidth>
        {inspectNet && (
          <>
            <DialogTitle sx={{ display: "flex", alignItems: "center", gap: 1 }}>
              {inspectNet.name}
              {inspectNet.is_system && (
                <Chip icon={<LockIcon />} label={t("networks.system")} size="small" color="warning" sx={{ ml: 1 }} />
              )}
            </DialogTitle>
            <DialogContent>
              <Stack spacing={2}>
                {/* Key/value info */}
                <Paper variant="outlined" sx={{ p: 2 }}>
                  <Typography variant="subtitle2" color="text.secondary" gutterBottom>
                    {t("common.details")}
                  </Typography>
                  <Stack spacing={0.5}>
                    {(
                      [
                        ["ID", inspectNet.id],
                        [t("networks.driver"), inspectNet.driver],
                        [t("containers.scope"), inspectNet.scope],
                        [t("networks.subnet"), inspectNet.subnet || "—"],
                        [t("networks.gateway"), inspectNet.gateway || "—"],
                        [t("networks.internal"), inspectNet.internal ? t("common.yes") : t("common.no")],
                        [t("common.created"), inspectNet.created],
                      ] as [string, string][]
                    ).map(([k, v]) => (
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

                {Object.keys(inspectNet.labels).length > 0 && (
                  <Paper variant="outlined" sx={{ p: 2 }}>
                    <Typography variant="subtitle2" color="text.secondary" gutterBottom>
                      {t("common.labels")}
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

                <Paper variant="outlined" sx={{ p: 2 }}>
                  <Typography variant="subtitle2" color="text.secondary" gutterBottom>
                    {t("networks.connected_containers", { count: inspectNet.containers.length })}
                  </Typography>
                  {inspectNet.containers.length === 0 ? (
                    <Typography variant="body2" color="text.secondary">
                      {t("networks.no_connected_containers")}
                    </Typography>
                  ) : (
                    <List dense disablePadding>
                      {inspectNet.containers.map((c) => (
                        <ListItem key={c.id} disableGutters>
                          <ListItemText
                            primary={c.name || c.id.slice(0, 12)}
                            secondary={`${c.ipv4_address || t("networks.no_ip")} · ${c.mac_address || t("networks.no_mac")}`}
                            primaryTypographyProps={{ fontFamily: "monospace", fontSize: "0.85rem" }}
                            secondaryTypographyProps={{
                              fontFamily: "monospace",
                              fontSize: "0.75rem",
                            }}
                          />
                        </ListItem>
                      ))}
                    </List>
                  )}
                </Paper>

                {Object.keys(inspectNet.options).length > 0 && (
                  <Paper variant="outlined" sx={{ p: 2 }}>
                    <Typography variant="subtitle2" color="text.secondary" gutterBottom>
                      {t("networks.driver_options")}
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
              <Button onClick={() => setInspectNet(null)}>{t("common.close")}</Button>
            </DialogActions>
          </>
        )}
      </Dialog>
    </Box>
  );
}
