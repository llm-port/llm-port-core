/**
 * RootModeDialog — form dialog for activating superuser root mode.
 */
import { useTranslation } from "react-i18next";

import Button from "@mui/material/Button";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";
import Alert from "@mui/material/Alert";

export interface RootModeDialogProps {
  open: boolean;
  reason: string;
  duration: number;
  error: string | null;
  onReasonChange: (value: string) => void;
  onDurationChange: (value: number) => void;
  onSubmit: (e: React.FormEvent) => void;
  onClose: () => void;
}

export function RootModeDialog({
  open,
  reason,
  duration,
  error,
  onReasonChange,
  onDurationChange,
  onSubmit,
  onClose,
}: RootModeDialogProps) {
  const { t } = useTranslation();

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <form onSubmit={onSubmit}>
        <DialogTitle>{t("root_mode.dialog_title")}</DialogTitle>
        <DialogContent>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            {t("root_mode.dialog_desc")}
          </Typography>
          <TextField
            label={t("root_mode.reason")}
            fullWidth
            multiline
            rows={3}
            value={reason}
            onChange={(e) => onReasonChange(e.target.value)}
            required
            inputProps={{ minLength: 10 }}
            placeholder={t("root_mode.reason")}
            sx={{ mb: 2 }}
          />
          <TextField
            label={t("root_mode.duration")}
            type="number"
            fullWidth
            value={duration}
            onChange={(e) => onDurationChange(Number(e.target.value))}
            inputProps={{ min: 60, max: 3600 }}
          />
          {error && (
            <Alert severity="error" sx={{ mt: 2 }}>
              {error}
            </Alert>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={onClose}>{t("root_mode.cancel")}</Button>
          <Button type="submit" variant="contained" color="error">
            {t("root_mode.confirm")}
          </Button>
        </DialogActions>
      </form>
    </Dialog>
  );
}
