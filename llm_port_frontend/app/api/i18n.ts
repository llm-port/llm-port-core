export interface UiLanguage {
  code: string;
  name: string;
}

export async function listLanguages(): Promise<UiLanguage[]> {
  const response = await fetch("/api/i18n/languages", {
    credentials: "include",
  });
  if (!response.ok) {
    const text = await response.text().catch(() => response.statusText);
    throw new Error(`API ${response.status}: ${text}`);
  }
  const json = (await response.json()) as { languages?: UiLanguage[] };
  return json.languages ?? [];
}
