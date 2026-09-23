/**
 * A vLLM container LLM.Port did not start, found on a machine and routed as
 * it is (Phase 8): from the machine's page, to the providers page, to a real
 * request through the gateway, and back out again.
 *
 * Needs a running vLLM container on spark-3201 that another tool started --
 * on the DGX pair, spark_manager's `spark-llm-Qwen-Qwen3-Embedding-0.6B-8102`.
 */
import { expect, test, type Page } from "@playwright/test";

const SHOTS = "../docs/images/found";
const MACHINE = "10.88.10.71";
const CONTAINER = "spark-llm-Qwen-Qwen3-Embedding-0.6B-8102";
const GATEWAY = process.env.LLM_PORT_GATEWAY_URL ?? "http://127.0.0.1:8001";

async function openMachine(page: Page) {
  await page.goto("/admin/nodes");
  await page.getByText(MACHINE, { exact: true }).first().click();
  await expect(page.getByTestId("found-vllm")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId(`found-${CONTAINER}`)).toBeVisible({ timeout: 30_000 });
}

test("a found container is routed as it is, answers through the gateway, and is let go", async ({ page }) => {
  test.setTimeout(180_000);
  await openMachine(page);
  const row = page.getByTestId(`found-${CONTAINER}`);
  await expect(row).toContainText("running");
  await expect(row).toContainText("Started by spark");
  await expect(row).toContainText("Answers as Qwen/Qwen3-Embedding-0.6B");
  await page.getByTestId("found-vllm").scrollIntoViewIfNeeded();
  await page.getByTestId("found-vllm").screenshot({ path: `${SHOTS}/found-on-machine.png` });

  await page.getByTestId(`found-route-${CONTAINER}`).click();
  const alias = page.getByTestId("found-alias");
  await expect(alias).toHaveValue("qwen3-embedding-0.6b");
  await page.getByRole("dialog").screenshot({ path: `${SHOTS}/found-route-dialog.png` });
  await page.getByTestId("found-route-confirm").click();
  await expect(row).toContainText("Routed as qwen3-embedding-0.6b", { timeout: 30_000 });
  await page.getByTestId("found-vllm").screenshot({ path: `${SHOTS}/found-routed.png` });

  // It is a provider now, owned by the container rather than editable.
  await page.goto("/admin/llm/providers");
  const provider = page.getByRole("row", { name: /qwen3-embedding-0\.6b/ });
  await expect(provider).toBeVisible({ timeout: 30_000 });
  await provider.screenshot({ path: `${SHOTS}/found-provider.png` });

  // A real request through the gateway, under the name it was given.
  const token = (await page.context().cookies()).find((c) => c.name === "fapiauth")?.value;
  const started = Date.now();
  const reply = await page.request.post(`${GATEWAY}/v1/embeddings`, {
    headers: { Authorization: `Bearer ${token}` },
    data: { model: "qwen3-embedding-0.6b", input: ["LLM.Port found this model running"] },
  });
  const body = await reply.json();
  console.log(`  gateway /v1/embeddings: ${reply.status()} in ${Date.now() - started} ms, ` +
    `model=${body.model}, dimensions=${body.data?.[0]?.embedding?.length}`);
  expect(reply.status()).toBe(200);
  expect(body.data[0].embedding.length).toBeGreaterThan(100);

  // And out again: the container keeps running, the name stops routing.
  await openMachine(page);
  page.once("dialog", (d) => d.accept());
  await row.getByRole("button", { name: "Stop routing" }).click();
  await expect(page.getByTestId(`found-route-${CONTAINER}`)).toBeVisible({ timeout: 30_000 });
  await expect(row).toContainText("running");
  const after = await page.request.post(`${GATEWAY}/v1/embeddings`, {
    headers: { Authorization: `Bearer ${token}` },
    data: { model: "qwen3-embedding-0.6b", input: ["still there?"] },
  });
  console.log(`  after stopping routing: ${after.status()}`);
  expect(after.status()).not.toBe(200);
});
