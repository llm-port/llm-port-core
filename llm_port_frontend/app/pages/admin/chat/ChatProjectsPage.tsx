import { useState } from "react";
import { useTranslation } from "react-i18next";
import { chatAdmin, type ChatProject } from "~/api/chat";
import { DataTable, type ColumnDef } from "~/components/DataTable";
import { ConfirmDialog } from "~/components/ConfirmDialog";
import { useAsyncData } from "~/lib/useAsyncData";

import IconButton from "@mui/material/IconButton";
import Tooltip from "@mui/material/Tooltip";
import DeleteIcon from "@mui/icons-material/Delete";

export default function ChatProjectsPage() {
  const { t } = useTranslation();

  const {
    data: projects,
    loading,
    error,
    refresh: load,
    setError,
  } = useAsyncData(() => chatAdmin.listProjects(), [], {
    initialValue: [] as ChatProject[],
  });

  const [deleteTarget, setDeleteTarget] = useState<ChatProject | null>(null);
  const [deleting, setDeleting] = useState(false);

  async function handleDelete() {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await chatAdmin.deleteProject(deleteTarget.id);
      setDeleteTarget(null);
      await load();
    } catch (err: unknown) {
      setError(
        err instanceof Error ? err.message : t("common.error_unexpected"),
      );
    } finally {
      setDeleting(false);
    }
  }

  const columns: ColumnDef<ChatProject>[] = [
    {
      key: "name",
      label: t("chat_admin.col_name"),
      sortable: true,
      sortValue: (r) => r.name,
      searchValue: (r) => r.name,
      render: (r) => r.name,
    },
    {
      key: "user_id",
      label: t("chat_admin.col_user"),
      sortable: true,
      sortValue: (r) => r.user_id,
      searchValue: (r) => r.user_id,
      render: (r) => r.user_id,
    },
    {
      key: "tenant_id",
      label: t("chat_admin.col_tenant"),
      sortable: true,
      sortValue: (r) => r.tenant_id,
      searchValue: (r) => r.tenant_id,
      render: (r) => r.tenant_id,
    },
    {
      key: "model_alias",
      label: t("chat_admin.col_model"),
      render: (r) => r.model_alias ?? "—",
    },
    {
      key: "created_at",
      label: t("chat_admin.col_created"),
      sortable: true,
      sortValue: (r) => r.created_at,
      render: (r) => new Date(r.created_at).toLocaleString(),
    },
    {
      key: "actions",
      label: "",
      align: "right",
      render: (r) => (
        <Tooltip title={t("common.delete")}>
          <IconButton size="small" onClick={() => setDeleteTarget(r)}>
            <DeleteIcon fontSize="small" />
          </IconButton>
        </Tooltip>
      ),
    },
  ];

  return (
    <>
      <DataTable
        columns={columns}
        rows={projects}
        rowKey={(r) => r.id}
        loading={loading}
        error={error}
        onRefresh={load}
        searchPlaceholder={t("chat_admin.search_projects")}
        pagination
      />

      <ConfirmDialog
        open={!!deleteTarget}
        title={t("chat_admin.delete_project_title")}
        message={t("chat_admin.delete_project_message", {
          name: deleteTarget?.name,
        })}
        confirmLabel={t("common.delete")}
        confirmColor="error"
        loading={deleting}
        onConfirm={handleDelete}
        onClose={() => setDeleteTarget(null)}
      />
    </>
  );
}
