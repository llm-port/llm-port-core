/**
 * Clusters the machines still run that this server does not manage.
 *
 * After a server is rebuilt without its database, or restored from an older
 * backup, its machines go on running the clusters and models the old server
 * started. Recreating them would restart every model; taking one over records
 * it here as it runs. Before anything is recorded the server has the cluster
 * confirm that what it would deploy for each model is exactly what runs, so
 * nothing restarts afterwards either.
 *
 * Shows nothing when there is nothing to show: in a server that never lost
 * anything, no machine runs a cluster it does not know.
 */
import { useCallback, useEffect, useState } from "react";

import Alert from "@mui/material/Alert";
import Button from "@mui/material/Button";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import Chip from "@mui/material/Chip";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import Stack from "@mui/material/Stack";
import Table from "@mui/material/Table";
import TableBody from "@mui/material/TableBody";
import TableCell from "@mui/material/TableCell";
import TableHead from "@mui/material/TableHead";
import TableRow from "@mui/material/TableRow";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import {
  inferenceApi,
  type FoundCluster,
  type FoundClusters,
} from "~/api/inference";

function headName(cluster: FoundCluster): string {
  return cluster.head?.name ?? cluster.head?.hostname ?? cluster.address ?? "cluster";
}

/** A name to suggest for the cluster: its head machine's. */
export function suggestClusterName(cluster: FoundCluster): string {
  const head = cluster.head?.hostname ?? cluster.head?.name ?? "found";
  return `${head}-cluster`;
}

function TakeOverDialog({
  cluster,
  onClose,
  onTakenOver,
}: {
  cluster: FoundCluster;
  onClose: () => void;
  onTakenOver: (environmentId: string) => void;
}) {
  const [name, setName] = useState(() => suggestClusterName(cluster));
  const [aliases, setAliases] = useState<Record<string, string>>(() =>
    Object.fromEntries(cluster.apps.map((a) => [a.app_name, a.suggested_alias])),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const incomplete = !name.trim() || cluster.apps.some((a) => !(aliases[a.app_name] ?? "").trim());

  async function takeOver() {
    setBusy(true);
    setError(null);
    try {
      const result = await inferenceApi.takeOverCluster({
        node_id: cluster.described_by,
        name: name.trim(),
        aliases: Object.fromEntries(
          Object.entries(aliases).map(([app, alias]) => [app, alias.trim()]),
        ),
      });
      onTakenOver(result.environment_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open onClose={busy ? undefined : onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Take over the cluster on {headName(cluster)}</DialogTitle>
      <DialogContent>
        <Typography variant="body2" sx={{ mb: 2 }}>
          The cluster and its models are recorded here as they run; nothing
          restarts. First the cluster is asked to confirm that what this server
          would deploy for each model is exactly what runs. If anything
          differs, nothing is recorded and you are told what.
        </Typography>
        {error && (
          <Alert severity="error" sx={{ mb: 2 }} data-testid="takeover-error">
            {error}
          </Alert>
        )}
        <Stack spacing={2}>
          <TextField
            autoFocus
            fullWidth
            size="small"
            label="Cluster name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            inputProps={{ "data-testid": "takeover-name" }}
          />
          {cluster.apps.map((app) => (
            <TextField
              key={app.app_name}
              fullWidth
              size="small"
              label={`${app.model_id}: name at the gateway`}
              value={aliases[app.app_name] ?? ""}
              onChange={(e) => setAliases({ ...aliases, [app.app_name]: e.target.value })}
              helperText={
                app.notes.length > 0
                  ? app.notes.join(" ")
                  : "What clients ask for as the model. The old server's name is not stored on the machines."
              }
              inputProps={{ "data-testid": `takeover-alias-${app.deployment_id}` }}
            />
          ))}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={busy}>
          Cancel
        </Button>
        <Button
          variant="contained"
          disabled={incomplete || busy}
          onClick={() => void takeOver()}
          data-testid="takeover-confirm"
        >
          {busy ? "Checking with the cluster…" : "Take over"}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

function FoundClusterRow({
  cluster,
  onTakeOver,
}: {
  cluster: FoundCluster;
  onTakeOver: () => void;
}) {
  return (
    <Stack spacing={1} data-testid={`found-cluster-${cluster.described_by}`}>
      <Stack direction="row" alignItems="center" spacing={1} flexWrap="wrap" useFlexGap>
        <Typography variant="subtitle2" sx={{ flexGrow: 1 }}>
          {headName(cluster)}
          <Typography component="span" variant="caption" color="text.secondary">
            {" · "}
            {cluster.members.length} machine{cluster.members.length === 1 ? "" : "s"}
            {cluster.runtime_version ? ` · runtime ${cluster.runtime_version}` : ""}
          </Typography>
        </Typography>
        <Button
          size="small"
          variant="outlined"
          disabled={!cluster.can_take_over}
          onClick={onTakeOver}
          data-testid="takeover-open"
        >
          Take over…
        </Button>
      </Stack>
      <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap>
        {cluster.members.map((m) => (
          <Chip
            key={m.runtime_node_id ?? m.ip ?? m.name}
            size="small"
            variant="outlined"
            color={m.node_id ? "default" : "warning"}
            label={`${m.name ?? m.ip}${m.role === "head" ? " (head)" : ""}`}
          />
        ))}
      </Stack>
      {cluster.apps.length > 0 ? (
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Model</TableCell>
              <TableCell>Copies</TableCell>
              <TableCell>GPUs per copy</TableCell>
              <TableCell>State</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {cluster.apps.map((app) => (
              <TableRow key={app.app_name}>
                <TableCell>
                  <Typography variant="body2">{app.model_id}</Typography>
                  {app.hf_repo_id && (
                    <Typography variant="caption" color="text.secondary">
                      {app.hf_repo_id}
                    </Typography>
                  )}
                </TableCell>
                <TableCell>
                  {app.running_copies} of {app.copies} running
                </TableCell>
                <TableCell>{app.gpus_per_copy ?? "-"}</TableCell>
                <TableCell>
                  <Chip
                    size="small"
                    variant="outlined"
                    label={app.status ?? "unknown"}
                    color={app.status === "RUNNING" ? "success" : "default"}
                  />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      ) : (
        <Typography variant="body2" color="text.secondary">
          It serves no model LLM.Port deployed.
        </Typography>
      )}
      {cluster.other_apps.length > 0 && (
        <Typography variant="caption" color="text.secondary">
          Also running, not deployed by LLM.Port and left as they are: {cluster.other_apps.join(", ")}.
        </Typography>
      )}
      {cluster.blockers.length > 0 && (
        <Alert severity="warning" data-testid="takeover-blockers">
          {cluster.blockers.join(" ")}
        </Alert>
      )}
    </Stack>
  );
}

export function FoundClustersCard({
  onTakenOver,
}: {
  onTakenOver: (environmentId: string) => void;
}) {
  const [found, setFound] = useState<FoundClusters | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [looking, setLooking] = useState(false);
  const [taking, setTaking] = useState<FoundCluster | null>(null);

  const load = useCallback(async () => {
    setLooking(true);
    try {
      setFound(await inferenceApi.foundClusters());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLooking(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (error) {
    return (
      <Alert severity="warning" action={<Button size="small" color="inherit" onClick={() => void load()}>Retry</Button>}>
        Could not look for clusters your machines already run: {error}
      </Alert>
    );
  }
  if (!found || (found.clusters.length === 0 && found.unreadable.length === 0)) return null;

  return (
    <Card variant="outlined" data-testid="found-clusters">
      <CardContent>
        <Stack direction="row" alignItems="center" sx={{ mb: 0.5 }}>
          <Typography variant="subtitle1" fontWeight={600} sx={{ flexGrow: 1 }}>
            Running on your machines, not managed here
          </Typography>
          <Button size="small" color="inherit" disabled={looking} onClick={() => void load()}>
            {looking ? "Looking…" : "Look again"}
          </Button>
        </Stack>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          An LLM.Port server started these clusters and no longer knows them:
          it was rebuilt, or restored from an older backup. Take one over to
          manage it here again; its models keep serving throughout.
        </Typography>
        <Stack spacing={3}>
          {found.clusters.map((cluster) => (
            <FoundClusterRow
              key={cluster.address ?? cluster.described_by}
              cluster={cluster}
              onTakeOver={() => setTaking(cluster)}
            />
          ))}
        </Stack>
        {found.unreadable.length > 0 && (
          <Stack spacing={0.5} sx={{ mt: found.clusters.length > 0 ? 2 : 0 }}>
            {found.unreadable.map((m) => (
              <Typography key={m.node_id} variant="caption" color="text.secondary">
                {m.name} runs the cluster runtime but could not say what it serves: {m.error}
              </Typography>
            ))}
          </Stack>
        )}
      </CardContent>
      {taking && (
        <TakeOverDialog
          cluster={taking}
          onClose={() => setTaking(null)}
          onTakenOver={(id) => {
            setTaking(null);
            onTakenOver(id);
          }}
        />
      )}
    </Card>
  );
}
