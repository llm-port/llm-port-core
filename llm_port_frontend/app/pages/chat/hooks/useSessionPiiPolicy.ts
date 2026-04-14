/**
 * useSessionPiiPolicy — hook for managing session PII policy overrides.
 *
 * Follows the same pattern as useToolPolicy: fetches on session change,
 * provides optimistic update with rollback on error.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import type { SessionPIIOverride, SessionPIIPolicy } from "~/api/pii";
import {
  getSessionPiiPolicy,
  patchSessionPiiPolicy,
  clearSessionPiiPolicy,
} from "~/api/pii";

export interface UseSessionPiiPolicyResult {
  /** Full policy (floor + override + effective). null before first load. */
  policy: SessionPIIPolicy | null;
  /** Whether the policy is currently being fetched. */
  loading: boolean;
  /** Last error message, if any. */
  error: string | null;
  /** Re-fetch policy from server. */
  refresh: () => Promise<void>;
  /** Update (strengthen) the session PII override. */
  updateOverride: (patch: SessionPIIOverride) => Promise<void>;
  /** Clear the session PII override, reverting to floor. */
  clearOverride: () => Promise<void>;
}

export function useSessionPiiPolicy(
  sessionId: string | null,
): UseSessionPiiPolicyResult {
  const [policy, setPolicy] = useState<SessionPIIPolicy | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const prevSessionRef = useRef<string | null>(null);

  const refresh = useCallback(async () => {
    if (!sessionId) return;
    setLoading(true);
    setError(null);
    try {
      const p = await getSessionPiiPolicy(sessionId);
      setPolicy(p);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to load PII policy",
      );
    } finally {
      setLoading(false);
    }
  }, [sessionId]);

  // Fetch when session changes
  useEffect(() => {
    if (sessionId && sessionId !== prevSessionRef.current) {
      refresh();
    } else if (!sessionId) {
      setPolicy(null);
      setError(null);
    }
    prevSessionRef.current = sessionId;
  }, [sessionId, refresh]);

  const updateOverride = useCallback(
    async (patch: SessionPIIOverride) => {
      if (!sessionId) return;
      setError(null);
      try {
        const updated = await patchSessionPiiPolicy(sessionId, patch);
        setPolicy(updated);
      } catch (err) {
        setError(
          err instanceof Error ? err.message : "Failed to update PII override",
        );
        // Refresh to get the actual server state
        await refresh();
      }
    },
    [sessionId, refresh],
  );

  const clearOverride = useCallback(async () => {
    if (!sessionId) return;
    setError(null);
    try {
      await clearSessionPiiPolicy(sessionId);
      // Re-fetch to get the floor-only policy
      await refresh();
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to clear PII override",
      );
    }
  }, [sessionId, refresh]);

  return { policy, loading, error, refresh, updateOverride, clearOverride };
}
