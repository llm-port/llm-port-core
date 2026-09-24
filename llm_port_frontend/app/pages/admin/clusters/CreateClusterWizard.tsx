/**
 * Create a cluster — three questions instead of six screens.
 *
 * Behind these steps sit the calls the old environment page made the operator
 * discover and sequence by hand: ensure a control plane, create the
 * environment, add each member, plan the fabric, apply the plan, reconcile.
 * None of those six nouns appears here. The wizard runs them in order and
 * reports what it is doing in the operator's words.
 *
 * The head is chosen, not asked: it is derivable (most accelerators) and an
 * operator has no basis to answer it on their first cluster. It is stated,
 * with an override, rather than hidden.
 */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { inferenceApi } from "~/api/inference";
import type { EnvironmentPlan, RuntimeBundle } from "~/api/inference";
import type { ManagedNode } from "~/api/nodes";
import { NetworkPicker } from "./NetworkPicker";
import { machineStatusLabel, nodeLabel } from "./presentation";
import { gpuCount, suggestHeadNode } from "./readiness";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Checkbox from "@mui/material/Checkbox";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import LinearProgress from "@mui/material/LinearProgress";
import MenuItem from "@mui/material/MenuItem";
import Stack from "@mui/material/Stack";
import Step from "@mui/material/Step";
import StepLabel from "@mui/material/StepLabel";
import Stepper from "@mui/material/Stepper";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

const STEPS = ["name", "machines", "network"] as const;

export interface CreateClusterWizardProps {
  open: boolean;
  nodes: ManagedNode[];
  /** Node ids already serving in another cluster — shown, not blocked. */
  busyNodeIds?: Set<string>;
  onClose: () => void;
  onCreated: (clusterId: string) => void;
}

export function CreateClusterWizard({
  open,
  nodes,
  busyNodeIds,
  onClose,
  onCreated,
}: CreateClusterWizardProps) {
  const { t } = useTranslation();
  const [step, setStep] = useState(0);
  const [name, setName] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [headId, setHeadId] = useState("");
  const [working, setWorking] = useState("");
  const [error, setError] = useState<string | null>(null);

  const [bundles, setBundles] = useState<RuntimeBundle[]>([]);

  const [notice, setNotice] = useState<string | null>(null);

  const [clusterId, setClusterId] = useState<string | null>(null);
  const [plan, setPlan] = useState<EnvironmentPlan | null>(null);
  const [candidateId, setCandidateId] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setStep(0);
    setName("");
    setSelected([]);
    setHeadId("");
    setWorking("");
    setError(null);
    setClusterId(null);
    setPlan(null);
    setCandidateId(null);
    setBundles([]);
    setNotice(null);
  }, [open]);

  // Which runtime image each machine picked will run.  Asked on every change
  // because the answer is per machine: adding an x86 box to a set of aarch64
  // ones does not change what they run, it adds a second image.
  useEffect(() => {
    if (!open || selected.length === 0) {
      setBundles([]);
      return;
    }
    let cancelled = false;
    void (async () => {
      const found = await inferenceApi
        .listRuntimeBundles(selected)
        .catch(() => [] as RuntimeBundle[]);
      if (cancelled) return;
      setBundles(found);
    })();
    return () => {
      cancelled = true;
    };
  }, [open, selected]);

  const chosen = nodes.filter((n) => selected.includes(n.id));
  // node id -> the image that machine resolves to, if any.  The server sends
  // the assignment it will actually make, so nothing is re-derived here.
  const bundleByNode = new Map<string, RuntimeBundle>();
  for (const bundle of bundles) {
    for (const id of bundle.compatible_node_ids) bundleByNode.set(id, bundle);
  }
  const unsupported = selected.filter((id) => !bundleByNode.has(id));
  // Why a machine resolved to nothing.  The server explains it per bundle;
  // any one of those reasons is the answer the operator needs ("this is an
  // AMD card", "this machine has not reported its platform yet").
  function whyUnsupported(nodeId: string): string {
    for (const bundle of bundles) {
      const reason = bundle.incompatible[nodeId];
      if (reason) return reason;
    }
    return t("clusters.create.no_image_reason");
  }

  function toggle(nodeId: string) {
    // Computed outside the updater on purpose: a state updater must be pure,
    // and setting the head from inside `setSelected` meant it never took
    // (React may call the updater more than once, and it reads a stale
    // `headId` from the closure). The browser test caught this as "no machine
    // is shown as leading the cluster".
    const next = selected.includes(nodeId)
      ? selected.filter((id) => id !== nodeId)
      : [...selected, nodeId];
    setSelected(next);

    // Keep the suggestion current as the selection changes, unless the
    // operator has overridden it with a machine still in the set.
    if (!next.includes(headId)) {
      const suggestion = suggestHeadNode(nodes.filter((n) => next.includes(n.id)));
      setHeadId(suggestion?.id ?? "");
    }
  }

  /** Reuse a control plane if one exists; otherwise make one silently. */
  async function ensureControlPlane(): Promise<string> {
    const existing = await inferenceApi.listControlPlanes();
    const usable = existing.find((cp) => cp.enabled) ?? existing[0];
    if (usable) return usable.id;

    const drivers = await inferenceApi.listDrivers().catch(() => [] as string[]);
    if (drivers.length === 0) {
      throw new Error(t("clusters.create.no_driver"));
    }
    const created = await inferenceApi.createControlPlane({
      name: "default",
      driver: drivers[0],
      description: t("clusters.create.control_plane_description"),
    });
    return created.id;
  }

  /** Steps 1-2 committed, then ask the backend what networks exist. */
  async function createAndPlan() {
    setError(null);
    setWorking(t("clusters.create.working_create"));
    try {
      const controlPlaneId = await ensureControlPlane();
      const cluster = await inferenceApi.createEnvironment({
        control_plane_id: controlPlaneId,
        name: name.trim(),
        head_node_id: headId || null,
      });
      setClusterId(cluster.id);

      setWorking(t("clusters.create.working_members"));
      for (const node of chosen) {
        await inferenceApi.addEnvironmentNode(
          cluster.id,
          node.id,
          node.id === headId ? "head" : "worker",
        );
      }

      setWorking(t("clusters.network.looking"));
      const found = await inferenceApi.planEnvironment(cluster.id);
      setPlan(found);
      setCandidateId(found.recommended_candidate_id);
      setStep(2);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setWorking("");
    }
  }

  /** Step 3: bind the chosen network and start the cluster.
   *
   * The server refuses an approval made against inventory that has since
   * moved, which is right -- but it used to leave the operator staring at a
   * conflict with nothing to do. A machine that reports fresh network facts
   * while someone is reading the options is normal, especially just after it
   * enrols, so the recovery is to re-derive and show the current choice
   * rather than to fail.
   */
  async function applyAndStart(isRetry = false) {
    if (!clusterId || !plan) return;
    setError(null);
    setNotice(null);
    setWorking(t("clusters.network.applying"));
    try {
      await inferenceApi.applyEnvironmentPlan(clusterId, {
        plan,
        selected_candidate_id: candidateId,
      });
      setWorking(t("clusters.network.starting"));
      await inferenceApi.reconcileEnvironment(clusterId);
      onCreated(clusterId);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : String(err);
      const stale = /stale plan/i.test(message);
      if (stale && !isRetry) {
        setWorking(t("clusters.create.working_replan"));
        try {
          const refreshed = await inferenceApi.planEnvironment(clusterId);
          setPlan(refreshed);
          // Keep the operator's choice when the same network is still on
          // offer; otherwise fall back to the new recommendation.
          const chosenCidr = plan.candidates.find(
            (c) => c.candidate_id === candidateId,
          )?.cidr;
          const sameNetwork = refreshed.candidates.find(
            (c) => c.cidr === chosenCidr,
          );
          setCandidateId(
            sameNetwork?.candidate_id ?? refreshed.recommended_candidate_id,
          );
          setNotice(t("clusters.create.replanned"));
          return;
        } catch (retryErr: unknown) {
          setError(
            retryErr instanceof Error ? retryErr.message : String(retryErr),
          );
          return;
        } finally {
          setWorking("");
        }
      }
      setError(message);
    } finally {
      setWorking("");
    }
  }

  const blocked = (plan?.blockers.length ?? 0) > 0;
  const busy = Boolean(working);

  return (
    <Dialog open={open} onClose={busy ? undefined : onClose} maxWidth="md" fullWidth>
      <DialogTitle>{t("clusters.create.title")}</DialogTitle>
      <DialogContent dividers>
        <Stepper activeStep={step} sx={{ mb: 3 }}>
          {STEPS.map((key) => (
            <Step key={key}>
              <StepLabel>{t(`clusters.create.step_${key}`)}</StepLabel>
            </Step>
          ))}
        </Stepper>

        {error && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {error}
          </Alert>
        )}

        {notice && (
          <Alert severity="info" sx={{ mb: 2 }}>
            {notice}
          </Alert>
        )}

        {busy && (
          <Box sx={{ mb: 2 }}>
            <Typography variant="body2" sx={{ mb: 0.5 }}>
              {working}
            </Typography>
            <LinearProgress />
          </Box>
        )}

        {step === 0 && (
          <Stack spacing={2}>
            <Typography variant="body2" color="text.secondary">
              {t("clusters.create.intro")}
            </Typography>
            <TextField
              label={t("clusters.create.name")}
              value={name}
              autoFocus
              fullWidth
              placeholder="dgx-pair"
              onChange={(e) => setName(e.target.value)}
            />
          </Stack>
        )}

        {step === 1 && (
          <Stack spacing={1}>
            <Typography variant="body2" color="text.secondary">
              {t("clusters.create.pick_intro")}
            </Typography>
            {nodes.length === 0 && (
              <Alert severity="info">
                {t("clusters.create.no_machines")}
              </Alert>
            )}
            {nodes.map((node) => {
              const isSelected = selected.includes(node.id);
              const gpus = gpuCount(node);
              return (
                <Stack
                  key={node.id}
                  direction="row"
                  spacing={1}
                  alignItems="center"
                  sx={{
                    border: 1,
                    borderColor: isSelected ? "primary.main" : "divider",
                    borderRadius: 1,
                    px: 1,
                    py: 0.5,
                  }}
                >
                  <Checkbox
                    checked={isSelected}
                    onChange={() => toggle(node.id)}
                    slotProps={{
                      input: { "aria-label": t("clusters.create.use_machine", { name: nodeLabel(node) }) },
                    }}
                  />
                  <Box sx={{ flexGrow: 1, minWidth: 0 }}>
                    <Typography variant="body2" fontWeight={600}>
                      {nodeLabel(node)}
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      {node.host}
                      {" · "}
                      {gpus > 0
                        ? t("clusters.list.accelerators", { count: gpus })
                        : t("clusters.list.no_accelerators")}
                      {" · "}
                      {machineStatusLabel(node.status)}
                      {busyNodeIds?.has(node.id) ? ` · ${t("clusters.create.in_other_cluster")}` : ""}
                    </Typography>
                  </Box>
                  {isSelected && node.id === headId && (
                    <Chip size="small" color="primary" label={t("clusters.topology.leads")} />
                  )}
                </Stack>
              );
            })}

            {selected.length > 0 && bundles.length > 0 && (
              <Box sx={{ mt: 1 }}>
                {unsupported.length > 0 && (
                  <Alert severity="warning" sx={{ mb: 1 }}>
                    {t("clusters.create.unsupported", {
                      count: unsupported.length,
                      machines: unsupported
                        .map(
                          (id) =>
                            `${nodes.find((n) => n.id === id)?.agent_id ?? id} (${whyUnsupported(id)})`,
                        )
                        .join(", "),
                    })}
                  </Alert>
                )}
                {chosen
                  .filter((node) => bundleByNode.has(node.id))
                  .map((node) => {
                    const bundle = bundleByNode.get(node.id)!;
                    return (
                      <Typography
                        key={node.id}
                        variant="caption"
                        color="text.secondary"
                        component="div"
                      >
                        {t("clusters.create.runs_bundle", {
                          machine: node.agent_id,
                          bundle: bundle.display_name,
                          runtime: bundle.runtime_version,
                          vllm: bundle.vllm_version,
                        })}
                      </Typography>
                    );
                  })}
              </Box>
            )}

            {chosen.length > 1 && (
              <TextField
                select
                size="small"
                label={t("clusters.create.head_label")}
                value={headId}
                helperText={t("clusters.create.head_help")}
                sx={{ mt: 1 }}
                onChange={(e) => setHeadId(e.target.value)}
              >
                {chosen.map((node) => (
                  <MenuItem key={node.id} value={node.id}>
                    {nodeLabel(node)}
                  </MenuItem>
                ))}
              </TextField>
            )}
          </Stack>
        )}

        {step === 2 && (
          <Stack spacing={2}>
            {!plan ? (
              <Box sx={{ display: "flex", justifyContent: "center", p: 3 }}>
                <CircularProgress />
              </Box>
            ) : (
              <NetworkPicker
                plan={plan}
                selectedId={candidateId}
                onSelect={setCandidateId}
              />
            )}
          </Stack>
        )}
      </DialogContent>

      <DialogActions>
        <Button onClick={onClose} disabled={busy}>
          {t("common.cancel")}
        </Button>
        {step === 0 && (
          <Button
            variant="contained"
            disabled={!name.trim()}
            onClick={() => setStep(1)}
          >
            {t("common.next")}
          </Button>
        )}
        {step === 1 && (
          <Button
            variant="contained"
            disabled={chosen.length === 0 || busy}
            onClick={() => void createAndPlan()}
          >
            {t("common.next")}
          </Button>
        )}
        {step === 2 && (
          <Button
            variant="contained"
            disabled={busy || !plan || blocked || !candidateId}
            onClick={() => void applyAndStart()}
            data-testid="create-cluster"
          >
            {t("clusters.create.submit")}
          </Button>
        )}
      </DialogActions>
    </Dialog>
  );
}
