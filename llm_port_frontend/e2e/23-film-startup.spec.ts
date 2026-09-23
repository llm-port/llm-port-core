/**
 * Film the newest gpu-pair cluster coming up: a frame whenever the banner
 * changes, until it is ready or failed.
 */
import { expect, test } from "@playwright/test";

const SHOTS = "../docs/images/onboarding";
const SEQUENCE = "../docs/images/onboarding/sequence";

test("watch the cluster come up, frame by frame", async ({ page }) => {
  test.setTimeout(45 * 60_000);
  await page.goto("/admin/clusters");
  const link = page.getByText(/^gpu-pair-/).first();
  const name = (await link.innerText()).trim();
  await link.click();
  await expect(page).toHaveURL(/\/admin\/clusters\/[0-9a-f-]+$/);
  // The list page has a banner with the same test id; wait for this
  // cluster's own page before reading one, or the first frame is the list's.
  await expect(page.getByRole("heading", { name })).toBeVisible();
  const banner = page.getByTestId("next-step");
  await expect(banner).not.toHaveAttribute("data-stage", "serving");

  let frame = 0;
  let last = "";
  const started = Date.now();
  while (Date.now() - started < 40 * 60_000) {
    const stage = (await banner.getAttribute("data-stage").catch(() => null)) ?? "?";
    const text = (await banner.innerText().catch(() => "")).replace(/\s+/g, " ");
    if (text !== last) {
      frame += 1;
      await page.screenshot({
        path: `${SEQUENCE}/${String(frame).padStart(3, "0")}-${stage}.png`,
      });
      const minutes = ((Date.now() - started) / 60_000).toFixed(1);
      console.log(`  [${minutes}m] #${frame} ${stage}: ${text.slice(0, 240)}`);
      last = text;
    }
    if (!["starting", "no-network"].includes(stage)) break;
    await page.waitForTimeout(5_000);
  }
  await page.waitForTimeout(700);
  await page.screenshot({ path: `${SHOTS}/09-cluster-result.png`, fullPage: true });
});
