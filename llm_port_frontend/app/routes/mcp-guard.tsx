import { Outlet } from "react-router";
import ModuleGuard from "~/components/ModuleGuard";

export default function McpGuardLayout() {
  return (
    <ModuleGuard module="mcp">
      <Outlet />
    </ModuleGuard>
  );
}
