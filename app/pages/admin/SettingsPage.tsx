import { useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import Divider from "@mui/material/Divider";
import IconButton from "@mui/material/IconButton";
import InputAdornment from "@mui/material/InputAdornment";
import MenuItem from "@mui/material/MenuItem";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import Tab from "@mui/material/Tab";
import Tabs from "@mui/material/Tabs";
import TextField from "@mui/material/TextField";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import AutoFixHighIcon from "@mui/icons-material/AutoFixHigh";
import ContentCopyIcon from "@mui/icons-material/ContentCopy";
import SearchIcon from "@mui/icons-material/Search";

import ModulesTab from "~/components/ModulesTab";
import PiiPolicyForm from "~/components/PiiPolicyForm";
import { useServices } from "~/lib/ServicesContext";
import { fetchPIIPolicyOptions, normalizePIIPolicy } from "~/api/pii";
import {
  systemSettingsApi,
  type SystemSettingSchemaItem,
  type WizardStep,
} from "~/api/systemSettings";

type SettingsTab = "general" | "system-init" | "modules";

function getCurrentTab(
  _pathname: string,
  tabQuery: string | null,
): SettingsTab {
  if (tabQuery === "system-init") return "system-init";
  if (tabQuery === "modules") return "modules";
  return "general";
}

function asInputValue(value: unknown): string {
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean")
    return String(value);
  if (value === null || value === undefined) return "";
  try {
    return JSON.stringify(value);
  } catch {
    return "";
  }
}

function generateSecureToken(length = 48): string {
  const alphabet =
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
  const bytes = new Uint8Array(length);
  window.crypto.getRandomValues(bytes);
  let token = "";
  for (let idx = 0; idx < bytes.length; idx += 1) {
    token += alphabet[bytes[idx] % alphabet.length];
  }
  return token;
}

function toTitleCase(value: string): string {
  return value
    .split(" ")
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function isPiiSettingKey(key: string): boolean {
  return key.startsWith("llm_port_api.pii_");
}

function isMailerSettingKey(key: string): boolean {
  return key.startsWith("llm_port_mailer.");
}

export default function SettingsPage() {
  const { t } = useTranslation();
  const { isModuleEnabled, loading: modulesLoading } = useServices();
  const location = useLocation();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [tab, setTab] = useState<SettingsTab>(() =>
    getCurrentTab(location.pathname, searchParams.get("tab")),
  );

  const [schema, setSchema] = useState<SystemSettingSchemaItem[]>([]);
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [secretMasked, setSecretMasked] = useState<Record<string, string>>({});
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(false);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [wizardSteps, setWizardSteps] = useState<WizardStep[]>([]);
  const [wizardStepIndex, setWizardStepIndex] = useState(0);
  const [wizardTargetHost, setWizardTargetHost] = useState("local");
  const [wizardSaving, setWizardSaving] = useState(false);
  const [piiOptionsLoading, setPiiOptionsLoading] = useState(false);
  const [piiOptionsError, setPiiOptionsError] = useState<string | null>(null);
  const [piiOptions, setPiiOptions] = useState<Awaited<
    ReturnType<typeof fetchPIIPolicyOptions>
  > | null>(null);

  useEffect(() => {
    setTab(getCurrentTab(location.pathname, searchParams.get("tab")));
  }, [location.pathname, searchParams]);

  async function loadSettings() {
    setLoading(true);
    setError(null);
    try {
      const [schemaItems, valuesResp, stepsResp] = await Promise.all([
        systemSettingsApi.schema(),
        systemSettingsApi.values(),
        systemSettingsApi.wizardSteps(),
      ]);
      setSchema(schemaItems);
      setWizardSteps(stepsResp.steps);
      const nextValues: Record<string, unknown> = {};
      const masked: Record<string, string> = {};
      for (const item of schemaItems) {
        const v = valuesResp.items[item.key];
        if (!v) {
          nextValues[item.key] = item.default;
          continue;
        }
        if (item.is_secret) {
          nextValues[item.key] = "";
          masked[item.key] = v.masked ?? "";
        } else {
          nextValues[item.key] = v.value ?? item.default;
        }
      }
      setValues(nextValues);
      setSecretMasked(masked);
    } catch (e: unknown) {
      setError(
        e instanceof Error ? e.message : "Failed to load system settings.",
      );
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadSettings();
  }, []);

  const piiModuleEnabled = isModuleEnabled("pii");
  const mailerModuleEnabled = isModuleEnabled("mailer");

  useEffect(() => {
    if (modulesLoading || !piiModuleEnabled) {
      setPiiOptionsError(null);
      return;
    }
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
  }, [modulesLoading, piiModuleEnabled]);

  const piiPolicyValue = useMemo(
    () =>
      normalizePIIPolicy(
        values["llm_port_api.pii_default_policy"],
        piiOptions ?? undefined,
      ),
    [values, piiOptions],
  );

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    const visibleSchema = schema.filter(
      (item) =>
        !isPiiSettingKey(item.key) &&
        !(isMailerSettingKey(item.key) && !mailerModuleEnabled),
    );
    const items = q
      ? visibleSchema.filter((s) =>
          `${s.label} ${s.description} ${s.key} ${s.category} ${s.group}`
            .toLowerCase()
            .includes(q),
        )
      : visibleSchema;
    const groups = new Map<string, SystemSettingSchemaItem[]>();
    for (const item of items) {
      const key = `${item.category}::${item.group}`;
      groups.set(key, [...(groups.get(key) ?? []), item]);
    }
    return groups;
  }, [schema, search]);

  function handleTabChange(_event: React.SyntheticEvent, nextTab: SettingsTab) {
    setTab(nextTab);
    navigate(`/admin/settings?tab=${nextTab}`, { replace: true });
  }

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
    setValues((prev) => ({ ...prev, [key]: parsed }));
  }

  function generateSecretForKey(key: string) {
    try {
      const token = generateSecureToken();
      setValues((prev) => ({ ...prev, [key]: token }));
      setStatusMessage(t("settings.secret_generated"));
      setError(null);
    } catch (e: unknown) {
      setError(
        e instanceof Error ? e.message : t("settings.secret_generate_failed"),
      );
    }
  }

  async function copySecretForKey(key: string) {
    const value = values[key];
    if (typeof value !== "string" || !value) {
      setError(t("settings.secret_copy_empty"));
      return;
    }
    try {
      await navigator.clipboard.writeText(value);
      setStatusMessage(t("settings.secret_copied"));
      setError(null);
    } catch (e: unknown) {
      setError(
        e instanceof Error ? e.message : t("settings.secret_copy_failed"),
      );
    }
  }

  async function saveKey(item: SystemSettingSchemaItem) {
    setBusyKey(item.key);
    setStatusMessage(null);
    setError(null);
    try {
      const value = values[item.key];
      const result = await systemSettingsApi.update(item.key, value, "local");
      setStatusMessage(
        `${item.label}: ${result.apply_status} (${result.apply_scope})`,
      );
      if (item.is_secret) {
        setValues((prev) => ({ ...prev, [item.key]: "" }));
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to update setting.");
    } finally {
      setBusyKey(null);
      await loadSettings();
    }
  }

  async function savePiiSettings() {
    const piiServiceUrlKey = "llm_port_api.pii_service_url";
    const piiPolicyKey = "llm_port_api.pii_default_policy";
    setBusyKey("__pii_policy__");
    setStatusMessage(null);
    setError(null);
    try {
      await systemSettingsApi.update(
        piiServiceUrlKey,
        values[piiServiceUrlKey] ?? "",
        "local",
      );
      const result = await systemSettingsApi.update(
        piiPolicyKey,
        piiPolicyValue,
        "local",
      );
      setStatusMessage(
        `PII policy: ${result.apply_status} (${result.apply_scope})`,
      );
    } catch (e: unknown) {
      setError(
        e instanceof Error ? e.message : "Failed to update PII settings.",
      );
    } finally {
      setBusyKey(null);
      await loadSettings();
    }
  }

  async function applyWizardStep() {
    const step = wizardSteps[wizardStepIndex];
    if (!step) return;
    setWizardSaving(true);
    setStatusMessage(null);
    setError(null);
    try {
      const payload: Record<string, unknown> = {};
      const keysForStep = step.setting_keys.filter(
        (key) =>
          !(isPiiSettingKey(key) && !piiModuleEnabled) &&
          !(isMailerSettingKey(key) && !mailerModuleEnabled),
      );
      for (const key of keysForStep) {
        if (values[key] !== undefined) payload[key] = values[key];
      }
      const result = await systemSettingsApi.wizardApply(
        payload,
        wizardTargetHost,
      );
      const failed = result.results.filter((r) => r.apply_status !== "success");
      if (failed.length > 0) {
        setError(`Wizard apply failed for ${failed.length} setting(s).`);
      } else {
        setStatusMessage(`Wizard step "${step.title}" applied successfully.`);
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Wizard apply failed.");
    } finally {
      setWizardSaving(false);
      await loadSettings();
    }
  }

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        minHeight: 0,
        height: "100%",
      }}
    >
      <Box sx={{ mb: 2 }}>
        <Typography variant="h5" sx={{ mb: 1 }}>
          {t("settings.title")}
        </Typography>
        <Tabs value={tab} onChange={handleTabChange}>
          <Tab label={t("settings.general.title")} value="general" />
          <Tab label={t("settings.system_init.title")} value="system-init" />
          <Tab
            label={t("settings.modules.title", { defaultValue: "Modules" })}
            value="modules"
          />
        </Tabs>
      </Box>

      {error && (
        <Alert severity="error" sx={{ mb: 1 }}>
          {error}
        </Alert>
      )}
      {statusMessage && (
        <Alert severity="success" sx={{ mb: 1 }}>
          {statusMessage}
        </Alert>
      )}

      <Box
        sx={{
          minHeight: 0,
          flexGrow: 1,
          display: "flex",
          flexDirection: "column",
          overflow: "auto",
        }}
      >
        {tab === "general" && (
          <Stack spacing={2}>
            <TextField
              size="small"
              fullWidth
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder={t("settings.search_placeholder")}
              InputProps={{
                startAdornment: (
                  <InputAdornment position="start">
                    <SearchIcon fontSize="small" />
                  </InputAdornment>
                ),
              }}
            />

            {loading && <CircularProgress size={20} />}
            {!loading && filtered.size === 0 && (
              <Typography color="text.secondary">
                {t("settings.no_results")}
              </Typography>
            )}

            {[...filtered.entries()].map(([groupKey, items]) => {
              const [category, group] = groupKey.split("::");
              return (
                <Paper key={groupKey} sx={{ p: 2 }}>
                  <Typography variant="subtitle1">
                    {toTitleCase(category.replaceAll("_", " "))} /{" "}
                    {toTitleCase(group.replaceAll("_", " "))}
                  </Typography>
                  <Divider sx={{ my: 1 }} />
                  <Stack spacing={1.5}>
                    {items.map((item) => (
                      <Box key={item.key}>
                        <Stack
                          direction={{ xs: "column", md: "row" }}
                          spacing={1}
                          alignItems={{ xs: "stretch", md: "center" }}
                        >
                          <TextField
                            size="small"
                            fullWidth
                            type={item.type === "secret" ? "password" : "text"}
                            label={item.label}
                            value={asInputValue(values[item.key])}
                            onChange={(event) =>
                              setLocalValue(
                                item.key,
                                event.target.value,
                                item.type,
                              )
                            }
                            helperText={`${item.description}${item.is_secret && secretMasked[item.key] ? ` (current: ${secretMasked[item.key]})` : ""}`}
                          />
                          <Chip
                            size="small"
                            color={
                              item.apply_scope === "live_reload"
                                ? "success"
                                : item.apply_scope === "service_restart"
                                  ? "warning"
                                  : "error"
                            }
                            label={item.apply_scope}
                          />
                          {item.type === "secret" && (
                            <>
                              <Tooltip title={t("settings.generate_secret")}>
                                <IconButton
                                  size="small"
                                  color="primary"
                                  onClick={() => generateSecretForKey(item.key)}
                                >
                                  <AutoFixHighIcon fontSize="small" />
                                </IconButton>
                              </Tooltip>
                              <Tooltip title={t("common.copy")}>
                                <IconButton
                                  size="small"
                                  color="primary"
                                  onClick={() =>
                                    void copySecretForKey(item.key)
                                  }
                                >
                                  <ContentCopyIcon fontSize="small" />
                                </IconButton>
                              </Tooltip>
                            </>
                          )}
                          <Button
                            variant="contained"
                            size="small"
                            disabled={busyKey === item.key}
                            onClick={() => void saveKey(item)}
                          >
                            {busyKey === item.key
                              ? t("common.loading")
                              : t("common.save")}
                          </Button>
                        </Stack>
                      </Box>
                    ))}
                  </Stack>
                </Paper>
              );
            })}

            {!modulesLoading && piiModuleEnabled && (
              <Paper sx={{ p: 2 }}>
                <Typography variant="subtitle1" sx={{ mb: 1 }}>
                  {t("pii_policy.system_section", {
                    defaultValue: "PII Policy (System Default)",
                  })}
                </Typography>
                <Stack spacing={1.5}>
                  <TextField
                    size="small"
                    fullWidth
                    label={t("pii_policy.pii_service_url", {
                      defaultValue: "PII Service URL",
                    })}
                    value={asInputValue(values["llm_port_api.pii_service_url"])}
                    onChange={(event) =>
                      setLocalValue(
                        "llm_port_api.pii_service_url",
                        event.target.value,
                        "string",
                      )
                    }
                    helperText={t("pii_policy.pii_service_url_help", {
                      defaultValue:
                        "Internal URL for llm_port_pii (for example http://llm-port-pii:8000/api).",
                    })}
                  />
                  {piiOptionsError && (
                    <Alert severity="warning">{piiOptionsError}</Alert>
                  )}
                  <PiiPolicyForm
                    value={piiPolicyValue}
                    options={piiOptions}
                    disabled={piiOptionsLoading}
                    onChange={(next) =>
                      setValues((prev) => ({
                        ...prev,
                        ["llm_port_api.pii_default_policy"]: next,
                      }))
                    }
                  />
                  <Stack direction="row" justifyContent="flex-end">
                    <Button
                      variant="contained"
                      size="small"
                      disabled={
                        busyKey === "__pii_policy__" || piiOptionsLoading
                      }
                      onClick={() => void savePiiSettings()}
                    >
                      {busyKey === "__pii_policy__"
                        ? t("common.loading")
                        : t("common.save")}
                    </Button>
                  </Stack>
                </Stack>
              </Paper>
            )}
          </Stack>
        )}

        {tab === "system-init" && (
          <Stack spacing={2}>
            <Paper sx={{ p: 2 }}>
              <Typography variant="h6">
                {t("settings.system_init.title")}
              </Typography>
              <Typography variant="body2" color="text.secondary">
                {t("settings.system_init.description")}
              </Typography>
              <Stack
                direction={{ xs: "column", md: "row" }}
                spacing={1}
                sx={{ mt: 1 }}
              >
                <TextField
                  size="small"
                  label={t("settings.system_init.target_host")}
                  value={wizardTargetHost}
                  onChange={(event) => setWizardTargetHost(event.target.value)}
                  select
                  sx={{ minWidth: 220 }}
                >
                  <MenuItem value="local">local</MenuItem>
                </TextField>
              </Stack>
            </Paper>

            <Paper sx={{ p: 2 }}>
              <Stack direction="row" spacing={1} flexWrap="wrap">
                {wizardSteps.map((step, idx) => (
                  <Chip
                    key={step.id}
                    color={idx === wizardStepIndex ? "primary" : "default"}
                    label={`${idx + 1}. ${step.title}`}
                    onClick={() => setWizardStepIndex(idx)}
                  />
                ))}
              </Stack>
              {wizardSteps[wizardStepIndex] && (
                <Box sx={{ mt: 2 }}>
                  <Typography variant="subtitle1">
                    {wizardSteps[wizardStepIndex].title}
                  </Typography>
                  <Typography variant="body2" color="text.secondary">
                    {wizardSteps[wizardStepIndex].description}
                  </Typography>
                  {wizardSteps[wizardStepIndex].id === "pii" &&
                    !piiModuleEnabled && (
                      <Alert severity="info" sx={{ mt: 1.5 }}>
                        {t("pii_policy.hidden_when_disabled", {
                          defaultValue:
                            "PII settings are hidden because the PII module is currently disabled.",
                        })}
                      </Alert>
                    )}
                  {wizardSteps[wizardStepIndex].id === "mailer" &&
                    !mailerModuleEnabled && (
                      <Alert severity="info" sx={{ mt: 1.5 }}>
                        {t("mailer.hidden_when_disabled", {
                          defaultValue:
                            "Mailer settings are hidden because the Mailer module is currently disabled.",
                        })}
                      </Alert>
                    )}
                  {wizardSteps[wizardStepIndex].setting_keys.filter(
                    (key) =>
                      !(isPiiSettingKey(key) && !piiModuleEnabled) &&
                      !(isMailerSettingKey(key) && !mailerModuleEnabled),
                  ).length > 0 ? (
                    <Stack spacing={1.5} sx={{ mt: 1.5 }}>
                      {wizardSteps[wizardStepIndex].setting_keys
                        .filter(
                          (key) =>
                            !(isPiiSettingKey(key) && !piiModuleEnabled) &&
                            !(isMailerSettingKey(key) && !mailerModuleEnabled),
                        )
                        .map((key) => {
                          const item = schema.find((s) => s.key === key);
                          if (!item) return null;
                          if (item.key === "llm_port_api.pii_default_policy") {
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
                                    setValues((prev) => ({
                                      ...prev,
                                      [item.key]: next,
                                    }))
                                  }
                                />
                              </Box>
                            );
                          }
                          return (
                            <Stack
                              key={item.key}
                              direction={{ xs: "column", md: "row" }}
                              spacing={1}
                            >
                              <TextField
                                size="small"
                                fullWidth
                                type={
                                  item.type === "secret" ? "password" : "text"
                                }
                                label={item.label}
                                value={asInputValue(values[item.key])}
                                onChange={(event) =>
                                  setLocalValue(
                                    item.key,
                                    event.target.value,
                                    item.type,
                                  )
                                }
                                helperText={item.description}
                              />
                              {item.type === "secret" && (
                                <>
                                  <Tooltip
                                    title={t("settings.generate_secret")}
                                  >
                                    <IconButton
                                      size="small"
                                      color="primary"
                                      onClick={() =>
                                        generateSecretForKey(item.key)
                                      }
                                    >
                                      <AutoFixHighIcon fontSize="small" />
                                    </IconButton>
                                  </Tooltip>
                                  <Tooltip title={t("common.copy")}>
                                    <IconButton
                                      size="small"
                                      color="primary"
                                      onClick={() =>
                                        void copySecretForKey(item.key)
                                      }
                                    >
                                      <ContentCopyIcon fontSize="small" />
                                    </IconButton>
                                  </Tooltip>
                                </>
                              )}
                            </Stack>
                          );
                        })}
                    </Stack>
                  ) : (
                    <Typography variant="body2" sx={{ mt: 1.5 }}>
                      {t("settings.system_init.no_fields")}
                    </Typography>
                  )}
                  <Stack direction="row" spacing={1} sx={{ mt: 2 }}>
                    <Button
                      variant="outlined"
                      disabled={wizardStepIndex === 0}
                      onClick={() =>
                        setWizardStepIndex((idx) => Math.max(0, idx - 1))
                      }
                    >
                      {t("common.back")}
                    </Button>
                    <Button
                      variant="contained"
                      disabled={wizardSaving}
                      onClick={() => void applyWizardStep()}
                    >
                      {wizardSaving
                        ? t("common.loading")
                        : t("settings.system_init.apply_step")}
                    </Button>
                    <Button
                      variant="outlined"
                      disabled={wizardStepIndex >= wizardSteps.length - 1}
                      onClick={() =>
                        setWizardStepIndex((idx) =>
                          Math.min(wizardSteps.length - 1, idx + 1),
                        )
                      }
                    >
                      {t("settings.system_init.next_step")}
                    </Button>
                  </Stack>
                </Box>
              )}
            </Paper>
          </Stack>
        )}

        {tab === "modules" && <ModulesTab />}
      </Box>
    </Box>
  );
}
