/**
 * The logs page: filters built from the labels the logs carry, named for
 * people, and one machine's logs one click away. Read-only.
 */
import { expect, test, type Page } from "@playwright/test";

const SHOTS = "../docs/images/logs";

async function filterNames(page: Page): Promise<string[]> {
  return page.locator('[data-testid^="logs-filter-"]').evaluateAll((els) =>
    els.map((el) => el.getAttribute("data-testid")!.replace("logs-filter-", "")),
  );
}

test("filters come from the logs, with names people use", async ({ page }) => {
  await page.goto("/admin/logs");
  await page.getByText("Last 15m", { exact: true }).click();
  await page.getByRole("option", { name: /24/ }).click();
  await expect(page.getByTestId("logs-filter-host")).toBeVisible({ timeout: 30_000 });
  await page.waitForTimeout(1500);

  const labels = await filterNames(page);
  console.log(`  filters: ${labels.join(", ")}`);
  expect(labels[0]).toBe("host");
  expect(labels).not.toContain("service_name");
  const titles = await page.locator("label").allInnerTexts();
  console.log(`  titles: ${titles.join(" | ")}`);
  await page.screenshot({ path: `${SHOTS}/logs-filters.png` });

  await page.getByTestId("logs-filter-host").click();
  const machines = await page.getByRole("option").allInnerTexts();
  console.log(`  machine options: ${machines.join(" | ")}`);
  await page.screenshot({ path: `${SHOTS}/logs-machine-picker.png` });
  await page.getByRole("option", { name: /spark-3201/ }).click();
  await page.waitForTimeout(2500);

  const sources = await page.getByTestId("logs-filter-job").innerText().catch(() => "?");
  console.log(`  after choosing spark-3201, Source reads: ${sources}`);
  await page.getByTestId("logs-filter-job").click();
  console.log(`  source options now: ${(await page.getByRole("option").allInnerTexts()).join(" | ")}`);
  await page.keyboard.press("Escape");

  const machineCells = await page.locator("tbody tr td:nth-child(3)").allInnerTexts();
  const distinct = [...new Set(machineCells.map((c) => c.trim()))];
  console.log(`  machines in the table: ${distinct.join(", ")} (${machineCells.length} rows)`);
  expect(distinct.every((m) => m === "spark-3201")).toBe(true);
  await page.screenshot({ path: `${SHOTS}/logs-one-machine.png` });
});

test("a machine's page opens its logs", async ({ page }) => {
  await page.goto("/admin/nodes");
  await page.getByText("10.88.10.49", { exact: true }).first().click();
  await page.getByRole("button", { name: "Open in Logs" }).click();
  await expect(page).toHaveURL(/\/admin\/logs\?host=10\.88\.10\.49/);
  await expect(page.getByTestId("logs-filter-host")).toContainText("spark-ts3202", { timeout: 30_000 });
  await page.waitForTimeout(2000);
  await page.screenshot({ path: `${SHOTS}/logs-from-machine-page.png` });
});
