import { Outlet } from "react-router";
import ModuleGuard from "~/components/ModuleGuard";

export default function SkillsGuardLayout() {
  return (
    <ModuleGuard module="skills">
      <Outlet />
    </ModuleGuard>
  );
}
