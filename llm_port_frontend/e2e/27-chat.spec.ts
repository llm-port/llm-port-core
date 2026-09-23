/**
 * Step 7 of the guide: the served model, in chat.
 *
 * The deployment made in 26 predates the wizard's chat-name field, so it is
 * not offered in chat. That is a state worth a picture of its own (the FAQ
 * has it), and then the fix from the deployment page, and then a real reply.
 */
import { expect, test } from "@playwright/test";

const SHOTS = "../docs/images/onboarding";
const FAQ = "../docs/images/faq";
const NAME = "qwen-chat";
const ALIAS = "qwen2.5-0.5b-instruct";

test.describe.configure({ mode: "serial" });

test("offer the deployment in chat from its page", async ({ page }) => {
  await page.goto("/admin/deployments");
  await page.getByText(NAME, { exact: true }).first().click();
  await expect(page).toHaveURL(/\/admin\/deployments\/[0-9a-f-]{36}/);
  const inChat = page.getByTestId("chat-alias");
  await expect(inChat).toBeVisible();

  if (!(await inChat.innerText()).includes(ALIAS)) {
    await page.waitForTimeout(500);
    await page.screenshot({ path: `${FAQ}/deployment-not-offered-in-chat.png` });
    await page.getByRole("button", { name: /Offer in chat|Change/ }).click();
    const dialog = page.getByRole("dialog");
    const field = dialog.getByLabel("Offer in chat as");
    await expect(field).toHaveValue(ALIAS);
    await page.waitForTimeout(300);
    await page.screenshot({ path: `${FAQ}/offer-in-chat-dialog.png` });
    await dialog.getByRole("button", { name: "Save" }).click();
  }
  await expect(inChat).toContainText(`offered as ${ALIAS}`, { timeout: 30_000 });
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${SHOTS}/11-deployment.png`, fullPage: true });
});

test("chat with it", async ({ page }) => {
  test.setTimeout(5 * 60_000);
  await page.goto("/chat");
  const picker = page.locator("#model-selector");
  // Publication runs on the reconciler's next pass; the list is fetched on
  // load, so look again until the alias is there.
  await expect(async () => {
    await page.reload();
    await expect(picker).toBeVisible({ timeout: 15_000 });
    await picker.getByRole("combobox").click();
    const offered = await page.getByRole("option").allInnerTexts();
    console.log(`  chat offers: ${offered.join(" | ")}`);
    await expect(page.getByRole("option", { name: ALIAS, exact: true })).toBeVisible({
      timeout: 2_000,
    });
  }).toPass({ timeout: 3 * 60_000, intervals: [5_000] });
  await page.getByRole("option", { name: ALIAS, exact: true }).click();
  await expect(picker).toContainText(ALIAS);

  const composer = page.getByPlaceholder(/type a message/i);
  await composer.fill("In one sentence: what does a two-machine GPU cluster let me do?");
  await composer.press("Enter");

  // Scoped to the assistant's turn: matching the page would match the prompt.
  const reply = page.locator('[data-message-role="assistant"]').last();
  await expect(reply).toBeVisible({ timeout: 120_000 });
  await expect(reply).not.toBeEmpty({ timeout: 120_000 });
  // Let the stream finish before the picture.
  let last = "";
  for (let i = 0; i < 20; i += 1) {
    const now = await reply.innerText();
    if (now && now === last) break;
    last = now;
    await page.waitForTimeout(1_000);
  }
  console.log(`  reply: ${last.replace(/\s+/g, " ").slice(0, 200)}`);
  await page.screenshot({ path: `${SHOTS}/12-chat.png` });
});
