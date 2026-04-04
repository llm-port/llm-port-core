/**
 * ServicesContext — React context that provides the optional-module
 * manifest to any component in the tree.
 *
 * Usage:
 *   const { isModuleEnabled, getModule, services, loading } = useServices();
 *   if (isModuleEnabled("rag")) { ... }
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import {
  servicesApi,
  type ServiceInfo,
  type ServicesManifest,
} from "~/api/services";

// ── Cache ────────────────────────────────────────────────────────────
const CACHE_KEY = "llm-port-services-v1";
const CACHE_TTL_MS = 60_000; // 1 minute

interface CacheEntry {
  manifest: ServicesManifest;
  expiresAt: number;
}

function readCache(): ServicesManifest | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.sessionStorage.getItem(CACHE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as CacheEntry;
    if (parsed.expiresAt <= Date.now()) {
      window.sessionStorage.removeItem(CACHE_KEY);
      return null;
    }
    return parsed.manifest;
  } catch {
    return null;
  }
}

function writeCache(manifest: ServicesManifest): void {
  if (typeof window === "undefined") return;
  try {
    const entry: CacheEntry = {
      manifest,
      expiresAt: Date.now() + CACHE_TTL_MS,
    };
    window.sessionStorage.setItem(CACHE_KEY, JSON.stringify(entry));
  } catch {
    // ignore
  }
}

// ── Context value ────────────────────────────────────────────────────
interface ServicesContextValue {
  /** All known services. */
  services: ServiceInfo[];
  /** True while first fetch is in-flight. */
  loading: boolean;
  /** Non-null if the initial fetch failed. */
  error: string | null;
  /** Check if a specific module is enabled (and optionally healthy). */
  isModuleEnabled: (name: string) => boolean;
  /** Look up full metadata for a module by name. */
  getModule: (name: string) => ServiceInfo | undefined;
  /** Force re-fetch the manifest. */
  refresh: () => Promise<void>;
}

const ServicesContext = createContext<ServicesContextValue>({
  services: [],
  loading: true,
  error: null,
  isModuleEnabled: () => false,
  getModule: () => undefined,
  refresh: async () => {},
});

// ── Provider ─────────────────────────────────────────────────────────
export function ServicesProvider({ children }: { children: ReactNode }) {
  const [services, setServices] = useState<ServiceInfo[]>(() => {
    const cached = readCache();
    return cached?.services ?? [];
  });
  const [loading, setLoading] = useState(() => readCache() === null);
  const [error, setError] = useState<string | null>(null);

  const fetchManifest = useCallback(async () => {
    try {
      const manifest = await servicesApi.list();
      setServices(manifest.services);
      setError(null);
      writeCache(manifest);
    } catch (err) {
      // On failure, keep previous state but flag the error.
      // This lets the UI degrade gracefully — if we've never fetched
      // successfully, all modules show as disabled (safe default).
      setError(err instanceof Error ? err.message : "Failed to load services");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchManifest();
  }, [fetchManifest]);

  const serviceMap = useMemo(() => {
    const map = new Map<string, ServiceInfo>();
    for (const svc of services) {
      map.set(svc.name, svc);
    }
    return map;
  }, [services]);

  const isModuleEnabled = useCallback(
    (name: string) => {
      const svc = serviceMap.get(name);
      if (svc?.enabled === true) return true;
      // "rag" is satisfied by either the full RAG Engine or the embedded RAG Lite plugin.
      if (name === "rag") {
        const lite = serviceMap.get("rag_lite");
        return lite?.enabled === true;
      }
      return false;
    },
    [serviceMap],
  );

  const getModule = useCallback(
    (name: string) => serviceMap.get(name),
    [serviceMap],
  );

  const value = useMemo<ServicesContextValue>(
    () => ({
      services,
      loading,
      error,
      isModuleEnabled,
      getModule,
      refresh: fetchManifest,
    }),
    [services, loading, error, isModuleEnabled, getModule, fetchManifest],
  );

  return <ServicesContext value={value}>{children}</ServicesContext>;
}

// ── Hook ─────────────────────────────────────────────────────────────
export function useServices(): ServicesContextValue {
  return useContext(ServicesContext);
}
