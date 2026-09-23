/**
 * Deploy a model — one dialog.
 *
 * The old flow asked for an environment, a model, a replica count, a GPU
 * count, and then left the operator to discover that a spec document was
 * being composed from their answers. Here the spec stays an implementation
 * detail: pick a model, say how much of the cluster to give it, deploy.
 */
import { useEffect, useState } from "react";

import { inferenceApi } from "~/api/inference";
import type { InferenceEnvironment } from "~/api/inference";
import type { Model } from "~/api/llm";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import LinearProgress from "@mui/material/LinearProgress";
import MenuItem from "@mui/material/MenuItem";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

/**
 * The smallest document the backend's v1alpha1 schema accepts.
 *
 * ``chatName`` becomes ``service.alias``: the name the model is offered under
 * in chat and at the gateway. The backend deliberately never makes one up,
 * so a deployment made here without one served perfectly and never appeared
 * in chat. Blank still means "API only".
 */
export function buildSpec(replicas: number, gpusPerReplica: number, chatName = "") {
  const alias = chatName.trim();
  return {
    api_version: "inference.llmport.ai/v1alpha1",
    engine: { name: "vllm", config: {} },
    scale: { replicas },
    resources: { replica: { gpus: gpusPerReplica } },
    service: { path: "/v1", openai: true, ...(alias ? { alias } : {}) },
  };
}

/**
 * The name to offer a model under in chat, proposed from the model.
 *
 * The repository's own name, lower-cased: two deployments of one model on
 * two clusters then share it, and chat routes across both.
 */
export function suggestChatName(model: Model | undefined): string {
  if (!model) return "";
  const base = (model.hf_repo_id || model.display_name || "").split("/").pop() as string;
  return base.trim().toLowerCase();
}

/** A name the backend accepts, derived from what the operator picked. */
export function suggestDeploymentName(model: Model | undefined): string {
  if (!model) return "";
  const base = (model.hf_repo_id || model.display_name || "model")
    .split("/")
    .pop() as string;
  return base.toLowerCase().replace(/[^a-z0-9-]+/g, "-").replace(/^-|-$/g, "");
}

/**
 * The models worth offering, each with what tells it apart.
 *
 * A failed download has no files: deploying it can only fail, so it is left
 * out. One still downloading is offered -- the deployment waits for it -- but
 * says so. Where two records share a name (older installs made one per
 * download) the date added is the difference, so it is shown.
 */
export function deployableModels(models: Model[]): { model: Model; detail: string }[] {
  const usable = models.filter((m) => m.status === "available" || m.status === "downloading");
  const named = new Map<string, number>();
  for (const m of usable) named.set(m.display_name, (named.get(m.display_name) ?? 0) + 1);
  return usable.map((model) => {
    const parts: string[] = [];
    if (model.hf_repo_id && model.hf_repo_id !== model.display_name) parts.push(model.hf_repo_id);
    if (model.status === "downloading") parts.push("still downloading");
    if ((named.get(model.display_name) ?? 0) > 1) {
      parts.push(`added ${new Date(model.created_at).toLocaleDateString()}`);
    }
    return { model, detail: parts.join(" · ") };
  });
}

export interface DeployModelWizardProps {
  open: boolean;
  models: Model[];
  clusters: InferenceEnvironment[];
  /** Pre-selected when opened from a cluster page. */
  clusterId?: string;
  onClose: () => void;
  onDeployed: (deploymentId: string) => void;
}

export function DeployModelWizard({
  open,
  models,
  clusters,
  clusterId,
  onClose,
  onDeployed,
}: DeployModelWizardProps) {
  const [modelId, setModelId] = useState("");
  const [targetCluster, setTargetCluster] = useState(clusterId ?? "");
  const [name, setName] = useState("");
  const [chatName, setChatName] = useState("");
  const [replicas, setReplicas] = useState(1);
  const [gpus, setGpus] = useState(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setModelId("");
    setTargetCluster(clusterId ?? (clusters.length === 1 ? clusters[0].id : ""));
    setName("");
    setChatName("");
    setReplicas(1);
    setGpus(1);
    setError(null);
  }, [open, clusterId, clusters]);

  const model = models.find((m) => m.id === modelId);
  const offered = deployableModels(models);

  function pickModel(id: string) {
    setModelId(id);
    const suggestion = suggestDeploymentName(models.find((m) => m.id === id));
    // Only fill the name while the operator has not typed their own.
    setName((current) => (current ? current : suggestion));
    setChatName((current) =>
      current ? current : suggestChatName(models.find((m) => m.id === id)),
    );
  }

  async function deploy() {
    setBusy(true);
    setError(null);
    try {
      const created = await inferenceApi.createDeployment({
        environment_id: targetCluster,
        model_id: modelId,
        name: name.trim(),
        spec: buildSpec(replicas, gpus, chatName),
      });
      // Ask for convergence straight away rather than waiting for the loop:
      // the operator is watching, and a 30s pause reads as nothing happening.
      await inferenceApi.reconcileDeployment(created.id).catch(() => undefined);
      onDeployed(created.id);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  const ready = Boolean(modelId && targetCluster && name.trim());

  return (
    <Dialog open={open} onClose={busy ? undefined : onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Deploy a model</DialogTitle>
      <DialogContent dividers>
        <Stack spacing={2} sx={{ mt: 1 }}>
          {error && <Alert severity="error">{error}</Alert>}

          {offered.length === 0 && (
            <Alert severity="info">
              No models are available yet. Add one under LLM → Models first.
            </Alert>
          )}

          <TextField
            select
            label="Model"
            value={modelId}
            fullWidth
            onChange={(e) => pickModel(e.target.value)}
            slotProps={{
              select: {
                // The menu shows what tells the entries apart; the chosen
                // one reads as a plain name in the field.
                renderValue: (id) => models.find((m) => m.id === id)?.display_name ?? "",
              },
            }}
          >
            {offered.map(({ model: m, detail }) => (
              <MenuItem key={m.id} value={m.id}>
                <Box>
                  <Typography variant="body1">{m.display_name}</Typography>
                  {detail && (
                    <Typography variant="caption" color="text.secondary">
                      {detail}
                    </Typography>
                  )}
                </Box>
              </MenuItem>
            ))}
          </TextField>

          {!clusterId && (
            <TextField
              select
              label="Cluster"
              value={targetCluster}
              fullWidth
              onChange={(e) => setTargetCluster(e.target.value)}
            >
              {clusters.map((c) => (
                <MenuItem key={c.id} value={c.id}>
                  {c.name} ({c.status})
                </MenuItem>
              ))}
            </TextField>
          )}

          <TextField
            label="Name this deployment"
            value={name}
            fullWidth
            helperText="How it will appear in the list and in logs."
            onChange={(e) => setName(e.target.value)}
          />

          <TextField
            label="Offer in chat as"
            value={chatName}
            fullWidth
            helperText={
              chatName.trim()
                ? "The name people pick in chat and use at the API. Clear it to serve by endpoint only."
                : "Not offered in chat: it is reachable only at its endpoint."
            }
            onChange={(e) => setChatName(e.target.value)}
          />

          <Stack direction="row" spacing={2}>
            <TextField
              label="Copies"
              type="number"
              value={replicas}
              slotProps={{ htmlInput: { min: 1 } }}
              helperText="More copies serve more traffic."
              onChange={(e) => setReplicas(Math.max(1, Number(e.target.value) || 1))}
            />
            <TextField
              label="Accelerators per copy"
              type="number"
              value={gpus}
              slotProps={{ htmlInput: { min: 0, step: 1 } }}
              helperText="Large models need more than one."
              onChange={(e) => setGpus(Math.max(0, Number(e.target.value) || 0))}
            />
          </Stack>

          {model && (
            <Typography variant="caption" color="text.secondary">
              The model is copied to every machine that needs it before the first
              copy starts. You can watch that on the deployment page.
            </Typography>
          )}

          {busy && (
            <Box>
              <Typography variant="body2" sx={{ mb: 0.5 }}>
                Creating the deployment…
              </Typography>
              <LinearProgress />
            </Box>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={busy}>
          Cancel
        </Button>
        <Button variant="contained" disabled={!ready || busy} onClick={() => void deploy()}>
          Deploy
        </Button>
      </DialogActions>
    </Dialog>
  );
}
