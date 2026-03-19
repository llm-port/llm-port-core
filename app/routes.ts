import {
  type RouteConfig,
  index,
  layout,
  route,
} from "@react-router/dev/routes";

export default [
  index("routes/home.tsx"),
  route("/login", "routes/login.tsx"),
  route("/forgot-password", "routes/forgot-password.tsx"),
  route("/reset-password", "routes/reset-password.tsx"),
  // Standalone chat UI
  layout("routes/chat.tsx", [
    route("/chat", "pages/chat/ChatPage.tsx"),
    route("/chat/:sessionId", "pages/chat/ChatPage.tsx", {
      id: "chat-session",
    }),
  ]),
  layout("routes/admin.tsx", [
    route("/admin", "pages/admin/AdminRouteRedirectPage.tsx"),
    route("/admin/dashboard", "pages/admin/DashboardPage.tsx"),
    route("/admin/containers", "pages/admin/ContainersPage.tsx"),
    route("/admin/containers/new", "pages/admin/CreateContainerPage.tsx"),
    route("/admin/containers/:id", "pages/admin/ContainerDetailPage.tsx"),
    route("/admin/images", "pages/admin/ImagesPage.tsx"),
    route("/admin/networks", "pages/admin/NetworksPage.tsx"),
    route("/admin/stacks", "pages/admin/StacksPage.tsx"),
    route("/admin/logs", "pages/admin/LogsPage.tsx"),
    route("/admin/audit", "pages/admin/AuditRedirectPage.tsx"),
    route("/admin/settings", "pages/admin/SettingsPage.tsx"),
    route("/admin/users", "pages/admin/UsersPage.tsx"),
    route("/admin/roles", "pages/admin/RolesPage.tsx"),
    route("/admin/groups", "pages/admin/GroupsPage.tsx"),
    route("/admin/auth-providers", "pages/admin/AuthProvidersPage.tsx"),
    route("/admin/llm/providers", "pages/admin/llm/ProvidersPage.tsx"),
    route("/admin/llm/models", "pages/admin/llm/ModelsPage.tsx"),
    route("/admin/llm/models/:id", "pages/admin/llm/ModelDetailPage.tsx"),
    route("/admin/llm/runtimes", "pages/admin/llm/RuntimesPage.tsx"),
    route("/admin/llm/runtimes/:id", "pages/admin/llm/RuntimeDetailPage.tsx"),
    route("/admin/nodes", "pages/admin/nodes/NodeFleetPage.tsx"),
    route("/admin/nodes/:id", "pages/admin/nodes/NodeDetailPage.tsx"),
    route("/admin/scheduler", "pages/admin/SchedulerPage.tsx"),
    route("/admin/llm/agent-trace", "pages/admin/llm/GraphPage.tsx"),
    route("/admin/llm/endpoint", "pages/admin/agents/ApiDocsPage.tsx"),
    // PII routes — guarded by module status
    layout("routes/pii-guard.tsx", [
      route("/admin/pii/dashboard", "pages/admin/pii/PIIDashboardPage.tsx"),
      route("/admin/pii/activity", "pages/admin/pii/PIIActivityLogPage.tsx"),
      route("/admin/pii/policies", "pages/admin/pii/PIITenantPoliciesPage.tsx"),
    ]),
    // Security map
    route("/admin/security-map", "pages/admin/SecurityMapPage.tsx"),
    // User profile
    route("/admin/profile", "pages/admin/ProfilePage.tsx"),
    // Chat routes — guarded by the chat-guard layout that checks module status
    layout("routes/chat-guard.tsx", [
      route("/admin/chat/projects", "pages/admin/chat/ChatProjectsPage.tsx"),
      route("/admin/chat/sessions", "pages/admin/chat/ChatSessionsPage.tsx"),
      route(
        "/admin/chat/attachments",
        "pages/admin/chat/ChatAttachmentsPage.tsx",
      ),
    ]),
    // RAG routes — guarded by the rag-guard layout that checks module status
    layout("routes/rag-guard.tsx", [
      route("/admin/rag/runtime", "pages/admin/rag/RagRuntimeConfigPage.tsx"),
      route("/admin/rag/collectors", "pages/admin/rag/RagCollectorsPage.tsx"),
      route("/admin/rag/explorer", "pages/admin/rag/RagExplorerPage.tsx"),
      route("/admin/rag/publishes", "pages/admin/rag/RagPublishesPage.tsx"),
      route("/admin/rag/search", "pages/admin/rag/RagSearchPage.tsx"),
      // RAG Lite
      route(
        "/admin/rag/knowledge-base",
        "pages/admin/rag/RagLiteKnowledgeBasePage.tsx",
      ),
      route("/admin/rag/documents", "pages/admin/rag/RagLiteDocumentsPage.tsx"),
      route(
        "/admin/rag/collections",
        "pages/admin/rag/RagLiteCollectionsPage.tsx",
      ),
    ]),
    // MCP routes — guarded by module status
    layout("routes/mcp-guard.tsx", [
      route("/admin/mcp/servers", "pages/admin/mcp/MCPServersPage.tsx"),
      route(
        "/admin/mcp/servers/:id",
        "pages/admin/mcp/MCPServerDetailPage.tsx",
      ),
    ]),
    // Skills routes — guarded by module status
    layout("routes/skills-guard.tsx", [
      route("/admin/skills", "pages/admin/skills/SkillsListPage.tsx"),
      route("/admin/skills/create", "pages/admin/skills/CreateSkillPage.tsx"),
      route("/admin/skills/:id", "pages/admin/skills/SkillDetailPage.tsx"),
    ]),
  ]),
] satisfies RouteConfig;
