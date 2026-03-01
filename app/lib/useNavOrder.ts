/**
 * Hook to persist sidebar navigation order to localStorage.
 * Supports two sections: main (scrollable top area) and pinned (bottom area).
 *
 * When new nav items appear (e.g. a module is enabled) that weren't in the
 * stored order, they are appended to the appropriate section automatically.
 */
import { useState, useCallback, useEffect } from "react";

const STORAGE_KEY = "llm-port-nav-order";

export interface NavOrderState {
  mainIds: string[];
  pinnedIds: string[];
}

/**
 * Reconcile a possibly-stale stored order with the current set of nav IDs.
 * - IDs no longer present in `allIds` are dropped.
 * - New IDs (not in storage) are appended: to pinned if they're in
 *   `defaultPinnedIds`, otherwise to main.
 */
function reconcile(
  stored: NavOrderState | null,
  allIds: string[],
  defaultPinnedIds: string[],
): NavOrderState {
  if (!stored) {
    return {
      mainIds: allIds.filter((id) => !defaultPinnedIds.includes(id)),
      pinnedIds: defaultPinnedIds.filter((id) => allIds.includes(id)),
    };
  }

  const allSet = new Set(allIds);
  const main = stored.mainIds.filter((id) => allSet.has(id));
  const pinned = stored.pinnedIds.filter((id) => allSet.has(id));
  const placed = new Set([...main, ...pinned]);

  for (const id of allIds) {
    if (!placed.has(id)) {
      if (defaultPinnedIds.includes(id)) {
        pinned.push(id);
      } else {
        main.push(id);
      }
    }
  }

  return { mainIds: main, pinnedIds: pinned };
}

function readStored(): NavOrderState | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (
      parsed &&
      typeof parsed === "object" &&
      Array.isArray(parsed.mainIds) &&
      Array.isArray(parsed.pinnedIds)
    ) {
      return parsed as NavOrderState;
    }
  } catch {
    // corrupt data — ignore
  }
  return null;
}

export function useNavOrder(allIds: string[], defaultPinnedIds: string[]) {
  const [order, setOrder] = useState<NavOrderState>(() =>
    reconcile(readStored(), allIds, defaultPinnedIds),
  );

  // Persist to localStorage whenever order changes
  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(order));
    } catch {
      // quota exceeded or private mode — ignore
    }
  }, [order]);

  const resetOrder = useCallback(() => {
    setOrder(reconcile(null, allIds, defaultPinnedIds));
    if (typeof window !== "undefined") {
      try {
        localStorage.removeItem(STORAGE_KEY);
      } catch {
        // ignore
      }
    }
  }, [allIds, defaultPinnedIds]);

  return { order, setOrder, resetOrder };
}
