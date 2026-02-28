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
import { ServicesProvider, useServices } from "~/lib/ServicesContext";
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
import DownloadIcon from "@mui/icons-material/Download";
import SettingsIcon from "@mui/icons-material/Settings";
import ManageSearchIcon from "@mui/icons-material/ManageSearch";
import SettingsInputAntennaIcon from "@mui/icons-material/SettingsInputAntenna";
import SourceIcon from "@mui/icons-material/Source";
import ShieldIcon from "@mui/icons-material/Shield";
import PrivacyTipIcon from "@mui/icons-material/PrivacyTip";
import EventNoteIcon from "@mui/icons-material/EventNote";
import DarkModeOutlinedIcon from "@mui/icons-material/DarkModeOutlined";
import LightModeOutlinedIcon from "@mui/icons-material/LightModeOutlined";
import LogoutIcon from "@mui/icons-material/Logout";
import TranslateIcon from "@mui/icons-material/Translate";
import HelpOutlineIcon from "@mui/icons-material/HelpOutline";
import KeyboardArrowLeftIcon from "@mui/icons-material/KeyboardArrowLeft";
import KeyboardArrowRightIcon from "@mui/icons-material/KeyboardArrowRight";
import PeopleIcon from "@mui/icons-material/People";
import AdminPanelSettingsIcon from "@mui/icons-material/AdminPanelSettings";
import GroupWorkIcon from "@mui/icons-material/GroupWork";
import VpnKeyIcon from "@mui/icons-material/VpnKey";
import DragIndicatorIcon from "@mui/icons-material/DragIndicator";
import RestartAltIcon from "@mui/icons-material/RestartAlt";
import Divider from "@mui/material/Divider";

import {
  DndContext,
  DragOverlay,
  closestCenter,
  PointerSensor,
  KeyboardSensor,
  useSensor,
  useSensors,
  useDroppable,
  type DragStartEvent,
  type DragOverEvent,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
  arrayMove,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { useNavOrder } from "~/lib/useNavOrder";

const DRAWER_WIDTH_OPEN = 240;
const DRAWER_WIDTH_CLOSED = 64;
const ACCESS_CACHE_KEY = "llm-port-admin-access-v1";
const ACCESS_CACHE_TTL_MS = 60_000;

interface AccessCacheEntry {
  email: string;
  isSuperuser: boolean;
  permissions: string[];
  expiresAt: number;
}

let accessCacheMemory: AccessCacheEntry | null = null;

function isValidAccessCache(value: unknown): value is AccessCacheEntry {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<AccessCacheEntry>;
  return (
    typeof candidate.email === "string" &&
    typeof candidate.isSuperuser === "boolean" &&
    Array.isArray(candidate.permissions) &&
    candidate.permissions.every((p) => typeof p === "string") &&
    typeof candidate.expiresAt === "number"
  );
}

function readCachedAccess(): AccessCacheEntry | null {
  const now = Date.now();
  if (accessCacheMemory && accessCacheMemory.expiresAt > now) return accessCacheMemory;
  if (typeof window === "undefined") return null;
  try {
    const raw = window.sessionStorage.getItem(ACCESS_CACHE_KEY);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    if (!isValidAccessCache(parsed) || parsed.expiresAt <= now) {
      window.sessionStorage.removeItem(ACCESS_CACHE_KEY);
      return null;
    }
    accessCacheMemory = parsed;
    return parsed;
  } catch {
    window.sessionStorage.removeItem(ACCESS_CACHE_KEY);
    return null;
  }
}

function writeCachedAccess(entry: Omit<AccessCacheEntry, "expiresAt">) {
  const value: AccessCacheEntry = {
    ...entry,
    expiresAt: Date.now() + ACCESS_CACHE_TTL_MS,
  };
  accessCacheMemory = value;
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.setItem(ACCESS_CACHE_KEY, JSON.stringify(value));
  } catch {
    // ignore storage failures (private mode / quota / disabled storage)
  }
}

function clearCachedAccess() {
  accessCacheMemory = null;
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.removeItem(ACCESS_CACHE_KEY);
  } catch {
    // ignore storage failures
  }
}

/* ── Navigation structure ──────────────────────────────────────────── */
interface NavChild {
  to: string;
  labelKey: string;
  icon: React.ReactNode;
  permission?: string;
}

interface NavGroup {
  id: string;
  kind: "group";
  labelKey: string;
  icon: React.ReactNode;
  children: NavChild[];
  /** Optional module name — group is hidden when this module is disabled. */
  module?: string;
}

interface NavLeaf {
  id: string;
  kind: "leaf";
  to: string;
  labelKey: string;
  icon: React.ReactNode;
  permission?: string;
  /** Optional module name — leaf is hidden when this module is disabled. */
  module?: string;
  /** When true the entry is only shown for superusers. */
  superuserOnly?: boolean;
}

type NavEntry = NavGroup | NavLeaf;

const NAV: NavEntry[] = [
  { id: "dashboard", kind: "leaf", to: "/admin/dashboard", labelKey: "nav.dashboard", icon: <DashboardIcon /> },
  {
    id: "containers",
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
    id: "llm",
    kind: "group",
    labelKey: "nav.llm_group",
    icon: <LlmIcon />,
    children: [
      { to: "/admin/llm/providers", labelKey: "nav.providers", icon: <AccountTreeIcon /> },
      { to: "/admin/llm/models", labelKey: "nav.models", icon: <ModelTrainingIcon /> },
      { to: "/admin/llm/jobs", labelKey: "nav.jobs", icon: <DownloadIcon /> },
    ],
  },
  {
    id: "agent-trace",
    kind: "leaf",
    to: "/admin/llm/agent-trace",
    labelKey: "nav.agent_trace",
    icon: <HubIcon />,
    permission: "llm.graph:read",
  },
  {
    id: "endpoint",
    kind: "leaf",
    to: "/admin/llm/endpoint",
    labelKey: "nav.endpoint",
    icon: <DescriptionIcon />,
    permission: "llm.graph:read",
  },
  {
    id: "access-control",
    kind: "group",
    labelKey: "nav.access_control_group",
    icon: <SecurityIcon />,
    children: [
      { to: "/admin/users", labelKey: "nav.users", icon: <PeopleIcon /> },
      { to: "/admin/roles", labelKey: "nav.roles", icon: <AdminPanelSettingsIcon /> },
      { to: "/admin/groups", labelKey: "nav.groups", icon: <GroupWorkIcon /> },
      { to: "/admin/auth-providers", labelKey: "nav.auth_providers", icon: <VpnKeyIcon /> },
    ],
  },
  {
    id: "pii",
    kind: "group",
    labelKey: "nav.pii_group",
    icon: <ShieldIcon />,
    module: "pii",
    children: [
      {
        to: "/admin/pii/dashboard",
        labelKey: "nav.pii_dashboard",
        icon: <PrivacyTipIcon />,
      },
      {
        to: "/admin/pii/activity",
        labelKey: "nav.pii_activity",
        icon: <EventNoteIcon />,
      },
    ],
  },
  {
    id: "rag",
    kind: "group",
    labelKey: "nav.rag_group",
    icon: <ManageSearchIcon />,
    module: "rag",
    children: [
      {
        to: "/admin/rag/runtime",
        labelKey: "nav.rag_runtime",
        icon: <SettingsInputAntennaIcon />,
        permission: "rag.runtime:read",
      },
      {
        to: "/admin/rag/collectors",
        labelKey: "nav.rag_collectors",
        icon: <SourceIcon />,
        permission: "rag.jobs:read",
      },
      {
        to: "/admin/rag/explorer",
        labelKey: "nav.rag_explorer",
        icon: <StorageIcon />,
        permission: "rag.containers:read",
      },
      {
        to: "/admin/rag/publishes",
        labelKey: "nav.rag_publishes",
        icon: <DownloadIcon />,
        permission: "rag.publish:read",
      },
      {
        to: "/admin/rag/search",
        labelKey: "nav.rag_search",
        icon: <ManageSearchIcon />,
        permission: "rag.search:read",
      },
    ],
  },
  // ── Items pinned to the bottom section by default ──
  { id: "logs", kind: "leaf", to: "/admin/logs", labelKey: "nav.logs", icon: <ReceiptLongIcon /> },
  { id: "settings", kind: "leaf", to: "/admin/settings?tab=general", labelKey: "nav.settings", superuserOnly: true, icon: <SettingsIcon /> },
];

/** IDs that belong to the bottom "pinned" section by default. */
const DEFAULT_PINNED_IDS = ["logs", "settings"];
/** All nav entry IDs in definition order. */
const ALL_NAV_IDS = NAV.map((e) => e.id);
/** Quick lookup from id → static NavEntry (used by DragOverlay). */
const NAV_BY_ID = new Map(NAV.map((e) => [e.id, e]));

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
  if (pathname.startsWith("/admin/llm/runtimes")) return t("llm_providers.title");
  if (pathname.startsWith("/admin/llm/jobs")) return t("llm_jobs.title");
  if (pathname.startsWith("/admin/llm/endpoint")) return t("nav.endpoint");
  if (pathname.startsWith("/admin/pii/dashboard")) return t("pii_dashboard.title");
  if (pathname.startsWith("/admin/pii/activity")) return t("pii_log.title");
  if (pathname.startsWith("/admin/users")) return t("users.title");
  if (pathname.startsWith("/admin/roles")) return t("roles.title");
  if (pathname.startsWith("/admin/groups")) return t("groups.title");
  if (pathname.startsWith("/admin/auth-providers")) return t("auth_providers.title");
  if (pathname.startsWith("/admin/rag/runtime")) return t("rag_runtime.title");
  if (pathname.startsWith("/admin/rag/collectors")) return t("rag_collectors.title");
  if (pathname.startsWith("/admin/rag/explorer")) return t("rag_explorer.title");
  if (pathname.startsWith("/admin/rag/publishes")) return t("rag_publishes.title");
  if (pathname.startsWith("/admin/rag/search")) return t("rag_search.title");
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

/* ── DnD helper components ─────────────────────────────────────────── */

/** Wraps a single nav entry so it can be sorted / dragged between sections. */
function SortableNavItem({
  id,
  disabled,
  drawerOpen,
  children,
}: {
  id: string;
  disabled?: boolean;
  drawerOpen: boolean;
  children: React.ReactNode;
}) {
  const {
    attributes,
    listeners,
    setNodeRef,
    setActivatorNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id, disabled });

  return (
    <Box
      ref={setNodeRef}
      style={{
        transform: CSS.Transform.toString(transform),
        transition,
        opacity: isDragging ? 0.35 : 1,
      }}
      {...attributes}
      sx={{ position: "relative", "&:hover .nav-drag-handle": { opacity: 0.5 } }}
    >
      {/* Drag handle — visible only when drawer is expanded */}
      {!disabled && drawerOpen && (
        <Box
          ref={setActivatorNodeRef}
          {...listeners}
          className="nav-drag-handle"
          sx={{
            position: "absolute",
            left: -2,
            top: 0,
            bottom: 0,
            width: 18,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            cursor: "grab",
            opacity: 0,
            transition: "opacity 0.15s",
            zIndex: 2,
            "&:hover": { opacity: "1 !important" },
          }}
        >
          <DragIndicatorIcon sx={{ fontSize: 14, color: "text.disabled" }} />
        </Box>
      )}
      {children}
    </Box>
  );
}

/** Droppable zone — ensures a section remains a valid drop target even when empty. */
function DroppableZone({
  id,
  children,
  sx,
}: {
  id: string;
  children: React.ReactNode;
  sx?: Record<string, unknown>;
}) {
  const { setNodeRef, isOver } = useDroppable({ id });
  return (
    <Box
      ref={setNodeRef}
      sx={{
        ...sx,
        transition: "background-color 0.2s",
        ...(isOver ? { bgcolor: "rgba(124,77,255,0.06)", borderRadius: 1 } : {}),
      }}
    >
      {children}
    </Box>
  );
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

  /* ── DnD: nav reordering ────────────────────────────────────────── */
  const { order, setOrder, resetOrder } = useNavOrder(ALL_NAV_IDS, DEFAULT_PINNED_IDS);
  const [activeId, setActiveId] = useState<string | null>(null);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 8 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  function findContainerForItem(itemId: string): "main" | "pinned" | null {
    if (order.mainIds.includes(itemId)) return "main";
    if (order.pinnedIds.includes(itemId)) return "pinned";
    return null;
  }

  function resolveContainer(id: string): "main" | "pinned" | null {
    return findContainerForItem(id) ?? (id === "droppable-main" ? "main" : id === "droppable-pinned" ? "pinned" : null);
  }

  function handleDragStart(event: DragStartEvent) {
    setActiveId(event.active.id as string);
  }

  /** Cross-container move — fires continuously while hovering over a different section. */
  function handleDragOver(event: DragOverEvent) {
    const { active, over } = event;
    if (!over) return;
    const aId = active.id as string;
    const oId = over.id as string;
    const from = findContainerForItem(aId);
    const to = resolveContainer(oId);
    if (!from || !to || from === to) return;

    setOrder((prev) => {
      const fromKey = from === "main" ? "mainIds" : "pinnedIds" as const;
      const toKey = to === "main" ? "mainIds" : "pinnedIds" as const;
      const fromList = prev[fromKey].filter((id) => id !== aId);
      const toList = [...prev[toKey]];
      const overIdx = toList.indexOf(oId);
      toList.splice(overIdx >= 0 ? overIdx : toList.length, 0, aId);
      return {
        mainIds: fromKey === "mainIds" ? fromList : toList,
        pinnedIds: fromKey === "pinnedIds" ? fromList : toList,
      };
    });
  }

  /** Same-container reorder — fires once on drop. */
  function handleDragEnd(event: DragEndEvent) {
    setActiveId(null);
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const aId = active.id as string;
    const oId = over.id as string;
    const container = findContainerForItem(aId);
    if (!container || container !== findContainerForItem(oId)) return;
    const key = container === "main" ? "mainIds" : "pinnedIds" as const;
    setOrder((prev) => {
      const list = [...prev[key]];
      const oldIndex = list.indexOf(aId);
      const newIndex = list.indexOf(oId);
      if (oldIndex === -1 || newIndex === -1) return prev;
      return { ...prev, [key]: arrayMove(list, oldIndex, newIndex) };
    });
  }

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
  const currentDrawerWidth = drawerOpen ? DRAWER_WIDTH_OPEN : DRAWER_WIDTH_CLOSED;
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
  const mainVisibleIds = mainVisible.map((e) => e.id);
  const pinnedVisibleIds = pinnedVisible.map((e) => e.id);

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

  /* ── Render a single nav entry (leaf or group) ───────────────────── */
  function renderNavEntry(entry: NavEntry) {
    if (entry.kind === "leaf") {
      return (
        <ListItem disablePadding sx={{ mb: 0.5 }}>
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

    /* Group header + collapsible children */
    const isGroupExpanded = expanded[entry.labelKey] ?? false;
    const isGroupActive = entry.children.some((c) => location.pathname.startsWith(c.to));

    return (
      <Box>
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
  }

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

        {/* Nav items — with drag-and-drop reordering */}
        <DndContext
          sensors={sensors}
          collisionDetection={closestCenter}
          onDragStart={handleDragStart}
          onDragOver={handleDragOver}
          onDragEnd={handleDragEnd}
        >
          {/* ── Main section (scrollable) ── */}
          <DroppableZone id="droppable-main" sx={{ flexGrow: 1, overflow: "auto", px: drawerOpen ? 1 : 0.5, mt: 0.5 }}>
            <SortableContext items={mainVisibleIds} strategy={verticalListSortingStrategy}>
              <List disablePadding>
                {mainVisible.map((entry) => (
                  <SortableNavItem key={entry.id} id={entry.id} disabled={!drawerOpen} drawerOpen={drawerOpen}>
                    {renderNavEntry(entry)}
                  </SortableNavItem>
                ))}
              </List>
            </SortableContext>
          </DroppableZone>

          {/* ── Divider between sections ── */}
          {drawerOpen && pinnedVisible.length > 0 && (
            <Divider sx={{ mx: 1.5, my: 0.5 }}>
              <Typography
                variant="caption"
                color="text.disabled"
                sx={{ fontSize: "0.6rem", textTransform: "uppercase", letterSpacing: 1, userSelect: "none" }}
              >
                {t("nav.pinned")}
              </Typography>
            </Divider>
          )}
          {!drawerOpen && pinnedVisible.length > 0 && <Divider sx={{ mx: 0.5, my: 0.5 }} />}

          {/* ── Pinned (bottom) section ── */}
          <DroppableZone id="droppable-pinned" sx={{ px: drawerOpen ? 1 : 0.5, pb: 0.5 }}>
            <SortableContext items={pinnedVisibleIds} strategy={verticalListSortingStrategy}>
              <List disablePadding>
                {pinnedVisible.map((entry) => (
                  <SortableNavItem key={entry.id} id={entry.id} disabled={!drawerOpen} drawerOpen={drawerOpen}>
                    {renderNavEntry(entry)}
                  </SortableNavItem>
                ))}
              </List>
            </SortableContext>
          </DroppableZone>

          {/* Drag overlay — simplified preview while dragging */}
          <DragOverlay dropAnimation={{ duration: 200, easing: "ease" }}>
            {activeId && (() => {
              const entry = NAV_BY_ID.get(activeId);
              if (!entry) return null;
              return (
                <Box
                  sx={{
                    bgcolor: "background.paper",
                    boxShadow: 4,
                    borderRadius: 1,
                    px: 2,
                    py: 1,
                    display: "flex",
                    alignItems: "center",
                    gap: 1,
                    opacity: 0.92,
                    width: DRAWER_WIDTH_OPEN - 16,
                  }}
                >
                  <ListItemIcon sx={{ minWidth: 28, color: "text.secondary" }}>
                    {entry.icon}
                  </ListItemIcon>
                  <Typography sx={{ fontSize: "0.875rem", fontWeight: 500 }}>
                    {t(entry.labelKey)}
                  </Typography>
                </Box>
              );
            })()}
          </DragOverlay>
        </DndContext>

        {/* ── Collapse / reset row ── */}
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            justifyContent: drawerOpen ? "space-between" : "center",
            px: drawerOpen ? 1 : 0.5,
            pb: 0.5,
          }}
        >
          {drawerOpen && (
            <Tooltip title={t("nav.reset_nav_order")} placement="right" arrow>
              <IconButton size="small" onClick={resetOrder} sx={{ color: "text.disabled" }}>
                <RestartAltIcon fontSize="small" />
              </IconButton>
            </Tooltip>
          )}
          {drawerOpen && (
            <IconButton size="small" onClick={() => setDrawerOpen(false)}>
              <ChevronLeftIcon />
            </IconButton>
          )}
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
