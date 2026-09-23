/**
 * Pictures of screens that changed after they were first taken. Read-only:
 * every dialog opened here is cancelled, nothing is created.
 */
import { expect, test } from "@playwright/test";

const SHOTS = "../docs/images/onboarding";
const FAQ = "../docs/images/faq";

test("the Add a machine panel", async ({ page }) => {
  await page.goto("/admin/nodes");
  await page.getByRole("button", { name: "Add a machine" }).last().click();
  const panel = page.getByText("On the machine you want to add", { exact: false });
  await expect(panel).toBeVisible();
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${SHOTS}/02-add-a-machine.png` });

  // The token path, shown but not used: creating a token would mint a real
  // credential for a machine that does not exist.
  await page.getByRole("button", { name: "Skip the approval step" }).click();
  await expect(page.getByRole("button", { name: "Create a token" })).toBeVisible();
  // The drawer scrolls on its own; bring the card to its middle.
  await page
    .getByRole("button", { name: "Create a token" })
    .evaluate((el) => el.scrollIntoView({ block: "center" }));
  // toBeVisible() passed while the card was 0px tall, squeezed by the
  // drawer's flex column; measure it.
  const note = await page.getByLabel("Note").boundingBox();
  expect(note?.height ?? 0, "the token card collapsed").toBeGreaterThan(20);
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${FAQ}/skip-the-approval-step.png` });
});

test("the deploy wizard, with its chat name", async ({ page }) => {
  await page.goto("/admin/clusters");
  const link = page.getByText(/^gpu-pair-/).first();
  const cluster = (await link.innerText()).trim();
  await link.click();
  await expect(page.getByRole("heading", { name: cluster })).toBeVisible();
  await page.getByRole("button", { name: "Deploy a model" }).first().click();

  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Model").click();
  await page.getByRole("option", { name: /Qwen2\.5-0\.5B-Instruct/ }).first().click();
  await dialog.getByLabel("Name this deployment").fill("qwen-chat");
  await expect(dialog.getByLabel("Offer in chat as")).toHaveValue("qwen2.5-0.5b-instruct");
  await page.waitForTimeout(600);
  await page.screenshot({ path: `${SHOTS}/10-deploy-wizard.png` });
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toBeHidden();
});
