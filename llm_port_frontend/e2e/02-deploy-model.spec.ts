import { expect, test } from "@playwright/test";

/**
 * The second half of the journey: a model serving on the cluster the first
 * spec built.
 *
 * Real hardware, so the timings are real: the model is copied to every
 * machine that needs it before the first copy starts. The spec reports where
 * the deployment got to rather than pretending a slow engine load is a pass.
 */

test.describe.configure({ mode: "serial" });

const DEPLOYMENT_NAME = `e2e-${Date.now().toString(36)}`;

test("a cluster exists to deploy onto", async ({ page }) => {
  await page.goto("/admin/clusters");
  const cards = page.getByRole("button").filter({ hasText: /^e2e-/ });
  await expect(
    cards.first(),
    "No cluster from the first spec — run 01-cluster-journey first",
  ).toBeVisible();
});

test("the deploy wizard asks three things and nothing about specs", async ({
  page,
}) => {
  await page.goto("/admin/deployments");
  await page.getByRole("button", { name: "Deploy a model" }).click();

  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();

  // The operator picks a model and a size. No spec document, no engine name,
  // no api_version.
  const body = await dialog.innerText();
  for (const jargon of ["v1alpha1", "spec", "engine", "api_version"]) {
    expect(body.toLowerCase(), `"${jargon}" leaked into the deploy dialog`).not.toContain(
      jargon,
    );
  }

  await dialog.getByLabel("Model").click();
  await page.getByRole("option").first().click();
  await expect(dialog.getByLabel("Name this deployment")).not.toHaveValue("");

  // Pick the cluster explicitly.  The wizard only pre-selects when there is
  // exactly one, which is right -- with several it should not guess -- but it
  // means this test cannot assume the fleet is empty.  Runs accumulate
  // clusters, so by the second run "the only cluster" is no longer true.
  const clusterField = dialog.getByLabel("Cluster");
  if (await clusterField.isVisible()) {
    await clusterField.click();
    await page.getByRole("option").first().click();
  }

  await dialog.getByLabel("Name this deployment").fill(DEPLOYMENT_NAME);
  await dialog.getByRole("button", { name: "Deploy" }).click();

  await expect(page).toHaveURL(/\/admin\/deployments\/[0-9a-f-]{36}/, {
    timeout: 60_000,
  });
});

test("the deployment reports its progress in plain words", async ({ page }) => {
  await page.goto("/admin/deployments");
  await expect(page.getByText(DEPLOYMENT_NAME)).toBeVisible();

  const row = page.getByRole("row").filter({ hasText: DEPLOYMENT_NAME });
  const state = await row.innerText();

  // Whatever it is doing, it must say so in words an operator uses — never a
  // raw phase name from the state machine.
  expect(
    /Queued|Copying the model|Starting|Serving|Degraded|Failed/.test(state),
    `Deployment state was not in operator vocabulary: ${state}`,
  ).toBeTruthy();

  test.info().annotations.push({
    type: "deployment-state",
    description: state.replace(/\s+/g, " ").trim(),
  });
});

test("the deployment page explains what it is waiting on", async ({ page }) => {
  await page.goto("/admin/deployments");
  await page.getByText(DEPLOYMENT_NAME).click();
  await expect(page).toHaveURL(/\/admin\/deployments\/[0-9a-f-]{36}/);

  // The ten Phase 6 elements, in the reworked vocabulary.  Matched as
  // headings rather than as loose text: a section's own empty-state sentence
  // ("No metrics observed yet.") contains the section's name, so a substring
  // match resolves to two elements and fails for the wrong reason.
  for (const section of [
    "Health",
    "Copies (ready / wanted)",
    "Machines",
    "Model files",
    "Endpoints",
    "Logs",
    "Metrics",
    "Last checked",
  ]) {
    await expect(
      page.getByText(section, { exact: true }).first(),
      `section "${section}" is missing`,
    ).toBeVisible();
  }
  await expect(
    page.getByRole("button", { name: "Show raw provider status" }),
  ).toBeVisible();
});
