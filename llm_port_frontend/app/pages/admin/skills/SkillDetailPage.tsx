import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams, useNavigate } from "react-router";
import { useTranslation } from "react-i18next";
import {
  getSkill,
  updateSkill,
  publishSkill,
  archiveSkill,
  deleteSkill,
  listVersions,
  listAssignments,
  createAssignment,
  deleteAssignment,
  exportSkill,
  type SkillDetail,
  type SkillVersion,
  type SkillAssignment,
  type AssignmentTargetType,
  type UpdateSkillPayload,
  type SkillExport,
} from "~/api/skills";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import Divider from "@mui/material/Divider";
import IconButton from "@mui/material/IconButton";
import MenuItem from "@mui/material/MenuItem";
import Stack from "@mui/material/Stack";
import Tab from "@mui/material/Tab";
import Tabs from "@mui/material/Tabs";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import Tooltip from "@mui/material/Tooltip";
import CircularProgress from "@mui/material/CircularProgress";
import List from "@mui/material/List";
import ListItem from "@mui/material/ListItem";
import ListItemText from "@mui/material/ListItemText";

import ArrowBackIcon from "@mui/icons-material/ArrowBack";
import SaveIcon from "@mui/icons-material/Save";
import PublishIcon from "@mui/icons-material/Publish";
import ArchiveIcon from "@mui/icons-material/Archive";
import DeleteIcon from "@mui/icons-material/Delete";
import DownloadIcon from "@mui/icons-material/Download";
import HistoryIcon from "@mui/icons-material/History";
import AssignmentIcon from "@mui/icons-material/Assignment";

import { ConfirmDialog } from "~/components/ConfirmDialog";
import { FormDialog } from "~/components/FormDialog";

import { BasicsFields, ContentFields, type SkillFormValues } from "./SkillFormFields";

// ── Tab panel helper ────────────────────────────────────────────────────────

function TabPanel({
  children,
  value,
  index,
}: {
  children: React.ReactNode;
  value: number;
  index: number;
}) {
  return value === index ? <Box sx={{ py: 2 }}>{children}</Box> : null;
}

// ── Main page ───────────────────────────────────────────────────────────────

export default function SkillDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { t } = useTranslation();
  const navigate = useNavigate();

  const [skill, setSkill] = useState<SkillDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [tab, setTab] = useState(0);

  // Form values — shared component
  const [form, setForm] = useState<SkillFormValues>({
    name: "",
    description: "",
    scope: "global",
    priority: 50,
    tags: "",
    bodyMarkdown: "",
    allowedTools: "",
    preferredTools: "",
    forbiddenTools: "",
    knowledgeSources: "",
    changeNote: "",
  });

  // Versions & assignments
  const [versions, setVersions] = useState<SkillVersion[]>([]);
  const [assignments, setAssignments] = useState<SkillAssignment[]>([]);
  const [versionsLoading, setVersionsLoading] = useState(false);
  const [assignmentsLoading, setAssignmentsLoading] = useState(false);

  // Dialogs
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [assignOpen, setAssignOpen] = useState(false);
  const [assignTargetType, setAssignTargetType] =
    useState<AssignmentTargetType>("tenant");
  const [assignTargetId, setAssignTargetId] = useState("");
  const [assigning, setAssigning] = useState(false);

  const onChange = useCallback(
    <K extends keyof SkillFormValues>(key: K, val: SkillFormValues[K]) => {
      setForm((prev) => ({ ...prev, [key]: val }));
    },
    [],
  );

  const dirty = useMemo(() => {
    if (!skill) return false;
    return (
      form.name !== skill.name ||
      form.description !== (skill.description || "") ||
      form.bodyMarkdown !== (skill.body_markdown || "") ||
      form.scope !== skill.scope ||
      form.priority !== skill.priority ||
      form.tags !== (skill.tags ?? []).join(", ") ||
      form.allowedTools !== (skill.allowed_tools ?? []).join(", ") ||
      form.forbiddenTools !== (skill.forbidden_tools ?? []).join(", ") ||
      form.preferredTools !== (skill.preferred_tools ?? []).join(", ") ||
      form.knowledgeSources !== (skill.knowledge_sources ?? []).join(", ")
    );
  }, [skill, form]);

  const populateForm = useCallback((s: SkillDetail) => {
    setForm({
      name: s.name,
      description: s.description || "",
      bodyMarkdown: s.body_markdown || "",
      scope: s.scope,
      priority: s.priority,
      tags: (s.tags ?? []).join(", "),
      allowedTools: (s.allowed_tools ?? []).join(", "),
      forbiddenTools: (s.forbidden_tools ?? []).join(", "),
      preferredTools: (s.preferred_tools ?? []).join(", "),
      knowledgeSources: (s.knowledge_sources ?? []).join(", "),
      changeNote: "",
    });
  }, []);

  useEffect(() => {
    if (!id) return;
    setLoading(true);
    getSkill(id)
      .then((s) => {
        setSkill(s);
        populateForm(s);
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [id, populateForm]);

  async function loadVersions() {
    if (!id) return;
    setVersionsLoading(true);
    try {
      setVersions(await listVersions(id));
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load versions");
    } finally {
      setVersionsLoading(false);
    }
  }

  async function loadAssignments() {
    if (!id) return;
    setAssignmentsLoading(true);
    try {
      setAssignments(await listAssignments(id));
    } catch (err: unknown) {
      setError(
        err instanceof Error ? err.message : "Failed to load assignments",
      );
    } finally {
      setAssignmentsLoading(false);
    }
  }

  function splitCsv(s: string): string[] {
    return s
      .split(",")
      .map((v) => v.trim())
      .filter(Boolean);
  }

  async function handleSave() {
    if (!id) return;
    setSaving(true);
    setError(null);
    try {
      const payload: UpdateSkillPayload = {
        name: form.name,
        description: form.description || undefined,
        body_markdown: form.bodyMarkdown,
        scope: form.scope,
        priority: form.priority,
        tags: splitCsv(form.tags),
        allowed_tools: splitCsv(form.allowedTools),
        forbidden_tools: splitCsv(form.forbiddenTools),
        preferred_tools: splitCsv(form.preferredTools),
        knowledge_sources: splitCsv(form.knowledgeSources),
        change_note: form.changeNote || undefined,
      };
      const updated = await updateSkill(id, payload);
      setSkill(updated);
      populateForm(updated);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  async function handlePublish() {
    if (!id) return;
    try {
      const s = await publishSkill(id);
      setSkill(s);
      populateForm(s);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Publish failed");
    }
  }

  async function handleArchive() {
    if (!id) return;
    try {
      const s = await archiveSkill(id);
      setSkill(s);
      populateForm(s);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Archive failed");
    }
  }

  async function handleDelete() {
    if (!id) return;
    setDeleting(true);
    try {
      await deleteSkill(id);
      navigate("/admin/skills");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Delete failed");
    } finally {
      setDeleting(false);
    }
  }

  async function handleExport() {
    if (!id) return;
    try {
      const data: SkillExport = await exportSkill(id);
      const blob = new Blob([JSON.stringify(data, null, 2)], {
        type: "application/json",
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${skill?.slug || "skill"}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Export failed");
    }
  }

  async function handleCreateAssignment() {
    if (!id) return;
    setAssigning(true);
    try {
      await createAssignment(id, {
        target_type: assignTargetType,
        target_id: assignTargetId,
      });
      setAssignOpen(false);
      setAssignTargetId("");
      await loadAssignments();
    } catch (err: unknown) {
      setError(
        err instanceof Error ? err.message : "Failed to create assignment",
      );
    } finally {
      setAssigning(false);
    }
  }

  async function handleDeleteAssignment(assignmentId: string) {
    if (!id) return;
    try {
      await deleteAssignment(id, assignmentId);
      await loadAssignments();
    } catch (err: unknown) {
      setError(
        err instanceof Error ? err.message : "Failed to delete assignment",
      );
    }
  }

  if (loading) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", py: 8 }}>
        <CircularProgress />
      </Box>
    );
  }

  if (!skill) {
    return (
      <Alert severity="error">
        {error || t("skills.not_found", "Skill not found")}
      </Alert>
    );
  }

  return (
    <Box>
      {/* Header */}
      <Stack direction="row" alignItems="center" spacing={2} sx={{ mb: 2 }}>
        <IconButton onClick={() => navigate("/admin/skills")}>
          <ArrowBackIcon />
        </IconButton>
        <Typography variant="h5" sx={{ flexGrow: 1 }}>
          {skill.name}
        </Typography>
        <Chip
          label={skill.status}
          color={
            skill.status === "published"
              ? "success"
              : skill.status === "draft"
                ? "info"
                : "default"
          }
          size="small"
        />
        <Chip
          label={`v${skill.current_version}`}
          size="small"
          variant="outlined"
        />
      </Stack>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}

      {/* Action buttons */}
      <Stack direction="row" spacing={1} sx={{ mb: 2 }}>
        <Button
          variant="contained"
          startIcon={<SaveIcon />}
          onClick={handleSave}
          disabled={saving || !dirty}
          size="small"
        >
          {saving ? t("common.saving", "Saving…") : t("common.save", "Save")}
        </Button>
        {skill.status === "draft" && (
          <Button
            variant="outlined"
            startIcon={<PublishIcon />}
            onClick={handlePublish}
            size="small"
          >
            {t("skills.publish", "Publish")}
          </Button>
        )}
        {skill.status === "published" && (
          <Button
            variant="outlined"
            startIcon={<ArchiveIcon />}
            onClick={handleArchive}
            size="small"
          >
            {t("skills.archive", "Archive")}
          </Button>
        )}
        <Tooltip title={t("skills.export", "Export")}>
          <IconButton onClick={handleExport} size="small">
            <DownloadIcon />
          </IconButton>
        </Tooltip>
        <Box sx={{ flexGrow: 1 }} />
        <IconButton
          color="error"
          size="small"
          onClick={() => setDeleteOpen(true)}
        >
          <DeleteIcon />
        </IconButton>
      </Stack>

      <Divider sx={{ mb: 2 }} />

      {/* Tabs */}
      <Tabs
        value={tab}
        onChange={(_, v) => {
          setTab(v);
          if (v === 2 && versions.length === 0) loadVersions();
          if (v === 3 && assignments.length === 0) loadAssignments();
        }}
      >
        <Tab label={t("skills.tab_editor", "Editor")} />
        <Tab label={t("skills.tab_metadata", "Metadata")} />
        <Tab
          label={t("skills.tab_versions", "Versions")}
          icon={<HistoryIcon />}
          iconPosition="start"
        />
        <Tab
          label={t("skills.tab_assignments", "Assignments")}
          icon={<AssignmentIcon />}
          iconPosition="start"
        />
      </Tabs>

      {/* ── Editor Tab ── */}
      <TabPanel value={tab} index={0}>
        <ContentFields
          values={form}
          onChange={onChange}
          disabled={saving}
          showChangeNote
        />
      </TabPanel>

      {/* ── Metadata Tab ── */}
      <TabPanel value={tab} index={1}>
        <Box sx={{ maxWidth: 600 }}>
          <BasicsFields values={form} onChange={onChange} disabled={saving} />
        </Box>
      </TabPanel>

      {/* ── Versions Tab ── */}
      <TabPanel value={tab} index={2}>
        {versionsLoading ? (
          <CircularProgress size={24} />
        ) : versions.length === 0 ? (
          <Typography color="text.secondary">
            {t("skills.no_versions", "No versions yet.")}
          </Typography>
        ) : (
          <List>
            {versions.map((v) => (
              <ListItem key={v.id} divider>
                <ListItemText
                  primary={`v${v.version}${v.change_note ? ` — ${v.change_note}` : ""}`}
                  secondary={new Date(v.created_at).toLocaleString()}
                />
              </ListItem>
            ))}
          </List>
        )}
      </TabPanel>

      {/* ── Assignments Tab ── */}
      <TabPanel value={tab} index={3}>
        <Button
          variant="outlined"
          size="small"
          onClick={() => setAssignOpen(true)}
          sx={{ mb: 2 }}
        >
          {t("skills.add_assignment", "Add Assignment")}
        </Button>
        {assignmentsLoading ? (
          <CircularProgress size={24} />
        ) : assignments.length === 0 ? (
          <Typography color="text.secondary">
            {t("skills.no_assignments", "No assignments.")}
          </Typography>
        ) : (
          <List>
            {assignments.map((a) => (
              <ListItem
                key={a.id}
                divider
                secondaryAction={
                  <IconButton
                    edge="end"
                    color="error"
                    onClick={() => handleDeleteAssignment(a.id)}
                    size="small"
                  >
                    <DeleteIcon fontSize="small" />
                  </IconButton>
                }
              >
                <ListItemText
                  primary={`${a.target_type}: ${a.target_id}`}
                  secondary={
                    a.priority_override != null
                      ? `Priority override: ${a.priority_override}`
                      : undefined
                  }
                />
              </ListItem>
            ))}
          </List>
        )}
      </TabPanel>

      {/* ── Delete dialog ── */}
      <ConfirmDialog
        open={deleteOpen}
        title={t("skills.delete_title", "Delete Skill")}
        message={t("skills.delete_confirm", {
          name: skill.name,
          defaultValue: `Delete "${skill.name}"? This cannot be undone.`,
        })}
        loading={deleting}
        confirmLabel={t("common.delete", "Delete")}
        confirmColor="error"
        onConfirm={handleDelete}
        onClose={() => setDeleteOpen(false)}
      />

      {/* ── Assignment dialog ── */}
      <FormDialog
        open={assignOpen}
        title={t("skills.add_assignment", "Add Assignment")}
        loading={assigning}
        submitLabel={t("skills.assign", "Assign")}
        cancelLabel={t("common.cancel", "Cancel")}
        submitDisabled={!assignTargetId.trim()}
        onSubmit={handleCreateAssignment}
        onClose={() => setAssignOpen(false)}
      >
        <Stack spacing={2} sx={{ mt: 1 }}>
          <TextField
            label={t("skills.target_type", "Target Type")}
            select
            value={assignTargetType}
            onChange={(e) =>
              setAssignTargetType(e.target.value as AssignmentTargetType)
            }
            fullWidth
          >
            <MenuItem value="tenant">Tenant</MenuItem>
            <MenuItem value="global">Global</MenuItem>
            <MenuItem value="workspace">Workspace</MenuItem>
            <MenuItem value="project">Project</MenuItem>
            <MenuItem value="assistant">Assistant</MenuItem>
          </TextField>
          <TextField
            label={t("skills.target_id", "Target ID")}
            value={assignTargetId}
            onChange={(e) => setAssignTargetId(e.target.value)}
            fullWidth
            required
          />
        </Stack>
      </FormDialog>
    </Box>
  );
}
