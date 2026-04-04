/**
 * Chat module guard layout — wraps all /admin/chat/* routes.
 * When the Chat module is disabled, shows a "module not enabled" message
 * instead of rendering the child route.
 */
import { Outlet } from "react-router";
import ModuleGuard from "~/components/ModuleGuard";

export default function ChatGuardLayout() {
  return (
    <ModuleGuard module="chat">
      <Outlet />
    </ModuleGuard>
  );
}
