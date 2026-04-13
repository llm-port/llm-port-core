/**
 * ChatWindow — active chat session with message history + streaming.
 */
import { useEffect, useRef, useState } from "react";
import Box from "@mui/material/Box";
import IconButton from "@mui/material/IconButton";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import Button from "@mui/material/Button";
import CircularProgress from "@mui/material/CircularProgress";
import { useTheme } from "@mui/material/styles";
import MenuIcon from "@mui/icons-material/Menu";
import ReplayIcon from "@mui/icons-material/Replay";
import ErrorOutlineIcon from "@mui/icons-material/ErrorOutline";
import LightModeIcon from "@mui/icons-material/LightMode";
import DarkModeIcon from "@mui/icons-material/DarkMode";
import TranslateIcon from "@mui/icons-material/Translate";
import AdminPanelSettingsIcon from "@mui/icons-material/AdminPanelSettings";
import { useTranslation } from "react-i18next";
import { useNavigate, useLocation } from "react-router";

import Avatar from "@mui/material/Avatar";
import Menu from "@mui/material/Menu";
import MenuItem from "@mui/material/MenuItem";
import type { ChatSession, TokenUsage } from "~/api/chatTypes";
import type { UiLanguage } from "~/api/i18n";
import i18n from "~/i18n";
import type { InitialMessageState } from "./ChatWelcome";
import { useThemeMode } from "~/theme-mode";
import { useChatStream } from "../hooks/useChatStream";
import MessageBubble from "./MessageBubble";
import MessageDebugPanel from "./MessageDebugPanel";
import ChatInput from "./ChatInput";

interface Props {
  sessionId: string;
  selectedModel: string;
  onModelChange: (alias: string) => void;
  onSessionCreated: (session: ChatSession) => void;
  onSessionUpdated: (session: ChatSession) => void;
  onToggleSidebar: () => void;
  sidebarOpen: boolean;
  languages: UiLanguage[];
  language: string;
  onLanguageChange: (code: string) => void;
  isSuperuser: boolean;
  canDebug?: boolean;
  onToolsToggle: () => void;
  toolsOpen: boolean;
}

export default function ChatWindow({
  sessionId,
  selectedModel,
  onModelChange,
  onSessionCreated,
  onSessionUpdated,
  onToggleSidebar,
  sidebarOpen,
  languages,
  language,
  onLanguageChange,
  isSuperuser,
  canDebug = false,
  onToolsToggle,
  toolsOpen,
}: Props) {
  const { t } = useTranslation();
  const theme = useTheme();
  const navigate = useNavigate();
  const { mode, toggleMode } = useThemeMode();
  const [langMenuAnchor, setLangMenuAnchor] = useState<HTMLElement | null>(
    null,
  );
  const [debugTraceId, setDebugTraceId] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const location = useLocation();
  const initialSentRef = useRef(false);

  const {
    messages,
    streamingContent,
    streamingUsage,
    isStreaming,
    isLoading,
    error,
    getResponseMs,
    send,
    retry,
    stop,
    loadHistory,
  } = useChatStream({
    sessionId,
    selectedModel,
    onSessionCreated,
    onSessionUpdated,
  });

  // Load history OR auto-send initial message (mutually exclusive on mount).
  // A new session navigated from ChatWelcome carries an initialMessage in
  // location.state — send it immediately without loading history (there is
  // none yet). On page refresh the state is cleared so we fall through to
  // loadHistory which fetches persisted messages from the server.
  useEffect(() => {
    const state = location.state as InitialMessageState | undefined;
    if (state?.initialMessage && !initialSentRef.current) {
      initialSentRef.current = true;
      send(state.initialMessage, state.initialModel, state.initialFiles);
      // Clear navigation state so a page refresh won't re-send the message.
      // Preserve React Router's internal history keys while removing user state.
      const { usr: _usr, ...rest } = (window.history.state ?? {}) as Record<
        string,
        unknown
      >;
      window.history.replaceState(rest, "");
      return;
    }
    loadHistory();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  // Auto-scroll to bottom on new content
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, streamingContent]);

  const handleSend = (text: string, files: File[]) => {
    send(text, selectedModel, files);
  };

  // Parse token_estimate from message for display
  const usageFromMessage = (
    tokenEstimate: number | null,
    modelAlias: string | null,
  ): TokenUsage | null => {
    if (!tokenEstimate) return null;
    return {
      prompt_tokens: 0,
      completion_tokens: tokenEstimate,
      total_tokens: tokenEstimate,
    };
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
      {/* Top bar */}
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          px: 1.5,
          py: 1,
          minHeight: 56,
          borderBottom: `1px solid ${theme.palette.divider}`,
        }}
      >
        {!sidebarOpen && (
          <IconButton size="small" onClick={onToggleSidebar} sx={{ mr: 1 }}>
            <MenuIcon />
          </IconButton>
        )}
        <Typography variant="body1" fontWeight={600} noWrap sx={{ flex: 1 }}>
          {t("chat.conversation", { defaultValue: "Conversation" })}
        </Typography>
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

      {/* Messages area */}
      <Box
        sx={{
          flex: 1,
          overflowY: "auto",
          overflowX: "hidden",
          py: 2,
          display: "flex",
          flexDirection: "column",
        }}
      >
        <Box sx={{ maxWidth: 800, mx: "auto", width: "100%", flex: 1 }}>
          {isLoading && (
            <Box
              sx={{
                display: "flex",
                justifyContent: "center",
                py: 4,
              }}
            >
              <CircularProgress size={28} />
            </Box>
          )}

          {/* Rendered messages */}
          {messages.map((msg) => (
            <MessageBubble
              key={msg.id}
              role={msg.role}
              content={msg.content}
              modelAlias={msg.model_alias}
              usage={
                msg.role === "assistant"
                  ? usageFromMessage(msg.token_estimate, msg.model_alias)
                  : null
              }
              responseMs={
                msg.role === "assistant" ? getResponseMs(msg.id) : null
              }
              traceId={msg.trace_id}
              canDebug={canDebug}
              onDebugClick={setDebugTraceId}
            />
          ))}

          {/* Streaming assistant message */}
          {isStreaming && streamingContent && (
            <MessageBubble
              role="assistant"
              content={streamingContent}
              modelAlias={selectedModel || null}
              usage={streamingUsage}
              streaming
            />
          )}

          {/* Error bubble — shown after messages so it appears at the bottom */}
          {error && (
            <Box
              sx={{
                display: "flex",
                flexDirection: "row",
                alignItems: "flex-start",
                gap: 1,
                mb: 1.5,
                px: 1,
              }}
            >
              <Avatar
                sx={{
                  width: 32,
                  height: 32,
                  mt: 0.5,
                  bgcolor: theme.palette.error.dark,
                }}
              >
                <ErrorOutlineIcon sx={{ fontSize: 18 }} />
              </Avatar>
              <Box
                sx={{
                  maxWidth: "75%",
                  minWidth: 60,
                  px: 2,
                  py: 1.5,
                  borderRadius: "4px 12px 12px 12px",
                  bgcolor:
                    theme.palette.mode === "dark"
                      ? theme.palette.background.paper
                      : theme.palette.grey[100],
                  border: `1px solid ${theme.palette.error.main}`,
                  color: theme.palette.text.primary,
                }}
              >
                <Typography
                  variant="caption"
                  sx={{
                    color: "error.main",
                    fontWeight: 600,
                    mb: 0.5,
                    display: "block",
                  }}
                >
                  {t("error_label", { ns: "chat", defaultValue: "Error" })}
                </Typography>
                <Typography
                  variant="body2"
                  sx={{ mb: 1, wordBreak: "break-word" }}
                >
                  {error}
                </Typography>
                <Box sx={{ display: "flex", justifyContent: "flex-end" }}>
                  <Button
                    size="small"
                    variant="outlined"
                    color="error"
                    startIcon={<ReplayIcon />}
                    onClick={retry}
                    sx={{ textTransform: "none", borderRadius: 2 }}
                  >
                    {t("retry", { ns: "chat", defaultValue: "Retry" })}
                  </Button>
                </Box>
              </Box>
            </Box>
          )}

          {/* Streaming indicator when waiting for first token */}
          {isStreaming && !streamingContent && (
            <Box
              sx={{
                display: "flex",
                justifyContent: "flex-start",
                px: 1,
                mb: 1.5,
              }}
            >
              <Box
                sx={{
                  px: 2,
                  py: 1.5,
                  borderRadius: "4px 12px 12px 12px",
                  bgcolor:
                    theme.palette.mode === "dark"
                      ? theme.palette.background.paper
                      : theme.palette.grey[100],
                  border: `1px solid ${theme.palette.divider}`,
                }}
              >
                <CircularProgress size={16} />
              </Box>
            </Box>
          )}

          <div ref={bottomRef} />
        </Box>
      </Box>

      {/* Input */}
      <ChatInput
        onSend={handleSend}
        onStop={stop}
        selectedModel={selectedModel}
        onModelChange={onModelChange}
        streaming={isStreaming}
        onToolsToggle={onToolsToggle}
        toolsOpen={toolsOpen}
      />

      {/* Debug panel — only rendered when RBAC allows */}
      {canDebug && debugTraceId && (
        <MessageDebugPanel
          traceId={debugTraceId}
          open
          onClose={() => setDebugTraceId(null)}
        />
      )}
    </Box>
  );
}
