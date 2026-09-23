import { expect, test } from "@playwright/test";

/**
 * The journey backwards: stop every deployment, then delete it, through the
 * screens an operator uses and nothing else.
 *
 * The suite has never had this, which the config says in as many words -- so
 * every run left its deployments behind and the next run competed with them
 * for the same GPUs. Running it first also makes the forward specs mean
 * something: a create that starts from a fleet full of half-finished runs is
 * not the journey a new operator takes.
 *
 * It asserts on destruction being *complete*, not merely acknowledged. A
 * delete that removes the row and leaves the Serve application running, or
 * leaves behind the provider the deployment created, has not deleted
 * anything -- it has hidden it.
 */

test.describe.configure({ mode: "serial" });

/** How long a stop may take before we call it stuck. */
const STOP_BUDGET_MS = 180_000;

/** Operator-facing words for a deployment that is no longer serving. */
const STOPPED_WORDS = /Stopped|Queued|Failed|Deleting/i;

async function deploymentNames(page: import("@playwright/test").Page) {
  await page.goto("/admin/deployments");
  const rows = page.getByRole("row");
  await expect(rows.first()).toBeVisible({ timeout: 30_000 });

  const names: string[] = [];
  for (const row of await rows.all()) {
    // A deployment row has a cell per column. The header and the empty state
    // ("Nothing deployed yet.") are rows too -- the empty state being a
    // single cell spanning the table, which read as row text turned that
    // sentence into a deployment named after itself.
    const cells = await row.getByRole("cell").all();
    if (cells.length < 2) continue;

    const name = (await cells[0].innerText()).split("\n")[0]?.trim();
    if (name) names.push(name);
  }
  return names;
}

test("every deployment can be stopped from the list", async ({ page }) => {
  const names = await deploymentNames(page);
  test.info().annotations.push({
    type: "fleet-before",
    description: names.join(", ") || "(empty)",
  });
  if (names.length === 0) test.skip(true, "No deployments to stop.");

  for (const name of names) {
    const row = page.getByRole("row").filter({ hasText: name }).first();
    const stop = row.getByRole("button", { name: "Stop" });

    // Already stopped deployments show Start instead; that is not a failure,
    // it is the state we are driving towards.
    if ((await stop.count()) === 0) continue;

    await stop.click();

    // The button is the request, not the outcome. What matters is the row
    // stopping saying it serves -- within a budget, so a deployment wedged
    // in "Stopping" is a failure rather than a slow pass.
    await expect(
      page.getByRole("row").filter({ hasText: name }).first(),
      `"${name}" never left the serving state after Stop`,
    ).toContainText(STOPPED_WORDS, { timeout: STOP_BUDGET_MS });
  }
});

test("a stopped deployment leaves nothing serving behind it", async ({ page }) => {
  // Read through the UI, as an operator would: the detail page is where the
  // replica counts live, and a deployment that still has copies ready has not
  // actually stopped whatever it started on the cluster.
  const names = await deploymentNames(page);
  if (names.length === 0) test.skip(true, "No deployments to inspect.");

  for (const name of names) {
    await page.goto("/admin/deployments");
    await page.getByText(name, { exact: true }).first().click();
    await expect(page).toHaveURL(/\/admin\/deployments\/[0-9a-f-]{36}/);

    const copies = page
      .getByText("Copies (ready / wanted)", { exact: true })
      .first()
      .locator("xpath=..");
    const reading = (await copies.innerText()).replace(/\s+/g, " ").trim();
    test.info().annotations.push({ type: `copies:${name}`, description: reading });

    expect(
      /0\s*\/|—|None|no copies/i.test(reading),
      `"${name}" was stopped but still reports copies ready: ${reading}`,
    ).toBeTruthy();
  }
});

test("every deployment can be deleted from the list", async ({ page }) => {
  const names = await deploymentNames(page);
  if (names.length === 0) test.skip(true, "No deployments to delete.");

  for (const name of names) {
    const row = page.getByRole("row").filter({ hasText: name }).first();
    await row.getByRole("button", { name: "Delete" }).click();

    // Deleting a model that is serving is not a thing to do by accident.
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await dialog.getByRole("button", { name: "Delete" }).click();

    await expect(
      page.getByRole("row").filter({ hasText: name }),
      `"${name}" is still listed after being deleted`,
    ).toHaveCount(0, { timeout: STOP_BUDGET_MS });
  }

  expect(
    await deploymentNames(page),
    "deployments remain after deleting every one of them",
  ).toEqual([]);
});

test("deleting the deployments took their providers with them", async ({ page }) => {
  // A provider is created for a deployment when it starts serving and is
  // meant to be removed with it. One left behind is a chat model an operator
  // can still select and nothing behind it -- the failure is silent and it is
  // the user who finds it.
  await page.goto("/admin/llm/providers");
  await expect(page.getByRole("heading").first()).toBeVisible({ timeout: 30_000 });

  const body = await page.locator("body").innerText();
  const orphans = [...body.matchAll(/\b(e2e-[a-z0-9]+|qwen2-5-0-5b-instruct)\b/gi)].map(
    (m) => m[1],
  );

  expect(
    [...new Set(orphans)],
    "providers left behind by deleted deployments",
  ).toEqual([]);
});
