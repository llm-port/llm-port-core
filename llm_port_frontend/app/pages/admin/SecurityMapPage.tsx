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
import MenuItem from "@mui/material/MenuItem";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import { alpha, useTheme } from "@mui/material/styles";

import CloudIcon from "@mui/icons-material/Cloud";
import DataUsageIcon from "@mui/icons-material/DataUsage";
import DnsIcon from "@mui/icons-material/Dns";
import HelpOutlineIcon from "@mui/icons-material/HelpOutline";
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
  type ResidencyKind,
  type ResidencyOverride,
  type Runtime,
  type Model,
} from "~/api/llm";
import {
  getLlmDataUsage,
  type DataUsageSummary,
  type DataUsagePerInstance,
} from "~/api/llmGraph";
import { residencyKind, residencyReason, splitByResidency, staysInside, type ResidencyBadge } from "~/lib/residency";
import { useCan } from "~/lib/useCan";
import ResidencyBar from "~/components/ResidencyBar";

// ─── Helpers ─────────────────────────────────────────────────────────────────

const RESIDENCY_COLOR: Record<ResidencyKind, "success" | "info" | "warning" | "default"> = {
  machines: "success",
  private: "info",
  external: "warning",
  unknown: "default",
};

const OVERRIDES: Exclude<ResidencyOverride, null>[] = ["machines", "private", "external"];

const BADGE_META: Record<
  ResidencyBadge,
  {
    color: "success" | "warning" | "error" | "default";
    labelKey: string;
    fallback: string;
  }
> = {
  air_gapped: {
    color: "success",
    labelKey: "security_map.badge_air_gapped",
    fallback: "Air-Gapped ✓",
  },
  hybrid: {
    color: "warning",
    labelKey: "security_map.badge_hybrid",
    fallback: "Hybrid",
  },
  cloud_only: {
    color: "error",
    labelKey: "security_map.badge_cloud_only",
    fallback: "Cloud Only",
  },
  none: {
    color: "default",
    labelKey: "security_map.badge_none",
    fallback: "No Providers",
  },
};

function runtimeStatusColor(
  status: string,
): "success" | "warning" | "error" | "default" {
  if (status === "running") return "success";
  if (status === "starting" || status === "creating" || status === "stopping")
    return "warning";
  if (status === "error") return "error";
  return "default";
}

/** Estimate byte-size from token count (~4 bytes/token). */
function tokensToBytes(tokens: number): number {
  return tokens * 4;
}

/** Format a byte count into a human-readable string. */
function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(
    Math.floor(Math.log(bytes) / Math.log(1024)),
    units.length - 1,
  );
  const value = bytes / Math.pow(1024, i);
  return `${value < 10 ? value.toFixed(2) : value < 100 ? value.toFixed(1) : Math.round(value)} ${units[i]}`;
}

/** Format a token count into a compact string. */
function formatTokens(tokens: number): string {
  if (tokens < 1_000) return String(tokens);
  if (tokens < 1_000_000) return `${(tokens / 1_000).toFixed(1)}K`;
  if (tokens < 1_000_000_000) return `${(tokens / 1_000_000).toFixed(1)}M`;
  return `${(tokens / 1_000_000_000).toFixed(2)}B`;
}

/** Aggregate usage for a set of runtime IDs. */
interface AggregatedUsage {
  totalRequests: number;
  totalTokens: number;
  totalPromptTokens: number;
  totalCompletionTokens: number;
  errorCount: number;
}

function aggregateUsage(
  runtimeIds: Set<string>,
  usageByInstance: Map<string, DataUsagePerInstance>,
): AggregatedUsage {
  let totalRequests = 0;
  let totalTokens = 0;
  let totalPromptTokens = 0;
  let totalCompletionTokens = 0;
  let errorCount = 0;
  for (const rid of runtimeIds) {
    const u = usageByInstance.get(rid);
    if (u) {
      totalRequests += u.total_requests;
      totalTokens += u.total_tokens;
      totalPromptTokens += u.total_prompt_tokens;
      totalCompletionTokens += u.total_completion_tokens;
      errorCount += u.error_count;
    }
  }
  return {
    totalRequests,
    totalTokens,
    totalPromptTokens,
    totalCompletionTokens,
    errorCount,
  };
}

// ─── Sub-components ──────────────────────────────────────────────────────────

interface ProviderCardProps {
  provider: Provider;
  runtimes: Runtime[];
  modelNames: Map<string, string>;
  usageByInstance: Map<string, DataUsagePerInstance>;
  t: (key: string, opts?: Record<string, unknown>) => string;
  /** Present when the viewer may say where a provider's prompts go. */
  onSetResidency?: (provider: Provider, override: ResidencyOverride) => void;
}

function ProviderCard({
  provider,
  runtimes,
  modelNames,
  usageByInstance,
  t,
  onSetResidency,
}: ProviderCardProps) {
  const kind = residencyKind(provider);
  const isLocal = staysInside(kind);
  const reason = residencyReason(t, provider.residency);
  const activeRuntimes = runtimes.filter((r) => r.status === "running").length;
  const runtimeIds = new Set(runtimes.map((r) => r.id));
  const usage = aggregateUsage(runtimeIds, usageByInstance);
  const estimatedBytes = tokensToBytes(usage.totalTokens);

  return (
    <Card variant="outlined" sx={{ mb: 1.5 }}>
      <CardContent sx={{ py: 1.5, "&:last-child": { pb: 1.5 } }}>
        <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 0.5 }}>
          <DnsIcon fontSize="small" color={isLocal ? "success" : "warning"} />
          <Typography variant="subtitle2" sx={{ flexGrow: 1 }}>
            {provider.name}
          </Typography>
          <Chip
            label={t(`security_map.residency.${kind}`)}
            size="small"
            color={RESIDENCY_COLOR[kind]}
            data-testid={`residency-${provider.id}`}
          />
          <Chip label={provider.type} size="small" variant="outlined" />
        </Stack>

        {reason && (
          <Typography variant="caption" color="text.secondary" component="div" sx={{ ml: 3.5, mb: 0.5 }}>
            {reason}
          </Typography>
        )}

        {provider.endpoint_url && (
          <Stack
            direction="row"
            alignItems="center"
            spacing={0.5}
            sx={{ ml: 3.5, mb: 0.5 }}
          >
            <LinkIcon sx={{ fontSize: 14, color: "text.secondary" }} />
            <Typography
              variant="caption"
              color="text.secondary"
              noWrap
              sx={{ maxWidth: 280 }}
            >
              {provider.endpoint_url}
            </Typography>
          </Stack>
        )}

        {/* A deployment or found container serves it: there is no runtime row to list. */}
        {provider.managed_by && (
          <List dense disablePadding sx={{ ml: 2 }}>
            <ListItem disableGutters sx={{ py: 0.25 }}>
              <ListItemIcon sx={{ minWidth: 28 }}>
                {provider.managed_by.state === "running" ? (
                  <PowerIcon fontSize="small" color="success" />
                ) : (
                  <PowerOffIcon fontSize="small" color="disabled" />
                )}
              </ListItemIcon>
              <ListItemText
                primary={provider.managed_by.name ?? provider.name}
                secondary={provider.managed_by.model_name}
                primaryTypographyProps={{ variant: "body2" }}
                secondaryTypographyProps={{ variant: "caption" }}
              />
              {provider.managed_by.state && (
                <Chip
                  label={provider.managed_by.state}
                  size="small"
                  color={runtimeStatusColor(provider.managed_by.state)}
                  variant="outlined"
                  sx={{ ml: 1 }}
                />
              )}
            </ListItem>
          </List>
        )}

        {/* An owned provider has no runtime rows: its owner above stands in for them. */}
        {(runtimes.length > 0 || !provider.managed_by) && (
          <>
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
              {t("security_map.no_runtimes", {
                defaultValue: "No runtimes deployed",
              })}
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
          </>
        )}

        {onSetResidency && (
          <TextField
            select
            size="small"
            label={t("security_map.residency_set_label")}
            value={provider.residency_override ?? ""}
            onChange={(e) => onSetResidency(provider, (e.target.value || null) as ResidencyOverride)}
            sx={{ mt: 1, ml: 3.5, minWidth: 220 }}
            slotProps={{ select: { displayEmpty: true }, inputLabel: { shrink: true } }}
            data-testid={`residency-set-${provider.id}`}
          >
            <MenuItem value="">{t("security_map.residency_detected")}</MenuItem>
            {OVERRIDES.map((value) => (
              <MenuItem key={value} value={value}>
                {t(`security_map.residency.${value}`)}
              </MenuItem>
            ))}
          </TextField>
        )}

        {/* Data volume */}
        {usage.totalRequests > 0 && (
          <Box
            sx={{
              mt: 1,
              ml: 3.5,
              p: 1,
              borderRadius: 1,
              bgcolor: (theme) => alpha(theme.palette.info.main, 0.06),
              border: (theme) =>
                `1px solid ${alpha(theme.palette.info.main, 0.15)}`,
            }}
          >
            <Stack
              direction="row"
              alignItems="center"
              spacing={0.5}
              sx={{ mb: 0.5 }}
            >
              <DataUsageIcon sx={{ fontSize: 14, color: "info.main" }} />
              <Typography variant="caption" fontWeight={600} color="info.main">
                {t("security_map.data_volume", { defaultValue: "Data Volume" })}
              </Typography>
            </Stack>
            <Stack direction="row" spacing={2} flexWrap="wrap" useFlexGap>
              <Tooltip title={`${usage.totalTokens.toLocaleString()} tokens`}>
                <Typography variant="caption" color="text.secondary">
                  {t("security_map.est_data", {
                    size: formatBytes(estimatedBytes),
                    defaultValue: "≈ {{size}}",
                  })}
                </Typography>
              </Tooltip>
              <Typography variant="caption" color="text.secondary">
                {formatTokens(usage.totalTokens)}{" "}
                {t("security_map.tokens", { defaultValue: "tokens" })}
              </Typography>
              <Typography variant="caption" color="text.secondary">
                {usage.totalRequests.toLocaleString()}{" "}
                {t("security_map.requests", { defaultValue: "requests" })}
              </Typography>
              {usage.errorCount > 0 && (
                <Typography variant="caption" color="error.main">
                  {usage.errorCount}{" "}
                  {t("security_map.errors", { defaultValue: "errors" })}
                </Typography>
              )}
            </Stack>
          </Box>
        )}
      </CardContent>
    </Card>
  );
}

// ─── Main page ───────────────────────────────────────────────────────────────

export default function SecurityMapPage() {
  const { t } = useTranslation();
  const theme = useTheme();
  const can = useCan();
  const [saveError, setSaveError] = useState<string | null>(null);

  const [allProviders, setAllProviders] = useState<Provider[]>([]);
  const [allRuntimes, setAllRuntimes] = useState<Runtime[]>([]);
  const [allModels, setAllModels] = useState<Model[]>([]);
  const [dataUsage, setDataUsage] = useState<DataUsageSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const [prov, rt, mdl, usage] = await Promise.all([
          providersApi.list(),
          runtimesApi.list(),
          modelsApi.list(),
          getLlmDataUsage().catch(() => null),
        ]);
        if (!cancelled) {
          setAllProviders(prov);
          setAllRuntimes(rt);
          setAllModels(mdl);
          setDataUsage(usage);
        }
      } catch (err: unknown) {
        if (!cancelled)
          setError(err instanceof Error ? err.message : t("common.load_failed"));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  async function setResidency(provider: Provider, override: ResidencyOverride) {
    setSaveError(null);
    try {
      const updated = await providersApi.setResidency(provider.id, override);
      setAllProviders((current) => current.map((p) => (p.id === updated.id ? { ...p, ...updated } : p)));
    } catch (err: unknown) {
      setSaveError(err instanceof Error ? err.message : t("common.action_failed"));
    }
  }
  const residencySetter = can("llm.providers:update") ? setResidency : undefined;

  const {
    localProviders,
    remoteProviders,
    unknownProviders,
    runtimesByProvider,
    modelNames,
    badge,
    localPct,
    externalPct,
    unknownPct,
    localRuntimeCount,
    remoteRuntimeCount,
    usageByInstance,
    localUsage,
    remoteUsage,
    localDataPct,
  } = useMemo(() => {
    const split = splitByResidency(allProviders);
    const local = split.inside;
    const remote = split.external;
    const runtimeMap = new Map<string, Runtime[]>();
    for (const rt of allRuntimes) {
      const list = runtimeMap.get(rt.provider_id) ?? [];
      list.push(rt);
      runtimeMap.set(rt.provider_id, list);
    }
    const names = new Map(allModels.map((m) => [m.id, m.display_name]));
    const localRt = local.reduce(
      (n, p) => n + (runtimeMap.get(p.id)?.length ?? 0),
      0,
    );
    const remoteRt = remote.reduce(
      (n, p) => n + (runtimeMap.get(p.id)?.length ?? 0),
      0,
    );

    // Build per-instance usage map and side aggregates
    const instMap = new Map<string, DataUsagePerInstance>();
    if (dataUsage) {
      for (const inst of dataUsage.instances) {
        instMap.set(inst.provider_instance_id, inst);
      }
    }
    const localRtIds = new Set(
      local.flatMap((p) => (runtimeMap.get(p.id) ?? []).map((r) => r.id)),
    );
    const remoteRtIds = new Set(
      remote.flatMap((p) => (runtimeMap.get(p.id) ?? []).map((r) => r.id)),
    );
    const localUsg = aggregateUsage(localRtIds, instMap);
    const remoteUsg = aggregateUsage(remoteRtIds, instMap);
    const totalTokens = localUsg.totalTokens + remoteUsg.totalTokens;

    return {
      localProviders: local,
      remoteProviders: remote,
      unknownProviders: split.unknown,
      runtimesByProvider: runtimeMap,
      modelNames: names,
      badge: split.badge as ResidencyBadge,
      localPct: split.insidePct,
      externalPct: split.externalPct,
      unknownPct: split.unknownPct,
      localRuntimeCount: localRt,
      remoteRuntimeCount: remoteRt,
      usageByInstance: instMap,
      localUsage: localUsg,
      remoteUsage: remoteUsg,
      localDataPct:
        totalTokens > 0
          ? Math.round((localUsg.totalTokens / totalTokens) * 100)
          : 100,
    };
  }, [allProviders, allRuntimes, allModels, dataUsage]);

  const badgeMeta = BADGE_META[badge];

  if (loading) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", py: 8 }}>
        <CircularProgress />
      </Box>
    );
  }

  if (error) {
    return (
      <Alert severity="error" sx={{ m: 2 }}>
        {error}
      </Alert>
    );
  }

  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 2 }}>
      {saveError && (
        <Alert severity="error" onClose={() => setSaveError(null)}>
          {saveError}
        </Alert>
      )}
      {/* ── Summary bar ─────────────────────────────────────── */}
      <Card variant="outlined">
        <CardContent>
          <Stack
            direction={{ xs: "column", sm: "row" }}
            alignItems={{ sm: "center" }}
            spacing={2}
          >
            <Stack
              direction="row"
              alignItems="center"
              spacing={1}
              sx={{ flexGrow: 1 }}
            >
              <ShieldIcon color="primary" fontSize="large" />
              <Box>
                <Typography variant="h5" fontWeight={700}>
                  {t("security_map.title", {
                    defaultValue: "Data Residency Map",
                  })}
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  {t("security_map.subtitle", {
                    defaultValue:
                      "Overview of where your inference data is processed",
                  })}
                </Typography>
              </Box>
            </Stack>
            <Chip
              icon={badge === "air_gapped" ? <VerifiedUserIcon /> : undefined}
              label={t(badgeMeta.labelKey, {
                defaultValue: badgeMeta.fallback,
              })}
              color={badgeMeta.color}
              variant="filled"
              sx={{ fontWeight: 700, fontSize: "0.9rem", px: 1 }}
            />
          </Stack>

          <Divider sx={{ my: 2 }} />

          {/* Counts row */}
          <Stack direction="row" spacing={4} flexWrap="wrap" useFlexGap>
            <Stack alignItems="center">
              <Typography variant="h4" fontWeight={700} color="success.main">
                {localProviders.length}
              </Typography>
              <Typography variant="caption" color="text.secondary">
                {t("security_map.local_providers", {
                  defaultValue: "Local Providers",
                })}
              </Typography>
            </Stack>
            <Stack alignItems="center">
              <Typography variant="h4" fontWeight={700} color="warning.main">
                {remoteProviders.length}
              </Typography>
              <Typography variant="caption" color="text.secondary">
                {t("security_map.remote_providers", {
                  defaultValue: "Cloud Providers",
                })}
              </Typography>
            </Stack>
            {unknownProviders.length > 0 && (
              <Stack alignItems="center">
                <Typography variant="h4" fontWeight={700} color="text.secondary">
                  {unknownProviders.length}
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  {t("security_map.unknown_providers")}
                </Typography>
              </Stack>
            )}
            <Stack alignItems="center">
              <Typography variant="h4" fontWeight={700} color="success.main">
                {localRuntimeCount}
              </Typography>
              <Typography variant="caption" color="text.secondary">
                {t("security_map.local_runtimes", {
                  defaultValue: "Local Runtimes",
                })}
              </Typography>
            </Stack>
            <Stack alignItems="center">
              <Typography variant="h4" fontWeight={700} color="warning.main">
                {remoteRuntimeCount}
              </Typography>
              <Typography variant="caption" color="text.secondary">
                {t("security_map.remote_runtimes", {
                  defaultValue: "Cloud Runtimes",
                })}
              </Typography>
            </Stack>
          </Stack>

          {/* Data volume stats */}
          {(localUsage.totalTokens > 0 || remoteUsage.totalTokens > 0) && (
            <>
              <Divider sx={{ my: 2 }} />
              <Stack
                direction="row"
                alignItems="center"
                spacing={1}
                sx={{ mb: 1 }}
              >
                <DataUsageIcon fontSize="small" color="info" />
                <Typography variant="subtitle2" fontWeight={600}>
                  {t("security_map.data_volume_title", {
                    defaultValue: "Data Volume",
                  })}
                </Typography>
              </Stack>
              <Stack direction="row" spacing={4} flexWrap="wrap" useFlexGap>
                <Tooltip
                  title={`${localUsage.totalTokens.toLocaleString()} tokens (${localUsage.totalPromptTokens.toLocaleString()} prompt + ${localUsage.totalCompletionTokens.toLocaleString()} completion)`}
                >
                  <Stack alignItems="center">
                    <Typography
                      variant="h5"
                      fontWeight={700}
                      color="success.main"
                    >
                      {formatBytes(tokensToBytes(localUsage.totalTokens))}
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      {t("security_map.local_data", {
                        defaultValue: "On-Premises Data",
                      })}
                    </Typography>
                  </Stack>
                </Tooltip>
                <Tooltip
                  title={`${remoteUsage.totalTokens.toLocaleString()} tokens (${remoteUsage.totalPromptTokens.toLocaleString()} prompt + ${remoteUsage.totalCompletionTokens.toLocaleString()} completion)`}
                >
                  <Stack alignItems="center">
                    <Typography
                      variant="h5"
                      fontWeight={700}
                      color="warning.main"
                    >
                      {formatBytes(tokensToBytes(remoteUsage.totalTokens))}
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      {t("security_map.cloud_data", {
                        defaultValue: "Cloud Data",
                      })}
                    </Typography>
                  </Stack>
                </Tooltip>
                <Stack alignItems="center">
                  <Typography variant="h5" fontWeight={700}>
                    {(
                      localUsage.totalRequests + remoteUsage.totalRequests
                    ).toLocaleString()}
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    {t("security_map.total_requests", {
                      defaultValue: "Total Requests",
                    })}
                  </Typography>
                </Stack>
              </Stack>

              {/* Data residency bar (by actual tokens) */}
              <Box sx={{ mt: 2 }}>
                <Stack
                  direction="row"
                  justifyContent="space-between"
                  sx={{ mb: 0.5 }}
                >
                  <Typography
                    variant="caption"
                    fontWeight={600}
                    color="success.main"
                  >
                    {t("security_map.bar_local_data", {
                      defaultValue: "On-Premises Data",
                    })}{" "}
                    {localDataPct}%
                  </Typography>
                  <Typography
                    variant="caption"
                    fontWeight={600}
                    color="warning.main"
                  >
                    {t("security_map.bar_cloud_data", {
                      defaultValue: "Cloud Data",
                    })}{" "}
                    {100 - localDataPct}%
                  </Typography>
                </Stack>
                <LinearProgress
                  variant="determinate"
                  value={localDataPct}
                  sx={{
                    height: 12,
                    borderRadius: 1.5,
                    bgcolor: "warning.light",
                    "& .MuiLinearProgress-bar": {
                      bgcolor: "success.main",
                      borderRadius: 1.5,
                    },
                  }}
                />
                <Typography
                  variant="caption"
                  color="text.secondary"
                  sx={{ mt: 0.5, display: "block" }}
                >
                  {t("security_map.data_bar_note", {
                    defaultValue:
                      "Based on actual token throughput (≈ 4 bytes/token)",
                  })}
                </Typography>
              </Box>
            </>
          )}

          {/* Progress bar */}
          <Box sx={{ mt: 2 }}>
            <Stack
              direction="row"
              justifyContent="space-between"
              sx={{ mb: 0.5 }}
            >
              <Typography
                variant="caption"
                fontWeight={600}
                color="success.main"
              >
                {t("security_map.bar_local", { defaultValue: "On-Premises" })}{" "}
                {localPct}%
              </Typography>
              {unknownPct > 0 && (
                <Typography variant="caption" fontWeight={600} color="text.secondary">
                  {t("security_map.residency.unknown")} {unknownPct}%
                </Typography>
              )}
              <Typography
                variant="caption"
                fontWeight={600}
                color="warning.main"
              >
                {t("security_map.bar_cloud", { defaultValue: "External" })}{" "}
                {externalPct}%
              </Typography>
            </Stack>
            <ResidencyBar insidePct={localPct} unknownPct={unknownPct} externalPct={externalPct} />
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
              <Stack
                direction="row"
                alignItems="center"
                spacing={1}
                sx={{ mb: 2 }}
              >
                <ShieldIcon color="success" />
                <Typography variant="h6" fontWeight={700}>
                  {t("security_map.local_title", {
                    defaultValue: "Local (On-Premises)",
                  })}
                </Typography>
              </Stack>

              <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                {t("security_map.local_desc", {
                  defaultValue:
                    "Data stays within your private infrastructure. No external network calls for inference.",
                })}
              </Typography>

              {localProviders.length === 0 ? (
                <Typography
                  variant="body2"
                  color="text.secondary"
                  sx={{ fontStyle: "italic" }}
                >
                  {t("security_map.no_local", {
                    defaultValue: "No local providers configured.",
                  })}
                </Typography>
              ) : (
                localProviders.map((p) => (
                  <ProviderCard
                    key={p.id}
                    provider={p}
                    runtimes={runtimesByProvider.get(p.id) ?? []}
                    modelNames={modelNames}
                    usageByInstance={usageByInstance}
                    t={t}
                    onSetResidency={residencySetter}
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
              <Stack
                direction="row"
                alignItems="center"
                spacing={1}
                sx={{ mb: 2 }}
              >
                <CloudIcon color="warning" />
                <Typography variant="h6" fontWeight={700}>
                  {t("security_map.cloud_title", {
                    defaultValue: "Cloud (Remote)",
                  })}
                </Typography>
              </Stack>

              <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                {t("security_map.cloud_desc", {
                  defaultValue:
                    "Inference requests are sent to external cloud endpoints. Data leaves your network.",
                })}
              </Typography>

              {remoteProviders.length === 0 ? (
                <Typography
                  variant="body2"
                  color="text.secondary"
                  sx={{ fontStyle: "italic" }}
                >
                  {t("security_map.no_remote", {
                    defaultValue: "No cloud providers configured.",
                  })}
                </Typography>
              ) : (
                remoteProviders.map((p) => (
                  <ProviderCard
                    key={p.id}
                    provider={p}
                    runtimes={runtimesByProvider.get(p.id) ?? []}
                    modelNames={modelNames}
                    usageByInstance={usageByInstance}
                    t={t}
                    onSetResidency={residencySetter}
                  />
                ))
              )}
            </CardContent>
          </Card>
        </Grid>
      </Grid>

      {/* ── Providers that could not be placed ─────────────────── */}
      {unknownProviders.length > 0 && (
        <Card variant="outlined" data-testid="residency-unknown">
          <CardContent>
            <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1 }}>
              <HelpOutlineIcon color="action" />
              <Typography variant="h6" fontWeight={700}>
                {t("security_map.unknown_title")}
              </Typography>
            </Stack>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
              {t("security_map.unknown_desc")}
            </Typography>
            {unknownProviders.map((p) => (
              <ProviderCard
                key={p.id}
                provider={p}
                runtimes={runtimesByProvider.get(p.id) ?? []}
                modelNames={modelNames}
                usageByInstance={usageByInstance}
                t={t}
                onSetResidency={residencySetter}
              />
            ))}
          </CardContent>
        </Card>
      )}
    </Box>
  );
}
