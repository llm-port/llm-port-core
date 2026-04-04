/**
 * Admin → LLM → Models list page.
 * Shows all registered/downloaded models with status, source, and actions.
 */
import { useState, useEffect, useCallback, useRef } from "react";
import { Link as RouterLink, useNavigate } from "react-router";
import { useTranslation } from "react-i18next";
import {
  models,
  search,
  type Model,
  type ModelInstance,
  type DownloadModelPayload,
  type RegisterModelPayload,
  type HFModelHit,
  type DownloadResponse,
} from "~/api/llm";
import { DataTable, type ColumnDef } from "~/components/DataTable";
import { ModelStatusChip } from "~/components/Chips";

import Autocomplete from "@mui/material/Autocomplete";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import Link from "@mui/material/Link";
import Stack from "@mui/material/Stack";
import Tab from "@mui/material/Tab";
import Tabs from "@mui/material/Tabs";
import TextField from "@mui/material/TextField";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import IconButton from "@mui/material/IconButton";
import CircularProgress from "@mui/material/CircularProgress";

import AddIcon from "@mui/icons-material/Add";
import DeleteIcon from "@mui/icons-material/Delete";
import ComputerIcon from "@mui/icons-material/Computer";
import DnsIcon from "@mui/icons-material/Dns";
import CloudIcon from "@mui/icons-material/Cloud";
import OpenInNewIcon from "@mui/icons-material/OpenInNew";

export default function ModelsPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [data, setData] = useState<Model[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const loadingRef = useRef(false);

  // Add dialog
  const [showAdd, setShowAdd] = useState(false);
  const [addTab, setAddTab] = useState(0); // 0 = download, 1 = register

  // In-use guard dialog
  const [inUseModel, setInUseModel] = useState<Model | null>(null);

  // Download fields
  const [hfRepo, setHfRepo] = useState("");
  const [hfRevision, setHfRevision] = useState("");
  const [dlDisplayName, setDlDisplayName] = useState("");

  // Register fields
  const [regName, setRegName] = useState("");
  const [regPath, setRegPath] = useState("");

  // HF autocomplete
  const [hfOptions, setHfOptions] = useState<HFModelHit[]>([]);
  const [hfSearching, setHfSearching] = useState(false);
  const [hfInputValue, setHfInputValue] = useState("");

  // Debounced HF search
  useEffect(() => {
    if (hfInputValue.length < 2) {
      setHfOptions([]);
      return;
    }
    const timer = setTimeout(async () => {
      setHfSearching(true);
      try {
        const hits = await search.hfModels(hfInputValue, 12);
        setHfOptions(hits);
      } catch {
        setHfOptions([]);
      } finally {
        setHfSearching(false);
      }
    }, 350);
    return () => clearTimeout(timer);
  }, [hfInputValue]);

  async function load(silent = false) {
    if (loadingRef.current) return;
    loadingRef.current = true;
    if (!silent) {
      setLoading(true);
      setError(null);
    }
    try {
      // Auto-import any new models from the local HF cache first
      await models.scanLocal().catch(() => {});
      setData(await models.list());
      setError(null);
    } catch (e: unknown) {
      if (!silent)
        setError(e instanceof Error ? e.message : t("llm_models.failed_load"));
    } finally {
      if (!silent) setLoading(false);
      loadingRef.current = false;
    }
  }

  useEffect(() => {
    load();
  }, []);

  // Auto-refresh while any model is downloading
  useEffect(() => {
    const hasActive = data.some((m) => m.status === "downloading");
    if (!hasActive) return;
    const timer = setInterval(() => {
      void load(true);
    }, 5000);
    return () => clearInterval(timer);
  }, [data]);

  async function handleDownload(e: React.FormEvent) {
    e.preventDefault();
    const payload: DownloadModelPayload = { hf_repo_id: hfRepo };
    if (hfRevision) payload.hf_revision = hfRevision;
    if (dlDisplayName) payload.display_name = dlDisplayName;
    try {
      const resp: DownloadResponse = await models.download(payload);
      // Optimistically add the new model to the list immediately
      setData((prev) => [resp.model, ...prev]);
      setShowAdd(false);
      resetForm();
      if (!resp.dispatched) {
        alert(
          t("llm_models.download_dispatch_failed", {
            error: resp.dispatch_error ?? t("common.unknown_error"),
          }),
        );
      }
    } catch (err: unknown) {
      alert(
        err instanceof Error
          ? err.message
          : t("llm_models.download_request_failed"),
      );
      // Still refresh in case the model was partially created
      await load(true);
    }
  }

  async function handleRegister(e: React.FormEvent) {
    e.preventDefault();
    const payload: RegisterModelPayload = {
      display_name: regName,
      path: regPath,
    };
    try {
      await models.register(payload);
      setShowAdd(false);
      resetForm();
      await load(true);
    } catch (err: unknown) {
      alert(
        err instanceof Error ? err.message : t("llm_models.register_failed"),
      );
    }
  }

  function requestDelete(model: Model) {
    // Check if any runtime instance is active (not stopped / error)
    const active = (model.instances ?? []).filter(
      (i) => i.runtime_status !== "stopped" && i.runtime_status !== "error",
    );
    if (active.length > 0) {
      setInUseModel(model);
      return;
    }
    void confirmDelete(model.id);
  }

  async function confirmDelete(id: string) {
    if (!confirm(t("llm_models.confirm_delete"))) return;
    setData((prev) => prev.filter((m) => m.id !== id));
    try {
      await models.delete(id);
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : t("common.delete_failed"));
      await load(true);
    }
  }

  function resetForm() {
    setHfRepo("");
    setHfRevision("");
    setDlDisplayName("");
    setRegName("");
    setRegPath("");
  }

  /** Render compact location chips for a model's running instances. */
  function renderInstanceChips(instances: ModelInstance[]) {
    if (!instances || instances.length === 0) {
      return (
        <Typography variant="caption" color="text.disabled" fontSize="0.75rem">
          {t("llm_models.not_deployed")}
        </Typography>
      );
    }
    return (
      <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.5 }}>
        {instances.map((inst) => {
          const isNode = inst.execution_target === "node";
          const isRemote = inst.execution_target === "remote";
          const isCloud = inst.provider_type === "cloud";
          const label = isCloud
            ? inst.provider_name
            : isNode
              ? (inst.node_host ?? "Remote")
              : isRemote
                ? "Remote"
                : "Local";
          const icon = isCloud ? (
            <CloudIcon fontSize="small" />
          ) : isNode || isRemote ? (
            <DnsIcon fontSize="small" />
          ) : (
            <ComputerIcon fontSize="small" />
          );
          const statusColor =
            inst.runtime_status === "running"
              ? "success"
              : inst.runtime_status === "error"
                ? "error"
                : inst.runtime_status === "stopped"
                  ? "default"
                  : "warning";
          return (
            <Tooltip
              key={inst.runtime_id}
              title={`${inst.runtime_name} (${inst.runtime_status}) — ${inst.provider_name} [${inst.provider_type}]`}
            >
              <Chip
                icon={icon}
                label={label}
                size="small"
                color={statusColor}
                variant="outlined"
                sx={{ fontSize: "0.7rem", height: 22 }}
              />
            </Tooltip>
          );
        })}
      </Box>
    );
  }

  const columns: ColumnDef<Model>[] = [
    {
      key: "display_name",
      label: t("common.name"),
      sortable: true,
      sortValue: (m) => m.display_name,
      searchValue: (m) => `${m.display_name} ${m.hf_repo_id ?? ""}`,
      render: (m) => (
        <>
          <Link
            component={RouterLink}
            to={`/admin/llm/models/${m.id}`}
            underline="hover"
            color="primary.light"
            fontWeight={600}
            sx={{ fontSize: "0.85rem" }}
          >
            {m.display_name}
          </Link>
          {m.hf_repo_id && (
            <Typography
              variant="caption"
              display="block"
              color="text.secondary"
              fontFamily="monospace"
            >
              {m.hf_repo_id}
            </Typography>
          )}
        </>
      ),
    },
    {
      key: "source",
      label: t("llm_common.source"),
      sortable: true,
      sortValue: (m) => m.source,
      searchValue: (m) => m.source,
      render: (m) => (
        <Typography variant="body2" color="text.secondary" fontSize="0.8rem">
          {m.source}
        </Typography>
      ),
    },
    {
      key: "instances",
      label: t("llm_models.deployed_on"),
      render: (m) => renderInstanceChips(m.instances),
    },
    {
      key: "status",
      label: t("common.status"),
      sortable: true,
      sortValue: (m) => m.status,
      render: (m) => (
        <ModelStatusChip
          value={m.status}
          onClick={
            m.status === "downloading"
              ? () =>
                  navigate(
                    `/admin/scheduler?highlight=${encodeURIComponent(m.display_name)}`,
                  )
              : undefined
          }
        />
      ),
    },
    {
      key: "created_at",
      label: t("common.created"),
      sortable: true,
      sortValue: (m) => m.created_at,
      render: (m) => (
        <Typography variant="body2" color="text.secondary" fontSize="0.8rem">
          {new Date(m.created_at).toLocaleString()}
        </Typography>
      ),
    },
    {
      key: "actions",
      label: "",
      align: "right",
      render: (m) => (
        <Tooltip title={t("common.delete")}>
          <IconButton
            size="small"
            color="error"
            onClick={(e) => {
              e.stopPropagation();
              requestDelete(m);
            }}
          >
            <DeleteIcon fontSize="small" />
          </IconButton>
        </Tooltip>
      ),
    },
  ];

  return (
    <>
      <DataTable
        columns={columns}
        rows={data}
        rowKey={(m) => m.id}
        loading={loading}
        error={error}
        title={t("llm_models.title")}
        emptyMessage={t("llm_models.empty")}
        onRefresh={load}
        searchPlaceholder={t("llm_models.search_placeholder")}
        columnFilters={[
          {
            label: t("common.status"),
            value: "",
            options: [
              { value: "available", label: t("llm_common.available") },
              { value: "downloading", label: t("llm_common.downloading") },
              { value: "failed", label: t("llm_common.failed") },
            ],
            onChange: () => {},
          },
        ]}
        toolbarActions={
          <Button
            size="small"
            variant="contained"
            startIcon={<AddIcon />}
            onClick={() => setShowAdd(true)}
          >
            {t("llm_models.add_model")}
          </Button>
        }
      />

      {/* Add model dialog */}
      <Dialog
        open={showAdd}
        onClose={() => {
          setShowAdd(false);
          resetForm();
        }}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>{t("llm_models.add_model")}</DialogTitle>
        <Tabs value={addTab} onChange={(_, v) => setAddTab(v)} sx={{ px: 3 }}>
          <Tab label={t("llm_models.download_from_hf")} />
          <Tab label={t("llm_models.register_local")} />
        </Tabs>

        {addTab === 0 && (
          <form onSubmit={handleDownload}>
            <DialogContent
              sx={{
                display: "flex",
                flexDirection: "column",
                gap: 2,
                pt: "16px !important",
              }}
            >
              <Autocomplete
                freeSolo
                options={hfOptions}
                getOptionLabel={(opt) =>
                  typeof opt === "string" ? opt : opt.id
                }
                inputValue={hfInputValue}
                onInputChange={(_, v) => {
                  setHfInputValue(v);
                  setHfRepo(v);
                }}
                onChange={(_, v) => {
                  if (v && typeof v !== "string") {
                    setHfRepo(v.id);
                    setHfInputValue(v.id);
                    if (!dlDisplayName)
                      setDlDisplayName(v.id.split("/").pop() ?? "");
                  }
                }}
                loading={hfSearching}
                renderOption={(props, opt) => {
                  const { key, ...rest } = props;
                  return (
                    <li key={key} {...rest}>
                      <Stack sx={{ width: "100%" }}>
                        <Typography
                          variant="body2"
                          fontWeight={600}
                          fontFamily="monospace"
                          fontSize="0.85rem"
                        >
                          {(opt as HFModelHit).id}
                        </Typography>
                        <Typography variant="caption" color="text.secondary">
                          {(opt as HFModelHit).pipeline_tag ??
                            t("llm_models.model")}
                          {" \u00b7 "}
                          {t("llm_models.downloads", {
                            count: (opt as HFModelHit).downloads ?? 0,
                          })}
                          {" \u00b7 "}
                          {t("llm_models.likes", {
                            count: (opt as HFModelHit).likes ?? 0,
                          })}
                        </Typography>
                      </Stack>
                    </li>
                  );
                }}
                renderInput={(params) => (
                  <TextField
                    {...params}
                    label={t("llm_models.hf_repo_id")}
                    placeholder="meta-llama/Llama-3.1-8B"
                    required
                    autoFocus
                    slotProps={{
                      input: {
                        ...params.InputProps,
                        endAdornment: (
                          <>
                            {hfSearching ? (
                              <CircularProgress size={18} />
                            ) : null}
                            {params.InputProps.endAdornment}
                          </>
                        ),
                      },
                    }}
                  />
                )}
              />
              <TextField
                label={t("llm_models.revision_optional")}
                placeholder="main"
                value={hfRevision}
                onChange={(e) => setHfRevision(e.target.value)}
                fullWidth
              />
              <TextField
                label={t("llm_models.display_name_optional")}
                value={dlDisplayName}
                onChange={(e) => setDlDisplayName(e.target.value)}
                fullWidth
              />
            </DialogContent>
            <DialogActions>
              <Button
                onClick={() => {
                  setShowAdd(false);
                  resetForm();
                }}
              >
                {t("common.cancel")}
              </Button>
              <Button type="submit" variant="contained">
                {t("llm_models.start_download")}
              </Button>
            </DialogActions>
          </form>
        )}

        {addTab === 1 && (
          <form onSubmit={handleRegister}>
            <DialogContent
              sx={{
                display: "flex",
                flexDirection: "column",
                gap: 2,
                pt: "16px !important",
              }}
            >
              <TextField
                label={t("llm_models.display_name")}
                value={regName}
                onChange={(e) => setRegName(e.target.value)}
                required
                autoFocus
                fullWidth
              />
              <TextField
                label={t("llm_models.path_on_host")}
                placeholder="/srv/air-gap/models/my-model"
                value={regPath}
                onChange={(e) => setRegPath(e.target.value)}
                required
                fullWidth
              />
            </DialogContent>
            <DialogActions>
              <Button
                onClick={() => {
                  setShowAdd(false);
                  resetForm();
                }}
              >
                {t("common.cancel")}
              </Button>
              <Button type="submit" variant="contained">
                {t("llm_models.register")}
              </Button>
            </DialogActions>
          </form>
        )}
      </Dialog>

      {/* In-use guard dialog */}
      <Dialog
        open={inUseModel !== null}
        onClose={() => setInUseModel(null)}
        maxWidth="xs"
        fullWidth
      >
        <DialogTitle>{t("llm_models.in_use_title")}</DialogTitle>
        <DialogContent>
          <Typography variant="body2" sx={{ mb: 2 }}>
            {t("llm_models.in_use_message", {
              model: inUseModel?.display_name ?? "",
            })}
          </Typography>
          {inUseModel?.instances
            ?.filter(
              (i) =>
                i.runtime_status !== "stopped" && i.runtime_status !== "error",
            )
            .map((inst) => (
              <Stack
                key={inst.runtime_id}
                direction="row"
                alignItems="center"
                justifyContent="space-between"
                sx={{
                  py: 0.75,
                  px: 1,
                  mb: 0.5,
                  borderRadius: 1,
                  bgcolor: "action.hover",
                }}
              >
                <Stack>
                  <Typography
                    variant="body2"
                    fontWeight={600}
                    fontSize="0.85rem"
                  >
                    {inst.runtime_name}
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    {inst.provider_name} &middot; {inst.runtime_status}
                  </Typography>
                </Stack>
                <Button
                  size="small"
                  variant="outlined"
                  endIcon={<OpenInNewIcon fontSize="small" />}
                  onClick={() => {
                    setInUseModel(null);
                    navigate(`/admin/llm/runtimes/${inst.runtime_id}`);
                  }}
                >
                  {t("llm_models.go_to_runtime")}
                </Button>
              </Stack>
            ))}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setInUseModel(null)}>
            {t("common.close")}
          </Button>
        </DialogActions>
      </Dialog>
    </>
  );
}
