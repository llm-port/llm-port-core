import { useState } from "react";
import { useTranslation } from "react-i18next";
import { nodesApi, type NodeProfile } from "~/api/nodes";
import { ConfirmDialog } from "~/components/ConfirmDialog";
import { DataTable, type ColumnDef } from "~/components/DataTable";
import { useAsyncData } from "~/lib/useAsyncData";
import NodeProfileFormDialog from "./NodeProfileFormDialog";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import IconButton from "@mui/material/IconButton";
import Stack from "@mui/material/Stack";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";

import AddIcon from "@mui/icons-material/Add";
import DeleteOutlineIcon from "@mui/icons-material/DeleteOutline";
import EditIcon from "@mui/icons-material/Edit";
import StarIcon from "@mui/icons-material/Star";

export default function NodeProfilesPage() {
  const { t } = useTranslation();
  const { data, loading, error, refresh, setError } = useAsyncData(
    () => nodesApi.listProfiles(),
    [],
    { initialValue: [] as NodeProfile[] },
  );

  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<NodeProfile | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<NodeProfile | null>(null);
  const [deleting, setDeleting] = useState(false);

  function openCreate() {
    setEditing(null);
    setFormOpen(true);
  }

  function openEdit(profile: NodeProfile) {
    setEditing(profile);
    setFormOpen(true);
  }

  async function handleSave(payload: Record<string, unknown>) {
    try {
      if (editing) {
        await nodesApi.updateProfile(editing.id, payload);
      } else {
        await nodesApi.createProfile(
          payload as Parameters<typeof nodesApi.createProfile>[0],
        );
      }
      setFormOpen(false);
      await refresh();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Save failed.");
    }
  }

  async function handleDelete() {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await nodesApi.deleteProfile(deleteTarget.id);
      setDeleteTarget(null);
      await refresh();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Delete failed.");
    } finally {
      setDeleting(false);
    }
  }

  const columns: ColumnDef<NodeProfile>[] = [
    {
      key: "name",
      label: t("node_profiles.name"),
      sortable: true,
      sortValue: (row) => row.name,
      searchValue: (row) => `${row.name} ${row.description ?? ""}`,
      render: (row) => (
        <Box>
          <Stack direction="row" spacing={0.5} alignItems="center">
            <Typography variant="body2" fontWeight={600}>
              {row.name}
            </Typography>
            {row.is_default && (
              <Chip
                size="small"
                icon={<StarIcon sx={{ fontSize: 14 }} />}
                label={t("node_profiles.default")}
                color="primary"
                variant="outlined"
                sx={{ height: 20 }}
              />
            )}
          </Stack>
          {row.description && (
            <Typography variant="caption" color="text.secondary">
              {row.description}
            </Typography>
          )}
        </Box>
      ),
    },
    {
      key: "runtime",
      label: t("node_profiles.runtime"),
      searchValue: (row) => {
        const rt = row.runtime_config as Record<string, unknown>;
        return String(rt?.runtime_type ?? "");
      },
      render: (row) => {
        const rt = row.runtime_config as Record<string, unknown>;
        const val = String(rt?.runtime_type ?? "auto");
        return (
          <Typography variant="body2" fontFamily="monospace">
            {val}
          </Typography>
        );
      },
    },
    {
      key: "gpu",
      label: t("node_profiles.gpu_vendor"),
      searchValue: (row) => {
        const gc = row.gpu_config as Record<string, unknown>;
        return String(gc?.vendor ?? "");
      },
      render: (row) => {
        const gc = row.gpu_config as Record<string, unknown>;
        const val = String(gc?.vendor ?? "auto");
        return (
          <Typography variant="body2" fontFamily="monospace">
            {val}
          </Typography>
        );
      },
    },
    {
      key: "created",
      label: t("node_profiles.created"),
      sortable: true,
      sortValue: (row) => Date.parse(row.created_at),
      render: (row) => (
        <Typography variant="body2" color="text.secondary">
          {new Date(row.created_at).toLocaleDateString()}
        </Typography>
      ),
    },
    {
      key: "actions",
      label: t("common.actions"),
      align: "right" as const,
      hideable: false,
      render: (row) => (
        <Stack direction="row" spacing={0.5} justifyContent="flex-end">
          <Tooltip title={t("common.edit")}>
            <IconButton size="small" onClick={() => openEdit(row)}>
              <EditIcon fontSize="small" />
            </IconButton>
          </Tooltip>
          <Tooltip title={t("common.delete")}>
            <IconButton
              size="small"
              color="error"
              onClick={() => setDeleteTarget(row)}
            >
              <DeleteOutlineIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        </Stack>
      ),
    },
  ];

  if (loading) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", py: 6 }}>
        <CircularProgress size={28} />
      </Box>
    );
  }

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <Stack direction="row" justifyContent="space-between" alignItems="center">
        <Typography variant="h5">{t("node_profiles.page_title")}</Typography>
        <Button
          variant="contained"
          size="small"
          startIcon={<AddIcon />}
          onClick={openCreate}
        >
          {t("node_profiles.create")}
        </Button>
      </Stack>

      {error && (
        <Alert severity="error" onClose={() => setError(null)}>
          {error}
        </Alert>
      )}

      <DataTable
        columns={columns}
        rows={data}
        rowKey={(r) => r.id}
        emptyMessage={t("node_profiles.empty")}
        searchPlaceholder={t("node_profiles.search_placeholder")}
      />

      <NodeProfileFormDialog
        open={formOpen}
        onClose={() => setFormOpen(false)}
        onSave={handleSave}
        profile={editing}
      />

      <ConfirmDialog
        open={!!deleteTarget}
        title={t("node_profiles.delete_title")}
        message={t("node_profiles.delete_confirm", {
          name: deleteTarget?.name,
        })}
        confirmLabel={t("common.delete")}
        loading={deleting}
        onConfirm={() => void handleDelete()}
        onClose={() => setDeleteTarget(null)}
      />
    </Box>
  );
}
