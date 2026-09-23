/**
 * A name PII tokenization took out comes back in the chat page's streamed answer.
 *
 * The chat page always streams, and the gateway dropped the token mapping on
 * the streaming path: tokenize mode showed "Hello [PERSON_1]". Needs a PII
 * policy in tokenize mode that scans requests to local models, and the PII
 * module running (`llmport dev up --modules pii`).
 */
import { expect, test } from "@playwright/test";

const ALIAS = "qwen2.5-0.5b-instruct";

test("a tokenized name comes back as the name in a streamed answer", async ({ page }) => {
  test.setTimeout(3 * 60_000);
  await page.goto("/chat");
  const picker = page.locator("#model-selector");
  await expect(picker).toBeVisible({ timeout: 15_000 });
  await picker.getByRole("combobox").click();
  await page.getByRole("option", { name: ALIAS, exact: true }).click();

  const composer = page.getByPlaceholder(/type a message/i);
  await composer.fill("Repeat exactly this sentence and nothing else: Hello Alice Meyer, welcome back.");
  await composer.press("Enter");

  const reply = page.locator('[data-message-role="assistant"]').last();
  await expect(reply).not.toBeEmpty({ timeout: 120_000 });
  let last = "";
  for (let i = 0; i < 20; i += 1) {
    const now = await reply.innerText();
    if (now && now === last) break;
    last = now;
    await page.waitForTimeout(1_000);
  }
  console.log(`  reply: ${last.replace(/\s+/g, " ")}`);
  await page.screenshot({ path: "../docs/images/pipeline/chat-pii-name-streamed.png" });

  expect(last).toContain("Alice Meyer");
  expect(last).not.toContain("[PERSON_");
});
