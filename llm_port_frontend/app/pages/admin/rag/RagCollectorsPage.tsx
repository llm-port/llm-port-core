import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  ragCollectors,
  ragJobs,
  type RagCollectorSummary,
  type RagIngestJob,
} from "~/api/rag";
import { DataTable, type ColumnDef } from "~/components/DataTable";

import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import IconButton from "@mui/material/IconButton";
import Stack from "@mui/material/Stack";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";

function statusColor(status: string): "default" | "success" | "warning" | "error" {
  const normalized = status.toLowerCase();
  if (["success", "succeeded", "completed", "done"].includes(normalized)) return "success";
  if (["running", "queued", "pending", "created"].includes(normalized)) return "warning";
  if (["failed", "error"].includes(normalized)) return "error";
  return "default";
}

export default function RagCollectorsPage() {
  const { t } = useTranslation();
  const [collectors, setCollectors] = useState<RagCollectorSummary[]>([]);
  const [jobs, setJobs] = useState<RagIngestJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyCollectorId, setBusyCollectorId] = useState<string | null>(null);

  const hasActiveJobs = useMemo(
    () => jobs.some((job) => ["queued", "running", "pending"].includes(job.status.toLowerCase())),
    [jobs],
  );

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const [collectorPayload, jobsPayload] = await Promise.all([
        ragCollectors.list(),
        ragJobs.list(50),
      ]);
      setCollectors(collectorPayload.collectors);
      setJobs(jobsPayload.jobs);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t("rag_collectors.failed_load"));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  useEffect(() => {
    if (!hasActiveJobs) return;
    const timer = setInterval(() => void load(), 5000);
    return () => clearInterval(timer);
  }, [hasActiveJobs]);

  async function runCollector(collectorId: string) {
    setBusyCollectorId(collectorId);
    try {
      await ragCollectors.run(collectorId);
      await load();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t("rag_collectors.failed_run"));
    } finally {
      setBusyCollectorId(null);
    }
  }

  const collectorColumns: ColumnDef<RagCollectorSummary>[] = [
    {
      key: "id",
      label: t("rag_collectors.collector_id"),
      sortable: true,
      sortValue: (row) => row.id,
      searchValue: (row) => row.id,
      render: (row) => <Typography fontWeight={600}>{row.id}</Typography>,
    },
    {
      key: "type",
      label: t("rag_collectors.type"),
      sortable: true,
      sortValue: (row) => row.type,
      searchValue: (row) => row.type,
      render: (row) => row.type,
    },
    {
      key: "tenant",
      label: t("rag_collectors.tenant"),
      sortable: true,
      sortValue: (row) => row.tenant_id,
      searchValue: (row) => row.tenant_id,
      render: (row) => row.tenant_id,
    },
    {
      key: "workspace",
      label: t("rag_collectors.workspace"),
      sortable: true,
      sortValue: (row) => row.workspace_id ?? "",
      searchValue: (row) => row.workspace_id ?? "",
      render: (row) => row.workspace_id ?? t("common.none"),
    },
    {
      key: "enabled",
      label: t("common.status"),
      sortable: true,
      sortValue: (row) => (row.enabled ? 1 : 0),
      render: (row) => (
        <Chip
          size="small"
          color={row.enabled ? "success" : "default"}
          label={row.enabled ? t("common.active") : t("common.inactive")}
        />
      ),
    },
    {
      key: "schedule",
      label: t("rag_collectors.schedule"),
      searchValue: (row) => row.schedule,
      render: (row) => (
        <Typography variant="body2" sx={{ fontFamily: "monospace" }}>
          {row.schedule}
        </Typography>
      ),
    },
    {
      key: "actions",
      label: t("common.actions"),
      align: "right",
      render: (row) => (
        <Tooltip title={t("rag_collectors.run_now")}>
          <span>
            <IconButton
              size="small"
              disabled={!row.enabled || busyCollectorId === row.id}
              onClick={(event) => {
                event.stopPropagation();
                void runCollector(row.id);
              }}
            >
              <PlayArrowIcon fontSize="small" />
            </IconButton>
          </span>
        </Tooltip>
      ),
    },
  ];

  const jobColumns: ColumnDef<RagIngestJob>[] = [
    {
      key: "job_id",
      label: t("rag_collectors.job_id"),
      sortable: true,
      sortValue: (row) => row.created_at,
      searchValue: (row) => `${row.job_id} ${row.collector_id} ${row.source_id ?? ""}`,
      render: (row) => (
        <Typography variant="body2" sx={{ fontFamily: "monospace", fontSize: "0.78rem" }}>
          {row.job_id.slice(0, 12)}
        </Typography>
      ),
    },
    {
      key: "collector",
      label: t("rag_collectors.collector_id"),
      sortable: true,
      sortValue: (row) => row.collector_id,
      render: (row) => row.collector_id,
    },
    {
      key: "status",
      label: t("common.status"),
      sortable: true,
      sortValue: (row) => row.status,
      render: (row) => (
        <Chip
          size="small"
          color={statusColor(row.status)}
          label={row.status}
        />
      ),
    },
    {
      key: "created",
      label: t("common.created"),
      sortable: true,
      sortValue: (row) => row.created_at,
      render: (row) => new Date(row.created_at).toLocaleString(),
    },
    {
      key: "error",
      label: t("common.error"),
      searchValue: (row) => row.error ?? "",
      render: (row) => (
        <Typography
          variant="body2"
          color={row.error ? "error.main" : "text.disabled"}
          sx={{ maxWidth: 280, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
        >
          {row.error ?? "—"}
        </Typography>
      ),
    },
  ];

  return (
    <Stack spacing={2}>
      <Stack direction="row" justifyContent="space-between" alignItems="center">
        <Typography variant="h5">{t("rag_collectors.title")}</Typography>
        <Button variant="outlined" onClick={() => void load()} disabled={loading}>
          {t("dashboard.refresh")}
        </Button>
      </Stack>

      <DataTable
        columns={collectorColumns}
        rows={collectors}
        rowKey={(row) => row.id}
        loading={loading}
        error={error}
        title={t("rag_collectors.collectors")}
        emptyMessage={t("rag_collectors.empty_collectors")}
        searchPlaceholder={t("rag_collectors.search_collectors")}
      />

      <DataTable
        columns={jobColumns}
        rows={jobs}
        rowKey={(row) => row.job_id}
        loading={loading}
        error={null}
        title={t("rag_collectors.jobs")}
        emptyMessage={t("rag_collectors.empty_jobs")}
        searchPlaceholder={t("rag_collectors.search_jobs")}
      />
    </Stack>
  );
}

