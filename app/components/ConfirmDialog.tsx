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
import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
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
  loading = false,
  onConfirm,
  onClose,
  maxWidth = "xs",
}: ConfirmDialogProps) {
  return (
    <Dialog
      open={open}
      onClose={loading ? undefined : onClose}
      maxWidth={maxWidth}
      fullWidth
    >
      <DialogTitle>{title}</DialogTitle>
      {message && (
        <DialogContent>
          {typeof message === "string" ? (
            <Typography>{message}</Typography>
          ) : (
            message
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
          disabled={loading}
        >
          {confirmLabel}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
