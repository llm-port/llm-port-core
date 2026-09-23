/**
 * When a cluster cannot start, does the console say why?
 *
 * The runtime image that loaded did not match the digest the catalogue pins,
 * and the integrity check refused it — correctly. The question this captures
 * is whether an operator can tell that from the screen.
 */
import { test } from "@playwright/test";

const SHOTS = "../docs/images/onboarding";

test("the cluster page explains a failed start", async ({ page }) => {
  await page.goto("/admin/clusters");
  await page.waitForLoadState("networkidle").catch(() => {});

  const target = page.getByText(/^gpu-box-/).first();
  if (!(await target.count())) {
    console.log("  no gpu-box cluster listed");
    return;
  }
  await target.click();
  await page.waitForTimeout(3000);
  await page.screenshot({ path: `${SHOTS}/09-cluster-failed.png`, fullPage: true });

  const text = await page.locator("body").innerText();
  console.log("--- cluster page ---");
  console.log(
    text
      .split("\n")
      .filter((l) => l.trim())
      .slice(0, 45)
      .map((l) => `    ${l}`)
      .join("\n"),
  );

  // Does the reason reach the operator, or only the database?
  const tells = {
    "names a digest mismatch": /pin|digest|sha256|does not match|mismatch/i.test(text),
    "names the image": /ray-vllm-gb10|runtime image/i.test(text),
    "offers a retry": /retry|try again|start/i.test(text),
  };
  console.log("--- what the page conveys ---");
  for (const [what, ok] of Object.entries(tells)) {
    console.log(`    ${ok ? "yes" : "NO "}  ${what}`);
  }
});
