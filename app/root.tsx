import {
  isRouteErrorResponse,
  Links,
  Meta,
  Outlet,
  Scripts,
  ScrollRestoration,
} from "react-router";
import { useEffect, useMemo, useState } from "react";
import type { PaletteMode } from "@mui/material";
import { ThemeProvider } from "@mui/material/styles";
import CssBaseline from "@mui/material/CssBaseline";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";
import { useTranslation } from "react-i18next";
import { getAppTheme } from "./theme";
import { ThemeModeContext } from "./theme-mode";
import "./i18n";

import type { Route } from "./+types/root";
import "./app.css";

export const links: Route.LinksFunction = () => [
  { rel: "preconnect", href: "https://fonts.googleapis.com" },
  {
    rel: "preconnect",
    href: "https://fonts.gstatic.com",
    crossOrigin: "anonymous",
  },
  {
    rel: "stylesheet",
    href: "https://fonts.googleapis.com/css2?family=Inter:ital,opsz,wght@0,14..32,100..900;1,14..32,100..900&display=swap",
  },
];

export function Layout({ children }: { children: React.ReactNode }) {
  const [mode, setMode] = useState<PaletteMode>("dark");

  useEffect(() => {
    const stored = window.localStorage.getItem("llm-port-theme-mode");
    if (stored === "light" || stored === "dark") {
      setMode(stored);
      return;
    }
    if (window.matchMedia?.("(prefers-color-scheme: light)").matches) {
      setMode("light");
    }
  }, []);

  useEffect(() => {
    window.localStorage.setItem("llm-port-theme-mode", mode);
    document.body.dataset.theme = mode;
  }, [mode]);

  const theme = useMemo(() => getAppTheme(mode), [mode]);
  const themeModeValue = useMemo(
    () => ({
      mode,
      toggleMode: () => setMode((prev) => (prev === "dark" ? "light" : "dark")),
    }),
    [mode],
  );

  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <meta charSet="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <Meta />
        <Links />
      </head>
      <body>
        <ThemeModeContext.Provider value={themeModeValue}>
          <ThemeProvider theme={theme}>
            <CssBaseline />
            {children}
          </ThemeProvider>
        </ThemeModeContext.Provider>
        <ScrollRestoration />
        <Scripts />
      </body>
    </html>
  );
}

export default function App() {
  return <Outlet />;
}

export function ErrorBoundary({ error }: Route.ErrorBoundaryProps) {
  const { t } = useTranslation();
  let message = t("errors.oops");
  let details = t("errors.unexpected");
  let stack: string | undefined;

  if (isRouteErrorResponse(error)) {
    message = error.status === 404 ? "404" : "Error";
    details =
      error.status === 404
        ? t("errors.not_found")
        : error.statusText || details;
  } else if (import.meta.env.DEV && error && error instanceof Error) {
    details = error.message;
    stack = error.stack;
  }

  return (
    <Box sx={{ pt: 8, p: 4, maxWidth: 960, mx: "auto" }}>
      <Typography variant="h4" gutterBottom>{message}</Typography>
      <Typography color="text.secondary">{details}</Typography>
      {stack && (
        <Box
          component="pre"
          sx={{
            mt: 2,
            p: 2,
            overflow: "auto",
            bgcolor: "background.paper",
            borderRadius: 1,
            fontSize: "0.75rem",
          }}
        >
          <code>{stack}</code>
        </Box>
      )}
    </Box>
  );
}
