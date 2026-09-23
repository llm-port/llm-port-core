/**
 * Steps 2-4 of `docs/onboarding-a-node.md`, walked in the console.
 *
 * One machine, which is what the guide describes for a first cluster. Every
 * screen is captured into `docs/images/onboarding/`.
 */
import { expect, test } from "@playwright/test";

const SHOTS = "../docs/images/onboarding";
const MACHINE = "spark-ts3202";
const CLUSTER_NAME = `gpu-box-${Date.now().toString(36).slice(-4)}`;

test.describe.configure({ mode: "serial" });

async function shoot(page: import("@playwright/test").Page, name: string) {
  await page.waitForTimeout(600);
  await page.screenshot({ path: `${SHOTS}/${name}.png` });
  console.log(`  captured ${name}.png`);
}

test("step 2 — the clusters page says what to do next", async ({ page }) => {
  await page.goto("/admin/clusters");
  await page.waitForLoadState("networkidle").catch(() => {});
  await shoot(page, "10-clusters-empty");

  const banner = page.getByTestId("next-step");
  if (await banner.count()) {
    console.log(`  next-step stage: ${await banner.getAttribute("data-stage")}`);
    console.log(`  next-step says: ${(await banner.innerText()).replace(/\s+/g, " ")}`);
  } else {
    console.log("  NO next-step banner on the clusters page");
  }
});

test("step 2 — create a cluster from the one machine", async ({ page }) => {
  await page.goto("/admin/clusters");
  await page.getByRole("button", { name: "Create a cluster" }).last().click();

  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText("Name it")).toBeVisible();
  await shoot(page, "11-cluster-name");

  await dialog.getByLabel("Cluster name").fill(CLUSTER_NAME);
  await dialog.getByRole("button", { name: "Next" }).click();

  await expect(dialog.getByText("Pick the machines")).toBeVisible();
  await dialog.getByLabel(`Use ${MACHINE}`).check();
  await shoot(page, "12-cluster-machines");

  // The guide promises the wizard names the runtime image it will run.
  const body = await dialog.innerText();
  const runtime = body.match(/runs [^\n]*Runtime[^\n]*/i)?.[0];
  console.log(`  runtime named by the wizard: ${runtime ?? "NOT SHOWN"}`);

  await dialog.getByRole("button", { name: "Next" }).click();
  await expect(dialog.getByText(/These machines can reach each other|network/i).first()).toBeVisible({
    timeout: 90_000,
  });
  await shoot(page, "13-cluster-network");
  console.log("--- network step ---");
  console.log(
    (await dialog.innerText())
      .split("\n")
      .slice(0, 25)
      .map((l) => `    ${l}`)
      .join("\n"),
  );

  const create = dialog.getByRole("button", { name: "Create cluster" });
  if (!(await create.isEnabled())) {
    const why = await dialog.innerText();
    console.log(`  CREATE DISABLED. Dialog said:\n${why.slice(0, 900)}`);
    await shoot(page, "13b-cluster-blocked");
    return;
  }

  await create.click();
  await page.waitForTimeout(4000);
  await shoot(page, "14-cluster-created");
  console.log(`  created cluster ${CLUSTER_NAME}`);
});

test("steps 3 and 4 — open the cluster and start it", async ({ page }) => {
  await page.goto("/admin/clusters");
  await page.waitForLoadState("networkidle").catch(() => {});

  const row = page.getByText(CLUSTER_NAME).first();
  if (!(await row.count())) {
    console.log(`  cluster ${CLUSTER_NAME} not in the list — creation did not complete`);
    return;
  }
  await row.click();
  await page.waitForTimeout(2500);
  await shoot(page, "15-cluster-detail");

  const text = await page.locator("body").innerText();
  console.log("--- cluster page controls ---");
  for (const label of ["Start", "Apply", "Plan", "Stop", "ready", "Ready", "stopped"]) {
    if (text.includes(label)) console.log(`    mentions: ${label}`);
  }

  const start = page.getByRole("button", { name: /^start/i }).first();
  if (await start.count()) {
    await start.click();
    console.log("  clicked Start; the runtime image pull can take minutes");
    await page.waitForTimeout(20_000);
    await shoot(page, "16-cluster-starting");
    console.log(
      `  status now: ${(await page.locator("body").innerText()).slice(0, 400).replace(/\s+/g, " ")}`,
    );
  } else {
    console.log("  NO start control found on the cluster page");
  }
});
