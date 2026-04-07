/**
 * ProfileSelectionDialog — lets the user pick an onboarding profile
 * (Private, Team, Enterprise) on first login.
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";

import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Card from "@mui/material/Card";
import CardActionArea from "@mui/material/CardActionArea";
import CardContent from "@mui/material/CardContent";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import Typography from "@mui/material/Typography";

import HomeIcon from "@mui/icons-material/Home";
import GroupsIcon from "@mui/icons-material/Groups";
import BusinessIcon from "@mui/icons-material/Business";

export type OnboardingProfile = "private" | "team" | "enterprise";

interface ProfileOption {
  id: OnboardingProfile;
  icon: React.ReactNode;
  titleKey: string;
  titleDefault: string;
  descKey: string;
  descDefault: string;
}

const PROFILES: ProfileOption[] = [
  {
    id: "private",
    icon: <HomeIcon sx={{ fontSize: 40 }} />,
    titleKey: "profile_select.private.title",
    titleDefault: "Private",
    descKey: "profile_select.private.description",
    descDefault:
      "Run llm.port at home as a local consolidated gateway. Quick provider setup, chat, and optional RAG/PII.",
  },
  {
    id: "team",
    icon: <GroupsIcon sx={{ fontSize: 40 }} />,
    titleKey: "profile_select.team.title",
    titleDefault: "Team",
    descKey: "profile_select.team.description",
    descDefault:
      "Mixed topology with local LLM servers and remote endpoints. Node fleet visibility, shared guardrails, and scheduler.",
  },
  {
    id: "enterprise",
    icon: <BusinessIcon sx={{ fontSize: 40 }} />,
    titleKey: "profile_select.enterprise.title",
    titleDefault: "Enterprise",
    descKey: "profile_select.enterprise.description",
    descDefault:
      "Governed control plane for broad LLM systems. Access control, PII policy, observability, audit, and extension governance.",
  },
];

export interface ProfileSelectionDialogProps {
  open: boolean;
  onSelect: (profile: OnboardingProfile) => void;
  onSkip: () => void;
}

export function ProfileSelectionDialog({
  open,
  onSelect,
  onSkip,
}: ProfileSelectionDialogProps) {
  const { t } = useTranslation();
  const [selected, setSelected] = useState<OnboardingProfile | null>(null);

  function handleContinue() {
    if (selected) {
      onSelect(selected);
    }
  }

  return (
    <Dialog open={open} maxWidth="md" fullWidth>
      <DialogTitle>
        {t("profile_select.title", {
          defaultValue: "Choose your setup profile",
        })}
      </DialogTitle>
      <DialogContent sx={{ pt: "8px !important" }}>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          {t("profile_select.subtitle", {
            defaultValue:
              "This determines your recommended setup steps. You can change it later and all features remain available regardless of profile.",
          })}
        </Typography>
        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr 1fr" },
            gap: 2,
          }}
        >
          {PROFILES.map((p) => (
            <Card
              key={p.id}
              variant="outlined"
              sx={{
                borderColor:
                  selected === p.id ? "primary.main" : "divider",
                borderWidth: selected === p.id ? 2 : 1,
                transition: "border-color 0.15s",
              }}
            >
              <CardActionArea
                onClick={() => setSelected(p.id)}
                sx={{ height: "100%" }}
              >
                <CardContent
                  sx={{
                    display: "flex",
                    flexDirection: "column",
                    alignItems: "center",
                    textAlign: "center",
                    gap: 1,
                    py: 3,
                  }}
                >
                  <Box sx={{ color: selected === p.id ? "primary.main" : "text.secondary" }}>
                    {p.icon}
                  </Box>
                  <Typography variant="subtitle1" fontWeight={600}>
                    {t(p.titleKey, { defaultValue: p.titleDefault })}
                  </Typography>
                  <Typography variant="body2" color="text.secondary">
                    {t(p.descKey, { defaultValue: p.descDefault })}
                  </Typography>
                </CardContent>
              </CardActionArea>
            </Card>
          ))}
        </Box>
      </DialogContent>
      <DialogActions sx={{ px: 2, pb: 1.5, justifyContent: "space-between" }}>
        <Button onClick={onSkip}>
          {t("common.skip", { defaultValue: "Skip" })}
        </Button>
        <Button
          variant="contained"
          disabled={!selected}
          onClick={handleContinue}
        >
          {t("common.continue", { defaultValue: "Continue" })}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
