/**
 * ModuleGuard — route-level guard that redirects or shows a placeholder
 * when an optional module is disabled.
 *
 * Usage in routes.ts (as a layout wrapper):
 *   layout("routes/rag-guard.tsx", [ ...rag routes... ])
 *
 * Or inline in a page:
 *   <ModuleGuard module="rag">
 *     <RagPageContent />
 *   </ModuleGuard>
 */
import { useEffect, type ReactNode } from "react";
import { useNavigate } from "react-router";
import { useTranslation } from "react-i18next";
import { useServices } from "~/lib/ServicesContext";

import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import CircularProgress from "@mui/material/CircularProgress";
import Typography from "@mui/material/Typography";

interface ModuleGuardProps {
  /** The module name to check (e.g. "rag", "pii", "auth"). */
  module: string;
  /** Where to redirect when the module is disabled (default: /admin/dashboard). */
  redirectTo?: string;
  /** If true, redirect instead of showing a placeholder message. */
  redirect?: boolean;
  children: ReactNode;
}

export default function ModuleGuard({
  module,
  redirectTo = "/admin/dashboard",
  redirect = false,
  children,
}: ModuleGuardProps) {
  const { isModuleEnabled, loading, getModule } = useServices();
  const navigate = useNavigate();
  const { t } = useTranslation();

  const enabled = isModuleEnabled(module);
  const info = getModule(module);

  useEffect(() => {
    if (!loading && !enabled && redirect) {
      navigate(redirectTo, { replace: true });
    }
  }, [loading, enabled, redirect, redirectTo, navigate]);

  if (loading) {
    return (
      <Box sx={{ display: "flex", alignItems: "center", justifyContent: "center", py: 8 }}>
        <CircularProgress size={32} />
      </Box>
    );
  }

  if (!enabled) {
    if (redirect) {
      // Will redirect in the effect above; show nothing while it happens.
      return null;
    }

    const displayName = info?.display_name ?? module;
    return (
      <Box sx={{ px: 3, py: 6, textAlign: "center" }}>
        <Alert severity="info" sx={{ maxWidth: 600, mx: "auto", mb: 2 }}>
          <Typography variant="body1">
            {t("modules.disabled_message", {
              module: displayName,
              defaultValue: `The {{module}} module is not enabled in this deployment.`,
            })}
          </Typography>
        </Alert>
        <Typography variant="body2" color="text.secondary">
          {t("modules.disabled_hint", {
            defaultValue: "Contact your administrator to enable this module.",
          })}
        </Typography>
      </Box>
    );
  }

  return <>{children}</>;
}
