/**
 * TokenUsageBadge — compact chip showing token counts for a message.
 */
import Chip from "@mui/material/Chip";
import Tooltip from "@mui/material/Tooltip";
import { useTranslation } from "react-i18next";

import type { TokenUsage } from "~/api/chatTypes";

interface Props {
  usage: TokenUsage;
}

function fmtNum(n: number): string {
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
}

export default function TokenUsageBadge({ usage }: Props) {
  const { t } = useTranslation();
  const label = `${fmtNum(usage.total_tokens)} ${t("token_suffix", {
    ns: "chat",
    defaultValue: "tok",
  })}`;
  const detail = t("chat.token_detail", {
    defaultValue:
      "Prompt: {{prompt}} · Completion: {{completion}} · Total: {{total}}",
    prompt: usage.prompt_tokens.toLocaleString(),
    completion: usage.completion_tokens.toLocaleString(),
    total: usage.total_tokens.toLocaleString(),
  });

  return (
    <Tooltip title={detail} arrow>
      <Chip
        label={label}
        size="small"
        variant="outlined"
        sx={{ fontSize: "0.7rem", height: 20, opacity: 0.7 }}
      />
    </Tooltip>
  );
}
