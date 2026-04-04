/**
 * SkillFormFields — modular, reusable skill form used in both
 * the Create-Skill wizard and the Edit (detail) page.
 *
 * Renders a two-step layout by default:
 *   Step 0 → "Basics" (name, scope, description, priority, tags)
 *   Step 1 → "Content" (markdown editor + preview, tools, knowledge)
 *
 * The parent controls the step and field values.
 */
import { useMemo } from "react";
import { useTranslation } from "react-i18next";

import Box from "@mui/material/Box";
import MenuItem from "@mui/material/MenuItem";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import type { SkillScope } from "~/api/skills";

// ── Value bag ──────────────────────────────────────────────────────────────

export interface SkillFormValues {
  name: string;
  description: string;
  scope: SkillScope;
  priority: number;
  tags: string;
  bodyMarkdown: string;
  allowedTools: string;
  preferredTools: string;
  forbiddenTools: string;
  knowledgeSources: string;
  changeNote: string;
}

export const EMPTY_FORM: SkillFormValues = {
  name: "",
  description: "",
  scope: "global",
  priority: 50,
  tags: "",
  bodyMarkdown: "",
  allowedTools: "",
  preferredTools: "",
  forbiddenTools: "",
  knowledgeSources: "",
  changeNote: "",
};

// ── Scopes ─────────────────────────────────────────────────────────────────

const SCOPES: { value: SkillScope; label: string }[] = [
  { value: "global", label: "Global" },
  { value: "tenant", label: "Tenant" },
  { value: "workspace", label: "Workspace" },
  { value: "assistant", label: "Assistant" },
  { value: "user", label: "User" },
];

// ── Step 0 — Basics ────────────────────────────────────────────────────────

export function BasicsFields({
  values,
  onChange,
  disabled,
}: {
  values: SkillFormValues;
  onChange: <K extends keyof SkillFormValues>(
    key: K,
    val: SkillFormValues[K],
  ) => void;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  return (
    <Stack spacing={2}>
      <TextField
        label={t("skills.skill_name", "Skill Name")}
        value={values.name}
        onChange={(e) => onChange("name", e.target.value)}
        fullWidth
        required
        disabled={disabled}
      />
      <TextField
        label={t("skills.scope", "Scope")}
        select
        value={values.scope}
        onChange={(e) => onChange("scope", e.target.value as SkillScope)}
        fullWidth
        disabled={disabled}
      >
        {SCOPES.map((s) => (
          <MenuItem key={s.value} value={s.value}>
            {t(`skills.scope_${s.value}`, s.label)}
          </MenuItem>
        ))}
      </TextField>
      <TextField
        label={t("skills.description", "Description")}
        value={values.description}
        onChange={(e) => onChange("description", e.target.value)}
        fullWidth
        multiline
        rows={3}
        disabled={disabled}
      />
      <TextField
        label={t("skills.priority", "Priority")}
        type="number"
        value={values.priority}
        onChange={(e) => onChange("priority", Number(e.target.value))}
        fullWidth
        disabled={disabled}
        inputProps={{ min: 0, max: 100 }}
        helperText={t("skills.priority_hint", "0 = lowest, 100 = highest")}
      />
      <TextField
        label={t("skills.tags", "Tags")}
        value={values.tags}
        onChange={(e) => onChange("tags", e.target.value)}
        fullWidth
        disabled={disabled}
        helperText={t("skills.csv_hint", "Comma-separated values")}
      />
    </Stack>
  );
}

// ── Step 1 — Content & tools ───────────────────────────────────────────────

export function ContentFields({
  values,
  onChange,
  disabled,
  showChangeNote,
}: {
  values: SkillFormValues;
  onChange: <K extends keyof SkillFormValues>(
    key: K,
    val: SkillFormValues[K],
  ) => void;
  disabled?: boolean;
  showChangeNote?: boolean;
}) {
  const { t } = useTranslation();
  const preview = useMemo(() => values.bodyMarkdown, [values.bodyMarkdown]);

  return (
    <Stack spacing={2}>
      {/* Markdown editor + preview */}
      <Stack direction={{ xs: "column", md: "row" }} spacing={2}>
        <Paper
          variant="outlined"
          sx={{
            flex: 1,
            minHeight: 350,
            display: "flex",
            flexDirection: "column",
          }}
        >
          <Typography
            variant="subtitle2"
            sx={{ px: 2, py: 1, borderBottom: 1, borderColor: "divider" }}
          >
            {t("skills.markdown_editor", "Markdown Editor")}
          </Typography>
          <TextField
            multiline
            fullWidth
            value={values.bodyMarkdown}
            onChange={(e) => onChange("bodyMarkdown", e.target.value)}
            disabled={disabled}
            sx={{
              flex: 1,
              "& .MuiInputBase-root": {
                fontFamily: "monospace",
                fontSize: 14,
                alignItems: "flex-start",
              },
              "& .MuiOutlinedInput-notchedOutline": { border: "none" },
            }}
            inputProps={{
              style: { minHeight: 300, resize: "none" },
              spellCheck: false,
            }}
          />
        </Paper>
        <Paper
          variant="outlined"
          sx={{ flex: 1, minHeight: 350, overflow: "auto" }}
        >
          <Typography
            variant="subtitle2"
            sx={{ px: 2, py: 1, borderBottom: 1, borderColor: "divider" }}
          >
            {t("skills.preview", "Preview")}
          </Typography>
          <Box sx={{ px: 2, py: 1 }}>
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{preview}</ReactMarkdown>
          </Box>
        </Paper>
      </Stack>

      {/* Tool lists */}
      <TextField
        label={t("skills.tools_allowed", "Allowed Tools")}
        value={values.allowedTools}
        onChange={(e) => onChange("allowedTools", e.target.value)}
        fullWidth
        disabled={disabled}
        helperText={t(
          "skills.tools_hint",
          "Comma-separated tool names (e.g. mcp.brave_search)",
        )}
      />
      <TextField
        label={t("skills.tools_preferred", "Preferred Tools")}
        value={values.preferredTools}
        onChange={(e) => onChange("preferredTools", e.target.value)}
        fullWidth
        disabled={disabled}
        helperText={t("skills.csv_hint", "Comma-separated values")}
      />
      <TextField
        label={t("skills.tools_forbidden", "Forbidden Tools")}
        value={values.forbiddenTools}
        onChange={(e) => onChange("forbiddenTools", e.target.value)}
        fullWidth
        disabled={disabled}
        helperText={t("skills.csv_hint", "Comma-separated values")}
      />
      <TextField
        label={t("skills.knowledge_sources", "Knowledge Sources")}
        value={values.knowledgeSources}
        onChange={(e) => onChange("knowledgeSources", e.target.value)}
        fullWidth
        disabled={disabled}
        helperText={t("skills.csv_hint", "Comma-separated values")}
      />

      {showChangeNote && (
        <TextField
          label={t("skills.change_note", "Change Note")}
          value={values.changeNote}
          onChange={(e) => onChange("changeNote", e.target.value)}
          fullWidth
          size="small"
          disabled={disabled}
          placeholder={t(
            "skills.change_note_placeholder",
            "Describe what changed (optional)…",
          )}
        />
      )}
    </Stack>
  );
}
