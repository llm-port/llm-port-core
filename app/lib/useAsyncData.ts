/**
 * useAsyncData — eliminates repetitive useState(loading) + useState(error)
 * + useState(data) + useEffect(load) patterns across all admin pages.
 *
 * Usage:
 *   const { data, loading, error, refresh } = useAsyncData(() => api.list(), []);
 *   const { data, loading, error, refresh } = useAsyncData(
 *     () => Promise.all([api.roles(), api.users()]),
 *     [],
 *   );
 */
import { useState, useEffect, useCallback, useRef, type DependencyList } from "react";

export interface AsyncState<T> {
  /** Resolved data — `initialValue` until the first successful load. */
  data: T;
  /** True while the fetcher is running. */
  loading: boolean;
  /** Error message from the last failed load, or null. */
  error: string | null;
  /** Re-run the fetcher imperatively (e.g. after a mutation). */
  refresh: () => Promise<void>;
  /** Manually replace `error`. Handy for showing save / delete errors. */
  setError: (msg: string | null) => void;
}

export interface UseAsyncDataOptions<T> {
  /** Starting value for `data` before the first load completes. */
  initialValue: T;
}

/**
 * Generic async data hook.
 *
 * @param fetcher  Async function that returns the data.
 * @param deps     Dependency list — the fetcher re-runs whenever these change.
 * @param options  Optional `{ initialValue }` (defaults to `undefined as T`).
 */
export function useAsyncData<T>(
  fetcher: () => Promise<T>,
  deps: DependencyList,
  options?: UseAsyncDataOptions<T>,
): AsyncState<T> {
  const [data, setData] = useState<T>(options?.initialValue as T);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const mountedRef = useRef(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await fetcher();
      if (mountedRef.current) setData(result);
    } catch (err: unknown) {
      if (mountedRef.current) {
        setError(err instanceof Error ? err.message : String(err));
      }
    } finally {
      if (mountedRef.current) setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  useEffect(() => {
    mountedRef.current = true;
    void load();
    return () => {
      mountedRef.current = false;
    };
  }, [load]);

  return { data, loading, error, refresh: load, setError };
}
