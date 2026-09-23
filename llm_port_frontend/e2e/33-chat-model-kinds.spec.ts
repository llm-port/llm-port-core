/**
 * The chat page offers chat models only, with an embedding model behind the
 * same gateway. Needs one of each routed -- on the DGX pair, `qwen-chat` and
 * spark_manager's embedding container routed as `qwen3-embedding-0.6b`.
 */
import { expect, test } from "@playwright/test";

test("the chat model picker leaves out models that cannot chat", async ({ page }) => {
  // Both are behind the gateway, each saying what it is.
  const token = (await page.context().cookies()).find((c) => c.name === "fapiauth")?.value;
  const listed = await page.request.get(`${process.env.LLM_PORT_GATEWAY_URL ?? "http://127.0.0.1:8001"}/v1/models`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  const kinds = Object.fromEntries(((await listed.json()).data as { id: string; kind: string | null }[]).map((m) => [m.id, m.kind]));
  console.log(`  gateway models: ${JSON.stringify(kinds)}`);
  expect(kinds["qwen3-embedding-0.6b"]).toBe("embeddings");

  await page.goto("/chat");
  await page.locator("#model-selector [role='combobox']").click();
  const options = await page.getByRole("option").allInnerTexts();
  console.log(`  chat picker offers: ${options.join(", ")}`);
  await page.screenshot({ path: "../docs/images/found/chat-picker.png" });

  expect(options).toContain("qwen2.5-0.5b-instruct");
  expect(options).not.toContain("qwen3-embedding-0.6b");
});
