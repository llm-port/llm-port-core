# Language Contribution Guide

This project uses runtime i18n with `react-i18next`.

- Frontend reads translations from: `/api/i18n/{lang}/{namespace}`
- Current namespace: `common`
- Source bundles are stored in backend (runtime-loaded):
  - `airgap_backend/i18n/en/common.json`
  - `airgap_backend/i18n/es/common.json`
  - `airgap_backend/i18n/de/common.json`
  - `airgap_backend/i18n/zh/common.json`

## 1. Add or Update UI Text in Code

In frontend components/pages, use:

```tsx
import { useTranslation } from "react-i18next";

const { t } = useTranslation();
return <span>{t("your.section.key")}</span>;
```

Rules:
- Do not hardcode user-facing text.
- Reuse existing keys when possible.
- Keep key names stable (renaming keys can break existing translations).

## 2. Add or Update Translation Keys

1. Add new keys to English first:
   - `airgap_backend/i18n/en/common.json`
2. Add the same keys to other languages:
   - `es`, `de`, `zh`
3. Keep JSON structure aligned across languages.

Recommended key style:
- `section.subsection.label`
- examples:
  - `logs.title`
  - `stacks.deploy_stack`
  - `llm_models.download_request_failed`

## 3. Add a New Language

1. Create folder:
   - `airgap_backend/i18n/<lang-code>/`
2. Add file:
   - `airgap_backend/i18n/<lang-code>/common.json`
3. Start by copying English `common.json`, then translate values.
4. (Optional) add display name in backend map:
   - `airgap_backend/airgap_backend/web/api/i18n/views.py`
   - `_LANGUAGE_NAMES`

If not added there, language still works but may show raw code as display name.

## 4. Cache/Refresh Behavior

Frontend i18n uses versioned load path and reload on language switch.

- Config file: `airgap_frontend/app/i18n.ts`
- Optional env var to force cache bust:
  - `VITE_I18N_VERSION=2` (any new value)

If translations do not appear:
1. Hard refresh browser (`Ctrl+F5`)
2. Restart frontend dev server/container
3. Restart backend API if translation files changed in running container

## 5. Validation Checklist

- Change language from top-right selector.
- Verify:
  - Dashboard
  - Container pages
  - Network create dialog
  - Stack deploy page
  - LLM pages
- Check network calls return expected bundle:
  - `/api/i18n/de/common`
  - `/api/i18n/zh/common`

## 6. Common Pitfalls

- Key exists only in English: other languages fallback to English.
- Hardcoded text left in JSX/dialog/tooltips.
- JSON typo (invalid commas/quotes) in translation file.
- Added key in one language but not others.

