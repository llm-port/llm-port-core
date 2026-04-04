import { Outlet } from "react-router";
import ModuleGuard from "~/components/ModuleGuard";

export default function PiiGuardLayout() {
  return (
    <ModuleGuard module="pii">
      <Outlet />
    </ModuleGuard>
  );
}
