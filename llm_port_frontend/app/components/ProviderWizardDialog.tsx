/**
 * ProviderWizardDialog — multi-step wizard for creating a new Provider.
 *
 * Step 1: Name, target (local / remote), engine, endpoint, deployment target.
 * Step 2: Runtime config for local Docker providers.
 */
import { useState, useEffect, useMemo, useRef, useCallback } from "react";
import { useTranslation } from "react-i18next";
import {
  providers,
  runtimes,
  models as modelsApi,
  type ProviderType,
  type ProviderTarget,
  type Model,
  type CreateProviderPayload,
  type CreateRuntimePayload,
} from "~/api/llm";
import {
  hardware,
  images,
  type HardwareInfo,
  type VllmImagePreset,
  type PullProgressEvent,
} from "~/api/admin";
import { nodesApi, type ManagedNode } from "~/api/nodes";
import { observability } from "~/api/observability";
import { HfModelSearch } from "~/components/HfModelSearch";

import Accordion from "@mui/material/Accordion";
import AccordionDetails from "@mui/material/AccordionDetails";
import AccordionSummary from "@mui/material/AccordionSummary";
import Alert from "@mui/material/Alert";
import Autocomplete from "@mui/material/Autocomplete";
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
import Radio from "@mui/material/Radio";
import RadioGroup from "@mui/material/RadioGroup";
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

import { VllmEngineArgsPanel } from "~/components/VllmEngineArgsPanel";
import {
  ContainerResourcesPanel,
  type ContainerResourceValues,
} from "~/components/ContainerResourcesPanel";

// ── Constants ────────────────────────────────────────────────────────
const PROVIDER_TYPES: ProviderType[] = ["vllm", "llamacpp", "tgi", "ollama"];
const PROVIDER_TARGETS: ProviderTarget[] = ["local_docker", "remote_endpoint"];
const CUSTOM_IMAGE_VALUE = "__custom__";
const AUTO_IMAGE_VALUE = "__auto__";

/** Known LiteLLM provider prefixes shown in the remote-endpoint dropdown. */
const LITELLM_PROVIDERS: { value: string; label: string }[] = [
  { value: "openai", label: "OpenAI" },
  { value: "anthropic", label: "Anthropic" },
  { value: "gemini", label: "Google Gemini" },
  { value: "vertex_ai", label: "Google Vertex AI" },
  { value: "bedrock", label: "AWS Bedrock" },
  { value: "azure", label: "Azure OpenAI" },
  { value: "azure_ai", label: "Azure AI" },
  { value: "mistral", label: "Mistral" },
  { value: "groq", label: "Groq" },
  { value: "deepseek", label: "DeepSeek" },
  { value: "cohere", label: "Cohere" },
  { value: "openrouter", label: "OpenRouter" },
  { value: "openai", label: "Other (OpenAI-compatible)" },
];

function formatBytes(bytes: number): string {
  if (bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(
    Math.floor(Math.log(bytes) / Math.log(1024)),
    units.length - 1,
  );
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

export function ProviderWizardDialog({
  open,
  models,
  onClose,
  onCreated,
}: ProviderWizardDialogProps) {
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
  const [remoteModel, setRemoteModel] = useState("");
  const [litellmProvider, setLitellmProvider] = useState("");
  const [testStatus, setTestStatus] = useState<
    "idle" | "testing" | "success" | "error"
  >("idle");
  const [testMessage, setTestMessage] = useState("");
  const [discoveredModels, setDiscoveredModels] = useState<string[]>([]);

  // Pricing step (remote only)
  const [pricingInputPrice, setPricingInputPrice] = useState(0);
  const [pricingOutputPrice, setPricingOutputPrice] = useState(0);

  // Node deployment target (local Docker providers)
  const [nodeList, setNodeList] = useState<ManagedNode[]>([]);
  const [nodesLoading, setNodesLoading] = useState(false);
  const [selectedNodeId, setSelectedNodeId] = useState<string>("");
  const [modelSource, setModelSource] = useState<
    "sync_from_server" | "download_from_hf"
  >("sync_from_server");
  const [hfRepoId, setHfRepoId] = useState("");

  // Step 2 — runtime config (local only)
  const [modelId, setModelId] = useState("");
  const [hwInfo, setHwInfo] = useState<HardwareInfo | null>(null);
  const [hwLoading, setHwLoading] = useState(false);
  const [imageChoice, setImageChoice] = useState(AUTO_IMAGE_VALUE);
  const [customImage, setCustomImage] = useState("");
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [engineArgs, setEngineArgs] = useState<
    Record<string, string | number | boolean>
  >({});
  const [openaiCompat, setOpenaiCompat] = useState(true);
  const [legacyGpu, setLegacyGpu] = useState(false);
  const [containerRes, setContainerRes] = useState<ContainerResourceValues>({
    gpuRequest: "",
    ipcMode: "",
    shmSize: "",
    memoryLimit: "",
    cpuLimit: "",
    containerPort: "",
  });
  const updateRes = (field: keyof ContainerResourceValues, value: string) =>
    setContainerRes((prev) => ({ ...prev, [field]: value }));

  // Image availability check & pull
  const [imageStatus, setImageStatus] = useState<
    | "idle"
    | "checking"
    | "available"
    | "missing"
    | "pulling"
    | "pulled"
    | "error"
  >("idle");
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
      setRemoteModel("");
      setLitellmProvider("");
      setTestStatus("idle");
      setTestMessage("");
      setDiscoveredModels([]);
      setPricingInputPrice(0);
      setPricingOutputPrice(0);
      setSelectedNodeId("");
      setModelSource("sync_from_server");
      setHfRepoId("");
      setModelId("");
      setHwInfo(null);
      setImageChoice(AUTO_IMAGE_VALUE);
      setCustomImage("");
      setAdvancedOpen(false);
      setLegacyGpu(false);
      setEngineArgs({});
      setContainerRes({
        gpuRequest: "",
        ipcMode: "",
        shmSize: "",
        memoryLimit: "",
        cpuLimit: "",
        containerPort: "",
      });
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

  // When legacyGpu is toggled, sync imageChoice to the legacy image or back to auto
  useEffect(() => {
    if (legacyGpu) {
      setImageChoice(hwInfo?.legacy_vllm_image ?? AUTO_IMAGE_VALUE);
    } else {
      // Restore to recommended preset or auto
      const rec = hwInfo?.vllm_image_presets.find((p) => p.is_recommended);
      setImageChoice(rec?.image ?? AUTO_IMAGE_VALUE);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [legacyGpu]);

  // Fetch available nodes when wizard opens with local_docker target
  useEffect(() => {
    if (!open || target !== "local_docker") return;
    let cancelled = false;
    setNodesLoading(true);
    nodesApi
      .list()
      .then((nodes) => {
        if (!cancelled) {
          // Only show healthy, scheduler-eligible, non-draining nodes
          setNodeList(
            nodes.filter(
              (n) =>
                n.scheduler_eligible &&
                !n.maintenance_mode &&
                !n.draining &&
                (n.status === "healthy" || n.status === "degraded"),
            ),
          );
        }
      })
      .catch(() => {
        if (!cancelled) setNodeList([]);
      })
      .finally(() => {
        if (!cancelled) setNodesLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, target]);

  // Fetch hardware info for step 2 — local or from a specific node
  useEffect(() => {
    if (!open || step !== 1 || target !== "local_docker") return;
    let cancelled = false;
    setHwLoading(true);
    const hwPromise = selectedNodeId
      ? nodesApi.hardware(selectedNodeId)
      : hardware.info();
    hwPromise
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
  }, [open, step, target, selectedNodeId]);

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
        setPullLayers({
          done: data.layers_done ?? 0,
          total: data.layers_total ?? 0,
        });
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

    // Remote + internet: the node pulls the image — skip local check.
    const remoteWithInternet =
      !!selectedNodeId && modelSource === "download_from_hf";
    if (remoteWithInternet) {
      setImageStatus("idle");
      return;
    }

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
    const imageName =
      lastColon > 0 ? resolvedImage.slice(0, lastColon) : resolvedImage;
    const imageTag =
      lastColon > 0 ? resolvedImage.slice(lastColon + 1) : "latest";

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
    return () => {
      cancelled = true;
    };
  }, [
    open,
    step,
    target,
    imageChoice,
    customImage,
    hwInfo,
    subscribeToPull,
    selectedNodeId,
    modelSource,
  ]);

  // ── Test connection handler ──────────────────────────────────────
  async function handleTestConnection() {
    setTestStatus("testing");
    setTestMessage("");
    try {
      const result = await providers.testEndpoint({
        ...(endpointUrl && { endpoint_url: endpointUrl }),
        ...(apiKey && { api_key: apiKey }),
        ...(litellmProvider && { litellm_provider: litellmProvider }),
        ...(remoteModel.trim() && { litellm_model: remoteModel.trim() }),
      });
      if (result.compatible) {
        setTestStatus("success");
        setTestMessage(
          t("llm_providers.test_connection_success", {
            count: result.models.length,
          }),
        );
        // Populate the model dropdown with discovered models
        if (result.models.length > 0) {
          setDiscoveredModels(result.models);
          if (!remoteModel.trim()) {
            setRemoteModel(result.models[0]);
          }
        }
      } else {
        setTestStatus("error");
        setTestMessage(
          result.error ?? t("llm_providers.test_connection_failed"),
        );
      }
    } catch {
      setTestStatus("error");
      setTestMessage(t("llm_providers.test_connection_failed"));
    }
  }

  const steps =
    target === "local_docker"
      ? [
          t("llm_providers.wizard_step_basics"),
          t("llm_providers.wizard_step_runtime"),
        ]
      : [
          t("llm_providers.wizard_step_basics"),
          t("observability.wizard_step_pricing"),
        ];
  const availableModels = useMemo(
    () => models.filter((m) => m.status === "available"),
    [models],
  );
  const isLast = step >= steps.length - 1;
  const localImageReady =
    imageStatus === "available" || imageStatus === "pulled";
  const localCheckInProgress = hwLoading || imageStatus === "checking";
  const hasModel =
    selectedNodeId && modelSource === "download_from_hf"
      ? !!hfRepoId.trim()
      : !!modelId;
  // Remote + internet: image will be pulled on the node, no local check needed.
  // Remote + air-gapped: image must be available locally for transfer.
  // Local: image must be available locally.
  const isRemoteNode = !!selectedNodeId;
  const isRemoteWithInternet =
    isRemoteNode && modelSource === "download_from_hf";
  const syncNeedsServerModelPrompt =
    isRemoteNode &&
    modelSource === "sync_from_server" &&
    availableModels.length === 0;
  const needsLocalImage = !isRemoteNode || modelSource === "sync_from_server";
  const localCreateBlocked = isRemoteWithInternet
    ? !hasModel
    : !hasModel || !localImageReady || localCheckInProgress;

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
    const imageName =
      lastColon > 0 ? resolvedImage.slice(0, lastColon) : resolvedImage;
    const imageTag =
      lastColon > 0 ? resolvedImage.slice(lastColon + 1) : "latest";

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
        type: target === "local_docker" ? engine : "cloud",
        target,
        ...(target === "remote_endpoint" &&
          endpointUrl && { endpoint_url: endpointUrl }),
        ...(target === "remote_endpoint" && apiKey && { api_key: apiKey }),
        ...(target === "remote_endpoint" &&
          remoteModel.trim() && { remote_model: remoteModel.trim() }),
        ...(target === "remote_endpoint" &&
          litellmProvider && { litellm_provider: litellmProvider }),
        ...(target === "remote_endpoint" &&
          remoteModel.trim() && { litellm_model: remoteModel.trim() }),
      };
      const newProv = await providers.create(provPayload);
      newProvId = newProv.id;

      // If user set pricing during wizard, create price catalog entry
      if (
        target === "remote_endpoint" &&
        (pricingInputPrice > 0 || pricingOutputPrice > 0)
      ) {
        try {
          await observability.createPricing({
            provider: litellmProvider || name,
            model: remoteModel.trim(),
            input_price_per_1k: pricingInputPrice,
            output_price_per_1k: pricingOutputPrice,
            currency: "USD",
          });
        } catch {
          /* best-effort — provider was created, pricing can be added later */
        }
      }

      if (target === "local_docker") {
        // Merge legacy-GPU flag into engine args
        const mergedArgs = { ...engineArgs };
        if (legacyGpu) mergedArgs["enforce-eager"] = true;

        // Build provider_config with engine_args + image
        const provider_config: Record<string, unknown> = {};
        const resolvedImage =
          imageChoice === CUSTOM_IMAGE_VALUE
            ? customImage.trim()
            : imageChoice === AUTO_IMAGE_VALUE
              ? undefined
              : imageChoice;
        if (resolvedImage) provider_config.image = resolvedImage;
        if (Object.keys(mergedArgs).length > 0)
          provider_config.engine_args = mergedArgs;

        // Container resource fields
        if (containerRes.gpuRequest.trim())
          provider_config.gpu_request = containerRes.gpuRequest.trim();
        if (containerRes.ipcMode.trim())
          provider_config.ipc_mode = containerRes.ipcMode.trim();
        if (containerRes.shmSize.trim())
          provider_config.shm_size = containerRes.shmSize.trim();
        if (containerRes.memoryLimit.trim())
          provider_config.memory_limit = containerRes.memoryLimit.trim();
        if (containerRes.cpuLimit.trim())
          provider_config.cpu_limit = containerRes.cpuLimit.trim();
        if (containerRes.containerPort.trim())
          provider_config.container_port = containerRes.containerPort.trim();

        // Backward-compat: also populate generic_config with commonly-used fields
        const generic_config: Record<string, unknown> = {};
        if (mergedArgs["max-model-len"] != null)
          generic_config.max_model_len = mergedArgs["max-model-len"];
        if (mergedArgs["dtype"] != null)
          generic_config.dtype = mergedArgs["dtype"];
        if (mergedArgs["gpu-memory-utilization"] != null)
          generic_config.gpu_memory_utilization =
            mergedArgs["gpu-memory-utilization"];
        if (mergedArgs["tensor-parallel-size"] != null)
          generic_config.tensor_parallel_size =
            mergedArgs["tensor-parallel-size"];
        if (mergedArgs["enforce-eager"]) generic_config.enforce_eager = true;

        // Resolve model_id: use existing model or create one via download for HF-direct
        let resolvedModelId = modelId;
        let runtimeName: string | undefined;
        if (
          selectedNodeId &&
          modelSource === "download_from_hf" &&
          hfRepoId.trim()
        ) {
          const dlResp = await modelsApi.download({
            hf_repo_id: hfRepoId.trim(),
            display_name: hfRepoId.trim().split("/").pop() ?? hfRepoId.trim(),
          });
          resolvedModelId = dlResp.model.id;
          runtimeName = hfRepoId.trim().split("/").pop() ?? hfRepoId.trim();
        }
        // Use model display name as the runtime alias so chat routing
        // resolves by model name, not the provider name.
        if (!runtimeName) {
          runtimeName =
            models.find((m) => m.id === resolvedModelId)?.display_name ?? name;
        }

        const rtPayload: CreateRuntimePayload = {
          name: runtimeName,
          provider_id: newProv.id,
          model_id: resolvedModelId,
          openai_compat: openaiCompat,
          ...(Object.keys(generic_config).length > 0 && { generic_config }),
          ...(Object.keys(provider_config).length > 0 && { provider_config }),
          ...(selectedNodeId && { target_node_id: selectedNodeId }),
          ...(selectedNodeId && { model_source: modelSource }),
          ...(selectedNodeId && {
            image_source:
              modelSource === "download_from_hf"
                ? "pull_from_registry"
                : "transfer_from_server",
          }),
        };
        await runtimes.create(rtPayload);
      }

      onClose();
      onCreated();
    } catch (err: unknown) {
      // Rollback: delete the orphan provider if runtime creation failed
      if (newProvId) {
        try {
          await providers.delete(newProvId);
        } catch {
          /* best-effort */
        }
      }
      alert(err instanceof Error ? err.message : t("common.create_failed"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth data-tour-id="providers.wizard">
      <DialogTitle>{t("llm_providers.new_provider")}</DialogTitle>
      <DialogContent
        sx={{
          display: "flex",
          flexDirection: "column",
          gap: 2,
          pt: "8px !important",
        }}
      >
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
              data-tour-id="providers.wizard.name"
            />
            <FormControl fullWidth data-tour-id="providers.wizard.target">
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

            {target === "local_docker" && (
              <>
                <FormControl fullWidth>
                  <InputLabel>
                    {t("llm_providers.deploy_target", "Deploy To")}
                  </InputLabel>
                  <Select
                    value={selectedNodeId}
                    label={t("llm_providers.deploy_target", "Deploy To")}
                    onChange={(e) => {
                      setSelectedNodeId(e.target.value);
                      // Reset step-2 state when deployment target changes
                      setHwInfo(null);
                      setImageChoice(AUTO_IMAGE_VALUE);
                      setImageStatus("idle");
                    }}
                    disabled={nodesLoading}
                  >
                    <MenuItem value="">
                      <Typography variant="body2">
                        {t(
                          "llm_providers.deploy_this_server",
                          "This Server (local)",
                        )}
                      </Typography>
                    </MenuItem>
                    {nodeList.map((node) => (
                      <MenuItem key={node.id} value={node.id}>
                        <Stack>
                          <Typography variant="body2">{node.host}</Typography>
                          <Typography variant="caption" color="text.secondary">
                            {node.agent_id}
                            {node.latest_inventory
                              ? ` · ${(node.latest_inventory as Record<string, unknown>).gpu_count ?? 0} GPU(s)`
                              : ""}
                          </Typography>
                        </Stack>
                      </MenuItem>
                    ))}
                  </Select>
                </FormControl>

                {/* Model source — only shown when deploying to a remote node */}
                {selectedNodeId && (
                  <FormControl component="fieldset" fullWidth>
                    <Typography variant="subtitle2" sx={{ mb: 0.5 }}>
                      {t("llm_providers.model_source", "Model Source")}
                    </Typography>
                    <RadioGroup
                      value={modelSource}
                      onChange={(e) =>
                        setModelSource(
                          e.target.value as
                            | "sync_from_server"
                            | "download_from_hf",
                        )
                      }
                    >
                      <FormControlLabel
                        value="sync_from_server"
                        control={<Radio size="small" />}
                        label={
                          <Stack>
                            <Typography variant="body2">
                              {t(
                                "llm_providers.model_source_sync",
                                "Sync from this server",
                              )}
                            </Typography>
                            <Typography
                              variant="caption"
                              color="text.secondary"
                            >
                              {t(
                                "llm_providers.model_source_sync_desc",
                                "Transfer model from the server's cache (for air-gapped nodes)",
                              )}
                            </Typography>
                          </Stack>
                        }
                      />
                      <FormControlLabel
                        value="download_from_hf"
                        control={<Radio size="small" />}
                        label={
                          <Stack>
                            <Typography variant="body2">
                              {t(
                                "llm_providers.model_source_hf",
                                "Download from HuggingFace",
                              )}
                            </Typography>
                            <Typography
                              variant="caption"
                              color="text.secondary"
                            >
                              {t(
                                "llm_providers.model_source_hf_desc",
                                "Node downloads the model directly (requires internet)",
                              )}
                            </Typography>
                          </Stack>
                        }
                      />
                    </RadioGroup>
                    {syncNeedsServerModelPrompt && (
                      <Alert severity="warning" variant="outlined" sx={{ mt: 1 }}>
                        {t(
                          "llm_providers.model_source_sync_requires_download",
                          "No model is currently available on this server for sync. Download a model first in the Models page, or switch to 'Download from HuggingFace'.",
                        )}
                      </Alert>
                    )}
                  </FormControl>
                )}
              </>
            )}

            {target === "remote_endpoint" && (
              <>
                <FormControl fullWidth data-tour-id="providers.wizard.litellm">
                  <InputLabel>
                    {t("llm_providers.litellm_provider", "Provider")}
                  </InputLabel>
                  <Select
                    value={litellmProvider}
                    label={t("llm_providers.litellm_provider", "Provider")}
                    onChange={(e) => {
                      setLitellmProvider(e.target.value);
                      setDiscoveredModels([]);
                    }}
                  >
                    {LITELLM_PROVIDERS.map((p) => (
                      <MenuItem key={`${p.value}-${p.label}`} value={p.value}>
                        {p.label}
                      </MenuItem>
                    ))}
                  </Select>
                </FormControl>

                <TextField
                  label={t("llm_providers.endpoint_url")}
                  placeholder="https://api.example.com/v1"
                  value={endpointUrl}
                  onChange={(e) => {
                    setEndpointUrl(e.target.value);
                    setTestStatus("idle");
                    setDiscoveredModels([]);
                  }}
                  fullWidth
                  helperText={
                    litellmProvider
                      ? t(
                          "llm_providers.endpoint_url_help_optional",
                          "Optional — leave empty for hosted providers like Gemini, Anthropic, etc.",
                        )
                      : t("llm_providers.endpoint_url_help")
                  }
                />
                <TextField
                  label={t("llm_providers.api_key")}
                  type="password"
                  value={apiKey}
                  onChange={(e) => {
                    setApiKey(e.target.value);
                    setTestStatus("idle");
                  }}
                  fullWidth
                  helperText={t("llm_providers.api_key_help")}
                  data-tour-id="providers.wizard.apikey"
                />
                <Autocomplete
                  freeSolo
                  options={discoveredModels}
                  value={remoteModel}
                  onInputChange={(_e, value) => setRemoteModel(value)}
                  renderInput={(params) => (
                    <TextField
                      {...params}
                      label={t("llm_common.model")}
                      placeholder={
                        discoveredModels.length > 0
                          ? t(
                              "llm_providers.select_or_type_model",
                              "Select or type a model…",
                            )
                          : t(
                              "llm_providers.test_to_discover",
                              "Test connection to discover models",
                            )
                      }
                      fullWidth
                    />
                  )}
                />

                <Button
                  variant="outlined"
                  size="small"
                  startIcon={
                    testStatus === "testing" ? (
                      <CircularProgress size={16} color="inherit" />
                    ) : (
                      <NetworkCheckIcon />
                    )
                  }
                  disabled={
                    (!endpointUrl && !litellmProvider) ||
                    testStatus === "testing"
                  }
                  onClick={() => void handleTestConnection()}
                  sx={{ alignSelf: "flex-start" }}
                  data-tour-id="providers.wizard.test"
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

        {/* ── Step 2: Pricing (remote endpoint only) ────────── */}
        {step === 1 && target === "remote_endpoint" && (
          <>
            <Typography variant="h6">
              {t("observability.wizard_pricing_heading")}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              {t("observability.wizard_pricing_description")}
            </Typography>
            <TextField
              label={t("observability.wizard_pricing_input")}
              type="number"
              value={pricingInputPrice}
              onChange={(e) => setPricingInputPrice(Number(e.target.value))}
              size="small"
              fullWidth
              inputProps={{ step: 0.000001, min: 0 }}
            />
            <TextField
              label={t("observability.wizard_pricing_output")}
              type="number"
              value={pricingOutputPrice}
              onChange={(e) => setPricingOutputPrice(Number(e.target.value))}
              size="small"
              fullWidth
              inputProps={{ step: 0.000001, min: 0 }}
            />
            <Typography variant="caption" color="text.secondary">
              {t("observability.wizard_pricing_skip_hint")}
            </Typography>
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
                      model:
                        hwInfo.gpu.devices[0]?.model ??
                        hwInfo.gpu.primary_vendor,
                      vram: formatBytes(hwInfo.gpu.total_vram_bytes),
                    })
                  : t("llm_runtimes.gpu_none")}
              </Alert>
            )}

            {/* Model selector — HF search when downloading on node, dropdown otherwise */}
            {selectedNodeId && modelSource === "download_from_hf" ? (
              <HfModelSearch
                value={hfRepoId}
                onChange={setHfRepoId}
                label={t("llm_models.hf_repo_id")}
                required
              />
            ) : (
              <FormControl fullWidth required>
                <InputLabel>{t("llm_common.model")}</InputLabel>
                <Select
                  value={modelId}
                  label={t("llm_common.model")}
                  onChange={(e) => setModelId(e.target.value)}
                >
                  {availableModels.length === 0 && (
                    <MenuItem value="" disabled>
                      {t(
                        "llm_providers.model_source_sync_requires_download_short",
                        "No local model available. Download first.",
                      )}
                    </MenuItem>
                  )}
                  {availableModels.map((m) => (
                    <MenuItem key={m.id} value={m.id}>
                      {m.display_name}
                    </MenuItem>
                  ))}
                </Select>
              </FormControl>
            )}

            <Box sx={{ position: "relative" }}>
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
                      <Typography variant="body2">
                        {t("llm_runtimes.image_auto")}
                      </Typography>
                    </Stack>
                  </MenuItem>
                  {imagePresets.map((preset) => (
                    <MenuItem key={preset.image} value={preset.image}>
                      <Stack
                        direction="row"
                        spacing={1}
                        alignItems="center"
                        sx={{ width: "100%" }}
                      >
                        <Box sx={{ flex: 1 }}>
                          <Typography variant="body2">
                            {preset.label}
                          </Typography>
                          <Typography
                            variant="caption"
                            color="text.secondary"
                            sx={{ fontFamily: "monospace" }}
                          >
                            {preset.image}
                          </Typography>
                        </Box>
                        {preset.is_recommended && (
                          <Chip
                            label={t("llm_runtimes.recommended_tag")}
                            size="small"
                            color="success"
                            variant="outlined"
                          />
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
              {localCheckInProgress && (
                <Box
                  sx={{
                    position: "absolute",
                    inset: 0,
                    zIndex: 2,
                    borderRadius: 1,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    bgcolor: "action.hover",
                    pointerEvents: "all",
                  }}
                >
                  <CircularProgress size={24} />
                </Box>
              )}
            </Box>

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
            {isRemoteWithInternet && (
              <Alert severity="info" variant="outlined" sx={{ py: 0.5 }}>
                {t(
                  "llm_runtimes.image_pull_on_node",
                  "The container image will be pulled directly on the remote node.",
                )}
              </Alert>
            )}
            {isRemoteNode &&
              modelSource === "sync_from_server" &&
              imageStatus === "available" && (
                <Alert severity="success" variant="outlined" sx={{ py: 0.5 }}>
                  {t(
                    "llm_runtimes.image_transfer_ready",
                    "Image available locally — it will be transferred to the remote node.",
                  )}
                </Alert>
              )}
            {!isRemoteWithInternet && imageStatus === "checking" && (
              <Alert
                severity="info"
                variant="outlined"
                icon={<CircularProgress size={18} />}
                sx={{ py: 0.5 }}
              >
                {t("llm_runtimes.image_checking")}
              </Alert>
            )}
            {!isRemoteWithInternet && imageStatus === "available" && (
              <Alert severity="success" variant="outlined" sx={{ py: 0.5 }}>
                {t("llm_runtimes.image_available")}
              </Alert>
            )}
            {!isRemoteWithInternet && imageStatus === "pulled" && (
              <Alert severity="success" variant="outlined" sx={{ py: 0.5 }}>
                {t("llm_runtimes.image_pull_success")}
              </Alert>
            )}
            {!isRemoteWithInternet && imageStatus === "missing" && (
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
            {!isRemoteWithInternet && imageStatus === "pulling" && (
              <Alert
                severity="info"
                variant="outlined"
                sx={{ py: 1, "& .MuiAlert-message": { width: "100%" } }}
              >
                <Stack spacing={1}>
                  <Stack
                    direction="row"
                    justifyContent="space-between"
                    alignItems="center"
                  >
                    <Typography variant="body2">
                      {t("llm_runtimes.image_pulling")}
                    </Typography>
                    <Typography
                      variant="caption"
                      color="text.secondary"
                      sx={{ whiteSpace: "nowrap", ml: 1 }}
                    >
                      {pullPercent > 0 || pullLayers.total > 0
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
            {!isRemoteWithInternet && imageStatus === "error" && (
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
              sx={{
                border: 1,
                borderColor: "divider",
                borderRadius: 1,
                "&::before": { display: "none" },
              }}
            >
              <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                <Typography variant="subtitle2">
                  {t(
                    "llm_runtimes.engine_configuration",
                    "Engine Configuration",
                  )}
                </Typography>
              </AccordionSummary>
              <AccordionDetails
                sx={{ display: "flex", flexDirection: "column", gap: 2 }}
              >
                <VllmEngineArgsPanel
                  values={engineArgs}
                  onChange={setEngineArgs}
                  version={legacyGpu ? "0.6.6" : "0.7.3"}
                  modelName={models.find((m) => m.id === modelId)?.display_name}
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
                <FormControlLabel
                  control={
                    <Checkbox
                      checked={legacyGpu}
                      onChange={(e) => setLegacyGpu(e.target.checked)}
                    />
                  }
                  label={t("llm_runtime_detail.enforce_eager")}
                />

                <Typography variant="subtitle2" sx={{ mt: 1 }}>
                  Container Resources
                </Typography>
                <ContainerResourcesPanel
                  values={containerRes}
                  onChange={updateRes}
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
            disabled={
              busy || !name || (target === "local_docker" && localCreateBlocked)
            }
            onClick={() => void handleFinish()}
            data-tour-id="providers.wizard.create"
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
