/**
 * Redirect: /admin/llm/runtimes → /admin/llm/providers
 * Runtimes are now managed inline on the unified Providers page.
 */
import { Navigate } from "react-router";

export default function RuntimesPage() {
  return <Navigate to="/admin/llm/providers" replace />;
}
