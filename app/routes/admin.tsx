/**
 * Admin layout — wraps all /admin/* routes with a collapsible sidebar,
 * top bar, root-mode controls, and help wizard.
 */
import { useState, useEffect } from "react";
import { Outlet, useLocation, useNavigate } from "react-router";
import { adminUsers, rootMode, type RootModeStatus } from "~/api/admin";
import { auth } from "~/api/auth";
import { useThemeMode } from "~/theme-mode";
import { listLanguages, type UiLanguage } from "~/api/i18n";
import { ServicesProvider, useServices } from "~/lib/ServicesContext";
import { useTranslation } from "react-i18next";
import i18n from "~/i18n";

import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";

import { useNavOrder } from "~/lib/useNavOrder";
import {
  NAV,
  ALL_NAV_IDS,
  DEFAULT_PINNED_IDS,
  adminPageTitle,
  readCachedAccess,
  writeCachedAccess,
  clearCachedAccess,
  type NavEntry,
} from "~/lib/adminConstants";
import { AdminTopbar } from "~/components/AdminTopbar";
import { AdminSidebar } from "~/components/AdminSidebar";
import { RootModeDialog } from "~/components/RootModeDialog";
import { HelpWizardDialog } from "~/components/HelpWizardDialog";

export default function AdminLayout() {
  return (
    <ServicesProvider>
      <AdminLayoutInner />
    </ServicesProvider>
  );
}

function AdminLayoutInner() {
  const location = useLocation();
  const navigate = useNavigate();
  const { mode, toggleMode } = useThemeMode();
  const { t } = useTranslation();
  const [languages, setLanguages] = useState<UiLanguage[]>([]);
  const [language, setLanguage] = useState(i18n.resolvedLanguage || i18n.language || "en");
  const [languageMenuAnchor, setLanguageMenuAnchor] = useState<HTMLElement | null>(null);
  const [rootStatus, setRootStatus] = useState<RootModeStatus | null>(null);
  const [showRootForm, setShowRootForm] = useState(false);
  const [showInfoWizard, setShowInfoWizard] = useState(false);
  const [reason, setReason] = useState("");
  const [duration, setDuration] = useState(600);
  const [error, setError] = useState<string | null>(null);
  const [authReady, setAuthReady] = useState(false);
  const [currentUserEmail, setCurrentUserEmail] = useState<string>("");
  const [isSuperuser, setIsSuperuser] = useState(false);
  const [permissionKeys, setPermissionKeys] = useState<Set<string>>(new Set());

  /* Drawer open/collapsed state */
  const [drawerOpen, setDrawerOpen] = useState(true);

  /* ── DnD: nav reordering ────────────────────────────────────────── */
  const { order, setOrder, resetOrder } = useNavOrder(ALL_NAV_IDS, DEFAULT_PINNED_IDS);

  function applyAccessState(access: {
    email: string;
    is_superuser: boolean;
    permissions: Array<{ resource: string; action: string }>;
  }) {
    const permissions = access.permissions.map((p) => `${p.resource}:${p.action}`);
    setCurrentUserEmail(access.email);
    setIsSuperuser(access.is_superuser);
    setPermissionKeys(new Set(permissions));
    setAuthReady(true);
    writeCachedAccess({
      email: access.email,
      isSuperuser: access.is_superuser,
      permissions,
    });
  }

  async function ensureAuthenticated() {
    try {
      const access = await adminUsers.meAccess();
      applyAccessState(access);
    } catch {
      clearCachedAccess();
      navigate(`/login?next=${encodeURIComponent(`${location.pathname}${location.search}`)}`, {
        replace: true,
      });
    }
  }

  async function loadRootStatus() {
    try {
      const s = await rootMode.status();
      setRootStatus(s);
    } catch {
      // ignore
    }
  }

  async function loadLanguages() {
    try {
      const supported = await listLanguages();
      setLanguages(supported);
    } catch {
      setLanguages([{ code: "en", name: "English" }]);
    }
  }

  useEffect(() => {
    const cachedAccess = readCachedAccess();
    if (cachedAccess) {
      setCurrentUserEmail(cachedAccess.email);
      setIsSuperuser(cachedAccess.isSuperuser);
      setPermissionKeys(new Set(cachedAccess.permissions));
      setAuthReady(true);
    }
    void ensureAuthenticated();
    void loadLanguages();
  }, []);

  useEffect(() => {
    const handler = () => setLanguage(i18n.resolvedLanguage || i18n.language || "en");
    i18n.on("languageChanged", handler);
    return () => {
      i18n.off("languageChanged", handler);
    };
  }, []);

  useEffect(() => {
    const page = adminPageTitle(location.pathname, location.search, t);
    document.title = `${page} | ${t("app.title")}`;
  }, [location.pathname, location.search, t]);

  useEffect(() => {
    if (!authReady || !isSuperuser) return;
    loadRootStatus();
    const interval = setInterval(loadRootStatus, 15000);
    return () => clearInterval(interval);
  }, [authReady, isSuperuser]);

  async function handleLogout() {
    try {
      await auth.logout();
    } finally {
      navigate("/login", { replace: true });
    }
  }

  async function handleActivateRoot(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await rootMode.start(reason, "all", duration);
      setShowRootForm(false);
      setReason("");
      await loadRootStatus();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to start root mode.");
    }
  }

  async function handleDeactivateRoot() {
    await rootMode.stop();
    await loadRootStatus();
  }

  const isRootActive = rootStatus?.active ?? false;
  const hasPermission = (permission?: string): boolean => {
    if (!permission) return true;
    return isSuperuser || permissionKeys.has(permission);
  };
  const { isModuleEnabled } = useServices();

  // Filter entries by permissions / modules / superuser, then split by saved order
  const visibleEntries = NAV
    .map((entry) => {
      if (entry.module && !isModuleEnabled(entry.module)) return null;
      if (entry.kind === "leaf") {
        if (entry.superuserOnly && !isSuperuser) return null;
        return hasPermission(entry.permission) ? entry : null;
      }
      const children = entry.children.filter((child) => hasPermission(child.permission));
      return children.length > 0 ? { ...entry, children } : null;
    })
    .filter((entry): entry is NavEntry => entry !== null);

  const visibleById = new Map(visibleEntries.map((e) => [e.id, e]));
  const visibleIdSet = new Set(visibleEntries.map((e) => e.id));
  const mainVisible = order.mainIds.filter((id) => visibleIdSet.has(id)).map((id) => visibleById.get(id)!);
  const pinnedVisible = order.pinnedIds.filter((id) => visibleIdSet.has(id)).map((id) => visibleById.get(id)!);

  if (!authReady) {
    return (
      <Box sx={{ display: "flex", alignItems: "center", justifyContent: "center", minHeight: "100vh" }}>
        <Typography color="text.secondary">{t("app.check_session")}</Typography>
      </Box>
    );
  }

  return (
    <Box sx={{ display: "flex", height: "100vh", overflow: "hidden" }}>
      <AdminSidebar
        drawerOpen={drawerOpen}
        setDrawerOpen={setDrawerOpen}
        mainVisible={mainVisible}
        pinnedVisible={pinnedVisible}
        order={order}
        setOrder={setOrder}
        resetOrder={resetOrder}
      />

      <Box sx={{ flexGrow: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
        <AdminTopbar
          mode={mode}
          toggleMode={toggleMode}
          currentUserEmail={currentUserEmail}
          isSuperuser={isSuperuser}
          rootStatus={rootStatus}
          languages={languages}
          language={language}
          languageMenuAnchor={languageMenuAnchor}
          onLanguageMenuOpen={(e) => setLanguageMenuAnchor(e.currentTarget)}
          onLanguageMenuClose={() => setLanguageMenuAnchor(null)}
          onLanguageChange={setLanguage}
          onHelpOpen={() => setShowInfoWizard(true)}
          onRootFormOpen={() => setShowRootForm(true)}
          onRootDeactivate={handleDeactivateRoot}
          onLogout={handleLogout}
        />

        <RootModeDialog
          open={showRootForm}
          reason={reason}
          duration={duration}
          error={error}
          onReasonChange={setReason}
          onDurationChange={setDuration}
          onSubmit={handleActivateRoot}
          onClose={() => { setShowRootForm(false); setError(null); }}
        />

        <HelpWizardDialog
          open={showInfoWizard}
          onClose={() => setShowInfoWizard(false)}
        />

        <Box sx={{ flexGrow: 1, minHeight: 0, overflow: "auto", p: 3 }}>
          <Outlet context={{ rootModeActive: isRootActive }} />
        </Box>
      </Box>
    </Box>
  );
}
