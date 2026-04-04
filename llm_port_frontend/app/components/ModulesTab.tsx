/**
 * ModulesTab — Settings sub-page that lists every optional module with
 * its health status, container states, and an enable / disable toggle.
 *
 * Lives at  /admin/settings?tab=modules
 */
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import Accordion from "@mui/material/Accordion";
import AccordionDetails from "@mui/material/AccordionDetails";
import AccordionSummary from "@mui/material/AccordionSummary";
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
import FormControlLabel from "@mui/material/FormControlLabel";
import Stack from "@mui/material/Stack";
import Switch from "@mui/material/Switch";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import ArticleIcon from "@mui/icons-material/Article";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import StopIcon from "@mui/icons-material/Stop";
import RefreshIcon from "@mui/icons-material/Refresh";

import ContainerLogsDialog from "~/components/ContainerLogsDialog";
import PiiPolicyForm from "~/components/PiiPolicyForm";
import { fetchPIIPolicyOptions, normalizePIIPolicy } from "~/api/pii";
import { useServices } from "~/lib/ServicesContext";
import { servicesApi, type ServiceInfo } from "~/api/services";
import {
  systemSettingsApi,
  type SystemSettingSchemaItem,
} from "~/api/systemSettings";

// ── Helpers ──────────────────────────────────────────────────────────

function statusColor(
  status: string,
): "success" | "warning" | "error" | "default" {
  if (status === "healthy") return "success";
  if (status === "configured") return "warning";
  if (status === "unhealthy") return "error";
  return "default";
}

function containerStateColor(
  state: string,
): "success" | "warning" | "error" | "default" {
  if (state === "running") return "success";
  if (state === "paused" || state === "restarting") return "warning";
  if (state === "exited" || state === "dead") return "error";
  return "default";
}

function isPiiSettingKey(key: string): boolean {
  return key.startsWith("llm_port_api.pii_");
}

function isMailerSettingKey(key: string): boolean {
  return key.startsWith("llm_port_mailer.");
}

function isMcpSettingKey(key: string): boolean {
  return key.startsWith("llm_port_api.mcp_") || key.startsWith("llm_port_mcp.");
}

function isSkillsSettingKey(key: string): boolean {
  return (
    key.startsWith("llm_port_api.skills_") || key.startsWith("llm_port_skills.")
  );
}

function isRagLiteSettingKey(key: string): boolean {
  return (
    key.startsWith("llm_port_api.rag_lite_") || key.startsWith("llm_port_rag.")
  );
}

function isModuleEnableFlag(key: string): boolean {
  const explicitModuleFlags = new Set([
    "llm_port_api.pii_enabled",
    "llm_port_api.mailer_enabled",
    "llm_port_api.mcp_enabled",
    "llm_port_api.skills_enabled",
    "llm_port_api.rag_lite_enabled",
    "llm_port_pii.enabled",
    "llm_port_mailer.enabled",
    "llm_port_mcp.enabled",
    "llm_port_skills.enabled",
    "llm_port_rag.enabled",
  ]);
  if (explicitModuleFlags.has(key)) return true;
  return /(?:^|\.)(pii|mailer|mcp|skills|rag_lite)_enabled$/.test(key);
}

function moduleSettingsFor(
  moduleName: string,
  schema: SystemSettingSchemaItem[],
): SystemSettingSchemaItem[] {
  if (moduleName === "pii") {
    return schema.filter(
      (item) => isPiiSettingKey(item.key) && !isModuleEnableFlag(item.key),
    );
  }
  if (moduleName === "mailer") {
    return schema.filter(
      (item) => isMailerSettingKey(item.key) && !isModuleEnableFlag(item.key),
    );
  }
  if (moduleName === "mcp") {
    return schema.filter(
      (item) => isMcpSettingKey(item.key) && !isModuleEnableFlag(item.key),
    );
  }
  if (moduleName === "skills") {
    return schema.filter(
      (item) => isSkillsSettingKey(item.key) && !isModuleEnableFlag(item.key),
    );
  }
  if (moduleName === "rag_lite") {
    return schema.filter(
      (item) => isRagLiteSettingKey(item.key) && !isModuleEnableFlag(item.key),
    );
  }
  return [];
}

function asInputValue(value: unknown): string {
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  if (value === null || value === undefined) return "";
  try {
    return JSON.stringify(value);
  } catch {
    return "";
  }
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
  const [expandedModule, setExpandedModule] = useState<string | null>(null);
  const [settingsSchema, setSettingsSchema] = useState<
    SystemSettingSchemaItem[]
  >([]);
  const [settingsValues, setSettingsValues] = useState<Record<string, unknown>>(
    {},
  );
  const [settingsLoading, setSettingsLoading] = useState(false);
  const [settingsError, setSettingsError] = useState<string | null>(null);
  const [settingsBusyKey, setSettingsBusyKey] = useState<string | null>(null);
  const [settingsStatus, setSettingsStatus] = useState<string | null>(null);
  const [piiOptionsLoading, setPiiOptionsLoading] = useState(false);
  const [piiOptionsError, setPiiOptionsError] = useState<string | null>(null);
  const [piiOptions, setPiiOptions] = useState<Awaited<
    ReturnType<typeof fetchPIIPolicyOptions>
  > | null>(null);

  const piiPolicyValue = useMemo(
    () =>
      normalizePIIPolicy(
        settingsValues["llm_port_api.pii_default_policy"],
        piiOptions ?? undefined,
      ),
    [settingsValues, piiOptions],
  );

  const hasPiiSettings = useMemo(
    () => settingsSchema.some((item) => isPiiSettingKey(item.key)),
    [settingsSchema],
  );

  async function loadModuleSettings() {
    setSettingsLoading(true);
    setSettingsError(null);
    try {
      const [schemaItems, valuesResp] = await Promise.all([
        systemSettingsApi.schema(),
        systemSettingsApi.values(),
      ]);
      setSettingsSchema(schemaItems);

      const nextValues: Record<string, unknown> = {};
      for (const item of schemaItems) {
        const v = valuesResp.items[item.key];
        if (!v) {
          nextValues[item.key] = item.default;
          continue;
        }
        nextValues[item.key] = item.is_secret ? "" : (v.value ?? item.default);
      }
      setSettingsValues(nextValues);
    } catch (error: unknown) {
      setSettingsError(
        error instanceof Error
          ? error.message
          : t("common.action_failed", { defaultValue: "Action failed." }),
      );
    } finally {
      setSettingsLoading(false);
    }
  }

  useEffect(() => {
    void loadModuleSettings();
  }, []);

  useEffect(() => {
    if (!hasPiiSettings) return;
    let cancelled = false;
    setPiiOptionsLoading(true);
    fetchPIIPolicyOptions()
      .then((options) => {
        if (!cancelled) {
          setPiiOptions(options);
          setPiiOptionsError(null);
        }
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setPiiOptionsError(
            error instanceof Error
              ? error.message
              : "Failed to load PII options.",
          );
        }
      })
      .finally(() => {
        if (!cancelled) setPiiOptionsLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [hasPiiSettings]);

  function setLocalValue(
    key: string,
    raw: string,
    type: SystemSettingSchemaItem["type"],
  ) {
    let parsed: unknown = raw;
    if (type === "int") parsed = Number(raw);
    if (type === "bool") parsed = raw === "true";
    if (type === "json") {
      try {
        parsed = raw.trim() ? JSON.parse(raw) : {};
      } catch {
        parsed = raw;
      }
    }
    setSettingsValues((prev) => ({ ...prev, [key]: parsed }));
  }

  async function saveSetting(
    item: SystemSettingSchemaItem,
    overrideValue?: unknown,
  ) {
    setSettingsBusyKey(item.key);
    setSettingsStatus(null);
    setSettingsError(null);
    try {
      const value = overrideValue ?? settingsValues[item.key];
      const result = await systemSettingsApi.update(item.key, value, "local");
      setSettingsStatus(
        `${item.label}: ${result.apply_status} (${result.apply_scope})`,
      );
      if (item.is_secret) {
        setSettingsValues((prev) => ({ ...prev, [item.key]: "" }));
      }
    } catch (error: unknown) {
      setSettingsError(
        error instanceof Error
          ? error.message
          : t("common.action_failed", { defaultValue: "Action failed." }),
      );
    } finally {
      setSettingsBusyKey(null);
      await loadModuleSettings();
    }
  }

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
            module: t(`modules.${svc.name}.name`, {
              defaultValue: svc.display_name,
            }),
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
          onClick={() => {
            void refresh();
            void loadModuleSettings();
          }}
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
            defaultValue:
              "No optional modules are configured in this deployment.",
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
                  <Stack direction="row" spacing={1} alignItems="center">
                    <Typography variant="subtitle1" fontWeight="bold">
                      {t(`modules.${svc.name}.name`, {
                        defaultValue: svc.display_name,
                      })}
                    </Typography>
                    {svc.enterprise && (
                      <Chip
                        size="small"
                        label="Enterprise"
                        color="secondary"
                        variant="outlined"
                      />
                    )}
                  </Stack>
                  <Typography variant="body2" color="text.secondary">
                    {t(`modules.${svc.name}.description`, {
                      defaultValue: svc.description,
                    })}
                  </Typography>
                </Box>

                <Stack direction="row" spacing={1} alignItems="center">
                  <Chip
                    size="small"
                    label={
                      svc.enabled
                        ? t(`modules.status.${svc.status}`, {
                            defaultValue: svc.status,
                          })
                        : t("modules.status.disabled", {
                            defaultValue: "disabled",
                          })
                    }
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
                          module: t(`modules.${svc.name}.name`, {
                            defaultValue: svc.display_name,
                          }),
                        }),
                      }}
                    />
                  )}
                </Stack>
              </Stack>

              {/* Container states */}
              {svc.containers && svc.containers.length > 0 && (
                <Stack
                  direction="row"
                  spacing={0.5}
                  flexWrap="wrap"
                  useFlexGap
                  sx={{ mt: 1.5 }}
                >
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

              {moduleSettingsFor(svc.name, settingsSchema).length > 0 && (
                <Accordion
                  disableGutters
                  sx={{ mt: 1.5 }}
                  expanded={expandedModule === svc.name}
                  onChange={(_event, expanded) => {
                    setExpandedModule(expanded ? svc.name : null);
                  }}
                >
                  <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                    <Typography variant="body2" fontWeight={600}>
                      {t("modules_tab.module_settings", {
                        defaultValue: "Module Settings",
                      })}
                    </Typography>
                  </AccordionSummary>
                  <AccordionDetails>
                    <Stack spacing={1.5}>
                      {settingsError && (
                        <Alert severity="error">{settingsError}</Alert>
                      )}
                      {settingsStatus && (
                        <Alert
                          severity="success"
                          onClose={() => setSettingsStatus(null)}
                        >
                          {settingsStatus}
                        </Alert>
                      )}

                      {settingsLoading ? (
                        <Box
                          sx={{
                            py: 1,
                            display: "flex",
                            justifyContent: "center",
                          }}
                        >
                          <CircularProgress size={20} />
                        </Box>
                      ) : (
                        moduleSettingsFor(svc.name, settingsSchema).map(
                          (item) => {
                            if (
                              item.key === "llm_port_api.pii_default_policy"
                            ) {
                              return (
                                <Box key={item.key}>
                                  <Typography variant="body2" sx={{ mb: 1 }}>
                                    {item.label}
                                  </Typography>
                                  {piiOptionsError && (
                                    <Alert severity="warning" sx={{ mb: 1 }}>
                                      {piiOptionsError}
                                    </Alert>
                                  )}
                                  <PiiPolicyForm
                                    value={piiPolicyValue}
                                    options={piiOptions}
                                    disabled={piiOptionsLoading}
                                    onChange={(next) =>
                                      setSettingsValues((prev) => ({
                                        ...prev,
                                        [item.key]: next,
                                      }))
                                    }
                                  />
                                  <Stack
                                    direction="row"
                                    justifyContent="flex-end"
                                    sx={{ mt: 1 }}
                                  >
                                    <Button
                                      size="small"
                                      variant="contained"
                                      disabled={
                                        settingsBusyKey === item.key ||
                                        piiOptionsLoading
                                      }
                                      onClick={() =>
                                        void saveSetting(item, piiPolicyValue)
                                      }
                                    >
                                      {settingsBusyKey === item.key
                                        ? t("common.loading")
                                        : t("common.save")}
                                    </Button>
                                  </Stack>
                                </Box>
                              );
                            }

                            return (
                              <Stack
                                key={item.key}
                                direction={{ xs: "column", md: "row" }}
                                spacing={1}
                              >
                                {item.type === "bool" ? (
                                  <FormControlLabel
                                    sx={{ flexGrow: 1 }}
                                    control={
                                      <Switch
                                        checked={
                                          settingsValues[item.key] === true ||
                                          settingsValues[item.key] === "true"
                                        }
                                        onChange={(event) =>
                                          setSettingsValues((prev) => ({
                                            ...prev,
                                            [item.key]: event.target.checked,
                                          }))
                                        }
                                      />
                                    }
                                    label={
                                      <Box>
                                        <Typography variant="body2">
                                          {item.label}
                                        </Typography>
                                        <Typography
                                          variant="caption"
                                          color="text.secondary"
                                        >
                                          {item.description}
                                        </Typography>
                                      </Box>
                                    }
                                  />
                                ) : item.type === "enum" ? (
                                  <TextField
                                    size="small"
                                    fullWidth
                                    select
                                    label={item.label}
                                    value={asInputValue(
                                      settingsValues[item.key],
                                    )}
                                    onChange={(event) =>
                                      setLocalValue(
                                        item.key,
                                        event.target.value,
                                        item.type,
                                      )
                                    }
                                    helperText={item.description}
                                  >
                                    {(item.enum_values ?? []).map((v) => (
                                      <MenuItem key={v} value={v}>
                                        {v}
                                      </MenuItem>
                                    ))}
                                  </TextField>
                                ) : (
                                  <TextField
                                    size="small"
                                    fullWidth
                                    multiline={item.type === "json"}
                                    minRows={
                                      item.type === "json" ? 2 : undefined
                                    }
                                    type={
                                      item.type === "int" ? "number" : "text"
                                    }
                                    label={item.label}
                                    value={asInputValue(
                                      settingsValues[item.key],
                                    )}
                                    onChange={(event) =>
                                      setLocalValue(
                                        item.key,
                                        event.target.value,
                                        item.type,
                                      )
                                    }
                                    helperText={item.description}
                                  />
                                )}
                                <Button
                                  variant="contained"
                                  size="small"
                                  disabled={settingsBusyKey === item.key}
                                  onClick={() => void saveSetting(item)}
                                >
                                  {settingsBusyKey === item.key
                                    ? t("common.loading")
                                    : t("common.save")}
                                </Button>
                              </Stack>
                            );
                          },
                        )
                      )}
                    </Stack>
                  </AccordionDetails>
                </Accordion>
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
                disabled={
                  svc.module_type === "plugin" ||
                  !svc.containers ||
                  svc.containers.length === 0
                }
                onClick={() => setLogsModule(svc)}
              >
                {t("modules_tab.logs", { defaultValue: "Logs" })}
              </Button>
            </CardActions>
          </Card>
        );
      })}

      {/* Confirmation dialog */}
      <Dialog
        open={confirmDialog !== null}
        onClose={() => setConfirmDialog(null)}
      >
        <DialogTitle>
          {confirmDialog?.action === "enable"
            ? t("modules_tab.confirm_enable_title", {
                defaultValue: "Enable {{module}}?",
                module: t(`modules.${confirmDialog?.module.name}.name`, {
                  defaultValue: confirmDialog?.module.display_name,
                }),
              })
            : t("modules_tab.confirm_disable_title", {
                defaultValue: "Disable {{module}}?",
                module: t(`modules.${confirmDialog?.module.name}.name`, {
                  defaultValue: confirmDialog?.module.display_name,
                }),
              })}
        </DialogTitle>
        <DialogContent>
          <DialogContentText>
            {confirmDialog?.action === "enable"
              ? t("modules_tab.confirm_enable_body", {
                  defaultValue:
                    "This will start all containers for the {{module}} module.",
                  module: t(`modules.${confirmDialog?.module.name}.name`, {
                    defaultValue: confirmDialog?.module.display_name,
                  }),
                })
              : t("modules_tab.confirm_disable_body", {
                  defaultValue:
                    "This will stop all containers for the {{module}} module. Any in-flight requests will be interrupted.",
                  module: t(`modules.${confirmDialog?.module.name}.name`, {
                    defaultValue: confirmDialog?.module.display_name,
                  }),
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
        moduleDisplayName={t(`modules.${logsModule?.name}.name`, {
          defaultValue: logsModule?.display_name ?? "",
        })}
        containers={logsModule?.containers ?? []}
      />
    </Stack>
  );
}
