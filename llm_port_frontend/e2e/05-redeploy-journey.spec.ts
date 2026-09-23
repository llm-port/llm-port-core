import { expect, test } from "@playwright/test";

/**
 * Build it back up: deploy a model onto an existing cluster and wait for it to
 * actually serve, using only the screens.
 *
 * The pair to `00-teardown`. Run backwards then forwards and the two together
 * say the thing that matters -- that an operator can take a model off a
 * cluster and put it back without a terminal.
 *
 * Where it differs from `02-deploy-model` is the ending. That spec records
 * whatever state the deployment reached and passes; useful as a smoke test,
 * useless for "it never came up". This one waits for serving and, when that
 * does not happen, fails with what the deployment page itself says is wrong --
 * because a deployment that sits in one phase forever is the failure we keep
 * hitting, and "timed out" does not help anybody find it.
 */

test.describe.configure({ mode: "serial" });

/** The cluster to deploy onto. */
const CLUSTER = process.env.E2E_CLUSTER ?? "workstation-wsl";

const DEPLOYMENT_NAME = `e2e-${Date.now().toString(36)}`;

/** A model load on a cold cache is minutes, not seconds. */
const SERVING_BUDGET_MS = 600_000;

/** What the list says while it is still working. */
const IN_PROGRESS = /Queued|Copying the model|Starting|Preparing|Applying/i;

/** Everything the deployment page knows about why it is not serving. */
async function whyNotServing(page: import("@playwright/test").Page, name: string) {
  await page.goto("/admin/deployments");
  const row = page.getByRole("row").filter({ hasText: name }).first();
  const listed = (await row.innerText().catch(() => "(no row)")).replace(/\s+/g, " ");

  await row.getByText(name, { exact: true }).click().catch(() => {});
  await page.waitForURL(/\/admin\/deployments\/[0-9a-f-]{36}/).catch(() => {});
  const detail = (await page.locator("main").innerText().catch(() => "")).replace(
    /\s+/g,
    " ",
  );
  return `list: ${listed}\n\ndetail: ${detail.slice(0, 1200)}`;
}

test("the fleet starts empty", async ({ page }) => {
  await page.goto("/admin/deployments");
  const rows = page.getByRole("row");
  await expect(rows.first()).toBeVisible({ timeout: 30_000 });

  let deployments = 0;
  for (const row of await rows.all()) {
    if ((await row.getByRole("cell").all()).length >= 2) deployments += 1;
  }
  expect(deployments, "run 00-teardown first: the fleet is not empty").toBe(0);
});

test("the cluster is ready to take a deployment", async ({ page }) => {
  await page.goto("/admin/clusters");
  const card = page.getByText(CLUSTER, { exact: true }).first();
  await expect(card, `no cluster named "${CLUSTER}"`).toBeVisible({ timeout: 30_000 });
});

test("an operator can deploy a model onto it", async ({ page }) => {
  await page.goto("/admin/deployments");
  await page.getByRole("button", { name: "Deploy a model" }).click();

  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();

  await dialog.getByLabel("Model").click();
  await page.getByRole("option").first().click();

  // Choose the cluster by name rather than taking the first: the fleet has
  // more than one, and deploying onto the DGX pair by accident is a slow way
  // to find that out.
  const clusterField = dialog.getByLabel("Cluster");
  if (await clusterField.isVisible()) {
    await clusterField.click();
    await page.getByRole("option", { name: CLUSTER }).click();
  }

  await dialog.getByLabel("Name this deployment").fill(DEPLOYMENT_NAME);
  await dialog.getByRole("button", { name: "Deploy" }).click();

  await expect(page).toHaveURL(/\/admin\/deployments\/[0-9a-f-]{36}/, {
    timeout: 60_000,
  });
});

test("it reaches serving, and says why if it does not", async ({ page }) => {
  test.setTimeout(SERVING_BUDGET_MS + 120_000);

  const deadline = Date.now() + SERVING_BUDGET_MS;
  let last = "";

  while (Date.now() < deadline) {
    await page.goto("/admin/deployments");
    const row = page.getByRole("row").filter({ hasText: DEPLOYMENT_NAME }).first();
    last = (await row.innerText().catch(() => "")).replace(/\s+/g, " ").trim();

    if (/Serving/i.test(last)) {
      test.info().annotations.push({ type: "reached", description: last });
      return;
    }
    // A deployment that has stopped making progress is the bug, so give up on
    // a terminal state immediately rather than burning the whole budget.
    if (/Failed|Degraded/i.test(last)) break;
    expect(
      IN_PROGRESS.test(last) || last === "",
      `deployment state is neither progress nor failure: ${last}`,
    ).toBeTruthy();

    await page.waitForTimeout(15_000);
  }

  const explanation = await whyNotServing(page, DEPLOYMENT_NAME);
  throw new Error(
    `"${DEPLOYMENT_NAME}" never reached Serving within ` +
      `${Math.round(SERVING_BUDGET_MS / 1000)}s.\n\nlast list state: ${last}\n\n${explanation}`,
  );
});

test("the served model is offered as a provider", async ({ page }) => {
  // The other half of the contract that `00-teardown` checks the end of: a
  // deployment that serves should appear as something an operator can select,
  // without them wiring anything up.
  await page.goto("/admin/llm/providers");
  await expect(
    page.getByText(DEPLOYMENT_NAME, { exact: false }).first(),
    "a serving deployment did not produce a provider",
  ).toBeVisible({ timeout: 120_000 });
});
