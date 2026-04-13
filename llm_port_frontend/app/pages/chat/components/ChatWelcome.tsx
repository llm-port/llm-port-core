/**
 * ChatWelcome — centred welcome when no session is active.
 *
 * On first message, creates a session and navigates to it, passing the
 * initial message via React Router state so ChatWindow can send it.
 */
import { useState } from "react";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";
import IconButton from "@mui/material/IconButton";
import Chip from "@mui/material/Chip";
import Tooltip from "@mui/material/Tooltip";
import { useTheme } from "@mui/material/styles";
import MenuIcon from "@mui/icons-material/Menu";
import AutoAwesomeIcon from "@mui/icons-material/AutoAwesome";
import LightModeIcon from "@mui/icons-material/LightMode";
import DarkModeIcon from "@mui/icons-material/DarkMode";
import TranslateIcon from "@mui/icons-material/Translate";
import AdminPanelSettingsIcon from "@mui/icons-material/AdminPanelSettings";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";

import Menu from "@mui/material/Menu";
import MenuItem from "@mui/material/MenuItem";
import type { ChatSession } from "~/api/chatTypes";
import type { UiLanguage } from "~/api/i18n";
import i18n from "~/i18n";
import { chatApi } from "~/api/chatClient";
import { useThemeMode } from "~/theme-mode";
import ChatInput from "./ChatInput";

interface Props {
  selectedModel: string;
  onModelChange: (alias: string) => void;
  onSessionCreated: (
    session: ChatSession,
    initialState?: InitialMessageState,
  ) => void;
  onToggleSidebar: () => void;
  sidebarOpen: boolean;
  languages: UiLanguage[];
  language: string;
  onLanguageChange: (code: string) => void;
  isSuperuser: boolean;
  onToolsToggle?: () => void;
  toolsOpen?: boolean;
}

/** Navigation state passed to ChatWindow for first-message auto-send. */
export interface InitialMessageState {
  initialMessage: string;
  initialModel: string;
  initialFiles?: File[];
}

export default function ChatWelcome({
  selectedModel,
  onModelChange,
  onSessionCreated,
  onToggleSidebar,
  sidebarOpen,
  languages,
  language,
  onLanguageChange,
  isSuperuser,
  onToolsToggle,
  toolsOpen,
}: Props) {
  const { t } = useTranslation();
  const theme = useTheme();
  const navigate = useNavigate();
  const { mode, toggleMode } = useThemeMode();
  const [sending, setSending] = useState(false);
  const [langMenuAnchor, setLangMenuAnchor] = useState<HTMLElement | null>(
    null,
  );

  const suggestions = [
    t("suggestion_1", {
      ns: "chat",
      defaultValue: "Explain quantum computing in simple terms",
    }),
    t("suggestion_2", {
      ns: "chat",
      defaultValue: "Write a Python function to merge two sorted lists",
    }),
    t("suggestion_3", {
      ns: "chat",
      defaultValue: "Summarize the key points of a research paper",
    }),
    t("suggestion_4", {
      ns: "chat",
      defaultValue: "Help me brainstorm ideas for a startup",
    }),
  ];

  const handleSend = async (text: string, files: File[]) => {
    if (sending) return;
    setSending(true);
    try {
      const session = await chatApi.createSession({
        title: text.slice(0, 100),
      });
      onSessionCreated(session, {
        initialMessage: text,
        initialModel: selectedModel,
        initialFiles: files.length > 0 ? files : undefined,
      });
    } catch {
      setSending(false);
    }
  };

  const handleSuggestion = (text: string) => {
    handleSend(text, []);
  };

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        position: "relative",
      }}
    >
      {/* Top bar with sidebar toggle */}
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          px: 1.5,
          py: 1,
          minHeight: 56,
        }}
      >
        <Box>
          {!sidebarOpen && (
            <IconButton size="small" onClick={onToggleSidebar}>
              <MenuIcon />
            </IconButton>
          )}
        </Box>
        <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
          {isSuperuser && (
            <Tooltip title={t("nav.admin", { defaultValue: "Admin panel" })}>
              <IconButton size="small" onClick={() => navigate("/admin")}>
                <AdminPanelSettingsIcon fontSize="small" />
              </IconButton>
            </Tooltip>
          )}
          <Tooltip title={t("language.label", { defaultValue: "Language" })}>
            <IconButton
              size="small"
              onClick={(e) => setLangMenuAnchor(e.currentTarget)}
            >
              <TranslateIcon fontSize="small" />
            </IconButton>
          </Tooltip>
          <Menu
            anchorEl={langMenuAnchor}
            open={Boolean(langMenuAnchor)}
            onClose={() => setLangMenuAnchor(null)}
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
                    .reloadResources([lang.code], ["common", "chat"])
                    .then(() => i18n.changeLanguage(lang.code));
                  setLangMenuAnchor(null);
                }}
              >
                {lang.name}
              </MenuItem>
            ))}
          </Menu>
          <Tooltip
            title={
              mode === "dark"
                ? t("theme.light", {
                    ns: "common",
                    defaultValue: "Switch to light mode",
                  })
                : t("theme.dark", {
                    ns: "common",
                    defaultValue: "Switch to dark mode",
                  })
            }
          >
            <IconButton size="small" onClick={toggleMode}>
              {mode === "dark" ? (
                <LightModeIcon fontSize="small" />
              ) : (
                <DarkModeIcon fontSize="small" />
              )}
            </IconButton>
          </Tooltip>
        </Box>
      </Box>

      {/* Centred hero area */}
      <Box
        sx={{
          flex: 1,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          px: 3,
          pb: 10,
        }}
      >
        <AutoAwesomeIcon
          sx={{ fontSize: 48, color: theme.palette.primary.main, mb: 2 }}
        />
        <Typography variant="h4" fontWeight={700} gutterBottom>
          {t("welcome_title", {
            ns: "chat",
            defaultValue: "How can I help you?",
          })}
        </Typography>
        <Typography
          variant="body1"
          color="text.secondary"
          sx={{ mb: 4, maxWidth: 480, textAlign: "center" }}
        >
          {t("welcome_subtitle", {
            ns: "chat",
            defaultValue:
              "Start a conversation, upload a file, or try one of these suggestions.",
          })}
        </Typography>

        {/* Suggestion chips */}
        <Box
          sx={{
            display: "flex",
            flexWrap: "wrap",
            gap: 1,
            justifyContent: "center",
            maxWidth: 600,
          }}
        >
          {suggestions.map((s) => (
            <Chip
              key={s}
              label={s}
              variant="outlined"
              onClick={() => handleSuggestion(s)}
              sx={{
                cursor: "pointer",
                "&:hover": {
                  bgcolor: theme.palette.action.hover,
                },
              }}
            />
          ))}
        </Box>
      </Box>

      {/* Bottom-anchored input */}
      <ChatInput
        onSend={handleSend}
        selectedModel={selectedModel}
        onModelChange={onModelChange}
        streaming={sending}
        onToolsToggle={onToolsToggle}
        toolsOpen={toolsOpen}
      />
    </Box>
  );
}
