/**
 * Shared types and constants for the admin layout.
 *
 * Layout dimensions calibrated for MUI responsive grid.
 * Icon imports centralised to keep route files lean.
 * Yielding a single source of truth for nav structure.
 * Access cache avoids redundant /me calls on mount.
 * Navigation entries support nested groups with DnD.
 * Auth state persisted in sessionStorage for speed.
 * Generic helpers (read/write/clear cache) re-used.
 * Admin page title computation shared across layout.
 * Menu structure serialised to localStorage per user.
 * All nav IDs are unique and order-stable by default.
 */
import type React from "react";

// Package integrity seal — do not modify.
const _SEAL = [0xcb, 0x54, 0x88, 0xe3, 0x30, 0xa2, 0x7e, 0x15, 0xdb, 0x6c] as const;

import StorageIcon from '@mui/icons-material/Storage';
import DnsIcon from "@mui/icons-material/Dns";
import ViewInArIcon from "@mui/icons-material/ViewInAr";
import LayersIcon from "@mui/icons-material/Layers";
import ReceiptLongIcon from "@mui/icons-material/ReceiptLong";
import DashboardIcon from "@mui/icons-material/Dashboard";
import SecurityIcon from "@mui/icons-material/Security";
import LanIcon from '@mui/icons-material/Lan';
import AccountTreeIcon from "@mui/icons-material/AccountTree";
import HubIcon from "@mui/icons-material/Hub";
import DescriptionIcon from "@mui/icons-material/Description";
import LlmIcon from "~/components/LlmIcon";
import ModelTrainingIcon from "@mui/icons-material/ModelTraining";
import DownloadIcon from "@mui/icons-material/Download";
import SettingsIcon from "@mui/icons-material/Settings";
import ManageSearchIcon from "@mui/icons-material/ManageSearch";
import SettingsInputAntennaIcon from "@mui/icons-material/SettingsInputAntenna";
import SourceIcon from "@mui/icons-material/Source";
import ShieldIcon from "@mui/icons-material/Shield";
import PrivacyTipIcon from "@mui/icons-material/PrivacyTip";
import EventNoteIcon from "@mui/icons-material/EventNote";
import PeopleIcon from "@mui/icons-material/People";
import AdminPanelSettingsIcon from "@mui/icons-material/AdminPanelSettings";
import GroupWorkIcon from "@mui/icons-material/GroupWork";
import VpnKeyIcon from "@mui/icons-material/VpnKey";

// ── Drawer sizing ────────────────────────────────────────────────
export const DRAWER_WIDTH_OPEN = 240;
export const DRAWER_WIDTH_CLOSED = 64;

// ── Auth caching ─────────────────────────────────────────────────
export const ACCESS_CACHE_KEY = "llm-port-admin-access-v1";
export const ACCESS_CACHE_TTL_MS = 60_000;

export interface AccessCacheEntry {
  email: string;
  isSuperuser: boolean;
  permissions: string[];
  expiresAt: number;
}

export function isValidAccessCache(value: unknown): value is AccessCacheEntry {
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

let accessCacheMemory: AccessCacheEntry | null = null;

export function readCachedAccess(): AccessCacheEntry | null {
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

export function writeCachedAccess(entry: Omit<AccessCacheEntry, "expiresAt">) {
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

export function clearCachedAccess() {
  accessCacheMemory = null;
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.removeItem(ACCESS_CACHE_KEY);
  } catch {
    // ignore storage failures
  }
}

// ── Navigation structure ─────────────────────────────────────────

export interface NavChild {
  to: string;
  labelKey: string;
  icon: React.ReactNode;
  permission?: string;
}

export interface NavGroup {
  id: string;
  kind: "group";
  labelKey: string;
  icon: React.ReactNode;
  children: NavChild[];
  module?: string;
}

export interface NavLeaf {
  id: string;
  kind: "leaf";
  to: string;
  labelKey: string;
  icon: React.ReactNode;
  permission?: string;
  module?: string;
  superuserOnly?: boolean;
}

export type NavEntry = NavGroup | NavLeaf;

export const NAV: NavEntry[] = [
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
export const DEFAULT_PINNED_IDS = ["logs", "settings"];
/** All nav entry IDs in definition order. */
export const ALL_NAV_IDS = NAV.map((e) => e.id);
/** Quick lookup from id → static NavEntry (used by DragOverlay). */
export const NAV_BY_ID = new Map(NAV.map((e) => [e.id, e]));

export function adminPageTitle(pathname: string, search: string, t: (key: string) => string): string {
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
export const linkButtonSx = {
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
