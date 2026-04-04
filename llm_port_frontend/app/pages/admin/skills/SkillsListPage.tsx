import { useState } from "react";
import { useNavigate } from "react-router";
import { useTranslation } from "react-i18next";
import { DataTable, type ColumnDef } from "~/components/DataTable";
import { ConfirmDialog } from "~/components/ConfirmDialog";
import { useAsyncData } from "~/lib/useAsyncData";
import {
  listSkills,
  deleteSkill,
  publishSkill,
  archiveSkill,
  type SkillSummary,
} from "~/api/skills";

import Alert from "@mui/material/Alert";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import IconButton from "@mui/material/IconButton";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";

import AddIcon from "@mui/icons-material/Add";
import DeleteIcon from "@mui/icons-material/Delete";
import OpenInNewIcon from "@mui/icons-material/OpenInNew";
import PublishIcon from "@mui/icons-material/Publish";
import ArchiveIcon from "@mui/icons-material/Archive";

const STATUS_COLOR: Record<string, "success" | "warning" | "default" | "info"> =
  {
    draft: "info",
    published: "success",
    archived: "default",
  };

const SCOPE_COLOR: Record<
  string,
  "primary" | "secondary" | "default" | "info"
> = {
  global: "primary",
  tenant: "secondary",
  workspace: "info",
  assistant: "info",
  user: "default",
};

export default function SkillsListPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();

  const {
    data: skills,
    loading,
    error,
    refresh: load,
    setError,
  } = useAsyncData(() => listSkills(), [], {
    initialValue: [] as SkillSummary[],
  });

  // Delete dialog
  const [deleteTarget, setDeleteTarget] = useState<SkillSummary | null>(null);
  const [deleting, setDeleting] = useState(false);

  async function confirmDelete() {
    if (!deleteTarget) return;
    setDeleting(true);
    setError(null);
    try {
      await deleteSkill(deleteTarget.id);
      setDeleteTarget(null);
      await load();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Delete failed");
    } finally {
      setDeleting(false);
    }
  }

  async function handlePublish(id: string) {
    try {
      await publishSkill(id);
      await load();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Publish failed");
    }
  }

  async function handleArchive(id: string) {
    try {
      await archiveSkill(id);
      await load();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Archive failed");
    }
  }

  const columns: ColumnDef<SkillSummary>[] = [
    {
      key: "name",
      label: t("skills.name", "Name"),
      sortable: true,
      sortValue: (s) => s.name,
      searchValue: (s) => `${s.name} ${s.slug}`,
      render: (s) => (
        <>
          <Typography fontWeight={600}>{s.name}</Typography>
          <Typography
            variant="caption"
            color="text.secondary"
            fontFamily="monospace"
          >
            {s.slug}
          </Typography>
        </>
      ),
      minWidth: 200,
    },
    {
      key: "scope",
      label: t("skills.scope", "Scope"),
      sortable: true,
      sortValue: (s) => s.scope,
      render: (s) => (
        <Chip
          size="small"
          label={s.scope}
          color={SCOPE_COLOR[s.scope] ?? "default"}
          variant="outlined"
        />
      ),
      minWidth: 100,
    },
    {
      key: "status",
      label: t("skills.status", "Status"),
      sortable: true,
      sortValue: (s) => s.status,
      render: (s) => (
        <Chip
          size="small"
          label={s.status}
          color={STATUS_COLOR[s.status] ?? "default"}
        />
      ),
      minWidth: 100,
    },
    {
      key: "version",
      label: t("skills.version", "Version"),
      sortable: true,
      sortValue: (s) => s.current_version,
      render: (s) => <Typography>v{s.current_version}</Typography>,
      minWidth: 80,
    },
    {
      key: "priority",
      label: t("skills.priority", "Priority"),
      sortable: true,
      sortValue: (s) => s.priority,
      render: (s) => <Typography>{s.priority}</Typography>,
      minWidth: 80,
    },
    {
      key: "tags",
      label: t("skills.tags", "Tags"),
      render: (s) => (
        <Stack direction="row" spacing={0.5} flexWrap="wrap">
          {s.tags.slice(0, 3).map((tag) => (
            <Chip key={tag} size="small" label={tag} variant="outlined" />
          ))}
          {s.tags.length > 3 && (
            <Chip
              size="small"
              label={`+${s.tags.length - 3}`}
              variant="outlined"
            />
          )}
        </Stack>
      ),
      minWidth: 160,
    },
    {
      key: "actions",
      label: t("common.actions", "Actions"),
      align: "right",
      render: (s) => (
        <Stack direction="row" spacing={0.5} justifyContent="flex-end">
          <IconButton
            size="small"
            title={t("skills.view_details", "View details")}
            onClick={() => navigate(`/admin/skills/${s.id}`)}
          >
            <OpenInNewIcon fontSize="small" />
          </IconButton>
          {s.status === "draft" && (
            <IconButton
              size="small"
              color="primary"
              title={t("skills.publish", "Publish")}
              onClick={() => handlePublish(s.id)}
            >
              <PublishIcon fontSize="small" />
            </IconButton>
          )}
          {s.status === "published" && (
            <IconButton
              size="small"
              title={t("skills.archive", "Archive")}
              onClick={() => handleArchive(s.id)}
            >
              <ArchiveIcon fontSize="small" />
            </IconButton>
          )}
          <IconButton
            size="small"
            color="error"
            title={t("common.delete", "Delete")}
            onClick={() => setDeleteTarget(s)}
          >
            <DeleteIcon fontSize="small" />
          </IconButton>
        </Stack>
      ),
      minWidth: 180,
    },
  ];

  return (
    <>
      {error && (
        <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}

      <DataTable
        title={t("skills.list_title", "Skills")}
        rows={skills}
        columns={columns}
        rowKey={(s) => s.id}
        loading={loading}
        onRefresh={load}
        emptyMessage={t("skills.no_skills", "No skills created yet.")}
        searchPlaceholder={t("skills.search", "Search skills…")}
        toolbarActions={
          <Button
            variant="contained"
            startIcon={<AddIcon />}
            onClick={() => navigate("/admin/skills/create")}
            size="small"
          >
            {t("skills.create", "Create Skill")}
          </Button>
        }
      />

      {/* ── Delete confirmation ── */}
      <ConfirmDialog
        open={!!deleteTarget}
        title={t("skills.delete_title", "Delete Skill")}
        message={t("skills.delete_confirm", {
          name: deleteTarget?.name,
          defaultValue: `Delete skill "${deleteTarget?.name}"? This cannot be undone.`,
        })}
        loading={deleting}
        confirmLabel={t("common.delete", "Delete")}
        confirmColor="error"
        onConfirm={confirmDelete}
        onClose={() => setDeleteTarget(null)}
      />
    </>
  );
}
