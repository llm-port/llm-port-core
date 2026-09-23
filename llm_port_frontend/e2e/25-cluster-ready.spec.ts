import { expect, test } from "@playwright/test";

test("the ready cluster", async ({ page }) => {
  await page.goto("/admin/clusters");
  const link = page.getByText(/^gpu-pair-/).first();
  const name = (await link.innerText()).trim();
  await link.click();
  await expect(page.getByRole("heading", { name })).toBeVisible();
  await expect(page.getByTestId("next-step")).toHaveAttribute("data-stage", "ready", { timeout: 60_000 });
  await page.waitForTimeout(3000);
  await page.screenshot({ path: "../docs/images/onboarding/09-cluster-ready.png", fullPage: true });
  console.log(`  ${(await page.getByTestId("next-step").innerText()).replace(/\s+/g, " ")}`);
});
