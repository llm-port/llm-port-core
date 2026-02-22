export interface ApiServerSettings {
  endpointUrl: string;
  containerName: string;
}

export interface AdminGeneralSettings {
  apiServer: ApiServerSettings;
}

const STORAGE_KEY = "llm-port-admin-general-settings";

const DEFAULT_ENDPOINT_URL = import.meta.env.VITE_LLM_PORT_API_DOCS_URL ?? "http://localhost:8001/api/docs";
const DEFAULT_CONTAINER_NAME = import.meta.env.VITE_LLM_PORT_API_CONTAINER_NAME ?? "llm-port-api";

export const DEFAULT_ADMIN_GENERAL_SETTINGS: AdminGeneralSettings = {
  apiServer: {
    endpointUrl: DEFAULT_ENDPOINT_URL,
    containerName: DEFAULT_CONTAINER_NAME,
  },
};

function asString(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

export function getAdminGeneralSettings(): AdminGeneralSettings {
  if (typeof window === "undefined") {
    return DEFAULT_ADMIN_GENERAL_SETTINGS;
  }

  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) {
      return DEFAULT_ADMIN_GENERAL_SETTINGS;
    }

    const parsed = JSON.parse(raw) as Partial<AdminGeneralSettings>;
    const endpointUrl = asString(parsed.apiServer?.endpointUrl)?.trim();
    const containerName = asString(parsed.apiServer?.containerName)?.trim();

    return {
      apiServer: {
        endpointUrl: endpointUrl || DEFAULT_ADMIN_GENERAL_SETTINGS.apiServer.endpointUrl,
        containerName: containerName || DEFAULT_ADMIN_GENERAL_SETTINGS.apiServer.containerName,
      },
    };
  } catch {
    return DEFAULT_ADMIN_GENERAL_SETTINGS;
  }
}

export function saveAdminGeneralSettings(settings: AdminGeneralSettings): void {
  if (typeof window === "undefined") {
    return;
  }
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
}
