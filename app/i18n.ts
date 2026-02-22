import i18n from "i18next";
import LanguageDetector from "i18next-browser-languagedetector";
import HttpBackend from "i18next-http-backend";
import { initReactI18next } from "react-i18next";

const i18nVersion = (import.meta.env.VITE_I18N_VERSION as string | undefined) ?? "1";

void i18n
  .use(HttpBackend)
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    fallbackLng: "en",
    defaultNS: "common",
    ns: ["common"],
    debug: false,
    interpolation: {
      escapeValue: false,
    },
    load: "languageOnly",
    supportedLngs: false,
    backend: {
      loadPath: `/api/i18n/{{lng}}/{{ns}}?v=${encodeURIComponent(i18nVersion)}`,
    },
    detection: {
      order: ["localStorage", "navigator", "htmlTag"],
      lookupLocalStorage: "airgap-lang",
      caches: ["localStorage"],
    },
    react: {
      useSuspense: false,
    },
  });

export default i18n;
