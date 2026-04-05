/**
 * HelpWizardDialog — multi-step info slides that introduce key features.
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";

import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import MobileStepper from "@mui/material/MobileStepper";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";

import KeyboardArrowLeftIcon from "@mui/icons-material/KeyboardArrowLeft";
import KeyboardArrowRightIcon from "@mui/icons-material/KeyboardArrowRight";

export interface HelpWizardDialogProps {
  open: boolean;
  onClose: () => void;
}

export function HelpWizardDialog({ open, onClose }: HelpWizardDialogProps) {
  const { t } = useTranslation();
  const [step, setStep] = useState(0);

  const slides = [
    {
      title: t("help.slides.welcome.title"),
      description: t("help.slides.welcome.description"),
      image: "/help-welcome.png",
    },
    {
      title: t("help.slides.chat.title"),
      description: t("help.slides.chat.description"),
      image: "/help-chat.png",
    },
    {
      title: t("help.slides.llm_server.title"),
      description: t("help.slides.llm_server.description"),
      image: "/help-llm-server.png",
    },
    {
      title: t("help.slides.endpoint.title"),
      description: t("help.slides.endpoint.description"),
      image: "/help-api-endpoint.png",
    },
    {
      title: t("help.slides.rag.title"),
      description: t("help.slides.rag.description"),
      image: "/help-rag.png",
    },
    {
      title: t("help.slides.mcp.title"),
      description: t("help.slides.mcp.description"),
      image: "/help-mcp.png",
    },
    {
      title: t("help.slides.skills.title"),
      description: t("help.slides.skills.description"),
      image: "/help-skills.png",
    },
    {
      title: t("help.slides.observability.title"),
      description: t("help.slides.observability.description"),
      image: "/help-observability.png",
    },
  ];

  function handleClose() {
    setStep(0);
    onClose();
  }

  return (
    <Dialog open={open} onClose={handleClose} maxWidth="md" fullWidth>
      <DialogTitle>{t("help.title")}</DialogTitle>
      <DialogContent sx={{ pt: "8px !important" }}>
        <Stack spacing={1.5}>
          <Typography variant="subtitle1" fontWeight={600}>
            {slides[step]?.title}
          </Typography>
          <Typography variant="body2" color="text.secondary">
            {slides[step]?.description}
          </Typography>
          <Box
            sx={{
              width: "100%",
              borderRadius: 2,
              overflow: "hidden",
              border: (theme) => `1px solid ${theme.palette.divider}`,
              bgcolor: "background.default",
            }}
          >
            <Box
              component="img"
              src={slides[step]?.image}
              alt={slides[step]?.title}
              sx={{
                width: "100%",
                maxHeight: 360,
                objectFit: "cover",
                display: "block",
              }}
            />
          </Box>
        </Stack>
      </DialogContent>
      <DialogActions sx={{ px: 2, pb: 1.5, justifyContent: "space-between" }}>
        <Button onClick={handleClose}>{t("common.close")}</Button>
        <MobileStepper
          variant="dots"
          position="static"
          steps={slides.length}
          activeStep={step}
          nextButton={
            <Button
              size="small"
              onClick={() =>
                setStep((prev) => Math.min(slides.length - 1, prev + 1))
              }
              disabled={step >= slides.length - 1}
            >
              {t("help.next")}
              <KeyboardArrowRightIcon fontSize="small" />
            </Button>
          }
          backButton={
            <Button
              size="small"
              onClick={() => setStep((prev) => Math.max(0, prev - 1))}
              disabled={step === 0}
            >
              <KeyboardArrowLeftIcon fontSize="small" />
              {t("help.back")}
            </Button>
          }
        />
      </DialogActions>
    </Dialog>
  );
}
