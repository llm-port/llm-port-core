/**
 * PageHelpDrawer — right-side drawer showing contextual help
 * for the current admin page. Triggered by F1 or the "About This Page"
 * menu item.
 */
import { useTranslation } from "react-i18next";
import { useLocation } from "react-router";

import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import Drawer from "@mui/material/Drawer";
import Divider from "@mui/material/Divider";
import List from "@mui/material/List";
import ListItem from "@mui/material/ListItem";
import ListItemIcon from "@mui/material/ListItemIcon";
import ListItemText from "@mui/material/ListItemText";
import Typography from "@mui/material/Typography";

import ArrowRightIcon from "@mui/icons-material/ArrowRight";
import InfoOutlinedIcon from "@mui/icons-material/InfoOutlined";
import KeyboardIcon from "@mui/icons-material/Keyboard";

import { resolvePageHelp } from "~/lib/pageHelp";

export interface PageHelpDrawerProps {
  open: boolean;
  onClose: () => void;
}

export function PageHelpDrawer({ open, onClose }: PageHelpDrawerProps) {
  const { t } = useTranslation();
  const location = useLocation();

  const entry = resolvePageHelp(location.pathname);

  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={onClose}
      slotProps={{
        paper: {
          sx: { width: 360, maxWidth: "90vw" },
        },
      }}
    >
      <Box sx={{ p: 2.5, height: "100%", display: "flex", flexDirection: "column" }}>
        {/* Header */}
        <Box sx={{ display: "flex", alignItems: "center", gap: 1, mb: 1 }}>
          <InfoOutlinedIcon color="primary" />
          <Typography variant="h6" sx={{ fontWeight: 600, flex: 1 }}>
            {entry
              ? t(entry.titleKey, { ns: "tour" })
              : t("page_help.unknown.title", {
                  ns: "tour",
                  defaultValue: "About This Page",
                })}
          </Typography>
        </Box>

        {/* Description */}
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          {entry
            ? t(entry.descriptionKey, { ns: "tour" })
            : t("page_help.unknown.description", {
                ns: "tour",
                defaultValue:
                  "No specific help is available for this page yet.",
              })}
        </Typography>

        {/* Actions */}
        {entry && entry.actionKeys.length > 0 && (
          <>
            <Divider sx={{ mb: 1 }} />
            <Typography
              variant="overline"
              color="text.secondary"
              sx={{ mb: 0.5 }}
            >
              {t("page_help.actions_heading", {
                ns: "tour",
                defaultValue: "What you can do here",
              })}
            </Typography>
            <List dense disablePadding>
              {entry.actionKeys.map((key) => (
                <ListItem key={key} disableGutters sx={{ py: 0.25 }}>
                  <ListItemIcon sx={{ minWidth: 28 }}>
                    <ArrowRightIcon fontSize="small" color="action" />
                  </ListItemIcon>
                  <ListItemText
                    primary={t(key, { ns: "tour" })}
                    primaryTypographyProps={{ variant: "body2" }}
                  />
                </ListItem>
              ))}
            </List>
          </>
        )}

        {/* Spacer */}
        <Box sx={{ flex: 1 }} />

        {/* Footer */}
        <Divider sx={{ mb: 1.5 }} />
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
          }}
        >
          <Chip
            icon={<KeyboardIcon />}
            label="F1"
            size="small"
            variant="outlined"
          />
          <Button variant="text" onClick={onClose}>
            {t("page_help.close", {
              ns: "tour",
              defaultValue: "Got it",
            })}
          </Button>
        </Box>
      </Box>
    </Drawer>
  );
}
