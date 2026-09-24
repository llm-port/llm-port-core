/**
 * One cluster — the picture, the machines, what it is serving.
 *
 * Replaces the environment detail page, whose seven equal-weight cards
 * (members, fabric, conditions, plan, artifacts, metrics, raw status) made an
 * operator read the whole domain model to find the one thing they came for.
 * Here the machinery is still present and unchanged, but it lives under
 * Advanced; the top of the page answers "is it working, and what do I do
 * next".
 */
import { useCallback, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate, useParams } from "react-router";

import { inferenceApi } from "~/api/inference";
import type {
  ComputePool,
  EnvironmentMetrics,
  EnvironmentNode,
  InferenceDeployment,
  InferenceEnvironment,
} from "~/api/inference";
import { models as modelsApi, type Model } from "~/api/llm";
import { nodesApi, type ManagedNode } from "~/api/nodes";
import {
  AsyncSection,
  CardRowSkeleton,
  TableSkeleton,
} from "~/components/AsyncSection";
import { ConfirmDialog } from "~/components/ConfirmDialog";
import { HostModelDialog } from "~/components/hosting/HostModelDialog";
import { useAsyncData } from "~/lib/useAsyncData";

import Accordion from "@mui/material/Accordion";
import AccordionDetails from "@mui/material/AccordionDetails";
import AccordionSummary from "@mui/material/AccordionSummary";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import Drawer from "@mui/material/Drawer";
import Grid from "@mui/material/Grid";
import IconButton from "@mui/material/IconButton";
import Stack from "@mui/material/Stack";
import Table from "@mui/material/Table";
import TableBody from "@mui/material/TableBody";
import TableCell from "@mui/material/TableCell";
import TableHead from "@mui/material/TableHead";
import TableRow from "@mui/material/TableRow";
import Typography from "@mui/material/Typography";

import AddIcon from "@mui/icons-material/Add";
import ArrowBackIcon from "@mui/icons-material/ArrowBack";
import CloseIcon from "@mui/icons-material/Close";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import RefreshIcon from "@mui/icons-material/Refresh";
import StopIcon from "@mui/icons-material/Stop";
import DeleteOutlineIcon from "@mui/icons-material/DeleteOutline";

import {
  JsonBlock,
  LabeledValue,
  PartialsNotice,
  asRecord,
  asRecordArray,
  asText,
} from "../inference/common";
import { ChooseNetworkDialog } from "./ChooseNetworkDialog";
import InsightsIcon from "@mui/icons-material/Insights";

import { ClusterTopology } from "./ClusterTopology";
import { NextStepBanner } from "./NextStepBanner";
import {
  clusterStatusColor,
  clusterStatusLabel,
  formatTimestamp,
  machineStatusLabel,
  nodeLabel,
  phaseLabel,
  poolLabel,
  poolMixSummary,
  poolsWorthShowing,
} from "./presentation";
import { clusterReadiness, gpuCount } from "./readiness";
import { copiesWanted, deploymentPhaseColor } from "../inference/common";

interface ClusterData {
  cluster: InferenceEnvironment | null;
  members: EnvironmentNode[];
  pools: ComputePool[];
  nodes: ManagedNode[];
  deployments: InferenceDeployment[];
  models: Model[];
  metrics: EnvironmentMetrics | null;
  metricsError: string | null;
}

/** How often the screens that show moving state re-read it. */
const LIVE_REFRESH_MS = 10_000;

const EMPTY: ClusterData = {
  cluster: null,
  members: [],
  pools: [],
  nodes: [],
  deployments: [],
  models: [],
  metrics: null,
  metricsError: null,
};

/**
 * Why this page does not gather its data in one `Promise.all`.
 *
 * It used to, and the result was that the whole screen stayed a spinner until
 * the slowest of six calls returned — with metrics awaited *after* the rest,
 * so it was serial on top of that. The failure mode is backwards: a cluster
 * whose nodes are unwell is precisely when one of those calls hangs, and also
 * precisely when the operator needs the page. They were made to wait longest
 * for the screen that would have told them what was wrong.
 *
 * So each source loads on its own. The identity call is the only one the page
 * frame needs; everything else fills in beside it and fails in its own place.
 */

/** Statuses in which the cluster may have Ray running on its machines. */
const RUNNING = new Set(["ready", "running", "degraded", "preparing"]);

export default function ClusterDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { t } = useTranslation();
  const navigate = useNavigate();
  // The cluster itself: one cheap GET, and the only thing the frame needs
  // before it can render a title, a status and the navigation.
  const {
    data: cluster,
    loading: clusterLoading,
    error: clusterError,
    refresh: refreshCluster,
    setError,
  } = useAsyncData(() => inferenceApi.getEnvironment(id!), [id], {
    initialValue: null as InferenceEnvironment | null,
    // A cluster coming up changes without anybody clicking, and a screen that
    // says "Starting" until the operator thinks to reload cannot be told
    // apart from one that is stuck.
    refreshMs: LIVE_REFRESH_MS,
  });

  // Everything below starts at the same moment and none of it blocks the page.
  const members = useAsyncData(
    () => inferenceApi.listEnvironmentNodes(id!),
    [id],
    { initialValue: [] as EnvironmentNode[], refreshMs: LIVE_REFRESH_MS },
  );
  const pools = useAsyncData(() => inferenceApi.listEnvironmentPools(id!), [id], {
    initialValue: [] as ComputePool[],
  });
  const fleet = useAsyncData(() => nodesApi.list(), [], {
    initialValue: [] as ManagedNode[],
  });
  const deployments = useAsyncData(
    () =>
      inferenceApi
        .listDeployments()
        .then((all) => all.filter((d) => d.environment_id === id)),
    [id],
    { initialValue: [] as InferenceDeployment[], refreshMs: LIVE_REFRESH_MS },
  );
  const models = useAsyncData(() => modelsApi.list(), [], {
    initialValue: [] as Model[],
  });
  // The one most likely to hang on a sick cluster, and now the one whose
  // hanging costs the least.
  const metrics = useAsyncData(() => inferenceApi.environmentMetrics(id!), [id], {
    initialValue: null as EnvironmentMetrics | null,
    refreshMs: LIVE_REFRESH_MS,
  });

  const data: ClusterData = {
    cluster,
    members: members.data,
    pools: pools.data,
    nodes: fleet.data,
    deployments: deployments.data,
    models: models.data,
    metrics: metrics.data,
    metricsError: metrics.error,
  };

  const refresh = useCallback(async () => {
    await Promise.all([
      refreshCluster(),
      members.refresh(),
      pools.refresh(),
      fleet.refresh(),
      deployments.refresh(),
      models.refresh(),
      metrics.refresh(),
    ]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  const error = clusterError;

  const [deployOpen, setDeployOpen] = useState(false);
  const [networkOpen, setNetworkOpen] = useState(false);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [removeTarget, setRemoveTarget] = useState<EnvironmentNode | null>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (clusterLoading && !cluster) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", p: 4 }}>
        <CircularProgress />
      </Box>
    );
  }
  if (!cluster) {
    return (
      <Box sx={{ p: 2 }}>
        <Alert severity="error">{error ?? t("clusters.detail.not_found")}</Alert>
      </Box>
    );
  }

  const step = clusterReadiness(cluster, data.members, data.deployments);
  const observed = asRecord(cluster.observed_status);
  const conditions = asRecordArray(observed.conditions);
  const accelerators = data.members.reduce((sum, m) => {
    const node = data.nodes.find((n) => n.id === m.node_id);
    return sum + (node ? gpuCount(node) : 0);
  }, 0);
  const nodeOf = (nodeId: string) => data.nodes.find((n) => n.id === nodeId);
  const hostOf = (nodeId: string) => nodeLabel(nodeOf(nodeId), nodeId.slice(0, 8));
  const selectedMember = data.members.find((m) => m.node_id === selectedNodeId) ?? null;

  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    try {
      await action();
      await refresh();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t("common.error_unexpected"));
    } finally {
      setBusy(false);
    }
  }

  /**
   * Stop, wait for the machines to be clean, then delete.
   *
   * Deleting the row alone does not reach the machines, so a running
   * cluster deleted outright would leave Ray going on the hardware with
   * nothing left to stop it. The backend refuses that; this does the stop
   * for the operator rather than making them do it and come back.
   */
  // Every member offline: a stop can never be confirmed, so do not wait for one.
  const membersUnreachable =
    data.members.length > 0 &&
    data.members.every((m) => (nodeOf(m.node_id)?.status ?? "offline") === "offline");

  async function deleteCluster() {
    if (!cluster) return;
    setError(null);
    try {
      if (RUNNING.has(cluster.status) && membersUnreachable) {
        // Nothing can confirm a stop, so waiting for one would only hang.
        setDeleting(t("clusters.detail.deleting"));
        await inferenceApi.deleteEnvironment(cluster.id, { force: true });
        navigate("/admin/clusters");
        return;
      }
      if (RUNNING.has(cluster.status)) {
        setDeleting(t("clusters.detail.stopping_for_delete"));
        if (cluster.desired_state === "running") {
          await inferenceApi.updateEnvironment(cluster.id, { desired_state: "stopped" });
        }
        const deadline = Date.now() + 5 * 60_000;
        for (;;) {
          const now = await inferenceApi.getEnvironment(cluster.id);
          if (!RUNNING.has(now.status)) break;
          if (Date.now() > deadline) {
            throw new Error(t("clusters.detail.stop_unconfirmed"));
          }
          await new Promise((resolve) => window.setTimeout(resolve, 3000));
        }
      }
      setDeleting(t("clusters.detail.deleting"));
      await inferenceApi.deleteEnvironment(cluster.id);
      navigate("/admin/clusters");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t("common.error_unexpected"));
    } finally {
      setDeleting(null);
    }
  }

  function handleNextStep() {
    switch (step.stage) {
      case "cluster-empty":
        navigate("/admin/nodes");
        break;
      case "no-network":
        setNetworkOpen(true);
        break;
      case "stopped":
        void run(() =>
          inferenceApi.updateEnvironment(cluster!.id, { desired_state: "running" }),
        );
        break;
      case "ready":
        setDeployOpen(true);
        break;
      case "degraded":
        // Clears the failure backoff and queues a pass straight away.
        void run(() => inferenceApi.reconcileEnvironment(cluster!.id));
        break;
      default:
        break;
    }
  }

  return (
    <Stack spacing={2} sx={{ pb: 4 }}>
      {error && <Alert severity="error">{error}</Alert>}

      <Stack direction="row" alignItems="center" spacing={1} flexWrap="wrap">
        <Button size="small" startIcon={<ArrowBackIcon />} onClick={() => navigate("/admin/clusters")}>
          {t("clusters.list.title")}
        </Button>
        <Typography variant="h6" sx={{ flexGrow: 1 }}>
          {cluster.name}
        </Typography>
        <Chip
          size="small"
          label={clusterStatusLabel(cluster.status)}
          color={clusterStatusColor(cluster.status)}
        />
        <Button
          size="small"
          startIcon={<RefreshIcon />}
          disabled={busy}
          onClick={() => void run(() => inferenceApi.reconcileEnvironment(cluster.id))}
        >
          {t("clusters.detail.check_now")}
        </Button>
        {cluster.desired_state === "running" ? (
          <Button
            size="small"
            color="warning"
            startIcon={<StopIcon />}
            disabled={busy}
            onClick={() =>
              void run(() =>
                inferenceApi.updateEnvironment(cluster.id, { desired_state: "stopped" }),
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
            disabled={busy}
            onClick={() =>
              void run(() =>
                inferenceApi.updateEnvironment(cluster.id, { desired_state: "running" }),
              )
            }
          >
            {t("common.start")}
          </Button>
        )}
        <Button
          variant="contained"
          size="small"
          startIcon={<AddIcon />}
          onClick={() => setDeployOpen(true)}
        >
          {t("clusters.next.ready.action")}
        </Button>
        {/* There was no way to delete a cluster from the console at all:
            one built wrong stayed in the list for good. */}
        <Button
          size="small"
          color="error"
          startIcon={<DeleteOutlineIcon />}
          disabled={busy || deleting !== null}
          onClick={() => setDeleteOpen(true)}
        >
          {t("common.delete")}
        </Button>
      </Stack>
      {deleting && <Alert severity="info">{deleting}</Alert>}

      <NextStepBanner
        step={step}
        onAction={handleNextStep}
        busy={busy}
        rows={(cluster.progress?.machines ?? []).map((m) => ({
          label: hostOf(m.node_id),
          pct: m.progress_pct,
          message: m.message,
        }))}
      />

      {/* --- at a glance ------------------------------------------------ */}
      {/* Machines come from one call and the accelerator figures from
          another, so the row waits for whichever it needs rather than for
          both — and a number that is not in yet is a skeleton, never a 0. */}
      <AsyncSection
        loading={members.loading || metrics.loading}
        empty={data.members.length === 0 && !data.metrics}
        skeleton={<CardRowSkeleton count={4} />}
      >
      <Grid container spacing={2}>
        <Grid size={{ xs: 6, md: 3 }}>
          <Card variant="outlined">
            <CardContent>
              <LabeledValue
                label={t("clusters.detail.machines")}
                value={t("clusters.detail.machines_up", {
                  up: data.metrics?.nodes_alive ?? data.members.length,
                  total: data.members.length,
                })}
              />
            </CardContent>
          </Card>
        </Grid>
        <Grid size={{ xs: 6, md: 3 }}>
          <Card variant="outlined">
            <CardContent>
              <LabeledValue
                label={t("clusters.detail.accelerators")}
                value={accelerators > 0 ? String(accelerators) : t("clusters.detail.none_reported")}
              />
            </CardContent>
          </Card>
        </Grid>
        <Grid size={{ xs: 6, md: 3 }}>
          <Card variant="outlined">
            <CardContent>
              <LabeledValue
                label={t("clusters.detail.free_accelerators")}
                value={
                  data.metrics
                    ? t("clusters.detail.n_of_m", {
                        n: data.metrics.gpus_available,
                        m: data.metrics.gpus_total,
                      })
                    : t("clusters.unknown_machine")
                }
              />
            </CardContent>
          </Card>
        </Grid>
        <Grid size={{ xs: 6, md: 3 }}>
          <Card variant="outlined">
            <CardContent>
              <LabeledValue label={t("clusters.deployments.title")} value={String(data.deployments.length)} />
            </CardContent>
          </Card>
        </Grid>
      </Grid>
      </AsyncSection>

      {/* --- the picture ------------------------------------------------ */}
      <Card variant="outlined">
        <CardContent>
          <Stack
            direction="row"
            justifyContent="space-between"
            alignItems="center"
            sx={{ mb: 1 }}
          >
            <Typography variant="subtitle2">{t("clusters.detail.wiring")}</Typography>
            {/* The cluster's own Grafana dashboard.
                It was rendered from the template and nothing in the product
                linked to it, so the panels existed and could not be found.
                Here rather than behind "Advanced": the numbers it draws are
                the ones somebody looking at this card came for, and an
                operator should not have to know the word "Advanced" to see
                whether their hardware is working. */}
            {data.metrics?.dashboard_url && (
              <Button
                size="small"
                component="a"
                href={data.metrics.dashboard_url}
                target="_blank"
                rel="noopener"
                startIcon={<InsightsIcon />}
              >
                {t("clusters.detail.open_dashboard")}
              </Button>
            )}
          </Stack>
          <AsyncSection
            loading={members.loading || fleet.loading}
            error={members.error}
            empty={data.members.length === 0}
            height={420}
          >
            <ClusterTopology
              cluster={cluster}
              members={data.members}
              nodes={data.nodes}
              selectedNodeId={selectedNodeId}
              onSelect={setSelectedNodeId}
            />
          </AsyncSection>
          <Typography variant="caption" color="text.secondary">
            {t("clusters.detail.click_machine")}
          </Typography>
        </CardContent>
      </Card>

      {/* --- only when the machines are not all alike -------------------- */}
      {!pools.loading && poolsWorthShowing(data.pools) && (
        <Card variant="outlined">
          <CardContent>
            <Typography variant="subtitle2" gutterBottom>
              {poolMixSummary(data.pools)}
            </Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
              {t("clusters.detail.pools_explain")}
            </Typography>
            <Stack spacing={1}>
              {data.pools.map((pool) => (
                <Stack
                  key={pool.id}
                  direction="row"
                  alignItems="center"
                  spacing={1}
                  sx={{ justifyContent: "space-between" }}
                >
                  <Typography variant="body2">{pool.name}</Typography>
                  <Typography variant="caption" color="text.secondary">
                    {poolLabel(pool)}
                  </Typography>
                </Stack>
              ))}
            </Stack>
          </CardContent>
        </Card>
      )}

      {/* --- what it is serving ----------------------------------------- */}
      <Card variant="outlined">
        <CardContent>
          <Typography variant="subtitle2" gutterBottom>
            {t("clusters.detail.models")}
          </Typography>
          <AsyncSection
            loading={deployments.loading}
            error={deployments.error}
            empty={data.deployments.length === 0}
            skeleton={<TableSkeleton rows={2} />}
          >
          {data.deployments.length === 0 ? (
            <Typography variant="body2" color="text.secondary">
              {t("clusters.detail.nothing_deployed")}
            </Typography>
          ) : (
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>{t("clusters.deployments.col_name")}</TableCell>
                  <TableCell>{t("clusters.deployments.col_model")}</TableCell>
                  <TableCell>{t("clusters.deployments.col_state")}</TableCell>
                  <TableCell align="right">{t("clusters.deployments.col_copies")}</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {data.deployments.map((deployment) => (
                  <TableRow
                    key={deployment.id}
                    hover
                    sx={{ cursor: "pointer" }}
                    onClick={() => navigate(`/admin/deployments/${deployment.id}`)}
                  >
                    <TableCell>{deployment.name}</TableCell>
                    <TableCell>
                      {data.models.find((m) => m.id === deployment.model_id)?.display_name ??
                        t("clusters.detail.unknown_model")}
                    </TableCell>
                    <TableCell>
                      <Chip
                        size="small"
                        label={phaseLabel(deployment.phase)}
                        color={deploymentPhaseColor(deployment.phase)}
                      />
                    </TableCell>
                    <TableCell align="right">
                      {deployment.ready_replicas} / {copiesWanted(deployment)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
          </AsyncSection>
        </CardContent>
      </Card>

      {/* --- advanced ---------------------------------------------------- */}
      {/* Unmounted while collapsed: "behind Advanced" should mean the
          machinery is genuinely absent from the page at rest, not merely
          invisible. */}
      <Accordion
        variant="outlined"
        disableGutters
        slotProps={{ transition: { unmountOnExit: true } }}
      >
        <AccordionSummary expandIcon={<ExpandMoreIcon />}>
          <Typography variant="subtitle2">{t("clusters.detail.advanced")}</Typography>
        </AccordionSummary>
        <AccordionDetails>
          <Stack spacing={2}>
            <Box>
              <Typography variant="caption" color="text.secondary">
                {t("clusters.detail.health_checks")}
              </Typography>
              {conditions.length === 0 ? (
                <Typography variant="body2" color="text.secondary">
                  {t("clusters.detail.no_checks")}
                </Typography>
              ) : (
                <Stack spacing={0.5} sx={{ mt: 0.5 }}>
                  {conditions.map((condition, index) => (
                    <Stack
                      key={asText(condition.type) ?? index}
                      direction="row"
                      spacing={1}
                      alignItems="center"
                    >
                      <Chip
                        size="small"
                        color={asText(condition.status) === "True" ? "success" : "warning"}
                        label={asText(condition.type) ?? t("clusters.detail.check")}
                      />
                      <Typography variant="body2">{asText(condition.message)}</Typography>
                    </Stack>
                  ))}
                </Stack>
              )}
            </Box>

            {data.metricsError ? (
              <Alert severity="info">
                {t("clusters.detail.metrics_unavailable", { error: data.metricsError })}
              </Alert>
            ) : (
              data.metrics && <PartialsNotice partials={data.metrics.partials} />
            )}

            {data.metrics && data.metrics.scrape_targets.length > 0 && (
              <Typography variant="caption" color="text.secondary">
                {t("clusters.detail.prometheus_targets", {
                  targets: data.metrics.scrape_targets.map((target) => target.url).join(", "),
                })}
              </Typography>
            )}


            <Button size="small" onClick={() => setNetworkOpen(true)} sx={{ alignSelf: "flex-start" }}>
              {t("clusters.detail.change_network")}
            </Button>

            <JsonBlock value={cluster.observed_status} label={t("clusters.detail.raw_status")} />
          </Stack>
        </AccordionDetails>
      </Accordion>

      {/* --- node drawer -------------------------------------------------- */}
      <Drawer
        anchor="right"
        open={selectedMember !== null}
        onClose={() => setSelectedNodeId(null)}
        slotProps={{ paper: { sx: { width: { xs: "100%", sm: 380 }, p: 2 } } }}
      >
        {selectedMember && (
          <Stack spacing={2}>
            <Stack direction="row" alignItems="center">
              <Box sx={{ flexGrow: 1 }}>
                <Typography variant="h6">{hostOf(selectedMember.node_id)}</Typography>
                <Typography variant="caption" color="text.secondary">
                  {selectedMember.role === "head"
                    ? t("clusters.detail.drawer_leads")
                    : t("clusters.detail.drawer_worker")}
                  {nodeOf(selectedMember.node_id)?.host
                    ? ` · ${nodeOf(selectedMember.node_id)?.host}`
                    : ""}
                </Typography>
              </Box>
              <IconButton aria-label={t("common.close")} onClick={() => setSelectedNodeId(null)}>
                <CloseIcon />
              </IconButton>
            </Stack>

            <Grid container spacing={1.5}>
              <Grid size={6}>
                <LabeledValue
                  label={t("clusters.detail.fleet_status")}
                  value={
                    nodeOf(selectedMember.node_id)?.status
                      ? machineStatusLabel(nodeOf(selectedMember.node_id)!.status)
                      : t("clusters.unknown_machine")
                  }
                />
              </Grid>
              <Grid size={6}>
                <LabeledValue
                  label={t("clusters.detail.in_cluster")}
                  value={
                    selectedMember.member_status === "alive"
                      ? t("clusters.detail.member_alive")
                      : selectedMember.member_status === "dead"
                        ? t("clusters.topology.legend_dropped")
                        : (selectedMember.member_status ?? t("clusters.detail.not_reported"))
                  }
                />
              </Grid>
              <Grid size={6}>
                <LabeledValue
                  label={t("clusters.detail.accelerators")}
                  value={String(gpuCount(nodeOf(selectedMember.node_id) ?? ({} as ManagedNode)))}
                />
              </Grid>
              <Grid size={6}>
                <LabeledValue
                  label={t("clusters.detail.joined")}
                  value={formatTimestamp(selectedMember.joined_at)}
                />
              </Grid>
              {poolsWorthShowing(data.pools) && (
                <Grid size={6}>
                  <LabeledValue
                    label={t("clusters.detail.machine_group")}
                    value={
                      data.pools.find((p) => p.id === selectedMember.compute_pool_id)?.name ??
                      t("clusters.detail.not_grouped")
                    }
                  />
                </Grid>
              )}
            </Grid>

            <Box>
              <Typography variant="caption" color="text.secondary" display="block">
                {t("clusters.detail.drawer_deployments")}
              </Typography>
              {data.deployments.length === 0 ? (
                <Typography variant="body2" color="text.secondary">
                  {t("clusters.detail.none")}
                </Typography>
              ) : (
                <Stack spacing={0.5} sx={{ mt: 0.5 }}>
                  {data.deployments.map((d) => (
                    <Typography key={d.id} variant="body2">
                      {d.name} — {phaseLabel(d.phase)}
                    </Typography>
                  ))}
                </Stack>
              )}
              {/* Serve reports replica counts, not placement, so we do not
                  claim which machine runs which copy. */}
              <Typography variant="caption" color="text.secondary">
                {t("clusters.detail.placement_unknown")}
              </Typography>
            </Box>

            <Button
              size="small"
              color="error"
              variant="outlined"
              disabled={busy}
              onClick={() => setRemoveTarget(selectedMember)}
            >
              {t("clusters.detail.remove_machine")}
            </Button>
          </Stack>
        )}
      </Drawer>

      <ChooseNetworkDialog
        open={networkOpen}
        clusterId={cluster.id}
        onClose={() => setNetworkOpen(false)}
        onApplied={() => {
          setNetworkOpen(false);
          void refresh();
        }}
      />

      <HostModelDialog
        open={deployOpen}
        models={data.models}
        clusterId={cluster.id}
        lockCluster
        onClose={() => setDeployOpen(false)}
        onHosted={(deploymentId) => {
          setDeployOpen(false);
          navigate(`/admin/deployments/${deploymentId}`);
        }}
      />

      <ConfirmDialog
        open={deleteOpen}
        title={t("clusters.detail.delete_title")}
        message={
          data.deployments.some((d) => d.desired_state !== "deleted")
            ? t("clusters.detail.delete_has_deployments")
            : RUNNING.has(cluster.status) && membersUnreachable
              ? t("clusters.detail.delete_offline")
              : RUNNING.has(cluster.status)
                ? t("clusters.detail.delete_running")
                : t("clusters.detail.delete_stopped")
        }
        confirmLabel={t("common.delete")}
        loading={deleting !== null}
        onConfirm={() => {
          setDeleteOpen(false);
          void deleteCluster();
        }}
        onClose={() => setDeleteOpen(false)}
      />

      <ConfirmDialog
        open={removeTarget !== null}
        title={t("clusters.detail.remove_title")}
        message={
          removeTarget
            ? t("clusters.detail.remove_message", { name: hostOf(removeTarget.node_id) })
            : ""
        }
        confirmLabel={t("common.remove")}
        loading={busy}
        onConfirm={() => {
          const target = removeTarget;
          setRemoveTarget(null);
          setSelectedNodeId(null);
          if (target) {
            void run(() => inferenceApi.removeEnvironmentNode(cluster.id, target.node_id));
          }
        }}
        onClose={() => setRemoveTarget(null)}
      />
    </Stack>
  );
}
