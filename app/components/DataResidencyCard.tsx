import { useMemo } from "react";
import { Link as RouterLink } from "react-router";
import { useTranslation } from "react-i18next";

import Box from "@mui/material/Box";
import Card from "@mui/material/Card";
import CardActionArea from "@mui/material/CardActionArea";
import CardContent from "@mui/material/CardContent";
import Chip from "@mui/material/Chip";
import LinearProgress from "@mui/material/LinearProgress";
import Stack from "@mui/material/Stack";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";

import ShieldIcon from "@mui/icons-material/Shield";
import CloudIcon from "@mui/icons-material/Cloud";
import VerifiedUserIcon from "@mui/icons-material/VerifiedUser";

import type { Provider } from "~/api/llm";

export interface DataResidencyCardProps {
  providers: Provider[];
}

type ResidencyBadge = "air_gapped" | "hybrid" | "cloud_only" | "none";

function classifyBadge(local: number, remote: number): ResidencyBadge {
  if (local === 0 && remote === 0) return "none";
  if (remote === 0) return "air_gapped";
  if (local === 0) return "cloud_only";
  return "hybrid";
}

const BADGE_COLOR: Record<ResidencyBadge, "success" | "warning" | "error" | "default"> = {
  air_gapped: "success",
  hybrid: "warning",
  cloud_only: "error",
  none: "default",
};

export default function DataResidencyCard({ providers }: DataResidencyCardProps) {
  const { t } = useTranslation();

  const { local, remote, badge, localPct } = useMemo(() => {
    const loc = providers.filter((p) => p.target === "local_docker").length;
    const rem = providers.filter((p) => p.target === "remote_endpoint").length;
    const total = loc + rem;
    return {
      local: loc,
      remote: rem,
      badge: classifyBadge(loc, rem),
      localPct: total > 0 ? Math.round((loc / total) * 100) : 100,
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
            <Tooltip title={t("security_map.local_tooltip", { defaultValue: "Providers running on local infrastructure" })}>
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
            <Tooltip title={t("security_map.cloud_tooltip", { defaultValue: "Providers connecting to remote cloud endpoints" })}>
              <Stack direction="row" spacing={0.5} alignItems="center">
                <CloudIcon fontSize="small" color="warning" />
                <Typography variant="body2" fontWeight={600}>
                  {remote}
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  {t("security_map.cloud_short", { defaultValue: "Cloud" })}
                </Typography>
              </Stack>
            </Tooltip>
          </Stack>

          <Box sx={{ position: "relative" }}>
            <LinearProgress
              variant="determinate"
              value={localPct}
              sx={{
                height: 8,
                borderRadius: 1,
                bgcolor: "warning.light",
                "& .MuiLinearProgress-bar": { bgcolor: "success.main", borderRadius: 1 },
              }}
            />
            <Typography variant="caption" color="text.secondary" sx={{ mt: 0.5, display: "block" }}>
              {t("security_map.local_pct", { pct: localPct, defaultValue: "{{pct}}% on-premises" })}
            </Typography>
          </Box>
        </CardContent>
      </CardActionArea>
    </Card>
  );
}
