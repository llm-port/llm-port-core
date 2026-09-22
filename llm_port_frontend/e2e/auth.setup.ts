import { expect, test as setup } from "@playwright/test";

// The package is ESM ("type": "module"), so __dirname does not exist; the
// path is relative to the config's rootDir anyway.
export const STORAGE_STATE = "e2e/.auth/admin.json";

/**
 * Sign in once and reuse the session.
 *
 * Uses the backend's dev-login, which mints a cookie for admin@localhost and
 * only exists when `environment == "dev"` — so this cannot accidentally be a
 * way into a real deployment.
 */
setup("authenticate as admin", async ({ page, context }) => {
  const response = await page.request.post("/api/auth/dev-login");
  expect(
    response.ok(),
    `dev-login failed (${response.status()}). Is the backend running in dev mode?`,
  ).toBeTruthy();

  await page.goto("/admin/clusters");
  await expect(page).not.toHaveURL(/\/login/);

  await context.storageState({ path: STORAGE_STATE });
});
