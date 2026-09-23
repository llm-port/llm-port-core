/**
 * The FAQ's pictures of scaling past what a cluster holds. Read-only: the
 * dialog is cancelled. Expects the deployment to have been asked for more
 * copies than the cluster has accelerators (29-scale with SCALE_TO=3).
 */
import { expect, test } from "@playwright/test";

const FAQ = "../docs/images/faq";

test("more copies than accelerators", async ({ page }) => {
  await page.goto("/admin/deployments");
  await page.getByText("qwen-chat", { exact: true }).first().click();
  await expect(page.getByText("Copies (ready / wanted)")).toBeVisible();
  await expect(page.getByText(/cannot start/)).toBeVisible({ timeout: 60_000 });
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${FAQ}/scaled-past-the-cluster.png` });

  await page.getByRole("button", { name: /^Scale$/ }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByTestId("scale-capacity")).toBeVisible();
  await expect(dialog.getByTestId("scale-over-capacity")).toBeVisible();
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${FAQ}/scale-dialog-warns-past-capacity.png` });
  await dialog.getByRole("button", { name: "Cancel" }).click();
});
