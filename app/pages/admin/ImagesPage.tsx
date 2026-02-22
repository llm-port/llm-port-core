/**
 * Admin → Images page.
 * Pull, prune, and browse local images — table powered by DataTable.
 */
import { useState, useEffect } from "react";
import { useOutletContext } from "react-router";
import { useTranslation } from "react-i18next";
import { images, type ImageSummary, type PruneReport } from "~/api/admin";
import { DataTable, type ColumnDef } from "~/components/DataTable";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import CloudDownloadIcon from "@mui/icons-material/CloudDownload";
import DeleteSweepIcon from "@mui/icons-material/DeleteSweep";

interface AdminContext {
  rootModeActive: boolean;
}

function bytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(2)} GB`;
}

export default function ImagesPage() {
  const { t } = useTranslation();
  const { rootModeActive } = useOutletContext<AdminContext>();
  const [data, setData] = useState<ImageSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pullImage, setPullImage] = useState("");
  const [pullTag, setPullTag] = useState("latest");
  const [pulling, setPulling] = useState(false);
  const [pullError, setPullError] = useState<string | null>(null);
  const [pruneReport, setPruneReport] = useState<PruneReport | null>(null);
  const [pruning, setPruning] = useState(false);
  const columns: ColumnDef<ImageSummary>[] = [
    {
      key: "tags",
      label: t("images.tags"),
      searchValue: (img) => img.repo_tags.join(" "),
      render: (img) =>
        img.repo_tags.length > 0 ? (
          <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap>
            {img.repo_tags.map((tag) => (
              <Chip
                key={tag}
                label={tag}
                size="small"
                variant="outlined"
                sx={{ fontFamily: "monospace", fontSize: "0.75rem" }}
              />
            ))}
          </Stack>
        ) : (
          <Typography variant="caption" color="text.secondary">
            {t("common.none")}
          </Typography>
        ),
    },
    {
      key: "id",
      label: "ID",
      searchValue: (img) => img.id,
      render: (img) => (
        <Typography variant="body2" fontFamily="monospace" fontSize="0.8rem" color="text.secondary">
          {img.id.slice(7, 19)}
        </Typography>
      ),
    },
    {
      key: "size",
      label: t("common.size"),
      sortable: true,
      sortValue: (img) => img.size,
      render: (img) => (
        <Typography variant="body2" fontSize="0.8rem">
          {bytes(img.size)}
        </Typography>
      ),
    },
    {
      key: "created",
      label: t("common.created"),
      sortable: true,
      sortValue: (img) => img.created,
      render: (img) => (
        <Typography variant="body2" fontSize="0.8rem" color="text.secondary">
          {img.created ? new Date(Number(img.created) * 1000).toLocaleDateString() : "—"}
        </Typography>
      ),
    },
  ];

  async function load() {
    setLoading(true);
    setError(null);
    try {
      setData(await images.list());
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t("images.failed_load"));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function handlePull(e: React.FormEvent) {
    e.preventDefault();
    setPullError(null);
    setPulling(true);
    try {
      await images.pull(pullImage, pullTag);
      setPullImage("");
      setPullTag("latest");
      await load();
    } catch (e: unknown) {
      setPullError(e instanceof Error ? e.message : t("images.pull_failed"));
    } finally {
      setPulling(false);
    }
  }

  async function handlePrune(dryRun: boolean) {
    setPruning(true);
    try {
      const report = await images.prune(dryRun);
      setPruneReport(report);
      if (!dryRun) await load();
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : t("images.prune_failed"));
    } finally {
      setPruning(false);
    }
  }

  return (
    <Box sx={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}>
      {/* Controls (non-scrolling) */}
      <Box sx={{ flexShrink: 0 }}>
        {/* Pull form */}
        <Paper variant="outlined" sx={{ p: 2.5, mb: 2 }}>
          <form onSubmit={handlePull}>
            <Stack direction="row" spacing={1.5} alignItems="flex-end" flexWrap="wrap">
              <TextField
                label={t("containers.image")}
                size="small"
                placeholder="nginx"
                value={pullImage}
                onChange={(e) => setPullImage(e.target.value)}
                required
                sx={{ width: 220 }}
              />
              <TextField
                label={t("images.tag")}
                size="small"
                placeholder="latest"
                value={pullTag}
                onChange={(e) => setPullTag(e.target.value)}
                sx={{ width: 120 }}
              />
              <Button
                type="submit"
                variant="contained"
                disabled={pulling}
                startIcon={pulling ? <CircularProgress size={16} /> : <CloudDownloadIcon />}
              >
                {pulling ? t("images.pulling") : t("images.pull_image")}
              </Button>
            </Stack>
            {pullError && (
              <Alert severity="error" sx={{ mt: 1.5 }}>
                {pullError}
              </Alert>
            )}
          </form>
        </Paper>

        {/* Prune */}
        <Paper variant="outlined" sx={{ p: 2, mb: 2 }}>
          <Stack direction="row" alignItems="center" spacing={2}>
            <Typography variant="body2" color="text.secondary" sx={{ flexGrow: 1 }}>
              {t("images.prune_dangling")}{" "}
              <Typography component="span" variant="caption" color="text.secondary">
                ({t("images.requires_root_mode")})
              </Typography>
            </Typography>
            <Button
              size="small"
              variant="outlined"
              disabled={!rootModeActive || pruning}
              onClick={() => handlePrune(true)}
            >
              {t("images.dry_run")}
            </Button>
            <Button
              size="small"
              variant="contained"
              color="error"
              disabled={!rootModeActive || pruning}
              startIcon={<DeleteSweepIcon />}
              onClick={() => handlePrune(false)}
            >
              {pruning ? t("images.pruning") : t("images.prune")}
            </Button>
          </Stack>
        </Paper>

        {pruneReport && (
          <Alert severity={pruneReport.dry_run ? "info" : "success"} sx={{ mb: 2 }}>
            <strong>
              {pruneReport.dry_run ? t("images.dry_run_would_prune") : t("images.pruned")}
            </strong>{" "}
            {t("images.prune_result", {
              count: pruneReport.deleted.length,
              space: bytes(pruneReport.space_reclaimed),
            })}
          </Alert>
        )}
      </Box>

      {/* Table */}
      <DataTable
        title={t("images.title")}
        columns={columns}
        rows={data}
        rowKey={(img) => img.id}
        loading={loading}
        error={error}
        emptyMessage={t("images.empty")}
        onRefresh={load}
        searchPlaceholder={t("images.search_placeholder")}
      />
    </Box>
  );
}
