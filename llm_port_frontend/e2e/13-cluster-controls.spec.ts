/**
 * What can an operator actually click on a cluster page?
 *
 * The guide's step 4 is "Set the cluster running". The page mentions Start
 * and Stop, but a role=button lookup for Start found nothing -- so either the
 * control is not a button, or it is not there at all.
 */
import { test } from "@playwright/test";

const SHOTS = "../docs/images/onboarding";

function indent(text: string, limit = 30): string {
  return text
    .split("\n")
    .filter((l) => l.trim())
    .slice(0, limit)
    .map((l) => `    ${l}`)
    .join("\n");
}

test("enumerate every control on the cluster page", async ({ page }) => {
  await page.goto("/admin/clusters");
  await page.waitForLoadState("networkidle").catch(() => {});

  console.log("--- clusters page ---");
  console.log(indent(await page.locator("body").innerText(), 25));

  const target = page.getByText(/^gpu-box-/).first();
  if (!(await target.count())) {
    console.log("  no gpu-box cluster on the page");
    return;
  }
  await target.click();
  await page.waitForTimeout(3000);
  await page.screenshot({ path: `${SHOTS}/17-cluster-page.png`, fullPage: true });
  console.log(`  opened ${page.url()}`);

  console.log("--- buttons ---");
  const buttons = page.getByRole("button");
  const count = await buttons.count();
  for (let i = 0; i < count; i++) {
    const b = buttons.nth(i);
    const name = (await b.innerText().catch(() => "")).replace(/\s+/g, " ").trim();
    const aria = await b.getAttribute("aria-label");
    const enabled = await b.isEnabled().catch(() => false);
    const visible = await b.isVisible().catch(() => false);
    if (name || aria) {
      console.log(`    "${name || aria}"  enabled=${enabled} visible=${visible}`);
    }
  }

  console.log("--- page text ---");
  console.log(indent(await page.locator("body").innerText(), 60));
});
