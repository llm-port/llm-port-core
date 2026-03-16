/**
 * ChatSidebar — session list grouped by project with project CRUD.
 * Supports drag-and-drop of sessions between projects via @dnd-kit.
 */
import { useState, useCallback } from "react";
import Box from "@mui/material/Box";
import Chip from "@mui/material/Chip";
import Drawer from "@mui/material/Drawer";
import List from "@mui/material/List";
import ListItemButton from "@mui/material/ListItemButton";
import ListItemText from "@mui/material/ListItemText";
import ListItemIcon from "@mui/material/ListItemIcon";
import ListSubheader from "@mui/material/ListSubheader";
import IconButton from "@mui/material/IconButton";
import Typography from "@mui/material/Typography";
import Tooltip from "@mui/material/Tooltip";
import Button from "@mui/material/Button";
import Divider from "@mui/material/Divider";
import TextField from "@mui/material/TextField";
import Menu from "@mui/material/Menu";
import MenuItem from "@mui/material/MenuItem";
import Collapse from "@mui/material/Collapse";
import { useTheme } from "@mui/material/styles";
import AddIcon from "@mui/icons-material/Add";
import AccountCircleIcon from "@mui/icons-material/AccountCircle";
import ChatBubbleOutlineIcon from "@mui/icons-material/ChatBubbleOutline";
import DeleteOutlineIcon from "@mui/icons-material/DeleteOutline";
import FolderIcon from "@mui/icons-material/Folder";
import CreateNewFolderIcon from "@mui/icons-material/CreateNewFolder";
import LogoutIcon from "@mui/icons-material/Logout";
import ManageAccountsIcon from "@mui/icons-material/ManageAccounts";
import MenuIcon from "@mui/icons-material/Menu";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import ExpandLessIcon from "@mui/icons-material/ExpandLess";
import SettingsOutlinedIcon from "@mui/icons-material/SettingsOutlined";
import DriveFileMoveOutlinedIcon from "@mui/icons-material/DriveFileMoveOutlined";
import { useTranslation } from "react-i18next";
import {
  DndContext,
  DragOverlay,
  PointerSensor,
  useSensor,
  useSensors,
  useDroppable,
  useDraggable,
  closestCenter,
  type DragEndEvent,
  type DragStartEvent,
} from "@dnd-kit/core";

import type { ChatSession, ChatProject } from "~/api/chatTypes";
import ProjectSettingsDialog from "./ProjectSettingsDialog";

interface Props {
  open: boolean;
  onToggle: () => void;
  sessions: ChatSession[];
  projects: ChatProject[];
  activeSessionId?: string;
  onNewChat: () => void;
  onSelectSession: (id: string) => void;
  onDeleteSession: (id: string) => void;
  onCreateProject: (name: string) => Promise<ChatProject>;
  onDeleteProject: (id: string) => void;
  onUpdateProject: (
    id: string,
    updates: {
      name?: string;
      description?: string;
      system_instructions?: string;
    },
  ) => Promise<void>;
  onMoveSession: (sessionId: string, projectId: string | null) => void;
  width: number;
  isMobile: boolean;
  // User
  currentUserEmail: string;
  onLogout: () => void;
  onProfileOpen: () => void;
}

export default function ChatSidebar({
  open,
  onToggle,
  sessions,
  projects,
  activeSessionId,
  onNewChat,
  onSelectSession,
  onDeleteSession,
  onCreateProject,
  onDeleteProject,
  onUpdateProject,
  onMoveSession,
  width,
  isMobile,
  currentUserEmail,
  onLogout,
  onProfileOpen,
}: Props) {
  const { t } = useTranslation();
  const theme = useTheme();

  const [creatingProject, setCreatingProject] = useState(false);
  const [newProjectName, setNewProjectName] = useState("");
  const [collapsedProjects, setCollapsedProjects] = useState<Set<string>>(
    new Set(),
  );
  // User dropdown menu state
  const [userMenuAnchor, setUserMenuAnchor] = useState<HTMLElement | null>(
    null,
  );
  // Move-to-project menu state
  const [moveAnchor, setMoveAnchor] = useState<{
    el: HTMLElement;
    sessionId: string;
  } | null>(null);
  // Project settings dialog state
  const [editingProject, setEditingProject] = useState<ChatProject | null>(
    null,
  );
  // Drag-and-drop state
  const [activeDragId, setActiveDragId] = useState<string | null>(null);
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
  );
  const activeDragSession = activeDragId
    ? sessions.find((s) => s.id === activeDragId)
    : null;

  const handleDragStart = useCallback((event: DragStartEvent) => {
    setActiveDragId(String(event.active.id));
  }, []);

  const handleDragEnd = useCallback(
    (event: DragEndEvent) => {
      setActiveDragId(null);
      const { active, over } = event;
      if (!over) return;
      const sessionId = String(active.id);
      const targetId = String(over.id);
      // Determine the target project — "__no_project__" means remove from project
      const projectId = targetId === "__no_project__" ? null : targetId;
      // Find the session being dragged to check if it's already in the target
      const session = sessions.find((s) => s.id === sessionId);
      if (!session) return;
      const currentProjectId = session.project_id ?? null;
      if (currentProjectId === projectId) return; // No change
      onMoveSession(sessionId, projectId);
    },
    [sessions, onMoveSession],
  );

  // Partition sessions by project
  const orphanSessions = sessions.filter((s) => !s.project_id);
  const sessionsByProject = new Map<string, ChatSession[]>();
  for (const s of sessions) {
    if (s.project_id) {
      const list = sessionsByProject.get(s.project_id) ?? [];
      list.push(s);
      sessionsByProject.set(s.project_id, list);
    }
  }

  const toggleProject = (id: string) => {
    setCollapsedProjects((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const handleCreateProject = async () => {
    const name = newProjectName.trim();
    if (!name) return;
    await onCreateProject(name);
    setNewProjectName("");
    setCreatingProject(false);
  };

  const drawerContent = (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        bgcolor: theme.palette.background.default,
      }}
    >
      {/* Header */}
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          px: 1.5,
          py: 1,
          minHeight: 56,
        }}
      >
        <Typography variant="h6" noWrap sx={{ fontSize: "1rem" }}>
          {t("chat.title", { defaultValue: "Chat" })}
        </Typography>
        <Box>
          <Tooltip
            title={t("chat.new_project", { defaultValue: "New project" })}
          >
            <IconButton size="small" onClick={() => setCreatingProject(true)}>
              <CreateNewFolderIcon fontSize="small" />
            </IconButton>
          </Tooltip>
          <Tooltip title={t("chat.new_chat", { defaultValue: "New chat" })}>
            <IconButton size="small" onClick={onNewChat}>
              <AddIcon fontSize="small" />
            </IconButton>
          </Tooltip>
          {isMobile && (
            <IconButton size="small" onClick={onToggle}>
              <MenuIcon fontSize="small" />
            </IconButton>
          )}
        </Box>
      </Box>
      <Divider />

      {/* New-project inline form */}
      {creatingProject && (
        <Box sx={{ px: 1.5, py: 1 }}>
          <TextField
            autoFocus
            fullWidth
            size="small"
            placeholder={t("chat.project_name", {
              defaultValue: "Project name",
            })}
            value={newProjectName}
            onChange={(e) => setNewProjectName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") handleCreateProject();
              if (e.key === "Escape") setCreatingProject(false);
            }}
          />
          <Box
            sx={{
              display: "flex",
              gap: 0.5,
              mt: 0.5,
              justifyContent: "flex-end",
            }}
          >
            <Button size="small" onClick={() => setCreatingProject(false)}>
              {t("common.cancel", { defaultValue: "Cancel" })}
            </Button>
            <Button
              size="small"
              variant="contained"
              onClick={handleCreateProject}
              disabled={!newProjectName.trim()}
            >
              {t("common.create", { defaultValue: "Create" })}
            </Button>
          </Box>
        </Box>
      )}

      {/* Session list with drag-and-drop */}
      <Box sx={{ flex: 1, overflowY: "auto", overflowX: "hidden" }}>
        <DndContext
          sensors={sensors}
          collisionDetection={closestCenter}
          onDragStart={handleDragStart}
          onDragEnd={handleDragEnd}
        >
          {/* Projects */}
          {projects.map((proj) => {
            const collapsed = collapsedProjects.has(proj.id);
            const projSessions = sessionsByProject.get(proj.id) ?? [];

            return (
              <DroppableProjectZone
                key={proj.id}
                projectId={proj.id}
                isOver={false}
              >
                <ListSubheader
                  sx={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    bgcolor: "transparent",
                    cursor: "pointer",
                    lineHeight: "32px",
                    px: 1.5,
                  }}
                  onClick={() => toggleProject(proj.id)}
                >
                  <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
                    <FolderIcon sx={{ fontSize: 16 }} />
                    <Typography variant="caption" fontWeight={600} noWrap>
                      {proj.name}
                    </Typography>
                  </Box>
                  <Box>
                    {collapsed ? (
                      <ExpandMoreIcon sx={{ fontSize: 16 }} />
                    ) : (
                      <ExpandLessIcon sx={{ fontSize: 16 }} />
                    )}
                    <IconButton
                      size="small"
                      onClick={(e) => {
                        e.stopPropagation();
                        setEditingProject(proj);
                      }}
                      sx={{ ml: 0.5 }}
                    >
                      <SettingsOutlinedIcon sx={{ fontSize: 14 }} />
                    </IconButton>
                    <IconButton
                      size="small"
                      onClick={(e) => {
                        e.stopPropagation();
                        onDeleteProject(proj.id);
                      }}
                      sx={{ ml: 0.5 }}
                    >
                      <DeleteOutlineIcon sx={{ fontSize: 14 }} />
                    </IconButton>
                  </Box>
                </ListSubheader>
                <Collapse in={!collapsed}>
                  <List dense disablePadding>
                    {projSessions.map((s) => (
                      <DraggableSessionItem
                        key={s.id}
                        session={s}
                        active={s.id === activeSessionId}
                        onSelect={onSelectSession}
                        onDelete={onDeleteSession}
                        onMoveClick={(el) =>
                          setMoveAnchor({ el, sessionId: s.id })
                        }
                      />
                    ))}
                  </List>
                </Collapse>
              </DroppableProjectZone>
            );
          })}

          {/* Unorganised sessions */}
          {orphanSessions.length > 0 && (
            <DroppableProjectZone projectId="__no_project__" isOver={false}>
              {projects.length > 0 && <Divider sx={{ my: 0.5 }} />}
              <List dense disablePadding>
                {orphanSessions.map((s) => (
                  <DraggableSessionItem
                    key={s.id}
                    session={s}
                    active={s.id === activeSessionId}
                    onSelect={onSelectSession}
                    onDelete={onDeleteSession}
                    onMoveClick={(el) => setMoveAnchor({ el, sessionId: s.id })}
                  />
                ))}
              </List>
            </DroppableProjectZone>
          )}

          {/* Also allow dropping onto orphan zone when it's empty */}
          {orphanSessions.length === 0 && projects.length > 0 && (
            <DroppableProjectZone projectId="__no_project__" isOver={false}>
              <Box sx={{ px: 1.5, py: 1 }}>
                <Typography variant="caption" color="text.secondary">
                  {t("chat.drop_here_no_project", {
                    defaultValue: "Drop here to remove from project",
                  })}
                </Typography>
              </Box>
            </DroppableProjectZone>
          )}

          {/* Drag overlay — floating ghost */}
          <DragOverlay dropAnimation={null}>
            {activeDragSession ? (
              <Box
                sx={{
                  bgcolor: "action.selected",
                  borderRadius: 1,
                  px: 1.5,
                  py: 0.5,
                  display: "flex",
                  alignItems: "center",
                  gap: 0.5,
                  opacity: 0.9,
                  boxShadow: 3,
                }}
              >
                <ChatBubbleOutlineIcon sx={{ fontSize: 16 }} />
                <Typography variant="body2" noWrap sx={{ fontSize: "0.85rem" }}>
                  {activeDragSession.title ||
                    t("chat.untitled", { defaultValue: "Untitled chat" })}
                </Typography>
              </Box>
            ) : null}
          </DragOverlay>
        </DndContext>
      </Box>

      {/* Bottom user section */}
      <Divider />
      <Box
        sx={{
          px: 1.5,
          py: 1,
          display: "flex",
          alignItems: "center",
        }}
      >
        <Chip
          icon={<AccountCircleIcon />}
          label={currentUserEmail}
          variant="outlined"
          size="small"
          onClick={(e) => setUserMenuAnchor(e.currentTarget)}
          sx={{
            cursor: "pointer",
            maxWidth: "100%",
            "& .MuiChip-label": {
              overflow: "hidden",
              textOverflow: "ellipsis",
            },
          }}
        />
      </Box>

      {/* User dropdown menu */}
      <Menu
        anchorEl={userMenuAnchor}
        open={Boolean(userMenuAnchor)}
        onClose={() => setUserMenuAnchor(null)}
        anchorOrigin={{ vertical: "top", horizontal: "left" }}
        transformOrigin={{ vertical: "bottom", horizontal: "left" }}
        slotProps={{ paper: { sx: { minWidth: 180 } } }}
      >
        <MenuItem
          onClick={() => {
            setUserMenuAnchor(null);
            onProfileOpen();
          }}
        >
          <ListItemIcon>
            <ManageAccountsIcon fontSize="small" />
          </ListItemIcon>
          <ListItemText>
            {t("profile.manage_profile", { defaultValue: "Profile" })}
          </ListItemText>
        </MenuItem>
        <MenuItem
          onClick={() => {
            setUserMenuAnchor(null);
            onLogout();
          }}
        >
          <ListItemIcon>
            <LogoutIcon fontSize="small" />
          </ListItemIcon>
          <ListItemText>
            {t("profile.logout", { defaultValue: "Logout" })}
          </ListItemText>
        </MenuItem>
      </Menu>

      {/* Move-to-project popover menu */}
      <Menu
        anchorEl={moveAnchor?.el}
        open={!!moveAnchor}
        onClose={() => setMoveAnchor(null)}
      >
        <MenuItem
          dense
          onClick={() => {
            if (moveAnchor) onMoveSession(moveAnchor.sessionId, null);
            setMoveAnchor(null);
          }}
        >
          <Typography variant="body2" color="text.secondary">
            {t("chat.no_project", { defaultValue: "No project" })}
          </Typography>
        </MenuItem>
        {projects.map((p) => (
          <MenuItem
            key={p.id}
            dense
            onClick={() => {
              if (moveAnchor) onMoveSession(moveAnchor.sessionId, p.id);
              setMoveAnchor(null);
            }}
          >
            <ListItemIcon sx={{ minWidth: 28 }}>
              <FolderIcon fontSize="small" />
            </ListItemIcon>
            <ListItemText primary={p.name} />
          </MenuItem>
        ))}
      </Menu>

      {/* Project settings dialog */}
      <ProjectSettingsDialog
        open={!!editingProject}
        project={editingProject}
        onClose={() => setEditingProject(null)}
        onSave={onUpdateProject}
      />
    </Box>
  );

  // Mobile: temporary drawer. Desktop: persistent.
  if (isMobile) {
    return (
      <Drawer
        variant="temporary"
        open={open}
        onClose={onToggle}
        sx={{ "& .MuiDrawer-paper": { width } }}
      >
        {drawerContent}
      </Drawer>
    );
  }

  return (
    <Drawer
      variant="persistent"
      open={open}
      sx={{
        "& .MuiDrawer-paper": {
          width,
          borderRight: `1px solid ${theme.palette.divider}`,
          boxSizing: "border-box",
        },
      }}
    >
      {drawerContent}
    </Drawer>
  );
}

// ── DroppableProjectZone (internal) ──────────────────────────────

function DroppableProjectZone({
  projectId,
  children,
}: {
  projectId: string;
  isOver: boolean;
  children: React.ReactNode;
}) {
  const { setNodeRef, isOver: isOverCurrent } = useDroppable({
    id: projectId,
  });
  const theme = useTheme();

  return (
    <Box
      ref={setNodeRef}
      sx={{
        transition: "background-color 150ms ease",
        bgcolor: isOverCurrent ? theme.palette.action.hover : "transparent",
        borderRadius: 1,
        ...(isOverCurrent && {
          outline: `2px dashed ${theme.palette.primary.main}`,
          outlineOffset: -2,
        }),
      }}
    >
      {children}
    </Box>
  );
}

// ── DraggableSessionItem (internal) ─────────────────────────────

function DraggableSessionItem({
  session,
  active,
  onSelect,
  onDelete,
  onMoveClick,
}: {
  session: ChatSession;
  active: boolean;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  onMoveClick: (el: HTMLElement) => void;
}) {
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id: session.id,
  });

  return (
    <Box
      ref={setNodeRef}
      {...listeners}
      {...attributes}
      sx={{ opacity: isDragging ? 0.4 : 1 }}
    >
      <SessionItem
        session={session}
        active={active}
        onSelect={onSelect}
        onDelete={onDelete}
        onMoveClick={onMoveClick}
      />
    </Box>
  );
}

// ── SessionItem (internal) ───────────────────────────────────────

function SessionItem({
  session,
  active,
  onSelect,
  onDelete,
  onMoveClick,
}: {
  session: ChatSession;
  active: boolean;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  onMoveClick: (el: HTMLElement) => void;
}) {
  const { t } = useTranslation();
  const [hovered, setHovered] = useState(false);

  return (
    <ListItemButton
      selected={active}
      onClick={() => onSelect(session.id)}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      sx={{ pl: session.project_id ? 3 : 1.5, pr: 1 }}
    >
      <ListItemIcon sx={{ minWidth: 28 }}>
        <ChatBubbleOutlineIcon sx={{ fontSize: 16 }} />
      </ListItemIcon>
      <ListItemText
        primary={
          session.title || t("chat.untitled", { defaultValue: "Untitled chat" })
        }
        primaryTypographyProps={{
          noWrap: true,
          fontSize: "0.85rem",
        }}
      />
      {hovered && (
        <Box sx={{ display: "flex", ml: 0.5 }}>
          <Tooltip
            title={t("chat.move_to_project", {
              defaultValue: "Move to project",
            })}
          >
            <IconButton
              size="small"
              onClick={(e) => {
                e.stopPropagation();
                onMoveClick(e.currentTarget);
              }}
            >
              <DriveFileMoveOutlinedIcon sx={{ fontSize: 14 }} />
            </IconButton>
          </Tooltip>
          <Tooltip title={t("common.delete", { defaultValue: "Delete" })}>
            <IconButton
              size="small"
              onClick={(e) => {
                e.stopPropagation();
                onDelete(session.id);
              }}
            >
              <DeleteOutlineIcon sx={{ fontSize: 14 }} />
            </IconButton>
          </Tooltip>
        </Box>
      )}
    </ListItemButton>
  );
}
