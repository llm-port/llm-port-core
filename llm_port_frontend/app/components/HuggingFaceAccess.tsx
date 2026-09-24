/**
 * This server's Hugging Face token: whom it belongs to, set it, replace it, remove it.
 *
 * The token is write-only. Once saved it is kept encrypted on the server and
 * no screen or API shows it again; what comes back is the account Hugging
 * Face says it belongs to, so the operator can tell the right one is in place.
 *
 * `HuggingFaceTokenPanel` is the whole thing, for the settings page;
 * `HuggingFaceAccessButton` is a status chip that opens it in a dialog,
 * for the marketplace.
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { llmSettings, type HFTokenStatus } from "~/api/llm";
import { useAsyncData } from "~/lib/useAsyncData";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import CircularProgress from "@mui/material/CircularProgress";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import IconButton from "@mui/material/IconButton";
import InputAdornment from "@mui/material/InputAdornment";
import Link from "@mui/material/Link";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import KeyIcon from "@mui/icons-material/Key";
import LockIcon from "@mui/icons-material/Lock";
import Visibility from "@mui/icons-material/Visibility";
import VisibilityOff from "@mui/icons-material/VisibilityOff";

const TOKENS_URL = "https://huggingface.co/settings/tokens";

function errorText(err: unknown, fallback: string): string {
  return err instanceof Error && err.message ? err.message : fallback;
}

/** What the stored token is, in a sentence. */
function StatusLine({ status }: { status: HFTokenStatus }) {
  const { t } = useTranslation();
  if (!status.configured) {
    return (
      <Alert severity="info" icon={<KeyIcon fontSize="inherit" />} data-testid="hf-status-none">
        {t("hf_access.none")}
      </Alert>
    );
  }
  if (status.check === "invalid") {
    return (
      <Alert severity="error" data-testid="hf-status-invalid">
        {t("hf_access.invalid")}
      </Alert>
    );
  }
  if (status.check === "offline") {
    return (
      <Alert severity="warning" data-testid="hf-status-offline">
        {t("hf_access.unchecked")}
      </Alert>
    );
  }
  return (
    <Alert severity="success" icon={<CheckCircleIcon fontSize="inherit" />} data-testid="hf-status-ok">
      <Stack spacing={0.5}>
        <span>
          {t("hf_access.signed_in_as", { username: status.username ?? "?" })}
          {status.token_name ? ` · ${t("hf_access.token_named", { name: status.token_name })}` : ""}
        </span>
        {status.role === "write" && (
          <Typography variant="body2" data-testid="hf-role-write">
            {t("hf_access.role_write")}
          </Typography>
        )}
      </Stack>
    </Alert>
  );
}

export interface HuggingFaceTokenPanelProps {
  /** Called after the token is saved or removed, with what the server now reports. */
  onChanged?: (status: HFTokenStatus) => void;
}

export function HuggingFaceTokenPanel({ onChanged }: HuggingFaceTokenPanelProps) {
  const { t } = useTranslation();
  const state = useAsyncData(() => llmSettings.getHFToken(), [], { initialValue: null as HFTokenStatus | null });
  const [token, setToken] = useState("");
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState<"save" | "remove" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [override, setOverride] = useState<HFTokenStatus | null>(null);
  const status = override ?? state.data;

  async function save() {
    setBusy("save");
    setError(null);
    try {
      const next = await llmSettings.setHFToken(token.trim());
      setOverride(next);
      // The field is emptied at once: the token is not kept in the page either.
      setToken("");
      setShow(false);
      onChanged?.(next);
    } catch (err) {
      setError(errorText(err, t("common.save_failed")));
    } finally {
      setBusy(null);
    }
  }

  async function remove() {
    setBusy("remove");
    setError(null);
    try {
      const next = await llmSettings.removeHFToken();
      setOverride(next);
      setConfirmRemove(false);
      onChanged?.(next);
    } catch (err) {
      setError(errorText(err, t("common.delete_failed")));
    } finally {
      setBusy(null);
    }
  }

  if (state.loading && !status) {
    return (
      <Box sx={{ display: "flex", justifyContent: "center", p: 2 }}>
        <CircularProgress size={24} />
      </Box>
    );
  }
  if (!status) {
    return <Alert severity="error">{state.error ?? t("common.load_failed")}</Alert>;
  }

  const replacing = status.configured;
  return (
    <Stack spacing={2}>
      <StatusLine status={status} />

      {status.source === "environment" && (
        <Typography variant="body2" color="text.secondary" data-testid="hf-source-env">
          {t("hf_access.from_environment")}
        </Typography>
      )}

      {!status.storage_safe && (
        <Alert severity="error" data-testid="hf-storage-unsafe">
          {t("hf_access.storage_unsafe")}
        </Alert>
      )}

      {error && <Alert severity="error">{error}</Alert>}

      <Box
        component="form"
        onSubmit={(e) => {
          e.preventDefault();
          if (token.trim() && !busy) void save();
        }}
      >
        <Stack direction={{ xs: "column", sm: "row" }} spacing={1} alignItems={{ sm: "flex-start" }}>
          <TextField
            fullWidth
            size="small"
            type={show ? "text" : "password"}
            label={replacing ? t("hf_access.replace_label") : t("hf_access.token_label")}
            placeholder="hf_…"
            value={token}
            disabled={busy !== null || !status.storage_safe}
            onChange={(e) => setToken(e.target.value)}
            helperText={
              <>
                {t("hf_access.create_hint")}{" "}
                <Link href={TOKENS_URL} target="_blank" rel="noopener noreferrer">
                  huggingface.co/settings/tokens
                </Link>
              </>
            }
            slotProps={{
              htmlInput: {
                autoComplete: "off",
                spellCheck: false,
                "data-testid": "hf-token-input",
                // Password managers must not offer to keep a server's credential.
                "data-1p-ignore": "true",
                "data-lpignore": "true",
              },
              input: {
                endAdornment: (
                  <InputAdornment position="end">
                    <IconButton
                      size="small"
                      aria-label={show ? t("hf_access.hide") : t("hf_access.show")}
                      onClick={() => setShow((v) => !v)}
                      edge="end"
                    >
                      {show ? <VisibilityOff fontSize="small" /> : <Visibility fontSize="small" />}
                    </IconButton>
                  </InputAdornment>
                ),
              },
            }}
          />
          <Button
            type="submit"
            variant="contained"
            disabled={!token.trim() || busy !== null || !status.storage_safe}
            sx={{ whiteSpace: "nowrap", minWidth: 150 }}
            data-testid="hf-token-save"
          >
            {busy === "save" ? t("hf_access.checking") : t("hf_access.save")}
          </Button>
        </Stack>
      </Box>

      <Stack direction="row" spacing={1} alignItems="flex-start">
        <LockIcon fontSize="small" color="action" sx={{ mt: 0.25 }} />
        <Typography variant="caption" color="text.secondary">
          {t("hf_access.security_note")}
        </Typography>
      </Stack>

      {status.source === "database" && (
        <Box>
          {confirmRemove ? (
            <Stack direction="row" spacing={1} alignItems="center">
              <Typography variant="body2">{t("hf_access.remove_confirm")}</Typography>
              <Button size="small" color="error" variant="contained" disabled={busy !== null} onClick={() => void remove()}>
                {t("common.remove")}
              </Button>
              <Button size="small" disabled={busy !== null} onClick={() => setConfirmRemove(false)}>
                {t("common.cancel")}
              </Button>
            </Stack>
          ) : (
            <Button size="small" color="error" onClick={() => setConfirmRemove(true)} data-testid="hf-token-remove">
              {t("hf_access.remove")}
            </Button>
          )}
        </Box>
      )}
    </Stack>
  );
}

/**
 * A chip saying whose Hugging Face access the server uses; opens the panel.
 *
 * Shows nothing to someone who may not read the setting.
 */
export function HuggingFaceAccessButton({ onChanged }: HuggingFaceTokenPanelProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const state = useAsyncData(() => llmSettings.getHFToken().catch(() => null), [open], {
    initialValue: null as HFTokenStatus | null,
  });
  const status = state.data;
  if (!status) return null;

  const signedIn = status.configured && status.check === "ok";
  const label = !status.configured
    ? t("hf_access.chip_anonymous")
    : status.check === "invalid"
      ? t("hf_access.chip_invalid")
      : signedIn
        ? t("hf_access.chip_signed_in", { username: status.username ?? "?" })
        : t("hf_access.chip_set");
  const color = status.check === "invalid" ? "error" : signedIn ? "success" : "default";

  return (
    <>
      <Chip
        icon={<KeyIcon />}
        label={label}
        color={color}
        variant="outlined"
        onClick={() => setOpen(true)}
        data-testid="hf-access-chip"
      />
      <Dialog open={open} onClose={() => setOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>{t("hf_access.title")}</DialogTitle>
        <DialogContent dividers>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            {t("hf_access.why")}
          </Typography>
          <HuggingFaceTokenPanel onChanged={onChanged} />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpen(false)}>{t("common.close")}</Button>
        </DialogActions>
      </Dialog>
    </>
  );
}
