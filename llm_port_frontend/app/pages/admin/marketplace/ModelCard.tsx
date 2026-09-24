/**
 * One model in the marketplace: what it is, what it can do, whether it fits.
 */
import { useTranslation } from "react-i18next";

import type { MarketModel } from "~/api/marketplace";
import { formatBytes } from "~/lib/engine";
import { fitSummary, formatCount, formatParams } from "~/lib/hosting";

import Avatar from "@mui/material/Avatar";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Card from "@mui/material/Card";
import CardActionArea from "@mui/material/CardActionArea";
import Chip from "@mui/material/Chip";
import Divider from "@mui/material/Divider";
import Stack from "@mui/material/Stack";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";

import BuildIcon from "@mui/icons-material/Build";
import CodeIcon from "@mui/icons-material/Code";
import DownloadIcon from "@mui/icons-material/Download";
import FavoriteBorderIcon from "@mui/icons-material/FavoriteBorder";
import ImageIcon from "@mui/icons-material/Image";
import LockIcon from "@mui/icons-material/Lock";
import PsychologyIcon from "@mui/icons-material/Psychology";
import ScatterPlotIcon from "@mui/icons-material/ScatterPlot";
import StorageIcon from "@mui/icons-material/Storage";
import VerifiedIcon from "@mui/icons-material/Verified";

const CAPABILITY_ICONS: Record<string, React.ReactElement> = {
  tools: <BuildIcon />,
  reasoning: <PsychologyIcon />,
  vision: <ImageIcon />,
  code: <CodeIcon />,
  embedding: <ScatterPlotIcon />,
};

export const CAPABILITIES = ["tools", "reasoning", "vision", "code", "embedding"] as const;

/** A small chip per capability, in a fixed order. */
export function CapabilityChips({ capabilities }: { capabilities: string[] }) {
  const { t } = useTranslation();
  const shown = CAPABILITIES.filter((c) => capabilities.includes(c));
  if (shown.length === 0) return null;
  return (
    <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap>
      {shown.map((c) => (
        <Tooltip key={c} title={t(`marketplace.capability_help.${c}`)}>
          <Chip size="small" variant="outlined" icon={CAPABILITY_ICONS[c]} label={t(`marketplace.capability.${c}`)} />
        </Tooltip>
      ))}
    </Stack>
  );
}

const TONE = { success: "success", info: "info", warning: "warning", error: "error", default: "default" } as const;

/** The fit on the chosen cluster, as a chip. */
export function FitChip({ model }: { model: MarketModel }) {
  const { t } = useTranslation();
  if (!model.runnable) {
    return (
      <Chip
        size="small"
        label={t("marketplace.not_runnable_short")}
        variant="outlined"
        data-testid={`fit-${model.repo_id}`}
      />
    );
  }
  if (!model.fit) return null;
  const { label, tone } = fitSummary(model.fit);
  return <Chip size="small" label={label} color={TONE[tone]} data-testid={`fit-${model.repo_id}`} />;
}

/** "Qwen · 8B · 16 GB · AWQ". */
export function modelFacts(model: MarketModel): string {
  return [
    model.author,
    formatParams(model.params_b, model.active_params_b),
    model.weights_bytes ? formatBytes(model.weights_bytes) : null,
    model.quantization ? model.quantization.toUpperCase() : null,
  ]
    .filter(Boolean)
    .join(" · ");
}

export interface ModelCardProps {
  model: MarketModel;
  onOpen: (repoId: string) => void;
  onHost: (repoId: string) => void;
  canHost: boolean;
}

export function ModelCard({ model, onOpen, onHost, canHost }: ModelCardProps) {
  const { t } = useTranslation();
  const blurb = model.curated?.blurb ? t(model.curated.blurb, { defaultValue: "" }) : "";
  const hostBlocked = !model.runnable || model.fit?.status === "too_large" || model.fit?.status === "no_accelerators";

  return (
    <Card
      variant="outlined"
      sx={{ height: "100%", display: "flex", flexDirection: "column", opacity: model.runnable ? 1 : 0.7 }}
      data-testid={`model-card-${model.repo_id}`}
    >
      <CardActionArea
        onClick={() => onOpen(model.repo_id)}
        // ButtonBase centres its content: top-align it, so cards in a row line up.
        sx={{
          flexGrow: 1,
          alignItems: "stretch",
          justifyContent: "flex-start",
          display: "flex",
          flexDirection: "column",
          p: 2,
          gap: 1.25,
        }}
      >
        <Stack direction="row" spacing={1.5} alignItems="center" sx={{ width: "100%" }}>
          <Avatar variant="rounded" sx={{ width: 36, height: 36, bgcolor: "primary.main", fontSize: 16 }}>
            {(model.author ?? model.name).slice(0, 1).toUpperCase()}
          </Avatar>
          <Box sx={{ minWidth: 0, flexGrow: 1 }}>
            <Stack direction="row" spacing={0.5} alignItems="center">
              <Typography variant="subtitle1" fontWeight={600} noWrap title={model.repo_id}>
                {model.name}
              </Typography>
              {model.gated && (
                <Tooltip title={t("marketplace.gated_help")}>
                  <LockIcon fontSize="small" color="action" data-testid="gated-icon" />
                </Tooltip>
              )}
            </Stack>
            <Typography variant="caption" color="text.secondary" noWrap component="div">
              {modelFacts(model)}
            </Typography>
          </Box>
        </Stack>

        {blurb && (
          <Typography variant="body2" color="text.secondary" sx={{ width: "100%" }}>
            {blurb}
          </Typography>
        )}
        {!model.runnable && (
          <Typography variant="body2" color="text.secondary" sx={{ width: "100%" }}>
            {t(`marketplace.not_runnable.${model.not_runnable_reason ?? "task"}`)}
          </Typography>
        )}

        <Box sx={{ width: "100%" }}>
          <CapabilityChips capabilities={model.capabilities} />
        </Box>

        <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap sx={{ width: "100%" }}>
          {model.curated?.verified && (
            <Tooltip title={t("marketplace.verified_help", { on: model.curated.verified.on, date: model.curated.verified.date })}>
              <Chip size="small" color="primary" variant="outlined" icon={<VerifiedIcon />} label={t("marketplace.verified")} />
            </Tooltip>
          )}
          {model.local && (
            <Chip
              size="small"
              icon={<StorageIcon />}
              label={
                model.local.status === "downloading"
                  ? t("marketplace.local_downloading")
                  : model.local.deployments > 0
                    ? t("marketplace.local_deployed", { count: model.local.deployments })
                    : t("marketplace.local_kept")
              }
              data-testid={`local-${model.repo_id}`}
            />
          )}
        </Stack>
      </CardActionArea>

      <Divider />
      <Stack direction="row" alignItems="center" spacing={1} sx={{ px: 2, py: 1 }}>
        <FitChip model={model} />
        <Box sx={{ flexGrow: 1 }} />
        {typeof model.downloads === "number" && model.downloads > 0 && (
          <Tooltip title={t("marketplace.downloads_help")}>
            <Stack direction="row" spacing={0.25} alignItems="center" sx={{ color: "text.secondary" }}>
              <DownloadIcon sx={{ fontSize: 16 }} />
              <Typography variant="caption">{formatCount(model.downloads)}</Typography>
            </Stack>
          </Tooltip>
        )}
        {typeof model.likes === "number" && model.likes > 0 && (
          <Stack direction="row" spacing={0.25} alignItems="center" sx={{ color: "text.secondary" }}>
            <FavoriteBorderIcon sx={{ fontSize: 16 }} />
            <Typography variant="caption">{formatCount(model.likes)}</Typography>
          </Stack>
        )}
        {canHost && (
          <Button
            size="small"
            variant="contained"
            disabled={hostBlocked}
            onClick={() => onHost(model.repo_id)}
            data-testid={`host-${model.repo_id}`}
          >
            {t("marketplace.host")}
          </Button>
        )}
      </Stack>
    </Card>
  );
}
