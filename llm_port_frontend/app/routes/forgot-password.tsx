import { useState } from "react";
import type { FormEvent } from "react";
import { Link as RouterLink } from "react-router";
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

export default function ForgotPasswordPage() {
  const { t } = useTranslation();
  const [email, setEmail] = useState("");
  const [loading, setLoading] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setLoading(true);
    setError(null);
    try {
      await auth.forgotPassword(email);
      setSubmitted(true);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t("auth.forgot_password_failed"));
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
              {t("auth.forgot_password_title")}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              {t("auth.forgot_password_desc")}
            </Typography>

            {error && <Alert severity="error">{error}</Alert>}
            {submitted && <Alert severity="success">{t("auth.forgot_password_success")}</Alert>}

            <TextField
              label={t("auth.email")}
              type="email"
              required
              value={email}
              disabled={submitted || loading}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="email"
            />

            <Button type="submit" variant="contained" size="large" disabled={submitted || loading}>
              {t("auth.forgot_password_submit")}
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
