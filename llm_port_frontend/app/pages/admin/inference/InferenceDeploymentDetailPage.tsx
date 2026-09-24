/**
 * Inference Deployment detail (Phase 6, WI-5).
 *
 * This is the page the phase's exit criterion exercises: an operator must be
 * able to diagnose, scale and stop a workload here without opening the Ray
 * Dashboard or a shell. It renders the ten elements the migration plan names:
 *
 *   1. normalized health            6. endpoint
 *   2. desired / ready replicas     7. logs
 *   3. environment                  8. metrics
 *   4. participating nodes          9. last reconcile / error
 *   5. artifact readiness          10. advanced raw provider status
 *
 * Two states render as labelled partials rather than as zeros or errors:
 * metrics tiers the backend could not reach, and artifact syncs still in
 * flight. Both are real conditions on the current hardware, and hiding them
 * behind a spinner would make a stuck sync look like a slow one.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate, useParams } from "react-router";

import { inferenceApi } from "~/api/inference";
import type { RuntimeMonitoring, StatKey } from "~/api/llm";
import { StatCardRow, type StatCardItem } from "~/components/StatCardRow";
import type {
  ArtifactReadiness,
  DeploymentMetrics,
  GatewayTraffic,
  EnvironmentNode,
  InferenceDeployment,
  InferenceEndpoint,
  InferenceEnvironment,
  LogPage,
  LogSource,
} from "~/api/inference";
import { models as modelsApi, type Model } from "~/api/llm";
import { nodesApi, type ManagedNode } from "~/api/nodes";
import { FormDialog } from "~/components/FormDialog";
import { useAsyncData } from "~/lib/useAsyncData";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import Grid from "@mui/material/Grid";
import MenuItem from "@mui/material/MenuItem";
import Stack from "@mui/material/Stack";
import Table from "@mui/material/Table";
import TableBody from "@mui/material/TableBody";
import TableCell from "@mui/material/TableCell";
import TableHead from "@mui/material/TableHead";
import TableRow from "@mui/material/TableRow";
import TextField from "@mui/material/TextField";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";

import MonitorIcon from "@mui/icons-material/Monitor";
import ArrowBackIcon from "@mui/icons-material/ArrowBack";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import RefreshIcon from "@mui/icons-material/Refresh";
import StopIcon from "@mui/icons-material/Stop";
import SyncIcon from "@mui/icons-material/Sync";
import TuneIcon from "@mui/icons-material/Tune";

import { suggestChatName } from "~/lib/hosting";
import { DeploymentEngineCard } from "./DeploymentEngineCard";
import { gpuCount } from "../clusters/readiness";
import { clusterStatusLabel, nodeLabel, phaseLabel } from "../clusters/presentation";
import {
  JsonBlock,
  LabeledValue,
  PartialsNotice,
  acceleratorsPerCopy,
  asRecord,
  asText,
  copiesWanted,
  deploymentPhaseColor,
  endpointStatusColor,
  environmentStatusColor,
  formatTimestamp,
  reconcileSummary,
  shortId,
} from "./common";

interface DetailBundle {
  deployment: InferenceDeployment | null;
  environment: InferenceEnvironment | null;
  members: EnvironmentNode[];
  nodes: ManagedNode[];
  model: Model | null;
  endpoints: InferenceEndpoint[];
  artifacts: ArtifactReadiness | null;
  metrics: DeploymentMetrics | null;
  /** Set when the metrics route itself failed (e.g. 501 from the driver). */
  metricsError: string | null;
}

const EMPTY: DetailBundle = {
  deployment: null,
  environment: null,
  members: [],
  nodes: [],
  model: null,
  endpoints: [],
  artifacts: null,
  metrics: null,
  metricsError: null,
};

/**
 * Why this page no longer loads in one function.
 *
 * It used to run four stages back to back: fetch the deployment, then five
 * calls together, then artifact readiness, then metrics — each waiting on the
 * one before, and a single spinner over the lot. Four round-trip generations
 * before anything appeared, and the last two stages are exactly the ones that
 * hang when a deployment is unwell.
 *
 * Now only the deployment itself gates the page. Everything that needs it
 * starts the moment it lands, and each part reports its own progress.
 */


/**
 * The engine figures, and how to format each.
 *
 * The same seven the providers page shows, deliberately: a number should
 * mean the same thing and be rounded the same way wherever it is read.
 */
const ENGINE_STATS: {
  key: StatKey;
  i18nKey: string;
  format: (v: number) => string;
}[] = [
  {
    key: "running_requests",
    i18nKey: "llm_monitoring.stat.running_requests",
    format: (v) => Math.round(v).toString(),
  },
  {
    key: "waiting_requests",
    i18nKey: "llm_monitoring.stat.waiting_requests",
    format: (v) => Math.round(v).toString(),
  },
  {
    key: "kv_cache_usage",
    i18nKey: "llm_monitoring.stat.kv_cache_usage",
    format: (v) => `${v.toFixed(1)}%`,
  },
  {
    key: "prefix_cache_hit_rate",
    i18nKey: "llm_monitoring.stat.prefix_cache_hit_rate",
    format: (v) => `${v.toFixed(1)}%`,
  },
  {
    key: "mtp_acceptance",
    i18nKey: "llm_monitoring.stat.mtp_acceptance",
    format: (v) => `${v.toFixed(1)}%`,
  },
  {
    key: "generation_tokens_per_sec",
    i18nKey: "llm_monitoring.stat.generation_tokens_per_sec",
    format: (v) => v.toFixed(1),
  },
  {
    key: "preemption_rate",
    i18nKey: "llm_monitoring.stat.preemption_rate",
    format: (v) => v.toFixed(1),
  },
];

/** Formats a millisecond figure the way an operator reads it. */
function ms(value: number | null): string {
  if (value === null) return "—";
  return value >= 1000 ? `${(value / 1000).toFixed(2)} s` : `${Math.round(value)} ms`;
}

/**
 * What the gateway measured, as cards.
 *
 * Three states rather than one, because they mean different things and a
 * single "0" would collapse them:
 *
 *   * no gateway instance at all — nothing is routed here yet;
 *   * an instance that has served nothing in the window — a real zero;
 *   * traffic, with the figures.
 *
 * Tokens per second is generation speed, not throughput across the window.
 * A deployment that produced 475 tokens in an hour during which the engine
 * worked for three seconds is doing about 150 tokens/sec, and reporting 0.13
 * would tell an operator their hardware was broken.
 */
function TrafficRow({ traffic }: { traffic: GatewayTraffic | null }) {
  const { t } = useTranslation();
  if (traffic === null) {
    return (
      <Typography variant="caption" color="text.disabled" sx={{ mt: 1, display: "block" }}>
        {t("inference.detail.traffic_none_routed")}
      </Typography>
    );
  }

  const minutes = Math.round(traffic.window_sec / 60);
  if (traffic.requests === 0) {
    return (
      <Typography variant="caption" color="text.disabled" sx={{ mt: 1, display: "block" }}>
        {t("inference.detail.traffic_no_requests", { count: minutes })}
      </Typography>
    );
  }

  const cards: StatCardItem[] = [
    {
      key: "requests",
      label: t("inference.detail.traffic_requests"),
      value: String(traffic.requests),
    },
    {
      key: "speed",
      label: t("inference.detail.traffic_speed"),
      value:
        traffic.output_tokens_per_sec === null
          ? null
          : `${traffic.output_tokens_per_sec} tok/s`,
      emptyHint: t("inference.detail.traffic_speed_empty"),
    },
    {
      key: "ttft",
      label: t("inference.detail.traffic_ttft"),
      value: traffic.p50_ttft_ms === null ? null : ms(traffic.p50_ttft_ms),
      emptyHint: t("inference.detail.traffic_ttft_empty"),
    },
    {
      key: "ttft95",
      label: t("inference.detail.traffic_ttft95"),
      value: traffic.p95_ttft_ms === null ? null : ms(traffic.p95_ttft_ms),
      emptyHint: t("inference.detail.traffic_ttft_empty"),
    },
    {
      key: "latency",
      label: t("inference.detail.traffic_latency"),
      value:
        traffic.p50_latency_ms === null ? null : ms(traffic.p50_latency_ms),
    },
    {
      key: "failed",
      label: t("inference.detail.traffic_failed"),
      value:
        traffic.error_rate === null
          ? null
          : `${traffic.errors} (${(traffic.error_rate * 100).toFixed(1)}%)`,
      emptyHint: t("inference.detail.traffic_failed_empty"),
    },
  ];

  return (
    <>
      <Typography variant="caption" color="text.secondary" sx={{ mt: 2, display: "block" }}>
        {t("inference.detail.traffic_window", { count: minutes })}
      </Typography>
      <StatCardRow cards={cards} />
    </>
  );
}

/**
 * What the engine reports, as the same cards the providers page shows.
 *
 * Fetched here rather than folded into the metrics response because it comes
 * from Prometheus and the rest of that response does not: a screen that
 * cannot reach Prometheus should still get its replica counts.
 */
function EngineRow({ deploymentId }: { deploymentId: string }) {
  const { t } = useTranslation();
  const statsReq = useAsyncData(
    () => inferenceApi.deploymentMonitoringStats(deploymentId),
    [deploymentId],
    { initialValue: null as RuntimeMonitoring | null },
  );

  // Same cadence as the providers page, for the same reason: the cards track
  // a live engine without hammering the proxy.
  useEffect(() => {
    const id = window.setInterval(() => {
      void statsReq.refresh();
    }, 30_000);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deploymentId]);

  if (statsReq.data && !statsReq.data.enabled) return null;

  const values = statsReq.data?.stats ?? {};
  const stale = statsReq.data ? statsReq.data.stale : true;
  const dashboardUrl = statsReq.data?.dashboard_url ?? null;

  const cards: StatCardItem[] = ENGINE_STATS.map((d) => {
    const raw = values[d.key];
    return {
      key: d.key,
      label: t(d.i18nKey),
      value: raw != null ? d.format(raw) : null,
      emptyHint: t(
        stale ? "llm_monitoring.stale_hint" : "llm_monitoring.no_data_hint",
      ),
    };
  });

  return (
    <>
      <Typography variant="caption" color="text.secondary" sx={{ mt: 2, display: "block" }}>
        {t("inference.detail.engine_reported")}
      </Typography>
      <StatCardRow
        cards={cards}
        loading={statsReq.loading}
        emptyLabel={t("llm_monitoring.no_data")}
        sx={{
          cursor: dashboardUrl ? "pointer" : "default",
          "&:hover": dashboardUrl ? { bgcolor: "action.selected" } : undefined,
        }}
        header={
          dashboardUrl ? (
            <Stack
              direction="row"
              alignItems="center"
              spacing={0.75}
              sx={{ color: "text.secondary" }}
              role="button"
              aria-label={t("llm_monitoring.dashboard")}
              onClick={() =>
                window.open(dashboardUrl, "_blank", "noopener,noreferrer")
              }
            >
              <MonitorIcon sx={{ fontSize: 16 }} />
              <Typography variant="caption" sx={{ fontWeight: 600 }}>
                {t("llm_monitoring.section")}
              </Typography>
              <Typography
                variant="caption"
                color="text.disabled"
                sx={{ ml: "auto" }}
              >
                {t("llm_monitoring.dashboard")}
              </Typography>
            </Stack>
          ) : undefined
        }
      />
    </>
  );
}

export default function InferenceDeploymentDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { t } = useTranslation();
  const navigate = useNavigate();
  const roleLabel = (role: string) =>
    role === "head" || role === "worker" ? t(`clusters.role.${role}`) : role;
  // The only call the page frame needs.
  const {
    data: deployment,
    loading,
    error,
    refresh: refreshDeployment,
    setError,
  } = useAsyncData(() => inferenceApi.getDeployment(id!), [id], {
    initialValue: null as InferenceDeployment | null,
    // This is the screen an operator watches while a model loads.
    refreshMs: 10_000,
  });

  // These need the deployment before they know what to ask for, so they run
  // as soon as it lands rather than as a second stage the page waits through.
  const envId = deployment?.environment_id ?? null;
  const modelId = deployment?.model_id ?? null;
  // The parts of the page follow the deployment while it is coming up. They
  // used to be read once, at page load: a deployment opened while its model
  // was copying went on saying "syncing, on 0 of 2 machines" next to a
  // health of Serving.
  const phase = deployment?.phase ?? null;
  const settling = phase === "pending" || phase === "preparing" || phase === "applying";
  const followMs = settling ? 10_000 : 0;

  const environmentLoad = useAsyncData(
    () => (envId ? inferenceApi.getEnvironment(envId) : Promise.resolve(null)),
    [envId],
    { initialValue: null as InferenceEnvironment | null },
  );
  const membersLoad = useAsyncData(
    () => (envId ? inferenceApi.listEnvironmentNodes(envId) : Promise.resolve([])),
    [envId],
    { initialValue: [] as EnvironmentNode[], refreshMs: followMs },
  );
  const artifactsLoad = useAsyncData(
    () =>
      envId && modelId
        ? inferenceApi.artifactReadiness(envId, modelId)
        : Promise.resolve(null),
    [envId, modelId],
    { initialValue: null as ArtifactReadiness | null, refreshMs: followMs },
  );
  const endpointsLoad = useAsyncData(
    () => (id ? inferenceApi.listEndpoints(id) : Promise.resolve([])),
    [id],
    { initialValue: [] as InferenceEndpoint[], refreshMs: followMs },
  );
  // The one that hangs when the engine is failing to come up — which is the
  // moment the rest of this page is most worth reading.
  const metricsLoad = useAsyncData(
    () => (id ? inferenceApi.deploymentMetrics(id) : Promise.resolve(null)),
    [id],
    { initialValue: null as DeploymentMetrics | null, refreshMs: 15_000 },
  );
  // Fleet-wide lookups, independent of which deployment this is.
  const fleet = useAsyncData(() => nodesApi.list(), [], {
    initialValue: [] as ManagedNode[],
  });
  const allModels = useAsyncData(() => modelsApi.list(), [], {
    initialValue: [] as Model[],
  });

  const data: DetailBundle = {
    deployment,
    environment: environmentLoad.data,
    members: membersLoad.data,
    nodes: fleet.data,
    model: allModels.data.find((m) => m.id === modelId) ?? null,
    endpoints: endpointsLoad.data,
    artifacts: artifactsLoad.data,
    metrics: metricsLoad.data,
    metricsError: metricsLoad.error,
  };

  const refresh = useCallback(async () => {
    await Promise.all([
      refreshDeployment(),
      environmentLoad.refresh(),
      membersLoad.refresh(),
      artifactsLoad.refresh(),
      endpointsLoad.refresh(),
      metricsLoad.refresh(),
      fleet.refresh(),
      allModels.refresh(),
    ]);
    // Keyed on the loaders themselves, not on ``id``: each one is rebuilt
    // once the deployment tells it what to ask for. Keyed on ``id`` this kept
    // the first render's, from before the deployment had loaded, so every
    // Scale, Stop or Save re-read the cluster, its machines and the model
    // files as "nothing to ask about" and blanked those cards.
  }, [
    refreshDeployment,
    environmentLoad.refresh,
    membersLoad.refresh,
    artifactsLoad.refresh,
    endpointsLoad.refresh,
    metricsLoad.refresh,
    fleet.refresh,
    allModels.refresh,
  ]);

  // A change of phase is when the rest changes most -- and reaching Running
  // is also when the polling above stops, so read everything once more then.
  const seenPhase = useRef<string | null>(null);
  useEffect(() => {
    if (!phase) return;
    if (seenPhase.current !== null && seenPhase.current !== phase) {
      void Promise.all([
        environmentLoad.refresh(),
        membersLoad.refresh(),
        artifactsLoad.refresh(),
        endpointsLoad.refresh(),
      ]);
    }
    seenPhase.current = phase;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase]);

  const [busy, setBusy] = useState<string | null>(null);
  const [scaleOpen, setScaleOpen] = useState(false);
  const [scaling, setScaling] = useState(false);
  const [replicaInput, setReplicaInput] = useState(1);
  const [chatOpen, setChatOpen] = useState(false);
  const [chatSaving, setChatSaving] = useState(false);
  const [chatInput, setChatInput] = useState("");

  // --- Logs ---------------------------------------------------------------
  const [logSource, setLogSource] = useState<LogSource>("runtime_container");
  const [logNodeId, setLogNodeId] = useState("");
  const [logTail, setLogTail] = useState(200);
  const [logPage, setLogPage] = useState<LogPage | null>(null);
  const [logLoading, setLogLoading] = useState(false);
  const [logError, setLogError] = useState<string | null>(null);

  const environment = data.environment;
  const observed = asRecord(deployment?.observed_status);
  const observation = asRecord(observed.observation);
  const chatAlias = asText(asRecord(deployment?.spec?.service).alias) || null;

  // The operator's name for the machine, not the address it answers on --
  // same rule as the cluster screens, so the two never disagree.
  const nodeHost = useCallback(
    (nodeId: string | null | undefined) =>
      nodeId
        ? nodeLabel(
            data.nodes.find((n) => n.id === nodeId),
            shortId(nodeId),
          )
        : "-",
    [data.nodes],
  );

  const loadLogs = useCallback(async () => {
    if (!id) return;
    setLogLoading(true);
    setLogError(null);
    try {
      const page = await inferenceApi.deploymentLogs(id, {
        source: logSource,
        node_id: logNodeId || undefined,
        tail: logTail,
      });
      setLogPage(page);
    } catch (err: unknown) {
      // A driver that cannot serve logs answers 501; say so rather than
      // showing an empty log view that reads as "nothing happened".
      setLogError(err instanceof Error ? err.message : String(err));
      setLogPage(null);
    } finally {
      setLogLoading(false);
    }
  }, [id, logSource, logNodeId, logTail]);

  useEffect(() => {
    void loadLogs();
  }, [loadLogs]);

  /**
   * Make "the next refresh should show it" true.
   *
   * A cold read has nothing cached, so the backend starts a fetch on the node
   * and answers with whatever it has -- which on the first load is nothing,
   * plus a note saying the fetch is still running. The page then never asked
   * again, so that note was the final state: an operator opening a deployment
   * saw "the node has not answered within 30s" and sat there.
   *
   * So when a page comes back empty *and* still in flight, try again a few
   * times. Bounded, and only for that case: a genuinely empty log is not a
   * reason to keep polling a node.
   */
  const emptyAndPending =
    logPage !== null && logPage.lines.length === 0 && Boolean(logPage.detail);
  const [logRetries, setLogRetries] = useState(0);

  useEffect(() => {
    // Reset the budget whenever the operator changes what they are asking for.
    setLogRetries(0);
  }, [id, logSource, logNodeId, logTail]);

  useEffect(() => {
    if (!emptyAndPending || logLoading || logRetries >= 4) return;
    const timer = setTimeout(() => {
      setLogRetries((n) => n + 1);
      void loadLogs();
    }, 3_000);
    return () => clearTimeout(timer);
  }, [emptyAndPending, logLoading, logRetries, loadLogs]);


  async function runAction(key: string, action: () => Promise<unknown>) {
    setBusy(key);
    try {
      await action();
      await refresh();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t("common.action_failed"));
    } finally {
      setBusy(null);
    }
  }

  const perCopy = deployment ? acceleratorsPerCopy(deployment) : 1;
  const clusterAccelerators = data.members.reduce((sum, member) => {
    const node = data.nodes.find((n) => n.id === member.node_id);
    return sum + (node ? gpuCount(node) : 0);
  }, 0);
  const copiesThatFit =
    clusterAccelerators > 0 && perCopy > 0 ? Math.floor(clusterAccelerators / perCopy) : null;

  async function handleScale() {
    if (!deployment) return;
    setScaling(true);
    try {
      // Replace the whole spec: v1alpha1 is not partially patchable, and the
      // scale block is mutually exclusive between fixed and autoscaled.
      const spec = {
        ...deployment.spec,
        scale: { replicas: replicaInput },
      };
      await inferenceApi.updateDeployment(deployment.id, { spec });
      // Act now, not at the reconciler's next tick.
      await inferenceApi.reconcileDeployment(deployment.id).catch(() => undefined);
      setScaleOpen(false);
      await refresh();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t("inference.detail.scale_failed"));
    } finally {
      setScaling(false);
    }
  }

  async function handleChatName() {
    if (!deployment) return;
    setChatSaving(true);
    try {
      // A full replacement, as for scale. Only the alias changes, and the
      // alias is not part of what the engine runs, so the replicas are not
      // restarted: the next pass only republishes the deployment.
      const current = asRecord(deployment.spec.service);
      const { alias: _previous, ...service } = current;
      const alias = chatInput.trim();
      const spec = {
        ...deployment.spec,
        service: alias ? { ...service, alias } : service,
      };
      await inferenceApi.updateDeployment(deployment.id, { spec });
      await inferenceApi.reconcileDeployment(deployment.id).catch(() => undefined);
      setChatOpen(false);
      await refresh();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t("inference.detail.chat_failed"));
    } finally {
      setChatSaving(false);
    }
  }

  if (loading && !deployment) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", p: 4 }}>
        <CircularProgress />
      </Box>
    );
  }

  if (!deployment) {
    return (
      <Box sx={{ p: 2 }}>
        <Alert severity="error">{error ?? t("inference.detail.not_found")}</Alert>
      </Box>
    );
  }

  const artifacts = data.artifacts;
  const artifactRows = artifacts
    ? [
        ...artifacts.ready_node_ids.map((n) => ({ nodeId: n, state: "ready" })),
        ...artifacts.pending_node_ids.map((n) => ({
          nodeId: n,
          state: "syncing",
        })),
        ...artifacts.failed_node_ids.map((n) => ({
          nodeId: n,
          state: "failed",
        })),
      ]
    : [];

  return (
    <Stack spacing={2} sx={{ pb: 4 }}>
      {error && <Alert severity="error">{error}</Alert>}

      {/* --- Header ------------------------------------------------------ */}
      <Stack direction="row" alignItems="center" spacing={1} flexWrap="wrap">
        <Button
          size="small"
          startIcon={<ArrowBackIcon />}
          onClick={() => navigate("/admin/deployments")}
        >
          {t("clusters.deployments.title")}
        </Button>
        <Typography variant="h6" sx={{ flexGrow: 1 }}>
          {deployment.name}
        </Typography>
        <Button
          size="small"
          startIcon={<TuneIcon />}
          onClick={() => {
            setReplicaInput(copiesWanted(deployment));
            setScaleOpen(true);
          }}
        >
          {t("inference.detail.scale")}
        </Button>
        <Button
          size="small"
          startIcon={<SyncIcon />}
          disabled={busy === "reconcile"}
          onClick={() =>
            runAction("reconcile", () =>
              inferenceApi.reconcileDeployment(deployment.id),
            )
          }
        >
          {t("clusters.detail.check_now")}
        </Button>
        {deployment.desired_state === "active" ? (
          <Button
            size="small"
            color="warning"
            startIcon={<StopIcon />}
            disabled={busy === "stop"}
            onClick={() =>
              runAction("stop", () =>
                inferenceApi.updateDeployment(deployment.id, {
                  desired_state: "stopped",
                }),
              )
            }
          >
            {t("common.stop")}
          </Button>
        ) : (
          <Button
            size="small"
            color="success"
            startIcon={<PlayArrowIcon />}
            disabled={busy === "start"}
            onClick={() =>
              runAction("start", () =>
                inferenceApi.updateDeployment(deployment.id, {
                  desired_state: "active",
                }),
              )
            }
          >
            {t("common.start")}
          </Button>
        )}
      </Stack>

      {/* --- 1, 2, 3, 9: health, replicas, environment, reconcile -------- */}
      <Card variant="outlined">
        <CardContent>
          <Grid container spacing={2}>
            <Grid size={{ xs: 12, sm: 6, md: 3 }}>
              <Typography variant="caption" color="text.secondary" display="block">
                {t("inference.detail.health")}
              </Typography>
              <Stack direction="row" spacing={0.5} alignItems="center">
                <Chip
                  size="small"
                  label={phaseLabel(deployment.phase)}
                  color={deploymentPhaseColor(deployment.phase)}
                />
                {deployment.desired_state !== "active" && (
                  <Chip
                    size="small"
                    variant="outlined"
                    label={t("inference.detail.desired", {
                      state:
                        deployment.desired_state === "stopped"
                          ? t("clusters.phase.stopped")
                          : deployment.desired_state === "deleted"
                            ? t("clusters.phase.deleted")
                            : deployment.desired_state,
                    })}
                  />
                )}
              </Stack>
            </Grid>
            <Grid size={{ xs: 12, sm: 6, md: 3 }}>
              <LabeledValue
                label={t("inference.detail.copies")}
                value={`${deployment.ready_replicas} / ${copiesWanted(deployment)}`}
              />
            </Grid>
            <Grid size={{ xs: 12, sm: 6, md: 3 }}>
              <Typography variant="caption" color="text.secondary" display="block">
                {t("clusters.deployments.col_cluster")}
              </Typography>
              {environment ? (
                <Stack direction="row" spacing={0.5} alignItems="center">
                  <Button
                    size="small"
                    sx={{ textTransform: "none", p: 0, minWidth: 0 }}
                    onClick={() =>
                      navigate(`/admin/clusters/${environment.id}`)
                    }
                  >
                    {environment.name}
                  </Button>
                  <Chip
                    size="small"
                    label={clusterStatusLabel(environment.status)}
                    color={environmentStatusColor(environment.status)}
                  />
                </Stack>
              ) : (
                <Typography variant="body2">
                  {shortId(deployment.environment_id)}
                </Typography>
              )}
            </Grid>
            <Grid size={{ xs: 12, sm: 6, md: 3 }}>
              <LabeledValue
                label={t("clusters.deployments.col_model")}
                value={data.model?.display_name ?? shortId(deployment.model_id)}
              />
            </Grid>
            <Grid size={{ xs: 12, md: 6 }}>
              <LabeledValue
                label={t("inference.detail.last_checked")}
                value={reconcileSummary(deployment)}
              />
            </Grid>
            <Grid size={{ xs: 12, md: 6 }}>
              <LabeledValue
                label={t("inference.detail.last_message")}
                value={
                  deployment.phase_message ??
                  asText(observation.reason) ??
                  t("clusters.detail.none_reported")
                }
              />
            </Grid>
            <Grid size={{ xs: 12, md: 6 }}>
              <Typography variant="caption" color="text.secondary" display="block">
                {t("inference.detail.in_chat")}
              </Typography>
              <Stack direction="row" spacing={1} alignItems="center">
                <Typography variant="body2" data-testid="chat-alias">
                  {chatAlias
                    ? t("inference.detail.offered_as", { alias: chatAlias })
                    : t("inference.detail.not_offered")}
                </Typography>
                <Button
                  size="small"
                  onClick={() => {
                    setChatInput(chatAlias ?? suggestChatName(data.model ?? undefined));
                    setChatOpen(true);
                  }}
                >
                  {chatAlias ? t("inference.detail.change") : t("inference.detail.offer_in_chat")}
                </Button>
              </Stack>
            </Grid>
          </Grid>
        </CardContent>
      </Card>

      {/* --- How vLLM runs it --------------------------------------------- */}
      <DeploymentEngineCard
        deployment={deployment}
        repoId={data.model?.hf_repo_id ?? null}
        onSaved={refresh}
      />

      {/* --- 4: participating nodes -------------------------------------- */}
      <Card variant="outlined">
        <CardContent>
          <Typography variant="subtitle2" gutterBottom>
            {t("clusters.detail.machines")}
          </Typography>
          {data.members.length === 0 ? (
            <Typography variant="body2" color="text.secondary">
              {t("inference.detail.no_machines")}
            </Typography>
          ) : (
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>{t("inference.detail.col_machine")}</TableCell>
                  <TableCell>{t("inference.detail.col_role")}</TableCell>
                  <TableCell>{t("inference.detail.col_ray_status")}</TableCell>
                  <TableCell>{t("clusters.detail.joined")}</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {data.members.map((member) => (
                  <TableRow key={member.node_id}>
                    <TableCell>
                      <Tooltip title={member.node_id}>
                        <span>{nodeHost(member.node_id)}</span>
                      </Tooltip>
                    </TableCell>
                    <TableCell>
                      <Chip
                        size="small"
                        label={roleLabel(member.role)}
                        color={member.role === "head" ? "primary" : "default"}
                      />
                    </TableCell>
                    <TableCell>{member.member_status ?? t("clusters.unknown_machine")}</TableCell>
                    <TableCell>{formatTimestamp(member.joined_at)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {/* --- 5: artifact readiness --------------------------------------- */}
      <Card variant="outlined">
        <CardContent>
          <Stack
            direction="row"
            alignItems="center"
            justifyContent="space-between"
            sx={{ mb: 1 }}
          >
            <Typography variant="subtitle2">{t("inference.detail.model_files")}</Typography>
            <Button
              size="small"
              startIcon={<SyncIcon />}
              disabled={busy === "sync"}
              onClick={() =>
                runAction("sync", () =>
                  inferenceApi.syncArtifact(
                    deployment.environment_id,
                    deployment.model_id,
                  ),
                )
              }
            >
              {t("inference.detail.sync")}
            </Button>
          </Stack>
          {!artifacts ? (
            <Typography variant="body2" color="text.secondary">
              {t("inference.detail.files_unknown")}
            </Typography>
          ) : (
            <>
              <Stack direction="row" spacing={1} sx={{ mb: 1 }}>
                <Chip
                  size="small"
                  color={artifacts.all_ready ? "success" : "warning"}
                  label={
                    artifacts.all_ready
                      ? t("inference.detail.files_everywhere")
                      : t("inference.detail.files_on_some", {
                          ready: artifacts.ready_node_ids.length,
                          total: artifactRows.length,
                        })
                  }
                />
                <Chip
                  size="small"
                  variant="outlined"
                  label={t("inference.detail.revision", {
                    revision: artifacts.desired_revision ?? t("inference.detail.unresolved"),
                  })}
                />
              </Stack>
              {/* Per-node state, not a spinner: a node stuck on FAILED must be
                  visible while the others are still syncing. */}
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>{t("inference.detail.col_machine")}</TableCell>
                    <TableCell>{t("clusters.deployments.col_state")}</TableCell>
                    <TableCell>{t("inference.detail.col_root_path")}</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {artifactRows.map((row) => (
                    <TableRow key={row.nodeId}>
                      <TableCell>{nodeHost(row.nodeId)}</TableCell>
                      <TableCell>
                        <Chip
                          size="small"
                          label={t(`inference.detail.file_state_${row.state}`)}
                          color={
                            row.state === "ready"
                              ? "success"
                              : row.state === "failed"
                                ? "error"
                                : "info"
                          }
                        />
                      </TableCell>
                      <TableCell sx={{ fontFamily: "monospace" }}>
                        {artifacts.root_paths[row.nodeId] ?? "-"}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              {artifacts.blockers.length > 0 && (
                <Alert severity="warning" sx={{ mt: 1 }}>
                  {artifacts.blockers.join("; ")}
                </Alert>
              )}
            </>
          )}
        </CardContent>
      </Card>

      {/* --- 6: endpoint -------------------------------------------------- */}
      <Card variant="outlined">
        <CardContent>
          <Typography variant="subtitle2" gutterBottom>
            {t("inference.detail.endpoints")}
          </Typography>
          {data.endpoints.length === 0 ? (
            <Typography variant="body2" color="text.secondary">
              {t("inference.detail.no_endpoint")}
            </Typography>
          ) : (
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>{t("common.name")}</TableCell>
                  <TableCell>{t("inference.detail.col_url")}</TableCell>
                  <TableCell>{t("common.status")}</TableCell>
                  <TableCell>{t("inference.detail.col_message")}</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {data.endpoints.map((endpoint) => (
                  <TableRow key={endpoint.id}>
                    <TableCell>{endpoint.name}</TableCell>
                    <TableCell sx={{ fontFamily: "monospace" }}>
                      {endpoint.address}
                      {endpoint.path}
                    </TableCell>
                    <TableCell>
                      <Chip
                        size="small"
                        label={endpoint.status}
                        color={endpointStatusColor(endpoint.status)}
                      />
                    </TableCell>
                    <TableCell>{endpoint.status_message ?? "-"}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {/* --- 8: metrics --------------------------------------------------- */}
      <Card variant="outlined">
        <CardContent>
          <Typography variant="subtitle2" gutterBottom>
            {t("inference.detail.metrics")}
          </Typography>
          {data.metricsError ? (
            <Alert severity="info">
              {t("clusters.detail.metrics_unavailable", { error: data.metricsError })}
            </Alert>
          ) : !data.metrics ? (
            <Typography variant="body2" color="text.secondary">
              {t("inference.detail.no_metrics")}
            </Typography>
          ) : (
            <>
              <Grid container spacing={2}>
                <Grid size={{ xs: 6, md: 3 }}>
                  <LabeledValue
                    label={t("inference.detail.application")}
                    value={data.metrics.app_name ?? t("inference.detail.not_applied")}
                    mono
                  />
                </Grid>
                <Grid size={{ xs: 6, md: 3 }}>
                  <LabeledValue
                    label={t("inference.detail.runtime_state")}
                    value={data.metrics.app_status ?? t("clusters.unknown_machine")}
                  />
                </Grid>
                <Grid size={{ xs: 6, md: 3 }}>
                  <LabeledValue
                    label={t("inference.detail.copies_ready")}
                    value={`${data.metrics.replicas_ready} / ${data.metrics.replicas_total}`}
                  />
                </Grid>
                <Grid size={{ xs: 6, md: 3 }}>
                  <LabeledValue
                    label={t("inference.detail.observed_at")}
                    value={formatTimestamp(data.metrics.observed_at)}
                  />
                </Grid>
              </Grid>

              {/* What actually went through the front door.
                  The row above describes what Ray says it is running; this
                  one is measured at the gateway, per request, and is the only
                  part of this card that still answers when the cluster is
                  unreachable or Prometheus is down. */}
              <EngineRow deploymentId={id!} />
              <TrafficRow traffic={data.metrics.traffic} />

              {data.metrics.deployments.length > 0 && (
                <Table size="small" sx={{ mt: 1 }}>
                  <TableHead>
                    <TableRow>
                      <TableCell>{t("inference.detail.col_component")}</TableCell>
                      <TableCell>{t("common.status")}</TableCell>
                      <TableCell align="right">{t("inference.detail.col_ready")}</TableCell>
                      <TableCell align="right">{t("inference.detail.col_pending")}</TableCell>
                      <TableCell>{t("inference.detail.col_message")}</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {data.metrics.deployments.map((replica) => (
                      <TableRow key={replica.deployment_name}>
                        <TableCell sx={{ fontFamily: "monospace" }}>
                          {replica.deployment_name}
                        </TableCell>
                        <TableCell>{replica.status ?? "-"}</TableCell>
                        <TableCell align="right">
                          {replica.replicas_ready}
                        </TableCell>
                        <TableCell align="right">
                          {replica.replicas_pending}
                        </TableCell>
                        <TableCell>{replica.message ?? "-"}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}

              {data.metrics.scrape_targets.length > 0 && (
                <Box sx={{ mt: 1 }}>
                  <Typography variant="caption" color="text.secondary">
                    {t("clusters.detail.prometheus_targets", {
                      targets: data.metrics.scrape_targets.map((target) => target.url).join(", "),
                    })}
                  </Typography>
                </Box>
              )}

              {/* The honest-degradation requirement: a tier that could not be
                  reported is labelled, never rendered as zero. */}
              <PartialsNotice partials={data.metrics.partials} />
            </>
          )}
        </CardContent>
      </Card>

      {/* --- 7: logs ------------------------------------------------------ */}
      <Card variant="outlined">
        <CardContent>
          <Stack
            direction="row"
            spacing={1}
            alignItems="center"
            flexWrap="wrap"
            sx={{ mb: 1 }}
          >
            <Typography variant="subtitle2" sx={{ flexGrow: 1 }}>
              {t("inference.detail.logs")}
            </Typography>
            <TextField
              select
              size="small"
              label={t("inference.detail.log_source")}
              value={logSource}
              sx={{ minWidth: 180 }}
              onChange={(e) => setLogSource(e.target.value as LogSource)}
            >
              <MenuItem value="runtime_container">{t("inference.detail.log_runtime")}</MenuItem>
              <MenuItem value="serve_replica">{t("inference.detail.log_replica")}</MenuItem>
            </TextField>
            <TextField
              select
              size="small"
              label={t("inference.detail.log_machine")}
              value={logNodeId}
              sx={{ minWidth: 180 }}
              onChange={(e) => setLogNodeId(e.target.value)}
            >
              <MenuItem value="">{t("inference.detail.log_head")}</MenuItem>
              {data.members.map((member) => (
                <MenuItem key={member.node_id} value={member.node_id}>
                  {nodeHost(member.node_id)} ({roleLabel(member.role)})
                </MenuItem>
              ))}
            </TextField>
            <TextField
              size="small"
              type="number"
              label={t("inference.detail.log_tail")}
              value={logTail}
              sx={{ width: 110 }}
              slotProps={{ htmlInput: { min: 1, max: 5000 } }}
              onChange={(e) =>
                setLogTail(
                  Math.max(1, Math.min(5000, Number(e.target.value) || 200)),
                )
              }
            />
            <Button
              size="small"
              startIcon={<RefreshIcon />}
              disabled={logLoading}
              onClick={() => void loadLogs()}
            >
              {t("inference.detail.fetch")}
            </Button>
          </Stack>

          {logError && <Alert severity="warning">{logError}</Alert>}

          {logLoading ? (
            <Box sx={{ display: "flex", justifyContent: "center", p: 3 }}>
              <CircularProgress size={24} />
            </Box>
          ) : logPage && logPage.lines.length > 0 ? (
            <>
              {logPage.truncated && (
                <Typography variant="caption" color="text.secondary">
                  {t("inference.detail.truncated", { count: logTail })}
                </Typography>
              )}
              <Box
                component="pre"
                sx={{
                  mt: 1,
                  p: 1.5,
                  maxHeight: 420,
                  overflow: "auto",
                  bgcolor: "action.hover",
                  borderRadius: 1,
                  fontFamily: "monospace",
                  fontSize: 12,
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-word",
                }}
              >
                {logPage.lines
                  .map((line) =>
                    [
                      line.ts ? new Date(line.ts).toISOString() : "",
                      line.level ?? "",
                      line.message,
                    ]
                      .filter(Boolean)
                      .join("  "),
                  )
                  .join("\n")}
              </Box>
            </>
          ) : (
            // An empty page carries its own reason, so "nothing logged" and
            // "the node never answered" never look the same.
            <Typography variant="body2" color="text.secondary">
              {logPage?.detail ?? t("inference.detail.no_log_lines")}
            </Typography>
          )}
        </CardContent>
      </Card>

      {/* --- 10: advanced raw provider status ---------------------------- */}
      <Card variant="outlined">
        <CardContent>
          <Typography variant="subtitle2" gutterBottom>
            {t("clusters.detail.advanced")}
          </Typography>
          <Stack spacing={1}>
            <JsonBlock
              value={deployment.observed_status}
              label={t("clusters.detail.raw_status")}
            />
            <JsonBlock value={deployment.spec} label={t("inference.detail.show_spec")} />
          </Stack>
        </CardContent>
      </Card>

      <FormDialog
        open={chatOpen}
        title={t("inference.detail.offer_in_chat")}
        loading={chatSaving}
        submitLabel={t("common.save")}
        onSubmit={() => void handleChatName()}
        onClose={() => setChatOpen(false)}
      >
        <Stack spacing={2} sx={{ mt: 1 }}>
          <TextField
            label={t("inference.detail.offer_as_label")}
            value={chatInput}
            autoFocus
            fullWidth
            onChange={(e) => setChatInput(e.target.value)}
          />
          <Typography variant="caption" color="text.secondary">
            {t("inference.detail.offer_as_help")}
          </Typography>
        </Stack>
      </FormDialog>

      <FormDialog
        open={scaleOpen}
        title={t("inference.detail.scale")}
        loading={scaling}
        submitLabel={t("inference.detail.apply")}
        onSubmit={() => void handleScale()}
        onClose={() => setScaleOpen(false)}
      >
        <Stack spacing={2} sx={{ mt: 1 }}>
          <TextField
            label={t("clusters.deployments.col_copies")}
            type="number"
            value={replicaInput}
            slotProps={{ htmlInput: { min: 1 } }}
            helperText={t("inference.detail.scale_help")}
            onChange={(e) =>
              setReplicaInput(Math.max(1, Number(e.target.value) || 1))
            }
          />
          {copiesThatFit !== null && (
            <Typography variant="body2" color="text.secondary" data-testid="scale-capacity">
              {t("inference.detail.scale_capacity", {
                perCopy: t("inference.detail.accelerators", { count: perCopy }),
                total: clusterAccelerators,
                fit: t("inference.detail.copies_fit", { count: copiesThatFit }),
              })}
            </Typography>
          )}
          {copiesThatFit !== null && replicaInput > copiesThatFit && (
            <Alert severity="warning" data-testid="scale-over-capacity">
              {t("inference.detail.scale_over", { count: copiesThatFit })}
            </Alert>
          )}
        </Stack>
      </FormDialog>
    </Stack>
  );
}
