import { type RouteConfig, index, layout, route } from "@react-router/dev/routes";

export default [
  index("routes/home.tsx"),
  route("/login", "routes/login.tsx"),
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
    route("/admin/llm/jobs", "pages/admin/llm/JobsPage.tsx"),
    route("/admin/llm/agent-trace", "pages/admin/llm/GraphPage.tsx"),
    route("/admin/llm/endpoint", "pages/admin/agents/ApiDocsPage.tsx"),
    // PII dashboard routes
    route("/admin/pii/dashboard", "pages/admin/pii/PIIDashboardPage.tsx"),
    route("/admin/pii/activity", "pages/admin/pii/PIIActivityLogPage.tsx"),
    // RAG routes — guarded by the rag-guard layout that checks module status
    layout("routes/rag-guard.tsx", [
      route("/admin/rag/runtime", "pages/admin/rag/RagRuntimeConfigPage.tsx"),
      route("/admin/rag/collectors", "pages/admin/rag/RagCollectorsPage.tsx"),
      route("/admin/rag/explorer", "pages/admin/rag/RagExplorerPage.tsx"),
      route("/admin/rag/publishes", "pages/admin/rag/RagPublishesPage.tsx"),
      route("/admin/rag/search", "pages/admin/rag/RagSearchPage.tsx"),
    ]),
  ]),
] satisfies RouteConfig;
