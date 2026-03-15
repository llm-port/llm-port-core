/**
 * Dynamic settings form that renders MUI controls from a JSON Schema.
 *
 * Supports: string, integer/number, boolean, enum, x-sensitive (password).
 */

import { useEffect, useState, useCallback } from "react";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import CircularProgress from "@mui/material/CircularProgress";
import MenuItem from "@mui/material/MenuItem";
import Stack from "@mui/material/Stack";
import Switch from "@mui/material/Switch";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import FormControlLabel from "@mui/material/FormControlLabel";

import SaveIcon from "@mui/icons-material/Save";
import SettingsIcon from "@mui/icons-material/Settings";

import {
  getSettingsSchema,
  getSettings,
  updateSettings,
  type JSONSchema,
} from "~/api/mcp";

interface Props {
  serverId: string;
}

interface SchemaProperty {
  type?: string;
  title?: string;
  description?: string;
  default?: unknown;
  enum?: string[];
  minimum?: number;
  maximum?: number;
  "x-sensitive"?: boolean;
}

export function MCPSettingsPanel({ serverId }: Props) {
  const { t } = useTranslation();
  const [schema, setSchema] = useState<JSONSchema | null>(null);
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  const loadSettings = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [schemaData, valuesData] = await Promise.all([
        getSettingsSchema(serverId),
        getSettings(serverId),
      ]);
      setSchema(schemaData);
      setValues(valuesData);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load settings");
    } finally {
      setLoading(false);
    }
  }, [serverId]);

  useEffect(() => {
    loadSettings();
  }, [loadSettings]);

  async function handleSave() {
    setSaving(true);
    setError(null);
    setSuccess(false);
    try {
      const updated = await updateSettings(serverId, values);
      setValues(updated);
      setSuccess(true);
      setTimeout(() => setSuccess(false), 3000);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to save settings");
    } finally {
      setSaving(false);
    }
  }

  function handleChange(key: string, value: unknown) {
    setValues((prev) => ({ ...prev, [key]: value }));
  }

  if (loading) {
    return (
      <Card sx={{ mb: 3 }}>
        <CardContent>
          <Stack direction="row" alignItems="center" spacing={1}>
            <CircularProgress size={20} />
            <Typography>{t("common.loading", "Loading…")}</Typography>
          </Stack>
        </CardContent>
      </Card>
    );
  }

  if (error && !schema) {
    // Settings not available (e.g. stdio server or server doesn't support it)
    return null;
  }

  if (!schema) return null;

  const properties = (schema.properties ?? {}) as Record<
    string,
    SchemaProperty
  >;
  const fieldNames = Object.keys(properties);

  if (fieldNames.length === 0) return null;

  return (
    <Card sx={{ mb: 3 }}>
      <CardContent>
        <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 2 }}>
          <SettingsIcon fontSize="small" color="action" />
          <Typography variant="h6" fontWeight={600}>
            {t("mcp.provider_settings", "Provider Settings")}
          </Typography>
        </Stack>

        {error && (
          <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>
            {error}
          </Alert>
        )}
        {success && (
          <Alert severity="success" sx={{ mb: 2 }}>
            {t("mcp.settings_saved", "Settings saved successfully.")}
          </Alert>
        )}

        <Stack spacing={2}>
          {fieldNames.map((key) => {
            const prop = properties[key];
            return (
              <SettingsField
                key={key}
                fieldKey={key}
                prop={prop}
                value={values[key]}
                onChange={(v) => handleChange(key, v)}
                disabled={saving}
              />
            );
          })}
        </Stack>

        <Box sx={{ mt: 2, display: "flex", justifyContent: "flex-end" }}>
          <Button
            variant="contained"
            startIcon={<SaveIcon />}
            onClick={handleSave}
            disabled={saving}
            size="small"
          >
            {saving ? t("common.saving", "Saving…") : t("common.save", "Save")}
          </Button>
        </Box>
      </CardContent>
    </Card>
  );
}

// ── Individual field renderer ─────────────────────────────────────────

interface FieldProps {
  fieldKey: string;
  prop: SchemaProperty;
  value: unknown;
  onChange: (value: unknown) => void;
  disabled?: boolean;
}

function SettingsField({
  fieldKey,
  prop,
  value,
  onChange,
  disabled,
}: FieldProps) {
  const label = prop.title ?? fieldKey;
  const helperText = prop.description;

  // Boolean → Switch
  if (prop.type === "boolean") {
    return (
      <FormControlLabel
        control={
          <Switch
            checked={Boolean(value)}
            onChange={(e) => onChange(e.target.checked)}
            disabled={disabled}
            size="small"
          />
        }
        label={
          <Box>
            <Typography variant="body2">{label}</Typography>
            {helperText && (
              <Typography variant="caption" color="text.secondary">
                {helperText}
              </Typography>
            )}
          </Box>
        }
      />
    );
  }

  // Enum → Select
  if (prop.enum && prop.enum.length > 0) {
    return (
      <TextField
        label={label}
        select
        value={String(value ?? "")}
        onChange={(e) => onChange(e.target.value)}
        helperText={helperText}
        fullWidth
        size="small"
        disabled={disabled}
      >
        {prop.enum.map((opt) => (
          <MenuItem key={opt} value={opt}>
            {opt}
          </MenuItem>
        ))}
      </TextField>
    );
  }

  // Integer / Number
  if (prop.type === "integer" || prop.type === "number") {
    return (
      <TextField
        label={label}
        type="number"
        value={value ?? ""}
        onChange={(e) => {
          const v = e.target.value;
          onChange(
            prop.type === "integer" ? parseInt(v, 10) || 0 : parseFloat(v) || 0,
          );
        }}
        helperText={helperText}
        fullWidth
        size="small"
        disabled={disabled}
        slotProps={{
          htmlInput: {
            min: prop.minimum,
            max: prop.maximum,
          },
        }}
      />
    );
  }

  // String (default) — check x-sensitive for password field
  const isSensitive = prop["x-sensitive"] === true;
  return (
    <TextField
      label={label}
      type={isSensitive ? "password" : "text"}
      value={String(value ?? "")}
      onChange={(e) => onChange(e.target.value)}
      helperText={helperText}
      fullWidth
      size="small"
      disabled={disabled}
    />
  );
}
