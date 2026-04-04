import { useState } from "react";
import { useTranslation } from "react-i18next";
import { chatAdmin, type ChatSession } from "~/api/chat";
import { DataTable, type ColumnDef } from "~/components/DataTable";
import { ConfirmDialog } from "~/components/ConfirmDialog";
import { useAsyncData } from "~/lib/useAsyncData";

import Chip from "@mui/material/Chip";
import IconButton from "@mui/material/IconButton";
import Tooltip from "@mui/material/Tooltip";
import DeleteIcon from "@mui/icons-material/Delete";

export default function ChatSessionsPage() {
  const { t } = useTranslation();

  const {
    data: sessions,
    loading,
    error,
    refresh: load,
    setError,
  } = useAsyncData(() => chatAdmin.listSessions(), [], {
    initialValue: [] as ChatSession[],
  });

  const [deleteTarget, setDeleteTarget] = useState<ChatSession | null>(null);
  const [deleting, setDeleting] = useState(false);

  async function handleDelete() {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await chatAdmin.deleteSession(deleteTarget.id);
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

  const statusColor = (s: string) => {
    if (s === "active") return "success";
    if (s === "archived") return "default";
    return "warning";
  };

  const statusLabel = (status: string) =>
    t(`chat_admin.status_${status}`, { defaultValue: status });

  const columns: ColumnDef<ChatSession>[] = [
    {
      key: "title",
      label: t("chat_admin.col_title"),
      sortable: true,
      sortValue: (r) => r.title ?? "",
      searchValue: (r) => r.title ?? "",
      render: (r) => r.title || t("chat_admin.untitled_session"),
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
      key: "status",
      label: t("chat_admin.col_status"),
      sortable: true,
      sortValue: (r) => r.status,
      render: (r) => (
        <Chip
          label={statusLabel(r.status)}
          color={statusColor(r.status) as "success" | "default" | "warning"}
          size="small"
          variant="outlined"
        />
      ),
    },
    {
      key: "project_id",
      label: t("chat_admin.col_project"),
      searchValue: (r) => r.project_id ?? "",
      render: (r) => (r.project_id ? r.project_id.slice(0, 8) + "…" : "—"),
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
        rows={sessions}
        rowKey={(r) => r.id}
        loading={loading}
        error={error}
        onRefresh={load}
        searchPlaceholder={t("chat_admin.search_sessions")}
        pagination
      />

      <ConfirmDialog
        open={!!deleteTarget}
        title={t("chat_admin.delete_session_title")}
        message={t("chat_admin.delete_session_message")}
        confirmLabel={t("common.delete")}
        confirmColor="error"
        loading={deleting}
        onConfirm={handleDelete}
        onClose={() => setDeleteTarget(null)}
      />
    </>
  );
}
