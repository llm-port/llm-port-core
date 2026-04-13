/**
 * ExecutionModeSelector — dropdown to set the session execution mode.
 *
 * Allows users to choose between Local Only, Server Only, and Hybrid modes
 * for tool execution within the current chat session.
 */
import { useCallback } from "react";
import FormControl from "@mui/material/FormControl";
import InputLabel from "@mui/material/InputLabel";
import MenuItem from "@mui/material/MenuItem";
import Select from "@mui/material/Select";
import Tooltip from "@mui/material/Tooltip";
import type { SelectChangeEvent } from "@mui/material/Select";
import ComputerIcon from "@mui/icons-material/Computer";
import CloudIcon from "@mui/icons-material/Cloud";
import SyncAltIcon from "@mui/icons-material/SyncAlt";
import { useTranslation } from "react-i18next";

import type { ExecutionMode } from "~/api/tools";
import { patchSessionToolPolicy } from "~/api/tools";

interface Props {
  sessionId: string | null;
  value: ExecutionMode;
  onChange: (mode: ExecutionMode) => void;
  size?: "small" | "medium";
  disabled?: boolean;
}

const MODE_ICONS: Record<ExecutionMode, React.ReactElement> = {
  local_only: <ComputerIcon fontSize="small" />,
  server_only: <CloudIcon fontSize="small" />,
  hybrid: <SyncAltIcon fontSize="small" />,
};

const MODE_LABELS: Record<ExecutionMode, string> = {
  local_only: "Local Only",
  server_only: "Server Only",
  hybrid: "Hybrid",
};

const MODE_DESCRIPTIONS: Record<ExecutionMode, string> = {
  local_only: "Only tools on your local device are available",
  server_only: "Only server-managed and MCP tools are available",
  hybrid: "Both local and server tools are available",
};

export default function ExecutionModeSelector({
  sessionId,
  value,
  onChange,
  size = "small",
  disabled = false,
}: Props) {
  const { t } = useTranslation();

  const handleChange = useCallback(
    async (event: SelectChangeEvent<string>) => {
      const mode = event.target.value as ExecutionMode;
      onChange(mode);
      if (sessionId) {
        try {
          await patchSessionToolPolicy(sessionId, { execution_mode: mode });
        } catch {
          // Revert on failure — caller can refresh
        }
      }
    },
    [sessionId, onChange],
  );

  return (
    <FormControl size={size} sx={{ minWidth: 140 }}>
      <InputLabel id="exec-mode-label">
        {t("tools.executionMode", "Execution")}
      </InputLabel>
      <Select
        labelId="exec-mode-label"
        value={value}
        label={t("tools.executionMode", "Execution")}
        onChange={handleChange}
        disabled={disabled || !sessionId}
        renderValue={(selected) => (
          <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
            {MODE_ICONS[selected as ExecutionMode]}
            {MODE_LABELS[selected as ExecutionMode] ?? selected}
          </span>
        )}
      >
        {(["server_only", "local_only", "hybrid"] as ExecutionMode[]).map(
          (mode) => (
            <MenuItem key={mode} value={mode}>
              <Tooltip title={MODE_DESCRIPTIONS[mode]} placement="right">
                <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  {MODE_ICONS[mode]}
                  {MODE_LABELS[mode]}
                </span>
              </Tooltip>
            </MenuItem>
          ),
        )}
      </Select>
    </FormControl>
  );
}
