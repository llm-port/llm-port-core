import { type RouteConfig, index, layout, route } from "@react-router/dev/routes";

export default [
  index("routes/home.tsx"),
  route("/login", "routes/login.tsx"),
  layout("routes/admin.tsx", [
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
    route("/admin/llm/providers", "pages/admin/llm/ProvidersPage.tsx"),
    route("/admin/llm/models", "pages/admin/llm/ModelsPage.tsx"),
    route("/admin/llm/models/:id", "pages/admin/llm/ModelDetailPage.tsx"),
    route("/admin/llm/runtimes", "pages/admin/llm/RuntimesPage.tsx"),
    route("/admin/llm/runtimes/:id", "pages/admin/llm/RuntimeDetailPage.tsx"),
    route("/admin/llm/jobs", "pages/admin/llm/JobsPage.tsx"),
  ]),
] satisfies RouteConfig;
