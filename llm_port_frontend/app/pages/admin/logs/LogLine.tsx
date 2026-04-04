import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Collapse from "@mui/material/Collapse";
import IconButton from "@mui/material/IconButton";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";

import ContentCopyIcon from "@mui/icons-material/ContentCopy";
import ExpandLessIcon from "@mui/icons-material/ExpandLess";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";

import type { LogEntry } from "~/api/logs";

interface LogLineProps {
  entry: LogEntry;
  labels?: Record<string, string>;
}

export default function LogLine({ entry, labels }: LogLineProps) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  const [copied, setCopied] = useState(false);
  const labelsText = useMemo(
    () => (labels ? Object.entries(labels).map(([k, v]) => `${k}=${v}`).join(" ") : ""),
    [labels],
  );

  async function copyLine() {
    await navigator.clipboard.writeText(entry.line);
    setCopied(true);
    setTimeout(() => setCopied(false), 1000);
  }

  return (
    <Paper variant="outlined" sx={{ p: 1.25 }}>
      <Stack direction="row" spacing={1} alignItems="flex-start">
        <Box sx={{ minWidth: 185, flexShrink: 0 }}>
          <Typography variant="caption" color="text.secondary" fontFamily="monospace">
            {new Date(entry.ts).toLocaleTimeString()}
          </Typography>
          {labelsText && (
            <Typography variant="caption" color="text.disabled" sx={{ display: "block" }} noWrap>
              {labelsText}
            </Typography>
          )}
        </Box>
        <Box sx={{ flexGrow: 1, minWidth: 0 }}>
          <Typography variant="body2" fontFamily="monospace" sx={{ whiteSpace: "pre-wrap" }}>
            {entry.line}
          </Typography>
          {entry.structured && (
            <Collapse in={expanded}>
              <Box component="pre" sx={{ mb: 0, mt: 1, fontSize: "0.75rem", whiteSpace: "pre-wrap" }}>
                {JSON.stringify(entry.structured, null, 2)}
              </Box>
            </Collapse>
          )}
        </Box>
        <Stack direction="row" spacing={0.5}>
          {entry.structured && (
            <IconButton size="small" onClick={() => setExpanded((v) => !v)}>
              {expanded ? <ExpandLessIcon fontSize="small" /> : <ExpandMoreIcon fontSize="small" />}
            </IconButton>
          )}
          <Tooltip title={copied ? t("logs.copied") : t("logs.copy_line")}>
            <Button size="small" variant="text" onClick={copyLine} startIcon={<ContentCopyIcon fontSize="small" />}>
              {t("logs.copy")}
            </Button>
          </Tooltip>
        </Stack>
      </Stack>
    </Paper>
  );
}
