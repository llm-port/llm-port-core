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

import UsersPage from "~/pages/admin/UsersPage";
import { systemSettingsApi, type SystemSettingSchemaItem, type WizardStep } from "~/api/systemSettings";

type SettingsTab = "general" | "system-init" | "users";

function getCurrentTab(pathname: string, tabQuery: string | null): SettingsTab {
  if (pathname === "/admin/users") return "users";
  if (tabQuery === "users") return "users";
  if (tabQuery === "system-init") return "system-init";
  return "general";
}

function asInputValue(value: unknown): string {
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (value === null || value === undefined) return "";
  try {
    return JSON.stringify(value);
  } catch {
    return "";
  }
}

function generateSecureToken(length = 48): string {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
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

export default function SettingsPage() {
  const { t } = useTranslation();
  const location = useLocation();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [tab, setTab] = useState<SettingsTab>(() => getCurrentTab(location.pathname, searchParams.get("tab")));

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
      setError(e instanceof Error ? e.message : "Failed to load system settings.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadSettings();
  }, []);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    const items = q
      ? schema.filter((s) => `${s.label} ${s.description} ${s.key} ${s.category} ${s.group}`.toLowerCase().includes(q))
      : schema;
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

  function setLocalValue(key: string, raw: string, type: SystemSettingSchemaItem["type"]) {
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
      setError(e instanceof Error ? e.message : t("settings.secret_generate_failed"));
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
      setError(e instanceof Error ? e.message : t("settings.secret_copy_failed"));
    }
  }

  async function saveKey(item: SystemSettingSchemaItem) {
    setBusyKey(item.key);
    setStatusMessage(null);
    setError(null);
    try {
      const value = values[item.key];
      const result = await systemSettingsApi.update(item.key, value, "local");
      setStatusMessage(`${item.label}: ${result.apply_status} (${result.apply_scope})`);
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

  async function applyWizardStep() {
    const step = wizardSteps[wizardStepIndex];
    if (!step) return;
    setWizardSaving(true);
    setStatusMessage(null);
    setError(null);
    try {
      const payload: Record<string, unknown> = {};
      for (const key of step.setting_keys) {
        if (values[key] !== undefined) payload[key] = values[key];
      }
      const result = await systemSettingsApi.wizardApply(payload, wizardTargetHost);
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
    <Box sx={{ display: "flex", flexDirection: "column", minHeight: 0, height: "100%" }}>
      <Box sx={{ mb: 2 }}>
        <Typography variant="h5" sx={{ mb: 1 }}>
          {t("settings.title")}
        </Typography>
        <Tabs value={tab} onChange={handleTabChange}>
          <Tab label={t("settings.general.title")} value="general" />
          <Tab label={t("settings.system_init.title")} value="system-init" />
          <Tab label={t("users.title")} value="users" />
        </Tabs>
      </Box>

      {error && <Alert severity="error" sx={{ mb: 1 }}>{error}</Alert>}
      {statusMessage && <Alert severity="success" sx={{ mb: 1 }}>{statusMessage}</Alert>}

      <Box sx={{ minHeight: 0, flexGrow: 1, display: "flex", flexDirection: "column", overflow: "auto" }}>
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
              <Typography color="text.secondary">{t("settings.no_results")}</Typography>
            )}

            {[...filtered.entries()].map(([groupKey, items]) => {
              const [category, group] = groupKey.split("::");
              return (
                <Paper key={groupKey} sx={{ p: 2 }}>
                  <Typography variant="subtitle1">
                    {toTitleCase(category.replaceAll("_", " "))} / {toTitleCase(group.replaceAll("_", " "))}
                  </Typography>
                  <Divider sx={{ my: 1 }} />
                  <Stack spacing={1.5}>
                    {items.map((item) => (
                      <Box key={item.key}>
                        <Stack direction={{ xs: "column", md: "row" }} spacing={1} alignItems={{ xs: "stretch", md: "center" }}>
                          <TextField
                            size="small"
                            fullWidth
                            type={item.type === "secret" ? "password" : "text"}
                            label={item.label}
                            value={asInputValue(values[item.key])}
                            onChange={(event) => setLocalValue(item.key, event.target.value, item.type)}
                            helperText={`${item.description}${item.is_secret && secretMasked[item.key] ? ` (current: ${secretMasked[item.key]})` : ""}`}
                          />
                          <Chip
                            size="small"
                            color={item.apply_scope === "live_reload" ? "success" : item.apply_scope === "service_restart" ? "warning" : "error"}
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
                                  onClick={() => void copySecretForKey(item.key)}
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
                            {busyKey === item.key ? t("common.loading") : t("common.save")}
                          </Button>
                        </Stack>
                        <Typography variant="caption" color="text.secondary">{item.key}</Typography>
                      </Box>
                    ))}
                  </Stack>
                </Paper>
              );
            })}
          </Stack>
        )}

        {tab === "system-init" && (
          <Stack spacing={2}>
            <Paper sx={{ p: 2 }}>
              <Typography variant="h6">{t("settings.system_init.title")}</Typography>
              <Typography variant="body2" color="text.secondary">
                {t("settings.system_init.description")}
              </Typography>
              <Stack direction={{ xs: "column", md: "row" }} spacing={1} sx={{ mt: 1 }}>
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
                  <Typography variant="subtitle1">{wizardSteps[wizardStepIndex].title}</Typography>
                  <Typography variant="body2" color="text.secondary">
                    {wizardSteps[wizardStepIndex].description}
                  </Typography>
                  {wizardSteps[wizardStepIndex].setting_keys.length > 0 ? (
                    <Stack spacing={1.5} sx={{ mt: 1.5 }}>
                      {wizardSteps[wizardStepIndex].setting_keys.map((key) => {
                        const item = schema.find((s) => s.key === key);
                        if (!item) return null;
                        return (
                          <Stack key={item.key} direction={{ xs: "column", md: "row" }} spacing={1}>
                            <TextField
                              size="small"
                              fullWidth
                              type={item.type === "secret" ? "password" : "text"}
                              label={item.label}
                              value={asInputValue(values[item.key])}
                              onChange={(event) => setLocalValue(item.key, event.target.value, item.type)}
                              helperText={item.description}
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
                                    onClick={() => void copySecretForKey(item.key)}
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
                      onClick={() => setWizardStepIndex((idx) => Math.max(0, idx - 1))}
                    >
                      {t("common.back")}
                    </Button>
                    <Button
                      variant="contained"
                      disabled={wizardSaving}
                      onClick={() => void applyWizardStep()}
                    >
                      {wizardSaving ? t("common.loading") : t("settings.system_init.apply_step")}
                    </Button>
                    <Button
                      variant="outlined"
                      disabled={wizardStepIndex >= wizardSteps.length - 1}
                      onClick={() => setWizardStepIndex((idx) => Math.min(wizardSteps.length - 1, idx + 1))}
                    >
                      {t("settings.system_init.next_step")}
                    </Button>
                  </Stack>
                </Box>
              )}
            </Paper>
          </Stack>
        )}

        {tab === "users" && <UsersPage />}
      </Box>
    </Box>
  );
}
