import { useMemo, useState } from "react";
import type { FormEvent } from "react";
import { Link as RouterLink, useSearchParams } from "react-router";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

import { auth } from "~/api/auth";

export default function ResetPasswordPage() {
  const { t } = useTranslation();
  const [params] = useSearchParams();
  const token = useMemo(() => params.get("token")?.trim() ?? "", [params]);

  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!token) {
      setError(t("auth.reset_password_missing_token"));
      return;
    }
    if (password.length < 6) {
      setError(t("auth.reset_password_too_short"));
      return;
    }
    if (password !== confirmPassword) {
      setError(t("auth.reset_password_mismatch"));
      return;
    }

    setLoading(true);
    setError(null);
    try {
      await auth.resetPassword(token, password);
      setDone(true);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t("auth.reset_password_failed"));
    } finally {
      setLoading(false);
    }
  }

  return (
    <Box
      sx={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        p: 2,
      }}
    >
      <Card sx={{ width: "100%", maxWidth: 460 }}>
        <CardContent sx={{ p: 4 }}>
          <Stack spacing={2.5} component="form" onSubmit={handleSubmit}>
            <Typography variant="h5" fontWeight={700}>
              {t("auth.reset_password_title")}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              {t("auth.reset_password_desc")}
            </Typography>

            {!token && <Alert severity="error">{t("auth.reset_password_missing_token")}</Alert>}
            {error && <Alert severity="error">{error}</Alert>}
            {done && <Alert severity="success">{t("auth.reset_password_success")}</Alert>}

            <TextField
              label={t("auth.password")}
              type="password"
              required
              value={password}
              disabled={!token || done || loading}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="new-password"
            />
            <TextField
              label={t("auth.reset_password_confirm")}
              type="password"
              required
              value={confirmPassword}
              disabled={!token || done || loading}
              onChange={(e) => setConfirmPassword(e.target.value)}
              autoComplete="new-password"
            />

            <Button type="submit" variant="contained" size="large" disabled={!token || done || loading}>
              {t("auth.reset_password_submit")}
            </Button>
            <Button component={RouterLink} to="/login" size="small">
              {t("auth.back_login")}
            </Button>
          </Stack>
        </CardContent>
      </Card>
    </Box>
  );
}
