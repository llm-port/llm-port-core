import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  nodesApi,
  type NodeCommand,
  type NodeCommandTimeline,
} from "~/api/nodes";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import Divider from "@mui/material/Divider";
import LinearProgress from "@mui/material/LinearProgress";
import Stack from "@mui/material/Stack";
import Step from "@mui/material/Step";
import StepLabel from "@mui/material/StepLabel";
import Stepper from "@mui/material/Stepper";
import Table from "@mui/material/Table";
import TableBody from "@mui/material/TableBody";
import TableCell from "@mui/material/TableCell";
import TableContainer from "@mui/material/TableContainer";
import TableHead from "@mui/material/TableHead";
import TableRow from "@mui/material/TableRow";
import Typography from "@mui/material/Typography";

import ArrowForwardIcon from "@mui/icons-material/ArrowForward";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import ErrorIcon from "@mui/icons-material/Error";
import MemoryIcon from "@mui/icons-material/Memory";
import RestartAltIcon from "@mui/icons-material/RestartAlt";
import UpdateIcon from "@mui/icons-material/Update";

/* ── types ──────────────────────────────────────────────────────── */

interface SystemPackage {
  name: string;
  old_version?: string;
  new_version?: string;
}

interface FirmwareDevice {
  name: string;
  current_version?: string;
  update_version?: string;
  summary?: string;
}

interface CheckResult {
  package_manager: string | null;
  os: string;
  system_packages: {
    updates_available: boolean;
    package_count: number;
    packages: SystemPackage[];
  };
  firmware: {
    available: boolean;
    updates_available: boolean;
    device_count: number;
    devices: FirmwareDevice[];
  };
}

interface ApplyResult {
  scope: string;
  system_update?: {
    success: boolean;
    exit_code: number;
    output_tail: string;
  } | null;
  firmware_update?: {
    success: boolean;
    exit_code: number;
    output_tail: string;
  } | null;
  reboot_required: boolean;
  reboot_policy: string;
}

type UpdateStage =
  | "idle"
  | "checking"
  | "checked"
  | "applying"
  | "done"
  | "error";
type ApplyScope = "all" | "system" | "firmware";

const POLL_MS = 2_000;

const STAGES = ["check_updates", "apply_updates", "done"] as const;

/* ── component ──────────────────────────────────────────────────── */

export default function SystemUpdateDialog({
  open,
  nodeId,
  onClose,
}: {
  open: boolean;
  nodeId: string;
  onClose: () => void;
}) {
  const { t } = useTranslation();

  const [stage, setStage] = useState<UpdateStage>("idle");
  const [checkResult, setCheckResult] = useState<CheckResult | null>(null);
  const [activeCommandId, setActiveCommandId] = useState<string | null>(null);
  const [timeline, setTimeline] = useState<NodeCommandTimeline | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [applyResult, setApplyResult] = useState<ApplyResult | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  /* ── polling ─────────────────────────────────────────────── */

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const pollTimeline = useCallback(
    (cmdId: string) => {
      stopPolling();
      pollRef.current = setInterval(async () => {
        try {
          const tl = await nodesApi.commandTimeline(nodeId, cmdId);
          setTimeline(tl);
          const { status } = tl.command;
          if (
            status === "succeeded" ||
            status === "failed" ||
            status === "timed_out" ||
            status === "canceled"
          ) {
            stopPolling();
            if (status === "succeeded") {
              const result = tl.command.result ?? {};
              if ("system_packages" in result) {
                setCheckResult(result as unknown as CheckResult);
                setStage("checked");
              } else {
                setApplyResult(result as unknown as ApplyResult);
                setStage("done");
              }
            } else {
              setErrorMsg(tl.command.error_message ?? status);
              setStage("error");
            }
          }
        } catch {
          /* poll failures are non-fatal */
        }
      }, POLL_MS);
    },
    [nodeId, stopPolling],
  );

  useEffect(() => () => stopPolling(), [stopPolling]);

  /* ── reset on open ───────────────────────────────────────── */

  useEffect(() => {
    if (open) {
      setStage("idle");
      setCheckResult(null);
      setActiveCommandId(null);
      setTimeline(null);
      setErrorMsg(null);
      setApplyResult(null);
    }
  }, [open]);

  /* ── actions ─────────────────────────────────────────────── */

  async function handleCheckUpdates() {
    setStage("checking");
    setErrorMsg(null);
    try {
      const cmd: NodeCommand = await nodesApi.issueCommand(nodeId, {
        command_type: "check_system_updates",
      });
      setActiveCommandId(cmd.id);
      pollTimeline(cmd.id);
    } catch (err: unknown) {
      setErrorMsg(
        err instanceof Error ? err.message : "Failed to issue command",
      );
      setStage("error");
    }
  }

  async function handleApplyUpdates(scope: ApplyScope) {
    setStage("applying");
    setErrorMsg(null);
    try {
      const cmd: NodeCommand = await nodesApi.issueCommand(nodeId, {
        command_type: "apply_system_updates",
        payload: { scope },
      });
      setActiveCommandId(cmd.id);
      pollTimeline(cmd.id);
    } catch (err: unknown) {
      setErrorMsg(
        err instanceof Error ? err.message : "Failed to issue command",
      );
      setStage("error");
    }
  }

  async function handleReboot() {
    setStage("applying");
    setErrorMsg(null);
    try {
      const cmd: NodeCommand = await nodesApi.issueCommand(nodeId, {
        command_type: "apply_system_updates",
        payload: { scope: "system", reboot_policy: "if_required" },
      });
      setActiveCommandId(cmd.id);
      pollTimeline(cmd.id);
    } catch (err: unknown) {
      setErrorMsg(
        err instanceof Error ? err.message : "Failed to issue command",
      );
      setStage("error");
    }
  }

  /* ── stepper ─────────────────────────────────────────────── */

  let activeStep = 0;
  if (stage === "checking") activeStep = 0;
  else if (stage === "checked") activeStep = 1;
  else if (stage === "applying") activeStep = 1;
  else if (stage === "done") activeStep = 3;

  const isRunning = stage === "checking" || stage === "applying";

  const hasSysPkgs = checkResult?.system_packages?.updates_available ?? false;
  const hasFwUpdates = checkResult?.firmware?.updates_available ?? false;
  const hasAnyUpdate = hasSysPkgs || hasFwUpdates;

  return (
    <Dialog
      open={open}
      onClose={isRunning ? undefined : onClose}
      maxWidth="md"
      fullWidth
      slotProps={{ backdrop: { sx: { backdropFilter: "blur(4px)" } } }}
    >
      <DialogTitle>
        <Stack direction="row" spacing={1} alignItems="center">
          <UpdateIcon />
          <span>{t("system_updates.title")}</span>
        </Stack>
      </DialogTitle>
      <DialogContent dividers>
        {/* Stepper */}
        <Stepper activeStep={activeStep} alternativeLabel sx={{ mb: 3 }}>
          {STAGES.map((s, idx) => (
            <Step
              key={s}
              completed={stage === "done" ? true : activeStep > idx}
            >
              <StepLabel error={stage === "error" && activeStep === idx}>
                {t(`system_updates.step_${s}`)}
              </StepLabel>
            </Step>
          ))}
        </Stepper>

        {/* Running indicator */}
        {isRunning && <LinearProgress sx={{ mb: 2 }} />}

        {/* Idle state */}
        {stage === "idle" && (
          <Typography
            variant="body2"
            color="text.secondary"
            textAlign="center"
            sx={{ py: 3 }}
          >
            {t("system_updates.idle_message")}
          </Typography>
        )}

        {/* Checking state — show live timeline */}
        {stage === "checking" && timeline && (
          <TimelineLog events={timeline.events} />
        )}
        {stage === "checking" && !timeline && (
          <Stack alignItems="center" spacing={1} sx={{ py: 2 }}>
            <CircularProgress size={24} />
            <Typography variant="caption" color="text.secondary">
              {t("system_updates.checking")}
            </Typography>
          </Stack>
        )}

        {/* ── Check result: two separate lists ── */}
        {stage === "checked" && checkResult && (
          <Stack spacing={3}>
            {/* System Packages section */}
            <Box>
              <Stack
                direction="row"
                spacing={1}
                alignItems="center"
                sx={{ mb: 1 }}
              >
                <UpdateIcon
                  fontSize="small"
                  color={hasSysPkgs ? "warning" : "success"}
                />
                <Typography variant="subtitle2">
                  {t("system_updates.system_packages")}
                </Typography>
                <Chip
                  label={
                    hasSysPkgs
                      ? `${checkResult.system_packages.package_count} ${t("system_updates.available")}`
                      : t("system_updates.up_to_date")
                  }
                  color={hasSysPkgs ? "warning" : "success"}
                  size="small"
                />
                {checkResult.package_manager && (
                  <Chip
                    label={checkResult.package_manager}
                    size="small"
                    variant="outlined"
                  />
                )}
              </Stack>
              {hasSysPkgs &&
                checkResult.system_packages.packages.length > 0 && (
                  <TableContainer
                    sx={{
                      maxHeight: 220,
                      bgcolor: "action.hover",
                      borderRadius: 1,
                    }}
                  >
                    <Table size="small" stickyHeader>
                      <TableHead>
                        <TableRow>
                          <TableCell
                            sx={{ fontWeight: 600, fontSize: "0.75rem" }}
                          >
                            {t("system_updates.col_package")}
                          </TableCell>
                          <TableCell
                            sx={{ fontWeight: 600, fontSize: "0.75rem" }}
                          >
                            {t("system_updates.col_installed")}
                          </TableCell>
                          <TableCell />
                          <TableCell
                            sx={{ fontWeight: 600, fontSize: "0.75rem" }}
                          >
                            {t("system_updates.col_available")}
                          </TableCell>
                        </TableRow>
                      </TableHead>
                      <TableBody>
                        {checkResult.system_packages.packages.map((pkg, i) => (
                          <TableRow key={i}>
                            <TableCell
                              sx={{
                                fontFamily: "monospace",
                                fontSize: "0.8rem",
                              }}
                            >
                              {pkg.name}
                            </TableCell>
                            <TableCell
                              sx={{
                                fontFamily: "monospace",
                                fontSize: "0.75rem",
                                color: "text.secondary",
                              }}
                            >
                              {pkg.old_version || "—"}
                            </TableCell>
                            <TableCell sx={{ px: 0.5, width: 24 }}>
                              <ArrowForwardIcon
                                sx={{ fontSize: 14, color: "text.disabled" }}
                              />
                            </TableCell>
                            <TableCell
                              sx={{
                                fontFamily: "monospace",
                                fontSize: "0.75rem",
                                color: "success.main",
                              }}
                            >
                              {pkg.new_version || "—"}
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </TableContainer>
                )}
            </Box>

            {/* Firmware section */}
            {checkResult.firmware.available && (
              <>
                <Divider />
                <Box>
                  <Stack
                    direction="row"
                    spacing={1}
                    alignItems="center"
                    sx={{ mb: 1 }}
                  >
                    <MemoryIcon
                      fontSize="small"
                      color={hasFwUpdates ? "warning" : "success"}
                    />
                    <Typography variant="subtitle2">
                      {t("system_updates.firmware")}
                    </Typography>
                    <Chip
                      label={
                        hasFwUpdates
                          ? `${checkResult.firmware.device_count} ${t("system_updates.available")}`
                          : t("system_updates.up_to_date")
                      }
                      color={hasFwUpdates ? "warning" : "success"}
                      size="small"
                    />
                  </Stack>
                  {hasFwUpdates && checkResult.firmware.devices.length > 0 && (
                    <TableContainer
                      sx={{
                        maxHeight: 180,
                        bgcolor: "action.hover",
                        borderRadius: 1,
                      }}
                    >
                      <Table size="small" stickyHeader>
                        <TableHead>
                          <TableRow>
                            <TableCell
                              sx={{ fontWeight: 600, fontSize: "0.75rem" }}
                            >
                              {t("system_updates.col_device")}
                            </TableCell>
                            <TableCell
                              sx={{ fontWeight: 600, fontSize: "0.75rem" }}
                            >
                              {t("system_updates.col_installed")}
                            </TableCell>
                            <TableCell />
                            <TableCell
                              sx={{ fontWeight: 600, fontSize: "0.75rem" }}
                            >
                              {t("system_updates.col_available")}
                            </TableCell>
                          </TableRow>
                        </TableHead>
                        <TableBody>
                          {checkResult.firmware.devices.map((dev, i) => (
                            <TableRow key={i}>
                              <TableCell
                                sx={{
                                  fontFamily: "monospace",
                                  fontSize: "0.8rem",
                                }}
                              >
                                {dev.name}
                                {dev.summary && (
                                  <Typography
                                    variant="caption"
                                    display="block"
                                    color="text.secondary"
                                  >
                                    {dev.summary}
                                  </Typography>
                                )}
                              </TableCell>
                              <TableCell
                                sx={{
                                  fontFamily: "monospace",
                                  fontSize: "0.75rem",
                                  color: "text.secondary",
                                }}
                              >
                                {dev.current_version || "—"}
                              </TableCell>
                              <TableCell sx={{ px: 0.5, width: 24 }}>
                                <ArrowForwardIcon
                                  sx={{ fontSize: 14, color: "text.disabled" }}
                                />
                              </TableCell>
                              <TableCell
                                sx={{
                                  fontFamily: "monospace",
                                  fontSize: "0.75rem",
                                  color: "success.main",
                                }}
                              >
                                {dev.update_version || "—"}
                              </TableCell>
                            </TableRow>
                          ))}
                        </TableBody>
                      </Table>
                    </TableContainer>
                  )}
                </Box>
              </>
            )}

            {/* No updates at all */}
            {!hasAnyUpdate && (
              <Stack alignItems="center" sx={{ py: 2 }}>
                <CheckCircleIcon color="success" fontSize="large" />
                <Typography variant="body1" fontWeight={600} sx={{ mt: 1 }}>
                  {t("system_updates.all_up_to_date")}
                </Typography>
              </Stack>
            )}
          </Stack>
        )}

        {/* Applying state — show live timeline */}
        {stage === "applying" && timeline && (
          <TimelineLog events={timeline.events} />
        )}
        {stage === "applying" && !timeline && (
          <Stack alignItems="center" spacing={1} sx={{ py: 2 }}>
            <CircularProgress size={24} />
            <Typography variant="caption" color="text.secondary">
              {t("system_updates.applying")}
            </Typography>
          </Stack>
        )}

        {/* Done */}
        {stage === "done" && (
          <Stack spacing={2} sx={{ py: 2 }}>
            <Stack alignItems="center" spacing={1}>
              <CheckCircleIcon color="success" fontSize="large" />
              <Typography variant="body1" fontWeight={600}>
                {t("system_updates.complete")}
              </Typography>
            </Stack>
            {applyResult?.reboot_required && (
              <Alert
                severity="warning"
                icon={<RestartAltIcon />}
                action={
                  <Button
                    color="warning"
                    size="small"
                    variant="contained"
                    onClick={handleReboot}
                  >
                    {t("system_updates.reboot_now")}
                  </Button>
                }
              >
                {t("system_updates.reboot_required")}
              </Alert>
            )}
            {timeline && <TimelineLog events={timeline.events} />}
          </Stack>
        )}

        {/* Error */}
        {stage === "error" && (
          <Stack alignItems="center" spacing={1} sx={{ py: 2 }}>
            <ErrorIcon color="error" fontSize="large" />
            <Typography variant="body2" color="error">
              {errorMsg}
            </Typography>
            {timeline && <TimelineLog events={timeline.events} />}
          </Stack>
        )}
      </DialogContent>
      <DialogActions>
        {stage === "idle" && (
          <Button
            onClick={handleCheckUpdates}
            variant="contained"
            startIcon={<UpdateIcon />}
          >
            {t("system_updates.check")}
          </Button>
        )}
        {stage === "checked" && hasAnyUpdate && (
          <Stack direction="row" spacing={1}>
            {hasSysPkgs && hasFwUpdates && (
              <Button
                onClick={() => handleApplyUpdates("all")}
                variant="contained"
                color="warning"
                startIcon={<UpdateIcon />}
              >
                {t("system_updates.apply_all")}
              </Button>
            )}
            {hasSysPkgs && (
              <Button
                onClick={() => handleApplyUpdates("system")}
                variant={hasFwUpdates ? "outlined" : "contained"}
                color="warning"
                size={hasFwUpdates ? "small" : "medium"}
              >
                {t("system_updates.apply_system")}
              </Button>
            )}
            {hasFwUpdates && (
              <Button
                onClick={() => handleApplyUpdates("firmware")}
                variant={hasSysPkgs ? "outlined" : "contained"}
                color="warning"
                size={hasSysPkgs ? "small" : "medium"}
                startIcon={<MemoryIcon />}
              >
                {t("system_updates.apply_firmware")}
              </Button>
            )}
          </Stack>
        )}
        {stage === "error" && (
          <Button onClick={handleCheckUpdates} variant="outlined">
            {t("system_updates.retry")}
          </Button>
        )}
        <Button onClick={onClose} disabled={isRunning}>
          {stage === "done" || stage === "checked"
            ? t("system_updates.close")
            : t("system_updates.cancel")}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

/* ── sub-component: timeline event log ──────────────────────────── */

function TimelineLog({ events }: { events: NodeCommandTimeline["events"] }) {
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [events.length]);

  if (events.length === 0) return null;

  return (
    <Box
      sx={{
        fontSize: "0.75rem",
        fontFamily: "monospace",
        bgcolor: "grey.900",
        color: "grey.100",
        p: 1.5,
        borderRadius: 1,
        maxHeight: 200,
        overflow: "auto",
        whiteSpace: "pre-wrap",
        wordBreak: "break-word",
      }}
    >
      {events.map((e, i) => {
        const ts = new Date(e.ts).toLocaleTimeString();
        return (
          <Box key={i}>
            <Typography
              component="span"
              sx={{
                fontSize: "inherit",
                fontFamily: "inherit",
                color: "grey.500",
              }}
            >
              [{ts}]
            </Typography>{" "}
            <Typography
              component="span"
              sx={{
                fontSize: "inherit",
                fontFamily: "inherit",
                color: "info.main",
              }}
            >
              {e.phase}:
            </Typography>{" "}
            {e.message}
          </Box>
        );
      })}
      <div ref={endRef} />
    </Box>
  );
}
