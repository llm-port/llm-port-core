/**
 * Clusters — the entry point for the whole journey.
 *
 * Replaces "Inference Environments". One noun, one primary action, and a
 * banner that names the next step so an operator arriving with bare machines
 * is never left guessing which of four screens to visit first.
 */
import { useState, useCallback } from "react";
import { useNavigate } from "react-router";

import { inferenceApi } from "~/api/inference";
import type { EnvironmentNode, InferenceEnvironment } from "~/api/inference";
import { nodesApi, type ManagedNode } from "~/api/nodes";
import { useAsyncData } from "~/lib/useAsyncData";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Card from "@mui/material/Card";
import CardActionArea from "@mui/material/CardActionArea";
import CardContent from "@mui/material/CardContent";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import Grid from "@mui/material/Grid";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";

import AddIcon from "@mui/icons-material/Add";
import MemoryIcon from "@mui/icons-material/Memory";

import { ControlPlanesDialog } from "../inference/ControlPlanesDialog";
import { CreateClusterWizard } from "./CreateClusterWizard";
import { FoundClustersCard } from "./FoundClustersCard";
import { NextStepBanner } from "./NextStepBanner";
import { clusterStatusColor, memberSummary } from "./presentation";
import { fleetReadiness, gpuCount } from "./readiness";

interface FleetData {
  clusters: InferenceEnvironment[];
  nodes: ManagedNode[];
  members: Record<string, EnvironmentNode[]>;
}

const EMPTY: FleetData = { clusters: [], nodes: [], members: {} };

/**
 * Which nodes belong to which cluster — one call per cluster.
 *
 * A fan-out, so it must not sit in front of the list. It used to: the page
 * fetched the clusters, then the fleet, then N membership calls, and showed a
 * spinner until the last of them returned. A cluster whose nodes are
 * unreachable is exactly when one of those calls is slow, and also exactly
 * when the operator opened this page.
 */
async function loadMembers(
  clusters: InferenceEnvironment[],
): Promise<Record<string, EnvironmentNode[]>> {
  const lists = await Promise.all(
    clusters.map((c) =>
      inferenceApi.listEnvironmentNodes(c.id).catch(() => [] as EnvironmentNode[]),
    ),
  );
  const members: Record<string, EnvironmentNode[]> = {};
  clusters.forEach((c, i) => {
    members[c.id] = lists[i];
  });
  return members;
}

export default function ClustersPage() {
  const navigate = useNavigate();
  // The cluster list is the page. The fleet and the memberships decorate it
  // and arrive when they arrive.
  const clusters = useAsyncData(() => inferenceApi.listEnvironments(), [], {
    initialValue: [] as InferenceEnvironment[],
  });
  const nodes = useAsyncData(() => nodesApi.list(), [], {
    initialValue: [] as ManagedNode[],
  });
  const members = useAsyncData(
    () =>
      clusters.data.length === 0
        ? Promise.resolve({} as Record<string, EnvironmentNode[]>)
        : loadMembers(clusters.data),
    [clusters.data],
    { initialValue: {} as Record<string, EnvironmentNode[]> },
  );

  const data: FleetData = {
    clusters: clusters.data,
    nodes: nodes.data,
    members: members.data,
  };
  const loading = clusters.loading;
  const error = clusters.error ?? nodes.error;
  const refresh = useCallback(async () => {
    await Promise.all([clusters.refresh(), nodes.refresh(), members.refresh()]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const [wizardOpen, setWizardOpen] = useState(false);
  const [planesOpen, setPlanesOpen] = useState(false);

  const step = fleetReadiness(data.nodes, data.clusters);
  const claimed = new Set(
    Object.values(data.members).flatMap((list) => list.map((m) => m.node_id)),
  );

  function handleNextStep() {
    if (step.stage === "no-nodes") navigate("/admin/nodes");
    else setWizardOpen(true);
  }

  if (loading && data.clusters.length === 0) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", p: 4 }}>
        <CircularProgress />
      </Box>
    );
  }

  return (
    <Stack spacing={2} sx={{ pb: 4 }}>
      {error && <Alert severity="error">{error}</Alert>}

      <NextStepBanner step={step} onAction={handleNextStep} />

      {/* Only there when the machines run a cluster this server lost. */}
      <FoundClustersCard
        onTakenOver={(id) => {
          void refresh();
          navigate(`/admin/clusters/${id}`);
        }}
      />

      <Stack direction="row" alignItems="center" spacing={1}>
        <Typography variant="h6" sx={{ flexGrow: 1 }}>
          Clusters
        </Typography>
        <Button
          variant="contained"
          size="small"
          startIcon={<AddIcon />}
          disabled={data.nodes.length === 0}
          onClick={() => setWizardOpen(true)}
        >
          Create a cluster
        </Button>
      </Stack>

      {data.clusters.length === 0 ? (
        <Card variant="outlined">
          <CardContent sx={{ textAlign: "center", py: 5 }}>
            <MemoryIcon sx={{ fontSize: 40, color: "text.disabled" }} />
            <Typography variant="body1" sx={{ mt: 1 }}>
              No clusters yet
            </Typography>
            <Typography variant="body2" color="text.secondary">
              Group your machines into a cluster and they can serve models together.
            </Typography>
          </CardContent>
        </Card>
      ) : (
        <Grid container spacing={2}>
          {data.clusters.map((cluster) => {
            const members = data.members[cluster.id] ?? [];
            const accelerators = members.reduce((sum, m) => {
              const node = data.nodes.find((n) => n.id === m.node_id);
              return sum + (node ? gpuCount(node) : 0);
            }, 0);
            return (
              <Grid key={cluster.id} size={{ xs: 12, md: 6, lg: 4 }}>
                <Card variant="outlined" sx={{ height: "100%" }}>
                  <CardActionArea
                    sx={{ height: "100%", alignItems: "stretch" }}
                    onClick={() => navigate(`/admin/clusters/${cluster.id}`)}
                  >
                    <CardContent>
                      <Stack
                        direction="row"
                        alignItems="center"
                        justifyContent="space-between"
                        sx={{ mb: 1 }}
                      >
                        <Typography variant="subtitle1" fontWeight={600}>
                          {cluster.name}
                        </Typography>
                        <Chip
                          size="small"
                          label={cluster.status}
                          color={clusterStatusColor(cluster.status)}
                        />
                      </Stack>
                      <Typography variant="body2" color="text.secondary">
                        {memberSummary(members)}
                      </Typography>
                      <Typography variant="body2" color="text.secondary">
                        {accelerators > 0
                          ? `${accelerators} accelerator${accelerators === 1 ? "" : "s"}`
                          : "no accelerators reported"}
                      </Typography>
                      {cluster.status_message && (
                        <Typography variant="caption" color="warning.main" display="block" sx={{ mt: 1 }}>
                          {cluster.status_message}
                        </Typography>
                      )}
                    </CardContent>
                  </CardActionArea>
                </Card>
              </Grid>
            );
          })}
        </Grid>
      )}

      {/* Control planes are created for you with the first cluster. Kept
          reachable for the rare case of running more than one driver, but not
          on the path an operator has to walk. */}
      <Button
        size="small"
        color="inherit"
        sx={{ alignSelf: "flex-start", opacity: 0.7 }}
        onClick={() => setPlanesOpen(true)}
      >
        Advanced: schedulers
      </Button>

      <ControlPlanesDialog
        open={planesOpen}
        onClose={() => setPlanesOpen(false)}
        onChanged={() => void refresh()}
      />

      <CreateClusterWizard
        open={wizardOpen}
        nodes={data.nodes}
        busyNodeIds={claimed}
        onClose={() => setWizardOpen(false)}
        onCreated={(id) => {
          setWizardOpen(false);
          void refresh();
          navigate(`/admin/clusters/${id}`);
        }}
      />
    </Stack>
  );
}
