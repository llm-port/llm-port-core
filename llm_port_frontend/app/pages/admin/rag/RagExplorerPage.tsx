import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  ragContainers,
  ragDrafts,
  ragUploads,
  type RagContainer,
  type RagDraft,
} from "~/api/rag";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import Divider from "@mui/material/Divider";
import FormControl from "@mui/material/FormControl";
import InputLabel from "@mui/material/InputLabel";
import MenuItem from "@mui/material/MenuItem";
import Select from "@mui/material/Select";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

async function sha256Hex(file: File): Promise<string> {
  const bytes = await file.arrayBuffer();
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest))
    .map((x) => x.toString(16).padStart(2, "0"))
    .join("");
}

function contentTypeOf(file: File): string {
  if (file.type) return file.type;
  return "application/octet-stream";
}

export default function RagExplorerPage() {
  const { t } = useTranslation();
  const [tenantId, setTenantId] = useState("tenant_default");
  const [workspaceId, setWorkspaceId] = useState("");
  const [containers, setContainers] = useState<RagContainer[]>([]);
  const [selectedContainer, setSelectedContainer] = useState<string>("");
  const [draft, setDraft] = useState<RagDraft | null>(null);
  const [newContainerName, setNewContainerName] = useState("");
  const [scheduleAt, setScheduleAt] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const selectedContainerNode = useMemo(
    () => containers.find((item) => item.id === selectedContainer) ?? null,
    [containers, selectedContainer],
  );

  async function loadContainers() {
    const payload = await ragContainers.tree(tenantId, workspaceId || undefined);
    setContainers(payload.containers);
    if (!selectedContainer && payload.containers.length > 0) {
      setSelectedContainer(payload.containers[0].id);
    }
  }

  async function ensureDraft(containerId: string): Promise<RagDraft> {
    if (draft && draft.container_id === containerId && draft.status !== "published") {
      return draft;
    }
    const created = await ragDrafts.create({
      tenant_id: tenantId,
      workspace_id: workspaceId || null,
      container_id: containerId,
      created_by: null,
    });
    setDraft(created);
    return created;
  }

  async function refreshDraft(id: string) {
    const refreshed = await ragDrafts.get(id);
    setDraft(refreshed);
  }

  async function handleCreateContainer() {
    if (!newContainerName.trim()) return;
    setBusy(true);
    setError(null);
    setSuccess(null);
    try {
      const created = await ragContainers.create({
        tenant_id: tenantId,
        workspace_id: workspaceId || null,
        parent_id: selectedContainer || null,
        name: newContainerName.trim(),
        sort_order: 0,
        acl_principals: [`tenant:${tenantId}`],
      });
      setNewContainerName("");
      await loadContainers();
      setSelectedContainer(created.id);
      setSuccess("Container created.");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to create container.");
    } finally {
      setBusy(false);
    }
  }

  async function handleUpload(event: React.ChangeEvent<HTMLInputElement>) {
    const files = event.target.files;
    if (!files || files.length === 0 || !selectedContainer) return;
    setBusy(true);
    setError(null);
    setSuccess(null);
    try {
      const activeDraft = await ensureDraft(selectedContainer);
      for (const file of Array.from(files)) {
        const sha = await sha256Hex(file);
        const contentType = contentTypeOf(file);
        const presign = await ragUploads.presign({
          tenant_id: tenantId,
          workspace_id: workspaceId || null,
          container_id: selectedContainer,
          filename: file.name,
          size_bytes: file.size,
          content_type: contentType,
          sha256: sha,
        });
        const putRes = await fetch(presign.upload_url, {
          method: "PUT",
          headers: {
            ...presign.required_headers,
            "Content-Type": contentType,
          },
          body: file,
        });
        if (!putRes.ok) {
          throw new Error(`Upload failed for ${file.name}: ${putRes.status}`);
        }
        await ragUploads.complete({
          object_key: presign.object_key,
          tenant_id: tenantId,
          workspace_id: workspaceId || null,
          container_id: selectedContainer,
          filename: file.name,
          size_bytes: file.size,
          content_type: contentType,
          sha256: sha,
          draft_id: activeDraft.id,
          tags: [],
          acl_principals: [`tenant:${tenantId}`, ...(workspaceId ? [`workspace:${workspaceId}`] : [])],
          created_by: null,
        });
      }
      await refreshDraft(activeDraft.id);
      setSuccess("Files uploaded to draft.");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Upload failed.");
    } finally {
      setBusy(false);
      event.target.value = "";
    }
  }

  async function handlePublish(now: boolean) {
    if (!draft) return;
    setBusy(true);
    setError(null);
    setSuccess(null);
    try {
      const response = await ragDrafts.publish(draft.id, {
        scheduled_for: now ? null : (scheduleAt ? new Date(scheduleAt).toISOString() : null),
        triggered_by: null,
      });
      setSuccess(`Publish ${response.status}.`);
      await refreshDraft(draft.id);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Publish failed.");
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    void loadContainers();
  }, [tenantId, workspaceId]);

  useEffect(() => {
    if (!selectedContainer) return;
    void ensureDraft(selectedContainer);
  }, [selectedContainer]);

  return (
    <Stack spacing={2}>
      <Typography variant="h5">{t("rag_explorer.title")}</Typography>
      {error && <Alert severity="error">{error}</Alert>}
      {success && <Alert severity="success">{success}</Alert>}

      <Card variant="outlined">
        <CardContent>
          <Stack direction={{ xs: "column", md: "row" }} spacing={2}>
            <TextField label={t("rag_search.tenant_id")} value={tenantId} onChange={(e) => setTenantId(e.target.value)} />
            <TextField
              label={t("rag_search.workspace_id")}
              value={workspaceId}
              onChange={(e) => setWorkspaceId(e.target.value)}
            />
            <FormControl fullWidth>
              <InputLabel>{t("rag_explorer.container")}</InputLabel>
              <Select
                value={selectedContainer}
                label={t("rag_explorer.container")}
                onChange={(e) => setSelectedContainer(e.target.value)}
              >
                {containers.map((container) => (
                  <MenuItem key={container.id} value={container.id}>
                    {container.path}
                  </MenuItem>
                ))}
              </Select>
            </FormControl>
          </Stack>
        </CardContent>
      </Card>

      <Card variant="outlined">
        <CardContent>
          <Stack direction={{ xs: "column", md: "row" }} spacing={2} alignItems={{ md: "center" }}>
            <TextField
              label={t("rag_explorer.new_container")}
              value={newContainerName}
              onChange={(e) => setNewContainerName(e.target.value)}
              fullWidth
            />
            <Button variant="outlined" onClick={() => void handleCreateContainer()} disabled={busy}>
              {t("common.create")}
            </Button>
          </Stack>
        </CardContent>
      </Card>

      <Card variant="outlined">
        <CardContent>
          <Stack spacing={2}>
            <Typography variant="subtitle1">{t("rag_explorer.uploads")}</Typography>
            <Button component="label" variant="contained" disabled={busy || !selectedContainerNode}>
              {t("rag_explorer.upload_files")}
              <input hidden multiple type="file" onChange={(e) => void handleUpload(e)} />
            </Button>
            <Divider />
            <Stack direction={{ xs: "column", md: "row" }} spacing={2} alignItems={{ md: "center" }}>
              <Button variant="contained" onClick={() => void handlePublish(true)} disabled={busy || !draft}>
                {t("rag_explorer.publish_now")}
              </Button>
              <TextField
                label={t("rag_explorer.schedule_for")}
                type="datetime-local"
                value={scheduleAt}
                onChange={(e) => setScheduleAt(e.target.value)}
                InputLabelProps={{ shrink: true }}
              />
              <Button
                variant="outlined"
                onClick={() => void handlePublish(false)}
                disabled={busy || !draft || !scheduleAt}
              >
                {t("rag_explorer.schedule_publish")}
              </Button>
            </Stack>
          </Stack>
        </CardContent>
      </Card>

      <Card variant="outlined">
        <CardContent>
          <Typography variant="subtitle1">{t("rag_explorer.draft_ops")}</Typography>
          {!draft && <Typography color="text.secondary">{t("rag_explorer.no_draft")}</Typography>}
          {draft && draft.operations.length === 0 && (
            <Typography color="text.secondary">{t("rag_explorer.no_ops")}</Typography>
          )}
          {draft?.operations.map((op) => (
            <Box key={op.id} sx={{ py: 0.5 }}>
              <Typography variant="body2">
                #{op.id} {op.op_type} - {op.status}
              </Typography>
            </Box>
          ))}
        </CardContent>
      </Card>
    </Stack>
  );
}
