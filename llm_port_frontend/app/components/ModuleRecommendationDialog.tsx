/**
 * ModuleRecommendationDialog — shows recommended modules for the
 * selected onboarding profile, letting the user toggle and apply.
 */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Checkbox from "@mui/material/Checkbox";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import FormControlLabel from "@mui/material/FormControlLabel";
import Typography from "@mui/material/Typography";
import CircularProgress from "@mui/material/CircularProgress";

import type { OnboardingProfile } from "~/components/ProfileSelectionDialog";
import { useServices } from "~/lib/ServicesContext";

// ── Capability Matrix (from feature doc) ──────────────────────────────
// "default" = pre-checked, "optional" = unchecked, absent = hidden

type Recommendation = "default" | "optional";

const PROFILE_MODULES: Record<
  OnboardingProfile,
  Record<string, Recommendation>
> = {
  private: {
    pii: "default",
    rag_lite: "optional",
    mcp: "optional",
    skills: "optional",
    auth: "optional",
  },
  team: {
    pii: "default",
    rag_lite: "default",
    mcp: "optional",
    skills: "optional",
    auth: "optional",
  },
  enterprise: {
    pii: "default",
    rag_lite: "default",
    mcp: "default",
    skills: "default",
    auth: "default",
  },
};

const MODULE_LABELS: Record<string, { labelKey: string; defaultLabel: string }> = {
  pii: { labelKey: "module_recommend.module_pii", defaultLabel: "PII Guard" },
  rag_lite: { labelKey: "module_recommend.module_rag_lite", defaultLabel: "RAG Lite" },
  mcp: { labelKey: "module_recommend.module_mcp", defaultLabel: "MCP Registry" },
  skills: { labelKey: "module_recommend.module_skills", defaultLabel: "Skills Registry" },
  auth: { labelKey: "module_recommend.module_auth", defaultLabel: "Auth Providers (SSO)" },
};

export interface ModuleRecommendationDialogProps {
  open: boolean;
  profile: OnboardingProfile;
  onApply: (modules: Record<string, boolean>) => void;
  onSkip: () => void;
}

export function ModuleRecommendationDialog({
  open,
  profile,
  onApply,
  onSkip,
}: ModuleRecommendationDialogProps) {
  const { t } = useTranslation();
  const { isModuleEnabled } = useServices();
  const [applying, setApplying] = useState(false);

  const recommendations = PROFILE_MODULES[profile];
  const moduleNames = Object.keys(recommendations);

  // Initialize toggle state: default modules are checked, optional are unchecked,
  // but if the module is already enabled, keep it checked.
  const [toggles, setToggles] = useState<Record<string, boolean>>({});

  useEffect(() => {
    const initial: Record<string, boolean> = {};
    for (const name of moduleNames) {
      initial[name] =
        recommendations[name] === "default" || isModuleEnabled(name);
    }
    setToggles(initial);
  }, [profile, open]);

  function handleToggle(name: string) {
    setToggles((prev) => ({ ...prev, [name]: !prev[name] }));
  }

  async function handleApply() {
    setApplying(true);
    try {
      await onApply(toggles);
    } finally {
      setApplying(false);
    }
  }

  return (
    <Dialog open={open} maxWidth="sm" fullWidth>
      <DialogTitle>
        {t("module_recommend.title", {
          defaultValue: "Recommended modules",
        })}
      </DialogTitle>
      <DialogContent sx={{ pt: "8px !important" }}>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          {t("module_recommend.subtitle", {
            defaultValue:
              "These modules are recommended for your profile. Enabling a module may start additional containers.",
          })}
        </Typography>
        <Box sx={{ display: "flex", flexDirection: "column", gap: 0.5 }}>
          {moduleNames.map((name) => {
            const meta = MODULE_LABELS[name];
            if (!meta) return null;
            return (
              <FormControlLabel
                key={name}
                control={
                  <Checkbox
                    checked={toggles[name] ?? false}
                    onChange={() => handleToggle(name)}
                    disabled={applying}
                  />
                }
                label={t(meta.labelKey, { defaultValue: meta.defaultLabel })}
              />
            );
          })}
        </Box>
      </DialogContent>
      <DialogActions sx={{ px: 2, pb: 1.5, justifyContent: "space-between" }}>
        <Button onClick={onSkip} disabled={applying}>
          {t("common.skip", { defaultValue: "Skip" })}
        </Button>
        <Button
          variant="contained"
          onClick={handleApply}
          disabled={applying}
          startIcon={applying ? <CircularProgress size={16} /> : undefined}
        >
          {t("module_recommend.apply", { defaultValue: "Apply" })}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
