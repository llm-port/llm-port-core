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
import { RuntimeStatusChip, EngineChip } from "~/components/Chips";
import { VllmEngineArgsPanel } from "~/components/VllmEngineArgsPanel";

import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import Alert from "@mui/material/Alert";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import ArrowBackIcon from "@mui/icons-material/ArrowBack";
import DeleteIcon from "@mui/icons-material/Delete";
import EditIcon from "@mui/icons-material/Edit";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import SaveIcon from "@mui/icons-material/Save";
import StopIcon from "@mui/icons-material/Stop";
import RestartAltIcon from "@mui/icons-material/RestartAlt";
import FavoriteIcon from "@mui/icons-material/Favorite";
import HeartBrokenIcon from "@mui/icons-material/HeartBroken";

export default function RuntimeDetailPage() {
  const { t } = useTranslation();
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [rt, setRt] = useState<Runtime | null>(null);
  const [provider, setProvider] = useState<Provider | null>(null);
  const [model, setModel] = useState<Model | null>(null);
  const [health, setHealth] = useState<RuntimeHealth | null>(null);
  const [logs, setLogs] = useState("");
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
    setEditing(true);
  }

  async function handleSaveAndRestart() {
    if (!id || !rt) return;
    setSaving(true);
    try {
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
        if (engineArgs[flag] != null) generic_config[gcKey] = engineArgs[flag];
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
      // When enforce-eager is enabled the adapter auto-selects the correct
      // image — remove any previously stored image override.
      if (engineArgs["enforce-eager"]) delete provider_config.image;

      const updated = await runtimes.update(id, {
        name: editName !== rt.name ? editName : undefined,
        generic_config,
        provider_config,
      });
      setRt(updated); // use response directly — avoids race with DB commit
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
  const showLogs = !editing && (isRunning || (rt.status === "error" && !!logs));

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        gap: 3,
        height: "100%",
        overflow: "auto",
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
              <VllmEngineArgsPanel
                values={engineArgs}
                onChange={setEngineArgs}
                version={engineArgs["enforce-eager"] ? "0.6.6" : "0.7.3"}
                modelName={model?.display_name}
              />
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
              {t("llm_runtime_detail.save_and_restart")}
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
            <MetaField
              label={t("llm_runtime_detail.openai_compat")}
              value={rt.openai_compat ? t("common.yes") : t("common.no")}
            />
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
        </CardContent>
      </Card>

      {/* Health card */}
      {!editing && (isRunning || rt.status === "error") && (
        <Card variant="outlined">
          <CardContent>
            <Stack direction="row" alignItems="center" spacing={2}>
              {health ? (
                <>
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
                  <Typography variant="body2" color="text.secondary">
                    {health.detail}
                  </Typography>
                </>
              ) : (
                <Typography variant="body2" color="text.disabled">
                  {t("llm_runtime_detail.health_unavailable")}
                </Typography>
              )}
            </Stack>
          </CardContent>
        </Card>
      )}

      {/* Logs */}
      {showLogs && (
        <Box sx={{ flexGrow: 1, minHeight: 200 }}>
          <Typography variant="subtitle2" sx={{ mb: 1 }}>
            {t("llm_runtime_detail.container_logs")}
          </Typography>
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
