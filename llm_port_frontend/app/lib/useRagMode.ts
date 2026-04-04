/**
 * useRagMode — hook that detects whether the RAG module is running in
 * "lite" or "pro" mode by calling the /admin/rag/health endpoint.
 *
 * Returns:
 *   mode: "lite" | "pro" | null (null while loading or on error)
 *   loading: boolean
 */
import { useEffect, useState } from "react";
import { ragRuntime } from "~/api/rag";

export type RagMode = "lite" | "pro";

const CACHE_KEY = "llm-port-rag-mode-v1";
const CACHE_TTL_MS = 60_000; // 1 minute

function readCache(): RagMode | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.sessionStorage.getItem(CACHE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as { mode: RagMode; expiresAt: number };
    if (parsed.expiresAt <= Date.now()) {
      window.sessionStorage.removeItem(CACHE_KEY);
      return null;
    }
    return parsed.mode;
  } catch {
    return null;
  }
}

function writeCache(mode: RagMode): void {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.setItem(
      CACHE_KEY,
      JSON.stringify({ mode, expiresAt: Date.now() + CACHE_TTL_MS }),
    );
  } catch {
    // ignore
  }
}

export function useRagMode() {
  const [mode, setMode] = useState<RagMode | null>(() => readCache());
  const [loading, setLoading] = useState(() => readCache() === null);

  useEffect(() => {
    let cancelled = false;
    ragRuntime
      .health()
      .then((resp) => {
        if (cancelled) return;
        const m = resp.mode ?? "pro";
        setMode(m);
        writeCache(m);
      })
      .catch(() => {
        if (!cancelled) setMode(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return { mode, loading };
}
