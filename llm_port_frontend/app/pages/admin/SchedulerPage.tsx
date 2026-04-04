/**
 * Admin → Scheduler — unified background jobs page.
 * Shows all job types (model downloads, RAG ingests, …) in a single view.
 */
import { useState, useEffect, useMemo } from "react";
import { useSearchParams } from "react-router";
import { useTranslation } from "react-i18next";
import {
  scheduler,
  type UnifiedJob,
  type JobStatus,
  type JobType,
} from "~/api/scheduler";
import { DataTable, type ColumnDef } from "~/components/DataTable";

import Chip from "@mui/material/Chip";
import IconButton from "@mui/material/IconButton";
import LinearProgress from "@mui/material/LinearProgress";
import Stack from "@mui/material/Stack";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";

import CancelIcon from "@mui/icons-material/Cancel";
import ReplayIcon from "@mui/icons-material/Replay";

// ── Status chip (reuses same palette as LLM JobStatusChip) ──────────

const STATUS_COLOR: Record<
  string,
  "default" | "info" | "success" | "error" | "warning"
> = {
  queued: "default",
  running: "info",
  success: "success",
  failed: "error",
  canceled: "warning",
};

function StatusChip({ value }: { value: string }) {
  return (
    <Chip
      label={value}
      size="small"
      color={STATUS_COLOR[value] ?? "default"}
      variant="outlined"
    />
  );
}

// ── Job-type chip ───────────────────────────────────────────────────

const TYPE_LABEL: Record<JobType, string> = {
  model_download: "Model Download",
  rag_ingest: "RAG Ingest",
};

const TYPE_COLOR: Record<JobType, "primary" | "secondary"> = {
  model_download: "primary",
  rag_ingest: "secondary",
};

function TypeChip({ value }: { value: JobType }) {
  return (
    <Chip
      label={TYPE_LABEL[value] ?? value}
      size="small"
      color={TYPE_COLOR[value] ?? "default"}
      variant="filled"
    />
  );
}

// ── Page component ──────────────────────────────────────────────────

export default function SchedulerPage() {
  const { t } = useTranslation();
  const [searchParams] = useSearchParams();
  const highlightLabel = searchParams.get("highlight");
  const [data, setData] = useState<UnifiedJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState("");
  const [typeFilter, setTypeFilter] = useState("");

  const statusOptions = [
    { value: "queued", label: t("scheduler.queued") },
    { value: "running", label: t("scheduler.running") },
    { value: "success", label: t("scheduler.success") },
    { value: "failed", label: t("scheduler.failed") },
    { value: "canceled", label: t("scheduler.canceled") },
  ];

  const typeOptions = [
    { value: "model_download", label: t("scheduler.type_model_download") },
    { value: "rag_ingest", label: t("scheduler.type_rag_ingest") },
  ];

  async function load() {
    setLoading(true);
    setError(null);
    try {
      setData(
        await scheduler.list(
          typeFilter ? (typeFilter as JobType) : undefined,
          statusFilter ? (statusFilter as JobStatus) : undefined,
        ),
      );
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t("scheduler.failed_load"));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, [statusFilter, typeFilter]);

  // Auto-refresh when jobs are active
  useEffect(() => {
    const hasActive = data.some(
      (j) => j.status === "running" || j.status === "queued",
    );
    if (!hasActive) return;
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  }, [data]);

  async function handleCancel(id: string) {
    try {
      await scheduler.cancel(id);
      await load();
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : t("scheduler.cancel_failed"));
    }
  }

  async function handleRetry(id: string) {
    try {
      await scheduler.retry(id);
      await load();
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : t("scheduler.retry_failed"));
    }
  }

  const columns: ColumnDef<UnifiedJob>[] = [
    {
      key: "label",
      label: t("scheduler.job_name"),
      sortable: true,
      sortValue: (j) => j.label,
      searchValue: (j) => `${j.label} ${j.meta?.hf_repo_id ?? ""}`,
      render: (j) => (
        <Typography variant="body2" fontSize="0.85rem" fontWeight={500}>
          {j.label}
        </Typography>
      ),
    },
    {
      key: "job_type",
      label: t("scheduler.type"),
      sortable: true,
      sortValue: (j) => j.job_type,
      render: (j) => <TypeChip value={j.job_type} />,
    },
    {
      key: "status",
      label: t("common.status"),
      sortable: true,
      sortValue: (j) => j.status,
      render: (j) => <StatusChip value={j.status} />,
    },
    {
      key: "progress",
      label: t("scheduler.progress"),
      sortable: true,
      sortValue: (j) => j.progress,
      render: (j) =>
        j.progress >= 0 ? (
          <Stack
            direction="row"
            spacing={1}
            alignItems="center"
            sx={{ minWidth: 140 }}
          >
            <LinearProgress
              variant="determinate"
              value={j.progress}
              sx={{ flexGrow: 1, height: 8, borderRadius: 4 }}
            />
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ minWidth: 36 }}
            >
              {j.progress}%
            </Typography>
          </Stack>
        ) : (
          <Typography variant="body2" color="text.disabled" fontSize="0.8rem">
            —
          </Typography>
        ),
    },
    {
      key: "error",
      label: t("common.error"),
      searchValue: (j) => j.error_message ?? "",
      render: (j) =>
        j.error_message ? (
          <Tooltip title={j.error_message}>
            <Typography
              variant="body2"
              color="error"
              fontSize="0.75rem"
              sx={{
                maxWidth: 250,
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
            >
              {j.error_message}
            </Typography>
          </Tooltip>
        ) : (
          <Typography variant="body2" color="text.disabled" fontSize="0.8rem">
            —
          </Typography>
        ),
    },
    {
      key: "created_at",
      label: t("scheduler.started"),
      sortable: true,
      sortValue: (j) => j.created_at,
      render: (j) => (
        <Typography variant="body2" color="text.secondary" fontSize="0.8rem">
          {new Date(j.created_at).toLocaleString()}
        </Typography>
      ),
    },
    {
      key: "actions",
      label: "",
      align: "right",
      render: (j) => {
        const isDownload = j.job_type === "model_download";
        const cancelable =
          isDownload && (j.status === "queued" || j.status === "running");
        const retryable =
          isDownload &&
          (j.status === "queued" ||
            j.status === "failed" ||
            j.status === "canceled");
        return (
          <Stack direction="row" spacing={0.5} justifyContent="flex-end">
            {retryable && (
              <Tooltip title={t("scheduler.retry")}>
                <IconButton
                  size="small"
                  color="info"
                  onClick={(e) => {
                    e.stopPropagation();
                    void handleRetry(j.id);
                  }}
                >
                  <ReplayIcon fontSize="small" />
                </IconButton>
              </Tooltip>
            )}
            {cancelable && (
              <Tooltip title={t("common.cancel")}>
                <IconButton
                  size="small"
                  color="warning"
                  onClick={(e) => {
                    e.stopPropagation();
                    void handleCancel(j.id);
                  }}
                >
                  <CancelIcon fontSize="small" />
                </IconButton>
              </Tooltip>
            )}
          </Stack>
        );
      },
    },
  ];

  // Resolve highlight query param to a job ID
  const highlightId = useMemo(() => {
    if (!highlightLabel) return null;
    const match = data.find(
      (j) => j.label.toLowerCase() === highlightLabel.toLowerCase(),
    );
    return match?.id ?? null;
  }, [data, highlightLabel]);

  return (
    <DataTable
      columns={columns}
      rows={data}
      rowKey={(j) => j.id}
      loading={loading}
      error={error}
      title={t("scheduler.title")}
      emptyMessage={t("scheduler.empty")}
      onRefresh={load}
      searchPlaceholder={t("scheduler.search_placeholder")}
      highlightId={highlightId}
      columnFilters={[
        {
          label: t("scheduler.type"),
          value: typeFilter,
          options: typeOptions,
          onChange: setTypeFilter,
        },
        {
          label: t("common.status"),
          value: statusFilter,
          options: statusOptions,
          onChange: setStatusFilter,
        },
      ]}
    />
  );
}
