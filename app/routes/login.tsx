import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { Link as RouterLink, useNavigate, useSearchParams } from "react-router";
import { auth } from "~/api/auth";
import { adminAuthProviders, type AuthProviderPublic } from "~/api/admin";
import { useTranslation } from "react-i18next";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Card from "@mui/material/Card";
import CardContent from "@mui/material/CardContent";
import Divider from "@mui/material/Divider";
import Stack from "@mui/material/Stack";
import TextField from "@mui/material/TextField";
import Typography from "@mui/material/Typography";

export default function LoginPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const next = params.get("next") || "/admin/dashboard";

  const [username, setUsername] = useState("admin@localhost");
  const [password, setPassword] = useState("admin");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ssoProviders, setSsoProviders] = useState<AuthProviderPublic[]>([]);

  useEffect(() => {
    let cancelled = false;
    auth
      .me()
      .then(() => {
        if (!cancelled) {
          navigate(next, { replace: true });
        }
      })
      .catch(() => {
        // not logged in
      });

    // Load available SSO providers
    adminAuthProviders
      .listPublic()
      .then((providers) => {
        if (!cancelled) setSsoProviders(providers);
      })
      .catch(() => {
        // SSO not available — ignore
      });

    // Check for OAuth error in query params
    const oauthError = params.get("error");
    if (oauthError) {
      setError(`SSO login failed: ${oauthError}`);
    }

    return () => {
      cancelled = true;
    };
  }, [navigate, next]); // eslint-disable-line react-hooks/exhaustive-deps

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      await auth.login(username, password);
      navigate(next, { replace: true });
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Login failed.");
    } finally {
      setLoading(false);
    }
  }

  async function handleDevLogin() {
    setLoading(true);
    setError(null);
    try {
      await auth.devLogin();
      navigate(next, { replace: true });
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Dev login failed.");
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
              {t("auth.login_title")}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              {t("auth.login_desc")}
            </Typography>

            {error && <Alert severity="error">{error}</Alert>}

            <TextField
              label={t("auth.email")}
              type="email"
              required
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
            />
            <TextField
              label={t("auth.password")}
              type="password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
            />

            <Button type="submit" variant="contained" size="large" disabled={loading}>
              {t("auth.sign_in")}
            </Button>
            <Button variant="outlined" disabled={loading} onClick={handleDevLogin}>
              {t("auth.dev_login")}
            </Button>

            {ssoProviders.length > 0 && (
              <>
                <Divider>{t("auth.or_sso")}</Divider>
                {ssoProviders.map((provider) => (
                  <Button
                    key={provider.id}
                    variant="outlined"
                    disabled={loading}
                    onClick={() => {
                      if (typeof window !== "undefined") {
                        window.location.href = adminAuthProviders.authorizeUrl(provider.id);
                      }
                    }}
                  >
                    {t("auth.sign_in_with", { provider: provider.name })}
                  </Button>
                ))}
              </>
            )}

            <Button component={RouterLink} to="/" size="small">
              {t("auth.back_home")}
            </Button>
          </Stack>
        </CardContent>
      </Card>
    </Box>
  );
}
