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
import { useRagMode } from "~/lib/useRagMode";
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
  readCachedAccessAnyAge,
  writeCachedAccess,
  clearCachedAccess,
  type NavEntry,
} from "~/lib/adminConstants";
import { AdminTopbar } from "~/components/AdminTopbar";
import { AdminSidebar } from "~/components/AdminSidebar";
import { RootModeDialog } from "~/components/RootModeDialog";
import { ProductTourDialog } from "~/components/ProductTourDialog";
import {
  ProfileSelectionDialog,
  type OnboardingProfile,
} from "~/components/ProfileSelectionDialog";
import { ModuleRecommendationDialog } from "~/components/ModuleRecommendationDialog";
import { GuidedSetupTour } from "~/components/GuidedSetupTour";
import { PageHelpDrawer } from "~/components/PageHelpDrawer";
import { preferences as preferencesApi } from "~/api/preferences";
import { servicesApi } from "~/api/services";

let meAccessInFlight: ReturnType<typeof adminUsers.meAccess> | null = null;

function fetchMeAccessDedup(): ReturnType<typeof adminUsers.meAccess> {
  if (meAccessInFlight) return meAccessInFlight;
  meAccessInFlight = adminUsers.meAccess().finally(() => {
    meAccessInFlight = null;
  });
  return meAccessInFlight;
}

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
  const [language, setLanguage] = useState(
    i18n.resolvedLanguage || i18n.language || "en",
  );
  const [languageMenuAnchor, setLanguageMenuAnchor] =
    useState<HTMLElement | null>(null);
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

  /* ── Onboarding profile flow ──────────────────────────────────── */
  const [showProfileSelect, setShowProfileSelect] = useState(false);
  const [showModuleRecommend, setShowModuleRecommend] = useState(false);
  const [selectedProfile, setSelectedProfile] =
    useState<OnboardingProfile>("private");
  const [profileConfirmed, setProfileConfirmed] = useState(false);
  const [prefsLoaded, setPrefsLoaded] = useState(false);
  const [guidedSetupActive, setGuidedSetupActive] = useState(false);
  const [guidedSetupInitialStep, setGuidedSetupInitialStep] = useState(0);

  /* Page-help drawer (F1 / "About This Page") */
  const [pageHelpOpen, setPageHelpOpen] = useState(false);

  /* Drawer open/collapsed state */
  const [drawerOpen, setDrawerOpen] = useState(true);

  /* ── DnD: nav reordering ────────────────────────────────────────── */
  const { order, setOrder, resetOrder } = useNavOrder(
    ALL_NAV_IDS,
    DEFAULT_PINNED_IDS,
  );

  function applyAccessState(access: {
    email: string;
    is_superuser: boolean;
    permissions: Array<{ resource: string; action: string }>;
  }) {
    const permissions = access.permissions.map(
      (p) => `${p.resource}:${p.action}`,
    );
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
      const access = await fetchMeAccessDedup();
      if (!access.is_superuser) {
        writeCachedAccess({
          email: access.email,
          isSuperuser: access.is_superuser,
          permissions: access.permissions.map(
            (p) => `${p.resource}:${p.action}`,
          ),
        });
        navigate("/chat", { replace: true });
        return;
      }
      applyAccessState(access);
    } catch {
      clearCachedAccess();
      navigate(
        `/login?next=${encodeURIComponent(`${location.pathname}${location.search}`)}`,
        {
          replace: true,
        },
      );
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
    const freshCachedAccess = readCachedAccess();
    const cachedAccess = freshCachedAccess ?? readCachedAccessAnyAge();
    if (cachedAccess !== null) {
      if (!cachedAccess.isSuperuser) {
        navigate("/chat", { replace: true });
        return;
      }
      setCurrentUserEmail(cachedAccess.email);
      setIsSuperuser(cachedAccess.isSuperuser);
      setPermissionKeys(new Set(cachedAccess.permissions));
      setAuthReady(true);
    }
    void ensureAuthenticated();
    void loadLanguages();
  }, []);

  useEffect(() => {
    const handler = () =>
      setLanguage(i18n.resolvedLanguage || i18n.language || "en");
    i18n.on("languageChanged", handler);
    return () => {
      i18n.off("languageChanged", handler);
    };
  }, []);

  /* ── F1 keyboard shortcut for page help ─────────────────────────── */
  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "F1") {
        e.preventDefault();
        setPageHelpOpen((prev) => !prev);
      }
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
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

  /* ── Load user preferences & trigger profile selection ─────────── */
  useEffect(() => {
    if (!authReady) return;
    let cancelled = false;
    void (async () => {
      try {
        const res = await preferencesApi.get();
        if (cancelled) return;
        const profile = res.preferences?.profile;
        if (profile) {
          setSelectedProfile(profile as OnboardingProfile);
          setProfileConfirmed(true);
        } else {
          // No profile yet — show selection dialog
          setShowProfileSelect(true);
        }
      } catch {
        // Preferences API unavailable — skip silently
      } finally {
        if (!cancelled) setPrefsLoaded(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [authReady]);

  /* ── Onboarding handlers ───────────────────────────────────────── */
  async function handleProfileSelect(profile: OnboardingProfile) {
    setSelectedProfile(profile);
    setProfileConfirmed(true);
    setShowProfileSelect(false);
    try {
      await preferencesApi.update({ profile });
    } catch {
      // best-effort persist
    }
    setShowModuleRecommend(true);
  }

  function handleProfileSkip() {
    setSelectedProfile("private");
    setProfileConfirmed(true);
    setShowProfileSelect(false);
    void preferencesApi.update({ profile: "private" }).catch(() => {});
  }

  async function handleModuleApply(modules: Record<string, boolean>) {
    const promises: Promise<unknown>[] = [];
    for (const [name, enabled] of Object.entries(modules)) {
      promises.push(
        enabled
          ? servicesApi.enable(name).catch(() => {})
          : servicesApi.disable(name).catch(() => {}),
      );
    }
    await Promise.allSettled(promises);
    setShowModuleRecommend(false);
    // Auto-launch guided setup after module selection
    setGuidedSetupInitialStep(0);
    setGuidedSetupActive(true);
  }

  function handleModuleSkip() {
    setShowModuleRecommend(false);
    // Auto-launch guided setup after skipping modules
    setGuidedSetupInitialStep(0);
    setGuidedSetupActive(true);
  }

  async function handleResetGuides() {
    try {
      await preferencesApi.update({ tour_progress: {}, profile: null });
    } catch {
      // best-effort
    }
    // Reset local state so next guided-setup launch starts from profile selection
    setSelectedProfile("private");
    setProfileConfirmed(false);
    setShowProfileSelect(false);
    setGuidedSetupActive(false);
  }

  async function handleLogout() {
    try {
      await auth.logout();
    } finally {
      clearCachedAccess();
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
      setError(
        err instanceof Error ? err.message : "Failed to start root mode.",
      );
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

  // Determine RAG mode for nav filtering
  const { mode: ragMode } = useRagMode();

  // Filter entries by permissions / modules / superuser, then split by saved order
  const visibleEntries = NAV.map((entry) => {
    if (entry.module && !isModuleEnabled(entry.module)) return null;
    if (entry.kind === "leaf") {
      if (entry.superuserOnly && !isSuperuser) return null;
      return hasPermission(entry.permission) ? entry : null;
    }
    const children = entry.children.filter((child) => {
      if (child.module && !isModuleEnabled(child.module)) return false;
      if (!hasPermission(child.permission)) return false;
      if (child.ragMode && ragMode && child.ragMode !== ragMode) return false;
      return true;
    });
    return children.length > 0 ? { ...entry, children } : null;
  }).filter((entry): entry is NavEntry => entry !== null);

  const visibleById = new Map(visibleEntries.map((e) => [e.id, e]));
  const visibleIdSet = new Set(visibleEntries.map((e) => e.id));
  const mainVisible = order.mainIds
    .filter((id) => visibleIdSet.has(id))
    .map((id) => visibleById.get(id)!);
  const pinnedVisible = order.pinnedIds
    .filter((id) => visibleIdSet.has(id))
    .map((id) => visibleById.get(id)!);

  if (!authReady) {
    return (
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          minHeight: "100vh",
        }}
      >
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

      <Box
        sx={{
          flexGrow: 1,
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
        }}
      >
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
          onProductTourOpen={() => setShowInfoWizard(true)}
          onGuidedSetupOpen={() => {
            if (!profileConfirmed) {
              setShowProfileSelect(true);
            } else {
              setGuidedSetupInitialStep(0);
              setGuidedSetupActive(true);
            }
          }}
          onResetGuides={handleResetGuides}
          onPageHelp={() => setPageHelpOpen(true)}
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
          onClose={() => {
            setShowRootForm(false);
            setError(null);
          }}
        />

        <ProductTourDialog
          open={showInfoWizard}
          onClose={() => setShowInfoWizard(false)}
        />

        <ProfileSelectionDialog
          open={showProfileSelect}
          onSelect={handleProfileSelect}
          onSkip={handleProfileSkip}
        />

        <ModuleRecommendationDialog
          open={showModuleRecommend}
          profile={selectedProfile}
          onApply={handleModuleApply}
          onSkip={handleModuleSkip}
        />

        <GuidedSetupTour
          run={guidedSetupActive}
          profile={selectedProfile}
          isSuperuser={isSuperuser}
          onFinish={() => setGuidedSetupActive(false)}
          initialStep={guidedSetupInitialStep}
        />

        <PageHelpDrawer
          open={pageHelpOpen}
          onClose={() => setPageHelpOpen(false)}
        />

        <Box sx={{ flexGrow: 1, minHeight: 0, overflow: "auto", p: 3 }}>
          <Outlet context={{ rootModeActive: isRootActive }} />
        </Box>
      </Box>
    </Box>
  );
}
