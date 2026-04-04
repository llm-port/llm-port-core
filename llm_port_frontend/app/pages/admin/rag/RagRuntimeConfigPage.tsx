import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  ragRuntime,
  ragLite,
  type RagEmbeddingProvider,
  type RagRuntimeConfigPayload,
  type RagLiteConfigDTO,
} from "~/api/rag";
import { useRagMode } from "~/lib/useRagMode";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import CircularProgress from "@mui/material/CircularProgress";
import FormControl from "@mui/material/FormControl";
import InputLabel from "@mui/material/InputLabel";
import MenuItem from "@mui/material/MenuItem";
import Select from "@mui/material/Select";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

// ── Lite runtime config (read-only) ───────────────────────────────

function RagLiteRuntimeConfig() {
  const { t } = useTranslation();
  const [loading, setLoading] = useState(true);
  const [config, setConfig] = useState<RagLiteConfigDTO | null>(null);
  const [health, setHealth] = useState<string>("unknown");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const h = await ragRuntime.health();
        if (!cancelled) setHealth(h.status);
      } catch {
        if (!cancelled) setHealth("unreachable");
      }
      try {
        const c = await ragLite.config();
        if (!cancelled) setConfig(c);
      } catch {
        // config may not be available yet
      }
      if (!cancelled) setLoading(false);
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  if (loading) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", py: 6 }}>
        <CircularProgress />
      </Box>
    );
  }

  return (
    <Stack spacing={2}>
      <Stack direction="row" justifyContent="space-between" alignItems="center">
        <Typography variant="h5">{t("rag_runtime.title")}</Typography>
        <Typography variant="body2" color="text.secondary">
          {t("rag_runtime.health")}: {health}
        </Typography>
      </Stack>

      <Alert severity="info">{t("rag_runtime.lite_settings_hint")}</Alert>

      {config && (
        <Card variant="outlined">
          <CardContent>
            <Stack spacing={1.5}>
              <Typography variant="subtitle2">
                {t("rag_runtime.embedding_model")}
              </Typography>
              <Typography variant="body2" color="text.secondary">
                {config.embedding_model || "—"} (dim {config.embedding_dim})
              </Typography>
              <Typography variant="subtitle2">
                {t("rag_runtime.max_tokens")}
              </Typography>
              <Typography variant="body2" color="text.secondary">
                {config.chunk_max_tokens} ({t("rag_runtime.overlap")}:{" "}
                {config.chunk_overlap_tokens})
              </Typography>
              <Typography variant="subtitle2">
                {t("rag_runtime.file_store")}
              </Typography>
              <Typography variant="body2" color="text.secondary">
                {config.file_store_root} • max {config.upload_max_file_mb} MB
              </Typography>
            </Stack>
          </CardContent>
        </Card>
      )}
    </Stack>
  );
}

// ── Pro runtime config (full form) ────────────────────────────────

function defaultPayload(): RagRuntimeConfigPayload {
  return {
    embedding_provider: "openai_compat",
    embedding_model: "",
    embedding_base_url: "",
    embedding_api_key_ref: "",
    embedding_dim: 1024,
    chunking_policy: {
      max_tokens: 512,
      overlap: 64,
      by_headings: false,
    },
  };
}

function RagProRuntimeConfig() {
  const { t } = useTranslation();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [health, setHealth] = useState<string>("unknown");
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState<string | null>(null);
  const [embeddingApiKey, setEmbeddingApiKey] = useState("");
  const [payload, setPayload] =
    useState<RagRuntimeConfigPayload>(defaultPayload);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const healthStatus = await ragRuntime.health();
      setHealth(healthStatus.status);
    } catch {
      setHealth("unreachable");
    }

    try {
      const config = await ragRuntime.getConfig();
      setPayload(config.payload);
      setUpdatedAt(config.updated_at);
    } catch {
      setPayload(defaultPayload());
      setUpdatedAt(null);
    }

    setLoading(false);
  }

  useEffect(() => {
    void load();
  }, []);

  async function handleSave(event: React.FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    setSuccess(null);
    try {
      const response = await ragRuntime.updateConfig({
        payload: {
          ...payload,
          embedding_base_url: payload.embedding_base_url || null,
          embedding_api_key_ref: payload.embedding_api_key_ref || null,
        },
        embedding_api_key: embeddingApiKey || null,
      });
      setPayload(response.payload);
      setUpdatedAt(response.updated_at);
      setEmbeddingApiKey("");
      setSuccess(t("rag_runtime.saved"));
    } catch (err: unknown) {
      setError(
        err instanceof Error ? err.message : t("rag_runtime.failed_save"),
      );
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", py: 6 }}>
        <CircularProgress />
      </Box>
    );
  }

  return (
    <Stack spacing={2}>
      <Stack direction="row" justifyContent="space-between" alignItems="center">
        <Typography variant="h5">{t("rag_runtime.title")}</Typography>
        <Stack direction="row" spacing={1} alignItems="center">
          <Typography variant="body2" color="text.secondary">
            {t("rag_runtime.health")}: {health}
          </Typography>
          <Button
            variant="outlined"
            onClick={() => void load()}
            disabled={saving}
          >
            {t("dashboard.refresh")}
          </Button>
        </Stack>
      </Stack>

      <Alert severity="info">{t("rag_runtime.description")}</Alert>
      {error && <Alert severity="error">{error}</Alert>}
      {success && <Alert severity="success">{success}</Alert>}

      <Card variant="outlined">
        <CardContent>
          <form onSubmit={handleSave}>
            <Stack spacing={2}>
              <FormControl fullWidth>
                <InputLabel>{t("rag_runtime.embedding_provider")}</InputLabel>
                <Select
                  value={payload.embedding_provider}
                  label={t("rag_runtime.embedding_provider")}
                  onChange={(e) =>
                    setPayload((prev) => ({
                      ...prev,
                      embedding_provider: e.target
                        .value as RagEmbeddingProvider,
                    }))
                  }
                >
                  <MenuItem value="openai_compat">openai_compat</MenuItem>
                  <MenuItem value="local">local</MenuItem>
                  <MenuItem value="huggingface">huggingface</MenuItem>
                </Select>
              </FormControl>

              <TextField
                label={t("rag_runtime.embedding_model")}
                value={payload.embedding_model}
                onChange={(e) =>
                  setPayload((prev) => ({
                    ...prev,
                    embedding_model: e.target.value,
                  }))
                }
                required
                fullWidth
              />

              <TextField
                label={t("rag_runtime.embedding_base_url")}
                value={payload.embedding_base_url ?? ""}
                onChange={(e) =>
                  setPayload((prev) => ({
                    ...prev,
                    embedding_base_url: e.target.value,
                  }))
                }
                fullWidth
              />

              <TextField
                label={t("rag_runtime.embedding_api_key_ref")}
                value={payload.embedding_api_key_ref ?? ""}
                onChange={(e) =>
                  setPayload((prev) => ({
                    ...prev,
                    embedding_api_key_ref: e.target.value,
                  }))
                }
                fullWidth
              />

              <TextField
                label={t("rag_runtime.embedding_api_key")}
                type="password"
                value={embeddingApiKey}
                onChange={(e) => setEmbeddingApiKey(e.target.value)}
                fullWidth
              />

              <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
                <TextField
                  label={t("rag_runtime.embedding_dim")}
                  type="number"
                  value={payload.embedding_dim}
                  onChange={(e) =>
                    setPayload((prev) => ({
                      ...prev,
                      embedding_dim:
                        Number(e.target.value) || prev.embedding_dim,
                    }))
                  }
                  fullWidth
                />
                <TextField
                  label={t("rag_runtime.max_tokens")}
                  type="number"
                  value={payload.chunking_policy.max_tokens}
                  onChange={(e) =>
                    setPayload((prev) => ({
                      ...prev,
                      chunking_policy: {
                        ...prev.chunking_policy,
                        max_tokens:
                          Number(e.target.value) ||
                          prev.chunking_policy.max_tokens,
                      },
                    }))
                  }
                  fullWidth
                />
                <TextField
                  label={t("rag_runtime.overlap")}
                  type="number"
                  value={payload.chunking_policy.overlap}
                  onChange={(e) =>
                    setPayload((prev) => ({
                      ...prev,
                      chunking_policy: {
                        ...prev.chunking_policy,
                        overlap:
                          Number(e.target.value) ||
                          prev.chunking_policy.overlap,
                      },
                    }))
                  }
                  fullWidth
                />
              </Stack>

              <FormControl fullWidth>
                <InputLabel>{t("rag_runtime.by_headings")}</InputLabel>
                <Select
                  value={payload.chunking_policy.by_headings ? "true" : "false"}
                  label={t("rag_runtime.by_headings")}
                  onChange={(e) =>
                    setPayload((prev) => ({
                      ...prev,
                      chunking_policy: {
                        ...prev.chunking_policy,
                        by_headings: e.target.value === "true",
                      },
                    }))
                  }
                >
                  <MenuItem value="false">{t("common.no")}</MenuItem>
                  <MenuItem value="true">{t("common.yes")}</MenuItem>
                </Select>
              </FormControl>

              <Stack
                direction="row"
                justifyContent="space-between"
                alignItems="center"
              >
                <Typography variant="body2" color="text.secondary">
                  {updatedAt
                    ? `${t("rag_runtime.last_updated")}: ${new Date(updatedAt).toLocaleString()}`
                    : t("rag_runtime.not_configured")}
                </Typography>
                <Button type="submit" variant="contained" disabled={saving}>
                  {saving ? t("common.loading") : t("common.save")}
                </Button>
              </Stack>
            </Stack>
          </form>
        </CardContent>
      </Card>
    </Stack>
  );
}

// ── Exported page — delegates to the correct mode ──────────────────

export default function RagRuntimeConfigPage() {
  const { mode } = useRagMode();
  if (mode === "lite") return <RagLiteRuntimeConfig />;
  return <RagProRuntimeConfig />;
}
