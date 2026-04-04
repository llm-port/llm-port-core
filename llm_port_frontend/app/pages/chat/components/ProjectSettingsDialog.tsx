/**
 * ProjectSettingsDialog — edit project name, description, and system prompt.
 */
import { useEffect, useState } from "react";
import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import { useTranslation } from "react-i18next";

import type { ChatProject } from "~/api/chatTypes";

interface Props {
  open: boolean;
  project: ChatProject | null;
  onClose: () => void;
  onSave: (
    id: string,
    updates: {
      name?: string;
      description?: string;
      system_instructions?: string;
    },
  ) => Promise<void>;
}

export default function ProjectSettingsDialog({
  open,
  project,
  onClose,
  onSave,
}: Props) {
  const { t } = useTranslation();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (project) {
      setName(project.name);
      setDescription(project.description ?? "");
      setSystemPrompt(project.system_instructions ?? "");
    }
  }, [project]);

  const handleSave = async () => {
    if (!project) return;
    setSaving(true);
    try {
      await onSave(project.id, {
        name: name.trim(),
        description: description.trim() || undefined,
        system_instructions: systemPrompt.trim() || undefined,
      });
      onClose();
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>
        {t("chat.project_settings", { defaultValue: "Project Settings" })}
      </DialogTitle>
      <DialogContent
        sx={{ display: "flex", flexDirection: "column", gap: 2, pt: 1 }}
      >
        <TextField
          label={t("chat.project_name", { defaultValue: "Project name" })}
          value={name}
          onChange={(e) => setName(e.target.value)}
          size="small"
          fullWidth
        />
        <TextField
          label={t("chat.project_description", { defaultValue: "Description" })}
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          size="small"
          fullWidth
        />
        <Typography variant="caption" color="text.secondary" sx={{ mt: 1 }}>
          {t("chat.system_prompt_hint", {
            defaultValue:
              "System prompt is prepended to every message in this project. Use it to define the project context, persona, or constraints.",
          })}
        </Typography>
        <TextField
          label={t("chat.system_prompt", { defaultValue: "System prompt" })}
          value={systemPrompt}
          onChange={(e) => setSystemPrompt(e.target.value)}
          multiline
          minRows={4}
          maxRows={12}
          fullWidth
          placeholder={t("chat.system_prompt_placeholder", {
            defaultValue: "You are a helpful assistant that specialises in…",
          })}
        />
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={saving}>
          {t("common.cancel", { defaultValue: "Cancel" })}
        </Button>
        <Button
          variant="contained"
          onClick={handleSave}
          disabled={saving || !name.trim()}
        >
          {t("common.save", { defaultValue: "Save" })}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
