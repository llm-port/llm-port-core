/**
 * Admin → LLM → Model detail page.
 * Shows model metadata and a table of its artifacts.
 */
import { useState, useEffect } from "react";
import { useParams, useNavigate } from "react-router";
import { useTranslation } from "react-i18next";
import { models, type Model, type Artifact } from "~/api/llm";
import { ModelStatusChip, FormatChip } from "~/components/Chips";
import { DataTable, type ColumnDef } from "~/components/DataTable";

import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import Chip from "@mui/material/Chip";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import CircularProgress from "@mui/material/CircularProgress";
import Alert from "@mui/material/Alert";

import ArrowBackIcon from "@mui/icons-material/ArrowBack";
import DeleteIcon from "@mui/icons-material/Delete";

function humanSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

export default function ModelDetailPage() {
  const { t } = useTranslation();
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [model, setModel] = useState<Model | null>(null);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    if (!id) return;
    setLoading(true);
    setError(null);
    try {
      const [m, arts] = await Promise.all([models.get(id), models.artifacts(id)]);
      setModel(m);
      setArtifacts(arts);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t("llm_model_detail.failed_load"));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, [id]);

  async function handleDelete() {
    if (!id || !confirm(t("llm_model_detail.confirm_delete"))) return;
    try {
      await models.delete(id);
      navigate("/admin/llm/models");
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : t("common.delete_failed"));
    }
  }

  if (loading) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", py: 6 }}>
        <CircularProgress size={32} />
      </Box>
    );
  }

  if (error || !model) {
    return <Alert severity="error">{error ?? t("llm_model_detail.not_found")}</Alert>;
  }

  const artifactCols: ColumnDef<Artifact>[] = [
    {
      key: "path",
      label: t("llm_model_detail.file"),
      sortable: true,
      sortValue: (a) => a.path,
      searchValue: (a) => a.path,
      render: (a) => (
        <Typography variant="body2" fontFamily="monospace" fontSize="0.8rem">
          {a.path.split("/").pop()}
        </Typography>
      ),
    },
    {
      key: "format",
      label: t("llm_model_detail.format"),
      sortable: true,
      sortValue: (a) => a.format,
      render: (a) => <FormatChip value={a.format} />,
    },
    {
      key: "size",
      label: t("common.size"),
      sortable: true,
      sortValue: (a) => a.size_bytes,
      render: (a) => (
        <Typography variant="body2" fontSize="0.8rem">
          {humanSize(a.size_bytes)}
        </Typography>
      ),
    },
    {
      key: "engines",
      label: t("llm_model_detail.compatible_engines"),
      render: (a) => (
        <Stack direction="row" spacing={0.5}>
          {(a.engine_compat ?? []).map((eng) => (
            <Chip key={eng} label={eng} size="small" variant="outlined" />
          ))}
        </Stack>
      ),
    },
    {
      key: "sha256",
      label: "SHA-256",
      render: (a) =>
        a.sha256 ? (
          <Typography variant="body2" fontFamily="monospace" fontSize="0.7rem" color="text.secondary">
            {a.sha256.slice(0, 16)}…
          </Typography>
        ) : (
          <Typography variant="body2" color="text.disabled" fontSize="0.8rem">
            —
          </Typography>
        ),
    },
  ];

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 3, height: "100%", overflow: "auto" }}>
      {/* Header */}
      <Stack direction="row" alignItems="center" spacing={2}>
        <Button
          size="small"
          startIcon={<ArrowBackIcon />}
          onClick={() => navigate("/admin/llm/models")}
        >
          {t("llm_models.title")}
        </Button>
        <Typography variant="h5" sx={{ flexGrow: 1 }}>
          {model.display_name}
        </Typography>
        <ModelStatusChip value={model.status} />
        <Button
          size="small"
          color="error"
          variant="outlined"
          startIcon={<DeleteIcon />}
          disabled={model.status === "downloading"}
          onClick={handleDelete}
        >
          {t("common.delete")}
        </Button>
      </Stack>

      {/* Metadata card */}
      <Card variant="outlined">
        <CardContent>
          <Stack direction="row" flexWrap="wrap" gap={4}>
            <MetaField label={t("llm_common.source")} value={model.source} />
            {model.hf_repo_id && <MetaField label={t("llm_model_detail.repo")} value={model.hf_repo_id} mono />}
            {model.hf_revision && <MetaField label={t("llm_model_detail.revision")} value={model.hf_revision} mono />}
            <MetaField label={t("common.created")} value={new Date(model.created_at).toLocaleString()} />
            {model.tags && model.tags.length > 0 && (
              <Box>
                <Typography variant="caption" color="text.secondary">
                  {t("images.tags")}
                </Typography>
                <Stack direction="row" spacing={0.5} mt={0.5}>
                  {model.tags.map((t) => (
                    <Chip key={t} label={t} size="small" variant="outlined" />
                  ))}
                </Stack>
              </Box>
            )}
          </Stack>
        </CardContent>
      </Card>

      {/* Artifacts table */}
      <DataTable
        columns={artifactCols}
        rows={artifacts}
        rowKey={(a) => a.id}
        title={t("llm_model_detail.artifacts")}
        emptyMessage={t("llm_model_detail.no_artifacts")}
        onRefresh={load}
      />
    </Box>
  );
}

function MetaField({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <Box>
      <Typography variant="caption" color="text.secondary">
        {label}
      </Typography>
      <Typography
        variant="body2"
        fontWeight={500}
        fontFamily={mono ? "monospace" : undefined}
        fontSize={mono ? "0.8rem" : undefined}
      >
        {value}
      </Typography>
    </Box>
  );
}
