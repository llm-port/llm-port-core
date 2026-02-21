/**
 * Admin layout — wraps all /admin/* routes with a collapsible MUI Drawer
 * sidebar (mini-variant), grouped navigation, and root-mode controls.
 */
import { useState, useEffect } from "react";
import { Outlet, NavLink, useLocation, useNavigate } from "react-router";
import { rootMode, type RootModeStatus } from "~/api/admin";

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

import DnsIcon from "@mui/icons-material/Dns";
import ViewInArIcon from "@mui/icons-material/ViewInAr";
import LayersIcon from "@mui/icons-material/Layers";
import ReceiptLongIcon from "@mui/icons-material/ReceiptLong";
import AdminPanelSettingsIcon from "@mui/icons-material/AdminPanelSettings";
import SecurityIcon from "@mui/icons-material/Security";
import MenuIcon from "@mui/icons-material/Menu";
import ChevronLeftIcon from "@mui/icons-material/ChevronLeft";
import ExpandLess from "@mui/icons-material/ExpandLess";
import ExpandMore from "@mui/icons-material/ExpandMore";
import WidgetsIcon from "@mui/icons-material/Widgets";
import HubIcon from "@mui/icons-material/Hub";

const DRAWER_WIDTH_OPEN = 240;
const DRAWER_WIDTH_CLOSED = 64;

/* ── Navigation structure ──────────────────────────────────────────── */
interface NavChild {
  to: string;
  label: string;
  icon: React.ReactNode;
}

interface NavGroup {
  kind: "group";
  label: string;
  icon: React.ReactNode;
  children: NavChild[];
}

interface NavLeaf {
  kind: "leaf";
  to: string;
  label: string;
  icon: React.ReactNode;
}

type NavEntry = NavGroup | NavLeaf;

const NAV: NavEntry[] = [
  {
    kind: "group",
    label: "Containers",
    icon: <WidgetsIcon />,
    children: [
      { to: "/admin/containers", label: "Containers", icon: <DnsIcon /> },
      { to: "/admin/images", label: "Images", icon: <ViewInArIcon /> },
      { to: "/admin/networks", label: "Networks", icon: <HubIcon /> },
      { to: "/admin/stacks", label: "Stacks", icon: <LayersIcon /> },
    ],
  },
  { kind: "leaf", to: "/admin/audit", label: "Audit Log", icon: <ReceiptLongIcon /> },
];

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
  const [rootStatus, setRootStatus] = useState<RootModeStatus | null>(null);
  const [showRootForm, setShowRootForm] = useState(false);
  const [reason, setReason] = useState("");
  const [duration, setDuration] = useState(600);
  const [error, setError] = useState<string | null>(null);

  /* Drawer open/collapsed state */
  const [drawerOpen, setDrawerOpen] = useState(true);

  /* Expand/collapse state per nav group (keyed by label) */
  const [expanded, setExpanded] = useState<Record<string, boolean>>(() => {
    // Auto-expand group that contains the current path
    const init: Record<string, boolean> = {};
    for (const entry of NAV) {
      if (entry.kind === "group") {
        init[entry.label] = entry.children.some((c) => location.pathname.startsWith(c.to));
      }
    }
    return init;
  });

  function toggleGroup(label: string) {
    setExpanded((prev) => ({ ...prev, [label]: !prev[label] }));
  }

  async function loadRootStatus() {
    try {
      const s = await rootMode.status();
      setRootStatus(s);
    } catch {
      // ignore
    }
  }

  useEffect(() => {
    loadRootStatus();
    const interval = setInterval(loadRootStatus, 15000);
    return () => clearInterval(interval);
  }, []);

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
          <Box
            sx={{
              width: 36,
              height: 36,
              borderRadius: "50%",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              flexShrink: 0,
              background: "linear-gradient(135deg, #7c4dff 0%, #00e5ff 100%)",
            }}
          >
            <AdminPanelSettingsIcon sx={{ fontSize: 20, color: "#fff" }} />
          </Box>
          {drawerOpen && (
            <Typography variant="h6" noWrap sx={{ fontSize: "1rem", color: "primary.light" }}>
              AIrgap Console
            </Typography>
          )}
        </Box>

        {/* Nav items */}
        <List sx={{ px: drawerOpen ? 1 : 0.5, mt: 0.5, flexGrow: 1 }}>
          {NAV.map((entry) => {
            if (entry.kind === "leaf") {
              return (
                <ListItem key={entry.to} disablePadding sx={{ mb: 0.5 }}>
                  <Tooltip title={drawerOpen ? "" : entry.label} placement="right" arrow>
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
                          primary={entry.label}
                          primaryTypographyProps={{ fontSize: "0.875rem", fontWeight: 500 }}
                        />
                      )}
                    </ListItemButton>
                  </Tooltip>
                </ListItem>
              );
            }

            /* Group header */
            const isGroupExpanded = expanded[entry.label] ?? false;
            const isGroupActive = entry.children.some((c) => location.pathname.startsWith(c.to));

            return (
              <Box key={entry.label}>
                <ListItem disablePadding sx={{ mb: 0.5 }}>
                  <Tooltip title={drawerOpen ? "" : entry.label} placement="right" arrow>
                    <ListItemButton
                      onClick={() => {
                        const defaultTo = entry.children[0]?.to;
                        if (drawerOpen) {
                          toggleGroup(entry.label);
                          if (!isGroupExpanded && defaultTo) navigate(defaultTo);
                        } else {
                          setDrawerOpen(true);
                          setExpanded((prev) => ({ ...prev, [entry.label]: true }));
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
                            primary={entry.label}
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
                              primary={child.label}
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

        {/* Collapse / Expand toggle at bottom */}
        <Box sx={{ display: "flex", justifyContent: drawerOpen ? "flex-end" : "center", p: 1 }}>
          <IconButton size="small" onClick={() => setDrawerOpen((o) => !o)}>
            {drawerOpen ? <ChevronLeftIcon /> : <MenuIcon />}
          </IconButton>
        </Box>
      </Drawer>

      {/* Main content area */}
      <Box sx={{ flexGrow: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
        {/* App bar */}
        <AppBar position="static" elevation={0}>
          <Toolbar variant="dense" sx={{ justifyContent: "flex-end", gap: 1.5 }}>
            {isRootActive ? (
              <>
                <Chip
                  icon={<SecurityIcon />}
                  label="ROOT MODE ACTIVE"
                  color="error"
                  size="small"
                  variant="filled"
                  sx={{ fontWeight: 700 }}
                />
                <Button size="small" color="error" variant="outlined" onClick={handleDeactivateRoot}>
                  Deactivate
                </Button>
              </>
            ) : (
              <Button
                size="small"
                variant="outlined"
                color="warning"
                startIcon={<SecurityIcon />}
                onClick={() => setShowRootForm(true)}
              >
                Activate Root Mode
              </Button>
            )}
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
            <DialogTitle>Activate Root Mode</DialogTitle>
            <DialogContent>
              <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                Root mode grants elevated privileges on system containers. All
                actions are audited at high severity.
              </Typography>
              <TextField
                label="Reason (min 10 chars)"
                fullWidth
                multiline
                rows={3}
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                required
                inputProps={{ minLength: 10 }}
                placeholder="Describe why you need root access…"
                sx={{ mb: 2 }}
              />
              <TextField
                label="Duration (seconds, 60–3600)"
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
                Cancel
              </Button>
              <Button type="submit" variant="contained" color="error">
                Activate
              </Button>
            </DialogActions>
          </form>
        </Dialog>

        {/* Page content — children manage their own scroll */}
        <Box
          sx={{
            flexGrow: 1,
            minHeight: 0,
            overflow: "hidden",
            display: "flex",
            flexDirection: "column",
            p: 3,
          }}
        >
          <Outlet context={{ rootModeActive: isRootActive }} />
        </Box>
      </Box>
    </Box>
  );
}
