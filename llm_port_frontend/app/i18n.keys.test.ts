/**
 * Every language carries every English string, and every literal key the
 * code asks for exists in English.
 *
 * Without this, a page written without translations shows English in every
 * language, and a key typed without adding it shows the key itself. Both
 * happened: the cluster pages were English-only, and ~70 keys used in code
 * were in no language file at all.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, resolve } from "node:path";

import { describe, expect, it } from "vitest";

// Vitest runs from the frontend's root (jsdom gives no file URL to resolve from).
const APP = resolve("app");
const I18N = resolve("../llm_port_backend/i18n");
const NAMESPACES = ["common", "chat", "tour"] as const;
const LANGUAGES = ["de", "es", "fr", "zh"] as const;

type Tree = { [key: string]: string | Tree };

function flatten(tree: Tree, prefix = ""): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(tree)) {
    if (typeof value === "string") out[prefix + key] = value;
    else Object.assign(out, flatten(value, `${prefix}${key}.`));
  }
  return out;
}

function load(lang: string, ns: string): Record<string, string> {
  return flatten(JSON.parse(readFileSync(join(I18N, lang, `${ns}.json`), "utf-8")) as Tree);
}

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return sources(path);
    return /\.(ts|tsx)$/.test(name) && !/\.test\.(ts|tsx)$/.test(name) ? [path] : [];
  });
}

const english = Object.fromEntries(NAMESPACES.map((ns) => [ns, load("en", ns)])) as Record<
  (typeof NAMESPACES)[number],
  Record<string, string>
>;

function placeholders(text: string): string[] {
  return [...text.matchAll(/\{\{\s*(\w+)/g)].map((m) => m[1]).sort();
}

describe("translations", () => {
  for (const lang of LANGUAGES) {
    for (const ns of NAMESPACES) {
      it(`${lang}/${ns}.json has every English key, with the same placeholders`, () => {
        const other = load(lang, ns);
        const missing = Object.keys(english[ns]).filter((key) => !(key in other));
        expect(missing).toEqual([]);
        const broken = Object.keys(english[ns]).filter(
          (key) =>
            key in other &&
            placeholders(english[ns][key]).join() !== placeholders(other[key]).join(),
        );
        expect(broken).toEqual([]);
      });
    }
  }

  it("every literal key the code uses exists in English", () => {
    const unknown: string[] = [];
    for (const file of sources(APP)) {
      const text = readFileSync(file, "utf-8");
      const fileNs = /useTranslation\(\s*\[?\s*"(\w+)"/.exec(text)?.[1] ?? "common";
      for (const match of text.matchAll(/\bt\(\s*"([^"]+)"/g)) {
        let key = match[1];
        let ns = fileNs;
        if (key.includes(":")) [ns, key] = key.split(":", 2) as [string, string];
        const table = english[ns as (typeof NAMESPACES)[number]];
        if (!table) continue;
        const known =
          key in table ||
          Object.keys(table).some((k) => k.startsWith(`${key}_`)) || // plurals: key_one, key_other
          NAMESPACES.some((n) => key in english[n]);
        if (!known) unknown.push(`${relative(APP, file)}: ${ns}:${key}`);
      }
    }
    expect(unknown).toEqual([]);
  });
});
