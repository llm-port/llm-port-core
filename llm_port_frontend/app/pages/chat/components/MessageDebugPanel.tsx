/**
 * MessageDebugPanel — right-side drawer showing request debug info (cost, tokens, pipeline).
 *
 * Only rendered when the user has `chat.debug:read` RBAC permission.
 * Fetches data by trace_id from the observability API.
 */
import type React from "react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import Box from "@mui/material/Box";
import CircularProgress from "@mui/material/CircularProgress";
import Chip from "@mui/material/Chip";
import Drawer from "@mui/material/Drawer";
import IconButton from "@mui/material/IconButton";
import Stack from "@mui/material/Stack";
import Tab from "@mui/material/Tab";
import Tabs from "@mui/material/Tabs";
import Typography from "@mui/material/Typography";
import CloseIcon from "@mui/icons-material/Close";

import {
  observability,
  type RequestLog,
  type ToolCallLog,
} from "~/api/observability";
import PipelineGraph from "~/components/PipelineGraph";

const DRAWER_WIDTH = 420;

interface Props {
  traceId: string;
  open: boolean;
  onClose: () => void;
}

function fmtCost(v: number | string | null | undefined): string {
  if (v == null) return "—";
  return `$${Number(v).toFixed(6)}`;
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <Box sx={{ minWidth: 100 }}>
      <Typography variant="caption" color="text.secondary">
        {label}
      </Typography>
      <Typography variant="body2" sx={{ wordBreak: "break-all" }}>
        {value}
      </Typography>
    </Box>
  );
}

export default function MessageDebugPanel({ traceId, open, onClose }: Props) {
  const { t } = useTranslation();
  const [request, setRequest] = useState<RequestLog | null>(null);
  const [toolCalls, setToolCalls] = useState<ToolCallLog[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState(0);

  useEffect(() => {
    if (!open || !traceId) return;

    let cancelled = false;
    setLoading(true);
    setError(null);
    setRequest(null);
    setToolCalls(null);
    setTab(0);

    observability
      .requestByTrace(traceId)
      .then((data) => {
        if (!cancelled) setRequest(data);
      })
      .catch((err: unknown) => {
        if (!cancelled)
          setError(
            err instanceof Error ? err.message : "Failed to load debug info",
          );
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [open, traceId]);

  // Lazy-load tool calls when pipeline tab is first opened
  useEffect(() => {
    if (tab !== 1 || !request || toolCalls !== null) return;

    observability
      .toolCalls(request.request_id)
      .then(setToolCalls)
      .catch(() => setToolCalls([]));
  }, [tab, request, toolCalls]);

  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={onClose}
      PaperProps={{ sx: { width: DRAWER_WIDTH, maxWidth: "90vw" } }}
    >
      {/* Header */}
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          px: 2,
          py: 1.5,
          borderBottom: 1,
          borderColor: "divider",
        }}
      >
        <Typography variant="subtitle1" fontWeight={600}>
          {t("chat.debug_panel_title", "Request Debug")}
        </Typography>
        <IconButton size="small" onClick={onClose}>
          <CloseIcon fontSize="small" />
        </IconButton>
      </Box>

      {/* Body */}
      {loading && (
        <Box sx={{ display: "flex", justifyContent: "center", py: 6 }}>
          <CircularProgress size={28} />
        </Box>
      )}

      {error && (
        <Box sx={{ px: 2, py: 4 }}>
          <Typography color="error" variant="body2">
            {error}
          </Typography>
        </Box>
      )}

      {!loading && !error && request && (
        <>
          <Tabs
            value={tab}
            onChange={(_: React.SyntheticEvent, v: number) => setTab(v)}
            sx={{ px: 2, minHeight: 36 }}
            TabIndicatorProps={{ sx: { height: 2 } }}
          >
            <Tab
              label={t("observability.tab_details", "Details")}
              sx={{ minHeight: 36, textTransform: "none", fontSize: 13 }}
            />
            <Tab
              label={t("observability.tab_pipeline", "Pipeline")}
              sx={{ minHeight: 36, textTransform: "none", fontSize: 13 }}
            />
          </Tabs>

          {/* Details tab */}
          {tab === 0 && (
            <Box sx={{ p: 2, overflowY: "auto" }}>
              {/* Status chip */}
              <Box sx={{ mb: 2 }}>
                <Chip
                  label={`${request.status_code}`}
                  size="small"
                  color={
                    request.status_code >= 200 && request.status_code < 300
                      ? "success"
                      : request.status_code >= 400 && request.status_code < 500
                        ? "warning"
                        : "error"
                  }
                />
              </Box>

              <Stack spacing={1.5}>
                <Detail
                  label={t("observability.request_id", "Request ID")}
                  value={request.request_id}
                />
                <Detail
                  label={t("observability.trace_id", "Trace ID")}
                  value={request.trace_id ?? "—"}
                />
                <Detail
                  label={t("observability.provider", "Provider")}
                  value={request.provider_instance_id ?? "—"}
                />
                <Detail
                  label={t("observability.endpoint", "Endpoint")}
                  value={request.endpoint}
                />
                <Detail
                  label={t("observability.stream", "Stream")}
                  value={request.stream != null ? String(request.stream) : "—"}
                />

                {/* Latency */}
                <Detail
                  label={t("observability.col_latency", "Latency")}
                  value={`${request.latency_ms} ms`}
                />
                <Detail
                  label={t("observability.ttft", "TTFT")}
                  value={
                    request.ttft_ms != null ? `${request.ttft_ms} ms` : "—"
                  }
                />

                {/* Tokens */}
                <Detail
                  label={t("observability.prompt_tokens", "Prompt tokens")}
                  value={String(request.prompt_tokens ?? "—")}
                />
                <Detail
                  label={t(
                    "observability.completion_tokens",
                    "Completion tokens",
                  )}
                  value={String(request.completion_tokens ?? "—")}
                />
                <Detail
                  label={t("observability.cached_tokens", "Cached tokens")}
                  value={String(request.cached_tokens ?? "—")}
                />

                {/* Cost */}
                <Detail
                  label={t("observability.input_cost", "Input cost")}
                  value={fmtCost(request.estimated_input_cost)}
                />
                <Detail
                  label={t("observability.output_cost", "Output cost")}
                  value={fmtCost(request.estimated_output_cost)}
                />
                <Detail
                  label={t("observability.total_cost", "Total cost")}
                  value={fmtCost(request.estimated_total_cost)}
                />
                <Detail
                  label={t("observability.currency", "Currency")}
                  value={request.currency ?? "—"}
                />
                <Detail
                  label={t("observability.estimate_status", "Estimate status")}
                  value={request.cost_estimate_status ?? "—"}
                />

                {/* Misc */}
                <Detail
                  label={t("observability.session_id", "Session ID")}
                  value={request.session_id ?? "—"}
                />
                <Detail
                  label={t("observability.finish_reason", "Finish reason")}
                  value={request.finish_reason ?? "—"}
                />
                <Detail
                  label={t("observability.retry_count", "Retry count")}
                  value={String(request.retry_count ?? 0)}
                />
                <Detail
                  label={t("observability.mcp_tool_calls", "MCP tool calls")}
                  value={String(request.mcp_tool_call_count ?? 0)}
                />
                <Detail
                  label={t("observability.mcp_iterations", "MCP iterations")}
                  value={String(request.mcp_tool_loop_iterations ?? 0)}
                />

                {request.skills_used && request.skills_used.length > 0 && (
                  <Detail
                    label={t("observability.skills_used", "Skills used")}
                    value={request.skills_used.map((s) => s.name).join(", ")}
                  />
                )}
                {request.rag_context && (
                  <Detail
                    label={t("observability.rag_context", "RAG context")}
                    value={`${request.rag_context.chunk_count} chunks (top_k=${request.rag_context.top_k})`}
                  />
                )}
                {request.error_code && (
                  <Detail
                    label={t("observability.error", "Error")}
                    value={request.error_code}
                  />
                )}
              </Stack>
            </Box>
          )}

          {/* Pipeline tab */}
          {tab === 1 && (
            <Box sx={{ p: 2, overflowY: "auto" }}>
              {toolCalls === null ? (
                <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
                  <CircularProgress size={24} />
                </Box>
              ) : (
                <PipelineGraph
                  request={request}
                  toolCalls={toolCalls.length > 0 ? toolCalls : undefined}
                />
              )}
            </Box>
          )}
        </>
      )}
    </Drawer>
  );
}
