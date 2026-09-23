/**
 * Steps 5-6 of the guide: deploy a chat model onto the pair and watch it go
 * from Queued to Serving. Every change of state is a frame.
 */
import { expect, test } from "@playwright/test";

const SHOTS = "../docs/images/onboarding";
const FAQ = "../docs/images/faq";
const SEQUENCE = "../docs/images/onboarding/sequence";
const NAME = "qwen-chat";

test.describe.configure({ mode: "serial" });

test("deploy a chat model from the cluster page", async ({ page }) => {
  await page.goto("/admin/clusters");
  const link = page.getByText(/^gpu-pair-/).first();
  const cluster = (await link.innerText()).trim();
  await link.click();
  await expect(page.getByRole("heading", { name: cluster })).toBeVisible();
  await page.getByRole("button", { name: "Deploy a model" }).first().click();

  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Model").click();
  await page.waitForTimeout(600);
  const options = await page.getByRole("option").allInnerTexts();
  console.log(`  model picker offers: ${options.map((o) => o.replace(/\s+/g, " ")).join(" | ")}`);
  await page.screenshot({ path: `${FAQ}/deploy-model-picker.png` });
  // Newest first: the record with files on this server.
  await page.getByRole("option", { name: /Qwen2\.5-0\.5B-Instruct/ }).first().click();
  await expect(dialog.getByLabel("Model")).toContainText("Qwen2.5-0.5B-Instruct");

  // Opened from a cluster page the cluster is already chosen and the field
  // is absent -- isEnabled() on a missing element waits for the test timeout.
  const clusterField = dialog.getByLabel("Cluster");
  if ((await clusterField.count()) > 0) {
    const current = await clusterField.innerText().catch(() => "");
    if (!current.includes(cluster)) {
      await clusterField.click();
      await page.getByRole("option", { name: cluster }).click();
    }
  }
  await dialog.getByLabel("Name this deployment").fill(NAME);
  await page.waitForTimeout(600);
  await page.screenshot({ path: `${SHOTS}/10-deploy-wizard.png` });
  await dialog.getByRole("button", { name: "Deploy" }).click();
  await expect(page).toHaveURL(/\/admin\/deployments\/[0-9a-f-]{36}/, { timeout: 60_000 });
  console.log(`  deployed -> ${page.url()}`);
});

test("watch it reach Serving", async ({ page }) => {
  test.setTimeout(40 * 60_000);
  await page.goto("/admin/deployments");
  await page.getByText(NAME, { exact: true }).first().click();
  await expect(page).toHaveURL(/\/admin\/deployments\/[0-9a-f-]{36}/);
  await page.waitForTimeout(2000);

  let frame = 200;
  let last = "";
  const started = Date.now();
  while (Date.now() - started < 35 * 60_000) {
    const body = (await page.locator("main, body").first().innerText()).replace(/\s+/g, " ");
    const health = body.match(/Health\s+(\w[\w ]*?)\s+(Copies|Cluster|Model)/)?.[1] ?? "?";
    const status = body.match(/(Queued|Copying the model[^.]*|Starting[^.]*|Serving|Failed[^.]*|Degraded)/)?.[1] ?? "";
    const key = `${health}|${status}`;
    if (key !== last) {
      frame += 1;
      await page.screenshot({ path: `${SEQUENCE}/${frame}-deploy.png` });
      console.log(`  [${((Date.now() - started) / 60_000).toFixed(1)}m] #${frame} health=${health} :: ${status.slice(0, 200)}`);
      last = key;
    }
    if (/Serving/.test(health) || /^Failed/.test(health)) break;
    await page.waitForTimeout(5_000);
  }
  await page.waitForTimeout(1000);
  await page.screenshot({ path: `${SHOTS}/11-deployment.png`, fullPage: true });
});
