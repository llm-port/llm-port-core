/**
 * Admin → LLM → Runtimes list page.
 * Create, start, stop, restart, delete LLM serving runtimes.
 */
import { useState, useEffect } from "react";
import { Link as RouterLink } from "react-router";
import { useTranslation } from "react-i18next";
import {
  runtimes,
  providers as provApi,
  models as modelApi,
  type Runtime,
  type Provider,
  type Model,
  type CreateRuntimePayload,
} from "~/api/llm";
import { DataTable, type ColumnDef } from "~/components/DataTable";
import { RuntimeStatusChip, EngineChip } from "~/components/Chips";

import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import FormControl from "@mui/material/FormControl";
import IconButton from "@mui/material/IconButton";
import InputLabel from "@mui/material/InputLabel";
import Link from "@mui/material/Link";
import MenuItem from "@mui/material/MenuItem";
import Select from "@mui/material/Select";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";

import AddIcon from "@mui/icons-material/Add";
import DeleteIcon from "@mui/icons-material/Delete";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import StopIcon from "@mui/icons-material/Stop";
import RestartAltIcon from "@mui/icons-material/RestartAlt";
import OpenInNewIcon from "@mui/icons-material/OpenInNew";

export default function RuntimesPage() {
  const { t } = useTranslation();
  const [data, setData] = useState<Runtime[]>([]);
  const [providersList, setProvidersList] = useState<Provider[]>([]);
  const [modelsList, setModelsList] = useState<Model[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  // Create dialog
  const [showCreate, setShowCreate] = useState(false);
  const [cName, setCName] = useState("");
  const [cProviderId, setCProviderId] = useState("");
  const [cModelId, setCModelId] = useState("");

  // lookup maps
  const provMap = Object.fromEntries(providersList.map((p) => [p.id, p]));
  const modelMap = Object.fromEntries(modelsList.map((m) => [m.id, m]));

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const [r, p, m] = await Promise.all([
        runtimes.list(),
        provApi.list(),
        modelApi.list(),
      ]);
      setData(r);
      setProvidersList(p);
      setModelsList(m);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t("llm_runtimes.failed_load"));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    const payload: CreateRuntimePayload = {
      name: cName,
      provider_id: cProviderId,
      model_id: cModelId,
    };
    try {
      await runtimes.create(payload);
      setShowCreate(false);
      setCName("");
      setCProviderId("");
      setCModelId("");
      await load();
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : t("common.create_failed"));
    }
  }

  async function handleAction(id: string, action: "start" | "stop" | "restart") {
    setActionLoading(`${id}-${action}`);
    try {
      await runtimes[action](id);
      await load();
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : t("common.action_failed"));
    } finally {
      setActionLoading(null);
    }
  }

  async function handleDelete(id: string) {
    if (!confirm(t("llm_runtimes.confirm_delete"))) return;
    setActionLoading(`${id}-delete`);
    try {
      await runtimes.delete(id);
      await load();
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : t("common.delete_failed"));
    } finally {
      setActionLoading(null);
    }
  }

  const columns: ColumnDef<Runtime>[] = [
    {
      key: "name",
      label: t("common.name"),
      sortable: true,
      sortValue: (r) => r.name,
      searchValue: (r) => r.name,
      render: (r) => (
        <Link
          component={RouterLink}
          to={`/admin/llm/runtimes/${r.id}`}
          underline="hover"
          color="primary.light"
          fontWeight={600}
          sx={{ fontSize: "0.85rem" }}
        >
          {r.name}
        </Link>
      ),
    },
    {
      key: "provider",
      label: t("llm_common.provider"),
      sortable: true,
      sortValue: (r) => provMap[r.provider_id]?.name ?? "",
      searchValue: (r) => provMap[r.provider_id]?.name ?? "",
      render: (r) => {
        const p = provMap[r.provider_id];
        return p ? (
          <Stack direction="row" spacing={1} alignItems="center">
            <EngineChip value={p.type} />
            <Typography variant="body2" fontSize="0.8rem">
              {p.name}
            </Typography>
          </Stack>
        ) : (
          <Typography variant="body2" color="text.secondary" fontSize="0.8rem">
            {r.provider_id.slice(0, 8)}
          </Typography>
        );
      },
    },
    {
      key: "model",
      label: t("llm_common.model"),
      sortable: true,
      sortValue: (r) => modelMap[r.model_id]?.display_name ?? "",
      searchValue: (r) => modelMap[r.model_id]?.display_name ?? "",
      render: (r) => (
        <Typography variant="body2" fontSize="0.8rem">
          {modelMap[r.model_id]?.display_name ?? r.model_id.slice(0, 8)}
        </Typography>
      ),
    },
    {
      key: "status",
      label: t("common.status"),
      sortable: true,
      sortValue: (r) => r.status,
      render: (r) => <RuntimeStatusChip value={r.status} />,
    },
    {
      key: "endpoint",
      label: t("llm_runtimes.endpoint"),
      render: (r) =>
        r.endpoint_url ? (
          <Stack direction="row" spacing={0.5} alignItems="center">
            <Typography variant="body2" fontFamily="monospace" fontSize="0.75rem">
              {r.endpoint_url}
            </Typography>
            <IconButton
              size="small"
              href={r.endpoint_url}
              target="_blank"
              rel="noopener"
              onClick={(e) => e.stopPropagation()}
            >
              <OpenInNewIcon sx={{ fontSize: 14 }} />
            </IconButton>
          </Stack>
        ) : (
          <Typography variant="body2" color="text.disabled" fontSize="0.8rem">
            —
          </Typography>
        ),
    },
    {
      key: "actions",
      label: t("common.actions"),
      align: "right",
      render: (r) => {
        const busy = !!actionLoading?.startsWith(r.id);
        const isRunning = r.status === "running";
        const isStopped = r.status === "stopped" || r.status === "error";
        return (
          <Stack direction="row" spacing={0.5} justifyContent="flex-end">
            {isStopped && (
              <Tooltip title={t("common.start")}>
                <IconButton
                  size="small"
                  color="success"
                  disabled={busy}
                  onClick={(e) => {
                    e.stopPropagation();
                    void handleAction(r.id, "start");
                  }}
                >
                  <PlayArrowIcon fontSize="small" />
                </IconButton>
              </Tooltip>
            )}
            {isRunning && (
              <>
                <Tooltip title={t("common.stop")}>
                  <IconButton
                    size="small"
                    color="warning"
                    disabled={busy}
                    onClick={(e) => {
                      e.stopPropagation();
                      void handleAction(r.id, "stop");
                    }}
                  >
                    <StopIcon fontSize="small" />
                  </IconButton>
                </Tooltip>
                <Tooltip title={t("common.restart")}>
                  <IconButton
                    size="small"
                    color="info"
                    disabled={busy}
                    onClick={(e) => {
                      e.stopPropagation();
                      void handleAction(r.id, "restart");
                    }}
                  >
                    <RestartAltIcon fontSize="small" />
                  </IconButton>
                </Tooltip>
              </>
            )}
            <Tooltip title={t("common.delete")}>
              <IconButton
                size="small"
                color="error"
                disabled={busy || isRunning}
                onClick={(e) => {
                  e.stopPropagation();
                  void handleDelete(r.id);
                }}
              >
                <DeleteIcon fontSize="small" />
              </IconButton>
            </Tooltip>
          </Stack>
        );
      },
    },
  ];

  return (
    <>
      <DataTable
        columns={columns}
        rows={data}
        rowKey={(r) => r.id}
        loading={loading}
        error={error}
        title={t("llm_runtimes.title")}
        emptyMessage={t("llm_runtimes.empty")}
        onRefresh={load}
        searchPlaceholder={t("llm_runtimes.search_placeholder")}
        toolbarActions={
          <Button
            size="small"
            variant="contained"
            startIcon={<AddIcon />}
            onClick={() => setShowCreate(true)}
          >
            {t("llm_runtimes.create_runtime")}
          </Button>
        }
      />

      {/* Create dialog */}
      <Dialog
        open={showCreate}
        onClose={() => setShowCreate(false)}
        maxWidth="sm"
        fullWidth
      >
        <form onSubmit={handleCreate}>
          <DialogTitle>{t("llm_runtimes.new_runtime")}</DialogTitle>
          <DialogContent sx={{ display: "flex", flexDirection: "column", gap: 2, pt: "8px !important" }}>
            <TextField
              label={t("common.name")}
              value={cName}
              onChange={(e) => setCName(e.target.value)}
              required
              autoFocus
              fullWidth
            />
            <FormControl fullWidth required>
              <InputLabel>{t("llm_common.provider")}</InputLabel>
              <Select
                value={cProviderId}
                label={t("llm_common.provider")}
                onChange={(e) => setCProviderId(e.target.value)}
              >
                {providersList.map((p) => (
                  <MenuItem key={p.id} value={p.id}>
                    {p.name} ({p.type})
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
            <FormControl fullWidth required>
              <InputLabel>{t("llm_common.model")}</InputLabel>
              <Select
                value={cModelId}
                label={t("llm_common.model")}
                onChange={(e) => setCModelId(e.target.value)}
              >
                {modelsList
                  .filter((m) => m.status === "available")
                  .map((m) => (
                    <MenuItem key={m.id} value={m.id}>
                      {m.display_name}
                    </MenuItem>
                  ))}
              </Select>
            </FormControl>
          </DialogContent>
          <DialogActions>
            <Button onClick={() => setShowCreate(false)}>{t("common.cancel")}</Button>
            <Button type="submit" variant="contained">
              {t("common.create")}
            </Button>
          </DialogActions>
        </form>
      </Dialog>
    </>
  );
}
