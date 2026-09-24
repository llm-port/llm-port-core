/** Global setup for component tests. */
import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import { afterEach, vi } from "vitest";

import chat from "../../../llm_port_backend/i18n/en/chat.json";
import common from "../../../llm_port_backend/i18n/en/common.json";
import tour from "../../../llm_port_backend/i18n/en/tour.json";

// The English strings the app ships, so a page renders what an English
// reader sees -- and a key missing from them renders as the key, which the
// assertions then fail on instead of passing over it.
void i18n.use(initReactI18next).init({
  lng: "en",
  fallbackLng: "en",
  defaultNS: "common",
  ns: ["common", "chat", "tour"],
  resources: { en: { common, chat, tour } },
  interpolation: { escapeValue: false },
  react: { useSuspense: false },
});

afterEach(() => {
  cleanup();
});

// jsdom implements neither of these, and MUI reads both during layout.
if (!window.matchMedia) {
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }));
}

if (!window.ResizeObserver) {
  window.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
}
