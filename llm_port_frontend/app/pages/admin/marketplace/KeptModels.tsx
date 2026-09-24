/**
 * The models this server keeps: what is downloading, what each one is, what
 * uses it -- and adding, hosting and deleting them.
 *
 * A download is the worker's job, not the page's. Leave while one runs and
 * come back, and it is where it has got to: the page only reads it, every
 * few seconds while anything is moving.
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";

import { jobs, models as modelsApi } from "~/api/llm";
import type { KeptModel } from "~/api/marketplace";
import { formatBytes } from "~/lib/engine";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import Checkbox from "@mui/material/Checkbox";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import FormControlLabel from "@mui/material/FormControlLabel";
import Grid from "@mui/material/Grid";
import LinearProgress from "@mui/material/LinearProgress";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import AddIcon from "@mui/icons-material/Add";
import FolderIcon from "@mui/icons-material/Folder";
import ManageSearchIcon from "@mui/icons-material/ManageSearch";

import { OwnerAvatar } from "./ModelCard";

/** A download the worker is still on. */
export function isDownloading(m: KeptModel): boolean {
  return m.download?.status === "queued" || m.download?.status === "running";
}

/** A download that ended without the model: offered again or removed. */
function downloadFailed(m: KeptModel): boolean {
  return m.status === "failed" && (m.download?.status === "failed" || m.download?.status === "canceled");
}

/** Deployments still holding the model (one being removed lets go shortly). */
function liveDeployments(m: KeptModel) {
  return m.deployments;
}

function runningRuntimes(m: KeptModel) {
  return m.runtimes.filter((r) => r.status !== "stopped" && r.status !== "error");
}

export interface KeptModelsProps {
  items: KeptModel[];
  loading: boolean;
  error: string | null;
  onChanged: () => void | Promise<void>;
  onHost: (model: KeptModel) => void;
  onOpen: (repoId: string) => void;
  can: (permission: string) => boolean;
}

function errorText(err: unknown, fallback: string): string {
  return err instanceof Error && err.message ? err.message : fallback;
}

export function KeptModels({ items, loading, error, onChanged, onHost, onOpen, can }: KeptModelsProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [notice, setNotice] = useState<{ severity: "success" | "info" | "error"; text: string } | null>(null);
  const [scanning, setScanning] = useState(false);
  const [busyJob, setBusyJob] = useState<string | null>(null);
  const [registerOpen, setRegisterOpen] = useState(false);
  const [deleting, setDeleting] = useState<KeptModel | null>(null);

  const downloading = items.filter(isDownloading);
  const failed = items.filter(downloadFailed);
  const ready = items.filter((m) => !isDownloading(m) && !downloadFailed(m));
  const onDisk = items.reduce((sum, m) => sum + (m.size_bytes ?? 0), 0);

  async function scan() {
    setScanning(true);
    setNotice(null);
    try {
      const result = await modelsApi.scanLocal();
      setNotice(
        result.imported_count > 0
          ? { severity: "success", text: t("marketplace.kept.scan_found", { count: result.imported_count }) }
          : { severity: "info", text: t("marketplace.kept.scan_none") },
      );
      await onChanged();
    } catch (err) {
      setNotice({ severity: "error", text: errorText(err, t("common.action_failed")) });
    } finally {
      setScanning(false);
    }
  }

  async function jobAction(m: KeptModel, action: "cancel" | "retry") {
    if (!m.download) return;
    setBusyJob(m.download.job_id);
    try {
      if (action === "cancel") await jobs.cancel(m.download.job_id);
      else await jobs.retry(m.download.job_id);
      await onChanged();
    } catch (err) {
      setNotice({ severity: "error", text: errorText(err, t("common.action_failed")) });
    } finally {
      setBusyJob(null);
    }
  }

  return (
    <Stack spacing={2.5}>
      <Stack direction={{ xs: "column", sm: "row" }} spacing={1} alignItems={{ sm: "center" }}>
        <Typography variant="body2" color="text.secondary" sx={{ flexGrow: 1 }}>
          {t("marketplace.kept.summary", { count: items.length, size: formatBytes(onDisk || null) })}
        </Typography>
        {can("llm.models:create") && (
          <>
            <Button
              size="small"
              startIcon={scanning ? <CircularProgress size={14} /> : <ManageSearchIcon />}
              disabled={scanning}
              onClick={() => void scan()}
              data-testid="kept-scan"
            >
              {t("marketplace.kept.scan")}
            </Button>
            <Button size="small" variant="outlined" startIcon={<AddIcon />} onClick={() => setRegisterOpen(true)}>
              {t("marketplace.kept.add_path")}
            </Button>
          </>
        )}
      </Stack>

      {notice && (
        <Alert severity={notice.severity} onClose={() => setNotice(null)}>
          {notice.text}
        </Alert>
      )}
      {error && <Alert severity="error">{error}</Alert>}

      {(downloading.length > 0 || failed.length > 0) && (
        <Box data-testid="kept-downloads">
          <Typography variant="subtitle1" fontWeight={600} gutterBottom>
            {t("marketplace.downloads.title")}
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
            {t("marketplace.downloads.help")}
          </Typography>
          <Stack spacing={1}>
            {downloading.map((m) => (
              <Paper key={m.model_id} variant="outlined" sx={{ p: 1.5 }} data-testid={`download-${m.model_id}`}>
                <Stack direction="row" spacing={1.5} alignItems="center">
                  <Box sx={{ flexGrow: 1, minWidth: 0 }}>
                    <Stack direction="row" spacing={1} alignItems="baseline">
                      <Typography variant="body2" fontWeight={600} noWrap>
                        {m.hf_repo_id ?? m.display_name}
                      </Typography>
                      <Typography variant="caption" color="text.secondary">
                        {m.download?.status === "queued"
                          ? t("marketplace.downloads.queued")
                          : t("marketplace.downloads.progress", { pct: m.download?.progress ?? 0 })}
                      </Typography>
                    </Stack>
                    <LinearProgress
                      variant={m.download?.status === "queued" ? "indeterminate" : "determinate"}
                      value={m.download?.progress ?? 0}
                      sx={{ mt: 0.75, height: 6, borderRadius: 3 }}
                    />
                  </Box>
                  {can("llm.jobs:cancel") && (
                    <Button
                      size="small"
                      color="warning"
                      disabled={busyJob === m.download?.job_id}
                      onClick={() => void jobAction(m, "cancel")}
                      data-testid={`download-cancel-${m.model_id}`}
                    >
                      {t("marketplace.downloads.cancel")}
                    </Button>
                  )}
                </Stack>
              </Paper>
            ))}
            {failed.map((m) => (
              <Paper key={m.model_id} variant="outlined" sx={{ p: 1.5 }} data-testid={`download-failed-${m.model_id}`}>
                <Stack direction="row" spacing={1.5} alignItems="center">
                  <Box sx={{ flexGrow: 1, minWidth: 0 }}>
                    <Typography variant="body2" fontWeight={600} noWrap>
                      {m.hf_repo_id ?? m.display_name}
                    </Typography>
                    <Typography variant="caption" color={m.download?.status === "canceled" ? "text.secondary" : "error"}>
                      {m.download?.status === "canceled"
                        ? t("marketplace.downloads.canceled")
                        : t("marketplace.downloads.failed", { error: m.download?.error ?? t("common.unknown_error") })}
                    </Typography>
                  </Box>
                  {can("llm.jobs:create") && (
                    <Button
                      size="small"
                      disabled={busyJob === m.download?.job_id}
                      onClick={() => void jobAction(m, "retry")}
                      data-testid={`download-retry-${m.model_id}`}
                    >
                      {t("marketplace.downloads.retry")}
                    </Button>
                  )}
                  {can("llm.models:delete") && (
                    <Button size="small" color="error" onClick={() => setDeleting(m)}>
                      {t("common.remove")}
                    </Button>
                  )}
                </Stack>
              </Paper>
            ))}
          </Stack>
        </Box>
      )}

      {loading && items.length === 0 ? (
        <Box sx={{ display: "flex", justifyContent: "center", p: 4 }}>
          <CircularProgress />
        </Box>
      ) : items.length === 0 ? (
        <Typography variant="body2" color="text.secondary" sx={{ py: 4, textAlign: "center" }}>
          {t("marketplace.local_empty")}
        </Typography>
      ) : (
        <Grid container spacing={2}>
          {ready.map((m) => (
            <Grid key={m.model_id} size={{ xs: 12, md: 6, xl: 4 }}>
              <KeptCard
                model={m}
                onHost={() => onHost(m)}
                onOpen={m.hf_repo_id ? () => onOpen(m.hf_repo_id as string) : undefined}
                onDelete={can("llm.models:delete") ? () => setDeleting(m) : undefined}
                onDeployment={(id) => navigate(`/admin/deployments/${id}`)}
                onRuntime={(id) => navigate(`/admin/llm/runtimes/${id}`)}
                canHost={can("inference.deployments:create")}
              />
            </Grid>
          ))}
        </Grid>
      )}

      <RegisterDialog
        open={registerOpen}
        onClose={() => setRegisterOpen(false)}
        onDone={async (name) => {
          setRegisterOpen(false);
          setNotice({ severity: "success", text: t("marketplace.kept.added", { name }) });
          await onChanged();
        }}
      />
      <DeleteDialog
        model={deleting}
        onClose={() => setDeleting(null)}
        onDone={async (text) => {
          setDeleting(null);
          setNotice({ severity: "success", text });
          await onChanged();
        }}
      />
    </Stack>
  );
}

function KeptCard({
  model,
  onHost,
  onOpen,
  onDelete,
  onDeployment,
  onRuntime,
  canHost,
}: {
  model: KeptModel;
  onHost: () => void;
  onOpen?: () => void;
  onDelete?: () => void;
  onDeployment: (id: string) => void;
  onRuntime: (id: string) => void;
  canHost: boolean;
}) {
  const { t } = useTranslation();
  const fromPath = model.source !== "huggingface";
  return (
    <Card variant="outlined" sx={{ height: "100%" }} data-testid={`kept-${model.model_id}`}>
      <CardContent>
        <Stack direction="row" spacing={1} alignItems="flex-start">
          {!fromPath && model.hf_repo_id?.includes("/") && (
            <OwnerAvatar owner={model.hf_repo_id.split("/")[0]} size={32} />
          )}
          <Box sx={{ flexGrow: 1, minWidth: 0 }}>
            <Typography variant="subtitle1" fontWeight={600} noWrap title={model.hf_repo_id ?? model.display_name}>
              {model.display_name}
            </Typography>
            <Typography variant="caption" color="text.secondary" component="div" noWrap>
              {fromPath ? (
                <>
                  <FolderIcon sx={{ fontSize: 13, verticalAlign: "text-bottom", mr: 0.5 }} />
                  {t("marketplace.kept.from_path")}
                </>
              ) : (
                [model.hf_repo_id, model.hf_revision && model.hf_revision !== "main" ? model.hf_revision : null]
                  .filter(Boolean)
                  .join(" @ ")
              )}
            </Typography>
          </Box>
          <Chip
            size="small"
            color={model.status === "available" ? "success" : "default"}
            label={model.status === "available" ? t("marketplace.kept.ready") : model.status}
          />
        </Stack>

        <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
          {model.size_bytes ? t("marketplace.kept.on_disk", { size: formatBytes(model.size_bytes) }) : t("marketplace.kept.size_unknown")}
        </Typography>

        <Box sx={{ mt: 1.5 }}>
          <Typography variant="caption" color="text.secondary" display="block" sx={{ mb: 0.5 }}>
            {t("marketplace.kept.used_by")}
          </Typography>
          {model.deployments.length === 0 && model.runtimes.length === 0 ? (
            <Typography variant="body2" color="text.secondary">
              {t("marketplace.kept.unused")}
            </Typography>
          ) : (
            <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap>
              {model.deployments.map((d) => (
                <Chip
                  key={d.id}
                  size="small"
                  variant="outlined"
                  color={d.phase === "running" ? "success" : "default"}
                  label={
                    d.desired_state === "deleted"
                      ? t("marketplace.kept.deployment_removing", { name: d.name })
                      : t("marketplace.kept.deployment_on", { name: d.name, cluster: d.cluster ?? "?" })
                  }
                  onClick={d.desired_state === "deleted" ? undefined : () => onDeployment(d.id)}
                />
              ))}
              {model.runtimes.map((r) => (
                <Chip
                  key={r.id}
                  size="small"
                  variant="outlined"
                  label={t("marketplace.kept.runtime", { name: r.name, status: r.status })}
                  onClick={() => onRuntime(r.id)}
                />
              ))}
            </Stack>
          )}
        </Box>

        <Stack direction="row" spacing={1} sx={{ mt: 2 }} justifyContent="flex-end">
          {onOpen && (
            <Button size="small" onClick={onOpen}>
              {t("common.details")}
            </Button>
          )}
          {onDelete && (
            <Button size="small" color="error" onClick={onDelete} data-testid={`kept-delete-${model.model_id}`}>
              {t("common.delete")}
            </Button>
          )}
          {canHost && model.status === "available" && (
            <Button size="small" variant="contained" onClick={onHost} data-testid={`kept-host-${model.model_id}`}>
              {t("marketplace.host")}
            </Button>
          )}
        </Stack>
      </CardContent>
    </Card>
  );
}

function RegisterDialog({
  open,
  onClose,
  onDone,
}: {
  open: boolean;
  onClose: () => void;
  onDone: (name: string) => void | Promise<void>;
}) {
  const { t } = useTranslation();
  const [name, setName] = useState("");
  const [path, setPath] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      await modelsApi.register({ display_name: name.trim(), path: path.trim() });
      setName("");
      setPath("");
      await onDone(name.trim());
    } catch (err) {
      setError(errorText(err, t("common.create_failed")));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onClose={busy ? undefined : onClose} maxWidth="sm" fullWidth>
      <DialogTitle>{t("marketplace.kept.add_path_title")}</DialogTitle>
      <DialogContent dividers>
        <Stack spacing={2}>
          <Typography variant="body2" color="text.secondary">
            {t("marketplace.kept.add_path_help")}
          </Typography>
          {error && <Alert severity="error">{error}</Alert>}
          <TextField
            label={t("marketplace.kept.path_label")}
            placeholder="/srv/models/my-finetune"
            value={path}
            onChange={(e) => setPath(e.target.value)}
            fullWidth
            slotProps={{ htmlInput: { "data-testid": "register-path", spellCheck: false } }}
          />
          <TextField
            label={t("marketplace.kept.name_label")}
            value={name}
            onChange={(e) => setName(e.target.value)}
            fullWidth
            slotProps={{ htmlInput: { "data-testid": "register-name" } }}
          />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={busy}>
          {t("common.cancel")}
        </Button>
        <Button
          variant="contained"
          disabled={busy || !name.trim() || !path.trim()}
          onClick={() => void submit()}
          data-testid="register-submit"
        >
          {t("common.add")}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

function DeleteDialog({
  model,
  onClose,
  onDone,
}: {
  model: KeptModel | null;
  onClose: () => void;
  onDone: (text: string) => void | Promise<void>;
}) {
  const { t } = useTranslation();
  const [files, setFiles] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!model) return null;

  const deployments = liveDeployments(model);
  const runtimes = runningRuntimes(model);
  const blocked = deployments.length > 0 || runtimes.length > 0;
  const ownFiles = model.source === "huggingface";

  async function remove() {
    if (!model) return;
    setBusy(true);
    setError(null);
    try {
      await modelsApi.delete(model.model_id, { files: ownFiles && files });
      await onDone(
        ownFiles && files
          ? t("marketplace.kept.deleted_with_files", { name: model.display_name })
          : t("marketplace.kept.deleted", { name: model.display_name }),
      );
    } catch (err) {
      setError(errorText(err, t("common.delete_failed")));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open onClose={busy ? undefined : onClose} maxWidth="sm" fullWidth>
      <DialogTitle>{t("marketplace.kept.delete_title", { name: model.display_name })}</DialogTitle>
      <DialogContent dividers>
        <Stack spacing={2}>
          {error && <Alert severity="error">{error}</Alert>}
          {blocked ? (
            <Alert severity="warning" data-testid="delete-blocked">
              {t("marketplace.kept.delete_in_use", {
                names: [...deployments.map((d) => d.name), ...runtimes.map((r) => r.name)].join(", "),
              })}
            </Alert>
          ) : (
            <>
              <Typography variant="body2">{t("marketplace.kept.delete_confirm")}</Typography>
              {ownFiles ? (
                <FormControlLabel
                  control={
                    <Checkbox
                      checked={files}
                      onChange={(e) => setFiles(e.target.checked)}
                      inputProps={{ "data-testid": "delete-files" } as React.InputHTMLAttributes<HTMLInputElement>}
                    />
                  }
                  label={
                    model.size_bytes
                      ? t("marketplace.kept.delete_files_size", { size: formatBytes(model.size_bytes) })
                      : t("marketplace.kept.delete_files")
                  }
                />
              ) : (
                <Typography variant="caption" color="text.secondary">
                  {t("marketplace.kept.delete_path_kept")}
                </Typography>
              )}
            </>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={busy}>
          {t("common.cancel")}
        </Button>
        {!blocked && (
          <Button color="error" variant="contained" disabled={busy} onClick={() => void remove()} data-testid="delete-confirm">
            {t("common.delete")}
          </Button>
        )}
      </DialogActions>
    </Dialog>
  );
}
