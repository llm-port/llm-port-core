import { type RouteConfig, index, layout, route } from "@react-router/dev/routes";

export default [
  index("routes/home.tsx"),
  layout("routes/admin.tsx", [
    route("/admin/containers", "routes/admin.containers.tsx"),
    route("/admin/containers/:id", "routes/admin.containers.$id.tsx"),
    route("/admin/images", "routes/admin.images.tsx"),
    route("/admin/networks", "routes/admin.networks.tsx"),
    route("/admin/stacks", "routes/admin.stacks.tsx"),
    route("/admin/audit", "routes/admin.audit.tsx"),
  ]),
] satisfies RouteConfig;
