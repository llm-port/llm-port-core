/**
 * ProviderWizardDialog — multi-step wizard for creating a new Provider.
 *
 * Step 1: Name, target (local / remote), engine, endpoint.
 * Step 2: Runtime config for local Docker providers.
 */
import { useState, useEffect, useMemo, useRef, useCallback } from "react";
import { useTranslation } from "react-i18next";
import {
  providers,
  runtimes,
  type ProviderType,
  type ProviderTarget,
  type Model,
  type CreateProviderPayload,
  type CreateRuntimePayload,
} from "~/api/llm";
import { hardware, images, type HardwareInfo, type VllmImagePreset, type PullProgressEvent } from "~/api/admin";

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
import InputLabel from "@mui/material/InputLabel";
import MenuItem from "@mui/material/MenuItem";
import Select from "@mui/material/Select";
import Stack from "@mui/material/Stack";
import Step from "@mui/material/Step";
import StepLabel from "@mui/material/StepLabel";
import Stepper from "@mui/material/Stepper";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import CircularProgress from "@mui/material/CircularProgress";
import LinearProgress from "@mui/material/LinearProgress";

import DownloadIcon from "@mui/icons-material/Download";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import NetworkCheckIcon from "@mui/icons-material/NetworkCheck";

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

// ── Props ────────────────────────────────────────────────────────────

export interface ProviderWizardDialogProps {
  open: boolean;
  /** Available models for the runtime step. */
  models: Model[];
  onClose: () => void;
  /** Called after a provider (and optional runtime) is created. */
  onCreated: () => void;
}

export function ProviderWizardDialog({ open, models, onClose, onCreated }: ProviderWizardDialogProps) {
  const { t } = useTranslation();

  // ── Wizard state ─────────────────────────────────────────────────
  const [step, setStep] = useState(0);
  const [busy, setBusy] = useState(false);

  // Step 1 — basics
  const [name, setName] = useState("");
  const [target, setTarget] = useState<ProviderTarget>("local_docker");
  const [engine, setEngine] = useState<ProviderType>("vllm");
  const [endpointUrl, setEndpointUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [testStatus, setTestStatus] = useState<"idle" | "testing" | "success" | "error">("idle");
  const [testMessage, setTestMessage] = useState("");

  // Step 2 — runtime config (local only)
  const [modelId, setModelId] = useState("");
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

  // Image availability check & pull
  const [imageStatus, setImageStatus] = useState<"idle" | "checking" | "available" | "missing" | "pulling" | "pulled" | "error">("idle");
  const [imageError, setImageError] = useState("");
  const [pullPercent, setPullPercent] = useState(0);
  const [pullLayers, setPullLayers] = useState({ done: 0, total: 0 });
  const sseRef = useRef<EventSource | null>(null);

  const imagePresets = useMemo<VllmImagePreset[]>(
    () => hwInfo?.vllm_image_presets ?? [],
    [hwInfo],
  );

  // Reset on close
  useEffect(() => {
    if (!open) {
      setStep(0);
      setBusy(false);
      setName("");
      setTarget("local_docker");
      setEngine("vllm");
      setEndpointUrl("");
      setApiKey("");
      setTestStatus("idle");
      setTestMessage("");
      setModelId("");
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
      setImageStatus("idle");
      setImageError("");
      setPullPercent(0);
      setPullLayers({ done: 0, total: 0 });
      if (sseRef.current) {
        sseRef.current.close();
        sseRef.current = null;
      }
    }
  }, [open]);

  // Fetch hardware info for step 2
  useEffect(() => {
    if (!open || step !== 1 || target !== "local_docker") return;
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
    return () => { cancelled = true; };
  }, [open, step, target]);

  // ── Subscribe to pull progress via SSE ──────────────────────────
  const subscribeToPull = useCallback((pullId: string) => {
    // Clean up any prior SSE connection
    if (sseRef.current) {
      sseRef.current.close();
      sseRef.current = null;
    }
    setImageStatus("pulling");
    setPullPercent(0);
    setPullLayers({ done: 0, total: 0 });

    const source = images.pullProgress(
      pullId,
      (data: PullProgressEvent) => {
        setPullPercent(data.percent ?? 0);
        setPullLayers({ done: data.layers_done ?? 0, total: data.layers_total ?? 0 });
      },
      (_data: PullProgressEvent) => {
        setImageStatus("pulled");
        setPullPercent(100);
        sseRef.current = null;
      },
      (data: PullProgressEvent | null) => {
        setImageStatus("error");
        setImageError(data?.error ?? "Connection lost");
        sseRef.current = null;
      },
    );
    sseRef.current = source;
  }, []);

  // Clean up SSE on unmount
  useEffect(() => {
    return () => {
      if (sseRef.current) {
        sseRef.current.close();
        sseRef.current = null;
      }
    };
  }, []);

  // ── Check image availability when selection changes ──────────────
  useEffect(() => {
    if (!open || step !== 1 || target !== "local_docker") return;

    // Resolve the actual image string
    let resolvedImage: string | undefined;
    if (imageChoice === AUTO_IMAGE_VALUE) {
      resolvedImage = hwInfo?.recommended_vllm_image ?? undefined;
    } else if (imageChoice === CUSTOM_IMAGE_VALUE) {
      resolvedImage = customImage.trim() || undefined;
    } else {
      resolvedImage = imageChoice;
    }
    if (!resolvedImage) {
      setImageStatus("idle");
      return;
    }

    // Parse image:tag
    const lastColon = resolvedImage.lastIndexOf(":");
    const imageName = lastColon > 0 ? resolvedImage.slice(0, lastColon) : resolvedImage;
    const imageTag = lastColon > 0 ? resolvedImage.slice(lastColon + 1) : "latest";

    let cancelled = false;
    setImageStatus("checking");
    setImageError("");
    images
      .check(imageName, imageTag)
      .then((res) => {
        if (!cancelled) {
          if (res.pulling && res.pull_id) {
            // A pull is already in progress — subscribe to it
            subscribeToPull(res.pull_id);
          } else {
            setImageStatus(res.exists ? "available" : "missing");
          }
        }
      })
      .catch(() => {
        if (!cancelled) setImageStatus("idle");
      });
    return () => { cancelled = true; };
  }, [open, step, target, imageChoice, customImage, hwInfo, subscribeToPull]);

  // ── Test connection handler ──────────────────────────────────────
  async function handleTestConnection() {
    setTestStatus("testing");
    setTestMessage("");
    try {
      const result = await providers.testEndpoint({
        endpoint_url: endpointUrl,
        ...(apiKey && { api_key: apiKey }),
      });
      if (result.compatible) {
        setTestStatus("success");
        setTestMessage(t("llm_providers.test_connection_success", { count: result.models.length }));
      } else {
        setTestStatus("error");
        setTestMessage(result.error ?? t("llm_providers.test_connection_failed"));
      }
    } catch {
      setTestStatus("error");
      setTestMessage(t("llm_providers.test_connection_failed"));
    }
  }

  const steps =
    target === "local_docker"
      ? [t("llm_providers.wizard_step_basics"), t("llm_providers.wizard_step_runtime")]
      : [t("llm_providers.wizard_step_basics")];
  const isLast = step >= steps.length - 1;

  // ── Pull image handler ────────────────────────────────────────────
  async function handlePullImage() {
    let resolvedImage: string | undefined;
    if (imageChoice === AUTO_IMAGE_VALUE) {
      resolvedImage = hwInfo?.recommended_vllm_image ?? undefined;
    } else if (imageChoice === CUSTOM_IMAGE_VALUE) {
      resolvedImage = customImage.trim() || undefined;
    } else {
      resolvedImage = imageChoice;
    }
    if (!resolvedImage) return;

    const lastColon = resolvedImage.lastIndexOf(":");
    const imageName = lastColon > 0 ? resolvedImage.slice(0, lastColon) : resolvedImage;
    const imageTag = lastColon > 0 ? resolvedImage.slice(lastColon + 1) : "latest";

    try {
      const result = await images.pull(imageName, imageTag);
      subscribeToPull(result.pull_id);
    } catch (err: unknown) {
      setImageStatus("error");
      setImageError(err instanceof Error ? err.message : String(err));
    }
  }

  async function handleFinish() {
    setBusy(true);
    let newProvId: string | undefined;
    try {
      const provPayload: CreateProviderPayload = {
        name,
        type: target === "local_docker" ? engine : "vllm",
        target,
        ...(target === "remote_endpoint" && endpointUrl && { endpoint_url: endpointUrl }),
        ...(target === "remote_endpoint" && apiKey && { api_key: apiKey }),
      };
      const newProv = await providers.create(provPayload);
      newProvId = newProv.id;

      if (target === "local_docker") {
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
          name,
          provider_id: newProv.id,
          model_id: modelId,
          openai_compat: openaiCompat,
          ...(Object.keys(generic_config).length > 0 && { generic_config }),
          ...(Object.keys(provider_config).length > 0 && { provider_config }),
        };
        await runtimes.create(rtPayload);
      }

      onClose();
      onCreated();
    } catch (err: unknown) {
      // Rollback: delete the orphan provider if runtime creation failed
      if (newProvId) {
        try { await providers.delete(newProvId); } catch { /* best-effort */ }
      }
      alert(err instanceof Error ? err.message : t("common.create_failed"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>{t("llm_providers.new_provider")}</DialogTitle>
      <DialogContent sx={{ display: "flex", flexDirection: "column", gap: 2, pt: "8px !important" }}>
        <Stepper activeStep={step} sx={{ mb: 1 }}>
          {steps.map((label) => (
            <Step key={label}>
              <StepLabel>{label}</StepLabel>
            </Step>
          ))}
        </Stepper>

        {/* ── Step 1: Basics ──────────────────────────────────── */}
        {step === 0 && (
          <>
            <TextField
              label={t("common.name")}
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
              autoFocus
              fullWidth
            />
            <FormControl fullWidth>
              <InputLabel>{t("llm_providers.target")}</InputLabel>
              <Select
                value={target}
                label={t("llm_providers.target")}
                onChange={(e) => setTarget(e.target.value as ProviderTarget)}
              >
                {PROVIDER_TARGETS.map((tgt) => (
                  <MenuItem key={tgt} value={tgt}>
                    {t(`llm_providers.target_${tgt}`)}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>

            {target === "local_docker" && (
              <FormControl fullWidth>
                <InputLabel>{t("llm_common.engine")}</InputLabel>
                <Select
                  value={engine}
                  label={t("llm_common.engine")}
                  onChange={(e) => setEngine(e.target.value as ProviderType)}
                >
                  {PROVIDER_TYPES.map((pt) => (
                    <MenuItem key={pt} value={pt}>
                      {pt}
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>
            )}

            {target === "remote_endpoint" && (
              <>
                <TextField
                  label={t("llm_providers.endpoint_url")}
                  placeholder="https://api.example.com/v1"
                  value={endpointUrl}
                  onChange={(e) => { setEndpointUrl(e.target.value); setTestStatus("idle"); }}
                  required
                  fullWidth
                  helperText={t("llm_providers.endpoint_url_help")}
                />
                <TextField
                  label={t("llm_providers.api_key")}
                  type="password"
                  value={apiKey}
                  onChange={(e) => { setApiKey(e.target.value); setTestStatus("idle"); }}
                  fullWidth
                  helperText={t("llm_providers.api_key_help")}
                />

                <Button
                  variant="outlined"
                  size="small"
                  startIcon={
                    testStatus === "testing"
                      ? <CircularProgress size={16} color="inherit" />
                      : <NetworkCheckIcon />
                  }
                  disabled={!endpointUrl || testStatus === "testing"}
                  onClick={() => void handleTestConnection()}
                  sx={{ alignSelf: "flex-start" }}
                >
                  {testStatus === "testing"
                    ? t("llm_providers.test_connection_testing")
                    : t("llm_providers.test_connection")}
                </Button>

                {testStatus === "success" && (
                  <Alert severity="success" variant="outlined" sx={{ py: 0.5 }}>
                    {testMessage}
                  </Alert>
                )}
                {testStatus === "error" && (
                  <Alert severity="error" variant="outlined" sx={{ py: 0.5 }}>
                    {testMessage}
                  </Alert>
                )}
              </>
            )}
          </>
        )}

        {/* ── Step 2: Runtime config (local Docker only) ──────── */}
        {step === 1 && target === "local_docker" && (
          <>
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

            <FormControl fullWidth required>
              <InputLabel>{t("llm_common.model")}</InputLabel>
              <Select
                value={modelId}
                label={t("llm_common.model")}
                onChange={(e) => setModelId(e.target.value)}
              >
                {models
                  .filter((m) => m.status === "available")
                  .map((m) => (
                    <MenuItem key={m.id} value={m.id}>
                      {m.display_name}
                    </MenuItem>
                  ))}
              </Select>
            </FormControl>

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

            {/* ── Image availability check & pull ────────────── */}
            {imageStatus === "checking" && (
              <Alert severity="info" variant="outlined" icon={<CircularProgress size={18} />} sx={{ py: 0.5 }}>
                {t("llm_runtimes.image_checking")}
              </Alert>
            )}
            {imageStatus === "available" && (
              <Alert severity="success" variant="outlined" sx={{ py: 0.5 }}>
                {t("llm_runtimes.image_available")}
              </Alert>
            )}
            {(imageStatus === "pulled") && (
              <Alert severity="success" variant="outlined" sx={{ py: 0.5 }}>
                {t("llm_runtimes.image_pull_success")}
              </Alert>
            )}
            {imageStatus === "missing" && (
              <Alert
                severity="warning"
                variant="outlined"
                sx={{ py: 0.5 }}
                action={
                  <Button
                    size="small"
                    startIcon={<DownloadIcon />}
                    onClick={() => void handlePullImage()}
                  >
                    {t("llm_runtimes.image_pull_button")}
                  </Button>
                }
              >
                {t("llm_runtimes.image_not_local")}
              </Alert>
            )}
            {imageStatus === "pulling" && (
              <Alert severity="info" variant="outlined" sx={{ py: 1 }}>
                <Stack spacing={1} sx={{ width: "100%" }}>
                  <Stack direction="row" justifyContent="space-between" alignItems="center">
                    <Typography variant="body2">
                      {t("llm_runtimes.image_pulling")}
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      {pullPercent > 0
                        ? `${pullPercent}%${pullLayers.total > 0 ? ` · ${pullLayers.done}/${pullLayers.total} ${t("llm_runtimes.image_pull_layers")}` : ""}`
                        : t("llm_runtimes.image_pull_starting")}
                    </Typography>
                  </Stack>
                  <LinearProgress
                    variant={pullPercent > 0 ? "determinate" : "indeterminate"}
                    value={pullPercent}
                    sx={{ borderRadius: 1 }}
                  />
                </Stack>
              </Alert>
            )}
            {imageStatus === "error" && (
              <Alert
                severity="error"
                variant="outlined"
                sx={{ py: 0.5 }}
                action={
                  <Button
                    size="small"
                    startIcon={<DownloadIcon />}
                    onClick={() => void handlePullImage()}
                  >
                    {t("llm_runtimes.image_pull_button")}
                  </Button>
                }
              >
                {t("llm_runtimes.image_pull_failed", { error: imageError })}
              </Alert>
            )}

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
        <Button onClick={onClose} disabled={busy}>
          {t("common.cancel")}
        </Button>
        {step > 0 && (
          <Button onClick={() => setStep((s) => s - 1)} disabled={busy}>
            {t("common.back")}
          </Button>
        )}
        {isLast ? (
          <Button
            variant="contained"
            disabled={busy || !name || (target === "local_docker" && (imageStatus === "missing" || imageStatus === "pulling" || imageStatus === "checking"))}
            onClick={() => void handleFinish()}
          >
            {busy ? t("common.creating") : t("common.create")}
          </Button>
        ) : (
          <Button
            variant="contained"
            disabled={!name}
            onClick={() => setStep((s) => s + 1)}
          >
            {t("common.next")}
          </Button>
        )}
      </DialogActions>
    </Dialog>
  );
}
