/**
 * CreateSkillPage — two-step wizard for creating a new skill.
 *   Step 1: Basics (name, scope, description, priority, tags)
 *   Step 2: Content & tools (markdown editor, tool lists)
 */
import { useCallback, useState } from "react";
import { useNavigate } from "react-router";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Paper from "@mui/material/Paper";
import Stack from "@mui/material/Stack";
import Step from "@mui/material/Step";
import StepLabel from "@mui/material/StepLabel";
import Stepper from "@mui/material/Stepper";
import Typography from "@mui/material/Typography";

import ArrowBackIcon from "@mui/icons-material/ArrowBack";

import { createSkill, type CreateSkillPayload } from "~/api/skills";
import {
  BasicsFields,
  ContentFields,
  EMPTY_FORM,
  type SkillFormValues,
} from "./SkillFormFields";

const STEPS = ["Basics", "Content & Tools"];

function splitCsv(s: string): string[] {
  return s
    .split(",")
    .map((v) => v.trim())
    .filter(Boolean);
}

export default function CreateSkillPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();

  const [step, setStep] = useState(0);
  const [values, setValues] = useState<SkillFormValues>({ ...EMPTY_FORM });
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const onChange = useCallback(
    <K extends keyof SkillFormValues>(key: K, val: SkillFormValues[K]) => {
      setValues((prev) => ({ ...prev, [key]: val }));
    },
    [],
  );

  const canAdvance = step === 0 && values.name.trim().length > 0;

  async function handleCreate() {
    setSaving(true);
    setError(null);
    try {
      const payload: CreateSkillPayload = {
        name: values.name,
        scope: values.scope,
        description: values.description || undefined,
        body_markdown:
          values.bodyMarkdown ||
          `# ${values.name}\n\nDescribe the skill instructions here.\n`,
        priority: values.priority,
        tags: splitCsv(values.tags).length ? splitCsv(values.tags) : undefined,
        allowed_tools: splitCsv(values.allowedTools).length
          ? splitCsv(values.allowedTools)
          : undefined,
        preferred_tools: splitCsv(values.preferredTools).length
          ? splitCsv(values.preferredTools)
          : undefined,
        forbidden_tools: splitCsv(values.forbiddenTools).length
          ? splitCsv(values.forbiddenTools)
          : undefined,
        knowledge_sources: splitCsv(values.knowledgeSources).length
          ? splitCsv(values.knowledgeSources)
          : undefined,
      };
      const result = await createSkill(payload);
      navigate(`/admin/skills/${result.id}`);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to create skill");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Box>
      {/* Header */}
      <Stack direction="row" alignItems="center" spacing={2} sx={{ mb: 3 }}>
        <Button
          startIcon={<ArrowBackIcon />}
          onClick={() => navigate("/admin/skills")}
          size="small"
        >
          {t("skills.back_to_list", "Skills")}
        </Button>
        <Typography variant="h5" sx={{ flexGrow: 1 }}>
          {t("skills.create", "Create Skill")}
        </Typography>
      </Stack>

      {error && (
        <Alert severity="error" sx={{ mb: 2 }} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}

      {/* Stepper */}
      <Stepper activeStep={step} sx={{ mb: 3 }}>
        {STEPS.map((label) => (
          <Step key={label}>
            <StepLabel>
              {t(
                `skills.step_${label.toLowerCase().replace(/ & /g, "_")}`,
                label,
              )}
            </StepLabel>
          </Step>
        ))}
      </Stepper>

      {/* Step content */}
      <Paper variant="outlined" sx={{ p: 3 }}>
        {step === 0 && (
          <BasicsFields values={values} onChange={onChange} disabled={saving} />
        )}
        {step === 1 && (
          <ContentFields
            values={values}
            onChange={onChange}
            disabled={saving}
          />
        )}
      </Paper>

      {/* Navigation buttons */}
      <Stack
        direction="row"
        spacing={2}
        sx={{ mt: 3 }}
        justifyContent="flex-end"
      >
        {step > 0 && (
          <Button onClick={() => setStep((s) => s - 1)} disabled={saving}>
            {t("common.back", "Back")}
          </Button>
        )}
        {step < STEPS.length - 1 && (
          <Button
            variant="contained"
            onClick={() => setStep((s) => s + 1)}
            disabled={!canAdvance}
          >
            {t("common.next", "Next")}
          </Button>
        )}
        {step === STEPS.length - 1 && (
          <Button
            variant="contained"
            onClick={handleCreate}
            disabled={saving || !values.name.trim()}
          >
            {saving
              ? t("common.creating", "Creating…")
              : t("skills.create", "Create Skill")}
          </Button>
        )}
      </Stack>
    </Box>
  );
}
