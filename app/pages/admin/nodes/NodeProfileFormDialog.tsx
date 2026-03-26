import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import type {
  NodeProfile,
  NodeProfileCreatePayload,
  NodeProfileUpdatePayload,
} from "~/api/nodes";
import { FormDialog } from "~/components/FormDialog";

import Checkbox from "@mui/material/Checkbox";
import FormControlLabel from "@mui/material/FormControlLabel";
import Stack from "@mui/material/Stack";
import Tab from "@mui/material/Tab";
import Tabs from "@mui/material/Tabs";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

/* ── JSON editor for a config section ────────────────────────────────── */

function JsonConfigField({
  label,
  value,
  onChange,
  error,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  error?: string;
}) {
  return (
    <TextField
      label={label}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      multiline
      minRows={4}
      maxRows={12}
      fullWidth
      error={!!error}
      helperText={error}
      slotProps={{
        input: { sx: { fontFamily: "monospace", fontSize: "0.85rem" } },
      }}
    />
  );
}

/* ── config section tabs ─────────────────────────────────────────────── */

const CONFIG_SECTIONS = [
  { key: "runtime_config", label: "Runtime" },
  { key: "gpu_config", label: "GPU" },
  { key: "storage_config", label: "Storage" },
  { key: "network_config", label: "Network" },
  { key: "logging_config", label: "Logging" },
  { key: "security_config", label: "Security" },
  { key: "update_config", label: "Updates" },
] as const;

type ConfigKey = (typeof CONFIG_SECTIONS)[number]["key"];

/* ── main dialog component ───────────────────────────────────────────── */

interface Props {
  open: boolean;
  onClose: () => void;
  onSave: (
    payload: NodeProfileCreatePayload | NodeProfileUpdatePayload,
  ) => Promise<void>;
  profile?: NodeProfile | null;
}

export default function NodeProfileFormDialog({
  open,
  onClose,
  onSave,
  profile,
}: Props) {
  const { t } = useTranslation();
  const isEdit = !!profile;

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [isDefault, setIsDefault] = useState(false);
  const [configTexts, setConfigTexts] = useState<Record<ConfigKey, string>>(
    {} as Record<ConfigKey, string>,
  );
  const [configErrors, setConfigErrors] = useState<
    Partial<Record<ConfigKey, string>>
  >({});
  const [saving, setSaving] = useState(false);
  const [activeTab, setActiveTab] = useState(0);

  useEffect(() => {
    if (!open) return;
    if (profile) {
      setName(profile.name);
      setDescription(profile.description ?? "");
      setIsDefault(profile.is_default);
      const texts = {} as Record<ConfigKey, string>;
      for (const sec of CONFIG_SECTIONS) {
        const raw = profile[sec.key] as Record<string, unknown>;
        texts[sec.key] =
          raw && Object.keys(raw).length > 0
            ? JSON.stringify(raw, null, 2)
            : "{}";
      }
      setConfigTexts(texts);
    } else {
      setName("");
      setDescription("");
      setIsDefault(false);
      const texts = {} as Record<ConfigKey, string>;
      for (const sec of CONFIG_SECTIONS) texts[sec.key] = "{}";
      setConfigTexts(texts);
    }
    setConfigErrors({});
    setActiveTab(0);
  }, [open, profile]);

  function validate(): boolean {
    const errors: Partial<Record<ConfigKey, string>> = {};
    for (const sec of CONFIG_SECTIONS) {
      try {
        JSON.parse(configTexts[sec.key] || "{}");
      } catch {
        errors[sec.key] = "Invalid JSON";
      }
    }
    setConfigErrors(errors);
    return Object.keys(errors).length === 0 && name.trim().length > 0;
  }

  async function handleSubmit() {
    if (!validate()) return;
    setSaving(true);
    try {
      const configs: Partial<Record<ConfigKey, Record<string, unknown>>> = {};
      for (const sec of CONFIG_SECTIONS) {
        configs[sec.key] = JSON.parse(configTexts[sec.key] || "{}");
      }
      await onSave({
        name: name.trim(),
        description: description.trim() || null,
        is_default: isDefault,
        ...configs,
      });
    } finally {
      setSaving(false);
    }
  }

  const currentSection = CONFIG_SECTIONS[activeTab];

  return (
    <FormDialog
      open={open}
      title={
        isEdit ? t("node_profiles.edit_title") : t("node_profiles.create_title")
      }
      loading={saving}
      submitLabel={isEdit ? t("common.save") : t("common.create")}
      submitDisabled={!name.trim()}
      onSubmit={() => void handleSubmit()}
      onClose={onClose}
      maxWidth="md"
    >
      <Stack spacing={2} sx={{ mt: 1 }}>
        <TextField
          label={t("node_profiles.name")}
          value={name}
          onChange={(e) => setName(e.target.value)}
          fullWidth
          required
          autoFocus
        />
        <TextField
          label={t("node_profiles.description")}
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          fullWidth
          multiline
          minRows={2}
        />
        <FormControlLabel
          control={
            <Checkbox
              checked={isDefault}
              onChange={(e) => setIsDefault(e.target.checked)}
            />
          }
          label={t("node_profiles.is_default")}
        />

        <Typography variant="subtitle2" sx={{ mt: 1 }}>
          {t("node_profiles.configuration")}
        </Typography>
        <Tabs
          value={activeTab}
          onChange={(_, v) => setActiveTab(v as number)}
          variant="scrollable"
          scrollButtons="auto"
        >
          {CONFIG_SECTIONS.map((sec) => (
            <Tab
              key={sec.key}
              label={sec.label}
              sx={{
                color: configErrors[sec.key] ? "error.main" : undefined,
              }}
            />
          ))}
        </Tabs>
        {currentSection && (
          <JsonConfigField
            label={currentSection.label}
            value={configTexts[currentSection.key] ?? "{}"}
            onChange={(v) =>
              setConfigTexts((prev) => ({ ...prev, [currentSection.key]: v }))
            }
            error={configErrors[currentSection.key]}
          />
        )}
      </Stack>
    </FormDialog>
  );
}
