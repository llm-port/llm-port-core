/**
 * Admin → LLM → Providers (unified).
 *
 * Consolidates the former Providers + Runtimes pages into a single view.
 * - Local Docker providers own exactly one runtime → start/stop/restart inline.
 * - Remote Endpoint providers have an external URL and no container to manage.
 */
import { useState, useMemo, useCallback } from "react";
import { Link as RouterLink, useNavigate } from "react-router";
import { useTranslation } from "react-i18next";
import {
  providers,
  runtimes,
  models as modelApi,
  ownerPath,
  type Provider,
  type Runtime,
  type Model,
  type UpdateProviderPayload,
} from "~/api/llm";
import { DataTable, type ColumnDef } from "~/components/DataTable";
import { EngineChip, RuntimeStatusChip } from "~/components/Chips";
import MonitoringCardRow from "~/pages/admin/llm/MonitoringCardRow";
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
  const navigate = useNavigate();

  // ── Data ─────────────────────────────────────────────────────────
  //
  // Three independent loads rather than one `Promise.all`.  Gathered
  // together, the table waited for the slowest of the three and showed
  // nothing until then -- and the slow one is `runtimes.list()`, which
  // reconciles every local runtime against Docker one container at a time
  // before it answers.  A single unresponsive container held up the whole
  // provider list, which is exactly backwards: that is when the operator
  // most needs to see it.
  //
  // The providers list is the page's spine; runtimes and models only enrich
  // its columns.  So the table renders as soon as the spine arrives, and the
  // enrichment fills in beside it.
  const {
    data: providersList,
    loading,
    error,
    refresh: reloadProviders,
  } = useAsyncData(() => providers.list(), [], {
    initialValue: [] as Provider[],
  });

  const runtimesState = useAsyncData(() => runtimes.list(), [], {
    initialValue: [] as Runtime[],
  });
  const modelsState = useAsyncData(() => modelApi.list(), [], {
    initialValue: [] as Model[],
  });
  const runtimesList = runtimesState.data;
  const modelsList = modelsState.data;
  // True while a column's source is still on its way, so the cell can say
  // "not yet" instead of rendering an absence as a value.
  const enrichmentPending = runtimesState.loading || modelsState.loading;

  const load = useCallback(async () => {
    await Promise.all([
      reloadProviders(),
      runtimesState.refresh(),
      modelsState.refresh(),
    ]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  // ── Wizard state ─────────────────────────────────────────────────
  const [showWizard, setShowWizard] = useState(false);

  // Edit dialog
  const [editTarget, setEditTarget] = useState<Provider | null>(null);
  const [editName, setEditName] = useState("");
  const [editEndpointUrl, setEditEndpointUrl] = useState("");
  const [editApiKey, setEditApiKey] = useState("");
  const [editRemoteModel, setEditRemoteModel] = useState("");

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
        return {
          provider: p,
          runtime: rt,
          model: rt ? (modelMap.get(rt.model_id) ?? null) : null,
        };
      }),
    [providersList, runtimeByProvider, modelMap],
  );

  // ── Runtime actions ──────────────────────────────────────────────
  async function handleRuntimeAction(
    runtimeId: string,
    action: "start" | "stop" | "restart",
  ) {
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
      // Backend cascade-deletes associated runtimes (stops containers too)
      await providers.delete(row.provider.id);
      await load();
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : t("common.delete_failed"));
    } finally {
      setActionLoading(null);
    }
  }

  async function handleUpdate() {
    if (!editTarget) return;
    try {
      const payload: UpdateProviderPayload = { name: editName.trim() };
      if (editTarget.target === "remote_endpoint") {
        if (editEndpointUrl.trim())
          payload.endpoint_url = editEndpointUrl.trim();
        if (editApiKey.trim()) payload.api_key = editApiKey.trim();
        payload.remote_model = editRemoteModel.trim() || null;
      }
      await providers.update(editTarget.id, payload);
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
          ) : r.provider.target === "inference_cluster" ? (
            // Ours, on our own hardware — the opposite of a remote endpoint,
            // which is what it was being labelled.
            <Chip
              label={t("clusters.deployments.col_cluster")}
              size="small"
              color="secondary"
              variant="outlined"
              sx={{ fontSize: "0.75rem" }}
            />
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
      sortValue: (r) =>
        r.model?.display_name ?? r.provider.managed_by?.model_name ?? "",
      searchValue: (r) =>
        r.model?.display_name ?? r.provider.managed_by?.model_name ?? "",
      render: (r) =>
        // A derived provider has no runtime row to join a model through, so
        // this column read "no runtime" for a deployment serving one.
        r.provider.managed_by?.model_name ? (
          <Typography variant="body2" fontSize="0.8rem">
            {r.provider.managed_by.model_name}
          </Typography>
        ) : r.model ? (
          <Typography variant="body2" fontSize="0.8rem">
            {r.model.display_name}
          </Typography>
        ) : r.provider.target === "remote_endpoint" ? (
          r.provider.remote_model ? (
            <Typography variant="body2" fontSize="0.8rem">
              {r.provider.remote_model}
            </Typography>
          ) : (
            <Typography variant="body2" color="text.disabled" fontSize="0.8rem">
              —
            </Typography>
          )
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
      sortValue: (r) =>
        r.runtime?.status ??
        r.provider.managed_by?.state ??
        (r.provider.target === "remote_endpoint" ? "remote" : ""),
      render: (r) =>
        r.runtime ? (
          <RuntimeStatusChip value={r.runtime.status} />
        ) : r.provider.managed_by ? (
          // Served by one of our deployments: it has no container of its own,
          // so the deployment's state is the only honest thing here. The cell
          // was blank before, which reads as "unknown" for something that is
          // running.
          <Chip
            label={r.provider.managed_by.state ?? "unknown"}
            size="small"
            color={
              r.provider.managed_by.state === "running" ? "success" : "default"
            }
            variant="outlined"
            sx={{ fontSize: "0.75rem" }}
          />
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
            <Typography
              variant="body2"
              fontFamily="monospace"
              fontSize="0.75rem"
            >
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
            {/* A provider owned by a deployment is managed from the
                deployment. Editing or deleting it here does not stick — the
                next reconcile recreates or overwrites it — so the controls
                are absent rather than present and futile. The backend
                refuses them too; this is so the operator never reaches for
                one. */}
            {r.provider.managed_by ? (
              <Tooltip title={r.provider.managed_by.kind === "found_container"
                ? `Found running as ${r.provider.managed_by.name ?? ""}; routed as it is`
                : `Managed by deployment ${r.provider.managed_by.name ?? ""}`}>
                <IconButton
                  size="small"
                  onClick={(e) => {
                    e.stopPropagation();
                    navigate(ownerPath(r.provider.managed_by!));
                  }}
                >
                  <OpenInNewIcon fontSize="small" />
                </IconButton>
              </Tooltip>
            ) : (
              <>
            <Tooltip title={t("common.edit")}>
              <IconButton
                size="small"
                onClick={(e) => {
                  e.stopPropagation();
                  setEditTarget(r.provider);
                  setEditName(r.provider.name);
                  setEditEndpointUrl(
                    r.provider.endpoint_url &&
                      !r.provider.endpoint_url.startsWith("litellm://")
                      ? r.provider.endpoint_url
                      : "",
                  );
                  setEditApiKey("");
                  setEditRemoteModel(r.provider.remote_model ?? "");
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
              </>
            )}
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
        expansion={(r) =>
          // The same cards for both kinds of provider. A cluster-backed one
          // used to get a link to its deployment instead, which is a dead end
          // for anybody whose role reaches this page and not that one — and
          // makes "is the hardware working" a two-screen question.
          r.provider.managed_by || r.runtime?.monitoring ? (
            <MonitoringCardRow
              provider={r.provider}
              known={Boolean(r.provider.managed_by) || Boolean(r.runtime?.monitoring)}
              onOpenOwner={
                r.provider.managed_by
                  ? () =>
                      navigate(ownerPath(r.provider.managed_by!))
                  : undefined
              }
            />
          ) : (
            <Typography variant="caption" color="text.disabled">
              {t(
                "llm_monitoring.not_configured",
                "No live metrics configured for this provider",
              )}
            </Typography>
          )
        }
        toolbarActions={
          <Button
            size="small"
            variant="contained"
            startIcon={<AddIcon />}
            onClick={() => setShowWizard(true)}
            data-tour-id="providers.add"
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
        submitDisabled={
          !editName.trim() ||
          (editTarget?.target === "remote_endpoint" &&
            !editEndpointUrl.trim() &&
            !editTarget.litellm_provider)
        }
        onSubmit={() => void handleUpdate()}
        onClose={() => setEditTarget(null)}
        maxWidth={editTarget?.target === "remote_endpoint" ? "sm" : "xs"}
      >
        <TextField
          label={t("common.name")}
          value={editName}
          onChange={(e) => setEditName(e.target.value)}
          required
          autoFocus
          fullWidth
        />
        {editTarget?.target === "remote_endpoint" && (
          <Stack spacing={2} sx={{ mt: 2 }}>
            <TextField
              label={t("llm_providers.endpoint_url")}
              value={editEndpointUrl}
              onChange={(e) => setEditEndpointUrl(e.target.value)}
              required={!editTarget?.litellm_provider}
              fullWidth
              helperText={
                editTarget?.litellm_provider
                  ? t(
                      "llm_providers.endpoint_url_help_optional",
                      "Optional — leave empty for hosted providers like Gemini, Anthropic, etc.",
                    )
                  : undefined
              }
            />
            <TextField
              label={t("llm_providers.api_key")}
              type="password"
              value={editApiKey}
              onChange={(e) => setEditApiKey(e.target.value)}
              fullWidth
              helperText={t("llm_providers.api_key_help")}
            />
            <TextField
              label={t("llm_common.model")}
              value={editRemoteModel}
              onChange={(e) => setEditRemoteModel(e.target.value)}
              fullWidth
            />
          </Stack>
        )}
      </FormDialog>
    </>
  );
}
