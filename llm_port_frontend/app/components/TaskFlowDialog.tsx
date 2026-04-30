/**
 * TaskFlowDialog — modal that lists available task-flow sub-process guides.
 *
 * Shown from the Help menu "How-To Guides" item. Each card shows a guide
 * title, description, and a "Start" button that launches the TaskFlowTour.
 */
import { useMemo } from "react";
import { useTranslation } from "react-i18next";

import Button from "@mui/material/Button";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import CardActions from "@mui/material/CardActions";
import Dialog from "@mui/material/Dialog";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import IconButton from "@mui/material/IconButton";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import CloseIcon from "@mui/icons-material/Close";

import CloudIcon from "@mui/icons-material/Cloud";
import DnsIcon from "@mui/icons-material/Dns";
import MemoryIcon from "@mui/icons-material/Memory";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import SchoolIcon from "@mui/icons-material/School";

import { useServices } from "~/lib/ServicesContext";
import {
  resolveEligibleTaskFlows,
  type TaskFlowCatalog,
} from "~/lib/tourEngine";

// ── Icon mapping ─────────────────────────────────────────────────────

const ICON_MAP: Record<string, React.ReactNode> = {
  Cloud: <CloudIcon color="primary" />,
  Dns: <DnsIcon color="primary" />,
  Memory: <MemoryIcon color="primary" />,
};

function flowIcon(iconName: string): React.ReactNode {
  return ICON_MAP[iconName] ?? <SchoolIcon color="primary" />;
}

// ── Props ────────────────────────────────────────────────────────────

export interface TaskFlowDialogProps {
  open: boolean;
  isSuperuser: boolean;
  onClose: () => void;
  onStart: (flowId: string) => void;
}

// ── Component ────────────────────────────────────────────────────────

export function TaskFlowDialog({
  open,
  isSuperuser,
  onClose,
  onStart,
}: TaskFlowDialogProps) {
  const { t } = useTranslation();
  const { isModuleEnabled } = useServices();

  const flows: TaskFlowCatalog[] = useMemo(
    () => resolveEligibleTaskFlows({ isModuleEnabled, isSuperuser }),
    [isModuleEnabled, isSuperuser],
  );

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        <Typography variant="h6" component="span">
          {t("help.how_to_guides", {
            ns: "tour",
            defaultValue: "How-To Guides",
          })}
        </Typography>
        <IconButton size="small" onClick={onClose} aria-label="close">
          <CloseIcon fontSize="small" />
        </IconButton>
      </DialogTitle>
      <DialogContent>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          {t("help.how_to_guides_subtitle", {
            ns: "tour",
            defaultValue:
              "Step-by-step walkthroughs for common tasks. Each guide highlights the relevant controls and walks you through the process.",
          })}
        </Typography>
        <Stack spacing={1.5}>
          {flows.map((flow) => (
            <Card key={flow.flowId} variant="outlined">
              <CardContent sx={{ pb: 0.5 }}>
                <Stack direction="row" spacing={1.5} alignItems="flex-start">
                  {flowIcon(flow.icon)}
                  <Stack sx={{ flex: 1 }}>
                    <Typography variant="subtitle2" sx={{ fontWeight: 600 }}>
                      {t(flow.titleKey, {
                        ns: "tour",
                        defaultValue: flow.titleKey,
                      })}
                    </Typography>
                    <Typography variant="body2" color="text.secondary">
                      {t(flow.descriptionKey, {
                        ns: "tour",
                        defaultValue: flow.descriptionKey,
                      })}
                    </Typography>
                  </Stack>
                </Stack>
              </CardContent>
              <CardActions sx={{ justifyContent: "flex-end", pt: 0 }}>
                <Button
                  size="small"
                  variant="contained"
                  startIcon={<PlayArrowIcon />}
                  onClick={() => {
                    onClose();
                    onStart(flow.flowId);
                  }}
                >
                  {t("help.start_guide", {
                    ns: "tour",
                    defaultValue: "Start",
                  })}
                </Button>
              </CardActions>
            </Card>
          ))}
          {flows.length === 0 && (
            <Typography
              variant="body2"
              color="text.secondary"
              sx={{ textAlign: "center", py: 2 }}
            >
              {t("help.no_guides_available", {
                ns: "tour",
                defaultValue: "No guides available for the current setup.",
              })}
            </Typography>
          )}
        </Stack>
      </DialogContent>
    </Dialog>
  );
}
