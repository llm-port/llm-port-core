/**
 * ChatPage — main standalone chat page with sidebar, welcome, and chat window.
 *
 * Route: /chat (welcome) and /chat/:sessionId (active session).
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams, useOutletContext } from "react-router";
import Box from "@mui/material/Box";
import Dialog from "@mui/material/Dialog";
import DialogTitle from "@mui/material/DialogTitle";
import DialogContent from "@mui/material/DialogContent";
import IconButton from "@mui/material/IconButton";
import CloseIcon from "@mui/icons-material/Close";
import useMediaQuery from "@mui/material/useMediaQuery";
import { useTheme } from "@mui/material/styles";

import Drawer from "@mui/material/Drawer";
import Tab from "@mui/material/Tab";
import Tabs from "@mui/material/Tabs";
import { chatApi, type ChatSession, type ChatProject } from "~/api/chatClient";
import { auth, type AuthUser } from "~/api/auth";
import { listLanguages, type UiLanguage } from "~/api/i18n";
import { clearCachedAccess } from "~/lib/adminConstants";
import { patchSessionToolPolicy } from "~/api/tools";
import type { InitialMessageState } from "./components/ChatWelcome";
import ChatSidebar from "./components/ChatSidebar";
import ChatWelcome from "./components/ChatWelcome";
import ChatWindow from "./components/ChatWindow";
import ExecutionModeSelector from "./components/ExecutionModeSelector";
import ToolPanel from "./components/ToolPanel";
import PiiPanel from "./components/PiiPanel";
import { useToolPolicy } from "./hooks/useToolPolicy";
import { useSessionPiiPolicy } from "./hooks/useSessionPiiPolicy";
import ProfilePage from "../admin/ProfilePage";

const SIDEBAR_WIDTH = 260;
const SIDEBAR_COLLAPSED_WIDTH = 0;

export default function ChatPage() {
  const { sessionId } = useParams<{ sessionId?: string }>();
  const { user, permissions } = useOutletContext<{
    user: AuthUser;
    permissions: Set<string>;
  }>();
  const canDebug = permissions.has("*") || permissions.has("chat.debug:read");
  const navigate = useNavigate();
  const theme = useTheme();
  const isMobile = useMediaQuery(theme.breakpoints.down("md"));

  // State
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [projects, setProjects] = useState<ChatProject[]>([]);
  const [sidebarOpen, setSidebarOpen] = useState(!isMobile);
  const [selectedModel, setSelectedModel] = useState<string>("");
  const [languages, setLanguages] = useState<UiLanguage[]>([]);
  const [language, setLanguage] = useState<string>("en");
  const [profileOpen, setProfileOpen] = useState(false);
  const [toolDrawerOpen, setToolDrawerOpen] = useState(false);
  const [drawerTab, setDrawerTab] = useState(0);
  const { executionMode, setExecutionMode } = useToolPolicy(sessionId ?? null);
  const {
    policy: piiPolicy,
    loading: piiLoading,
    error: piiError,
    refresh: piiRefresh,
    updateOverride: piiUpdate,
    clearOverride: piiClear,
  } = useSessionPiiPolicy(sessionId ?? null);
  const [localToolOverrides, setLocalToolOverrides] = useState<
    Map<string, boolean>
  >(new Map());

  const handleLocalToolOverride = useCallback(
    (toolId: string, enabled: boolean) => {
      setLocalToolOverrides((prev) => new Map(prev).set(toolId, enabled));
    },
    [],
  );

  // Refs
  const loadedRef = useRef(false);

  // Load sidebar data
  const refreshSidebar = useCallback(async () => {
    try {
      const [sess, proj] = await Promise.all([
        chatApi.listSessions(),
        chatApi.listProjects(),
      ]);
      setSessions(sess);
      setProjects(proj);
    } catch {
      // Gateway may not be available yet
    }
  }, []);

  useEffect(() => {
    if (!loadedRef.current) {
      loadedRef.current = true;
      refreshSidebar();
      listLanguages()
        .then(setLanguages)
        .catch(() => {});
    }
  }, [refreshSidebar]);

  // Close mobile sidebar on navigation
  useEffect(() => {
    if (isMobile) setSidebarOpen(false);
  }, [sessionId, isMobile]);

  // Handlers
  const handleNewChat = useCallback(() => {
    navigate("/chat");
  }, [navigate]);

  const handleSelectSession = useCallback(
    (id: string) => navigate(`/chat/${id}`),
    [navigate],
  );

  const handleSessionCreated = useCallback(
    async (sess: ChatSession, initialState?: InitialMessageState) => {
      setSessions((prev) => [sess, ...prev]);
      // Apply pre-session execution mode and tool overrides to the new session
      const hasOverrides = localToolOverrides.size > 0;
      if (executionMode !== "server_only" || hasOverrides) {
        try {
          const patch: Record<string, unknown> = {};
          if (executionMode !== "server_only") {
            patch.execution_mode = executionMode;
          }
          if (hasOverrides) {
            patch.tool_overrides = Array.from(localToolOverrides.entries()).map(
              ([tool_id, enabled]) => ({ tool_id, enabled }),
            );
          }
          await patchSessionToolPolicy(sess.id, patch);
        } catch {
          // Best-effort — session still works with defaults
        }
      }
      // Clear local overrides now that they're persisted
      setLocalToolOverrides(new Map());
      navigate(`/chat/${sess.id}`, { state: initialState });
    },
    [navigate, executionMode, localToolOverrides],
  );

  const handleDeleteSession = useCallback(
    async (id: string) => {
      await chatApi.deleteSession(id);
      setSessions((prev) => prev.filter((s) => s.id !== id));
      if (sessionId === id) navigate("/chat");
    },
    [sessionId, navigate],
  );

  const handleCreateProject = useCallback(async (name: string) => {
    const proj = await chatApi.createProject({ name });
    setProjects((prev) => [...prev, proj]);
    return proj;
  }, []);

  const handleDeleteProject = useCallback(
    async (id: string) => {
      await chatApi.deleteProject(id);
      setProjects((prev) => prev.filter((p) => p.id !== id));
      await refreshSidebar();
    },
    [refreshSidebar],
  );

  const handleMoveSession = useCallback(
    async (sessId: string, projectId: string | null) => {
      await chatApi.updateSession(sessId, {
        project_id: projectId ?? undefined,
      });
      setSessions((prev) =>
        prev.map((s) =>
          s.id === sessId ? { ...s, project_id: projectId } : s,
        ),
      );
    },
    [],
  );

  const handleUpdateProject = useCallback(
    async (
      id: string,
      updates: {
        name?: string;
        description?: string;
        system_instructions?: string;
      },
    ) => {
      await chatApi.updateProject(id, updates);
      setProjects((prev) =>
        prev.map((p) => (p.id === id ? { ...p, ...updates } : p)),
      );
    },
    [],
  );

  const handleLogout = useCallback(async () => {
    await auth.logout();
    clearCachedAccess();
    navigate("/");
  }, [navigate]);

  const handleLanguageChange = useCallback((code: string) => {
    setLanguage(code);
  }, []);

  const handleSessionUpdated = useCallback((updated: ChatSession) => {
    setSessions((prev) => prev.map((s) => (s.id === updated.id ? updated : s)));
  }, []);

  const sidebarWidth = sidebarOpen ? SIDEBAR_WIDTH : SIDEBAR_COLLAPSED_WIDTH;

  return (
    <Box sx={{ display: "flex", height: "100vh", overflow: "hidden" }}>
      {/* Sidebar */}
      <ChatSidebar
        open={sidebarOpen}
        onToggle={() => setSidebarOpen((v) => !v)}
        sessions={sessions}
        projects={projects}
        activeSessionId={sessionId}
        onNewChat={handleNewChat}
        onSelectSession={handleSelectSession}
        onDeleteSession={handleDeleteSession}
        onCreateProject={handleCreateProject}
        onDeleteProject={handleDeleteProject}
        onUpdateProject={handleUpdateProject}
        onMoveSession={handleMoveSession}
        width={SIDEBAR_WIDTH}
        isMobile={isMobile}
        currentUserEmail={user.email}
        onLogout={handleLogout}
        onProfileOpen={() => setProfileOpen(true)}
      />

      {/* Main content */}
      <Box
        sx={{
          flex: 1,
          display: "flex",
          flexDirection: "column",
          height: "100vh",
          minWidth: 0,
          ml: isMobile ? 0 : `${sidebarWidth}px`,
          transition: "margin-left 0.2s ease",
        }}
      >
        {sessionId ? (
          <ChatWindow
            key={sessionId}
            sessionId={sessionId}
            selectedModel={selectedModel}
            onModelChange={setSelectedModel}
            onSessionCreated={handleSessionCreated}
            onSessionUpdated={handleSessionUpdated}
            onToggleSidebar={() => setSidebarOpen((v) => !v)}
            sidebarOpen={sidebarOpen}
            languages={languages}
            language={language}
            onLanguageChange={handleLanguageChange}
            isSuperuser={user.is_superuser}
            canDebug={canDebug}
            onToolsToggle={() => setToolDrawerOpen((o) => !o)}
            toolsOpen={toolDrawerOpen}
          />
        ) : (
          <ChatWelcome
            selectedModel={selectedModel}
            onModelChange={setSelectedModel}
            onSessionCreated={handleSessionCreated}
            onToggleSidebar={() => setSidebarOpen((v) => !v)}
            sidebarOpen={sidebarOpen}
            languages={languages}
            language={language}
            onLanguageChange={handleLanguageChange}
            isSuperuser={user.is_superuser}
            onToolsToggle={() => setToolDrawerOpen((o) => !o)}
            toolsOpen={toolDrawerOpen}
          />
        )}

        {/* Tool routing drawer — shared across welcome + active session */}
        <Drawer
          anchor="right"
          open={toolDrawerOpen}
          onClose={() => setToolDrawerOpen(false)}
          variant="temporary"
          ModalProps={{ keepMounted: true }}
          PaperProps={{
            sx: {
              width: 320,
              mt: "56px",
              height: "calc(100% - 56px)",
              display: "flex",
              flexDirection: "column",
            },
          }}
          slotProps={{ backdrop: { sx: { backgroundColor: "transparent" } } }}
        >
          <Tabs
            value={drawerTab}
            onChange={(_, v) => setDrawerTab(v)}
            variant="fullWidth"
            sx={{ borderBottom: 1, borderColor: "divider", flexShrink: 0 }}
          >
            <Tab label="Tools" />
            <Tab label="Privacy" />
          </Tabs>

          {drawerTab === 0 && (
            <Box sx={{ flex: 1, overflow: "auto" }}>
              <Box sx={{ p: 1.5 }}>
                <ExecutionModeSelector
                  sessionId={sessionId ?? null}
                  value={executionMode}
                  onChange={setExecutionMode}
                />
              </Box>
              <ToolPanel
                sessionId={sessionId ?? null}
                executionMode={executionMode}
                localOverrides={localToolOverrides}
                onLocalOverride={handleLocalToolOverride}
              />
            </Box>
          )}

          {drawerTab === 1 && (
            <Box sx={{ flex: 1, overflow: "auto" }}>
              <PiiPanel
                policy={piiPolicy}
                loading={piiLoading}
                error={piiError}
                onUpdate={piiUpdate}
                onClear={piiClear}
                onRefresh={piiRefresh}
              />
            </Box>
          )}
        </Drawer>
      </Box>

      {/* Profile overlay */}
      <Dialog
        open={profileOpen}
        onClose={() => setProfileOpen(false)}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle
          sx={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
          }}
        >
          {""}
          <IconButton size="small" onClick={() => setProfileOpen(false)}>
            <CloseIcon />
          </IconButton>
        </DialogTitle>
        <DialogContent>
          <ProfilePage />
        </DialogContent>
      </Dialog>
    </Box>
  );
}
