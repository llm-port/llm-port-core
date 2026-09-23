/**
 * Clear the fleet back to nothing, through the console, before a walkthrough.
 *
 * Deletes every gpu-box-* cluster (with the Delete the cluster page lacked
 * until now) and every DGX machine record, so the onboarding that follows
 * starts where a new operator would.
 */
import { expect, test } from "@playwright/test";

test.describe.configure({ mode: "serial" });

test("delete leftover walkthrough clusters", async ({ page }) => {
  for (let round = 0; round < 5; round++) {
    await page.goto("/admin/clusters");
    await page.waitForLoadState("networkidle").catch(() => {});
    const cluster = page.getByText(/^gpu-box-/).first();
    if (!(await cluster.count())) break;
    const name = await cluster.innerText();
    await cluster.click();
    await page.getByRole("button", { name: "Delete" }).click();
    const dialog = page.getByRole("dialog");
    console.log(`  ${name}: ${(await dialog.innerText()).replace(/\s+/g, " ").slice(0, 160)}`);
    await dialog.getByRole("button", { name: "Delete" }).click();
    await expect(page).toHaveURL(/\/admin\/clusters$/, { timeout: 330_000 });
    console.log(`  deleted ${name}`);
  }
});

test("delete the DGX machine records", async ({ page }) => {
  await page.goto("/admin/nodes");
  await page.waitForLoadState("networkidle").catch(() => {});
  for (const host of ["spark-ts3202", "spark-3201"]) {
    const row = page.getByRole("row").filter({ hasText: host }).first();
    if (!(await row.count())) {
      console.log(`  ${host}: not listed`);
      continue;
    }
    const address = (await row.getByRole("cell").first().innerText()).split("\n")[0].trim();
    await row.getByRole("button", { name: /delete machine/i }).click();
    const dialog = page.getByRole("dialog");
    // Destructive, so it asks for the address typed back.
    await dialog.getByRole("textbox").fill(address);
    await dialog.getByRole("button", { name: /delete/i }).last().click();
    await expect(page.getByRole("row").filter({ hasText: host })).toHaveCount(0, {
      timeout: 30_000,
    });
    console.log(`  deleted ${host}`);
  }
});
