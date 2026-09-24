/**
 * The old LLM -> Models pages live in the marketplace now ("On this server").
 * Their addresses are kept so bookmarks and links still land somewhere useful.
 */
import { Navigate } from "react-router";

export default function ModelsRedirect() {
  return <Navigate to="/admin/marketplace?tab=local" replace />;
}
