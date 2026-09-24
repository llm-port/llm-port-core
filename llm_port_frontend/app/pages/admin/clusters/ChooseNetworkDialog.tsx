/**
 * "Choose the network" for a cluster that already exists.
 *
 * The same step 3 the wizard runs, reachable again later: machines get added,
 * cabling changes, and a cluster whose network was never applied must not be
 * a dead end.
 */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { inferenceApi, type EnvironmentPlan } from "~/api/inference";
import { NetworkPicker } from "./NetworkPicker";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import CircularProgress from "@mui/material/CircularProgress";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import LinearProgress from "@mui/material/LinearProgress";
import Typography from "@mui/material/Typography";

export interface ChooseNetworkDialogProps {
  open: boolean;
  clusterId: string;
  onClose: () => void;
  onApplied: () => void;
}

export function ChooseNetworkDialog({
  open,
  clusterId,
  onClose,
  onApplied,
}: ChooseNetworkDialogProps) {
  const { t } = useTranslation();
  const [plan, setPlan] = useState<EnvironmentPlan | null>(null);
  const [candidateId, setCandidateId] = useState<string | null>(null);
  const [working, setWorking] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setPlan(null);
    setError(null);
    setWorking(t("clusters.network.looking"));
    void (async () => {
      try {
        const found = await inferenceApi.planEnvironment(clusterId);
        if (cancelled) return;
        setPlan(found);
        setCandidateId(found.recommended_candidate_id);
      } catch (err: unknown) {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      } finally {
        if (!cancelled) setWorking("");
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, clusterId]);

  async function apply() {
    if (!plan) return;
    setError(null);
    setWorking(t("clusters.network.applying"));
    try {
      await inferenceApi.applyEnvironmentPlan(clusterId, {
        plan,
        selected_candidate_id: candidateId,
      });
      setWorking(t("clusters.network.starting"));
      await inferenceApi.reconcileEnvironment(clusterId);
      onApplied();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setWorking("");
    }
  }

  const busy = Boolean(working);
  const blocked = (plan?.blockers.length ?? 0) > 0;

  return (
    <Dialog open={open} onClose={busy ? undefined : onClose} maxWidth="md" fullWidth>
      <DialogTitle>{t("clusters.network.title")}</DialogTitle>
      <DialogContent dividers>
        {error && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {error}
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
        {!plan && !error ? (
          <Box sx={{ display: "flex", justifyContent: "center", p: 3 }}>
            <CircularProgress />
          </Box>
        ) : plan ? (
          <NetworkPicker plan={plan} selectedId={candidateId} onSelect={setCandidateId} />
        ) : null}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={busy}>
          {t("common.cancel")}
        </Button>
        <Button
          variant="contained"
          disabled={busy || !plan || blocked || !candidateId}
          onClick={() => void apply()}
        >
          {t("clusters.network.use")}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
