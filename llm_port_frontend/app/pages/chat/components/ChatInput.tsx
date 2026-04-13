/**
 * ChatInput — auto-resizing textarea with file attach & send button.
 */
import {
  type KeyboardEvent,
  type ChangeEvent,
  useCallback,
  useRef,
  useState,
} from "react";
import Box from "@mui/material/Box";
import IconButton from "@mui/material/IconButton";
import InputBase from "@mui/material/InputBase";
import Tooltip from "@mui/material/Tooltip";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import { useTheme } from "@mui/material/styles";
import SendIcon from "@mui/icons-material/Send";
import AttachFileIcon from "@mui/icons-material/AttachFile";
import StopIcon from "@mui/icons-material/Stop";
import BuildIcon from "@mui/icons-material/Build";
import { useTranslation } from "react-i18next";

import ModelSelector from "./ModelSelector";

interface Props {
  onSend: (text: string, files: File[]) => void;
  onStop?: () => void;
  selectedModel: string;
  onModelChange: (alias: string) => void;
  disabled?: boolean;
  streaming?: boolean;
  placeholder?: string;
  autoFocus?: boolean;
  onToolsToggle?: () => void;
  toolsOpen?: boolean;
}

export default function ChatInput({
  onSend,
  onStop,
  selectedModel,
  onModelChange,
  disabled = false,
  streaming = false,
  placeholder,
  autoFocus = true,
  onToolsToggle,
  toolsOpen = false,
}: Props) {
  const { t } = useTranslation();
  const theme = useTheme();
  const [text, setText] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const canSend = text.trim().length > 0 && !disabled && !streaming;

  const handleSend = useCallback(() => {
    if (!canSend) return;
    onSend(text.trim(), files);
    setText("");
    setFiles([]);
    // Re-focus input after send
    setTimeout(() => inputRef.current?.focus(), 0);
  }, [canSend, onSend, text, files]);

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleFileSelect = (e: ChangeEvent<HTMLInputElement>) => {
    const selected = e.target.files;
    if (selected) {
      setFiles((prev) => [...prev, ...Array.from(selected)]);
    }
    // Reset so same file can be selected again
    e.target.value = "";
  };

  const removeFile = (index: number) => {
    setFiles((prev) => prev.filter((_, i) => i !== index));
  };

  return (
    <Box
      sx={{
        px: 2,
        pb: 2,
        pt: 1,
        display: "flex",
        flexDirection: "column",
        gap: 1,
        maxWidth: 800,
        mx: "auto",
        width: "100%",
      }}
    >
      {/* File chips */}
      {files.length > 0 && (
        <Box sx={{ display: "flex", flexWrap: "wrap", gap: 0.5 }}>
          {files.map((f, i) => (
            <Chip
              key={`${f.name}-${i}`}
              label={f.name}
              size="small"
              onDelete={() => removeFile(i)}
            />
          ))}
        </Box>
      )}

      {/* Input bar */}
      <Box
        sx={{
          display: "flex",
          alignItems: "flex-end",
          gap: 1,
          bgcolor: theme.palette.background.paper,
          border: `1px solid ${theme.palette.divider}`,
          borderRadius: 3,
          px: 1.5,
          py: 0.75,
        }}
      >
        {/* Tools button */}
        {onToolsToggle && (
          <Tooltip title={t("tools.panel.title", { defaultValue: "Tools" })}>
            <IconButton
              size="small"
              onClick={onToolsToggle}
              color={toolsOpen ? "primary" : "default"}
            >
              <BuildIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        )}

        {/* Attach button */}
        <Tooltip title={t("chat.attach_file", { defaultValue: "Attach file" })}>
          <IconButton
            size="small"
            onClick={() => fileInputRef.current?.click()}
            disabled={disabled || streaming}
          >
            <AttachFileIcon fontSize="small" />
          </IconButton>
        </Tooltip>
        <input
          ref={fileInputRef}
          type="file"
          hidden
          multiple
          onChange={handleFileSelect}
        />

        {/* Textarea */}
        <InputBase
          inputRef={inputRef}
          multiline
          maxRows={8}
          fullWidth
          placeholder={
            placeholder ??
            t("input_placeholder", {
              ns: "chat",
              defaultValue: "Type a message…",
            })
          }
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={disabled}
          autoFocus={autoFocus}
          sx={{ fontSize: "0.95rem", py: 0.5 }}
        />

        {/* Model selector */}
        <ModelSelector
          value={selectedModel}
          onChange={onModelChange}
          size="small"
        />

        {/* Send / Stop button */}
        {streaming ? (
          <Tooltip title={t("chat.stop", { defaultValue: "Stop" })}>
            <IconButton size="small" color="error" onClick={onStop}>
              <StopIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        ) : (
          <Tooltip title={t("chat.send", { defaultValue: "Send" })}>
            <span>
              <IconButton
                size="small"
                color="primary"
                onClick={handleSend}
                disabled={!canSend}
              >
                {disabled ? (
                  <CircularProgress size={18} />
                ) : (
                  <SendIcon fontSize="small" />
                )}
              </IconButton>
            </span>
          </Tooltip>
        )}
      </Box>
    </Box>
  );
}
