import { expect, test } from "@playwright/test";

/**
 * The last mile: a model deployed onto the cluster is usable from the chat UI.
 *
 * Everything before this can pass while the journey is still broken for the
 * person it is built for. The cluster can be healthy, the deployment can read
 * "running 1/1", the endpoint record can exist — and the model still never
 * reaches the chat window, because getting there needs a published alias and
 * a gateway route that nothing else exercises.
 *
 * Two traps this spec is written around, both of which it fell into first:
 *
 *   - asserting the reply text matches a pattern also matches the *prompt*
 *     bubble, so the test passes without any completion happening.  The
 *     assertion has to be scoped to the assistant's turn.
 *   - the model list is fetched, so for the first moment after load nothing
 *     is selected.  A human sees the picker fill in; a test that types
 *     immediately does not.
 *
 * Needs the dev stack up (`llmport dev up`) and a running deployment whose
 * spec sets `service.alias`.
 */

const ALIAS = process.env.E2E_MODEL_ALIAS ?? "qwen2.5-0.5b";

/** Wait until the picker has actually settled on a model. */
async function waitForModel(page: import("@playwright/test").Page) {
  await expect(
    page.getByText("No models", { exact: false }),
    "the gateway published no model alias; a deployment without service.alias never reaches chat",
  ).toHaveCount(0, { timeout: 30_000 });

  const picker = page.locator("#model-selector");
  await expect(picker, "the model picker never appeared").toBeVisible({
    timeout: 30_000,
  });
  await expect(picker, `"${ALIAS}" was never selected`).toContainText(ALIAS, {
    timeout: 30_000,
  });
  return picker;
}

test.describe("chat against a served model", () => {
  test("the deployed model is offered and selected", async ({ page }) => {
    await page.goto("/chat");
    await waitForModel(page);
  });

  test("it answers a message end to end", async ({ page }) => {
    await page.goto("/chat");
    await waitForModel(page);

    const composer = page.getByPlaceholder(/type a message/i);
    await expect(composer).toBeVisible();
    await composer.fill("Reply with exactly one word: PONG");
    await composer.press("Enter");

    // Scoped to the assistant's turn.  Matching anywhere on the page would
    // match the prompt that was just typed, which is how this passed while
    // nothing had been sent at all.
    const assistantTurn = page.locator('[data-message-role="assistant"]').last();
    await expect(
      assistantTurn,
      "no assistant reply appeared — the model did not answer",
    ).toBeVisible({ timeout: 120_000 });

    // What is under test is the path: prompt -> gateway -> vLLM on the
    // cluster -> streamed back into this bubble.  Asserting the *words* would
    // be testing a 0.5B model's instruction-following, which it fails
    // cheerfully -- it answered "POKE" once -- and which would make this suite
    // red for a reason that has nothing to do with LLM.Port.
    await expect(assistantTurn).not.toBeEmpty({ timeout: 120_000 });
    const reply = ((await assistantTurn.innerText()) ?? "").trim();
    expect(reply.length, "the assistant bubble came back empty").toBeGreaterThan(0);
  });

  test("no error banner is shown for a healthy send", async ({ page }) => {
    await page.goto("/chat");
    await waitForModel(page);

    const composer = page.getByPlaceholder(/type a message/i);
    await composer.fill("Say OK");
    await composer.press("Enter");

    await expect(
      page.getByText("Please select a model", { exact: false }),
      "the send raced the model list",
    ).toHaveCount(0, { timeout: 30_000 });
  });
});
