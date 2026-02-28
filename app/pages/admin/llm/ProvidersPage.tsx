/**
 * Admin → LLM → Providers (unified).
 *
 * Consolidates the former Providers + Runtimes pages into a single view.
 * - Local Docker providers own exactly one runtime → start/stop/restart inline.
 * - Remote Endpoint providers have an external URL and no container to manage.
 *
 * A multi-step wizard handles creation:
 *   Step 1  – Name, target (local / remote), engine (local only) or endpoint
 *   Step 2  – Runtime configuration (local only): model, image, advanced opts
 */
import { useState, useEffect, useCallback, useMemo } from "react";
import { Link as RouterLink } from "react-router";
import { useTranslation } from "react-i18next";
import {
  providers,
  runtimes,
  models as modelApi,
  type Provider,
  type ProviderType,
  type ProviderTarget,
  type Runtime,
  type Model,
  type CreateProviderPayload,
  type CreateRuntimePayload,
} from "~/api/llm";
import { hardware, type HardwareInfo, type VllmImagePreset } from "~/api/admin";
import { DataTable, type ColumnDef } from "~/components/DataTable";
import { EngineChip, RuntimeStatusChip } from "~/components/Chips";

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
import Step from "@mui/material/Step";
import StepLabel from "@mui/material/StepLabel";
import Stepper from "@mui/material/Stepper";
import TextField from "@mui/material/TextField";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";

import AddIcon from "@mui/icons-material/Add";
import DeleteIcon from "@mui/icons-material/Delete";
import EditIcon from "@mui/icons-material/Edit";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import OpenInNewIcon from "@mui/icons-material/OpenInNew";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import RestartAltIcon from "@mui/icons-material/RestartAlt";
import StopIcon from "@mui/icons-material/Stop";

// ── Constants ────────────────────────────────────────────────────────
const PROVIDER_TYPES: ProviderType[] = ["vllm", "llamacpp", "tgi", "ollama"];
const PROVIDER_TARGETS: ProviderTarget[] = ["local_docker", "remote_endpoint"];
const CUSTOM_IMAGE_VALUE = "__custom__";
const AUTO_IMAGE_VALUE = "__auto__";

function formatBytes(bytes: number): string {
  if (bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** i).toFixed(i > 0 ? 1 : 0)} ${units[i]}`;
}

// ── Joined row type ──────────────────────────────────────────────────
interface ProviderRow {
  provider: Provider;
  runtime: Runtime | null;
  model: Model | null;
}

export default function ProvidersPage() {
  const { t } = useTranslation();

  // ── Data ─────────────────────────────────────────────────────────
  const [providersList, setProvidersList] = useState<Provider[]>([]);
  const [runtimesList, setRuntimesList] = useState<Runtime[]>([]);
  const [modelsList, setModelsList] = useState<Model[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  // ── Wizard state ─────────────────────────────────────────────────
  const [showWizard, setShowWizard] = useState(false);
  const [wizardStep, setWizardStep] = useState(0);
  const [wizardBusy, setWizardBusy] = useState(false);

  // Step 1 — basics
  const [wName, setWName] = useState("");
  const [wTarget, setWTarget] = useState<ProviderTarget>("local_docker");
  const [wEngine, setWEngine] = useState<ProviderType>("vllm");
  const [wEndpointUrl, setWEndpointUrl] = useState("");
  const [wApiKey, setWApiKey] = useState("");

  // Step 2 — runtime config (local only)
  const [wModelId, setWModelId] = useState("");
  const [hwInfo, setHwInfo] = useState<HardwareInfo | null>(null);
  const [hwLoading, setHwLoading] = useState(false);
  const [imageChoice, setImageChoice] = useState(AUTO_IMAGE_VALUE);
  const [customImage, setCustomImage] = useState("");
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [maxModelLen, setMaxModelLen] = useState("");
  const [dtype, setDtype] = useState("");
  const [gpuMemUtil, setGpuMemUtil] = useState("");
  const [tensorParallel, setTensorParallel] = useState("");
  const [extraArgs, setExtraArgs] = useState("");
  const [openaiCompat, setOpenaiCompat] = useState(true);

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

  const imagePresets = useMemo<VllmImagePreset[]>(
    () => hwInfo?.vllm_image_presets ?? [],
    [hwInfo],
  );

  // ── Data loading ─────────────────────────────────────────────────
  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [p, r, m] = await Promise.all([
        providers.list(),
        runtimes.list(),
        modelApi.list(),
      ]);
      setProvidersList(p);
      setRuntimesList(r);
      setModelsList(m);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t("llm_providers.failed_load"));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    load();
  }, [load]);

  // Fetch hardware info when wizard advances to step 2 (local only)
  useEffect(() => {
    if (!showWizard || wizardStep !== 1 || wTarget !== "local_docker") return;
    let cancelled = false;
    setHwLoading(true);
    hardware
      .info()
      .then((info) => {
        if (!cancelled) {
          setHwInfo(info);
          const rec = info.vllm_image_presets.find((p) => p.is_recommended);
          if (rec) setImageChoice(rec.image);
        }
      })
      .catch(() => {})
      .finally(() => {
        if (!cancelled) setHwLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [showWizard, wizardStep, wTarget]);

  // ── Wizard helpers ───────────────────────────────────────────────
  function resetWizard() {
    setWizardStep(0);
    setWizardBusy(false);
    setWName("");
    setWTarget("local_docker");
    setWEngine("vllm");
    setWEndpointUrl("");
    setWApiKey("");
    setWModelId("");
    setHwInfo(null);
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

  function closeWizard() {
    setShowWizard(false);
    resetWizard();
  }

  const wizardSteps =
    wTarget === "local_docker"
      ? [t("llm_providers.wizard_step_basics"), t("llm_providers.wizard_step_runtime")]
      : [t("llm_providers.wizard_step_basics")];

  const isLastStep = wizardStep >= wizardSteps.length - 1;

  async function handleWizardFinish() {
    setWizardBusy(true);
    try {
      // 1. Create provider
      const provPayload: CreateProviderPayload = {
        name: wName,
        type: wTarget === "local_docker" ? wEngine : "vllm",
        target: wTarget,
        ...(wTarget === "remote_endpoint" && wEndpointUrl && { endpoint_url: wEndpointUrl }),
        ...(wTarget === "remote_endpoint" && wApiKey && { api_key: wApiKey }),
      };
      const newProv = await providers.create(provPayload);

      // 2. For local providers, create the runtime immediately
      if (wTarget === "local_docker") {
        const generic_config: Record<string, unknown> = {};
        if (maxModelLen) generic_config.max_model_len = Number(maxModelLen);
        if (dtype) generic_config.dtype = dtype;
        if (gpuMemUtil) generic_config.gpu_memory_utilization = Number(gpuMemUtil);
        if (tensorParallel) generic_config.tensor_parallel_size = Number(tensorParallel);

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

        const rtPayload: CreateRuntimePayload = {
          name: wName,
          provider_id: newProv.id,
          model_id: wModelId,
          openai_compat: openaiCompat,
          ...(Object.keys(generic_config).length > 0 && { generic_config }),
          ...(Object.keys(provider_config).length > 0 && { provider_config }),
        };
        await runtimes.create(rtPayload);
      }

      closeWizard();
      await load();
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : t("common.create_failed"));
    } finally {
      setWizardBusy(false);
    }
  }

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
        const isRunning = rt?.status === "running";
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
      <Dialog
        open={showWizard}
        onClose={closeWizard}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>{t("llm_providers.new_provider")}</DialogTitle>
        <DialogContent sx={{ display: "flex", flexDirection: "column", gap: 2, pt: "8px !important" }}>
          <Stepper activeStep={wizardStep} sx={{ mb: 1 }}>
            {wizardSteps.map((label) => (
              <Step key={label}>
                <StepLabel>{label}</StepLabel>
              </Step>
            ))}
          </Stepper>

          {/* ── Step 1: Basics ──────────────────────────────────── */}
          {wizardStep === 0 && (
            <>
              <TextField
                label={t("common.name")}
                value={wName}
                onChange={(e) => setWName(e.target.value)}
                required
                autoFocus
                fullWidth
              />
              <FormControl fullWidth>
                <InputLabel>{t("llm_providers.target")}</InputLabel>
                <Select
                  value={wTarget}
                  label={t("llm_providers.target")}
                  onChange={(e) => setWTarget(e.target.value as ProviderTarget)}
                >
                  {PROVIDER_TARGETS.map((tgt) => (
                    <MenuItem key={tgt} value={tgt}>
                      {t(`llm_providers.target_${tgt}`)}
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>

              {/* Local Docker — engine picker */}
              {wTarget === "local_docker" && (
                <FormControl fullWidth>
                  <InputLabel>{t("llm_common.engine")}</InputLabel>
                  <Select
                    value={wEngine}
                    label={t("llm_common.engine")}
                    onChange={(e) => setWEngine(e.target.value as ProviderType)}
                  >
                    {PROVIDER_TYPES.map((pt) => (
                      <MenuItem key={pt} value={pt}>
                        {pt}
                      </MenuItem>
                    ))}
                  </Select>
                </FormControl>
              )}

              {/* Remote Endpoint — URL + key */}
              {wTarget === "remote_endpoint" && (
                <>
                  <TextField
                    label={t("llm_providers.endpoint_url")}
                    placeholder="https://api.example.com/v1"
                    value={wEndpointUrl}
                    onChange={(e) => setWEndpointUrl(e.target.value)}
                    required
                    fullWidth
                    helperText={t("llm_providers.endpoint_url_help")}
                  />
                  <TextField
                    label={t("llm_providers.api_key")}
                    type="password"
                    value={wApiKey}
                    onChange={(e) => setWApiKey(e.target.value)}
                    fullWidth
                    helperText={t("llm_providers.api_key_help")}
                  />
                </>
              )}
            </>
          )}

          {/* ── Step 2: Runtime config (local Docker only) ──────── */}
          {wizardStep === 1 && wTarget === "local_docker" && (
            <>
              {/* GPU banner */}
              {hwInfo && (
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

              {/* Model selector */}
              <FormControl fullWidth required>
                <InputLabel>{t("llm_common.model")}</InputLabel>
                <Select
                  value={wModelId}
                  label={t("llm_common.model")}
                  onChange={(e) => setWModelId(e.target.value)}
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

              {/* Container image picker */}
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

              {/* Advanced options */}
              <Accordion
                expanded={advancedOpen}
                onChange={(_, expanded) => setAdvancedOpen(expanded)}
                disableGutters
                elevation={0}
                sx={{ border: 1, borderColor: "divider", borderRadius: 1, "&::before": { display: "none" } }}
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
        </DialogContent>

        <DialogActions>
          <Button onClick={closeWizard} disabled={wizardBusy}>
            {t("common.cancel")}
          </Button>
          {wizardStep > 0 && (
            <Button onClick={() => setWizardStep((s) => s - 1)} disabled={wizardBusy}>
              {t("common.back")}
            </Button>
          )}
          {isLastStep ? (
            <Button
              variant="contained"
              disabled={wizardBusy || !wName}
              onClick={() => void handleWizardFinish()}
            >
              {wizardBusy ? t("common.creating") : t("common.create")}
            </Button>
          ) : (
            <Button
              variant="contained"
              disabled={!wName}
              onClick={() => setWizardStep((s) => s + 1)}
            >
              {t("common.next")}
            </Button>
          )}
        </DialogActions>
      </Dialog>

      {/* ── Edit dialog ─────────────────────────────────────────── */}
      <Dialog
        open={!!editTarget}
        onClose={() => setEditTarget(null)}
        maxWidth="xs"
        fullWidth
      >
        <form onSubmit={handleUpdate}>
          <DialogTitle>{t("llm_providers.edit_provider")}</DialogTitle>
          <DialogContent sx={{ pt: "8px !important" }}>
            <TextField
              label={t("common.name")}
              value={editName}
              onChange={(e) => setEditName(e.target.value)}
              required
              autoFocus
              fullWidth
            />
          </DialogContent>
          <DialogActions>
            <Button onClick={() => setEditTarget(null)}>{t("common.cancel")}</Button>
            <Button type="submit" variant="contained">
              {t("common.save")}
            </Button>
          </DialogActions>
        </form>
      </Dialog>
    </>
  );
}
