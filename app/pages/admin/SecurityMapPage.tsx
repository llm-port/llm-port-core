import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import Divider from "@mui/material/Divider";
import Grid from "@mui/material/Grid";
import LinearProgress from "@mui/material/LinearProgress";
import List from "@mui/material/List";
import ListItem from "@mui/material/ListItem";
import ListItemIcon from "@mui/material/ListItemIcon";
import ListItemText from "@mui/material/ListItemText";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import { alpha, useTheme } from "@mui/material/styles";

import CloudIcon from "@mui/icons-material/Cloud";
import DnsIcon from "@mui/icons-material/Dns";
import LinkIcon from "@mui/icons-material/Link";
import PowerIcon from "@mui/icons-material/Power";
import PowerOffIcon from "@mui/icons-material/PowerOff";
import ShieldIcon from "@mui/icons-material/Shield";
import VerifiedUserIcon from "@mui/icons-material/VerifiedUser";

import {
  providers as providersApi,
  runtimes as runtimesApi,
  models as modelsApi,
  type Provider,
  type Runtime,
  type Model,
} from "~/api/llm";

// ─── Helpers ─────────────────────────────────────────────────────────────────

type ResidencyBadge = "air_gapped" | "hybrid" | "cloud_only" | "none";

function classifyBadge(local: number, remote: number): ResidencyBadge {
  if (local === 0 && remote === 0) return "none";
  if (remote === 0) return "air_gapped";
  if (local === 0) return "cloud_only";
  return "hybrid";
}

const BADGE_META: Record<ResidencyBadge, { color: "success" | "warning" | "error" | "default"; labelKey: string; fallback: string }> = {
  air_gapped: { color: "success", labelKey: "security_map.badge_air_gapped", fallback: "Air-Gapped ✓" },
  hybrid: { color: "warning", labelKey: "security_map.badge_hybrid", fallback: "Hybrid" },
  cloud_only: { color: "error", labelKey: "security_map.badge_cloud_only", fallback: "Cloud Only" },
  none: { color: "default", labelKey: "security_map.badge_none", fallback: "No Providers" },
};

function runtimeStatusColor(status: string): "success" | "warning" | "error" | "default" {
  if (status === "running") return "success";
  if (status === "starting" || status === "creating" || status === "stopping") return "warning";
  if (status === "error") return "error";
  return "default";
}

// ─── Sub-components ──────────────────────────────────────────────────────────

interface ProviderCardProps {
  provider: Provider;
  runtimes: Runtime[];
  modelNames: Map<string, string>;
  t: (key: string, opts?: Record<string, unknown>) => string;
}

function ProviderCard({ provider, runtimes, modelNames, t }: ProviderCardProps) {
  const isLocal = provider.target === "local_docker";
  const activeRuntimes = runtimes.filter((r) => r.status === "running").length;

  return (
    <Card variant="outlined" sx={{ mb: 1.5 }}>
      <CardContent sx={{ py: 1.5, "&:last-child": { pb: 1.5 } }}>
        <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 0.5 }}>
          <DnsIcon fontSize="small" color={isLocal ? "success" : "warning"} />
          <Typography variant="subtitle2" sx={{ flexGrow: 1 }}>
            {provider.name}
          </Typography>
          <Chip label={provider.type} size="small" variant="outlined" />
        </Stack>

        {!isLocal && provider.endpoint_url && (
          <Stack direction="row" alignItems="center" spacing={0.5} sx={{ ml: 3.5, mb: 0.5 }}>
            <LinkIcon sx={{ fontSize: 14, color: "text.secondary" }} />
            <Typography variant="caption" color="text.secondary" noWrap sx={{ maxWidth: 280 }}>
              {provider.endpoint_url}
            </Typography>
          </Stack>
        )}

        {runtimes.length > 0 && (
          <List dense disablePadding sx={{ ml: 2 }}>
            {runtimes.map((rt) => (
              <ListItem key={rt.id} disableGutters sx={{ py: 0.25 }}>
                <ListItemIcon sx={{ minWidth: 28 }}>
                  {rt.status === "running" ? (
                    <PowerIcon fontSize="small" color="success" />
                  ) : (
                    <PowerOffIcon fontSize="small" color="disabled" />
                  )}
                </ListItemIcon>
                <ListItemText
                  primary={rt.name}
                  secondary={modelNames.get(rt.model_id) ?? rt.model_id}
                  primaryTypographyProps={{ variant: "body2" }}
                  secondaryTypographyProps={{ variant: "caption" }}
                />
                <Chip
                  label={rt.status}
                  size="small"
                  color={runtimeStatusColor(rt.status)}
                  variant="outlined"
                  sx={{ ml: 1 }}
                />
              </ListItem>
            ))}
          </List>
        )}

        {runtimes.length === 0 && (
          <Typography variant="caption" color="text.secondary" sx={{ ml: 3.5 }}>
            {t("security_map.no_runtimes", { defaultValue: "No runtimes deployed" })}
          </Typography>
        )}

        <Stack direction="row" spacing={1} sx={{ mt: 0.5, ml: 3.5 }}>
          <Typography variant="caption" color="text.secondary">
            {t("security_map.runtimes_active", {
              active: activeRuntimes,
              total: runtimes.length,
              defaultValue: "{{active}} / {{total}} runtimes active",
            })}
          </Typography>
        </Stack>
      </CardContent>
    </Card>
  );
}

// ─── Main page ───────────────────────────────────────────────────────────────

export default function SecurityMapPage() {
  const { t } = useTranslation();
  const theme = useTheme();

  const [allProviders, setAllProviders] = useState<Provider[]>([]);
  const [allRuntimes, setAllRuntimes] = useState<Runtime[]>([]);
  const [allModels, setAllModels] = useState<Model[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const [prov, rt, mdl] = await Promise.all([
          providersApi.list(),
          runtimesApi.list(),
          modelsApi.list(),
        ]);
        if (!cancelled) {
          setAllProviders(prov);
          setAllRuntimes(rt);
          setAllModels(mdl);
        }
      } catch (err: unknown) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load data");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const { localProviders, remoteProviders, runtimesByProvider, modelNames, badge, localPct, localRuntimeCount, remoteRuntimeCount } =
    useMemo(() => {
      const local = allProviders.filter((p) => p.target === "local_docker");
      const remote = allProviders.filter((p) => p.target === "remote_endpoint");
      const runtimeMap = new Map<string, Runtime[]>();
      for (const rt of allRuntimes) {
        const list = runtimeMap.get(rt.provider_id) ?? [];
        list.push(rt);
        runtimeMap.set(rt.provider_id, list);
      }
      const names = new Map(allModels.map((m) => [m.id, m.display_name]));
      const total = local.length + remote.length;
      const localRt = local.reduce((n, p) => n + (runtimeMap.get(p.id)?.length ?? 0), 0);
      const remoteRt = remote.reduce((n, p) => n + (runtimeMap.get(p.id)?.length ?? 0), 0);
      return {
        localProviders: local,
        remoteProviders: remote,
        runtimesByProvider: runtimeMap,
        modelNames: names,
        badge: classifyBadge(local.length, remote.length),
        localPct: total > 0 ? Math.round((local.length / total) * 100) : 100,
        localRuntimeCount: localRt,
        remoteRuntimeCount: remoteRt,
      };
    }, [allProviders, allRuntimes, allModels]);

  const badgeMeta = BADGE_META[badge];

  if (loading) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", py: 8 }}>
        <CircularProgress />
      </Box>
    );
  }

  if (error) {
    return <Alert severity="error" sx={{ m: 2 }}>{error}</Alert>;
  }

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
      {/* ── Summary bar ─────────────────────────────────────── */}
      <Card variant="outlined">
        <CardContent>
          <Stack direction={{ xs: "column", sm: "row" }} alignItems={{ sm: "center" }} spacing={2}>
            <Stack direction="row" alignItems="center" spacing={1} sx={{ flexGrow: 1 }}>
              <ShieldIcon color="primary" fontSize="large" />
              <Box>
                <Typography variant="h5" fontWeight={700}>
                  {t("security_map.title", { defaultValue: "Data Residency Map" })}
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  {t("security_map.subtitle", {
                    defaultValue: "Overview of where your inference data is processed",
                  })}
                </Typography>
              </Box>
            </Stack>
            <Chip
              icon={badge === "air_gapped" ? <VerifiedUserIcon /> : undefined}
              label={t(badgeMeta.labelKey, { defaultValue: badgeMeta.fallback })}
              color={badgeMeta.color}
              variant="filled"
              sx={{ fontWeight: 700, fontSize: "0.9rem", px: 1 }}
            />
          </Stack>

          <Divider sx={{ my: 2 }} />

          {/* Counts row */}
          <Stack direction="row" spacing={4} flexWrap="wrap" useFlexGap>
            <Stack alignItems="center">
              <Typography variant="h4" fontWeight={700} color="success.main">{localProviders.length}</Typography>
              <Typography variant="caption" color="text.secondary">
                {t("security_map.local_providers", { defaultValue: "Local Providers" })}
              </Typography>
            </Stack>
            <Stack alignItems="center">
              <Typography variant="h4" fontWeight={700} color="warning.main">{remoteProviders.length}</Typography>
              <Typography variant="caption" color="text.secondary">
                {t("security_map.remote_providers", { defaultValue: "Cloud Providers" })}
              </Typography>
            </Stack>
            <Stack alignItems="center">
              <Typography variant="h4" fontWeight={700} color="success.main">{localRuntimeCount}</Typography>
              <Typography variant="caption" color="text.secondary">
                {t("security_map.local_runtimes", { defaultValue: "Local Runtimes" })}
              </Typography>
            </Stack>
            <Stack alignItems="center">
              <Typography variant="h4" fontWeight={700} color="warning.main">{remoteRuntimeCount}</Typography>
              <Typography variant="caption" color="text.secondary">
                {t("security_map.remote_runtimes", { defaultValue: "Cloud Runtimes" })}
              </Typography>
            </Stack>
          </Stack>

          {/* Progress bar */}
          <Box sx={{ mt: 2 }}>
            <Stack direction="row" justifyContent="space-between" sx={{ mb: 0.5 }}>
              <Typography variant="caption" fontWeight={600} color="success.main">
                {t("security_map.bar_local", { defaultValue: "On-Premises" })} {localPct}%
              </Typography>
              <Typography variant="caption" fontWeight={600} color="warning.main">
                {t("security_map.bar_cloud", { defaultValue: "Cloud" })} {100 - localPct}%
              </Typography>
            </Stack>
            <LinearProgress
              variant="determinate"
              value={localPct}
              sx={{
                height: 12,
                borderRadius: 1.5,
                bgcolor: "warning.light",
                "& .MuiLinearProgress-bar": { bgcolor: "success.main", borderRadius: 1.5 },
              }}
            />
          </Box>
        </CardContent>
      </Card>

      {/* ── Two-column detail view ──────────────────────────── */}
      <Grid container spacing={2}>
        {/* Local column */}
        <Grid size={{ xs: 12, md: 6 }}>
          <Card
            variant="outlined"
            sx={{
              height: "100%",
              borderColor: "success.main",
              bgcolor: alpha(theme.palette.success.main, 0.04),
            }}
          >
            <CardContent>
              <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 2 }}>
                <ShieldIcon color="success" />
                <Typography variant="h6" fontWeight={700}>
                  {t("security_map.local_title", { defaultValue: "Local (On-Premises)" })}
                </Typography>
              </Stack>

              <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                {t("security_map.local_desc", {
                  defaultValue: "Data stays within your private infrastructure. No external network calls for inference.",
                })}
              </Typography>

              {localProviders.length === 0 ? (
                <Typography variant="body2" color="text.secondary" sx={{ fontStyle: "italic" }}>
                  {t("security_map.no_local", { defaultValue: "No local providers configured." })}
                </Typography>
              ) : (
                localProviders.map((p) => (
                  <ProviderCard
                    key={p.id}
                    provider={p}
                    runtimes={runtimesByProvider.get(p.id) ?? []}
                    modelNames={modelNames}
                    t={t}
                  />
                ))
              )}
            </CardContent>
          </Card>
        </Grid>

        {/* Cloud column */}
        <Grid size={{ xs: 12, md: 6 }}>
          <Card
            variant="outlined"
            sx={{
              height: "100%",
              borderColor: "warning.main",
              bgcolor: alpha(theme.palette.warning.main, 0.04),
            }}
          >
            <CardContent>
              <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 2 }}>
                <CloudIcon color="warning" />
                <Typography variant="h6" fontWeight={700}>
                  {t("security_map.cloud_title", { defaultValue: "Cloud (Remote)" })}
                </Typography>
              </Stack>

              <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                {t("security_map.cloud_desc", {
                  defaultValue: "Inference requests are sent to external cloud endpoints. Data leaves your network.",
                })}
              </Typography>

              {remoteProviders.length === 0 ? (
                <Typography variant="body2" color="text.secondary" sx={{ fontStyle: "italic" }}>
                  {t("security_map.no_remote", { defaultValue: "No cloud providers configured." })}
                </Typography>
              ) : (
                remoteProviders.map((p) => (
                  <ProviderCard
                    key={p.id}
                    provider={p}
                    runtimes={runtimesByProvider.get(p.id) ?? []}
                    modelNames={modelNames}
                    t={t}
                  />
                ))
              )}
            </CardContent>
          </Card>
        </Grid>
      </Grid>
    </Box>
  );
}
