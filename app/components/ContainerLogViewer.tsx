/**
 * ContainerLogViewer — displays the last N log lines for a single
 * Docker container.  Used inside the ModulesTab to let admins
 * inspect container output without leaving the settings page.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import Box from "@mui/material/Box";
import CircularProgress from "@mui/material/CircularProgress";
import IconButton from "@mui/material/IconButton";
import MenuItem from "@mui/material/MenuItem";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import ContentCopyIcon from "@mui/icons-material/ContentCopy";
import RefreshIcon from "@mui/icons-material/Refresh";
import VerticalAlignBottomIcon from "@mui/icons-material/VerticalAlignBottom";

import { servicesApi } from "~/api/services";

interface ContainerLogViewerProps {
  /** Module name (e.g. "rag", "pii", "mailer"). */
  module: string;
  /** Docker container name (e.g. "llm-port-rag"). */
  containerName: string;
}

const TAIL_OPTIONS = [50, 100, 200, 500, 1000];

export default function ContainerLogViewer({
  module,
  containerName,
}: ContainerLogViewerProps) {
  const { t } = useTranslation();
  const [logs, setLogs] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tail, setTail] = useState(200);
  const [autoScroll, setAutoScroll] = useState(true);
  const logBoxRef = useRef<HTMLDivElement>(null);

  const fetchLogs = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const text = await servicesApi.containerLogs(module, containerName, tail);
      setLogs(text);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch logs.");
      setLogs("");
    } finally {
      setLoading(false);
    }
  }, [module, containerName, tail]);

  // Fetch on mount and when tail changes.
  useEffect(() => {
    void fetchLogs();
  }, [fetchLogs]);

  // Auto-scroll to bottom when logs change.
  useEffect(() => {
    if (autoScroll && logBoxRef.current) {
      logBoxRef.current.scrollTop = logBoxRef.current.scrollHeight;
    }
  }, [logs, autoScroll]);

  async function copyLogs() {
    try {
      await navigator.clipboard.writeText(logs);
    } catch {
      // clipboard not available
    }
  }

  return (
    <Box sx={{ mt: 1 }}>
      {/* Toolbar */}
      <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 0.5 }}>
        <Typography variant="caption" color="text.secondary" sx={{ flexGrow: 1 }}>
          {containerName}
        </Typography>

        <TextField
          select
          size="small"
          variant="standard"
          value={tail}
          onChange={(e) => setTail(Number(e.target.value))}
          sx={{ minWidth: 80 }}
          slotProps={{
            inputLabel: { shrink: true },
          }}
          label={t("logs.tail_label", { defaultValue: "Lines" })}
        >
          {TAIL_OPTIONS.map((n) => (
            <MenuItem key={n} value={n}>
              {n}
            </MenuItem>
          ))}
        </TextField>

        <Tooltip title={t("logs.scroll_bottom", { defaultValue: "Auto-scroll" })}>
          <IconButton
            size="small"
            color={autoScroll ? "primary" : "default"}
            onClick={() => setAutoScroll((prev) => !prev)}
          >
            <VerticalAlignBottomIcon fontSize="small" />
          </IconButton>
        </Tooltip>

        <Tooltip title={t("logs.copy", { defaultValue: "Copy logs" })}>
          <IconButton size="small" onClick={() => void copyLogs()} disabled={!logs}>
            <ContentCopyIcon fontSize="small" />
          </IconButton>
        </Tooltip>

        <Tooltip title={t("dashboard.refresh", { defaultValue: "Refresh" })}>
          <IconButton size="small" onClick={() => void fetchLogs()} disabled={loading}>
            <RefreshIcon fontSize="small" />
          </IconButton>
        </Tooltip>
      </Stack>

      {/* Log output */}
      <Box
        ref={logBoxRef}
        sx={{
          bgcolor: "grey.900",
          color: "grey.100",
          fontFamily: "monospace",
          fontSize: "0.75rem",
          lineHeight: 1.6,
          p: 1.5,
          borderRadius: 1,
          maxHeight: 360,
          overflow: "auto",
          whiteSpace: "pre-wrap",
          wordBreak: "break-all",
          position: "relative",
        }}
      >
        {loading && !logs && (
          <Box sx={{ display: "flex", justifyContent: "center", py: 2 }}>
            <CircularProgress size={20} sx={{ color: "grey.500" }} />
          </Box>
        )}
        {error && (
          <Typography variant="body2" sx={{ color: "error.light" }}>
            {error}
          </Typography>
        )}
        {!loading && !error && !logs && (
          <Typography variant="body2" sx={{ color: "grey.500" }}>
            {t("logs.empty", { defaultValue: "No log output." })}
          </Typography>
        )}
        {logs}
      </Box>
    </Box>
  );
}
