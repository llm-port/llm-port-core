import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { ragJobs, ragPublishes, type RagIngestJob, type RagPublish } from "~/api/rag";
import { DataTable, type ColumnDef } from "~/components/DataTable";

import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";

function statusColor(status: string): "default" | "success" | "warning" | "error" {
  const normalized = status.toLowerCase();
  if (["success", "succeeded", "completed", "done"].includes(normalized)) return "success";
  if (["running", "queued", "pending", "scheduled", "created"].includes(normalized)) return "warning";
  if (["failed", "error", "partial"].includes(normalized)) return "error";
  return "default";
}

export default function RagPublishesPage() {
  const { t } = useTranslation();
  const [publishes, setPublishes] = useState<RagPublish[]>([]);
  const [jobs, setJobs] = useState<RagIngestJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const hasActiveItems = useMemo(
    () =>
      publishes.some((item) => ["scheduled", "queued", "running"].includes(item.status.toLowerCase())) ||
      jobs.some((job) => ["queued", "running", "pending"].includes(job.status.toLowerCase())),
    [publishes, jobs],
  );

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const [publishPayload, jobsPayload] = await Promise.all([
        ragPublishes.list(100),
        ragJobs.list(100),
      ]);
      setPublishes(publishPayload.publishes);
      setJobs(jobsPayload.jobs);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load publishes.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  useEffect(() => {
    if (!hasActiveItems) return;
    const timer = setInterval(() => void load(), 5000);
    return () => clearInterval(timer);
  }, [hasActiveItems]);

  const publishColumns: ColumnDef<RagPublish>[] = [
    {
      key: "id",
      label: "Publish",
      sortable: true,
      sortValue: (row) => row.created_at,
      render: (row) => (
        <Typography variant="body2" sx={{ fontFamily: "monospace", fontSize: "0.78rem" }}>
          {row.id.slice(0, 12)}
        </Typography>
      ),
    },
    {
      key: "container",
      label: "Container",
      searchValue: (row) => row.container_id,
      render: (row) => row.container_id.slice(0, 12),
    },
    {
      key: "status",
      label: t("common.status"),
      sortable: true,
      sortValue: (row) => row.status,
      render: (row) => <Chip size="small" color={statusColor(row.status)} label={row.status} />,
    },
    {
      key: "scheduled",
      label: "Scheduled",
      render: (row) => (row.scheduled_for ? new Date(row.scheduled_for).toLocaleString() : "now"),
    },
    {
      key: "error",
      label: t("common.error"),
      render: (row) => row.error ?? "—",
    },
  ];

  const jobColumns: ColumnDef<RagIngestJob>[] = [
    {
      key: "job_id",
      label: "Job",
      sortable: true,
      sortValue: (row) => row.created_at,
      render: (row) => (
        <Typography variant="body2" sx={{ fontFamily: "monospace", fontSize: "0.78rem" }}>
          {row.job_id.slice(0, 12)}
        </Typography>
      ),
    },
    {
      key: "job_type",
      label: "Type",
      sortable: true,
      sortValue: (row) => row.job_type,
      render: (row) => row.job_type,
    },
    {
      key: "status",
      label: t("common.status"),
      sortable: true,
      sortValue: (row) => row.status,
      render: (row) => <Chip size="small" color={statusColor(row.status)} label={row.status} />,
    },
    {
      key: "created_at",
      label: t("common.created"),
      sortable: true,
      sortValue: (row) => row.created_at,
      render: (row) => new Date(row.created_at).toLocaleString(),
    },
    {
      key: "error",
      label: t("common.error"),
      render: (row) => row.error ?? "—",
    },
  ];

  return (
    <Stack spacing={2}>
      <Stack direction="row" justifyContent="space-between" alignItems="center">
        <Typography variant="h5">{t("rag_publishes.title")}</Typography>
        <Button variant="outlined" onClick={() => void load()} disabled={loading}>
          {t("dashboard.refresh")}
        </Button>
      </Stack>
      <DataTable
        columns={publishColumns}
        rows={publishes}
        rowKey={(row) => row.id}
        loading={loading}
        error={error}
        title={t("rag_publishes.publishes")}
        emptyMessage={t("rag_publishes.empty_publishes")}
        searchPlaceholder={t("rag_publishes.search_publishes")}
      />
      <DataTable
        columns={jobColumns}
        rows={jobs}
        rowKey={(row) => row.job_id}
        loading={loading}
        error={null}
        title={t("rag_publishes.jobs")}
        emptyMessage={t("rag_publishes.empty_jobs")}
        searchPlaceholder={t("rag_publishes.search_jobs")}
      />
    </Stack>
  );
}
