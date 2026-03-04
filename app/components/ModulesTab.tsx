/**
 * ModulesTab — Settings sub-page that lists every optional module with
 * its health status, container states, and an enable / disable toggle.
 *
 * Lives at  /admin/settings?tab=modules
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Card from "@mui/material/Card";
import CardActions from "@mui/material/CardActions";
import CardContent from "@mui/material/CardContent";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogContentText from "@mui/material/DialogContentText";
import DialogTitle from "@mui/material/DialogTitle";
import Stack from "@mui/material/Stack";
import Switch from "@mui/material/Switch";
import Typography from "@mui/material/Typography";
import ArticleIcon from "@mui/icons-material/Article";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import StopIcon from "@mui/icons-material/Stop";
import RefreshIcon from "@mui/icons-material/Refresh";

import ContainerLogsDialog from "~/components/ContainerLogsDialog";
import { useServices } from "~/lib/ServicesContext";
import { servicesApi, type ServiceInfo } from "~/api/services";

// ── Helpers ──────────────────────────────────────────────────────────

function statusColor(status: string): "success" | "warning" | "error" | "default" {
  if (status === "healthy") return "success";
  if (status === "configured") return "warning";
  if (status === "unhealthy") return "error";
  return "default";
}

function containerStateColor(state: string): "success" | "warning" | "error" | "default" {
  if (state === "running") return "success";
  if (state === "paused" || state === "restarting") return "warning";
  if (state === "exited" || state === "dead") return "error";
  return "default";
}

// ── Component ────────────────────────────────────────────────────────

export default function ModulesTab() {
  const { t } = useTranslation();
  const { services, loading, error: loadError, refresh } = useServices();
  const [busy, setBusy] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionSuccess, setActionSuccess] = useState<string | null>(null);
  const [confirmDialog, setConfirmDialog] = useState<{
    module: ServiceInfo;
    action: "enable" | "disable";
  } | null>(null);
  const [logsModule, setLogsModule] = useState<ServiceInfo | null>(null);

  async function handleToggle(svc: ServiceInfo, action: "enable" | "disable") {
    setConfirmDialog({ module: svc, action });
  }

  async function confirmAction() {
    if (!confirmDialog) return;
    const { module: svc, action } = confirmDialog;
    setConfirmDialog(null);
    setBusy(svc.name);
    setActionError(null);
    setActionSuccess(null);

    try {
      const result =
        action === "enable"
          ? await servicesApi.enable(svc.name)
          : await servicesApi.disable(svc.name);

      if (result.errors.length > 0) {
        setActionError(
          t("modules_tab.partial_error", {
            defaultValue: "Some containers had errors: {{errors}}",
            errors: result.errors.join("; "),
          }),
        );
      } else {
        setActionSuccess(
          t("modules_tab.action_success", {
            defaultValue: "{{module}} {{action}}d successfully.",
            module: svc.display_name,
            action,
          }),
        );
      }
      await refresh();
    } catch (err) {
      setActionError(
        err instanceof Error ? err.message : t("common.action_failed"),
      );
    } finally {
      setBusy(null);
    }
  }

  if (loading) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
        <CircularProgress size={28} />
      </Box>
    );
  }

  return (
    <Stack spacing={2}>
      {/* Header row */}
      <Stack direction="row" justifyContent="space-between" alignItems="center">
        <Typography variant="h6">
          {t("modules_tab.title", { defaultValue: "Optional Modules" })}
        </Typography>
        <Button
          size="small"
          startIcon={<RefreshIcon />}
          onClick={() => void refresh()}
        >
          {t("dashboard.refresh", { defaultValue: "Refresh" })}
        </Button>
      </Stack>

      <Typography variant="body2" color="text.secondary">
        {t("modules_tab.description", {
          defaultValue:
            "Enable or disable optional modules. Disabling a module stops its containers; enabling starts them.",
        })}
      </Typography>

      {loadError && <Alert severity="warning">{loadError}</Alert>}
      {actionError && (
        <Alert severity="error" onClose={() => setActionError(null)}>
          {actionError}
        </Alert>
      )}
      {actionSuccess && (
        <Alert severity="success" onClose={() => setActionSuccess(null)}>
          {actionSuccess}
        </Alert>
      )}

      {services.length === 0 && (
        <Typography color="text.secondary">
          {t("modules_tab.no_modules", {
            defaultValue: "No optional modules are configured in this deployment.",
          })}
        </Typography>
      )}

      {/* Module cards */}
      {services.map((svc) => {
        const isRunning = svc.enabled;
        const isBusy = busy === svc.name;

        return (
          <Card key={svc.name} variant="outlined">
            <CardContent>
              <Stack
                direction={{ xs: "column", sm: "row" }}
                justifyContent="space-between"
                alignItems={{ xs: "flex-start", sm: "center" }}
                spacing={1}
              >
                <Box>
                  <Typography variant="subtitle1" fontWeight="bold">
                    {svc.display_name}
                  </Typography>
                  <Typography variant="body2" color="text.secondary">
                    {svc.description}
                  </Typography>
                </Box>

                <Stack direction="row" spacing={1} alignItems="center">
                  <Chip
                    size="small"
                    label={svc.enabled ? svc.status : "disabled"}
                    color={svc.enabled ? statusColor(svc.status) : "default"}
                    variant="outlined"
                  />
                  {isBusy ? (
                    <CircularProgress size={20} />
                  ) : (
                    <Switch
                      checked={isRunning}
                      onChange={() =>
                        void handleToggle(svc, isRunning ? "disable" : "enable")
                      }
                      inputProps={{
                        "aria-label": t("modules_tab.toggle_aria", {
                          defaultValue: "Toggle {{module}}",
                          module: svc.display_name,
                        }),
                      }}
                    />
                  )}
                </Stack>
              </Stack>

              {/* Container states */}
              {svc.containers && svc.containers.length > 0 && (
                <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap sx={{ mt: 1.5 }}>
                  {svc.containers.map((c) => (
                    <Chip
                      key={c.name}
                      size="small"
                      label={`${c.name}: ${c.state}`}
                      color={containerStateColor(c.state)}
                      variant="outlined"
                    />
                  ))}
                </Stack>
              )}
            </CardContent>

            <CardActions sx={{ px: 2, pb: 1.5 }}>
              <Button
                size="small"
                startIcon={<PlayArrowIcon />}
                disabled={isBusy || isRunning}
                onClick={() => void handleToggle(svc, "enable")}
              >
                {t("common.start")}
              </Button>
              <Button
                size="small"
                startIcon={<StopIcon />}
                disabled={isBusy || !isRunning}
                color="warning"
                onClick={() => void handleToggle(svc, "disable")}
              >
                {t("common.stop")}
              </Button>
              <Button
                size="small"
                startIcon={<ArticleIcon />}
                disabled={!svc.containers || svc.containers.length === 0}
                onClick={() => setLogsModule(svc)}
              >
                {t("modules_tab.logs", { defaultValue: "Logs" })}
              </Button>
            </CardActions>
          </Card>
        );
      })}

      {/* Confirmation dialog */}
      <Dialog open={confirmDialog !== null} onClose={() => setConfirmDialog(null)}>
        <DialogTitle>
          {confirmDialog?.action === "enable"
            ? t("modules_tab.confirm_enable_title", {
                defaultValue: "Enable {{module}}?",
                module: confirmDialog?.module.display_name,
              })
            : t("modules_tab.confirm_disable_title", {
                defaultValue: "Disable {{module}}?",
                module: confirmDialog?.module.display_name,
              })}
        </DialogTitle>
        <DialogContent>
          <DialogContentText>
            {confirmDialog?.action === "enable"
              ? t("modules_tab.confirm_enable_body", {
                  defaultValue:
                    "This will start all containers for the {{module}} module.",
                  module: confirmDialog?.module.display_name,
                })
              : t("modules_tab.confirm_disable_body", {
                  defaultValue:
                    "This will stop all containers for the {{module}} module. Any in-flight requests will be interrupted.",
                  module: confirmDialog?.module.display_name,
                })}
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setConfirmDialog(null)}>
            {t("common.cancel")}
          </Button>
          <Button
            variant="contained"
            color={confirmDialog?.action === "disable" ? "warning" : "primary"}
            onClick={() => void confirmAction()}
          >
            {confirmDialog?.action === "enable"
              ? t("common.start")
              : t("common.stop")}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Container logs overlay */}
      <ContainerLogsDialog
        open={logsModule !== null}
        onClose={() => setLogsModule(null)}
        module={logsModule?.name ?? ""}
        moduleDisplayName={logsModule?.display_name ?? ""}
        containers={logsModule?.containers ?? []}
      />
    </Stack>
  );
}
