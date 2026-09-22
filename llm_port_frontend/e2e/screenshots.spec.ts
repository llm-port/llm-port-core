/**
 * Screenshots of the screens that were reported as broken.
 *
 * Not an assertion suite -- it captures what an operator now sees, so the
 * before/after is reviewable without a browser on the reviewer's machine.
 * Read-only against the live fleet.
 *
 * Run with: npx playwright test e2e/screenshots.spec.ts
 */
import { test } from "@playwright/test";

const OUT = "test-results/ui";

test("cluster page, with the diagram and the dashboard link", async ({ page }) => {
  await page.goto("/admin/clusters");
  await page.getByText("e2e-muaua9sj").first().click();
  await page.getByRole("img", { name: "Cluster topology" }).waitFor({
    timeout: 30_000,
  });
  // The rings pulse on a 3s cycle; wait past the first beat so the capture is
  // not taken at the one instant the animation starts from.
  await page.waitForTimeout(1_500);
  await page.screenshot({ path: `${OUT}/cluster-detail.png`, fullPage: true });
});

test("the diagram on its own", async ({ page }) => {
  await page.goto("/admin/clusters");
  await page.getByText("e2e-muaua9sj").first().click();
  const diagram = page.getByRole("img", { name: "Cluster topology" });
  await diagram.waitFor({ timeout: 30_000 });
  await page.waitForTimeout(1_500);
  await diagram.screenshot({ path: `${OUT}/topology.png` });
});

test("the fleet page", async ({ page }) => {
  await page.goto("/admin/nodes");
  await page.getByText("10.88.10.49").first().waitFor({ timeout: 30_000 });
  await page.screenshot({ path: `${OUT}/fleet.png`, fullPage: true });
});

test("the provider list", async ({ page }) => {
  await page.goto("/admin/llm/providers");
  await page.getByRole("heading", { name: /providers/i }).waitFor({
    timeout: 30_000,
  });
  await page.waitForTimeout(1_500);
  await page.screenshot({ path: `${OUT}/providers.png`, fullPage: true });
});

test("the cluster's Grafana dashboard", async ({ page, browser }) => {
  await page.goto("/admin/clusters");
  await page.getByText("e2e-muaua9sj").first().click();
  const link = page.getByRole("link", { name: /metrics dashboard/i });
  await link.waitFor({ timeout: 30_000 });
  const href = await link.getAttribute("href");

  // A context of its own. Grafana is a different origin from the console,
  // and this context carries the console's storage state and baseURL; the
  // cross-origin navigation out of it was refused at the connection, while
  // the same URL loads fine from a fresh one.
  const context = await browser.newContext({ viewport: { width: 1600, height: 1200 } });
  const grafana = await context.newPage();
  await grafana.goto(`${href}&from=now-30m&to=now&kiosk=tv`, {
    waitUntil: "domcontentloaded",
  });
  // Grafana renders panels lazily; give the queries time to come back.
  await grafana.waitForTimeout(12_000);
  await grafana.screenshot({ path: `${OUT}/grafana.png`, fullPage: true });
  await context.close();
});

test("the deployment page, with the replicas' logs", async ({ page }) => {
  await page.goto("/admin/deployments");
  await page.getByText("Qwen2.5-0.5B-Instruct").first().click();
  await page
    .getByText(/LLMServer|OpenAiIngress/)
    .first()
    .waitFor({ timeout: 90_000 });
  // The logs card itself: the full page is mostly the panels above it, and
  // the point here is what the panel now contains.
  const card = page
    .locator(".MuiCard-root")
    .filter({ has: page.getByRole("heading", { name: "Logs" }) })
    .first();
  await card.scrollIntoViewIfNeeded();
  await card.screenshot({ path: `${OUT}/deployment-logs.png` });
});

test("the deployment's metrics card, with the gateway figures", async ({ page }) => {
  await page.goto("/admin/deployments");
  await page.getByText("Qwen2.5-0.5B-Instruct").first().click();
  await page
    .getByText(/Measured at the gateway/)
    .waitFor({ timeout: 60_000 });
  const card = page
    .locator(".MuiCard-root")
    .filter({ has: page.getByRole("heading", { name: "Metrics" }) })
    .first();
  await card.scrollIntoViewIfNeeded();
  await card.screenshot({ path: `${OUT}/deployment-metrics.png` });
});

test("the providers table's row expander", async ({ page }) => {
  await page.goto("/admin/llm/providers");
  await page.getByRole("heading", { name: /providers/i }).waitFor({
    timeout: 30_000,
  });
  // The chevron in the first cell is the expander toggle; clicking the row
  // itself lands on the name link and navigates instead.
  await page.locator("tbody tr").first().locator("button").first().click();
  await page.waitForTimeout(4_000);
  await page.screenshot({ path: `${OUT}/providers-expanded.png`, fullPage: true });
});
