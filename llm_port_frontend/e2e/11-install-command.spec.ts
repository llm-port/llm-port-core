/**
 * The install command the console hands out has to work on another machine.
 *
 * It was built from the browser's own URL. On the dev proxy that made it
 * `curl -fsSLO http://localhost:5173/...`, which on the machine being added
 * fetches from that machine and finds nothing — and nothing says so until
 * somebody runs it on real hardware.
 */
import { expect, test } from "@playwright/test";

const SHOTS = "../docs/images/onboarding";

test("the command names an address another machine can reach", async ({ page }) => {
  await page.goto("/admin/nodes");
  await page.waitForLoadState("networkidle").catch(() => {});
  await page.getByRole("button", { name: /add a machine/i }).first().click();
  await page.waitForTimeout(1500);

  // Both the navigation and this panel are MUI Drawers, so pick the one that
  // actually contains the onboarding copy rather than the first on the page.
  const dialog = page
    .locator('.MuiDrawer-paper, [role="dialog"]')
    .filter({ hasText: /On the machine you want to add/i })
    .first();
  await dialog.waitFor({ state: "visible", timeout: 15_000 });
  const text = await dialog.innerText();
  console.log("--- Add Node dialog ---");
  console.log(
    text
      .split("\n")
      .slice(0, 30)
      .map((l) => `    ${l}`)
      .join("\n"),
  );

  await page.screenshot({ path: `${SHOTS}/06-install-command.png` });

  const command = text.match(/curl -fsSLO \S+/)?.[0] ?? "";
  console.log(`--- command: ${command}`);

  expect(command, "a command is shown").toBeTruthy();
  expect(
    /localhost|127\.0\.0\.1|\[::1\]/.test(command),
    `the command must not point the machine at its own loopback: ${command}`,
  ).toBeFalsy();
});
