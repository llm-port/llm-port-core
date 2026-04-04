import { useState } from "react";
import { useTranslation } from "react-i18next";
import { chatAdmin, type ChatAttachment, type ChatStats } from "~/api/chat";
import { DataTable, type ColumnDef } from "~/components/DataTable";
import { ConfirmDialog } from "~/components/ConfirmDialog";
import { useAsyncData } from "~/lib/useAsyncData";

import Box from "@mui/material/Box";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import Chip from "@mui/material/Chip";
import IconButton from "@mui/material/IconButton";
import Stack from "@mui/material/Stack";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import DeleteIcon from "@mui/icons-material/Delete";

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${(bytes / Math.pow(k, i)).toFixed(1)} ${sizes[i]}`;
}

export default function ChatAttachmentsPage() {
  const { t } = useTranslation();

  const {
    data: { attachments, stats },
    loading,
    error,
    refresh: load,
    setError,
  } = useAsyncData(
    async () => {
      const [attachments, stats] = await Promise.all([
        chatAdmin.listAttachments(),
        chatAdmin.stats(),
      ]);
      return { attachments, stats };
    },
    [],
    {
      initialValue: {
        attachments: [] as ChatAttachment[],
        stats: {
          total_projects: 0,
          total_sessions: 0,
          total_attachments: 0,
          total_attachment_bytes: 0,
        } as ChatStats,
      },
    },
  );

  const [deleteTarget, setDeleteTarget] = useState<ChatAttachment | null>(null);
  const [deleting, setDeleting] = useState(false);

  async function handleDelete() {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await chatAdmin.deleteAttachment(deleteTarget.id);
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
    if (s === "completed") return "success";
    if (s === "failed") return "error";
    if (s === "skipped") return "default";
    return "warning";
  };

  const extractionStatusLabel = (status: string) =>
    t(`chat_admin.extraction_${status}`, { defaultValue: status });

  const scopeLabel = (scope: string) =>
    t(`chat_admin.scope_${scope}`, { defaultValue: scope });

  const columns: ColumnDef<ChatAttachment>[] = [
    {
      key: "filename",
      label: t("chat_admin.col_filename"),
      sortable: true,
      sortValue: (r) => r.filename,
      searchValue: (r) => r.filename,
      render: (r) => r.filename,
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
      key: "size",
      label: t("chat_admin.col_size"),
      sortable: true,
      sortValue: (r) => r.size_bytes,
      align: "right",
      render: (r) => formatBytes(r.size_bytes),
    },
    {
      key: "content_type",
      label: t("chat_admin.col_type"),
      searchValue: (r) => r.content_type,
      render: (r) => r.content_type,
    },
    {
      key: "extraction_status",
      label: t("chat_admin.col_extraction"),
      sortable: true,
      sortValue: (r) => r.extraction_status,
      render: (r) => (
        <Chip
          label={extractionStatusLabel(r.extraction_status)}
          color={
            statusColor(r.extraction_status) as
              | "success"
              | "error"
              | "default"
              | "warning"
          }
          size="small"
          variant="outlined"
        />
      ),
    },
    {
      key: "scope",
      label: t("chat_admin.col_scope"),
      render: (r) => scopeLabel(r.scope),
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

  const statCards = [
    {
      label: t("chat_admin.stat_projects"),
      value: stats.total_projects,
    },
    {
      label: t("chat_admin.stat_sessions"),
      value: stats.total_sessions,
    },
    {
      label: t("chat_admin.stat_attachments"),
      value: stats.total_attachments,
    },
    {
      label: t("chat_admin.stat_storage"),
      value: formatBytes(stats.total_attachment_bytes),
    },
  ];

  return (
    <>
      <Stack direction="row" spacing={2} sx={{ mb: 3 }}>
        {statCards.map((card) => (
          <Card key={card.label} sx={{ minWidth: 160, flex: 1 }}>
            <CardContent>
              <Typography variant="body2" color="text.secondary">
                {card.label}
              </Typography>
              <Typography variant="h5">{card.value}</Typography>
            </CardContent>
          </Card>
        ))}
      </Stack>

      <DataTable
        columns={columns}
        rows={attachments}
        rowKey={(r) => r.id}
        loading={loading}
        error={error}
        onRefresh={load}
        searchPlaceholder={t("chat_admin.search_attachments")}
        pagination
      />

      <ConfirmDialog
        open={!!deleteTarget}
        title={t("chat_admin.delete_attachment_title")}
        message={t("chat_admin.delete_attachment_message", {
          name: deleteTarget?.filename,
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
