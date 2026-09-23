/**
 * Scale the chat deployment from one copy to two -- one per DGX Spark -- and
 * film it: a frame whenever what the page says changes. Console errors and
 * failed API calls are logged, since that is where bugs show first.
 */
import { expect, test, type Page } from "@playwright/test";

const SHOTS = "../docs/images/onboarding";
const SEQUENCE = "../docs/images/scaling";
const NAME = "qwen-chat";
const TARGET = Number(process.env.SCALE_TO ?? 2);
const WATCH_MIN = Number(process.env.WATCH_MIN ?? 25);
// Frames for a second run go in their own series.
const SERIES = process.env.SERIES ?? "";

test.describe.configure({ mode: "serial" });

function watchForTrouble(page: Page, tag: string) {
  page.on("console", (m) => {
    if (m.type() === "error") console.log(`  [${tag}] console error: ${m.text().slice(0, 300)}`);
  });
  page.on("response", (r) => {
    if (r.url().includes("/api/") && r.status() >= 400) {
      console.log(`  [${tag}] ${r.status()} ${r.request().method()} ${r.url().replace(/^https?:\/\/[^/]+/, "")}`);
    }
  });
}

async function openDeployment(page: Page) {
  await page.goto("/admin/deployments");
  await page.getByText(NAME, { exact: true }).first().click();
  await expect(page).toHaveURL(/\/admin\/deployments\/[0-9a-f-]{36}/);
  await expect(page.getByText("Copies (ready / wanted)")).toBeVisible();
}

function summary(body: string) {
  const text = body.replace(/\s+/g, " ");
  const health = text.match(/Health\s+(.+?)\s+Copies \(ready/)?.[1] ?? "?";
  const copies = text.match(/Copies \(ready \/ wanted\)\s+(\d+ \/ \d+)/)?.[1] ?? "?";
  const message = text.match(/Last message\s+(.+?)\s+In chat/)?.[1] ?? "?";
  return { health, copies, message };
}

test("ask for a new number of copies", async ({ page }) => {
  watchForTrouble(page, "scale");
  await openDeployment(page);
  await page.waitForTimeout(1500);
  await page.screenshot({ path: `${SEQUENCE}/${SERIES}000-before.png` });

  await page.getByRole("button", { name: /^Scale$/ }).click();
  const dialog = page.getByRole("dialog");
  const field = dialog.getByLabel("Copies");
  console.log(`  dialog opens with copies = ${await field.inputValue()}`);
  await field.fill(String(TARGET));
  await page.waitForTimeout(400);
  await page.screenshot({ path: SERIES ? `${SEQUENCE}/${SERIES}dialog.png` : `${SHOTS}/13-scale-dialog.png` });
  await dialog.getByRole("button", { name: "Apply" }).click();
  await expect(dialog).toBeHidden({ timeout: 15_000 });
  await page.waitForTimeout(1000);
  const now = summary(await page.locator("main, body").first().innerText());
  console.log(`  right after Apply: ${JSON.stringify(now)}`);
  await page.screenshot({ path: `${SEQUENCE}/${SERIES}001-applied.png` });
});

test("watch it reach that number", async ({ page }) => {
  test.setTimeout(30 * 60_000);
  watchForTrouble(page, "watch");
  await openDeployment(page);

  let frame = 1;
  let last = "";
  let midShot = false;
  const started = Date.now();
  while (Date.now() - started < WATCH_MIN * 60_000) {
    const s = summary(await page.locator("main, body").first().innerText());
    const key = JSON.stringify(s);
    if (key !== last) {
      frame += 1;
      await page.screenshot({ path: `${SEQUENCE}/${SERIES}${String(frame).padStart(3, "0")}-frame.png` });
      console.log(`  [${((Date.now() - started) / 60_000).toFixed(1)}m] #${frame} ${key.slice(0, 300)}`);
      last = key;
    }
    // The middle of a scale-up, for the guide: serving while a copy starts.
    if (!SERIES && /more starting/.test(s.message) && !midShot) {
      await page.screenshot({ path: `${SHOTS}/14-deployment-scaling.png` });
      midShot = true;
    }
    const settled = s.copies === `${TARGET} / ${TARGET}` && /Serving/.test(s.health)
      && !/starting|stopping|cannot/.test(s.message);
    if (settled) break;
    if (/Failed/.test(s.health)) break;
    await page.waitForTimeout(5_000);
  }
  await page.waitForTimeout(1500);
  await page.screenshot({ path: SERIES ? `${SEQUENCE}/${SERIES}result.png` : `${SHOTS}/15-deployment-scaled.png` });
});
