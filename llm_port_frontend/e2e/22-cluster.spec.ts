/**
 * Steps 2-4 of the guide with two machines: create, choose the network, and
 * watch it come up -- the runtime image reaching the second machine from the
 * first. Every screen is kept; the startup is filmed as a sequence so the
 * guide and the FAQ can use whichever frames show what they need.
 */
import { expect, test } from "@playwright/test";

const SHOTS = "../docs/images/onboarding";
const SEQUENCE = "../docs/images/onboarding/sequence";
const NAME = `gpu-pair-${Date.now().toString(36).slice(-4)}`;

test.describe.configure({ mode: "serial" });

async function shoot(page: import("@playwright/test").Page, path: string) {
  await page.waitForTimeout(700);
  await page.screenshot({ path });
  console.log(`  captured ${path.split("/").pop()}`);
}

test("create a cluster from both machines", async ({ page }) => {
  await page.goto("/admin/clusters");
  await page.waitForLoadState("networkidle").catch(() => {});
  await shoot(page, `${SHOTS}/05-clusters-empty.png`);

  await page.getByRole("button", { name: "Create a cluster" }).last().click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Cluster name").fill(NAME);
  await shoot(page, `${SHOTS}/06-cluster-name.png`);
  await dialog.getByRole("button", { name: "Next" }).click();

  await dialog.getByLabel("Use spark-ts3202").check();
  await dialog.getByLabel("Use spark-3201").check();
  await expect(dialog.getByText("leads the cluster", { exact: true })).toBeVisible();
  await shoot(page, `${SHOTS}/07-cluster-machines.png`);
  console.log(
    `  wizard says: ${(await dialog.innerText()).match(/runs[^\n]*/g)?.join(" | ") ?? "no runtime line"}`,
  );

  await dialog.getByRole("button", { name: "Next" }).click();
  await expect(dialog.getByText(/These machines can reach each other/)).toBeVisible({
    timeout: 120_000,
  });
  await shoot(page, `${SHOTS}/08-cluster-network.png`);

  const create = dialog.getByRole("button", { name: "Create cluster" });
  await expect(create).toBeEnabled({ timeout: 60_000 });
  await create.click();
  const refreshed = dialog.getByText(/reported new network details/);
  if (await refreshed.isVisible({ timeout: 15_000 }).catch(() => false)) {
    await create.click();
  }
  await expect(page).toHaveURL(/\/admin\/clusters\/[0-9a-f-]+$/, { timeout: 60_000 });
  console.log(`  created ${NAME} -> ${page.url()}`);
});

test("watch it come up, frame by frame", async ({ page }) => {
  test.setTimeout(45 * 60_000);
  await page.goto("/admin/clusters");
  await page.getByText(NAME).first().click();

  let frame = 0;
  let last = "";
  const started = Date.now();
  while (Date.now() - started < 40 * 60_000) {
    const banner = page.getByTestId("next-step");
    const stage = (await banner.getAttribute("data-stage").catch(() => null)) ?? "?";
    const text = (await banner.innerText().catch(() => "")).replace(/\s+/g, " ");
    if (text !== last) {
      frame += 1;
      await page.screenshot({
        path: `${SEQUENCE}/${String(frame).padStart(3, "0")}-${stage}.png`,
      });
      const minutes = ((Date.now() - started) / 60_000).toFixed(1);
      console.log(`  [${minutes}m] ${stage}: ${text.slice(0, 220)}`);
      last = text;
    }
    if (stage !== "starting") break;
    await page.waitForTimeout(8_000);
  }
  await shoot(page, `${SHOTS}/09-cluster-result.png`);
});
