/**
 * AdminSidebar — collapsible MUI Drawer with drag-and-drop nav reordering.
 */
import { useState } from "react";
import { NavLink, useLocation, useNavigate } from "react-router";
import { useTranslation } from "react-i18next";

import Box from "@mui/material/Box";
import Collapse from "@mui/material/Collapse";
import Divider from "@mui/material/Divider";
import Drawer from "@mui/material/Drawer";
import IconButton from "@mui/material/IconButton";
import List from "@mui/material/List";
import ListItem from "@mui/material/ListItem";
import ListItemButton from "@mui/material/ListItemButton";
import ListItemIcon from "@mui/material/ListItemIcon";
import ListItemText from "@mui/material/ListItemText";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";

import ChevronLeftIcon from "@mui/icons-material/ChevronLeft";
import DragIndicatorIcon from "@mui/icons-material/DragIndicator";
import ExpandLess from "@mui/icons-material/ExpandLess";
import ExpandMore from "@mui/icons-material/ExpandMore";
import RestartAltIcon from "@mui/icons-material/RestartAlt";

import AppBrand from "~/components/AppBrand";

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

import {
  DRAWER_WIDTH_OPEN,
  DRAWER_WIDTH_CLOSED,
  NAV_BY_ID,
  linkButtonSx,
  type NavEntry,
} from "~/lib/adminConstants";

/* ── DnD helper components ─────────────────────────────────────────── */

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
      sx={{
        position: "relative",
        "&:hover .nav-drag-handle": { opacity: 0.5 },
      }}
    >
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
        ...(isOver
          ? { bgcolor: "rgba(124,77,255,0.06)", borderRadius: 1 }
          : {}),
      }}
    >
      {children}
    </Box>
  );
}

/* ── Props ─────────────────────────────────────────────────────────── */

export interface AdminSidebarProps {
  drawerOpen: boolean;
  setDrawerOpen: (open: boolean | ((prev: boolean) => boolean)) => void;
  mainVisible: NavEntry[];
  pinnedVisible: NavEntry[];
  order: { mainIds: string[]; pinnedIds: string[] };
  setOrder: React.Dispatch<
    React.SetStateAction<{ mainIds: string[]; pinnedIds: string[] }>
  >;
  resetOrder: () => void;
}

export function AdminSidebar({
  drawerOpen,
  setDrawerOpen,
  mainVisible,
  pinnedVisible,
  order,
  setOrder,
  resetOrder,
}: AdminSidebarProps) {
  const location = useLocation();
  const navigate = useNavigate();
  const { t } = useTranslation();

  const currentDrawerWidth = drawerOpen
    ? DRAWER_WIDTH_OPEN
    : DRAWER_WIDTH_CLOSED;

  const mainVisibleIds = mainVisible.map((e) => e.id);
  const pinnedVisibleIds = pinnedVisible.map((e) => e.id);

  /* Expand/collapse per group */
  const [expanded, setExpanded] = useState<Record<string, boolean>>(() => {
    const init: Record<string, boolean> = {};
    for (const entry of mainVisible.concat(pinnedVisible)) {
      if (entry.kind === "group") {
        init[entry.labelKey] = entry.children.some((c) =>
          location.pathname.startsWith(c.to),
        );
      }
    }
    return init;
  });

  function toggleGroup(label: string) {
    setExpanded((prev) => ({ ...prev, [label]: !prev[label] }));
  }

  /* ── DnD ── */
  const [activeId, setActiveId] = useState<string | null>(null);
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 8 } }),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    }),
  );

  function findContainerForItem(itemId: string): "main" | "pinned" | null {
    if (order.mainIds.includes(itemId)) return "main";
    if (order.pinnedIds.includes(itemId)) return "pinned";
    return null;
  }

  function resolveContainer(id: string): "main" | "pinned" | null {
    return (
      findContainerForItem(id) ??
      (id === "droppable-main"
        ? "main"
        : id === "droppable-pinned"
          ? "pinned"
          : null)
    );
  }

  function handleDragStart(event: DragStartEvent) {
    setActiveId(event.active.id as string);
  }

  function handleDragOver(event: DragOverEvent) {
    const { active, over } = event;
    if (!over) return;
    const aId = active.id as string;
    const oId = over.id as string;
    const from = findContainerForItem(aId);
    const to = resolveContainer(oId);
    if (!from || !to || from === to) return;

    setOrder((prev) => {
      const fromKey = from === "main" ? "mainIds" : ("pinnedIds" as const);
      const toKey = to === "main" ? "mainIds" : ("pinnedIds" as const);
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

  function handleDragEnd(event: DragEndEvent) {
    setActiveId(null);
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const aId = active.id as string;
    const oId = over.id as string;
    const container = findContainerForItem(aId);
    if (!container || container !== findContainerForItem(oId)) return;
    const key = container === "main" ? "mainIds" : ("pinnedIds" as const);
    setOrder((prev) => {
      const list = [...prev[key]];
      const oldIndex = list.indexOf(aId);
      const newIndex = list.indexOf(oId);
      if (oldIndex === -1 || newIndex === -1) return prev;
      return { ...prev, [key]: arrayMove(list, oldIndex, newIndex) };
    });
  }

  /* ── Render a single nav entry ── */
  function renderNavEntry(entry: NavEntry) {
    if (entry.kind === "leaf") {
      return (
        <ListItem disablePadding sx={{ mb: 0.5 }}>
          <Tooltip
            title={drawerOpen ? "" : t(entry.labelKey)}
            placement="right"
            arrow
          >
            <ListItemButton
              component={NavLink}
              to={entry.to}
              data-tour-id={`sidebar.${entry.id}`}
              sx={{
                ...linkButtonSx,
                justifyContent: drawerOpen ? "initial" : "center",
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
                <ListItemText
                  primary={t(entry.labelKey)}
                  primaryTypographyProps={{
                    fontSize: "0.875rem",
                    fontWeight: 500,
                  }}
                />
              )}
            </ListItemButton>
          </Tooltip>
        </ListItem>
      );
    }

    const isGroupExpanded = expanded[entry.labelKey] ?? false;
    const isGroupActive = entry.children.some((c) =>
      location.pathname.startsWith(c.to),
    );
    const defaultTo = entry.children[0]?.to;

    return (
      <Box>
        <ListItem disablePadding sx={{ mb: 0.5 }}>
          <Tooltip
            title={drawerOpen ? "" : t(entry.labelKey)}
            placement="right"
            arrow
          >
            <ListItemButton
              component="a"
              href={defaultTo}
              data-tour-id={`sidebar.${entry.id}`}
              onClick={(e: React.MouseEvent<HTMLAnchorElement>) => {
                // Allow Ctrl/Meta/Shift click to open in new tab natively
                if (e.ctrlKey || e.metaKey || e.shiftKey || e.button !== 0)
                  return;
                e.preventDefault();
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
                    primaryTypographyProps={{
                      fontSize: "0.875rem",
                      fontWeight: 600,
                    }}
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
                    <ListItemIcon
                      sx={{ minWidth: 36, color: "text.secondary" }}
                    >
                      {child.icon}
                    </ListItemIcon>
                    <ListItemText
                      primary={t(child.labelKey)}
                      primaryTypographyProps={{
                        fontSize: "0.8rem",
                        fontWeight: 500,
                      }}
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

  return (
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
          onClick={() => setDrawerOpen((o: boolean) => !o)}
          sx={{
            width: 40,
            height: 40,
            borderRadius: 1.5,
            flexShrink: 0,
            p: 0.5,
            "&:hover": { bgcolor: "action.hover" },
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
        {/* Main section (scrollable) */}
        <DroppableZone
          id="droppable-main"
          sx={{
            flexGrow: 1,
            overflow: "auto",
            px: drawerOpen ? 1 : 0.5,
            mt: 0.5,
          }}
        >
          <SortableContext
            items={mainVisibleIds}
            strategy={verticalListSortingStrategy}
          >
            <List disablePadding>
              {mainVisible.map((entry) => (
                <SortableNavItem
                  key={entry.id}
                  id={entry.id}
                  disabled={!drawerOpen}
                  drawerOpen={drawerOpen}
                >
                  {renderNavEntry(entry)}
                </SortableNavItem>
              ))}
            </List>
          </SortableContext>
        </DroppableZone>

        {/* Divider */}
        {drawerOpen && pinnedVisible.length > 0 && (
          <Divider sx={{ mx: 1.5, my: 0.5 }}>
            <Typography
              variant="caption"
              color="text.disabled"
              sx={{
                fontSize: "0.6rem",
                textTransform: "uppercase",
                letterSpacing: 1,
                userSelect: "none",
              }}
            >
              {t("nav.pinned")}
            </Typography>
          </Divider>
        )}
        {!drawerOpen && pinnedVisible.length > 0 && (
          <Divider sx={{ mx: 0.5, my: 0.5 }} />
        )}

        {/* Pinned (bottom) section */}
        <DroppableZone
          id="droppable-pinned"
          sx={{ px: drawerOpen ? 1 : 0.5, pb: 0.5 }}
        >
          <SortableContext
            items={pinnedVisibleIds}
            strategy={verticalListSortingStrategy}
          >
            <List disablePadding>
              {pinnedVisible.map((entry) => (
                <SortableNavItem
                  key={entry.id}
                  id={entry.id}
                  disabled={!drawerOpen}
                  drawerOpen={drawerOpen}
                >
                  {renderNavEntry(entry)}
                </SortableNavItem>
              ))}
            </List>
          </SortableContext>
        </DroppableZone>

        {/* Drag overlay */}
        <DragOverlay dropAnimation={{ duration: 200, easing: "ease" }}>
          {activeId &&
            (() => {
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

      {/* Collapse / reset row */}
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
            <IconButton
              size="small"
              onClick={resetOrder}
              sx={{ color: "text.disabled" }}
            >
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
  );
}
