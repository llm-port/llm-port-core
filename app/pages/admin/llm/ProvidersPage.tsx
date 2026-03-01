/**
 * Admin → LLM → Providers (unified).
 *
 * Consolidates the former Providers + Runtimes pages into a single view.
 * - Local Docker providers own exactly one runtime → start/stop/restart inline.
 * - Remote Endpoint providers have an external URL and no container to manage.
 */
import { useState, useMemo } from "react";
import { Link as RouterLink } from "react-router";
import { useTranslation } from "react-i18next";
import {
  providers,
  runtimes,
  models as modelApi,
  type Provider,
  type Runtime,
  type Model,
} from "~/api/llm";
import { DataTable, type ColumnDef } from "~/components/DataTable";
import { EngineChip, RuntimeStatusChip } from "~/components/Chips";
import { FormDialog } from "~/components/FormDialog";
import { ProviderWizardDialog } from "~/components/ProviderWizardDialog";
import { useAsyncData } from "~/lib/useAsyncData";

import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import IconButton from "@mui/material/IconButton";
import Link from "@mui/material/Link";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";

import AddIcon from "@mui/icons-material/Add";
import DeleteIcon from "@mui/icons-material/Delete";
import EditIcon from "@mui/icons-material/Edit";
import OpenInNewIcon from "@mui/icons-material/OpenInNew";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import RestartAltIcon from "@mui/icons-material/RestartAlt";
import StopIcon from "@mui/icons-material/Stop";

// ── Joined row type ──────────────────────────────────────────────────
interface ProviderRow {
  provider: Provider;
  runtime: Runtime | null;
  model: Model | null;
}

export default function ProvidersPage() {
  const { t } = useTranslation();

  // ── Data ─────────────────────────────────────────────────────────
  const {
    data: { providersList, runtimesList, modelsList },
    loading,
    error,
    refresh: load,
  } = useAsyncData(
    async () => {
      const [p, r, m] = await Promise.all([
        providers.list(),
        runtimes.list(),
        modelApi.list(),
      ]);
      return { providersList: p, runtimesList: r, modelsList: m };
    },
    [],
    { initialValue: { providersList: [] as Provider[], runtimesList: [] as Runtime[], modelsList: [] as Model[] } },
  );
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  // ── Wizard state ─────────────────────────────────────────────────
  const [showWizard, setShowWizard] = useState(false);

  // Edit dialog
  const [editTarget, setEditTarget] = useState<Provider | null>(null);
  const [editName, setEditName] = useState("");

  // ── Derived data ─────────────────────────────────────────────────
  const runtimeByProvider = useMemo(() => {
    const map = new Map<string, Runtime>();
    for (const r of runtimesList) map.set(r.provider_id, r);
    return map;
  }, [runtimesList]);

  const modelMap = useMemo(
    () => new Map(modelsList.map((m) => [m.id, m])),
    [modelsList],
  );

  const rows: ProviderRow[] = useMemo(
    () =>
      providersList.map((p) => {
        const rt = runtimeByProvider.get(p.id) ?? null;
        return { provider: p, runtime: rt, model: rt ? modelMap.get(rt.model_id) ?? null : null };
      }),
    [providersList, runtimeByProvider, modelMap],
  );

  // ── Runtime actions ──────────────────────────────────────────────
  async function handleRuntimeAction(runtimeId: string, action: "start" | "stop" | "restart") {
    setActionLoading(`${runtimeId}-${action}`);
    try {
      await runtimes[action](runtimeId);
      await load();
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : t("common.action_failed"));
    } finally {
      setActionLoading(null);
    }
  }

  async function handleDelete(row: ProviderRow) {
    if (!confirm(t("llm_providers.confirm_delete"))) return;
    setActionLoading(`${row.provider.id}-delete`);
    try {
      // Delete the runtime first if one exists
      if (row.runtime) await runtimes.delete(row.runtime.id);
      await providers.delete(row.provider.id);
      await load();
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : t("common.delete_failed"));
    } finally {
      setActionLoading(null);
    }
  }

  async function handleUpdate(e: React.FormEvent) {
    e.preventDefault();
    if (!editTarget) return;
    try {
      await providers.update(editTarget.id, { name: editName });
      setEditTarget(null);
      await load();
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : t("common.update_failed"));
    }
  }

  // ── Table columns ────────────────────────────────────────────────
  const columns: ColumnDef<ProviderRow>[] = [
    {
      key: "name",
      label: t("common.name"),
      sortable: true,
      sortValue: (r) => r.provider.name,
      searchValue: (r) => r.provider.name,
      render: (r) =>
        r.runtime ? (
          <Link
            component={RouterLink}
            to={`/admin/llm/runtimes/${r.runtime.id}`}
            underline="hover"
            color="primary.light"
            fontWeight={600}
            sx={{ fontSize: "0.85rem" }}
          >
            {r.provider.name}
          </Link>
        ) : (
          <Typography variant="body2" fontWeight={600}>
            {r.provider.name}
          </Typography>
        ),
    },
    {
      key: "target",
      label: t("llm_providers.target"),
      sortable: true,
      sortValue: (r) => r.provider.target,
      render: (r) => (
        <Stack direction="row" spacing={1} alignItems="center">
          {r.provider.target === "local_docker" ? (
            <EngineChip value={r.provider.type} />
          ) : (
            <Chip
              label={t("llm_providers.target_remote_endpoint")}
              size="small"
              color="info"
              variant="outlined"
              sx={{ fontSize: "0.75rem" }}
            />
          )}
        </Stack>
      ),
    },
    {
      key: "model",
      label: t("llm_common.model"),
      sortable: true,
      sortValue: (r) => r.model?.display_name ?? "",
      searchValue: (r) => r.model?.display_name ?? "",
      render: (r) =>
        r.model ? (
          <Typography variant="body2" fontSize="0.8rem">
            {r.model.display_name}
          </Typography>
        ) : r.provider.target === "remote_endpoint" ? (
          <Typography variant="body2" color="text.disabled" fontSize="0.8rem">
            —
          </Typography>
        ) : (
          <Typography variant="body2" color="text.disabled" fontSize="0.8rem">
            {t("llm_providers.no_runtime")}
          </Typography>
        ),
    },
    {
      key: "status",
      label: t("common.status"),
      sortable: true,
      sortValue: (r) => r.runtime?.status ?? (r.provider.target === "remote_endpoint" ? "remote" : ""),
      render: (r) =>
        r.runtime ? (
          <RuntimeStatusChip value={r.runtime.status} />
        ) : r.provider.target === "remote_endpoint" ? (
          <Chip
            label={t("llm_providers.target_remote_endpoint")}
            size="small"
            color="info"
            variant="outlined"
            sx={{ fontSize: "0.75rem" }}
          />
        ) : null,
    },
    {
      key: "endpoint",
      label: t("llm_runtimes.endpoint"),
      render: (r) => {
        const url = r.runtime?.endpoint_url ?? r.provider.endpoint_url;
        return url ? (
          <Stack direction="row" spacing={0.5} alignItems="center">
            <Typography variant="body2" fontFamily="monospace" fontSize="0.75rem">
              {url}
            </Typography>
            <IconButton
              size="small"
              href={url}
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
        );
      },
    },
    {
      key: "actions",
      label: t("common.actions"),
      align: "right",
      render: (r) => {
        const rt = r.runtime;
        const busy = !!actionLoading?.startsWith(rt?.id ?? r.provider.id);
        const isRunning = rt?.status === "running" || rt?.status === "starting";
        const isStopped = rt?.status === "stopped" || rt?.status === "error";

        return (
          <Stack direction="row" spacing={0.5} justifyContent="flex-end">
            {/* Runtime controls for local providers */}
            {rt && isStopped && (
              <Tooltip title={t("common.start")}>
                <IconButton
                  size="small"
                  color="success"
                  disabled={busy}
                  onClick={(e) => {
                    e.stopPropagation();
                    void handleRuntimeAction(rt.id, "start");
                  }}
                >
                  <PlayArrowIcon fontSize="small" />
                </IconButton>
              </Tooltip>
            )}
            {rt && isRunning && (
              <>
                <Tooltip title={t("common.stop")}>
                  <IconButton
                    size="small"
                    color="warning"
                    disabled={busy}
                    onClick={(e) => {
                      e.stopPropagation();
                      void handleRuntimeAction(rt.id, "stop");
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
                      void handleRuntimeAction(rt.id, "restart");
                    }}
                  >
                    <RestartAltIcon fontSize="small" />
                  </IconButton>
                </Tooltip>
              </>
            )}
            <Tooltip title={t("common.edit")}>
              <IconButton
                size="small"
                onClick={(e) => {
                  e.stopPropagation();
                  setEditTarget(r.provider);
                  setEditName(r.provider.name);
                }}
              >
                <EditIcon fontSize="small" />
              </IconButton>
            </Tooltip>
            <Tooltip title={t("common.delete")}>
              <IconButton
                size="small"
                color="error"
                disabled={busy || isRunning}
                onClick={(e) => {
                  e.stopPropagation();
                  void handleDelete(r);
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

  // ── Render ───────────────────────────────────────────────────────
  return (
    <>
      <DataTable
        columns={columns}
        rows={rows}
        rowKey={(r) => r.provider.id}
        loading={loading}
        error={error}
        title={t("llm_providers.title")}
        emptyMessage={t("llm_providers.empty")}
        onRefresh={load}
        searchPlaceholder={t("llm_providers.search_placeholder")}
        toolbarActions={
          <Button
            size="small"
            variant="contained"
            startIcon={<AddIcon />}
            onClick={() => setShowWizard(true)}
          >
            {t("llm_providers.add_provider")}
          </Button>
        }
      />

      {/* ── Create Provider Wizard ──────────────────────────────── */}
      <ProviderWizardDialog
        open={showWizard}
        models={modelsList}
        onClose={() => setShowWizard(false)}
        onCreated={load}
      />

      {/* ── Edit dialog ─────────────────────────────────────────── */}
      <FormDialog
        open={!!editTarget}
        title={t("llm_providers.edit_provider")}
        submitLabel={t("common.save")}
        cancelLabel={t("common.cancel")}
        onSubmit={() => void handleUpdate(new Event("submit") as unknown as React.FormEvent)}
        onClose={() => setEditTarget(null)}
        maxWidth="xs"
      >
            <TextField
              label={t("common.name")}
              value={editName}
              onChange={(e) => setEditName(e.target.value)}
              required
              autoFocus
              fullWidth
            />
      </FormDialog>
    </>
  );
}
