/**
 * Adding a machine.
 *
 * What this replaces: a token, and five lines of prose describing our
 * enrollment protocol — two of which ("Agent calls POST /nodes/enroll",
 * "Agent stores credential") were not things the operator does at all, and
 * three of which left the actual work unspecified. Install how? Set them
 * where?
 *
 * Two paths now, because there are two situations and they need different
 * answers:
 *
 *   The operator can paste into the machine (SSH'd from the same laptop).
 *   A pre-filled command carries everything. One copy, one paste.
 *
 *   The operator cannot (standing at the box, or connected from elsewhere).
 *   Then a 32-character token has to be *retyped*, which is the worst moment
 *   in onboarding. So the secret travels the other way: the machine asks, and
 *   the operator approves it here. Typed on the machine: one address.
 *
 * The second is the default, and the first is behind "already have a shell".
 */
import { useState } from "react";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import Divider from "@mui/material/Divider";
import Drawer from "@mui/material/Drawer";
import IconButton from "@mui/material/IconButton";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";

import CheckIcon from "@mui/icons-material/Check";
import CloseIcon from "@mui/icons-material/Close";
import ContentCopyIcon from "@mui/icons-material/ContentCopy";

import { nodesApi, type NodeEnrollmentToken, type NodeJoinRequest } from "~/api/nodes";
import { useAsyncData } from "~/lib/useAsyncData";

interface NodeOnboardingDrawerProps {
  open: boolean;
  onClose: () => void;
  /** Called after a machine is approved, so the fleet list can catch up. */
  onJoined?: () => void;
}

/**
 * Where this browser is talking to, which is also where the machine should.
 *
 * Read from the page rather than configured: the operator is already looking
 * at the backend, so asking them to type its address would be asking them to
 * repeat something the app knows.
 */
function backendOrigin(): string {
  if (typeof window === "undefined") return "http://<backend-host>:8000";
  return window.location.origin;
}

function CopyLine({ command }: { command: string }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(command);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      // A browser that refuses the clipboard is not an error worth a dialog;
      // the command is on screen and can be selected by hand.
      setCopied(false);
    }
  }

  return (
    <Box
      sx={{
        position: "relative",
        bgcolor: "action.hover",
        borderRadius: 1,
        p: 1.5,
        pr: 5,
      }}
    >
      <Box
        component="pre"
        sx={{
          fontFamily: "monospace",
          fontSize: "0.78rem",
          m: 0,
          whiteSpace: "pre-wrap",
          wordBreak: "break-all",
        }}
      >
        {command}
      </Box>
      <Tooltip title={copied ? "Copied" : "Copy"}>
        <IconButton
          size="small"
          onClick={() => void copy()}
          aria-label="Copy command"
          sx={{ position: "absolute", top: 4, right: 4 }}
        >
          {copied ? <CheckIcon fontSize="small" /> : <ContentCopyIcon fontSize="small" />}
        </IconButton>
      </Tooltip>
    </Box>
  );
}

/** "3 × GB10 · aarch64" — enough to recognise the box you are standing at. */
function hardwareSummary(capabilities: Record<string, unknown>): string {
  const gpu = (capabilities.gpu ?? {}) as Record<string, unknown>;
  const count = Number(capabilities.gpu_count ?? 0);
  const family = String(gpu.family ?? gpu.model ?? gpu.name ?? "").trim();
  const vendor = String(gpu.vendor ?? "").trim();
  const arch = String(capabilities.machine ?? "").trim();

  const parts: string[] = [];
  if (count > 0) {
    parts.push(family ? `${count} × ${family}` : `${count} × ${vendor || "accelerator"}`);
  } else {
    parts.push("no accelerators reported");
  }
  if (arch) parts.push(arch);
  return parts.join(" · ");
}

function WaitingMachine({
  request,
  busy,
  onApprove,
  onReject,
}: {
  request: NodeJoinRequest;
  busy: boolean;
  onApprove: () => void;
  onReject: () => void;
}) {
  const claimedElsewhere =
    request.source_ip && request.host && request.source_ip !== request.host;

  return (
    <Card variant="outlined">
      <CardContent>
        <Stack direction="row" alignItems="flex-start" spacing={2}>
          <Box sx={{ flexGrow: 1, minWidth: 0 }}>
            <Typography variant="subtitle1">{request.agent_id}</Typography>
            <Typography variant="caption" color="text.secondary" display="block">
              {hardwareSummary(request.capabilities)}
            </Typography>
            <Typography variant="caption" color="text.secondary" display="block">
              {request.host}
              {request.version ? ` · agent ${request.version}` : ""}
            </Typography>
            {claimedElsewhere && (
              // Worth showing rather than hiding: a machine claiming one
              // address while calling from another is not necessarily wrong,
              // but it is something to notice before saying yes.
              <Typography variant="caption" color="warning.main" display="block">
                Asked from {request.source_ip}
              </Typography>
            )}
          </Box>
          <Chip
            label={request.code}
            sx={{ fontFamily: "monospace", fontWeight: 600, letterSpacing: 1 }}
          />
        </Stack>

        <Typography variant="caption" color="text.secondary" sx={{ mt: 1.5, display: "block" }}>
          Check this code matches the one shown on the machine.
        </Typography>

        <Stack direction="row" spacing={1} sx={{ mt: 1.5 }}>
          <Button
            variant="contained"
            size="small"
            disabled={busy}
            onClick={onApprove}
            data-tour-id="nodes.join.approve"
          >
            Approve
          </Button>
          <Button variant="outlined" size="small" color="inherit" disabled={busy} onClick={onReject}>
            Not this one
          </Button>
        </Stack>
      </CardContent>
    </Card>
  );
}

export default function NodeOnboardingDrawer({
  open,
  onClose,
  onJoined,
}: NodeOnboardingDrawerProps) {
  const [busyId, setBusyId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [showToken, setShowToken] = useState(false);
  const [note, setNote] = useState("");
  const [token, setToken] = useState<NodeEnrollmentToken | null>(null);
  const [tokenBusy, setTokenBusy] = useState(false);

  // Polls while the drawer is open: the operator is standing at a machine
  // waiting for this list to change, so it has to change without a refresh.
  const waiting = useAsyncData(
    () => (open ? nodesApi.listJoinRequests() : Promise.resolve([])),
    [open],
    { initialValue: [] as NodeJoinRequest[] },
  );

  const origin = backendOrigin();
  const installCommand = [
    `curl -fsSLO ${origin}/api/install/llmport-agent.sh`,
    `sudo sh llmport-agent.sh --join ${origin}`,
  ].join("\n");

  async function decide(request: NodeJoinRequest, approve: boolean) {
    setBusyId(request.id);
    setActionError(null);
    try {
      if (approve) {
        await nodesApi.approveJoinRequest(request.id);
        onJoined?.();
      } else {
        await nodesApi.rejectJoinRequest(request.id);
      }
      await waiting.refresh();
    } catch (err: unknown) {
      setActionError(err instanceof Error ? err.message : "That did not work.");
    } finally {
      setBusyId(null);
    }
  }

  async function createToken() {
    setTokenBusy(true);
    setActionError(null);
    try {
      setToken(await nodesApi.createEnrollmentToken(note));
    } catch (err: unknown) {
      setActionError(err instanceof Error ? err.message : "Failed to create a token.");
    } finally {
      setTokenBusy(false);
    }
  }

  function handleClose() {
    setActionError(null);
    setToken(null);
    setNote("");
    setShowToken(false);
    onClose();
  }

  const tokenCommand = token
    ? [
        `curl -fsSLO ${origin}/api/install/llmport-agent.sh`,
        `sudo sh llmport-agent.sh \\`,
        `  --backend ${origin} \\`,
        `  --token ${token.token}`,
      ].join("\n")
    : "";

  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={handleClose}
      PaperProps={{ sx: { width: { xs: "100%", sm: 520 }, p: 3 } }}
      data-tour-id="nodes.onboarding"
    >
      <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 2 }}>
        <Typography variant="h6">Add a machine</Typography>
        <IconButton onClick={handleClose} size="small" aria-label="close">
          <CloseIcon />
        </IconButton>
      </Stack>

      {actionError && (
        <Alert severity="error" sx={{ mb: 2 }} onClose={() => setActionError(null)}>
          {actionError}
        </Alert>
      )}

      <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
        On the machine you want to add, run:
      </Typography>
      <CopyLine command={installCommand} />
      <Typography variant="caption" color="text.secondary" sx={{ mt: 1, display: "block" }}>
        It will show a short code and wait. Nothing else needs typing there —
        no token, no checksum. The installer verifies what it downloads.
      </Typography>

      <Divider sx={{ my: 3 }} />

      <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1.5 }}>
        <Typography variant="subtitle2">Waiting for approval</Typography>
        {waiting.loading && <CircularProgress size={14} />}
      </Stack>

      {waiting.error ? (
        <Alert severity="warning" variant="outlined">
          {waiting.error}
        </Alert>
      ) : waiting.data.length === 0 ? (
        <Typography variant="body2" color="text.secondary">
          No machine has asked yet. Run the command above and this list will
          fill in.
        </Typography>
      ) : (
        <Stack spacing={1.5}>
          {waiting.data.map((request) => (
            <WaitingMachine
              key={request.id}
              request={request}
              busy={busyId === request.id}
              onApprove={() => void decide(request, true)}
              onReject={() => void decide(request, false)}
            />
          ))}
        </Stack>
      )}

      <Box sx={{ mt: 1.5 }}>
        <Button size="small" onClick={() => void waiting.refresh()} disabled={waiting.loading}>
          Check again
        </Button>
      </Box>

      <Divider sx={{ my: 3 }} />

      {/* The non-interactive path. Behind a toggle because it is the answer
          to a narrower question — unattended provisioning — and putting it
          first would make the common case look harder than it is. */}
      {!showToken ? (
        <Button size="small" color="inherit" onClick={() => setShowToken(true)}>
          Provisioning this automatically?
        </Button>
      ) : (
        <Card variant="outlined">
          <CardContent>
            <Typography variant="subtitle2" gutterBottom>
              Unattended enrolment
            </Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
              For Ansible, cloud-init or an image build, where no one is at a
              browser to approve. The token is single-use and expires.
            </Typography>

            {!token ? (
              <Stack spacing={1}>
                <TextField
                  label="Note"
                  value={note}
                  onChange={(event) => setNote(event.target.value)}
                  fullWidth
                  size="small"
                  placeholder="Which machine or rack is this for?"
                  data-tour-id="nodes.onboarding.note"
                />
                <Button
                  variant="outlined"
                  onClick={() => void createToken()}
                  disabled={tokenBusy}
                  data-tour-id="nodes.onboarding.create"
                >
                  {tokenBusy ? "Creating..." : "Create a token"}
                </Button>
              </Stack>
            ) : (
              <>
                <Alert severity="warning" sx={{ mb: 2 }}>
                  Shown once. Expires{" "}
                  {new Date(token.expires_at).toLocaleTimeString([], {
                    hour: "2-digit",
                    minute: "2-digit",
                  })}
                  .
                </Alert>
                <CopyLine command={tokenCommand} />
              </>
            )}
          </CardContent>
        </Card>
      )}
    </Drawer>
  );
}
