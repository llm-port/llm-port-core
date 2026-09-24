/**
 * Whether the signed-in user may do something: `can("llm.models:download")`.
 *
 * Read from the admin layout, which already knows the user's permissions for
 * the sidebar. Outside it (a test rendering a page on its own) everything is
 * allowed: this only hides buttons, and the server refuses what is not.
 */
import { useOutletContext } from "react-router";

type Can = (permission?: string) => boolean;

const ALLOW_ALL: Can = () => true;

export function useCan(): Can {
  const context = useOutletContext<{ can?: Can } | undefined>();
  return context?.can ?? ALLOW_ALL;
}
