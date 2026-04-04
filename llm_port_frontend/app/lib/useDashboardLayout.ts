/**
 * Hook to persist dashboard section order & visibility to localStorage.
 * Mirrors the useNavOrder pattern — reconciles on mount so that newly-added
 * sections are appended at the end and removed ones are pruned.
 */
import { useState, useCallback, useEffect } from "react";
import { arrayMove } from "@dnd-kit/sortable";

const STORAGE_KEY = "llm-port-dashboard-layout";

export const DASHBOARD_SECTIONS = [
  "gauges",
  "node_fleet",
  "stat_cards",
  "grafana",
  "dependency_health",
  "module_status",
  "data_residency",
  "top_containers",
  "quick_links",
] as const;

export type SectionId = (typeof DASHBOARD_SECTIONS)[number];

export interface DashboardLayoutState {
  order: SectionId[];
  hidden: SectionId[];
}

function reconcile(
  stored: DashboardLayoutState | null,
  allIds: readonly SectionId[],
): DashboardLayoutState {
  if (!stored) {
    return { order: [...allIds], hidden: [] };
  }

  const allSet = new Set<SectionId>(allIds);

  // Drop removed sections, preserve ordering
  const order = stored.order.filter((id) => allSet.has(id));
  const placed = new Set(order);

  // Append new sections at the end
  for (const id of allIds) {
    if (!placed.has(id)) {
      order.push(id);
    }
  }

  // Prune hidden set to only existing sections
  const hidden = stored.hidden.filter((id) => allSet.has(id));

  return { order, hidden };
}

function readStored(): DashboardLayoutState | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (
      parsed &&
      typeof parsed === "object" &&
      Array.isArray(parsed.order) &&
      Array.isArray(parsed.hidden)
    ) {
      return parsed as DashboardLayoutState;
    }
  } catch {
    // corrupt data — ignore
  }
  return null;
}

export function useDashboardLayout() {
  const [layout, setLayout] = useState<DashboardLayoutState>(() =>
    reconcile(readStored(), DASHBOARD_SECTIONS),
  );

  // Persist whenever layout changes
  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(layout));
    } catch {
      // quota exceeded or private mode — ignore
    }
  }, [layout]);

  const reorder = useCallback((activeId: string, overId: string) => {
    setLayout((prev) => {
      const oldIndex = prev.order.indexOf(activeId as SectionId);
      const newIndex = prev.order.indexOf(overId as SectionId);
      if (oldIndex === -1 || newIndex === -1) return prev;
      return { ...prev, order: arrayMove(prev.order, oldIndex, newIndex) };
    });
  }, []);

  const toggleVisibility = useCallback((sectionId: SectionId) => {
    setLayout((prev) => {
      const isHidden = prev.hidden.includes(sectionId);
      return {
        ...prev,
        hidden: isHidden
          ? prev.hidden.filter((id) => id !== sectionId)
          : [...prev.hidden, sectionId],
      };
    });
  }, []);

  const resetLayout = useCallback(() => {
    const fresh = reconcile(null, DASHBOARD_SECTIONS);
    setLayout(fresh);
    if (typeof window !== "undefined") {
      try {
        localStorage.removeItem(STORAGE_KEY);
      } catch {
        // ignore
      }
    }
  }, []);

  return { layout, reorder, toggleVisibility, resetLayout };
}
