/**
 * Admin → Containers list page.
 * Powered by the shared DataTable with search, sort, and class filtering.
 */
import { useState } from "react";
import { Link as RouterLink, useOutletContext, useNavigate } from "react-router";
import { useTranslation } from "react-i18next";
import {
  containers,
  canStop,
  canDelete,
  canPause,
  type ContainerSummary,
} from "~/api/admin";
import { DataTable, type ColumnDef } from "~/components/DataTable";
import { ClassChip, StateChip } from "~/components/Chips";
import { useAsyncData } from "~/lib/useAsyncData";

import Button from "@mui/material/Button";
import IconButton from "@mui/material/IconButton";
import Link from "@mui/material/Link";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import Tooltip from "@mui/material/Tooltip";

import AddIcon from "@mui/icons-material/Add";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import StopIcon from "@mui/icons-material/Stop";
import RestartAltIcon from "@mui/icons-material/RestartAlt";
import PauseIcon from "@mui/icons-material/Pause";
import PlayCircleOutlineIcon from "@mui/icons-material/PlayCircleOutline";
import DeleteIcon from "@mui/icons-material/Delete";
import OpenInNewIcon from "@mui/icons-material/OpenInNew";

interface AdminContext {
  rootModeActive: boolean;
}

const CLASS_OPTIONS: { value: string; label: string }[] = [
  { value: "SYSTEM_CORE", label: "SYSTEM_CORE" },
  { value: "SYSTEM_AUX", label: "SYSTEM_AUX" },
  { value: "TENANT_APP", label: "TENANT_APP" },
  { value: "UNTRUSTED", label: "UNTRUSTED" },
];

export default function ContainersPage() {
  const { t } = useTranslation();
  const { rootModeActive } = useOutletContext<AdminContext>();
  const navigate = useNavigate();
  const {
    data,
    loading,
    error,
    refresh: load,
  } = useAsyncData(
    () => containers.list(),
    [],
    { initialValue: [] as ContainerSummary[] },
  );
  const [filterClasses, setFilterClasses] = useState<string[]>(
    CLASS_OPTIONS.map((o) => o.value),
  );
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  async function handleAction(
    id: string,
    action: "start" | "stop" | "restart" | "pause" | "unpause",
  ) {
    setActionLoading(`${id}-${action}`);
    try {
      await containers.lifecycle(id, action);
      await load();
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : t("common.action_failed"));
    } finally {
      setActionLoading(null);
    }
  }

  async function handleDelete(id: string) {
    if (!confirm(t("containers.confirm_delete"))) return;
    setActionLoading(`${id}-delete`);
    try {
      await containers.delete(id);
      await load();
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : t("common.delete_failed"));
    } finally {
      setActionLoading(null);
    }
  }

  const columns: ColumnDef<ContainerSummary>[] = [
    {
      key: "name",
      label: t("common.name"),
      sortable: true,
      sortValue: (c) => c.name,
      searchValue: (c) => `${c.name} ${c.id}`,
      render: (c) => (
        <>
          <Link
            component={RouterLink}
            to={`/admin/containers/${c.id}`}
            underline="hover"
            color="primary.light"
            fontWeight={600}
            sx={{ fontFamily: "monospace", fontSize: "0.8rem" }}
          >
            {c.name}
          </Link>
          <Typography
            variant="caption"
            display="block"
            color="text.secondary"
            fontFamily="monospace"
          >
            {c.id.slice(0, 12)}
          </Typography>
        </>
      ),
    },
    {
      key: "image",
      label: t("containers.image"),
      sortable: true,
      sortValue: (c) => c.image,
      searchValue: (c) => c.image,
      render: (c) => (
        <Typography variant="body2" fontFamily="monospace" fontSize="0.8rem">
          {c.image}
        </Typography>
      ),
    },
    {
      key: "state",
      label: t("containers.state"),
      sortable: true,
      sortValue: (c) => c.state,
      searchValue: (c) => c.state,
      render: (c) => (
        <>
          <StateChip value={c.state} />
          <Typography variant="caption" display="block" color="text.secondary">
            {c.status}
          </Typography>
        </>
      ),
    },
    {
      key: "container_class",
      label: t("containers.class"),
      sortable: true,
      sortValue: (c) => c.container_class,
      searchValue: (c) => c.container_class,
      render: (c) => <ClassChip value={c.container_class} />,
    },
    {
      key: "owner_scope",
      label: t("containers.scope"),
      sortable: true,
      sortValue: (c) => c.owner_scope,
      searchValue: (c) => c.owner_scope,
      render: (c) => (
        <Typography variant="body2" color="text.secondary" fontSize="0.8rem">
          {c.owner_scope}
        </Typography>
      ),
    },
    {
      key: "actions",
      label: t("common.actions"),
      align: "right",
      render: (c) => {
        const busy = !!actionLoading?.startsWith(c.id);
        const isRunning = c.state.toLowerCase() === "running";
        const isPaused = c.state.toLowerCase() === "paused";
        return (
          <Stack direction="row" spacing={0.5} justifyContent="flex-end">
            {!isRunning && !isPaused && (
              <Tooltip title={t("common.start")}>
                <IconButton
                  size="small"
                  color="success"
                  disabled={busy}
                  onClick={(e) => {
                    e.stopPropagation();
                    void handleAction(c.id, "start");
                  }}
                >
                  <PlayArrowIcon fontSize="small" />
                </IconButton>
              </Tooltip>
            )}
            {(isRunning || isPaused) && canStop(c.container_class, rootModeActive) && (
              <Tooltip title={t("common.stop")}>
                <IconButton
                  size="small"
                  color="warning"
                  disabled={busy}
                  onClick={(e) => {
                    e.stopPropagation();
                    void handleAction(c.id, "stop");
                  }}
                >
                  <StopIcon fontSize="small" />
                </IconButton>
              </Tooltip>
            )}
            {isRunning && canPause(c.container_class, rootModeActive) && (
              <Tooltip title={t("common.pause")}>
                <IconButton
                  size="small"
                  color="info"
                  disabled={busy}
                  onClick={(e) => {
                    e.stopPropagation();
                    void handleAction(c.id, "pause");
                  }}
                >
                  <PauseIcon fontSize="small" />
                </IconButton>
              </Tooltip>
            )}
            {isPaused && (
              <Tooltip title={t("common.unpause")}>
                <IconButton
                  size="small"
                  color="success"
                  disabled={busy}
                  onClick={(e) => {
                    e.stopPropagation();
                    void handleAction(c.id, "unpause");
                  }}
                >
                  <PlayCircleOutlineIcon fontSize="small" />
                </IconButton>
              </Tooltip>
            )}
            {isRunning && (
              <Tooltip title={t("common.restart")}>
                <IconButton
                  size="small"
                  disabled={busy}
                  onClick={(e) => {
                    e.stopPropagation();
                    void handleAction(c.id, "restart");
                  }}
                >
                  <RestartAltIcon fontSize="small" />
                </IconButton>
              </Tooltip>
            )}
            {canDelete(c.container_class, rootModeActive) && (
              <Tooltip title={t("common.delete")}>
                <IconButton
                  size="small"
                  color="error"
                  disabled={busy}
                  onClick={(e) => {
                    e.stopPropagation();
                    void handleDelete(c.id);
                  }}
                >
                  <DeleteIcon fontSize="small" />
                </IconButton>
              </Tooltip>
            )}
            <Tooltip title={t("common.details")}>
              <IconButton
                size="small"
                component={RouterLink}
                to={`/admin/containers/${c.id}`}
                onClick={(e: React.MouseEvent) => e.stopPropagation()}
              >
                <OpenInNewIcon fontSize="small" />
              </IconButton>
            </Tooltip>
          </Stack>
        );
      },
    },
  ];

  return (
    <DataTable
      title={t("containers.title")}
      columns={columns}
      rows={data.filter((c) => filterClasses.includes(c.container_class))}
      rowKey={(c) => c.id}
      loading={loading}
      error={error}
      emptyMessage={t("containers.empty")}
      onRefresh={load}
      searchPlaceholder={t("containers.search_placeholder")}
      toolbarActions={
        <Button
          variant="contained"
          size="small"
          startIcon={<AddIcon />}
          onClick={() => navigate("/admin/containers/new")}
        >
          {t("containers.create")}
        </Button>
      }
      columnFilters={[
        {
          label: t("containers.class"),
          value: "",
          multi: true,
          multiValue: filterClasses,
          onMultiChange: setFilterClasses,
          options: CLASS_OPTIONS,
          minWidth: 180,
        },
      ]}
    />
  );
}
