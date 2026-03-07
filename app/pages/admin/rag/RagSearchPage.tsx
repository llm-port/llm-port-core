import { useState } from "react";
import { useTranslation } from "react-i18next";
import {
  ragKnowledge,
  ragLite,
  type RagKnowledgeSearchResponse,
  type RagLiteSearchResponse,
  type RagSearchMode,
} from "~/api/rag";
import { useRagMode } from "~/lib/useRagMode";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import Checkbox from "@mui/material/Checkbox";
import FormControl from "@mui/material/FormControl";
import FormControlLabel from "@mui/material/FormControlLabel";
import InputLabel from "@mui/material/InputLabel";
import MenuItem from "@mui/material/MenuItem";
import Select from "@mui/material/Select";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

function splitCsv(value: string): string[] {
  return value
    .split(",")
    .map((chunk) => chunk.trim())
    .filter((chunk) => chunk.length > 0);
}

// ── Lite search ────────────────────────────────────────────────────

function RagLiteSearch() {
  const { t } = useTranslation();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [response, setResponse] = useState<RagLiteSearchResponse | null>(null);
  const [query, setQuery] = useState("");
  const [topK, setTopK] = useState(5);

  async function runSearch(event: React.FormEvent) {
    event.preventDefault();
    setLoading(true);
    setError(null);
    setResponse(null);
    try {
      const result = await ragLite.search({ query, top_k: topK });
      setResponse(result);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t("rag_search.failed"));
    } finally {
      setLoading(false);
    }
  }

  return (
    <Stack spacing={2}>
      <Typography variant="h5">{t("rag_search.title")}</Typography>
      <Alert severity="info">{t("rag_search.description")}</Alert>
      {error && <Alert severity="error">{error}</Alert>}

      <Card variant="outlined">
        <CardContent>
          <form onSubmit={runSearch}>
            <Stack spacing={2}>
              <TextField
                label={t("rag_search.query")}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                required
                fullWidth
                multiline
                minRows={2}
              />
              <Stack direction="row" spacing={2} alignItems="center">
                <TextField
                  label={t("rag_search.top_k")}
                  type="number"
                  value={topK}
                  onChange={(e) =>
                    setTopK(
                      Math.max(1, Math.min(50, Number(e.target.value) || 1)),
                    )
                  }
                  sx={{ width: 140 }}
                />
                <Box sx={{ flexGrow: 1 }} />
                <Button type="submit" variant="contained" disabled={loading}>
                  {loading ? t("common.loading") : t("rag_search.search")}
                </Button>
              </Stack>
            </Stack>
          </form>
        </CardContent>
      </Card>

      {response && (
        <Stack spacing={1.5}>
          <Typography variant="h6">
            {t("rag_search.results", { count: response.results.length })}
          </Typography>
          {response.results.map((result, index) => (
            <Card
              key={`${result.document_id}-${result.chunk_index}`}
              variant="outlined"
            >
              <CardContent>
                <Stack spacing={0.75}>
                  <Typography variant="body2" color="text.secondary">
                    {result.filename} • chunk {result.chunk_index} • score{" "}
                    {result.score.toFixed(4)}
                  </Typography>
                  <Typography variant="body2" sx={{ whiteSpace: "pre-wrap" }}>
                    {result.chunk_text}
                  </Typography>
                </Stack>
              </CardContent>
            </Card>
          ))}
        </Stack>
      )}
    </Stack>
  );
}

// ── Pro search ─────────────────────────────────────────────────────

function RagProSearch() {
  const { t } = useTranslation();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [response, setResponse] = useState<RagKnowledgeSearchResponse | null>(
    null,
  );

  const [tenantId, setTenantId] = useState("tenant-demo");
  const [workspaceId, setWorkspaceId] = useState("");
  const [query, setQuery] = useState("");
  const [userId, setUserId] = useState("user-demo");
  const [groupIds, setGroupIds] = useState("");
  const [sources, setSources] = useState("");
  const [tags, setTags] = useState("");
  const [docTypes, setDocTypes] = useState("");
  const [topK, setTopK] = useState(5);
  const [mode, setMode] = useState<RagSearchMode>("hybrid");
  const [debug, setDebug] = useState(true);

  async function runSearch(event: React.FormEvent) {
    event.preventDefault();
    setLoading(true);
    setError(null);
    setResponse(null);
    try {
      const result = await ragKnowledge.search({
        tenant_id: tenantId,
        workspace_id: workspaceId || null,
        query,
        principals: {
          user_id: userId,
          group_ids: splitCsv(groupIds),
        },
        filters: {
          sources: splitCsv(sources),
          tags: splitCsv(tags),
          doc_types: splitCsv(docTypes),
          container_ids: [],
          include_descendants: true,
          source_kind: null,
          asset_ids: [],
          time_from: null,
          time_to: null,
        },
        top_k: topK,
        mode,
        debug,
      });
      setResponse(result);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t("rag_search.failed"));
    } finally {
      setLoading(false);
    }
  }

  return (
    <Stack spacing={2}>
      <Typography variant="h5">{t("rag_search.title")}</Typography>
      <Alert severity="info">{t("rag_search.description")}</Alert>
      {error && <Alert severity="error">{error}</Alert>}

      <Card variant="outlined">
        <CardContent>
          <form onSubmit={runSearch}>
            <Stack spacing={2}>
              <Stack direction={{ xs: "column", md: "row" }} spacing={2}>
                <TextField
                  label={t("rag_search.tenant_id")}
                  value={tenantId}
                  onChange={(e) => setTenantId(e.target.value)}
                  required
                  fullWidth
                />
                <TextField
                  label={t("rag_search.workspace_id")}
                  value={workspaceId}
                  onChange={(e) => setWorkspaceId(e.target.value)}
                  fullWidth
                />
              </Stack>

              <TextField
                label={t("rag_search.query")}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                required
                fullWidth
                multiline
                minRows={2}
              />

              <Stack direction={{ xs: "column", md: "row" }} spacing={2}>
                <TextField
                  label={t("rag_search.user_id")}
                  value={userId}
                  onChange={(e) => setUserId(e.target.value)}
                  required
                  fullWidth
                />
                <TextField
                  label={t("rag_search.group_ids")}
                  value={groupIds}
                  onChange={(e) => setGroupIds(e.target.value)}
                  fullWidth
                />
              </Stack>

              <Stack direction={{ xs: "column", md: "row" }} spacing={2}>
                <TextField
                  label={t("rag_search.sources")}
                  value={sources}
                  onChange={(e) => setSources(e.target.value)}
                  fullWidth
                />
                <TextField
                  label={t("rag_search.tags")}
                  value={tags}
                  onChange={(e) => setTags(e.target.value)}
                  fullWidth
                />
                <TextField
                  label={t("rag_search.doc_types")}
                  value={docTypes}
                  onChange={(e) => setDocTypes(e.target.value)}
                  fullWidth
                />
              </Stack>

              <Stack
                direction={{ xs: "column", md: "row" }}
                spacing={2}
                alignItems="center"
              >
                <TextField
                  label={t("rag_search.top_k")}
                  type="number"
                  value={topK}
                  onChange={(e) =>
                    setTopK(
                      Math.max(1, Math.min(50, Number(e.target.value) || 1)),
                    )
                  }
                  sx={{ width: 140 }}
                />
                <FormControl sx={{ minWidth: 180 }}>
                  <InputLabel>{t("rag_search.mode")}</InputLabel>
                  <Select
                    value={mode}
                    label={t("rag_search.mode")}
                    onChange={(e) => setMode(e.target.value as RagSearchMode)}
                  >
                    <MenuItem value="vector">vector</MenuItem>
                    <MenuItem value="keyword">keyword</MenuItem>
                    <MenuItem value="hybrid">hybrid</MenuItem>
                  </Select>
                </FormControl>
                <FormControlLabel
                  control={
                    <Checkbox
                      checked={debug}
                      onChange={(e) => setDebug(e.target.checked)}
                    />
                  }
                  label={t("rag_search.debug")}
                />
                <Box sx={{ flexGrow: 1 }} />
                <Button type="submit" variant="contained" disabled={loading}>
                  {loading ? t("common.loading") : t("rag_search.search")}
                </Button>
              </Stack>
            </Stack>
          </form>
        </CardContent>
      </Card>

      {response && (
        <Stack spacing={1.5}>
          <Typography variant="h6">
            {t("rag_search.results", { count: response.results.length })}
          </Typography>
          {response.results.map((result, index) => (
            <Card key={`${result.source_uri}-${index}`} variant="outlined">
              <CardContent>
                <Stack spacing={0.75}>
                  <Typography variant="body2" color="text.secondary">
                    {result.doc_title ?? t("common.none")} • {result.source_uri}{" "}
                    • score {result.score.toFixed(4)}
                  </Typography>
                  {result.section && (
                    <Typography variant="caption" color="text.secondary">
                      {result.section}
                    </Typography>
                  )}
                  <Typography variant="body2" sx={{ whiteSpace: "pre-wrap" }}>
                    {result.chunk_text}
                  </Typography>
                </Stack>
              </CardContent>
            </Card>
          ))}

          {response.debug && (
            <Card variant="outlined">
              <CardContent>
                <Typography variant="subtitle2" sx={{ mb: 1 }}>
                  {t("rag_search.debug_payload")}
                </Typography>
                <Box
                  component="pre"
                  sx={{
                    m: 0,
                    p: 1.5,
                    overflow: "auto",
                    bgcolor: "action.hover",
                    borderRadius: 1,
                    fontSize: "0.78rem",
                  }}
                >
                  {JSON.stringify(response.debug, null, 2)}
                </Box>
              </CardContent>
            </Card>
          )}
        </Stack>
      )}
    </Stack>
  );
}

// ── Exported page — delegates to the correct mode ──────────────────

export default function RagSearchPage() {
  const { mode } = useRagMode();
  if (mode === "lite") return <RagLiteSearch />;
  return <RagProSearch />;
}
