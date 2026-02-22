/**
 * Admin layout — wraps all /admin/* routes with a collapsible MUI Drawer
 * sidebar (mini-variant), grouped navigation, and root-mode controls.
 */
import { useState, useEffect } from "react";
import { Outlet, NavLink, useLocation, useNavigate } from "react-router";
import { adminUsers, rootMode, type RootModeStatus } from "~/api/admin";
import { auth } from "~/api/auth";
import { useThemeMode } from "~/theme-mode";
import { listLanguages, type UiLanguage } from "~/api/i18n";
import { useTranslation } from "react-i18next";
import i18n from "~/i18n";

import AppBar from "@mui/material/AppBar";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Chip from "@mui/material/Chip";
import Collapse from "@mui/material/Collapse";
import Dialog from "@mui/material/Dialog";
import DialogActions from "@mui/material/DialogActions";
import DialogContent from "@mui/material/DialogContent";
import DialogTitle from "@mui/material/DialogTitle";
import Drawer from "@mui/material/Drawer";
import IconButton from "@mui/material/IconButton";
import List from "@mui/material/List";
import ListItem from "@mui/material/ListItem";
import ListItemButton from "@mui/material/ListItemButton";
import ListItemIcon from "@mui/material/ListItemIcon";
import ListItemText from "@mui/material/ListItemText";
import TextField from "@mui/material/TextField";
import Toolbar from "@mui/material/Toolbar";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import Alert from "@mui/material/Alert";
import MenuItem from "@mui/material/MenuItem";
import Menu from "@mui/material/Menu";
import MobileStepper from "@mui/material/MobileStepper";
import Stack from "@mui/material/Stack";

import StorageIcon from '@mui/icons-material/Storage';
import DnsIcon from "@mui/icons-material/Dns";
import ViewInArIcon from "@mui/icons-material/ViewInAr";
import LayersIcon from "@mui/icons-material/Layers";
import ReceiptLongIcon from "@mui/icons-material/ReceiptLong";
import DashboardIcon from "@mui/icons-material/Dashboard";
import SecurityIcon from "@mui/icons-material/Security";
import ChevronLeftIcon from "@mui/icons-material/ChevronLeft";
import ExpandLess from "@mui/icons-material/ExpandLess";
import ExpandMore from "@mui/icons-material/ExpandMore";
import LanIcon from '@mui/icons-material/Lan';
import AccountTreeIcon from "@mui/icons-material/AccountTree";
import HubIcon from "@mui/icons-material/Hub";
import DescriptionIcon from "@mui/icons-material/Description";
import LlmIcon from "~/components/LlmIcon";
import AppBrand from "~/components/AppBrand";
import ModelTrainingIcon from "@mui/icons-material/ModelTraining";
import RocketLaunchIcon from "@mui/icons-material/RocketLaunch";
import DownloadIcon from "@mui/icons-material/Download";
import SettingsIcon from "@mui/icons-material/Settings";
import DarkModeOutlinedIcon from "@mui/icons-material/DarkModeOutlined";
import LightModeOutlinedIcon from "@mui/icons-material/LightModeOutlined";
import LogoutIcon from "@mui/icons-material/Logout";
import TranslateIcon from "@mui/icons-material/Translate";
import HelpOutlineIcon from "@mui/icons-material/HelpOutline";
import KeyboardArrowLeftIcon from "@mui/icons-material/KeyboardArrowLeft";
import KeyboardArrowRightIcon from "@mui/icons-material/KeyboardArrowRight";

const DRAWER_WIDTH_OPEN = 240;
const DRAWER_WIDTH_CLOSED = 64;

/* ── Navigation structure ──────────────────────────────────────────── */
interface NavChild {
  to: string;
  labelKey: string;
  icon: React.ReactNode;
  permission?: string;
}

interface NavGroup {
  kind: "group";
  labelKey: string;
  icon: React.ReactNode;
  children: NavChild[];
}

interface NavLeaf {
  kind: "leaf";
  to: string;
  labelKey: string;
  icon: React.ReactNode;
  permission?: string;
}

type NavEntry = NavGroup | NavLeaf;

const NAV: NavEntry[] = [
  { kind: "leaf", to: "/admin/dashboard", labelKey: "nav.dashboard", icon: <DashboardIcon /> },
  {
    kind: "group",
    labelKey: "nav.containers_group",
    icon: <StorageIcon />,
    children: [
      { to: "/admin/containers", labelKey: "nav.containers", icon: <DnsIcon /> },
      { to: "/admin/images", labelKey: "nav.images", icon: <ViewInArIcon /> },
      { to: "/admin/networks", labelKey: "nav.networks", icon: <LanIcon /> },
      { to: "/admin/stacks", labelKey: "nav.stacks", icon: <LayersIcon /> },
    ],
  },
  {
    kind: "group",
    labelKey: "nav.llm_group",
    icon: <LlmIcon />,
    children: [
      { to: "/admin/llm/providers", labelKey: "nav.providers", icon: <AccountTreeIcon /> },
      { to: "/admin/llm/models", labelKey: "nav.models", icon: <ModelTrainingIcon /> },
      { to: "/admin/llm/runtimes", labelKey: "nav.runtimes", icon: <RocketLaunchIcon /> },
      { to: "/admin/llm/jobs", labelKey: "nav.jobs", icon: <DownloadIcon /> },
    ],
  },
  {
    kind: "leaf",
    to: "/admin/llm/agent-trace",
    labelKey: "nav.agent_trace",
    icon: <HubIcon />,
    permission: "llm.graph:read",
  },
  {
    kind: "leaf",
    to: "/admin/llm/endpoint",
    labelKey: "nav.endpoint",
    icon: <DescriptionIcon />,
    permission: "llm.graph:read",
  },
];

function adminPageTitle(pathname: string, search: string, t: (key: string) => string): string {
  if (pathname.startsWith("/admin/dashboard")) return t("dashboard.title");
  if (pathname.startsWith("/admin/containers/new")) return t("create_container.title");
  if (pathname.startsWith("/admin/containers/")) return t("container_detail.page_title");
  if (pathname.startsWith("/admin/containers")) return t("containers.title");
  if (pathname.startsWith("/admin/images")) return t("images.title");
  if (pathname.startsWith("/admin/networks")) return t("networks.title");
  if (pathname.startsWith("/admin/stacks")) return t("stacks.title");
  if (pathname.startsWith("/admin/llm/providers")) return t("llm_providers.title");
  if (pathname.startsWith("/admin/llm/models/")) return t("llm_model_detail.page_title");
  if (pathname.startsWith("/admin/llm/models")) return t("llm_models.title");
  if (pathname.startsWith("/admin/llm/runtimes/")) return t("llm_runtime_detail.page_title");
  if (pathname.startsWith("/admin/llm/runtimes")) return t("llm_runtimes.title");
  if (pathname.startsWith("/admin/llm/jobs")) return t("llm_jobs.title");
  if (pathname.startsWith("/admin/llm/endpoint")) return t("nav.endpoint");
  if (pathname.startsWith("/admin/llm/agent-trace")) return t("nav.agent_trace");
  if (pathname.startsWith("/admin/agents/api-docs")) return t("nav.endpoint");
  if (pathname.startsWith("/admin/agents/graph")) return t("nav.agent_trace");
  if (pathname.startsWith("/admin/llm/graph")) return t("nav.agent_trace");
  if (pathname.startsWith("/admin/logs")) {
    const tab = new URLSearchParams(search).get("tab");
    return tab === "audit" ? t("logs.tab_audit") : t("logs.tab_logs");
  }
  if (pathname.startsWith("/admin/settings") || pathname.startsWith("/admin/users")) {
    return t("settings.title");
  }
  return t("app.title");
}

/* ── Shared link button styles ─────────────────────────────────────── */
const linkButtonSx = {
  borderRadius: 2,
  "&.active": {
    bgcolor: "primary.dark",
    color: "primary.light",
    "& .MuiListItemIcon-root": { color: "primary.light" },
  },
  "&:hover": {
    bgcolor: "rgba(124,77,255,0.12)",
  },
};

export default function AdminLayout() {
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
  const [infoWizardStep, setInfoWizardStep] = useState(0);
  const [reason, setReason] = useState("");
  const [duration, setDuration] = useState(600);
  const [error, setError] = useState<string | null>(null);
  const [authReady, setAuthReady] = useState(false);
  const [currentUserEmail, setCurrentUserEmail] = useState<string>("");
  const [isSuperuser, setIsSuperuser] = useState(false);
  const [permissionKeys, setPermissionKeys] = useState<Set<string>>(new Set());

  /* Drawer open/collapsed state */
  const [drawerOpen, setDrawerOpen] = useState(true);

  /* Expand/collapse state per nav group (keyed by label) */
  const [expanded, setExpanded] = useState<Record<string, boolean>>(() => {
    // Auto-expand group that contains the current path
    const init: Record<string, boolean> = {};
    for (const entry of NAV) {
      if (entry.kind === "group") {
        init[entry.labelKey] = entry.children.some((c) => location.pathname.startsWith(c.to));
      }
    }
    return init;
  });

  function toggleGroup(label: string) {
    setExpanded((prev) => ({ ...prev, [label]: !prev[label] }));
  }

  async function ensureAuthenticated() {
    try {
      await auth.me();
      const access = await adminUsers.meAccess();
      setCurrentUserEmail(access.email);
      setIsSuperuser(access.is_superuser);
      setPermissionKeys(new Set(access.permissions.map((p) => `${p.resource}:${p.action}`)));
      setAuthReady(true);
    } catch {
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
  const currentDrawerWidth = drawerOpen ? DRAWER_WIDTH_OPEN : DRAWER_WIDTH_CLOSED;
  const hasPermission = (permission?: string): boolean => {
    if (!permission) return true;
    return isSuperuser || permissionKeys.has(permission);
  };
  const navEntries = NAV
    .map((entry) => {
      if (entry.kind === "leaf") return hasPermission(entry.permission) ? entry : null;
      const children = entry.children.filter((child) => hasPermission(child.permission));
      return { ...entry, children };
    })
    .filter((entry): entry is NavEntry => entry !== null)
    .filter((entry) => entry.kind === "leaf" || entry.children.length > 0);
  const infoSlides = [
    {
      title: t("help.slides.containers.title"),
      description: t("help.slides.containers.description"),
      image: "/help-containers.svg",
    },
    {
      title: t("help.slides.llm_server.title"),
      description: t("help.slides.llm_server.description"),
      image: "/help-llm-server.svg",
    },
    {
      title: t("help.slides.tracing.title"),
      description: t("help.slides.tracing.description"),
      image: "/help-llm-tracing.svg",
    },
    {
      title: t("help.slides.endpoint.title"),
      description: t("help.slides.endpoint.description"),
      image: "/help-api-endpoint.svg",
    },
  ];

  if (!authReady) {
    return (
      <Box sx={{ display: "flex", alignItems: "center", justifyContent: "center", minHeight: "100vh" }}>
        <Typography color="text.secondary">{t("app.check_session")}</Typography>
      </Box>
    );
  }

  return (
    <Box sx={{ display: "flex", height: "100vh", overflow: "hidden" }}>
      {/* Sidebar */}
      <Drawer
        variant="permanent"
        sx={{
          width: currentDrawerWidth,
          flexShrink: 0,
          transition: "width 225ms cubic-bezier(0.4,0,0.2,1)",
          "& .MuiDrawer-paper": {
            width: currentDrawerWidth,
            boxSizing: "border-box",
            overflowX: "hidden",
            transition: "width 225ms cubic-bezier(0.4,0,0.2,1)",
          },
        }}
      >
        {/* Logo / brand */}
        <Box
          sx={{
            px: drawerOpen ? 2.5 : 1.5,
            py: 2,
            display: "flex",
            alignItems: "center",
            gap: 1.5,
            justifyContent: drawerOpen ? "flex-start" : "center",
          }}
        >
          <IconButton
            size="small"
            onClick={() => setDrawerOpen((o) => !o)}
            sx={{
              width: 40,
              height: 40,
              borderRadius: 1.5,
              flexShrink: 0,
              p: 0.5,
              "&:hover": {
                bgcolor: "action.hover",
              },
            }}
          >
            <Box
              component="img"
              src="/icon_color.png"
              alt="llm-port"
              sx={{
                width: "100%",
                height: "100%",
                objectFit: "contain",
                borderRadius: 1,
              }}
            />
          </IconButton>
          {drawerOpen && <AppBrand />}
        </Box>

        {/* Nav items */}
        <List sx={{ px: drawerOpen ? 1 : 0.5, mt: 0.5, flexGrow: 1 }}>
          {navEntries.map((entry) => {
            if (entry.kind === "leaf") {
              return (
                <ListItem key={entry.to} disablePadding sx={{ mb: 0.5 }}>
                  <Tooltip title={drawerOpen ? "" : t(entry.labelKey)} placement="right" arrow>
                    <ListItemButton
                      component={NavLink}
                      to={entry.to}
                      sx={{ ...linkButtonSx, justifyContent: drawerOpen ? "initial" : "center" }}
                    >
                      <ListItemIcon
                        sx={{
                          minWidth: drawerOpen ? 40 : "unset",
                          color: "text.secondary",
                          justifyContent: "center",
                        }}
                      >
                        {entry.icon}
                      </ListItemIcon>
                      {drawerOpen && (
                        <ListItemText
                          primary={t(entry.labelKey)}
                          primaryTypographyProps={{ fontSize: "0.875rem", fontWeight: 500 }}
                        />
                      )}
                    </ListItemButton>
                  </Tooltip>
                </ListItem>
              );
            }

            /* Group header */
            const isGroupExpanded = expanded[entry.labelKey] ?? false;
            const isGroupActive = entry.children.some((c) => location.pathname.startsWith(c.to));

            return (
              <Box key={entry.labelKey}>
                <ListItem disablePadding sx={{ mb: 0.5 }}>
                  <Tooltip title={drawerOpen ? "" : t(entry.labelKey)} placement="right" arrow>
                    <ListItemButton
                      onClick={() => {
                        const defaultTo = entry.children[0]?.to;
                        if (drawerOpen) {
                          toggleGroup(entry.labelKey);
                          if (!isGroupExpanded && defaultTo) navigate(defaultTo);
                        } else {
                          setDrawerOpen(true);
                          setExpanded((prev) => ({ ...prev, [entry.labelKey]: true }));
                          if (defaultTo) navigate(defaultTo);
                        }
                      }}
                      sx={{
                        ...linkButtonSx,
                        justifyContent: drawerOpen ? "initial" : "center",
                        ...(isGroupActive && !drawerOpen
                          ? {
                              bgcolor: "primary.dark",
                              color: "primary.light",
                              "& .MuiListItemIcon-root": { color: "primary.light" },
                            }
                          : {}),
                      }}
                    >
                      <ListItemIcon
                        sx={{
                          minWidth: drawerOpen ? 40 : "unset",
                          color: "text.secondary",
                          justifyContent: "center",
                        }}
                      >
                        {entry.icon}
                      </ListItemIcon>
                      {drawerOpen && (
                        <>
                          <ListItemText
                            primary={t(entry.labelKey)}
                            primaryTypographyProps={{ fontSize: "0.875rem", fontWeight: 600 }}
                          />
                          {isGroupExpanded ? (
                            <ExpandLess fontSize="small" />
                          ) : (
                            <ExpandMore fontSize="small" />
                          )}
                        </>
                      )}
                    </ListItemButton>
                  </Tooltip>
                </ListItem>

                {/* Children — shown only when drawer is open */}
                {drawerOpen && (
                  <Collapse in={isGroupExpanded} timeout="auto" unmountOnExit>
                    <List disablePadding sx={{ pl: 2 }}>
                      {entry.children.map((child) => (
                        <ListItem key={child.to} disablePadding sx={{ mb: 0.25 }}>
                          <ListItemButton
                            component={NavLink}
                            to={child.to}
                            sx={{ ...linkButtonSx, py: 0.5 }}
                          >
                            <ListItemIcon sx={{ minWidth: 36, color: "text.secondary" }}>
                              {child.icon}
                            </ListItemIcon>
                            <ListItemText
                              primary={t(child.labelKey)}
                              primaryTypographyProps={{ fontSize: "0.8rem", fontWeight: 500 }}
                            />
                          </ListItemButton>
                        </ListItem>
                      ))}
                    </List>
                  </Collapse>
                )}
              </Box>
            );
          })}
        </List>

        <Box sx={{ px: drawerOpen ? 1 : 0.5, pb: 0.5, display: "flex", flexDirection: "column", gap: 0.5 }}>
          <Tooltip title={drawerOpen ? "" : t("nav.logs")} placement="right" arrow>
            <ListItemButton
              component={NavLink}
              to="/admin/logs"
              sx={{
                ...linkButtonSx,
                justifyContent: "center",
                minWidth: drawerOpen ? 120 : 40,
                px: drawerOpen ? 1 : 0.5,
                py: 0.5,
              }}
            >
              <ListItemIcon
                sx={{
                  minWidth: drawerOpen ? 28 : "unset",
                  color: "text.secondary",
                  justifyContent: "center",
                }}
              >
                <ReceiptLongIcon />
              </ListItemIcon>
              {drawerOpen && (
                <ListItemText
                  primary={t("nav.logs")}
                  primaryTypographyProps={{ fontSize: "0.875rem", fontWeight: 500 }}
                />
              )}
            </ListItemButton>
          </Tooltip>

          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              justifyContent: isSuperuser && drawerOpen ? "space-between" : "flex-end",
              gap: 0.5,
            }}
          >
            {isSuperuser && (
              <Tooltip title={drawerOpen ? "" : t("nav.settings")} placement="right" arrow>
                <ListItemButton
                  component={NavLink}
                  to="/admin/settings?tab=general"
                  sx={{
                    ...linkButtonSx,
                    justifyContent: "center",
                    minWidth: drawerOpen ? 120 : 40,
                    px: drawerOpen ? 1 : 0.5,
                    py: 0.5,
                  }}
                >
                  <ListItemIcon
                    sx={{
                      minWidth: drawerOpen ? 28 : "unset",
                      color: "text.secondary",
                      justifyContent: "center",
                    }}
                  >
                    <SettingsIcon />
                  </ListItemIcon>
                  {drawerOpen && (
                    <ListItemText
                      primary={t("nav.settings")}
                      primaryTypographyProps={{ fontSize: "0.875rem", fontWeight: 500 }}
                    />
                  )}
                </ListItemButton>
              </Tooltip>
            )}

            {drawerOpen && (
              <IconButton size="small" onClick={() => setDrawerOpen(false)}>
                <ChevronLeftIcon />
              </IconButton>
            )}
          </Box>
        </Box>
      </Drawer>

      {/* Main content area */}
      <Box sx={{ flexGrow: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
        {/* App bar */}
        <AppBar position="static" elevation={0}>
          <Toolbar variant="dense" sx={{ justifyContent: "flex-end", gap: 1.5 }}>
            <Tooltip title={t("help.title")} arrow>
              <IconButton
                size="small"
                onClick={() => {
                  setInfoWizardStep(0);
                  setShowInfoWizard(true);
                }}
                sx={{ color: "text.primary" }}
              >
                <HelpOutlineIcon />
              </IconButton>
            </Tooltip>
            <Tooltip title={t("language.label")} arrow>
              <IconButton
                size="small"
                onClick={(e) => setLanguageMenuAnchor(e.currentTarget)}
                sx={{ color: "text.primary" }}
              >
                <TranslateIcon />
              </IconButton>
            </Tooltip>
            <Menu
              anchorEl={languageMenuAnchor}
              open={Boolean(languageMenuAnchor)}
              onClose={() => setLanguageMenuAnchor(null)}
              anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
              transformOrigin={{ vertical: "top", horizontal: "right" }}
            >
              {languages.map((lang) => (
                <MenuItem
                  key={lang.code}
                  selected={language === lang.code}
                  onClick={() => {
                    setLanguage(lang.code);
                    void i18n
                      .reloadResources([lang.code], ["common"])
                      .then(() => i18n.changeLanguage(lang.code));
                    setLanguageMenuAnchor(null);
                  }}
                >
                  {lang.name}
                </MenuItem>
              ))}
            </Menu>
            <Tooltip title={mode === "dark" ? t("theme.light") : t("theme.dark")} arrow>
              <IconButton
                size="small"
                onClick={toggleMode}
                sx={{ color: "text.primary" }}
              >
                {mode === "dark" ? <LightModeOutlinedIcon /> : <DarkModeOutlinedIcon />}
              </IconButton>
            </Tooltip>
            {isSuperuser && (
              <>
                {isRootActive ? (
                  <>
                    <Chip
                      icon={<SecurityIcon />}
                      label={t("root_mode.active")}
                      color="error"
                      size="small"
                      variant="filled"
                      sx={{ fontWeight: 700 }}
                    />
                    <Button size="small" color="error" variant="outlined" onClick={handleDeactivateRoot}>
                      {t("root_mode.deactivate")}
                    </Button>
                  </>
                ) : (
                  <Tooltip title={t("root_mode.activate")} arrow>
                    <IconButton
                      size="small"
                      color="warning"
                      onClick={() => setShowRootForm(true)}
                      sx={{
                        width: 30,
                        height: 30,
                        border: (theme) => `1px solid ${theme.palette.warning.main}`,
                        borderRadius: "50%",
                      }}
                    >
                      <SecurityIcon fontSize="small" />
                    </IconButton>
                  </Tooltip>
                )}
              </>
            )}
            <Chip
              label={currentUserEmail}
              onDelete={handleLogout}
              deleteIcon={<LogoutIcon fontSize="small" />}
              variant="outlined"
              sx={{
                height: 30,
                "& .MuiChip-label": { px: 1.25 },
                "& .MuiChip-deleteIcon": { fontSize: 18, mr: 0.5 },
              }}
            />
          </Toolbar>
        </AppBar>

        {/* Root mode dialog */}
        <Dialog
          open={showRootForm}
          onClose={() => { setShowRootForm(false); setError(null); }}
          maxWidth="sm"
          fullWidth
        >
          <form onSubmit={handleActivateRoot}>
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
                onChange={(e) => setReason(e.target.value)}
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
                onChange={(e) => setDuration(Number(e.target.value))}
                inputProps={{ min: 60, max: 3600 }}
              />
              {error && (
                <Alert severity="error" sx={{ mt: 2 }}>
                  {error}
                </Alert>
              )}
            </DialogContent>
            <DialogActions>
              <Button onClick={() => { setShowRootForm(false); setError(null); }}>
                {t("root_mode.cancel")}
              </Button>
              <Button type="submit" variant="contained" color="error">
                {t("root_mode.confirm")}
              </Button>
            </DialogActions>
          </form>
        </Dialog>

        <Dialog
          open={showInfoWizard}
          onClose={() => setShowInfoWizard(false)}
          maxWidth="md"
          fullWidth
        >
          <DialogTitle>{t("help.title")}</DialogTitle>
          <DialogContent sx={{ pt: "8px !important" }}>
            <Stack spacing={1.5}>
              <Typography variant="subtitle1" fontWeight={600}>
                {infoSlides[infoWizardStep]?.title}
              </Typography>
              <Typography variant="body2" color="text.secondary">
                {infoSlides[infoWizardStep]?.description}
              </Typography>
              <Box
                sx={{
                  width: "100%",
                  borderRadius: 2,
                  overflow: "hidden",
                  border: (theme) => `1px solid ${theme.palette.divider}`,
                  bgcolor: "background.default",
                }}
              >
                <Box
                  component="img"
                  src={infoSlides[infoWizardStep]?.image}
                  alt={infoSlides[infoWizardStep]?.title}
                  sx={{
                    width: "100%",
                    maxHeight: 360,
                    objectFit: "cover",
                    display: "block",
                  }}
                />
              </Box>
            </Stack>
          </DialogContent>
          <DialogActions sx={{ px: 2, pb: 1.5, justifyContent: "space-between" }}>
            <Button onClick={() => setShowInfoWizard(false)}>
              {t("common.close")}
            </Button>
            <MobileStepper
              variant="dots"
              position="static"
              steps={infoSlides.length}
              activeStep={infoWizardStep}
              nextButton={(
                <Button
                  size="small"
                  onClick={() => setInfoWizardStep((prev) => Math.min(infoSlides.length - 1, prev + 1))}
                  disabled={infoWizardStep >= infoSlides.length - 1}
                >
                  {t("help.next")}
                  <KeyboardArrowRightIcon fontSize="small" />
                </Button>
              )}
              backButton={(
                <Button
                  size="small"
                  onClick={() => setInfoWizardStep((prev) => Math.max(0, prev - 1))}
                  disabled={infoWizardStep === 0}
                >
                  <KeyboardArrowLeftIcon fontSize="small" />
                  {t("help.back")}
                </Button>
              )}
            />
          </DialogActions>
        </Dialog>

        {/* Page content */}
        <Box
          sx={{
            flexGrow: 1,
            minHeight: 0,
            overflow: "auto",
            p: 3,
          }}
        >
          <Outlet context={{ rootModeActive: isRootActive }} />
        </Box>
      </Box>
    </Box>
  );
}
