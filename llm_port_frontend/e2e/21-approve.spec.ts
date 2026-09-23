/**
 * Step 1 of the guide, the console half: notice the machines waiting, and let
 * them in. Captures the screens the guide shows.
 */
import { expect, test } from "@playwright/test";

const SHOTS = "../docs/images/onboarding";
const FAQ = "../docs/images/faq";

test("waiting machines are announced, reviewed and approved", async ({ page }) => {
  // Elsewhere in the console the group is collapsed, and carries a dot for
  // the waiting machines inside it.
  await page.goto("/admin/dashboard");
  await page.waitForLoadState("networkidle").catch(() => {});
  await page.waitForTimeout(1500);
  await page.screenshot({ path: `${FAQ}/waiting-machine-dot-on-collapsed-group.png` });

  // On the Machines page the group is open and the entry carries the count.
  await page.goto("/admin/nodes");
  const badge = page.getByTestId("nav-badge-pendingJoins");
  await expect(badge).toHaveText(/[0-9]+/, { timeout: 30_000 });
  console.log(`  badge reads: ${await badge.innerText()}`);

  const banner = page.getByTestId("pending-joins");
  await expect(banner).toBeVisible({ timeout: 30_000 });
  console.log(`  banner: ${(await banner.innerText()).replace(/\s+/g, " ")}`);
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${SHOTS}/01-machines-waiting.png` });

  await banner.getByRole("button", { name: "Review" }).click();
  const panel = page
    .locator(".MuiDrawer-paper")
    .filter({ hasText: /On the machine you want to add/i })
    .first();
  await expect(panel.getByRole("button", { name: "Approve" }).first()).toBeVisible({
    timeout: 30_000,
  });
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${SHOTS}/03-waiting-for-approval.png` });

  for (let i = 0; i < 4; i++) {
    const approve = panel.getByRole("button", { name: "Approve" }).first();
    if (!(await approve.count())) break;
    await approve.click();
    await page.waitForTimeout(2000);
  }
  await page.keyboard.press("Escape");
});

test("both machines come up healthy", async ({ page }) => {
  await page.goto("/admin/nodes");
  for (const host of ["spark-ts3202", "spark-3201"]) {
    await expect(page.getByRole("row").filter({ hasText: host })).toContainText("healthy", {
      timeout: 180_000,
    });
  }
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${SHOTS}/04-machines-healthy.png` });
  await expect(page.getByTestId("pending-joins")).toHaveCount(0);
});
