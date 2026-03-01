/**
 * AdminTopbar — top app bar for the admin layout.
 *
 * Contains help button, language selector, theme toggle,
 * root-mode controls, and user/logout chip.
 */
import { useTranslation } from "react-i18next";
import type { RootModeStatus } from "~/api/admin";
import type { UiLanguage } from "~/api/i18n";
import i18n from "~/i18n";

import AppBar from "@mui/material/AppBar";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import IconButton from "@mui/material/IconButton";
import Menu from "@mui/material/Menu";
import MenuItem from "@mui/material/MenuItem";
import Toolbar from "@mui/material/Toolbar";
import Tooltip from "@mui/material/Tooltip";

import DarkModeOutlinedIcon from "@mui/icons-material/DarkModeOutlined";
import HelpOutlineIcon from "@mui/icons-material/HelpOutline";
import LightModeOutlinedIcon from "@mui/icons-material/LightModeOutlined";
import LogoutIcon from "@mui/icons-material/Logout";
import SecurityIcon from "@mui/icons-material/Security";
import TranslateIcon from "@mui/icons-material/Translate";

export interface AdminTopbarProps {
  mode: "light" | "dark";
  toggleMode: () => void;
  currentUserEmail: string;
  isSuperuser: boolean;
  rootStatus: RootModeStatus | null;
  languages: UiLanguage[];
  language: string;
  languageMenuAnchor: HTMLElement | null;
  onLanguageMenuOpen: (event: React.MouseEvent<HTMLElement>) => void;
  onLanguageMenuClose: () => void;
  onLanguageChange: (code: string) => void;
  onHelpOpen: () => void;
  onRootFormOpen: () => void;
  onRootDeactivate: () => void;
  onLogout: () => void;
}

export function AdminTopbar({
  mode,
  toggleMode,
  currentUserEmail,
  isSuperuser,
  rootStatus,
  languages,
  language,
  languageMenuAnchor,
  onLanguageMenuOpen,
  onLanguageMenuClose,
  onLanguageChange,
  onHelpOpen,
  onRootFormOpen,
  onRootDeactivate,
  onLogout,
}: AdminTopbarProps) {
  const { t } = useTranslation();
  const isRootActive = rootStatus?.active ?? false;

  return (
    <AppBar position="static" elevation={0}>
      <Toolbar variant="dense" sx={{ justifyContent: "flex-end", gap: 1.5 }}>
        <Tooltip title={t("help.title")} arrow>
          <IconButton
            size="small"
            onClick={onHelpOpen}
            sx={{ color: "text.primary" }}
          >
            <HelpOutlineIcon />
          </IconButton>
        </Tooltip>
        <Tooltip title={t("language.label")} arrow>
          <IconButton
            size="small"
            onClick={onLanguageMenuOpen}
            sx={{ color: "text.primary" }}
          >
            <TranslateIcon />
          </IconButton>
        </Tooltip>
        <Menu
          anchorEl={languageMenuAnchor}
          open={Boolean(languageMenuAnchor)}
          onClose={onLanguageMenuClose}
          anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
          transformOrigin={{ vertical: "top", horizontal: "right" }}
        >
          {languages.map((lang) => (
            <MenuItem
              key={lang.code}
              selected={language === lang.code}
              onClick={() => {
                onLanguageChange(lang.code);
                void i18n
                  .reloadResources([lang.code], ["common"])
                  .then(() => i18n.changeLanguage(lang.code));
                onLanguageMenuClose();
              }}
            >
              {lang.name}
            </MenuItem>
          ))}
        </Menu>
        <Tooltip title={mode === "dark" ? t("theme.light") : t("theme.dark")} arrow>
          <IconButton
            size="small"
            onClick={toggleMode}
            sx={{ color: "text.primary" }}
          >
            {mode === "dark" ? <LightModeOutlinedIcon /> : <DarkModeOutlinedIcon />}
          </IconButton>
        </Tooltip>
        {isSuperuser && (
          <>
            {isRootActive ? (
              <>
                <Chip
                  icon={<SecurityIcon />}
                  label={t("root_mode.active")}
                  color="error"
                  size="small"
                  variant="filled"
                  sx={{ fontWeight: 700 }}
                />
                <Button size="small" color="error" variant="outlined" onClick={onRootDeactivate}>
                  {t("root_mode.deactivate")}
                </Button>
              </>
            ) : (
              <Tooltip title={t("root_mode.activate")} arrow>
                <IconButton
                  size="small"
                  color="warning"
                  onClick={onRootFormOpen}
                  sx={{
                    width: 30,
                    height: 30,
                    border: (theme) => `1px solid ${theme.palette.warning.main}`,
                    borderRadius: "50%",
                  }}
                >
                  <SecurityIcon fontSize="small" />
                </IconButton>
              </Tooltip>
            )}
          </>
        )}
        <Chip
          label={currentUserEmail}
          onDelete={onLogout}
          deleteIcon={<LogoutIcon fontSize="small" />}
          variant="outlined"
          sx={{
            height: 30,
            "& .MuiChip-label": { px: 1.25 },
            "& .MuiChip-deleteIcon": { fontSize: 18, mr: 0.5 },
          }}
        />
      </Toolbar>
    </AppBar>
  );
}
