import { useMemo } from "react";
import { Link as RouterLink } from "react-router";
import { useTranslation } from "react-i18next";

import Box from "@mui/material/Box";
import Card from "@mui/material/Card";
import CardActionArea from "@mui/material/CardActionArea";
import CardContent from "@mui/material/CardContent";
import Chip from "@mui/material/Chip";
import Stack from "@mui/material/Stack";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";

import ShieldIcon from "@mui/icons-material/Shield";
import CloudIcon from "@mui/icons-material/Cloud";
import HelpOutlineIcon from "@mui/icons-material/HelpOutline";
import VerifiedUserIcon from "@mui/icons-material/VerifiedUser";

import type { Provider } from "~/api/llm";
import { splitByResidency, type ResidencyBadge } from "~/lib/residency";
import ResidencyBar from "~/components/ResidencyBar";

export interface DataResidencyCardProps {
  providers: Provider[];
}

const BADGE_COLOR: Record<ResidencyBadge, "success" | "warning" | "error" | "default"> = {
  air_gapped: "success",
  hybrid: "warning",
  cloud_only: "error",
  none: "default",
};

export default function DataResidencyCard({ providers }: DataResidencyCardProps) {
  const { t } = useTranslation();

  const { local, remote, unknown, badge, localPct, externalPct, unknownPct } = useMemo(() => {
    const split = splitByResidency(providers);
    return {
      local: split.inside.length,
      remote: split.external.length,
      unknown: split.unknown.length,
      badge: split.badge,
      localPct: split.insidePct,
      externalPct: split.externalPct,
      unknownPct: split.unknownPct,
    };
  }, [providers]);

  return (
    <Card variant="outlined">
      <CardActionArea component={RouterLink} to="/admin/security-map">
        <CardContent>
          <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1.5 }}>
            <ShieldIcon color="primary" />
            <Typography variant="h6" sx={{ flexGrow: 1 }}>
              {t("security_map.card_title", { defaultValue: "Data Residency" })}
            </Typography>
            <Chip
              icon={badge === "air_gapped" ? <VerifiedUserIcon /> : undefined}
              label={t(`security_map.badge_${badge}`, {
                defaultValue:
                  badge === "air_gapped"
                    ? "Air-Gapped ✓"
                    : badge === "hybrid"
                      ? "Hybrid"
                      : badge === "cloud_only"
                        ? "Cloud Only"
                        : "No Providers",
              })}
              color={BADGE_COLOR[badge]}
              size="small"
              variant="outlined"
            />
          </Stack>

          <Stack direction="row" spacing={3} sx={{ mb: 1 }}>
            <Tooltip title={t("security_map.local_tooltip", { defaultValue: "Providers on your machines or your own network" })}>
              <Stack direction="row" spacing={0.5} alignItems="center">
                <ShieldIcon fontSize="small" color="success" />
                <Typography variant="body2" fontWeight={600}>
                  {local}
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  {t("security_map.local_short", { defaultValue: "Local" })}
                </Typography>
              </Stack>
            </Tooltip>
            <Tooltip title={t("security_map.cloud_tooltip", { defaultValue: "Providers that send prompts outside your network" })}>
              <Stack direction="row" spacing={0.5} alignItems="center">
                <CloudIcon fontSize="small" color="warning" />
                <Typography variant="body2" fontWeight={600}>
                  {remote}
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  {t("security_map.cloud_short", { defaultValue: "External" })}
                </Typography>
              </Stack>
            </Tooltip>
            {unknown > 0 && (
              <Tooltip title={t("security_map.unknown_title", { defaultValue: "Where these send prompts is unknown" })}>
                <Stack direction="row" spacing={0.5} alignItems="center">
                  <HelpOutlineIcon fontSize="small" color="action" />
                  <Typography variant="body2" fontWeight={600}>
                    {unknown}
                  </Typography>
                  <Typography variant="body2" color="text.secondary">
                    {t("security_map.residency.unknown", { defaultValue: "Unknown" })}
                  </Typography>
                </Stack>
              </Tooltip>
            )}
          </Stack>

          <Box sx={{ position: "relative" }}>
            <ResidencyBar insidePct={localPct} unknownPct={unknownPct} externalPct={externalPct} height={8} />
            <Typography variant="caption" color="text.secondary" sx={{ mt: 0.5, display: "block" }}>
              {t("security_map.local_pct", { pct: localPct, defaultValue: "{{pct}}% on-premises" })}
            </Typography>
          </Box>
        </CardContent>
      </CardActionArea>
    </Card>
  );
}
