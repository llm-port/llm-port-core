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

import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import Checkbox from "@mui/material/Checkbox";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import FormControlLabel from "@mui/material/FormControlLabel";
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
  const [editMaxModelLen, setEditMaxModelLen] = useState("");
  const [editDtype, setEditDtype] = useState("");
  const [editGpuMemUtil, setEditGpuMemUtil] = useState("");
  const [editTensorParallel, setEditTensorParallel] = useState("");
  const [editSwapSpace, setEditSwapSpace] = useState("");
  const [editExtraArgs, setEditExtraArgs] = useState("");
  const [editEnforceEager, setEditEnforceEager] = useState(true);
  const [saving, setSaving] = useState(false);

  function openEditor() {
    if (!rt) return;
    const gc = rt.generic_config ?? {};
    const pc = rt.provider_config ?? {};
    setEditName(rt.name);
    setEditMaxModelLen(gc.max_model_len != null ? String(gc.max_model_len) : "");
    setEditDtype(gc.dtype ?? "");
    setEditGpuMemUtil(gc.gpu_memory_utilization != null ? String(gc.gpu_memory_utilization) : "");
    setEditTensorParallel(gc.tensor_parallel_size != null ? String(gc.tensor_parallel_size) : "");
    setEditSwapSpace(gc.swap_space != null ? String(gc.swap_space) : "");
    setEditExtraArgs(Array.isArray(pc.extra_args) ? pc.extra_args.join("\n") : "");
    setEditEnforceEager(gc.enforce_eager !== false);
    setEditing(true);
  }

  async function handleSaveAndRestart() {
    if (!id || !rt) return;
    setSaving(true);
    try {
      const generic_config: Record<string, unknown> = { ...(rt.generic_config ?? {}) };
      if (editMaxModelLen) generic_config.max_model_len = parseInt(editMaxModelLen, 10);
      else delete generic_config.max_model_len;
      if (editDtype) generic_config.dtype = editDtype;
      else delete generic_config.dtype;
      if (editGpuMemUtil) generic_config.gpu_memory_utilization = parseFloat(editGpuMemUtil);
      else delete generic_config.gpu_memory_utilization;
      if (editTensorParallel) generic_config.tensor_parallel_size = parseInt(editTensorParallel, 10);
      else delete generic_config.tensor_parallel_size;
      if (editSwapSpace) generic_config.swap_space = parseInt(editSwapSpace, 10);
      else delete generic_config.swap_space;
      generic_config.enforce_eager = editEnforceEager;

      const provider_config: Record<string, unknown> = { ...(rt.provider_config ?? {}) };
      if (editExtraArgs.trim()) provider_config.extra_args = editExtraArgs.trim().split(/\r?\n/).map(s => s.trim()).filter(Boolean);
      else delete provider_config.extra_args;

      const updated = await runtimes.update(id, {
        name: editName !== rt.name ? editName : undefined,
        generic_config,
        provider_config,
      });
      setRt(updated);          // use response directly — avoids race with DB commit
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

      // Health & logs for running or starting runtimes
      if (r.status === "running" || r.status === "starting") {
        try {
          setHealth(await runtimes.health(id));
        } catch {
          setHealth(null);
        }
        try {
          const logRes = await runtimes.fetchLogs(id, 300);
          setLogs(await logRes.text());
        } catch {
          setLogs("");
        }
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t("llm_runtime_detail.failed_load"));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, [id]);

  // Poll for status updates while in a transient state (starting/creating)
  useEffect(() => {
    if (!rt || !id) return;
    if (rt.status !== "starting" && rt.status !== "creating") return;
    const interval = setInterval(async () => {
      try {
        const updated = await runtimes.get(id);
        setRt(updated);
        if (updated.status === "running" || updated.status === "error" || updated.status === "stopped") {
          clearInterval(interval);
          load(); // full reload to get health/logs
        }
      } catch { /* ignore polling errors */ }
    }, 5000);
    return () => clearInterval(interval);
  }, [rt?.status, id]);

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
      setRt(updated);           // use response directly — avoids race with DB commit
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
    return <Alert severity="error">{error ?? t("llm_runtime_detail.not_found")}</Alert>;
  }

  const isRunning = rt.status === "running" || rt.status === "starting";
  const isStopped = rt.status === "stopped" || rt.status === "error";

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 3, height: "100%", overflow: "auto" }}>
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
              >
                {t("common.stop")}
              </Button>
              <Button
                size="small"
                variant="outlined"
                color="info"
                startIcon={<RestartAltIcon />}
                onClick={() => handleAction("restart")}
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
            disabled={isRunning}
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
        <Card variant="outlined" sx={{ borderColor: "primary.main" }}>
          <CardContent>
            <Typography variant="subtitle2" sx={{ mb: 2 }}>{t("llm_runtime_detail.edit_config")}</Typography>
            <Stack spacing={2}>
              <TextField
                label={t("common.name")}
                size="small"
                value={editName}
                onChange={(e) => setEditName(e.target.value)}
                fullWidth
              />
              <Stack direction="row" spacing={2} flexWrap="wrap">
                <TextField
                  label="max_model_len"
                  size="small"
                  type="number"
                  value={editMaxModelLen}
                  onChange={(e) => setEditMaxModelLen(e.target.value)}
                  placeholder="e.g. 4096"
                  helperText={t("llm_runtime_detail.max_model_len_help")}
                  sx={{ minWidth: 180 }}
                />
                <TextField
                  label="dtype"
                  size="small"
                  value={editDtype}
                  onChange={(e) => setEditDtype(e.target.value)}
                  placeholder="e.g. float16, auto"
                  sx={{ minWidth: 160 }}
                />
                <TextField
                  label="gpu_memory_utilization"
                  size="small"
                  type="number"
                  inputProps={{ step: 0.05, min: 0.1, max: 1.0 }}
                  value={editGpuMemUtil}
                  onChange={(e) => setEditGpuMemUtil(e.target.value)}
                  placeholder="e.g. 0.9"
                  sx={{ minWidth: 200 }}
                />
                <TextField
                  label="tensor_parallel_size"
                  size="small"
                  type="number"
                  value={editTensorParallel}
                  onChange={(e) => setEditTensorParallel(e.target.value)}
                  placeholder="e.g. 1"
                  sx={{ minWidth: 180 }}
                />
                <TextField
                  label="swap_space"
                  size="small"
                  type="number"
                  value={editSwapSpace}
                  onChange={(e) => setEditSwapSpace(e.target.value)}
                  placeholder="e.g. 4"
                  helperText={t("llm_runtime_detail.swap_space_help")}
                  sx={{ minWidth: 160 }}
                />
              </Stack>
              <FormControlLabel
                control={
                  <Checkbox
                    checked={editEnforceEager}
                    onChange={(e) => setEditEnforceEager(e.target.checked)}
                    size="small"
                  />
                }
                label={t("llm_runtime_detail.enforce_eager")}
              />
              <TextField
                label={t("llm_runtime_detail.extra_args")}
                size="small"
                multiline
                minRows={2}
                value={editExtraArgs}
                onChange={(e) => setEditExtraArgs(e.target.value)}
                placeholder={"--enforce-eager\n--disable-log-stats"}
                helperText={t("llm_runtime_detail.extra_args_help")}
                fullWidth
              />
              <Stack direction="row" spacing={1} justifyContent="flex-end">
                <Button size="small" onClick={() => setEditing(false)}>{t("common.cancel")}</Button>
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
            </Stack>
          </CardContent>
        </Card>
      )}

      {/* Metadata card */}
      <Card variant="outlined">
        <CardContent>
          <Stack direction="row" flexWrap="wrap" gap={4}>
            <MetaField label={t("llm_common.provider")} value={provider?.name ?? rt.provider_id.slice(0, 8)} />
            {provider && (
              <Box>
                <Typography variant="caption" color="text.secondary">{t("llm_common.engine")}</Typography>
                <Box mt={0.5}><EngineChip value={provider.type} /></Box>
              </Box>
            )}
            <MetaField label={t("llm_common.model")} value={model?.display_name ?? rt.model_id.slice(0, 8)} />
            <MetaField label={t("llm_runtime_detail.openai_compat")} value={rt.openai_compat ? t("common.yes") : t("common.no")} />
            {rt.endpoint_url && <MetaField label={t("llm_runtimes.endpoint")} value={rt.endpoint_url} mono />}
            {rt.container_ref && <MetaField label={t("containers.title")} value={rt.container_ref.slice(0, 12)} mono />}
            <MetaField label={t("common.created")} value={new Date(rt.created_at).toLocaleString()} />
          </Stack>
        </CardContent>
      </Card>

      {/* Health card */}
      {isRunning && (
        <Card variant="outlined">
          <CardContent>
            <Stack direction="row" alignItems="center" spacing={2}>
              {health ? (
                <>
                  {health.healthy ? (
                    <Chip icon={<FavoriteIcon />} label={t("llm_runtime_detail.healthy")} color="success" size="small" />
                  ) : (
                    <Chip icon={<HeartBrokenIcon />} label={t("llm_runtime_detail.unhealthy")} color="error" size="small" />
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
      {isRunning && (
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

function MetaField({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <Box>
      <Typography variant="caption" color="text.secondary">{label}</Typography>
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
