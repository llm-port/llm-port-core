/**
 * RAG module guard layout — wraps all /admin/rag/* routes.
 * When the RAG module is disabled, shows a "module not enabled" message
 * instead of rendering the child route.
 */
import { Outlet } from "react-router";
import ModuleGuard from "~/components/ModuleGuard";

export default function RagGuardLayout() {
  return (
    <ModuleGuard module="rag">
      <Outlet />
    </ModuleGuard>
  );
}
