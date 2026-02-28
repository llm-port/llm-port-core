/**
 * Admin → LLM → Runtimes list page.
 * Create, start, stop, restart, delete LLM serving runtimes.
 *
 * The create dialog now includes:
 *  - GPU hardware detection with VRAM display
 *  - Image preset picker  (built-in + admin-defined via env var)
 *  - Custom image text field (for NVIDIA NGC, private registries, etc.)
 *  - Advanced options: generic_config, provider_config, openai_compat
 */
import { useState, useEffect, useCallback, useMemo } from "react";
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
import { hardware, type HardwareInfo, type VllmImagePreset } from "~/api/admin";
import { DataTable, type ColumnDef } from "~/components/DataTable";
import { RuntimeStatusChip, EngineChip } from "~/components/Chips";

import Accordion from "@mui/material/Accordion";
import AccordionDetails from "@mui/material/AccordionDetails";
import AccordionSummary from "@mui/material/AccordionSummary";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Checkbox from "@mui/material/Checkbox";
import Chip from "@mui/material/Chip";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import FormControl from "@mui/material/FormControl";
import FormControlLabel from "@mui/material/FormControlLabel";
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
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import StopIcon from "@mui/icons-material/Stop";
import RestartAltIcon from "@mui/icons-material/RestartAlt";
import OpenInNewIcon from "@mui/icons-material/OpenInNew";

// ── Helpers ──────────────────────────────────────────────────────────
const CUSTOM_IMAGE_VALUE = "__custom__";
const AUTO_IMAGE_VALUE = "__auto__";

function formatBytes(bytes: number): string {
  if (bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** i).toFixed(i > 0 ? 1 : 0)} ${units[i]}`;
}

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

  // Image selection
  const [hwInfo, setHwInfo] = useState<HardwareInfo | null>(null);
  const [hwLoading, setHwLoading] = useState(false);
  const [imageChoice, setImageChoice] = useState(AUTO_IMAGE_VALUE);
  const [customImage, setCustomImage] = useState("");

  // Advanced options
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [maxModelLen, setMaxModelLen] = useState("");
  const [dtype, setDtype] = useState("");
  const [gpuMemUtil, setGpuMemUtil] = useState("");
  const [tensorParallel, setTensorParallel] = useState("");
  const [extraArgs, setExtraArgs] = useState("");
  const [openaiCompat, setOpenaiCompat] = useState(true);

  // lookup maps
  const provMap = Object.fromEntries(providersList.map((p) => [p.id, p]));
  const modelMap = Object.fromEntries(modelsList.map((m) => [m.id, m]));

  // Image presets from hardware endpoint
  const imagePresets = useMemo<VllmImagePreset[]>(
    () => hwInfo?.vllm_image_presets ?? [],
    [hwInfo],
  );

  // Determine if the selected provider is remote (no Docker needed)
  const selectedProviderIsRemote = useMemo(
    () => providersList.find((p) => p.id === cProviderId)?.target === "remote_endpoint",
    [providersList, cProviderId],
  );

  const load = useCallback(async () => {
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
  }, [t]);

  useEffect(() => {
    load();
  }, [load]);

  // Fetch hardware info when the create dialog opens
  useEffect(() => {
    if (!showCreate) return;
    let cancelled = false;
    setHwLoading(true);
    hardware
      .info()
      .then((info) => {
        if (!cancelled) {
          setHwInfo(info);
          // Pre-select the recommended preset
          const rec = info.vllm_image_presets.find((p) => p.is_recommended);
          if (rec) setImageChoice(rec.image);
        }
      })
      .catch(() => {
        // non-fatal — the user can still type a custom image
      })
      .finally(() => {
        if (!cancelled) setHwLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [showCreate]);

  function resetCreateForm() {
    setCName("");
    setCProviderId("");
    setCModelId("");
    setImageChoice(AUTO_IMAGE_VALUE);
    setCustomImage("");
    setAdvancedOpen(false);
    setMaxModelLen("");
    setDtype("");
    setGpuMemUtil("");
    setTensorParallel("");
    setExtraArgs("");
    setOpenaiCompat(true);
  }

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();

    // Build generic_config from advanced fields
    const generic_config: Record<string, unknown> = {};
    if (maxModelLen) generic_config.max_model_len = Number(maxModelLen);
    if (dtype) generic_config.dtype = dtype;
    if (gpuMemUtil) generic_config.gpu_memory_utilization = Number(gpuMemUtil);
    if (tensorParallel) generic_config.tensor_parallel_size = Number(tensorParallel);

    // Build provider_config — image override
    const provider_config: Record<string, unknown> = {};
    const resolvedImage =
      imageChoice === CUSTOM_IMAGE_VALUE
        ? customImage.trim()
        : imageChoice === AUTO_IMAGE_VALUE
          ? undefined
          : imageChoice;
    if (resolvedImage) provider_config.image = resolvedImage;
    if (extraArgs) {
      provider_config.extra_args = extraArgs
        .split(",")
        .map((a) => a.trim())
        .filter(Boolean);
    }

    const payload: CreateRuntimePayload = {
      name: cName,
      provider_id: cProviderId,
      model_id: cModelId,
      openai_compat: openaiCompat,
      ...(Object.keys(generic_config).length > 0 && { generic_config }),
      ...(Object.keys(provider_config).length > 0 && { provider_config }),
    };
    try {
      await runtimes.create(payload);
      setShowCreate(false);
      resetCreateForm();
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

            {/* ── GPU info banner ─────────────────────────────── */}
            {!selectedProviderIsRemote && hwInfo && (
              <Alert
                severity={hwInfo.gpu.has_gpu ? "success" : "warning"}
                variant="outlined"
                sx={{ py: 0.5 }}
              >
                {hwInfo.gpu.has_gpu
                  ? t("llm_runtimes.gpu_detected", {
                      model: hwInfo.gpu.devices[0]?.model ?? hwInfo.gpu.primary_vendor,
                      vram: formatBytes(hwInfo.gpu.total_vram_bytes),
                    })
                  : t("llm_runtimes.gpu_none")}
              </Alert>
            )}

            {/* ── Basic fields ────────────────────────────────── */}
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

            {/* ── Docker-only options (hidden for remote providers) ── */}
            {!selectedProviderIsRemote && (
              <>
                {/* ── Container image selector ────────────────────── */}
                <FormControl fullWidth>
              <InputLabel>{t("llm_runtimes.container_image")}</InputLabel>
              <Select
                value={imageChoice}
                label={t("llm_runtimes.container_image")}
                onChange={(e) => setImageChoice(e.target.value)}
                disabled={hwLoading}
              >
                <MenuItem value={AUTO_IMAGE_VALUE}>
                  <Stack direction="row" spacing={1} alignItems="center">
                    <Typography variant="body2">{t("llm_runtimes.image_auto")}</Typography>
                  </Stack>
                </MenuItem>
                {imagePresets.map((preset) => (
                  <MenuItem key={preset.image} value={preset.image}>
                    <Stack direction="row" spacing={1} alignItems="center" sx={{ width: "100%" }}>
                      <Box sx={{ flex: 1 }}>
                        <Typography variant="body2">{preset.label}</Typography>
                        <Typography variant="caption" color="text.secondary" sx={{ fontFamily: "monospace" }}>
                          {preset.image}
                        </Typography>
                      </Box>
                      {preset.is_recommended && (
                        <Chip label={t("llm_runtimes.recommended_tag")} size="small" color="success" variant="outlined" />
                      )}
                    </Stack>
                  </MenuItem>
                ))}
                <MenuItem value={CUSTOM_IMAGE_VALUE}>
                  <Typography variant="body2" color="primary">
                    {t("llm_runtimes.image_custom")}
                  </Typography>
                </MenuItem>
              </Select>
            </FormControl>

            {/* Custom image text field */}
            {imageChoice === CUSTOM_IMAGE_VALUE && (
              <TextField
                label={t("llm_runtimes.container_image")}
                placeholder={t("llm_runtimes.image_custom_placeholder")}
                value={customImage}
                onChange={(e) => setCustomImage(e.target.value)}
                required
                fullWidth
                sx={{ fontFamily: "monospace" }}
              />
            )}

            {/* ── Advanced options accordion ──────────────────── */}
            <Accordion
              expanded={advancedOpen}
              onChange={(_, expanded) => setAdvancedOpen(expanded)}
              disableGutters
              elevation={0}
              sx={{
                border: 1,
                borderColor: "divider",
                borderRadius: 1,
                "&::before": { display: "none" },
              }}
            >
              <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                <Typography variant="subtitle2">{t("llm_runtimes.advanced_options")}</Typography>
              </AccordionSummary>
              <AccordionDetails sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
                <Stack direction="row" spacing={2}>
                  <TextField
                    label={t("llm_runtimes.max_model_len")}
                    type="number"
                    value={maxModelLen}
                    onChange={(e) => setMaxModelLen(e.target.value)}
                    fullWidth
                    size="small"
                  />
                  <FormControl fullWidth size="small">
                    <InputLabel>{t("llm_runtimes.dtype")}</InputLabel>
                    <Select
                      value={dtype}
                      label={t("llm_runtimes.dtype")}
                      onChange={(e) => setDtype(e.target.value)}
                    >
                      <MenuItem value="">auto</MenuItem>
                      <MenuItem value="float16">float16</MenuItem>
                      <MenuItem value="bfloat16">bfloat16</MenuItem>
                      <MenuItem value="float32">float32</MenuItem>
                    </Select>
                  </FormControl>
                </Stack>
                <Stack direction="row" spacing={2}>
                  <TextField
                    label={t("llm_runtimes.gpu_memory_util")}
                    type="number"
                    inputProps={{ min: 0.1, max: 1.0, step: 0.05 }}
                    value={gpuMemUtil}
                    onChange={(e) => setGpuMemUtil(e.target.value)}
                    fullWidth
                    size="small"
                  />
                  <TextField
                    label={t("llm_runtimes.tensor_parallel")}
                    type="number"
                    inputProps={{ min: 1, step: 1 }}
                    value={tensorParallel}
                    onChange={(e) => setTensorParallel(e.target.value)}
                    fullWidth
                    size="small"
                  />
                </Stack>
                <TextField
                  label={t("llm_runtimes.extra_args")}
                  helperText={t("llm_runtimes.extra_args_help")}
                  value={extraArgs}
                  onChange={(e) => setExtraArgs(e.target.value)}
                  fullWidth
                  size="small"
                />
                <FormControlLabel
                  control={
                    <Checkbox
                      checked={openaiCompat}
                      onChange={(e) => setOpenaiCompat(e.target.checked)}
                    />
                  }
                  label={t("llm_runtimes.openai_compat")}
                />
              </AccordionDetails>
            </Accordion>
              </>
            )}

            {/* Remote provider hint */}
            {selectedProviderIsRemote && (
              <Alert severity="info" variant="outlined" sx={{ py: 0.5 }}>
                {t("llm_runtimes.remote_provider_hint")}
              </Alert>
            )}
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
