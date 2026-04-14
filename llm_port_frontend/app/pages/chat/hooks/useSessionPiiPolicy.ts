/**
 * useSessionPiiPolicy — hook for managing session PII policy overrides.
 *
 * Pre-session: fetches tenant defaults via /pii-defaults and stores overrides
 * locally.  On session creation the caller flushes local overrides to the server.
 *
 * With session: fetches / patches policy on the server.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import type {
  SessionPIIOverride,
  SessionPIIPolicy,
  PIIPolicyConfig,
} from "~/api/pii";
import {
  getPiiDefaults,
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
  /** Pre-session local overrides (empty when a session is active). */
  localOverrides: SessionPIIOverride;
  /** Re-fetch policy from server (or defaults). */
  refresh: () => Promise<void>;
  /** Update (strengthen) the session PII override. */
  updateOverride: (patch: SessionPIIOverride) => Promise<void>;
  /** Clear the session PII override, reverting to floor. */
  clearOverride: () => Promise<void>;
}

/**
 * Merge a flat SessionPIIOverride onto a PIIPolicyConfig floor to produce
 * an effective config.  Mirrors the server-side clamp_and_merge logic
 * (the UI controls already prevent weakening below floor).
 */
function applyOverride(
  floor: PIIPolicyConfig,
  ov: SessionPIIOverride,
): PIIPolicyConfig {
  return {
    telemetry: {
      ...floor.telemetry,
      ...(ov.telemetry_enabled != null && { enabled: ov.telemetry_enabled }),
      ...(ov.telemetry_mode != null && { mode: ov.telemetry_mode }),
    },
    egress: {
      ...floor.egress,
      ...(ov.egress_enabled_for_cloud != null && {
        enabled_for_cloud: ov.egress_enabled_for_cloud,
      }),
      ...(ov.egress_enabled_for_local != null && {
        enabled_for_local: ov.egress_enabled_for_local,
      }),
      ...(ov.egress_mode != null && { mode: ov.egress_mode }),
      ...(ov.egress_fail_action != null && {
        fail_action: ov.egress_fail_action,
      }),
    },
    presidio: {
      ...floor.presidio,
      ...(ov.presidio_threshold != null && {
        threshold: ov.presidio_threshold,
      }),
      entities: [
        ...floor.presidio.entities,
        ...(ov.presidio_entities_add ?? []),
      ],
    },
  };
}

export function useSessionPiiPolicy(
  sessionId: string | null,
): UseSessionPiiPolicyResult {
  const [policy, setPolicy] = useState<SessionPIIPolicy | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Pre-session state
  const [defaults, setDefaults] = useState<SessionPIIPolicy | null>(null);
  const [localOverrides, setLocalOverrides] = useState<SessionPIIOverride>({});
  const prevSessionRef = useRef<string | null | undefined>(undefined);

  // ── Fetch tenant defaults (pre-session) ──────────────────────────
  const fetchDefaults = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const d = await getPiiDefaults();
      setDefaults(d);
      setPolicy(d);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to load PII defaults",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  // ── Fetch session policy (with session) ──────────────────────────
  const fetchSession = useCallback(async () => {
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

  // ── Public refresh ───────────────────────────────────────────────
  const refresh = useCallback(async () => {
    if (sessionId) {
      await fetchSession();
    } else {
      await fetchDefaults();
    }
  }, [sessionId, fetchSession, fetchDefaults]);

  // ── Fetch on session change ──────────────────────────────────────
  useEffect(() => {
    if (sessionId) {
      if (sessionId !== prevSessionRef.current) {
        fetchSession();
        setLocalOverrides({});
      }
    } else if (prevSessionRef.current !== sessionId) {
      // Switched to no-session (new chat) or initial mount
      fetchDefaults();
    }
    prevSessionRef.current = sessionId;
  }, [sessionId, fetchSession, fetchDefaults]);

  // ── Recompute effective policy from defaults + local overrides ───
  useEffect(() => {
    if (sessionId || !defaults?.floor) return;
    const hasOverride = Object.values(localOverrides).some((v) => v != null);
    if (hasOverride) {
      setPolicy({
        ...defaults,
        has_override: true,
        override: localOverrides,
        effective: applyOverride(defaults.floor, localOverrides),
      });
    } else {
      setPolicy(defaults);
    }
  }, [sessionId, defaults, localOverrides]);

  // ── Update override ──────────────────────────────────────────────
  const updateOverride = useCallback(
    async (patch: SessionPIIOverride) => {
      if (sessionId) {
        setError(null);
        try {
          const updated = await patchSessionPiiPolicy(sessionId, patch);
          setPolicy(updated);
        } catch (err) {
          setError(
            err instanceof Error
              ? err.message
              : "Failed to update PII override",
          );
          await fetchSession();
        }
        return;
      }
      // Pre-session: accumulate locally
      setLocalOverrides((prev) => {
        const merged = { ...prev, ...patch };
        if (patch.presidio_entities_add) {
          const existing = prev.presidio_entities_add ?? [];
          merged.presidio_entities_add = [
            ...new Set([...existing, ...patch.presidio_entities_add]),
          ];
        }
        return merged;
      });
    },
    [sessionId, fetchSession],
  );

  // ── Clear override ───────────────────────────────────────────────
  const clearOverride = useCallback(async () => {
    if (sessionId) {
      setError(null);
      try {
        await clearSessionPiiPolicy(sessionId);
        await fetchSession();
      } catch (err) {
        setError(
          err instanceof Error ? err.message : "Failed to clear PII override",
        );
      }
      return;
    }
    // Pre-session: clear local overrides
    setLocalOverrides({});
  }, [sessionId, fetchSession]);

  return {
    policy,
    loading,
    error,
    localOverrides,
    refresh,
    updateOverride,
    clearOverride,
  };
}
