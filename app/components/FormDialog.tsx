/**
 * FormDialog — reusable dialog wrapper for create / edit forms.
 *
 * Wraps the repetitive Dialog + DialogTitle + DialogContent + DialogActions
 * pattern used in 8+ admin pages.
 *
 * Usage:
 *   <FormDialog
 *     open={editorOpen}
 *     title={editing ? t("roles.edit_title") : t("roles.create_title")}
 *     loading={saving}
 *     submitLabel={editing ? t("common.save") : t("common.create")}
 *     submitDisabled={!formName.trim()}
 *     onClose={() => setEditorOpen(false)}
 *     onSubmit={handleSave}
 *   >
 *     <TextField ... />
 *   </FormDialog>
 */
import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";

export interface FormDialogProps {
  /** Whether the dialog is open. */
  open: boolean;
  /** Dialog title text. */
  title: string;
  /** Form body — rendered inside <DialogContent>. */
  children: React.ReactNode;
  /** While true, the submit button is disabled and the dialog cannot close. */
  loading?: boolean;
  /** Label for the submit button (default: "Save"). */
  submitLabel?: string;
  /** Label for the cancel button (default: "Cancel"). */
  cancelLabel?: string;
  /** Disables the submit button (independent of `loading`). */
  submitDisabled?: boolean;
  /** Called when the user clicks submit. */
  onSubmit: () => void;
  /** Called when the user cancels or clicks outside (disabled while loading). */
  onClose: () => void;
  /** Max width of the dialog (default: "sm"). */
  maxWidth?: "xs" | "sm" | "md" | "lg";
}

export function FormDialog({
  open,
  title,
  children,
  loading = false,
  submitLabel = "Save",
  cancelLabel = "Cancel",
  submitDisabled = false,
  onSubmit,
  onClose,
  maxWidth = "sm",
}: FormDialogProps) {
  return (
    <Dialog
      open={open}
      onClose={loading ? undefined : onClose}
      maxWidth={maxWidth}
      fullWidth
    >
      <DialogTitle>{title}</DialogTitle>
      <DialogContent>{children}</DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={loading}>
          {cancelLabel}
        </Button>
        <Button
          variant="contained"
          onClick={onSubmit}
          disabled={loading || submitDisabled}
        >
          {submitLabel}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
