import { expect, test } from "@playwright/test";

/**
 * The journey the rework exists to make obvious: enrolled machines → a
 * cluster → a model serving on it.
 *
 * Runs against the real backend and the real node agents, so a failure here
 * is a failure of the product, not of a mock. The specs are ordered and share
 * state deliberately — you cannot deploy onto a cluster that does not exist.
 */

const CLUSTER_NAME = `e2e-${Date.now().toString(36)}`;

test.describe.configure({ mode: "serial" });

test("machines are enrolled and visible to the fleet", async ({ page }) => {
  await page.goto("/admin/nodes");
  // Both DGX machines should be in the fleet before anything else is tried.
  await expect(page.getByText("spark-ts3202")).toBeVisible();
  await expect(page.getByText("spark-3201")).toBeVisible();
});

test("clusters page names the next step instead of leaving you guessing", async ({
  page,
}) => {
  await page.goto("/admin/clusters");

  const banner = page.getByTestId("next-step");
  await expect(banner).toBeVisible();

  // With machines enrolled and (possibly) no cluster, the next step is either
  // to create one or to open an existing one — never "no-nodes".
  await expect(banner).not.toHaveAttribute("data-stage", "no-nodes");
  // Two exist by design when there is no cluster yet: the banner's call to
  // action and the toolbar button. Check the toolbar one.
  await expect(
    page.getByRole("button", { name: "Create a cluster" }).last(),
  ).toBeEnabled();
});

test("the create-cluster wizard walks name → machines → network", async ({
  page,
}) => {
  await page.goto("/admin/clusters");
  await page.getByRole("button", { name: "Create a cluster" }).last().click();

  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText("Name it")).toBeVisible();

  // Step 1 — name.
  await dialog.getByLabel("Cluster name").fill(CLUSTER_NAME);
  await dialog.getByRole("button", { name: "Next" }).click();

  // Step 2 — machines. Both DGX boxes, head chosen for us.
  await expect(dialog.getByText("Pick the machines")).toBeVisible();
  await dialog.getByLabel("Use spark-ts3202").check();
  await dialog.getByLabel("Use spark-3201").check();
  // Exact: the select below is labelled "Which machine leads the cluster",
  // so a substring match resolves to two elements.
  await expect(
    dialog.getByText("leads the cluster", { exact: true }),
  ).toBeVisible();

  // Step 3 — the network, discovered from the machines themselves.
  await dialog.getByRole("button", { name: "Next" }).click();
  await expect(
    dialog.getByText(/These machines can reach each other/),
  ).toBeVisible({ timeout: 60_000 });

  const create = dialog.getByRole("button", { name: "Create cluster" });
  // A plan with blockers must not be applyable — that is the guard rail.
  if (await create.isEnabled()) {
    await create.click();

    // A machine that reports fresh network facts mid-flow is normal, and the
    // wizard re-derives rather than dead-ending. Confirm once more if so.
    const refreshed = dialog.getByText(/reported new network details/);
    if (await refreshed.isVisible({ timeout: 20_000 }).catch(() => false)) {
      // The refreshed plan may itself carry blockers (a machine can be
      // mid-inventory-refresh). Only confirm when it is actually applyable;
      // otherwise report what stopped it rather than clicking a dead button.
      if (await create.isEnabled()) {
        await create.click();
      } else {
        const why = await dialog
          .getByText(/cannot form a cluster yet/)
          .locator("xpath=..")
          .innerText()
          .catch(() => "no reason shown");
        throw new Error(`Re-derived plan was not applyable: ${why}`);
      }
    }

    await expect(page).toHaveURL(/\/admin\/clusters\/[0-9a-f-]{36}/, {
      timeout: 90_000,
    });
  } else {
    const blockers = await dialog
      .getByText(/cannot form a cluster yet/)
      .isVisible();
    expect(
      blockers,
      "Create was disabled but no blocker was shown — the operator would be stuck with no reason given",
    ).toBeTruthy();
    test.info().annotations.push({
      type: "blocked",
      description: "Planner reported blockers; see the dialog text.",
    });
  }
});

test("the cluster page draws the topology and hides the machinery", async ({
  page,
}) => {
  await page.goto("/admin/clusters");
  await page.getByText(CLUSTER_NAME).first().click();
  await expect(page).toHaveURL(/\/admin\/clusters\/[0-9a-f-]{36}/);

  // The picture, not a table of membership rows.
  const topology = page.getByRole("img", { name: "Cluster topology" });
  await expect(topology).toBeVisible();
  await expect(topology.getByText("leads the cluster")).toBeVisible();

  // Machinery is absent at rest…
  await expect(page.getByText("HeadActive")).toHaveCount(0);
  // …and one click away.
  await page.getByText("Advanced", { exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Show raw provider status" }),
  ).toBeVisible();
});

test("no operator-facing screen leaks the domain vocabulary", async ({ page }) => {
  for (const path of ["/admin/clusters", "/admin/deployments"]) {
    await page.goto(path);
    await page.waitForLoadState("networkidle");
    const body = (await page.locator("body").innerText()).toLowerCase();
    for (const jargon of ["inference environment", "control plane", "reconcile"]) {
      expect(body, `"${jargon}" is visible on ${path}`).not.toContain(jargon);
    }
  }
});
