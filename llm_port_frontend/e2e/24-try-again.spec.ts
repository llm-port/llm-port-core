/**
 * A cluster that failed to start: what the operator sees, and Try again.
 * The failed screen is kept for the FAQ; the retry is filmed.
 */
import { expect, test } from "@playwright/test";

const SHOTS = "../docs/images/onboarding";
const FAQ = "../docs/images/faq";
const SEQUENCE = "../docs/images/onboarding/sequence";

test("the failure explains itself, and Try again resumes", async ({ page }) => {
  test.setTimeout(45 * 60_000);
  await page.goto("/admin/clusters");
  const link = page.getByText(/^gpu-pair-/).first();
  const name = (await link.innerText()).trim();
  await link.click();
  await expect(page.getByRole("heading", { name })).toBeVisible();
  const banner = page.getByTestId("next-step");
  await expect(banner).not.toHaveAttribute("data-stage", "serving");

  const stage = await banner.getAttribute("data-stage");
  console.log(`  before: ${stage}: ${(await banner.innerText()).replace(/\s+/g, " ").slice(0, 300)}`);
  if (stage === "degraded") {
    await page.waitForTimeout(700);
    await page.screenshot({ path: `${FAQ}/cluster-failed-with-reason-and-try-again.png` });
    await banner.getByRole("button", { name: "Try again" }).click();
    console.log("  pressed Try again");
  }

  let frame = 100;
  let last = "";
  const started = Date.now();
  while (Date.now() - started < 40 * 60_000) {
    const now = (await banner.getAttribute("data-stage").catch(() => null)) ?? "?";
    const text = (await banner.innerText().catch(() => "")).replace(/\s+/g, " ");
    if (text !== last) {
      frame += 1;
      await page.screenshot({ path: `${SEQUENCE}/${frame}-${now}.png` });
      console.log(`  [${((Date.now() - started) / 60_000).toFixed(1)}m] #${frame} ${now}: ${text.slice(0, 260)}`);
      last = text;
    }
    if (["ready", "degraded"].includes(now) && Date.now() - started > 20_000) break;
    await page.waitForTimeout(4_000);
  }
  await page.waitForTimeout(1000);
  await page.screenshot({ path: `${SHOTS}/09-cluster-result.png`, fullPage: true });
});
