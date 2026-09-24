import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import FormControl from "@mui/material/FormControl";
import FormControlLabel from "@mui/material/FormControlLabel";
import InputLabel from "@mui/material/InputLabel";
import MenuItem from "@mui/material/MenuItem";
import Paper from "@mui/material/Paper";
import Select from "@mui/material/Select";
import Stack from "@mui/material/Stack";
import Switch from "@mui/material/Switch";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import {
  type PIIPolicyConfig,
  type PIIPolicyOptionsResponse,
} from "~/api/pii";

interface PiiPolicyFormProps {
  value: PIIPolicyConfig;
  options: PIIPolicyOptionsResponse | null;
  disabled?: boolean;
  onChange: (next: PIIPolicyConfig) => void;
}

function unique(values: string[]): string[] {
  return Array.from(new Set(values));
}

export default function PiiPolicyForm({
  value,
  options,
  disabled = false,
  onChange,
}: PiiPolicyFormProps) {
  const { t } = useTranslation();
  const knownEntitySet = useMemo(
    () => new Set(options?.supported_entities ?? []),
    [options],
  );
  const entities = Array.isArray(value.presidio.entities) ? value.presidio.entities : [];
  const selectedEntities = options
    ? entities.filter((entity) => knownEntitySet.has(entity))
    : entities;
  const legacyEntities = options
    ? entities.filter((entity) => !knownEntitySet.has(entity))
    : [];
  const availableEntities = (options?.supported_entities ?? []).filter(
    (entity) => !selectedEntities.includes(entity),
  );
  const [entityToAdd, setEntityToAdd] = useState("");

  useEffect(() => {
    if (!availableEntities.includes(entityToAdd)) {
      setEntityToAdd(availableEntities[0] ?? "");
    }
  }, [availableEntities, entityToAdd]);

  function patch(next: Partial<PIIPolicyConfig>) {
    onChange({
      telemetry: {
        ...value.telemetry,
        ...(next.telemetry ?? {}),
      },
      egress: {
        ...value.egress,
        ...(next.egress ?? {}),
      },
      presidio: {
        ...value.presidio,
        ...(next.presidio ?? {}),
      },
    });
  }

  function setEntities(nextKnown: string[]) {
    patch({
      presidio: {
        ...value.presidio,
        entities: unique([...nextKnown, ...legacyEntities]),
      },
    });
  }

  function addEntity() {
    if (!entityToAdd || selectedEntities.includes(entityToAdd)) return;
    setEntities([...selectedEntities, entityToAdd]);
  }

  function removeEntity(entity: string) {
    setEntities(selectedEntities.filter((item) => item !== entity));
  }

  return (
    <Stack spacing={2}>
      <Paper variant="outlined" sx={{ p: 2 }}>
        <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>
          {t("pii_policy.form.telemetry")}
        </Typography>
        <Stack spacing={1.5}>
          <FormControlLabel
            control={(
              <Switch
                checked={value.telemetry.enabled}
                onChange={(event) => patch({ telemetry: { ...value.telemetry, enabled: event.target.checked } })}
                disabled={disabled}
              />
            )}
            label={t("pii_policy.form.telemetry_enable")}
          />
          <Stack direction={{ xs: "column", md: "row" }} spacing={1.5}>
            <FormControl size="small" fullWidth>
              <InputLabel>{t("common.mode")}</InputLabel>
              <Select
                label={t("common.mode")}
                value={value.telemetry.mode}
                onChange={(event) => patch({
                  telemetry: {
                    ...value.telemetry,
                    mode: event.target.value as PIIPolicyConfig["telemetry"]["mode"],
                  },
                })}
                disabled={disabled}
              >
                {(options?.telemetry_modes ?? ["sanitized", "metrics_only"]).map((mode) => (
                  <MenuItem key={mode} value={mode}>{mode}</MenuItem>
                ))}
              </Select>
            </FormControl>
            <FormControlLabel
              control={(
                <Switch
                  checked={value.telemetry.store_raw}
                  onChange={(event) => patch({ telemetry: { ...value.telemetry, store_raw: event.target.checked } })}
                  disabled={disabled}
                />
              )}
              label={t("pii_policy.form.store_raw")}
            />
          </Stack>
        </Stack>
      </Paper>

      <Paper variant="outlined" sx={{ p: 2 }}>
        <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>
          {t("pii_policy.form.egress")}
        </Typography>
        <Stack spacing={1.5}>
          <Stack direction={{ xs: "column", md: "row" }} spacing={1.5}>
            <FormControlLabel
              control={(
                <Switch
                  checked={value.egress.enabled_for_cloud}
                  onChange={(event) => patch({ egress: { ...value.egress, enabled_for_cloud: event.target.checked } })}
                  disabled={disabled}
                />
              )}
              label={t("pii_policy.form.egress_cloud")}
            />
            <FormControlLabel
              control={(
                <Switch
                  checked={value.egress.enabled_for_local}
                  onChange={(event) => patch({ egress: { ...value.egress, enabled_for_local: event.target.checked } })}
                  disabled={disabled}
                />
              )}
              label={t("pii_policy.form.egress_local")}
            />
          </Stack>
          <Stack direction={{ xs: "column", md: "row" }} spacing={1.5}>
            <FormControl size="small" fullWidth>
              <InputLabel>{t("common.mode")}</InputLabel>
              <Select
                label={t("common.mode")}
                value={value.egress.mode}
                onChange={(event) => patch({
                  egress: {
                    ...value.egress,
                    mode: event.target.value as PIIPolicyConfig["egress"]["mode"],
                  },
                })}
                disabled={disabled}
              >
                {(options?.egress_modes ?? ["redact", "tokenize_reversible"]).map((mode) => (
                  <MenuItem key={mode} value={mode}>{mode}</MenuItem>
                ))}
              </Select>
            </FormControl>
            <FormControl size="small" fullWidth>
              <InputLabel>{t("pii_policy.form.fail_action")}</InputLabel>
              <Select
                label={t("pii_policy.form.fail_action")}
                value={value.egress.fail_action}
                onChange={(event) => patch({
                  egress: {
                    ...value.egress,
                    fail_action: event.target.value as PIIPolicyConfig["egress"]["fail_action"],
                  },
                })}
                disabled={disabled}
              >
                {(options?.fail_actions ?? ["block", "allow", "fallback_to_local"]).map((mode) => (
                  <MenuItem key={mode} value={mode}>{mode}</MenuItem>
                ))}
              </Select>
            </FormControl>
          </Stack>
        </Stack>
      </Paper>

      <Paper variant="outlined" sx={{ p: 2 }}>
        <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>
          {t("pii_policy.form.presidio")}
        </Typography>
        <Stack spacing={1.5}>
          <Stack direction={{ xs: "column", md: "row" }} spacing={1.5}>
            <FormControl size="small" fullWidth>
              <InputLabel>{t("pii_policy.form.language")}</InputLabel>
              <Select
                label={t("pii_policy.form.language")}
                value={value.presidio.language}
                onChange={(event) => patch({
                  presidio: {
                    ...value.presidio,
                    language: event.target.value,
                  },
                })}
                disabled={disabled}
              >
                {(options?.supported_languages ?? ["en"]).map((lang) => (
                  <MenuItem key={lang} value={lang}>{lang}</MenuItem>
                ))}
              </Select>
            </FormControl>
            <TextField
              size="small"
              fullWidth
              type="number"
              label={t("pii_policy.form.score_threshold")}
              value={value.presidio.threshold}
              inputProps={{ min: 0, max: 1, step: 0.01 }}
              onChange={(event) => {
                const parsed = Number(event.target.value);
                patch({
                  presidio: {
                    ...value.presidio,
                    threshold: Number.isFinite(parsed) ? Math.max(0, Math.min(1, parsed)) : 0,
                  },
                });
              }}
              disabled={disabled}
            />
          </Stack>

          <Stack spacing={1}>
            <Typography variant="body2" color="text.secondary">
              {t("pii_policy.form.entities")}
            </Typography>
            <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
              {selectedEntities.map((entity) => (
                <Chip
                  key={entity}
                  label={entity}
                  onDelete={disabled ? undefined : () => removeEntity(entity)}
                  size="small"
                />
              ))}
              {legacyEntities.map((entity) => (
                <Chip
                  key={`legacy-${entity}`}
                  label={t("pii_policy.form.legacy_entity", { entity })}
                  size="small"
                  color="warning"
                  variant="outlined"
                />
              ))}
              {entities.length === 0 && (
                <Typography variant="caption" color="text.secondary">
                  {t("pii_policy.form.no_entities")}
                </Typography>
              )}
            </Stack>
            <Stack direction={{ xs: "column", md: "row" }} spacing={1}>
              <FormControl size="small" fullWidth>
                <InputLabel>{t("pii_policy.form.add_entity")}</InputLabel>
                <Select
                  label={t("pii_policy.form.add_entity")}
                  value={entityToAdd}
                  onChange={(event) => setEntityToAdd(String(event.target.value))}
                  disabled={disabled || availableEntities.length === 0}
                >
                  {availableEntities.map((entity) => (
                    <MenuItem key={entity} value={entity}>{entity}</MenuItem>
                  ))}
                </Select>
              </FormControl>
              <Button
                variant="outlined"
                onClick={addEntity}
                disabled={disabled || !entityToAdd}
              >
                {t("common.add")}
              </Button>
            </Stack>
          </Stack>
        </Stack>
      </Paper>
    </Stack>
  );
}
