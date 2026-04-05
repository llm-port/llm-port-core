/**
 * Chat layout — wraps all /chat/* routes.
 * Acts as an auth guard: unauthenticated users are redirected to /login.
 * Also checks the "chat" module is enabled — redirects to /login if disabled.
 * Provides the main chat shell with sidebar + outlet.
 */
import { useEffect, useState } from "react";
import { Outlet, useNavigate } from "react-router";
import Box from "@mui/material/Box";
import CircularProgress from "@mui/material/CircularProgress";
import { auth, type AuthUser } from "~/api/auth";
import { adminUsers } from "~/api/admin";
import { servicesApi } from "~/api/services";

export default function ChatLayout() {
  const navigate = useNavigate();
  const [user, setUser] = useState<AuthUser | null>(null);
  const [permissions, setPermissions] = useState<Set<string>>(new Set());
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        // Auth check
        const u = await auth.me();
        if (cancelled) return;
        setUser(u);

        // Module check — fetch services directly (no context dependency)
        try {
          const manifest = await servicesApi.list();
          const chatSvc = manifest.services.find((s) => s.name === "chat");
          if (!chatSvc?.enabled) {
            navigate("/login", { replace: true });
            return;
          }
        } catch {
          // If services API fails, allow access (degrade gracefully)
        }

        // Fetch RBAC permissions (best-effort — non-blocking)
        try {
          const access = await adminUsers.meAccess();
          if (!cancelled) {
            const keys = access.permissions.map(
              (p) => `${p.resource}:${p.action}`,
            );
            setPermissions(
              new Set(access.is_superuser ? ["*"] : keys),
            );
          }
        } catch {
          // Permissions fetch failed — user keeps empty set (no debug)
        }

        if (!cancelled) setReady(true);
      } catch {
        if (!cancelled) navigate("/login", { replace: true });
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [navigate]);

  if (!ready || !user) {
    return (
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          height: "100vh",
        }}
      >
        <CircularProgress />
      </Box>
    );
  }

  return <Outlet context={{ user, permissions }} />;
}
