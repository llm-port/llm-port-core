/**
 * Machines that have asked to join and are waiting for someone to say yes.
 *
 * The request only ever showed up inside the Add a machine panel, so an
 * operator who ran the install on a machine and came back to the console to
 * approve it found a fleet page with no mention of it anywhere. This is the
 * one source for the sidebar badge and the fleet page banner.
 *
 * Quiet on failure: listing join requests needs the manage permission, and a
 * user without it simply has nothing to approve.
 */
import { useEffect, useState } from "react";

import { nodesApi, type NodeJoinRequest } from "~/api/nodes";

/** Often enough to notice a machine within the time it takes to switch windows. */
const POLL_MS = 15_000;

export function usePendingJoins(enabled = true): NodeJoinRequest[] {
  const [pending, setPending] = useState<NodeJoinRequest[]>([]);

  useEffect(() => {
    if (!enabled) {
      setPending([]);
      return undefined;
    }
    let cancelled = false;

    let timer: number | undefined;

    async function load() {
      try {
        const rows = await nodesApi.listJoinRequests();
        if (!cancelled) setPending(rows);
      } catch (err: unknown) {
        if (cancelled) return;
        setPending([]);
        // Not allowed to approve, so not allowed to see: stop asking rather
        // than collecting a refusal every fifteen seconds.
        const text = err instanceof Error ? err.message : "";
        if (/API 40[13]/.test(text) && timer !== undefined) {
          window.clearInterval(timer);
          timer = undefined;
        }
      }
    }

    timer = window.setInterval(() => void load(), POLL_MS);
    void load();
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearInterval(timer);
    };
  }, [enabled]);

  return pending;
}
