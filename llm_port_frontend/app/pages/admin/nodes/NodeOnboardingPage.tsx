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
import i18n from "i18next";
import { Trans, useTranslation } from "react-i18next";

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
 * Where this browser is talking to.
 *
 * Only a fallback now. It used to be the whole answer, on the assumption that
 * the operator and the machine see the backend the same way -- and when they
 * do not, the console prints an install command that cannot work. In dev the
 * browser sits on the Vite proxy, so the command said
 * `curl http://localhost:5173/...`; run on a real machine that fetches from
 * itself and finds nothing. The backend is asked instead, because it is the
 * thing that knows its own addresses.
 */
function browserOrigin(): string {
  if (typeof window === "undefined") return "http://<backend-host>:8000";
  return window.location.origin;
}

/** An address a machine being added can actually reach. */
interface InstallAddress {
  url: string;
  seen_as: string;
  candidates: { url: string; interface: string }[];
  request_origin_is_reachable: boolean;
}

function CopyLine({ command }: { command: string }) {
  const { t } = useTranslation();
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
      <Tooltip title={copied ? t("nodes.onboard.copied") : t("common.copy")}>
        <IconButton
          size="small"
          onClick={() => void copy()}
          aria-label={t("nodes.onboard.copy_command")}
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
    parts.push(
      family
        ? `${count} × ${family}`
        : `${count} × ${vendor || i18n.t("nodes.onboard.accelerator")}`,
    );
  } else {
    parts.push(i18n.t("clusters.list.no_accelerators"));
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
  const { t } = useTranslation();
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
              {request.version ? ` · ${t("nodes.onboard.agent_version", { version: request.version })}` : ""}
            </Typography>
            {claimedElsewhere && (
              // Worth showing rather than hiding: a machine claiming one
              // address while calling from another is not necessarily wrong,
              // but it is something to notice before saying yes.
              <Typography variant="caption" color="warning.main" display="block">
                {t("nodes.onboard.asked_from", { ip: request.source_ip })}
              </Typography>
            )}
          </Box>
          <Chip
            label={request.code}
            sx={{ fontFamily: "monospace", fontWeight: 600, letterSpacing: 1 }}
          />
        </Stack>

        <Typography variant="caption" color="text.secondary" sx={{ mt: 1.5, display: "block" }}>
          {t("nodes.onboard.check_code")}
        </Typography>

        <Stack direction="row" spacing={1} sx={{ mt: 1.5 }}>
          <Button
            variant="contained"
            size="small"
            disabled={busy}
            onClick={onApprove}
            data-tour-id="nodes.join.approve"
          >
            {t("nodes.onboard.approve")}
          </Button>
          <Button variant="outlined" size="small" color="inherit" disabled={busy} onClick={onReject}>
            {t("nodes.onboard.reject")}
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
  const { t } = useTranslation();
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

  // Asked of the backend, not inferred from this page. See browserOrigin().
  const address = useAsyncData<InstallAddress | null>(
    () =>
      open
        ? nodesApi.installAddress().catch(() => null)
        : Promise.resolve(null),
    [open],
    { initialValue: null },
  );

  const [chosenOrigin, setChosenOrigin] = useState<string | null>(null);
  const origin = chosenOrigin ?? address.data?.url ?? browserOrigin();
  const candidates = address.data?.candidates ?? [];
  // More than one plausible address means we are guessing. Say so, and let
  // the operator pick, rather than printing one and being confidently wrong.
  const ambiguous = candidates.length > 1;

  // One paste, not two. Still downloaded to disk before it runs -- `&&` only
  // chains the download to the run -- so it can be read first as before.
  const installCommand =
    `curl -fsSLO ${origin}/api/install/llmport-agent.sh && ` +
    `sudo sh llmport-agent.sh --join ${origin}`;

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
      setActionError(err instanceof Error ? err.message : t("common.error_unexpected"));
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
      setActionError(err instanceof Error ? err.message : t("nodes.onboard.token_failed"));
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
    ? `curl -fsSLO ${origin}/api/install/llmport-agent.sh && ` +
      `sudo sh llmport-agent.sh --backend ${origin} --token ${token.token}`
    : "";

  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={handleClose}
      // The paper is a flex column. Once the panel is taller than the
      // window its children shrink to fit, and one with overflow hidden -- the
      // token card -- shrank to nothing: "Skip the approval step" opened a
      // card 0px tall. Let the paper scroll instead.
      PaperProps={{ sx: { width: { xs: "100%", sm: 520 }, p: 3, "& > *": { flexShrink: 0 } } }}
      data-tour-id="nodes.onboarding"
    >
      <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 2 }}>
        <Typography variant="h6">{t("clusters.next.no_nodes.action")}</Typography>
        <IconButton onClick={handleClose} size="small" aria-label={t("common.close")}>
          <CloseIcon />
        </IconButton>
      </Stack>

      {actionError && (
        <Alert severity="error" sx={{ mb: 2 }} onClose={() => setActionError(null)}>
          {actionError}
        </Alert>
      )}

      <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
        {t("nodes.onboard.run_this")}
      </Typography>
      <CopyLine command={installCommand} />
      <Typography variant="caption" color="text.secondary" sx={{ mt: 1, display: "block" }}>
        {t("nodes.onboard.what_happens")}
      </Typography>
      <Typography variant="caption" color="text.secondary" sx={{ mt: 0.5, display: "block" }}>
        <Trans i18nKey="nodes.onboard.no_sudo" components={{ code: <code /> }} />
      </Typography>

      {/*
        The address is the one thing here we cannot know for certain: this
        server has several, and only the machine being added knows which of
        them it can reach. Offering the choice beats printing a guess — the
        guess used to be this browser's own URL, which on a dev proxy or a
        tunnel is an address no other machine can reach at all.
      */}
      {ambiguous && (
        <Box sx={{ mt: 2 }}>
          <Typography variant="caption" color="text.secondary" display="block">
            {t("nodes.onboard.pick_address")}
          </Typography>
          <Stack direction="row" spacing={1} sx={{ mt: 1, flexWrap: "wrap", gap: 1 }}>
            {candidates.map((candidate) => (
              <Chip
                key={candidate.url}
                label={`${candidate.url.replace(/^https?:\/\//, "")} · ${candidate.interface}`}
                size="small"
                variant={candidate.url === origin ? "filled" : "outlined"}
                color={candidate.url === origin ? "primary" : "default"}
                onClick={() => setChosenOrigin(candidate.url)}
                sx={{ fontFamily: "monospace", fontSize: "0.7rem" }}
              />
            ))}
          </Stack>
        </Box>
      )}
      {address.data && !address.data.request_origin_is_reachable && (
        <Alert severity="info" sx={{ mt: 2 }}>
          <Trans
            i18nKey="nodes.onboard.local_address"
            values={{ address: address.data.seen_as }}
            components={{ code: <code /> }}
          />
        </Alert>
      )}

      <Divider sx={{ my: 3 }} />

      <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1.5 }}>
        <Typography variant="subtitle2">{t("nodes.onboard.waiting")}</Typography>
        {waiting.loading && <CircularProgress size={14} />}
      </Stack>

      {waiting.error ? (
        <Alert severity="warning" variant="outlined">
          {waiting.error}
        </Alert>
      ) : waiting.data.length === 0 ? (
        <Typography variant="body2" color="text.secondary">
          {t("nodes.onboard.none_waiting")}
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
          {t("nodes.onboard.check_again")}
        </Button>
      </Box>

      <Divider sx={{ my: 3 }} />

      {/* The non-interactive path. Behind a toggle because it is the answer
          to a narrower question — unattended provisioning — and putting it
          first would make the common case look harder than it is. */}
      {!showToken ? (
        <Button size="small" color="inherit" onClick={() => setShowToken(true)}>
          {t("nodes.onboard.skip_approval")}
        </Button>
      ) : (
        <Card variant="outlined">
          <CardContent>
            <Typography variant="subtitle2" gutterBottom>
              {t("nodes.onboard.token_title")}
            </Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
              {t("nodes.onboard.token_explain")}
            </Typography>

            {!token ? (
              <Stack spacing={1}>
                <TextField
                  label={t("nodes.onboard.note")}
                  value={note}
                  onChange={(event) => setNote(event.target.value)}
                  fullWidth
                  size="small"
                  placeholder={t("nodes.onboard.note_placeholder")}
                  data-tour-id="nodes.onboarding.note"
                />
                <Button
                  variant="outlined"
                  onClick={() => void createToken()}
                  disabled={tokenBusy}
                  data-tour-id="nodes.onboarding.create"
                >
                  {tokenBusy ? t("common.creating") : t("nodes.onboard.create_token")}
                </Button>
              </Stack>
            ) : (
              <>
                <Alert severity="warning" sx={{ mb: 2 }}>
                  {t("nodes.onboard.token_expires", {
                    time: new Date(token.expires_at).toLocaleTimeString(i18n.language || [], {
                      hour: "2-digit",
                      minute: "2-digit",
                    }),
                  })}
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
