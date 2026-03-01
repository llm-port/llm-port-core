import { useCallback, useEffect, useMemo, useState } from "react";
import { Link as RouterLink } from "react-router";
import { useTranslation } from "react-i18next";

import { containers, type ContainerSummary } from "~/api/admin";
import { systemSettingsApi } from "~/api/systemSettings";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import Link from "@mui/material/Link";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import StopIcon from "@mui/icons-material/Stop";
import RestartAltIcon from "@mui/icons-material/RestartAlt";

export default function ApiDocsPage() {
  const { t } = useTranslation();
  const [containerName, setContainerName] = useState("llm-port-api");
  const [activeUrl, setActiveUrl] = useState("http://127.0.0.1:8001/api/docs");
  const [serviceContainer, setServiceContainer] = useState<ContainerSummary | null>(null);
  const [loadingService, setLoadingService] = useState(false);
  const [actionBusy, setActionBusy] = useState<"start" | "stop" | "restart" | "register" | null>(null);
  const [serviceError, setServiceError] = useState<string | null>(null);

  const normalizedContainerName = useMemo(() => containerName.trim().toLowerCase(), [containerName]);

  const refreshService = useCallback(async () => {
    setLoadingService(true);
    setServiceError(null);
    try {
      const all = await containers.list();
      const exact = all.find((item) => item.name.toLowerCase() === normalizedContainerName);
      const prefix = all.find((item) => item.name.toLowerCase().startsWith(normalizedContainerName));
      const match = exact ?? prefix ?? null;
      setServiceContainer(match);
    } catch (error: unknown) {
      setServiceError(error instanceof Error ? error.message : t("agents_api_docs.failed_service"));
    } finally {
      setLoadingService(false);
    }
  }, [normalizedContainerName, t]);

  useEffect(() => {
    async function loadSystemSettings() {
      try {
        const values = await systemSettingsApi.values();
        const endpoint = values.items["api.server.endpoint_url"];
        const container = values.items["api.server.container_name"];
        if (endpoint && !endpoint.is_secret && typeof endpoint.value === "string") {
          setActiveUrl(endpoint.value);
        }
        if (container && !container.is_secret && typeof container.value === "string") {
          setContainerName(container.value);
        }
      } catch {
        // Keep fallback defaults if backend settings endpoint is unavailable.
      }
    }
    void loadSystemSettings();
  }, []);

  useEffect(() => {
    void refreshService();
  }, [refreshService]);

  async function runAction(action: "start" | "stop" | "restart") {
    if (!serviceContainer) {
      return;
    }
    setActionBusy(action);
    setServiceError(null);
    try {
      await containers.lifecycle(serviceContainer.id, action);
      await refreshService();
    } catch (error: unknown) {
      setServiceError(error instanceof Error ? error.message : t("agents_api_docs.failed_action"));
    } finally {
      setActionBusy(null);
    }
  }

  async function registerAsSystemAux() {
    if (!serviceContainer) {
      return;
    }
    setActionBusy("register");
    setServiceError(null);
    try {
      await containers.register(serviceContainer.id, {
        container_class: "SYSTEM_AUX",
        owner_scope: "platform",
        policy: "free",
      });
      await refreshService();
    } catch (error: unknown) {
      setServiceError(error instanceof Error ? error.message : t("agents_api_docs.failed_register"));
    } finally {
      setActionBusy(null);
    }
  }

  return (
    <Stack spacing={1.5}>
      <Paper sx={{ p: 1.5 }}>
        <Stack direction={{ xs: "column", md: "row" }} spacing={1} alignItems={{ xs: "stretch", md: "center" }}>
          <Chip
            variant="outlined"
            label={`${t("agents_api_docs.container_name")}: ${containerName}`}
          />
          <Button variant="outlined" onClick={() => void refreshService()} disabled={loadingService}>
            {t("dashboard.refresh")}
          </Button>
          <Button variant="text" component={RouterLink} to="/admin/settings?tab=general">
            {t("agents_api_docs.edit_in_settings")}
          </Button>
          {loadingService && <CircularProgress size={18} />}
        </Stack>
        {serviceError && <Alert severity="error" sx={{ mt: 1 }}>{serviceError}</Alert>}
        <Stack direction={{ xs: "column", md: "row" }} spacing={1} sx={{ mt: 1 }} alignItems={{ xs: "stretch", md: "center" }}>
          <Chip
            color={serviceContainer?.state === "running" ? "success" : "default"}
            label={
              serviceContainer
                ? `${t("common.status")}: ${serviceContainer.state}`
                : t("agents_api_docs.service_not_found")
            }
          />
          <Chip
            variant="outlined"
            label={
              serviceContainer
                ? `${t("agents_api_docs.class_label")}: ${serviceContainer.container_class}`
                : `${t("agents_api_docs.class_label")}: -`
            }
          />
          <Button
            variant="contained"
            size="small"
            startIcon={<PlayArrowIcon />}
            disabled={!serviceContainer || actionBusy !== null}
            onClick={() => void runAction("start")}
          >
            {t("common.start")}
          </Button>
          <Button
            variant="contained"
            color="warning"
            size="small"
            startIcon={<StopIcon />}
            disabled={!serviceContainer || actionBusy !== null}
            onClick={() => void runAction("stop")}
          >
            {t("common.stop")}
          </Button>
          <Button
            variant="outlined"
            size="small"
            startIcon={<RestartAltIcon />}
            disabled={!serviceContainer || actionBusy !== null}
            onClick={() => void runAction("restart")}
          >
            {t("common.restart")}
          </Button>
          <Button
            variant="text"
            size="small"
            disabled={!serviceContainer || actionBusy !== null}
            onClick={() => void registerAsSystemAux()}
          >
            {t("agents_api_docs.register_aux")}
          </Button>
        </Stack>
      </Paper>
      <Paper sx={{ p: 1.5 }}>
        <Stack direction={{ xs: "column", md: "row" }} spacing={1} alignItems={{ xs: "stretch", md: "center" }}>
          <Chip
            variant="outlined"
            label={`${t("agents_api_docs.endpoint_url")}: ${activeUrl}`}
          />
          <Button variant="outlined" href={activeUrl} target="_blank" rel="noopener noreferrer">
            {t("agents_api_docs.open_new_tab")}
          </Button>
        </Stack>
      </Paper>
      <Paper sx={{ p: 1, height: "72vh", minHeight: 520 }}>
        <Box
          component="iframe"
          title="api-docs"
          src={activeUrl}
          sx={{
            width: "100%",
            height: "100%",
            border: 0,
            borderRadius: 1,
            bgcolor: "background.paper",
          }}
        />
      </Paper>
      <Typography variant="caption" color="text.secondary">
        {t("agents_api_docs.note")}
        {" "}
        <Link component={RouterLink} to="/admin/settings?tab=general" underline="hover">
          {t("agents_api_docs.edit_in_settings")}
        </Link>
      </Typography>
    </Stack>
  );
}
