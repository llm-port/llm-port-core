/**
 * useToolPolicy — hook for managing session tool execution policy.
 *
 * Fetches the current tool policy when a session changes and provides
 * a setter for the execution mode that persists the change via API.
 * When no session exists (pre-session), mode is managed locally.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import type { ExecutionMode, SessionToolPolicy } from "~/api/tools";
import { getSessionToolPolicy, patchSessionToolPolicy } from "~/api/tools";

interface UseToolPolicyResult {
  executionMode: ExecutionMode;
  setExecutionMode: (mode: ExecutionMode) => void;
  policy: SessionToolPolicy | null;
  loading: boolean;
  refresh: () => Promise<void>;
}

export function useToolPolicy(
  sessionId: string | null,
  defaultMode: ExecutionMode = "server_only",
): UseToolPolicyResult {
  const [policy, setPolicy] = useState<SessionToolPolicy | null>(null);
  const [loading, setLoading] = useState(false);
  const [executionMode, setModeLocal] = useState<ExecutionMode>(defaultMode);
  const prevSessionRef = useRef<string | null>(null);

  const refresh = useCallback(async () => {
    if (!sessionId) return;
    setLoading(true);
    try {
      const p = await getSessionToolPolicy(sessionId);
      setPolicy(p);
      setModeLocal(p.execution_mode);
    } catch {
      // Use default on error
    } finally {
      setLoading(false);
    }
  }, [sessionId]);

  useEffect(() => {
    if (sessionId) {
      // Only fetch if this is a different session (not the initial transition
      // from null → session, where execution mode was already applied).
      if (
        prevSessionRef.current !== null &&
        prevSessionRef.current !== sessionId
      ) {
        refresh();
      } else if (prevSessionRef.current === null) {
        // Transition from no-session to session — keep the pre-selected mode.
        // The mode was already applied to the session during creation.
      }
    } else {
      setPolicy(null);
      // Don't reset executionMode — preserve the user's pre-session choice
    }
    prevSessionRef.current = sessionId;
  }, [sessionId, refresh]);

  const setExecutionMode = useCallback(
    (mode: ExecutionMode) => {
      setModeLocal(mode);
      if (sessionId) {
        patchSessionToolPolicy(sessionId, { execution_mode: mode }).then(
          (updated) => {
            setPolicy(updated);
          },
          () => {
            // Revert on failure
            refresh();
          },
        );
      }
    },
    [sessionId, refresh],
  );

  return { executionMode, setExecutionMode, policy, loading, refresh };
}
