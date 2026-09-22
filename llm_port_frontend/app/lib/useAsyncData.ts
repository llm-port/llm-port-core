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
  refresh: (silent?: boolean) => Promise<void>;
  /** Manually replace `error`. Handy for showing save / delete errors. */
  setError: (msg: string | null) => void;
}

export interface UseAsyncDataOptions<T> {
  /** Starting value for `data` before the first load completes. */
  initialValue: T;
  /**
   * Re-fetch every N milliseconds.
   *
   * Off by default, because most admin screens describe things that do not
   * move on their own. It exists for the ones that do: a cluster coming up
   * and a deployment starting both change without anybody clicking, and a
   * screen that shows "Starting" until the operator thinks to reload is
   * indistinguishable from one that is stuck.
   *
   * Refreshes are silent -- `loading` stays false and the previous data
   * stays on screen -- so a polling panel never flickers back to a skeleton.
   */
  refreshMs?: number;
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
  const inFlightRef = useRef(false);

  const load = useCallback(async (silent = false) => {
    // Never let a poll overlap the request it is repeating.
    //
    // Several of these endpoints issue a command to a node and wait on the
    // answer, which can take a minute or more.  A 10s interval against a 90s
    // response stacks nine requests, and a browser only opens six connections
    // per origin -- so the page starves itself, and nothing else on the site
    // can load until a reload aborts them.  Skipping a tick is the honest
    // behaviour: the data is already on its way.
    if (silent && inFlightRef.current) return;
    inFlightRef.current = true;
    if (!silent) {
      setLoading(true);
      setError(null);
    }
    try {
      const result = await fetcher();
      if (mountedRef.current) setData(result);
    } catch (err: unknown) {
      if (mountedRef.current) {
        setError(err instanceof Error ? err.message : String(err));
      }
    } finally {
      inFlightRef.current = false;
      if (mountedRef.current && !silent) setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  const refreshMs = options?.refreshMs ?? 0;
  useEffect(() => {
    if (refreshMs <= 0) return;
    const timer = setInterval(() => {
      // Silent: the point is that values change under the operator, not that
      // the page blinks at them every few seconds.
      void load(true);
    }, refreshMs);
    return () => clearInterval(timer);
  }, [refreshMs, load]);

  useEffect(() => {
    mountedRef.current = true;
    void load();
    return () => {
      mountedRef.current = false;
    };
  }, [load]);

  return { data, loading, error, refresh: load, setError };
}
