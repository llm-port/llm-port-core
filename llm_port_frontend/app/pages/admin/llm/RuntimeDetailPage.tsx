/**
 * Admin → LLM → Runtime detail page.
 * Shows runtime metadata, health status, live logs, and config editing.
 */
import { useState, useEffect, useRef } from "react";
import { useParams, useNavigate } from "react-router";
import { useTranslation } from "react-i18next";
import {
  runtimes,
  providers as provApi,
  models as modelApi,
  type Runtime,
  type RuntimeHealth,
  type Provider,
  type Model,
} from "~/api/llm";
import { nodesApi, type NodeCommandTimeline } from "~/api/nodes";
import { RuntimeStatusChip, EngineChip } from "~/components/Chips";
import { VllmEngineArgsPanel } from "~/components/VllmEngineArgsPanel";
import {
  ContainerResourcesPanel,
  type ContainerResourceValues,
} from "~/components/ContainerResourcesPanel";

import Accordion from "@mui/material/Accordion";
import AccordionSummary from "@mui/material/AccordionSummary";
import AccordionDetails from "@mui/material/AccordionDetails";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import Collapse from "@mui/material/Collapse";
import FormControl from "@mui/material/FormControl";
import InputLabel from "@mui/material/InputLabel";
import LinearProgress from "@mui/material/LinearProgress";
import MenuItem from "@mui/material/MenuItem";
import Alert from "@mui/material/Alert";
import Select from "@mui/material/Select";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import Stepper from "@mui/material/Stepper";
import Step from "@mui/material/Step";
import StepLabel from "@mui/material/StepLabel";

import ArrowBackIcon from "@mui/icons-material/ArrowBack";
import DeleteIcon from "@mui/icons-material/Delete";
import EditIcon from "@mui/icons-material/Edit";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import SaveIcon from "@mui/icons-material/Save";
import StopIcon from "@mui/icons-material/Stop";
import RefreshIcon from "@mui/icons-material/Refresh";
import RestartAltIcon from "@mui/icons-material/RestartAlt";
import FavoriteIcon from "@mui/icons-material/Favorite";
import HeartBrokenIcon from "@mui/icons-material/HeartBroken";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import ExpandLessIcon from "@mui/icons-material/ExpandLess";
import MemoryIcon from "@mui/icons-material/Memory";

/* ── Node Deployment Progress ─────────────────────────────────────── */

/** vLLM deployment stages emitted by the node agent. */
const DEPLOY_STAGES = [
  "dispatched",
  "validate",
  "remove_stale",
  "sync_model",
  "pull_image",
  "start_container",
  "resolve_endpoint",
  "ready",
] as const;

const STAGE_LABELS: Record<string, string> = {
  dispatched: "Dispatched",
  validate: "Validate",
  remove_stale: "Cleanup",
  sync_model: "Sync Model",
  pull_image: "Pull Image",
  start_container: "Start Container",
  resolve_endpoint: "Resolve Endpoint",
  ready: "Ready",
};

export function NodeDeploymentProgress({
  timeline,
  runtimeStatus,
  statusMessage,
}: {
  timeline: NodeCommandTimeline;
  runtimeStatus?: string;
  statusMessage?: string | null;
}) {
  const { command, events } = timeline;
  const isFailed =
    command.status === "failed" ||
    command.status === "canceled" ||
    command.status === "timed_out";
  const isFinished = command.status === "succeeded";

  // Command succeeded but runtime crashed shortly after (post-deploy failure)
  const isPostDeployFailure = isFinished && runtimeStatus === "error";

  // Determine the latest deployment phase by scanning progress events
  let lastPhase: string | null = null;
  for (const ev of events) {
    const phase = (ev.payload as Record<string, unknown> | undefined)?.phase;
    if (
      typeof phase === "string" &&
      DEPLOY_STAGES.includes(phase as (typeof DEPLOY_STAGES)[number])
    ) {
      lastPhase = phase;
    }
  }

  // Compute active step index from last known phase
  let activeStep: number;
  if (isFinished) {
    activeStep = DEPLOY_STAGES.length; // all complete
  } else if (lastPhase) {
    activeStep = DEPLOY_STAGES.indexOf(
      lastPhase as (typeof DEPLOY_STAGES)[number],
    );
  } else {
    // Fallback: map command status for backward compat (no granular events)
    if (command.status === "queued") activeStep = 0;
    else if (command.status === "dispatched") activeStep = 0;
    else if (command.status === "running") activeStep = 1;
    else activeStep = 0;
  }

  // Find the step index where the failure occurred
  const failedStep = isFailed ? Math.max(activeStep, 0) : -1;

  // For post-deploy crash, mark the last stage ("ready") as failed
  const lastStageIdx = DEPLOY_STAGES.length - 1;
  const showError = isFailed || isPostDeployFailure;
  const errorStep = isFailed
    ? failedStep
    : isPostDeployFailure
      ? lastStageIdx
      : -1;

  return (
    <Box>
      <Stepper
        activeStep={
          isPostDeployFailure
            ? lastStageIdx
            : activeStep === -1
              ? 0
              : activeStep
        }
        alternativeLabel
        sx={{ mb: 2 }}
      >
        {DEPLOY_STAGES.map((phase, idx) => (
          <Step
            key={phase}
            completed={showError ? idx < errorStep : activeStep > idx}
          >
            <StepLabel error={showError && idx === errorStep}>
              {STAGE_LABELS[phase] ?? phase}
            </StepLabel>
          </Step>
        ))}
      </Stepper>

      {!isFinished &&
        !isFailed &&
        !isPostDeployFailure &&
        command.status === "running" && <LinearProgress sx={{ mb: 2 }} />}

      {isFailed && (
        <Typography variant="body2" color="error" sx={{ mb: 1 }}>
          {command.error_message || command.status}
        </Typography>
      )}

      {isPostDeployFailure && (
        <Typography variant="body2" color="error" sx={{ mb: 1 }}>
          {statusMessage || "Container crashed after startup"}
        </Typography>
      )}

      {events.length > 0 && (
        <Box
          component="pre"
          sx={{
            fontSize: "0.75rem",
            fontFamily: "monospace",
            bgcolor: "grey.900",
            color: "grey.100",
            p: 1.5,
            borderRadius: 1,
            maxHeight: 200,
            overflow: "auto",
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
            m: 0,
          }}
        >
          {events.map((e) => {
            const ts = new Date(e.ts).toLocaleTimeString();
            return `[${ts}] ${e.phase}: ${e.message}\n`;
          })}
        </Box>
      )}
    </Box>
  );
}

export default function RuntimeDetailPage() {
  const { t } = useTranslation();
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [rt, setRt] = useState<Runtime | null>(null);
  const [provider, setProvider] = useState<Provider | null>(null);
  const [model, setModel] = useState<Model | null>(null);
  const [health, setHealth] = useState<RuntimeHealth | null>(null);
  const [logs, setLogs] = useState("");
  const [logsRefreshing, setLogsRefreshing] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const logRef = useRef<HTMLPreElement>(null);

  // ── Edit mode state ──────────────────────────────────────────────
  const [editing, setEditing] = useState(false);
  const [editName, setEditName] = useState("");
  const [engineArgs, setEngineArgs] = useState<
    Record<string, string | number | boolean>
  >({});
  const [saving, setSaving] = useState(false);
  const [resourcesOpen, setResourcesOpen] = useState(false);

  // Container resource fields (stored in provider_config)
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

  // Remote provider fields (for edit config)
  const [editRemoteModel, setEditRemoteModel] = useState("");
  const [editEndpointUrl, setEditEndpointUrl] = useState("");
  const [editApiKey, setEditApiKey] = useState("");
  const [editImage, setEditImage] = useState("");
  const [editModelSource, setEditModelSource] = useState<
    "sync_from_server" | "download_from_hf"
  >("sync_from_server");

  const isRemoteProvider = provider?.target === "remote_endpoint";

  // ── Node deployment timeline ─────────────────────────────────────
  const [cmdTimeline, setCmdTimeline] = useState<NodeCommandTimeline | null>(
    null,
  );
  const isNodeDeployment = !!rt?.assigned_node_id;

  // Map from generic_config snake_case keys to CLI kebab-case flags
  const GC_TO_FLAG: Record<string, string> = {
    max_model_len: "max-model-len",
    dtype: "dtype",
    gpu_memory_utilization: "gpu-memory-utilization",
    tensor_parallel_size: "tensor-parallel-size",
    swap_space: "swap-space",
    enforce_eager: "enforce-eager",
  };

  function openEditor() {
    if (!rt) return;
    const gc = rt.generic_config ?? {};
    const pc = rt.provider_config ?? {};
    setEditName(rt.name);

    // Seed engine args from provider_config.engine_args first
    const args: Record<string, string | number | boolean> = {
      ...((pc.engine_args as Record<string, string | number | boolean>) ?? {}),
    };
    // Overlay values from legacy generic_config (lower priority than engine_args)
    for (const [gcKey, flag] of Object.entries(GC_TO_FLAG)) {
      if (gc[gcKey] != null && args[flag] === undefined) {
        args[flag] = gc[gcKey] as string | number | boolean;
      }
    }
    setEngineArgs(args);
    // Seed container resource fields
    setContainerRes({
      gpuRequest: String(pc.gpu_request ?? ""),
      ipcMode: String(pc.ipc_mode ?? ""),
      shmSize: String(pc.shm_size ?? ""),
      memoryLimit: String(pc.memory_limit ?? ""),
      cpuLimit: String(pc.cpu_limit ?? ""),
      containerPort: String(pc.container_port ?? ""),
    });
    setResourcesOpen(false);
    // Seed remote provider fields
    setEditRemoteModel(provider?.remote_model ?? "");
    setEditEndpointUrl(provider?.endpoint_url ?? "");
    setEditApiKey("");
    setEditImage(
      String((rt.provider_config as Record<string, unknown>)?.image ?? ""),
    );
    setEditModelSource(
      ((rt.provider_config as Record<string, unknown>)?.model_source as
        | "sync_from_server"
        | "download_from_hf") ?? "sync_from_server",
    );
    setEditing(true);
  }

  async function handleSaveAndRestart() {
    if (!id || !rt) return;
    setSaving(true);
    try {
      if (isRemoteProvider && provider) {
        // ── Remote provider: update provider fields + runtime name ──
        const provUpdate: Record<string, unknown> = {};
        if (editRemoteModel.trim() !== (provider.remote_model ?? ""))
          provUpdate.remote_model = editRemoteModel.trim() || null;
        if (editEndpointUrl.trim() !== (provider.endpoint_url ?? ""))
          provUpdate.endpoint_url = editEndpointUrl.trim() || null;
        if (editApiKey.trim()) provUpdate.api_key = editApiKey.trim();

        if (Object.keys(provUpdate).length > 0) {
          const updatedProv = await provApi.update(provider.id, provUpdate);
          setProvider(updatedProv);
        }
        // Also update the runtime name if it changed
        const updated = await runtimes.update(id, {
          name: editName !== rt.name ? editName : undefined,
        });
        setRt(updated);
      } else {
        // ── Local provider: update engine args + container resources ──
        // Backward-compat: populate generic_config with commonly-used fields
        const generic_config: Record<string, unknown> = {
          ...(rt.generic_config ?? {}),
        };
        const FLAG_TO_GC: Record<string, string> = {
          "max-model-len": "max_model_len",
          dtype: "dtype",
          "gpu-memory-utilization": "gpu_memory_utilization",
          "tensor-parallel-size": "tensor_parallel_size",
          "swap-space": "swap_space",
          "enforce-eager": "enforce_eager",
        };
        // Clear legacy gc keys, then repopulate from engine args
        for (const gcKey of Object.values(FLAG_TO_GC))
          delete generic_config[gcKey];
        for (const [flag, gcKey] of Object.entries(FLAG_TO_GC)) {
          if (engineArgs[flag] != null)
            generic_config[gcKey] = engineArgs[flag];
        }

        const provider_config: Record<string, unknown> = {
          ...(rt.provider_config ?? {}),
        };
        // Store full engine_args dict
        if (Object.keys(engineArgs).length > 0) {
          provider_config.engine_args = engineArgs;
        } else {
          delete provider_config.engine_args;
        }
        // Remove legacy extra_args — everything is in engine_args now
        delete provider_config.extra_args;

        // Image override
        if (editImage.trim()) {
          provider_config.image = editImage.trim();
        } else {
          delete provider_config.image;
        }

        // Model source (node deployments only)
        if (isNodeDeployment) {
          provider_config.model_source = editModelSource;
          provider_config.image_source =
            editModelSource === "download_from_hf"
              ? "pull_from_registry"
              : "transfer_from_server";
        }

        // Container resource fields
        if (containerRes.gpuRequest.trim())
          provider_config.gpu_request = containerRes.gpuRequest.trim();
        else delete provider_config.gpu_request;
        if (containerRes.ipcMode.trim())
          provider_config.ipc_mode = containerRes.ipcMode.trim();
        else delete provider_config.ipc_mode;
        if (containerRes.shmSize.trim())
          provider_config.shm_size = containerRes.shmSize.trim();
        else delete provider_config.shm_size;
        if (containerRes.memoryLimit.trim())
          provider_config.memory_limit = containerRes.memoryLimit.trim();
        else delete provider_config.memory_limit;
        if (containerRes.cpuLimit.trim())
          provider_config.cpu_limit = containerRes.cpuLimit.trim();
        else delete provider_config.cpu_limit;
        if (containerRes.containerPort.trim())
          provider_config.container_port = containerRes.containerPort.trim();
        else delete provider_config.container_port;

        const updated = await runtimes.update(id, {
          name: editName !== rt.name ? editName : undefined,
          generic_config,
          provider_config,
        });
        setRt(updated);
      }
      setEditing(false);
      setLogs("");
      setHealth(null);
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : t("common.action_failed"));
    } finally {
      setSaving(false);
    }
  }

  async function load() {
    if (!id) return;
    setLoading(true);
    setError(null);
    try {
      const r = await runtimes.get(id);
      setRt(r);
      const [p, m] = await Promise.all([
        provApi.get(r.provider_id),
        modelApi.get(r.model_id),
      ]);
      setProvider(p);
      setModel(m);

      // Health & logs for running/starting/error runtimes
      if (
        r.status === "running" ||
        r.status === "starting" ||
        r.status === "error"
      ) {
        if (r.status !== "error") {
          try {
            setHealth(await runtimes.health(id));
          } catch {
            setHealth(null);
          }
        }
        try {
          const logRes = await runtimes.fetchLogs(id, 300);
          setLogs(await logRes.text());
        } catch {
          setLogs("");
        }
      }

      // Fetch node command timeline for node-deployed runtimes
      if (r.assigned_node_id && r.last_command_id) {
        try {
          const tl = await nodesApi.commandTimeline(
            r.assigned_node_id,
            r.last_command_id,
          );
          setCmdTimeline(tl);
        } catch {
          /* command may not exist yet */
        }
      }
    } catch (e: unknown) {
      setError(
        e instanceof Error ? e.message : t("llm_runtime_detail.failed_load"),
      );
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, [id]);

  // Poll while in a transient state OR while running until health confirmed.
  // This catches containers that crash shortly after start (e.g. bad args).
  useEffect(() => {
    if (!rt || !id) return;
    const isPending = rt.status === "starting" || rt.status === "creating";
    const isRunning = rt.status === "running";
    if (!isPending && !isRunning) return;
    // Stop polling once health is confirmed healthy
    if (isRunning && health?.healthy) return;

    const interval = setInterval(async () => {
      try {
        const updated = await runtimes.get(id); // triggers reconcile on backend
        setRt(updated);

        // Refresh node deployment command timeline
        if (updated.assigned_node_id && updated.last_command_id) {
          try {
            const tl = await nodesApi.commandTimeline(
              updated.assigned_node_id,
              updated.last_command_id,
            );
            setCmdTimeline(tl);
          } catch {
            /* ignore – command may not exist yet */
          }
        }

        if (updated.status === "error" || updated.status === "stopped") {
          load(); // full reload for logs; deps change stops this poller
          return;
        }

        if (updated.status === "running") {
          // Check health — stop polling once healthy
          try {
            const h = await runtimes.health(id);
            setHealth(h);
            if (h.healthy) {
              load(); // final full reload; health?.healthy change stops poller
              return;
            }
          } catch {
            setHealth(null);
          }
          // Keep fetching logs so user sees model-loading progress
          try {
            const logRes = await runtimes.fetchLogs(id, 300);
            setLogs(await logRes.text());
          } catch {
            /* ignore */
          }
        }
      } catch {
        /* ignore polling errors */
      }
    }, 5000);
    return () => clearInterval(interval);
  }, [rt?.status, id, health?.healthy]);

  // Auto-scroll logs
  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [logs]);

  async function handleAction(action: "start" | "stop" | "restart") {
    if (!id) return;
    try {
      const updated = await runtimes[action](id);
      setRt(updated); // use response directly — avoids race with DB commit
      setLogs("");
      setHealth(null);
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : t("common.action_failed"));
    }
  }

  async function handleDelete() {
    if (!id || !confirm(t("llm_runtimes.confirm_delete"))) return;
    try {
      await runtimes.delete(id);
      navigate("/admin/llm/providers");
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : t("common.delete_failed"));
    }
  }

  if (loading) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", py: 6 }}>
        <CircularProgress size={32} />
      </Box>
    );
  }

  if (error || !rt) {
    return (
      <Alert severity="error">
        {error ?? t("llm_runtime_detail.not_found")}
      </Alert>
    );
  }

  const isRunning = rt.status === "running" || rt.status === "starting";
  const isStopped = rt.status === "stopped" || rt.status === "error";
  const showLogs = !editing && (isRunning || isStopped);

  async function refreshLogs() {
    if (!id) return;
    setLogsRefreshing(true);
    try {
      const res = await runtimes.fetchLogs(id, 300);
      setLogs(await res.text());
    } catch {
      setLogs("");
    } finally {
      setLogsRefreshing(false);
    }
  }

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        gap: 3,
      }}
    >
      {/* Header */}
      <Stack direction="row" alignItems="center" spacing={2} flexWrap="wrap">
        <Button
          size="small"
          startIcon={<ArrowBackIcon />}
          onClick={() => navigate("/admin/llm/providers")}
        >
          {t("llm_providers.title")}
        </Button>
        <Typography variant="h5" sx={{ flexGrow: 1 }}>
          {rt.name}
        </Typography>
        <RuntimeStatusChip value={rt.status} />
        {!editing && (isRunning || rt.status === "error") && health && (
          <Tooltip title={health.detail || ""} arrow>
            {health.healthy ? (
              <Chip
                icon={<FavoriteIcon />}
                label={t("llm_runtime_detail.healthy")}
                color="success"
                size="small"
              />
            ) : (
              <Chip
                icon={<HeartBrokenIcon />}
                label={t("llm_runtime_detail.unhealthy")}
                color="error"
                size="small"
              />
            )}
          </Tooltip>
        )}
        <Stack direction="row" spacing={1}>
          {isStopped && (
            <Button
              size="small"
              variant="outlined"
              color="success"
              startIcon={<PlayArrowIcon />}
              onClick={() => handleAction("start")}
              disabled={editing}
            >
              {t("common.start")}
            </Button>
          )}
          {isRunning && (
            <>
              <Button
                size="small"
                variant="outlined"
                color="warning"
                startIcon={<StopIcon />}
                onClick={() => handleAction("stop")}
                disabled={editing}
              >
                {t("common.stop")}
              </Button>
              <Button
                size="small"
                variant="outlined"
                color="info"
                startIcon={<RestartAltIcon />}
                onClick={() => handleAction("restart")}
                disabled={editing}
              >
                {t("common.restart")}
              </Button>
            </>
          )}
          <Button
            size="small"
            variant="outlined"
            color="error"
            startIcon={<DeleteIcon />}
            disabled={isRunning || editing}
            onClick={handleDelete}
          >
            {t("common.delete")}
          </Button>
          <Button
            size="small"
            variant="outlined"
            startIcon={<EditIcon />}
            onClick={openEditor}
            disabled={editing}
          >
            {t("llm_runtime_detail.edit_config")}
          </Button>
        </Stack>
      </Stack>

      {/* Config editor card */}
      {editing && (
        <Card
          variant="outlined"
          sx={{
            borderColor: "primary.main",
            display: "flex",
            flexDirection: "column",
            maxHeight: "70vh",
          }}
        >
          <CardContent sx={{ overflow: "auto", flex: 1 }}>
            <Typography variant="subtitle2" sx={{ mb: 2 }}>
              {t("llm_runtime_detail.edit_config")}
            </Typography>
            <Stack spacing={2}>
              <TextField
                label={t("common.name")}
                size="small"
                value={editName}
                onChange={(e) => setEditName(e.target.value)}
                fullWidth
              />

              {isRemoteProvider ? (
                <>
                  {/* Remote provider fields */}
                  <TextField
                    label={t("llm_common.model")}
                    size="small"
                    value={editRemoteModel}
                    onChange={(e) => setEditRemoteModel(e.target.value)}
                    fullWidth
                    helperText="Model identifier used by the remote provider"
                  />
                  <TextField
                    label={t("llm_providers.endpoint_url")}
                    size="small"
                    value={editEndpointUrl}
                    onChange={(e) => setEditEndpointUrl(e.target.value)}
                    fullWidth
                    placeholder="https://api.example.com/v1"
                  />
                  <TextField
                    label={t("llm_providers.api_key")}
                    size="small"
                    type="password"
                    value={editApiKey}
                    onChange={(e) => setEditApiKey(e.target.value)}
                    fullWidth
                    placeholder="Leave blank to keep current key"
                  />
                </>
              ) : (
                <>
                  {/* Local provider fields */}
                  {isNodeDeployment && (
                    <FormControl size="small" fullWidth>
                      <InputLabel>
                        {t("llm_providers.model_source", "Model Source")}
                      </InputLabel>
                      <Select
                        value={editModelSource}
                        label={t("llm_providers.model_source", "Model Source")}
                        onChange={(e) =>
                          setEditModelSource(
                            e.target.value as
                              | "sync_from_server"
                              | "download_from_hf",
                          )
                        }
                      >
                        <MenuItem value="sync_from_server">
                          {t(
                            "llm_providers.model_source_sync",
                            "Sync from this server",
                          )}
                        </MenuItem>
                        <MenuItem value="download_from_hf">
                          {t(
                            "llm_providers.model_source_hf",
                            "Download from HuggingFace",
                          )}
                        </MenuItem>
                      </Select>
                    </FormControl>
                  )}
                  <TextField
                    label={t("llm_runtimes.container_image")}
                    size="small"
                    value={editImage}
                    onChange={(e) => setEditImage(e.target.value)}
                    fullWidth
                    placeholder={t(
                      "llm_runtimes.image_auto_placeholder",
                      "Leave blank for auto-detect",
                    )}
                    slotProps={{ input: { sx: { fontFamily: "monospace" } } }}
                  />
                  <VllmEngineArgsPanel
                    values={engineArgs}
                    onChange={setEngineArgs}
                    version={engineArgs["enforce-eager"] ? "0.6.6" : "0.7.3"}
                    modelName={model?.display_name}
                  />

                  {/* Container Resources */}
                  <Button
                    size="small"
                    startIcon={<MemoryIcon />}
                    endIcon={
                      resourcesOpen ? <ExpandLessIcon /> : <ExpandMoreIcon />
                    }
                    onClick={() => setResourcesOpen(!resourcesOpen)}
                    sx={{ alignSelf: "flex-start" }}
                  >
                    Container Resources
                  </Button>
                  <Collapse in={resourcesOpen}>
                    <Box sx={{ pl: 1 }}>
                      <ContainerResourcesPanel
                        values={containerRes}
                        onChange={updateRes}
                      />
                    </Box>
                  </Collapse>
                </>
              )}
            </Stack>
          </CardContent>
          <Stack
            direction="row"
            spacing={1}
            justifyContent="flex-end"
            sx={{ p: 2, borderTop: 1, borderColor: "divider" }}
          >
            <Button size="small" onClick={() => setEditing(false)}>
              {t("common.cancel")}
            </Button>
            <Button
              size="small"
              variant="contained"
              startIcon={saving ? <CircularProgress size={16} /> : <SaveIcon />}
              disabled={saving}
              onClick={handleSaveAndRestart}
            >
              {isRemoteProvider
                ? t("common.save", "Save")
                : t("llm_runtime_detail.save_and_restart")}
            </Button>
          </Stack>
        </Card>
      )}

      {/* Metadata card */}
      <Card variant="outlined">
        <CardContent>
          <Stack direction="row" flexWrap="wrap" gap={4}>
            <MetaField
              label={t("llm_common.provider")}
              value={provider?.name ?? rt.provider_id.slice(0, 8)}
            />
            {provider && (
              <Box>
                <Typography variant="caption" color="text.secondary">
                  {t("llm_common.engine")}
                </Typography>
                <Box mt={0.5}>
                  <EngineChip value={provider.type} />
                </Box>
              </Box>
            )}
            <MetaField
              label={t("llm_common.model")}
              value={model?.display_name ?? rt.model_id.slice(0, 8)}
            />
            {model?.hf_repo_id && (
              <MetaField
                label={t("llm_runtime_detail.model_id")}
                value={model.hf_repo_id}
                mono
              />
            )}
            <MetaField
              label={t("llm_runtime_detail.openai_compat")}
              value={rt.openai_compat ? t("common.yes") : t("common.no")}
            />
            <MetaField
              label="Execution Target"
              value={rt.execution_target || "local"}
            />
            {rt.assigned_node_id && (
              <MetaField
                label="Assigned Node"
                value={rt.assigned_node_id}
                mono
              />
            )}
            <MetaField
              label="Desired State"
              value={rt.desired_state || "running"}
            />
            {!!(
              provider?.capabilities &&
              Object.keys(provider.capabilities as Record<string, unknown>)
                .length > 0
            ) && (
              <Box>
                <Typography variant="caption" color="text.secondary">
                  {t("llm_runtime_detail.capabilities")}
                </Typography>
                <Box
                  mt={0.5}
                  sx={{ display: "flex", gap: 0.5, flexWrap: "wrap" }}
                >
                  {Object.entries(
                    provider.capabilities as Record<string, unknown>,
                  )
                    .filter(([, v]) => v === true)
                    .map(([key]) => (
                      <Chip
                        key={key}
                        label={key
                          .replace(/^(supports|requires)_/i, "")
                          .replace(/_/g, " ")}
                        color="info"
                        size="small"
                        variant="outlined"
                      />
                    ))}
                </Box>
              </Box>
            )}
            {rt.endpoint_url && (
              <MetaField
                label={t("llm_runtimes.endpoint")}
                value={rt.endpoint_url}
                mono
              />
            )}
            {rt.container_ref && (
              <MetaField
                label={t("containers.title")}
                value={rt.container_ref.slice(0, 12)}
                mono
              />
            )}
            <MetaField
              label={t("common.created")}
              value={new Date(rt.created_at).toLocaleString()}
            />
          </Stack>
          {rt.placement_explain_json && (
            <Accordion
              disableGutters
              elevation={0}
              sx={{
                mt: 2,
                bgcolor: "transparent",
                border: 1,
                borderColor: "divider",
                borderRadius: "8px !important",
                "&::before": { display: "none" },
              }}
            >
              <AccordionSummary
                expandIcon={<ExpandMoreIcon />}
                sx={{
                  minHeight: 36,
                  px: 1.5,
                  "& .MuiAccordionSummary-content": { my: 0.5 },
                }}
              >
                <Typography variant="caption" color="text.secondary">
                  Placement Explainability
                </Typography>
              </AccordionSummary>
              <AccordionDetails sx={{ px: 1.5, pt: 0, pb: 1.5 }}>
                <Box
                  component="pre"
                  sx={{
                    m: 0,
                    p: 1.25,
                    borderRadius: 1,
                    bgcolor: "background.default",
                    fontSize: "0.76rem",
                    fontFamily: "monospace",
                    overflow: "auto",
                    maxHeight: 300,
                    whiteSpace: "pre-wrap",
                    wordBreak: "break-word",
                  }}
                >
                  {JSON.stringify(rt.placement_explain_json, null, 2)}
                </Box>
              </AccordionDetails>
            </Accordion>
          )}
        </CardContent>
      </Card>

      {/* Deployment / runtime error — always visible until resolved */}
      {rt.status === "error" &&
        (cmdTimeline?.command?.error_message || rt.status_message) && (
          <Alert severity="error" variant="outlined">
            <Typography variant="body2" fontWeight="bold" gutterBottom>
              {isNodeDeployment ? "Deployment failed" : "Runtime error"}
            </Typography>
            <Typography variant="body2">
              {cmdTimeline?.command?.error_message || rt.status_message}
            </Typography>
          </Alert>
        )}

      {/* Node deployment progress */}
      {isNodeDeployment && cmdTimeline && (
        <Card variant="outlined">
          <CardContent>
            <Typography variant="subtitle2" sx={{ mb: 2 }}>
              Node Deployment
            </Typography>
            <NodeDeploymentProgress
              timeline={cmdTimeline}
              runtimeStatus={rt.status}
              statusMessage={rt.status_message}
            />
          </CardContent>
        </Card>
      )}

      {/* Logs */}
      {showLogs && (
        <Box sx={{ flexGrow: 1, minHeight: 200 }}>
          <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1 }}>
            <Typography variant="subtitle2">
              {t("llm_runtime_detail.container_logs")}
            </Typography>
            <Button
              size="small"
              startIcon={
                logsRefreshing ? (
                  <CircularProgress size={14} />
                ) : (
                  <RefreshIcon />
                )
              }
              onClick={refreshLogs}
              disabled={logsRefreshing}
            >
              {t("common.refresh", "Refresh")}
            </Button>
          </Stack>
          <Box
            ref={logRef}
            component="pre"
            sx={{
              bgcolor: "grey.900",
              color: "grey.100",
              p: 2,
              borderRadius: 1,
              fontFamily: "monospace",
              fontSize: "0.75rem",
              lineHeight: 1.6,
              overflow: "auto",
              maxHeight: 400,
              whiteSpace: "pre-wrap",
              wordBreak: "break-all",
            }}
          >
            {logs || t("llm_runtime_detail.no_logs")}
          </Box>
        </Box>
      )}
    </Box>
  );
}

function MetaField({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <Box>
      <Typography variant="caption" color="text.secondary">
        {label}
      </Typography>
      <Typography
        variant="body2"
        fontWeight={500}
        fontFamily={mono ? "monospace" : undefined}
        fontSize={mono ? "0.8rem" : undefined}
      >
        {value}
      </Typography>
    </Box>
  );
}
