/**
 * Host a model on a cluster: where, how big, how vLLM runs it, under what name.
 *
 * One dialog for every way in. From the marketplace it hosts a Hugging Face
 * repository (the server downloads it first when it does not keep it); from a
 * cluster or the deployments list it offers the models the server already
 * keeps, including ones registered from a local path. The fit check sizes the
 * copy, the suggested settings fill the engine form, and the operator sees
 * both before anything starts.
 */
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";

import { inferenceApi } from "~/api/inference";
import type { Model } from "~/api/llm";
import { marketplaceApi, type Fit, type MarketCluster, type MarketDetail } from "~/api/marketplace";
import { EngineSettingsEditor } from "~/components/engine/EngineSettingsEditor";
import { formatBytes, parseExtraFlags, type EngineConfig } from "~/lib/engine";
import {
  DEPLOYMENT_NAME,
  buildSpec,
  defaultGpuChoice,
  deployableModels,
  fitDetail,
  fitSummary,
  formatParams,
  gpuChoiceLabel,
  gpuChoices,
  suggestChatName,
  suggestDeploymentName,
} from "~/lib/hosting";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import LinearProgress from "@mui/material/LinearProgress";
import Paper from "@mui/material/Paper";
import Radio from "@mui/material/Radio";
import Stack from "@mui/material/Stack";
import Step from "@mui/material/Step";
import StepLabel from "@mui/material/StepLabel";
import Stepper from "@mui/material/Stepper";
import TextField from "@mui/material/TextField";
import ToggleButton from "@mui/material/ToggleButton";
import ToggleButtonGroup from "@mui/material/ToggleButtonGroup";
import Typography from "@mui/material/Typography";

type StepId = "model" | "where" | "settings" | "review";

export interface HostModelDialogProps {
  open: boolean;
  onClose: () => void;
  onHosted: (deploymentId: string) => void;
  /** A Hugging Face repository to host; downloaded first if the server does not keep it. */
  repoId?: string | null;
  /** Models the server keeps, offered when no repository is given. */
  models?: Model[];
  /** The cluster to preselect. */
  clusterId?: string | null;
  /** Opened from a cluster's own page: that cluster only. */
  lockCluster?: boolean;
}

const tone = { success: "success", info: "info", warning: "warning", error: "error", default: "default" } as const;

export function HostModelDialog({
  open,
  onClose,
  onHosted,
  repoId,
  models = [],
  clusterId,
  lockCluster = false,
}: HostModelDialogProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();

  const steps: StepId[] = repoId ? ["where", "settings", "review"] : ["model", "where", "settings", "review"];
  const [step, setStep] = useState<StepId>(steps[0]);
  const [keptId, setKeptId] = useState<string>("");
  const [clusters, setClusters] = useState<MarketCluster[] | null>(null);
  const [cluster, setCluster] = useState<string>(clusterId ?? "");
  const [detail, setDetail] = useState<MarketDetail | null>(null);
  const [detailState, setDetailState] = useState<"idle" | "loading" | "failed">("idle");
  const [detailError, setDetailError] = useState<string | null>(null);
  const [gpus, setGpus] = useState<number>(1);
  const [copies, setCopies] = useState<number>(1);
  const [config, setConfig] = useState<EngineConfig>({});
  const [extra, setExtra] = useState("");
  const [edited, setEdited] = useState(false);
  const [name, setName] = useState("");
  const [chatName, setChatName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const kept = models.find((m) => m.id === keptId);
  const repo = repoId ?? kept?.hf_repo_id ?? null;
  const offered = useMemo(() => deployableModels(models), [models]);

  // A fresh dialog each time it opens.
  useEffect(() => {
    if (!open) return;
    setStep(repoId ? "where" : "model");
    setKeptId("");
    setCluster(clusterId ?? "");
    setDetail(null);
    setDetailState("idle");
    setDetailError(null);
    setConfig({});
    setExtra("");
    setEdited(false);
    setName(suggestDeploymentName(repoId ?? ""));
    setChatName(suggestChatName(repoId ?? ""));
    setCopies(1);
    setError(null);
    setBusy(false);
    let live = true;
    marketplaceApi
      .clusters()
      .then((list) => {
        if (!live) return;
        setClusters(list);
        if (!clusterId) {
          const usable = list.filter((c) => c.gpu_count > 0);
          if (usable.length === 1 || list.length === 1) setCluster((usable[0] ?? list[0]).environment_id);
        }
      })
      .catch((err) => live && setError(err instanceof Error ? err.message : String(err)));
    return () => {
      live = false;
    };
  }, [open, repoId, clusterId]);

  // What the model is and how it fits, measured against the chosen cluster.
  useEffect(() => {
    if (!open || !repo) return;
    let live = true;
    setDetailState("loading");
    setDetailError(null);
    marketplaceApi
      .detail(repo, cluster || null)
      .then((d) => {
        if (!live) return;
        setDetail(d);
        setDetailState("idle");
        if (!cluster && d.cluster_id && !lockCluster) setCluster(d.cluster_id);
      })
      .catch((err) => {
        if (!live) return;
        setDetail(null);
        setDetailState("failed");
        setDetailError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      live = false;
    };
  }, [open, repo, cluster, lockCluster]);

  const chosen = (clusters ?? []).find((c) => c.environment_id === cluster) ?? null;
  const fit: Fit | null = (cluster && detail?.fits[cluster]) || null;
  const choices = useMemo(() => gpuChoices(fit, chosen), [fit, chosen]);
  const maxCopies = choices.find((c) => Math.abs(c.value - gpus) < 1e-6)?.maxCopies ?? 1;

  // The planned size follows the cluster; the suggested settings follow until the operator edits them.
  useEffect(() => {
    if (!open) return;
    setGpus(defaultGpuChoice(fit, choices));
    setCopies(1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, cluster, fit?.status, fit?.gpus_per_copy, choices.length]);

  useEffect(() => {
    if (!open || edited) return;
    setConfig({ ...(detail?.suggested.config ?? {}) });
  }, [open, detail, edited]);

  function pickKept(id: string) {
    setKeptId(id);
    const m = models.find((x) => x.id === id);
    setName(suggestDeploymentName(m));
    setChatName(suggestChatName(m));
    setDetail(null);
    setEdited(false);
    setConfig({});
  }

  function changeSize(value: number) {
    const before = gpus;
    setGpus(value);
    setCopies((c) => Math.min(c, choices.find((x) => Math.abs(x.value - value) < 1e-6)?.maxCopies ?? 1));
    setConfig((current) => {
      const next = { ...current };
      if (value < 1) {
        // A shared card: the copy takes its share and no more.
        const share = Math.round(value * 100) / 100;
        const asked = Number(next.gpu_memory_utilization);
        next.gpu_memory_utilization = Number.isFinite(asked) && asked > 0 ? Math.min(asked, share) : share;
      } else if (before < 1 && Number(next.gpu_memory_utilization) <= before + 1e-6) {
        // A whole card again: the share set for sharing would waste it.
        delete next.gpu_memory_utilization;
      }
      return next;
    });
  }

  const parsedExtra = parseExtraFlags(extra);
  const extraIssues = parsedExtra.issues;
  const settingsCount = Object.keys({ ...config, ...parsedExtra.config }).length;
  const nameOk = DEPLOYMENT_NAME.test(name.trim());
  const fitBlocks = fit?.status === "too_large" || fit?.status === "no_accelerators";
  const canNext: Record<StepId, boolean> = {
    model: Boolean(kept),
    where: Boolean(chosen) && (chosen?.gpu_count ?? 0) > 0 && !fitBlocks && detailState !== "loading",
    settings: extraIssues.length === 0,
    review: nameOk && !busy,
  };

  function next() {
    const i = steps.indexOf(step);
    if (i < steps.length - 1) setStep(steps[i + 1]);
  }
  function back() {
    const i = steps.indexOf(step);
    if (i > 0) setStep(steps[i - 1]);
  }

  async function host() {
    if (!chosen) return;
    setBusy(true);
    setError(null);
    const { config: extraConfig } = parseExtraFlags(extra);
    const engineConfig = { ...config, ...extraConfig };
    try {
      let deploymentId: string;
      if (kept) {
        const created = await inferenceApi.createDeployment({
          environment_id: chosen.environment_id,
          model_id: kept.id,
          name: name.trim(),
          spec: buildSpec({ copies, gpusPerCopy: gpus, chatName, engineConfig }),
        });
        deploymentId = created.id;
      } else if (repo) {
        const result = await marketplaceApi.host({
          repo_id: repo,
          environment_id: chosen.environment_id,
          name: name.trim(),
          alias: chatName.trim() || null,
          copies,
          gpus_per_copy: gpus,
          engine_config: engineConfig,
        });
        deploymentId = result.deployment_id;
      } else {
        return;
      }
      // Ask for convergence now: the operator is watching, and a pause reads as nothing happening.
      await inferenceApi.reconcileDeployment(deploymentId).catch(() => undefined);
      onHosted(deploymentId);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  const model = detail?.model;
  const title = repo ?? kept?.display_name ?? t("hosting.title_generic");
  const willDownload = !kept && !(detail?.local && detail.local.status !== "failed");

  return (
    <Dialog open={open} onClose={busy ? undefined : onClose} maxWidth="md" fullWidth>
      <DialogTitle sx={{ pb: 1 }}>
        {t("hosting.title", { model: title })}
      </DialogTitle>
      <Box sx={{ px: 3, pb: 1 }}>
        <Stepper activeStep={steps.indexOf(step)} alternativeLabel>
          {steps.map((s) => (
            <Step key={s}>
              <StepLabel>{t(`hosting.step.${s}`)}</StepLabel>
            </Step>
          ))}
        </Stepper>
      </Box>
      <DialogContent dividers sx={{ minHeight: 380 }}>
        <Stack spacing={2}>
          {error && <Alert severity="error">{error}</Alert>}

          {step === "model" && (
            <Stack spacing={1.5}>
              <Typography variant="body2" color="text.secondary">
                {t("hosting.model_intro")}
              </Typography>
              {offered.length === 0 && <Alert severity="info">{t("hosting.no_models")}</Alert>}
              <Stack spacing={1} sx={{ maxHeight: 360, overflowY: "auto" }}>
                {offered.map(({ model: m, detail: d }) => (
                  <Paper
                    key={m.id}
                    variant="outlined"
                    onClick={() => pickKept(m.id)}
                    sx={{
                      p: 1.25,
                      cursor: "pointer",
                      borderColor: keptId === m.id ? "primary.main" : undefined,
                    }}
                    data-testid={`host-kept-${m.id}`}
                  >
                    <Stack direction="row" spacing={1} alignItems="center">
                      <Radio size="small" checked={keptId === m.id} />
                      <Box>
                        <Typography variant="body1">{m.display_name}</Typography>
                        {d && (
                          <Typography variant="caption" color="text.secondary">
                            {d}
                          </Typography>
                        )}
                      </Box>
                    </Stack>
                  </Paper>
                ))}
              </Stack>
              <Box>
                <Button size="small" onClick={() => navigate("/admin/marketplace")}>
                  {t("hosting.find_more")}
                </Button>
              </Box>
            </Stack>
          )}

          {step === "where" && (
            <WhereStep
              clusters={clusters}
              cluster={cluster}
              onCluster={setCluster}
              lockCluster={lockCluster}
              detail={detail}
              detailState={detailState}
              detailError={detailError}
              hasRepo={Boolean(repo)}
              gpus={gpus}
              choices={choices}
              onGpus={changeSize}
              copies={copies}
              maxCopies={maxCopies}
              onCopies={setCopies}
              fit={fit}
              chosen={chosen}
            />
          )}

          {step === "settings" && (
            <EngineSettingsEditor
              value={config}
              extra={extra}
              target="cluster"
              onChange={(v, e) => {
                setEdited(true);
                setConfig(v);
                setExtra(e);
              }}
              model={{
                repoId: repo ?? kept?.display_name ?? null,
                maxContext: model?.max_context ?? null,
                capabilities: model?.capabilities ?? [],
                weightsBytes: model?.weights_bytes ?? null,
                kvBytesPerToken: model?.kv_bytes_per_token ?? null,
                needsRemoteCode: model?.needs_remote_code ?? false,
                architecture: model?.architecture ?? null,
              }}
              hardware={{
                gpuBytes: chosen?.gpu_bytes ?? null,
                tensorParallel: gpus >= 1 ? gpus : 1,
                maxContextThatFits: fit?.max_context ?? null,
              }}
              suggested={detail?.suggested}
            />
          )}

          {step === "review" && (
            <Stack spacing={2}>
              <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
                <TextField
                  label={t("hosting.name_label")}
                  value={name}
                  fullWidth
                  error={Boolean(name) && !nameOk}
                  helperText={name && !nameOk ? t("hosting.name_invalid") : t("hosting.name_help")}
                  onChange={(e) => setName(e.target.value)}
                  slotProps={{ htmlInput: { "data-testid": "host-name" } }}
                />
                <TextField
                  label={t("hosting.chat_label")}
                  value={chatName}
                  fullWidth
                  helperText={chatName.trim() ? t("hosting.chat_help") : t("hosting.chat_none")}
                  onChange={(e) => setChatName(e.target.value)}
                  slotProps={{ htmlInput: { "data-testid": "host-chat-name" } }}
                />
              </Stack>

              <Paper variant="outlined" sx={{ p: 2 }}>
                <Stack spacing={0.75}>
                  <SummaryRow label={t("hosting.summary.model")} value={repo ?? kept?.display_name ?? "—"} />
                  <SummaryRow label={t("hosting.summary.cluster")} value={chosen?.name ?? "—"} />
                  <SummaryRow
                    label={t("hosting.summary.size")}
                    value={`${t("hosting.copies_value", { count: copies })} × ${gpuChoiceLabel(gpus, chosen?.accelerator)}`}
                  />
                  <SummaryRow
                    label={t("hosting.summary.settings")}
                    value={
                      settingsCount > 0
                        ? t("hosting.settings_changed", { count: settingsCount })
                        : t("hosting.settings_default")
                    }
                  />
                </Stack>
              </Paper>

              {willDownload && (
                <Alert severity="info" data-testid="host-download-note">
                  {model?.weights_bytes
                    ? t("hosting.download_note_size", { size: formatBytes(model.weights_bytes) })
                    : t("hosting.download_note")}
                </Alert>
              )}
              {!willDownload && <Alert severity="success">{t("hosting.kept_note")}</Alert>}
              {fit && fit.status === "fits" && copies > fit.copies_now && (
                <Alert severity="warning">{t("hosting.busy_warning", { count: fit.copies_now })}</Alert>
              )}
            </Stack>
          )}

          {busy && (
            <Box>
              <Typography variant="body2" sx={{ mb: 0.5 }}>
                {t("hosting.creating")}
              </Typography>
              <LinearProgress />
            </Box>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={busy}>
          {t("common.cancel")}
        </Button>
        <Box sx={{ flexGrow: 1 }} />
        {steps.indexOf(step) > 0 && (
          <Button onClick={back} disabled={busy}>
            {t("common.back")}
          </Button>
        )}
        {step !== "review" ? (
          <Button variant="contained" disabled={!canNext[step]} onClick={next} data-testid="host-next">
            {t("common.next")}
          </Button>
        ) : (
          <Button variant="contained" disabled={!canNext.review} onClick={() => void host()} data-testid="host-submit">
            {t("hosting.host")}
          </Button>
        )}
      </DialogActions>
    </Dialog>
  );
}

function SummaryRow({ label, value }: { label: string; value: string }) {
  return (
    <Stack direction="row" spacing={2}>
      <Typography variant="body2" color="text.secondary" sx={{ minWidth: 140 }}>
        {label}
      </Typography>
      <Typography variant="body2" sx={{ wordBreak: "break-all" }}>
        {value}
      </Typography>
    </Stack>
  );
}

interface WhereStepProps {
  clusters: MarketCluster[] | null;
  cluster: string;
  onCluster: (id: string) => void;
  lockCluster: boolean;
  detail: MarketDetail | null;
  detailState: "idle" | "loading" | "failed";
  detailError: string | null;
  hasRepo: boolean;
  gpus: number;
  choices: { value: number; maxCopies: number }[];
  onGpus: (v: number) => void;
  copies: number;
  maxCopies: number;
  onCopies: (n: number) => void;
  fit: Fit | null;
  chosen: MarketCluster | null;
}

function WhereStep({
  clusters,
  cluster,
  onCluster,
  lockCluster,
  detail,
  detailState,
  detailError,
  hasRepo,
  gpus,
  choices,
  onGpus,
  copies,
  maxCopies,
  onCopies,
  fit,
  chosen,
}: WhereStepProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();

  if (clusters === null) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", p: 4 }}>
        <CircularProgress />
      </Box>
    );
  }
  if (clusters.length === 0) {
    return (
      <Alert
        severity="info"
        action={
          <Button size="small" onClick={() => navigate("/admin/clusters")}>
            {t("hosting.go_to_clusters")}
          </Button>
        }
      >
        {t("hosting.no_clusters")}
      </Alert>
    );
  }
  const shown = lockCluster ? clusters.filter((c) => c.environment_id === cluster) : clusters;
  const model = detail?.model;
  const params = formatParams(model?.params_b, model?.active_params_b);

  return (
    <Stack spacing={2}>
      {model && (
        <Typography variant="body2" color="text.secondary">
          {[params, model.weights_bytes ? formatBytes(model.weights_bytes) : null, model.quantization?.toUpperCase()]
            .filter(Boolean)
            .join(" · ")}
        </Typography>
      )}
      {hasRepo && detailState === "failed" && (
        <Alert severity="warning">{t("hosting.no_fit", { error: detailError ?? "" })}</Alert>
      )}
      {!hasRepo && <Alert severity="info">{t("hosting.local_no_fit")}</Alert>}

      <Typography variant="subtitle2">{t("hosting.where_title")}</Typography>
      <Stack spacing={1}>
        {shown.map((c) => {
          const f = detail?.fits[c.environment_id] ?? null;
          const summary = hasRepo && detail ? fitSummary(f) : null;
          const blocked = f?.status === "too_large" || c.gpu_count === 0;
          return (
            <Paper
              key={c.environment_id}
              variant="outlined"
              onClick={() => !blocked && onCluster(c.environment_id)}
              sx={{
                p: 1.5,
                cursor: blocked ? "not-allowed" : "pointer",
                opacity: blocked ? 0.6 : 1,
                borderColor: cluster === c.environment_id ? "primary.main" : undefined,
              }}
              data-testid={`host-cluster-${c.environment_id}`}
            >
              <Stack direction="row" spacing={1} alignItems="flex-start">
                <Radio size="small" checked={cluster === c.environment_id} disabled={blocked} sx={{ mt: -0.5 }} />
                <Box sx={{ flexGrow: 1 }}>
                  <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
                    <Typography variant="body1" fontWeight={600}>
                      {c.name}
                    </Typography>
                    {summary && <Chip size="small" label={summary.label} color={tone[summary.tone]} />}
                  </Stack>
                  <Typography variant="body2" color="text.secondary">
                    {c.gpu_count > 0
                      ? t("hosting.cluster_hardware", {
                          count: c.gpu_count,
                          accelerator: c.accelerator ?? t("hosting.an_accelerator"),
                          memory: formatBytes(c.gpu_bytes),
                        })
                      : t("hosting.cluster_no_gpus")}
                  </Typography>
                  {f && hasRepo && (
                    <Typography variant="caption" color="text.secondary">
                      {fitDetail(f, c)}
                    </Typography>
                  )}
                </Box>
              </Stack>
            </Paper>
          );
        })}
      </Stack>
      {detailState === "loading" && <LinearProgress />}

      {chosen && choices.length > 0 && (
        <Stack spacing={1.5}>
          <Typography variant="subtitle2">{t("hosting.size_title")}</Typography>
          <ToggleButtonGroup
            exclusive
            size="small"
            value={gpus}
            onChange={(_, v: number | null) => v !== null && onGpus(v)}
            sx={{ flexWrap: "wrap" }}
          >
            {choices.map((c) => (
              <ToggleButton key={c.value} value={c.value} data-testid={`host-gpus-${c.value}`} sx={{ textTransform: "none" }}>
                {gpuChoiceLabel(c.value, chosen.accelerator)}
              </ToggleButton>
            ))}
          </ToggleButtonGroup>
          <Typography variant="caption" color="text.secondary">
            {gpus < 1
              ? t("hosting.size_share_help")
              : gpus > 1
                ? t("hosting.size_split_help", { count: gpus })
                : fit?.status === "fits"
                  ? t("hosting.size_one_help")
                  : ""}
          </Typography>

          <Stack direction="row" spacing={2} alignItems="center">
            <TextField
              label={t("hosting.copies_label")}
              type="number"
              size="small"
              value={copies}
              sx={{ width: 140 }}
              slotProps={{ htmlInput: { min: 1, max: maxCopies, "data-testid": "host-copies" } }}
              onChange={(e) => onCopies(Math.min(maxCopies, Math.max(1, Number(e.target.value) || 1)))}
            />
            <Typography variant="caption" color="text.secondary">
              {t("hosting.copies_help", { count: maxCopies })}
            </Typography>
          </Stack>
        </Stack>
      )}
    </Stack>
  );
}
