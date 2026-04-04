import { Navigate } from "react-router";

export default function AuditRedirectPage() {
  return <Navigate to="/admin/logs?tab=audit" replace />;
}
