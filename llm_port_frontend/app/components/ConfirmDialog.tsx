/**
 * ConfirmDialog — reusable confirmation dialog (e.g. delete actions).
 *
 * Replaces 5+ copy-pasted delete-confirm dialogs across admin pages.
 *
 * Usage:
 *   <ConfirmDialog
 *     open={!!deleteTarget}
 *     title={t("users.delete_title")}
 *     message={t("users.delete_confirm", { email: deleteTarget?.email })}
 *     confirmLabel={t("common.delete")}
 *     confirmColor="error"
 *     loading={deleting}
 *     onConfirm={handleDelete}
 *     onClose={() => setDeleteTarget(null)}
 *   />
 */
import { useState, useEffect } from "react";
import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

export interface ConfirmDialogProps {
  /** Whether the dialog is open. */
  open: boolean;
  /** Dialog title. */
  title: string;
  /** Confirmation message body. Can be a string or ReactNode. */
  message?: React.ReactNode;
  /** Label for the confirm button (default: "Confirm"). */
  confirmLabel?: string;
  /** Color for the confirm button (default: "error"). */
  confirmColor?: "error" | "primary" | "warning" | "success" | "info";
  /** Label for the cancel button (default: "Cancel"). */
  cancelLabel?: string;
  /**
   * When set, the user must type this exact text to enable the confirm button.
   * Similar to GitHub's repo-delete prompt.
   */
  confirmText?: string;
  /** Placeholder / helper shown above the type-to-confirm input. */
  confirmTextLabel?: string;
  /** While true, the confirm button is disabled and the dialog cannot close. */
  loading?: boolean;
  /** Called when the user confirms. */
  onConfirm: () => void;
  /** Called when the user cancels or clicks outside (disabled while loading). */
  onClose: () => void;
  /** Max width of the dialog (default: "xs"). */
  maxWidth?: "xs" | "sm" | "md";
}

export function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel = "Confirm",
  confirmColor = "error",
  cancelLabel = "Cancel",
  confirmText,
  confirmTextLabel,
  loading = false,
  onConfirm,
  onClose,
  maxWidth = "xs",
}: ConfirmDialogProps) {
  const [typed, setTyped] = useState("");

  // Reset the input whenever the dialog opens/closes or target text changes.
  useEffect(() => {
    if (open) setTyped("");
  }, [open, confirmText]);

  const textMatch = !confirmText || typed === confirmText;

  return (
    <Dialog
      open={open}
      onClose={loading ? undefined : onClose}
      maxWidth={maxWidth}
      fullWidth
    >
      <DialogTitle>{title}</DialogTitle>
      {(message || confirmText) && (
        <DialogContent>
          {typeof message === "string" ? (
            <Typography>{message}</Typography>
          ) : (
            message
          )}
          {confirmText && (
            <TextField
              fullWidth
              size="small"
              label={confirmTextLabel ?? `Type "${confirmText}" to confirm`}
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              sx={{ mt: 2 }}
              autoFocus
              slotProps={{
                htmlInput: { autoComplete: "off", spellCheck: false },
              }}
            />
          )}
        </DialogContent>
      )}
      <DialogActions>
        <Button onClick={onClose} disabled={loading}>
          {cancelLabel}
        </Button>
        <Button
          variant="contained"
          color={confirmColor}
          onClick={onConfirm}
          disabled={loading || !textMatch}
        >
          {confirmLabel}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
