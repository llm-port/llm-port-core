/**
 * How vLLM runs this deployment, and a way to change it.
 *
 * The card lists what differs from vLLM's defaults; the dialog is the same
 * editor the host dialog uses, fed with the model's facts and the settings
 * the marketplace would suggest for it here. Saving changes the engine, so
 * the copies restart -- the dialog says so before, not after.
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { inferenceApi, type InferenceDeployment } from "~/api/inference";
import { marketplaceApi, type MarketDetail } from "~/api/marketplace";
import { EngineSettingsEditor } from "~/components/engine/EngineSettingsEditor";
import { toClusterConfig, toFlag, type EngineConfig } from "~/lib/engine";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import Chip from "@mui/material/Chip";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import LinearProgress from "@mui/material/LinearProgress";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";

import TuneIcon from "@mui/icons-material/Tune";

import { acceleratorsPerCopy, asRecord } from "./common";

/** The engine settings a deployment's spec carries. */
export function engineConfigOf(deployment: InferenceDeployment): EngineConfig {
  const config = asRecord(asRecord(deployment.spec?.engine).config);
  const out: EngineConfig = {};
  for (const [key, value] of Object.entries(config)) {
    if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") out[key] = value;
  }
  return out;
}

/** The spec with *config* as its engine settings, and nothing else changed. */
export function withEngineConfig(deployment: InferenceDeployment, config: EngineConfig): Record<string, unknown> {
  const engine = asRecord(deployment.spec?.engine);
  return { ...deployment.spec, engine: { ...engine, name: engine.name ?? "vllm", config } };
}

function tensorParallel(deployment: InferenceDeployment): number {
  const tp = Number(asRecord(deployment.spec?.topology).tensor_parallel_size);
  if (Number.isFinite(tp) && tp >= 1) return tp;
  const gpus = acceleratorsPerCopy(deployment);
  return gpus >= 1 ? gpus : 1;
}

export interface DeploymentEngineCardProps {
  deployment: InferenceDeployment;
  /** The Hugging Face repository the deployment serves, for the model's facts and suggestions. */
  repoId: string | null;
  onSaved: () => void | Promise<void>;
}

export function DeploymentEngineCard({ deployment, repoId, onSaved }: DeploymentEngineCardProps) {
  const { t } = useTranslation();
  const current = engineConfigOf(deployment);
  const entries = Object.entries(current);
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState<EngineConfig>(current);
  const [extra, setExtra] = useState("");
  const [detail, setDetail] = useState<MarketDetail | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function openEditor() {
    setValue(engineConfigOf(deployment));
    setExtra("");
    setError(null);
    setOpen(true);
    if (repoId && !detail) {
      // What the model is and what would be suggested here; the editor works without it.
      marketplaceApi
        .detail(repoId, deployment.environment_id)
        .then(setDetail)
        .catch(() => undefined);
    }
  }

  async function save() {
    const { config, issues } = toClusterConfig(value, extra);
    if (issues.length > 0) return;
    setSaving(true);
    setError(null);
    try {
      await inferenceApi.updateDeployment(deployment.id, { spec: withEngineConfig(deployment, config) });
      await inferenceApi.reconcileDeployment(deployment.id).catch(() => undefined);
      setOpen(false);
      await onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("common.save_failed"));
    } finally {
      setSaving(false);
    }
  }

  const fit = detail?.fits[deployment.environment_id] ?? null;
  const cluster = detail?.clusters.find((c) => c.environment_id === deployment.environment_id) ?? null;
  const changed = JSON.stringify(toClusterConfig(value, extra).config) !== JSON.stringify(current);

  return (
    <Card variant="outlined">
      <CardContent>
        <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1 }}>
          <Typography variant="subtitle2" sx={{ flexGrow: 1 }}>
            {t("inference.engine.title")}
          </Typography>
          <Button size="small" startIcon={<TuneIcon />} onClick={openEditor} data-testid="engine-edit">
            {t("inference.engine.edit")}
          </Button>
        </Stack>
        {entries.length === 0 ? (
          <Typography variant="body2" color="text.secondary">
            {t("inference.engine.defaults")}
          </Typography>
        ) : (
          <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap data-testid="engine-summary">
            {entries.map(([key, v]) => (
              <Chip
                key={key}
                size="small"
                variant="outlined"
                sx={{ fontFamily: "monospace" }}
                label={v === true ? `--${toFlag(key)}` : `--${toFlag(key)} ${String(v)}`}
              />
            ))}
          </Stack>
        )}
      </CardContent>

      <Dialog open={open} onClose={saving ? undefined : () => setOpen(false)} maxWidth="md" fullWidth>
        <DialogTitle>{t("inference.engine.dialog_title", { name: deployment.name })}</DialogTitle>
        <DialogContent dividers>
          <Stack spacing={2}>
            <Alert severity="warning">{t("inference.engine.restart_warning")}</Alert>
            {error && <Alert severity="error">{error}</Alert>}
            <EngineSettingsEditor
              value={value}
              extra={extra}
              target="cluster"
              onChange={(v, e) => {
                setValue(v);
                setExtra(e);
              }}
              model={{
                repoId,
                maxContext: detail?.model.max_context ?? null,
                capabilities: detail?.model.capabilities ?? [],
                weightsBytes: detail?.model.weights_bytes ?? null,
                kvBytesPerToken: detail?.model.kv_bytes_per_token ?? null,
                needsRemoteCode: detail?.model.needs_remote_code ?? false,
                architecture: detail?.model.architecture ?? null,
              }}
              hardware={{
                gpuBytes: cluster?.gpu_bytes ?? null,
                tensorParallel: tensorParallel(deployment),
                maxContextThatFits: fit?.max_context ?? null,
              }}
              suggested={detail?.suggested}
              disabled={saving}
            />
            {saving && (
              <Box>
                <LinearProgress />
              </Box>
            )}
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpen(false)} disabled={saving}>
            {t("common.cancel")}
          </Button>
          <Button
            variant="contained"
            disabled={saving || !changed || toClusterConfig(value, extra).issues.length > 0}
            onClick={() => void save()}
            data-testid="engine-save"
          >
            {t("inference.engine.save_restart")}
          </Button>
        </DialogActions>
      </Dialog>
    </Card>
  );
}
