/**
 * PiiPanel — session-scoped PII override controls.
 *
 * Shows the effective PII policy (floor + override), lets users
 * strengthen the override, and clear it. Floor-enforced fields are
 * shown as locked (disabled) with a tooltip.
 */
import { useCallback, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import FormControl from "@mui/material/FormControl";
import FormControlLabel from "@mui/material/FormControlLabel";
import InputLabel from "@mui/material/InputLabel";
import MenuItem from "@mui/material/MenuItem";
import Paper from "@mui/material/Paper";
import Select from "@mui/material/Select";
import Skeleton from "@mui/material/Skeleton";
import Slider from "@mui/material/Slider";
import Stack from "@mui/material/Stack";
import Switch from "@mui/material/Switch";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import LockIcon from "@mui/icons-material/Lock";
import RefreshIcon from "@mui/icons-material/Refresh";
import RestartAltIcon from "@mui/icons-material/RestartAlt";
import IconButton from "@mui/material/IconButton";

import type {
  SessionPIIOverride,
  SessionPIIPolicy,
  PIIPolicyConfig,
} from "~/api/pii";

// ── Props ──────────────────────────────────────────────────────

interface Props {
  policy: SessionPIIPolicy | null;
  loading: boolean;
  error: string | null;
  onUpdate: (patch: SessionPIIOverride) => Promise<void>;
  onClear: () => Promise<void>;
  onRefresh: () => Promise<void>;
}

// ── Helpers ────────────────────────────────────────────────────

// ── Component ──────────────────────────────────────────────────

export default function PiiPanel({
  policy,
  loading,
  error,
  onUpdate,
  onClear,
  onRefresh,
}: Props) {
  const { t } = useTranslation("chat");
  const [saving, setSaving] = useState(false);

  const floor: PIIPolicyConfig | null = policy?.floor ?? null;
  const effective: PIIPolicyConfig | null = policy?.effective ?? null;

  // Derived floor values
  const floorEgressCloud = floor?.egress?.enabled_for_cloud ?? false;
  const floorEgressLocal = floor?.egress?.enabled_for_local ?? false;
  const floorTelemetry = floor?.telemetry?.enabled ?? false;
  const floorThreshold = floor?.presidio?.threshold ?? 0;
  const floorEntities: string[] = useMemo(
    () => floor?.presidio?.entities ?? [],
    [floor],
  );

  // Effective display values
  const egressCloud = effective?.egress?.enabled_for_cloud ?? false;
  const egressLocal = effective?.egress?.enabled_for_local ?? false;
  const egressMode = effective?.egress?.mode ?? "redact";
  const failAction = effective?.egress?.fail_action ?? "block";
  const telemetryEnabled = effective?.telemetry?.enabled ?? false;
  const threshold = effective?.presidio?.threshold ?? 0;
  const allEntities: string[] = useMemo(
    () => effective?.presidio?.entities ?? [],
    [effective],
  );

  const floorEntitySet = useMemo(() => new Set(floorEntities), [floorEntities]);
  const addedEntities = useMemo(
    () => allEntities.filter((e) => !floorEntitySet.has(e)),
    [allEntities, floorEntitySet],
  );

  // Handlers with debounced save
  const save = useCallback(
    async (patch: SessionPIIOverride) => {
      setSaving(true);
      try {
        await onUpdate(patch);
      } finally {
        setSaving(false);
      }
    },
    [onUpdate],
  );

  // Loading skeleton
  if (loading && !policy) {
    return (
      <Box sx={{ p: 2 }}>
        <Stack spacing={2}>
          <Skeleton variant="rounded" height={60} />
          <Skeleton variant="rounded" height={80} />
          <Skeleton variant="rounded" height={80} />
        </Stack>
      </Box>
    );
  }

  // No session selected
  if (!policy) {
    return (
      <Box sx={{ p: 2 }}>
        <Typography variant="body2" color="text.secondary">
          {t(
            "pii_panel.no_session",
            "Select or create a session to view PII settings.",
          )}
        </Typography>
      </Box>
    );
  }

  return (
    <Box sx={{ p: 1.5, overflow: "auto" }}>
      <Stack spacing={1.5}>
        {/* Header */}
        <Stack
          direction="row"
          alignItems="center"
          justifyContent="space-between"
        >
          <Typography variant="subtitle2" color="text.secondary">
            {policy.has_override
              ? t("pii_panel.override_active", "Session override active")
              : t("pii_panel.using_defaults", "Using tenant defaults")}
          </Typography>
          <Stack direction="row" spacing={0.5}>
            {policy.has_override && (
              <Tooltip
                title={t(
                  "pii_panel.clear_override_tooltip",
                  "Clear override (revert to defaults)",
                )}
              >
                <IconButton size="small" onClick={onClear} disabled={saving}>
                  <RestartAltIcon fontSize="small" />
                </IconButton>
              </Tooltip>
            )}
            <Tooltip title={t("pii_panel.refresh", "Refresh")}>
              <IconButton size="small" onClick={onRefresh} disabled={loading}>
                <RefreshIcon fontSize="small" />
              </IconButton>
            </Tooltip>
          </Stack>
        </Stack>

        {error && (
          <Alert severity="error" variant="outlined" sx={{ py: 0 }}>
            {error}
          </Alert>
        )}
        {saving && <CircularProgress size={16} sx={{ alignSelf: "center" }} />}

        {/* Egress section */}
        <Paper variant="outlined" sx={{ p: 1.5 }}>
          <Typography variant="subtitle2" sx={{ fontWeight: 600, mb: 1 }}>
            {t("pii_panel.egress_protection", "Egress Protection")}
          </Typography>
          <Stack spacing={1}>
            <Tooltip
              title={
                floorEgressCloud
                  ? t("pii_panel.locked_tooltip", "Enforced by system policy")
                  : ""
              }
              placement="left"
            >
              <FormControlLabel
                control={
                  <Switch
                    size="small"
                    checked={egressCloud}
                    onChange={(e) =>
                      save({ egress_enabled_for_cloud: e.target.checked })
                    }
                    disabled={floorEgressCloud || saving}
                  />
                }
                label={
                  <Stack direction="row" spacing={0.5} alignItems="center">
                    <Typography variant="body2">
                      {t("pii_panel.cloud_egress", "Cloud egress")}
                    </Typography>
                    {floorEgressCloud && (
                      <LockIcon sx={{ fontSize: 14, color: "text.disabled" }} />
                    )}
                  </Stack>
                }
              />
            </Tooltip>
            <Tooltip
              title={
                floorEgressLocal
                  ? t("pii_panel.locked_tooltip", "Enforced by system policy")
                  : ""
              }
              placement="left"
            >
              <FormControlLabel
                control={
                  <Switch
                    size="small"
                    checked={egressLocal}
                    onChange={(e) =>
                      save({ egress_enabled_for_local: e.target.checked })
                    }
                    disabled={floorEgressLocal || saving}
                  />
                }
                label={
                  <Stack direction="row" spacing={0.5} alignItems="center">
                    <Typography variant="body2">
                      {t("pii_panel.local_egress", "Local egress")}
                    </Typography>
                    {floorEgressLocal && (
                      <LockIcon sx={{ fontSize: 14, color: "text.disabled" }} />
                    )}
                  </Stack>
                }
              />
            </Tooltip>
            <Stack direction="row" spacing={1}>
              <FormControl size="small" fullWidth>
                <InputLabel>{t("common:mode", "Mode")}</InputLabel>
                <Select
                  label={t("common:mode", "Mode")}
                  value={egressMode}
                  onChange={(e) =>
                    save({
                      egress_mode: e.target.value as
                        | "redact"
                        | "tokenize_reversible",
                    })
                  }
                  disabled={saving}
                >
                  <MenuItem value="redact">
                    {t("pii_panel.redact", "redact")}
                  </MenuItem>
                  <MenuItem value="tokenize_reversible">
                    {t("pii_panel.tokenize_reversible", "tokenize_reversible")}
                  </MenuItem>
                </Select>
              </FormControl>
              <FormControl size="small" fullWidth>
                <InputLabel>
                  {t("pii_panel.fail_action", "Fail Action")}
                </InputLabel>
                <Select
                  label={t("pii_panel.fail_action", "Fail Action")}
                  value={failAction}
                  onChange={(e) =>
                    save({
                      egress_fail_action: e.target.value as
                        | "block"
                        | "allow"
                        | "fallback_to_local",
                    })
                  }
                  disabled={saving}
                >
                  <MenuItem value="block">
                    {t("pii_panel.block", "block")}
                  </MenuItem>
                  <MenuItem value="allow">
                    {t("pii_panel.allow", "allow")}
                  </MenuItem>
                  <MenuItem value="fallback_to_local">
                    {t("pii_panel.fallback_to_local", "fallback_to_local")}
                  </MenuItem>
                </Select>
              </FormControl>
            </Stack>
          </Stack>
        </Paper>

        {/* Telemetry section */}
        <Paper variant="outlined" sx={{ p: 1.5 }}>
          <Typography variant="subtitle2" sx={{ fontWeight: 600, mb: 1 }}>
            {t("pii_panel.telemetry", "Telemetry")}
          </Typography>
          <Tooltip
            title={
              floorTelemetry
                ? t("pii_panel.locked_tooltip", "Enforced by system policy")
                : ""
            }
            placement="left"
          >
            <FormControlLabel
              control={
                <Switch
                  size="small"
                  checked={telemetryEnabled}
                  onChange={(e) =>
                    save({ telemetry_enabled: e.target.checked })
                  }
                  disabled={floorTelemetry || saving}
                />
              }
              label={
                <Stack direction="row" spacing={0.5} alignItems="center">
                  <Typography variant="body2">
                    {t("pii_panel.sanitize_telemetry", "Sanitize telemetry")}
                  </Typography>
                  {floorTelemetry && (
                    <LockIcon sx={{ fontSize: 14, color: "text.disabled" }} />
                  )}
                </Stack>
              }
            />
          </Tooltip>
        </Paper>

        {/* Presidio section */}
        <Paper variant="outlined" sx={{ p: 1.5 }}>
          <Typography variant="subtitle2" sx={{ fontWeight: 600, mb: 1 }}>
            {t("pii_panel.detection_settings", "Detection Settings")}
          </Typography>
          <Stack spacing={1.5}>
            {/* Threshold slider */}
            <Box>
              <Typography variant="body2" color="text.secondary" gutterBottom>
                {t("pii_panel.score_threshold", "Score Threshold: {{value}}", {
                  value: threshold.toFixed(2),
                })}
                {floorThreshold > 0 && (
                  <Typography
                    component="span"
                    variant="caption"
                    color="text.disabled"
                    sx={{ ml: 1 }}
                  >
                    {t("pii_panel.floor_value", "(floor: {{value}})", {
                      value: floorThreshold.toFixed(2),
                    })}
                  </Typography>
                )}
              </Typography>
              <Slider
                size="small"
                value={threshold}
                min={floorThreshold}
                max={1}
                step={0.01}
                onChange={(_, val) =>
                  save({ presidio_threshold: val as number })
                }
                disabled={saving}
                valueLabelDisplay="auto"
              />
            </Box>

            {/* Entity chips */}
            <Box>
              <Typography variant="body2" color="text.secondary" gutterBottom>
                {t("pii_panel.entities_count", "Entities ({{count}})", {
                  count: allEntities.length,
                })}
              </Typography>
              <Stack direction="row" flexWrap="wrap" useFlexGap spacing={0.5}>
                {floorEntities.map((entity) => (
                  <Chip
                    key={entity}
                    label={entity}
                    size="small"
                    variant="outlined"
                    icon={<LockIcon sx={{ fontSize: "14px !important" }} />}
                  />
                ))}
                {addedEntities.map((entity) => (
                  <Chip
                    key={entity}
                    label={entity}
                    size="small"
                    color="primary"
                    variant="outlined"
                    onDelete={() => {
                      // Remove by re-patching with entities minus this one
                      const next = addedEntities.filter((e) => e !== entity);
                      save({
                        presidio_entities_add: next.length > 0 ? next : null,
                      });
                    }}
                  />
                ))}
                {allEntities.length === 0 && (
                  <Typography variant="caption" color="text.disabled">
                    {t("pii_panel.no_entities", "No entities configured.")}
                  </Typography>
                )}
              </Stack>
            </Box>

            {/* Add entity */}
            <AddEntityControl
              existingEntities={allEntities}
              disabled={saving}
              onAdd={(entity) =>
                save({ presidio_entities_add: [...addedEntities, entity] })
              }
            />
          </Stack>
        </Paper>

        {/* Clear override button */}
        {policy.has_override && (
          <Button
            variant="outlined"
            color="warning"
            size="small"
            startIcon={<RestartAltIcon />}
            onClick={onClear}
            disabled={saving}
          >
            {t("pii_panel.clear_override", "Clear Session Override")}
          </Button>
        )}
      </Stack>
    </Box>
  );
}

// ── AddEntityControl ───────────────────────────────────────────

// Common Presidio entities for the dropdown
const COMMON_ENTITIES = [
  "PERSON",
  "EMAIL_ADDRESS",
  "PHONE_NUMBER",
  "CREDIT_CARD",
  "IBAN_CODE",
  "IP_ADDRESS",
  "US_SSN",
  "LOCATION",
  "DATE_TIME",
  "NRP",
  "MEDICAL_LICENSE",
  "URL",
  "US_DRIVER_LICENSE",
  "US_PASSPORT",
  "US_BANK_NUMBER",
  "UK_NHS",
  "CRYPTO",
  "AU_ABN",
  "AU_ACN",
  "AU_TFN",
  "AU_MEDICARE",
];

function AddEntityControl({
  existingEntities,
  disabled,
  onAdd,
}: {
  existingEntities: string[];
  disabled: boolean;
  onAdd: (entity: string) => void;
}) {
  const { t } = useTranslation("chat");
  const [value, setValue] = useState("");
  const available = COMMON_ENTITIES.filter(
    (e) => !existingEntities.includes(e),
  );

  if (available.length === 0) return null;

  return (
    <Stack direction="row" spacing={1} alignItems="center">
      <FormControl size="small" fullWidth>
        <InputLabel>{t("pii_panel.add_entity", "Add entity")}</InputLabel>
        <Select
          label={t("pii_panel.add_entity", "Add entity")}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          disabled={disabled}
        >
          {available.map((entity) => (
            <MenuItem key={entity} value={entity}>
              {entity}
            </MenuItem>
          ))}
        </Select>
      </FormControl>
      <Button
        variant="outlined"
        size="small"
        disabled={disabled || !value}
        onClick={() => {
          if (value) {
            onAdd(value);
            setValue("");
          }
        }}
      >
        {t("common:add", "Add")}
      </Button>
    </Stack>
  );
}
