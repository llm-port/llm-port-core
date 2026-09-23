/**
 * The onboarding guide, walked as a new operator would walk it.
 *
 * Follows `docs/onboarding-a-node.md` step by step through the console only,
 * and captures each screen into `docs/images/onboarding/` so the guide can
 * show what the operator is looking at.
 *
 * This is a walkthrough, not an assertion suite: where a step cannot be
 * completed it says so and carries on, because the point is to find out what
 * a real run hits.
 *
 * Run with: npx playwright test e2e/10-onboarding-walkthrough.spec.ts
 */
import { expect, test } from "@playwright/test";

const SHOTS = "../docs/images/onboarding";

async function settle(page: import("@playwright/test").Page) {
  await page.waitForLoadState("networkidle").catch(() => {});
  await page.waitForTimeout(700);
}

async function shoot(page: import("@playwright/test").Page, name: string) {
  await settle(page);
  await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: false });
  console.log(`  captured ${name}.png`);
}

test.describe.configure({ mode: "serial" });

test("the fleet page, as the guide sends the operator to it", async ({ page }) => {
  await page.goto("/admin/nodes");
  await settle(page);
  await shoot(page, "01-node-fleet");

  const body = await page.locator("body").innerText();

  // The guide calls this screen "Machines" and its button "Add a machine".
  console.log("--- wording the guide promises vs what is on screen ---");
  for (const word of ["Machines", "Add a machine", "Node Fleet", "Add Node", "Nodes"]) {
    console.log(`    ${body.includes(word) ? "present" : "ABSENT "}  ${word}`);
  }

  // A machine waiting for a decision is the thing the operator came here for.
  const mentionsPending = /pending|waiting|approve|request/i.test(body);
  console.log(`--- fleet page hints at a pending join: ${mentionsPending}`);
});

test("a pending join request is only reachable behind Add Node", async ({ page }) => {
  await page.goto("/admin/nodes");
  await settle(page);

  const add = page.getByRole("button", { name: /add a machine/i }).first();
  expect(await add.count(), "the fleet page has an Add a machine button").toBeTruthy();
  await add.click();
  await settle(page);
  await shoot(page, "02-add-node-dialog");

  const dialog = page.locator('[role="dialog"]').first();
  const text = await dialog.innerText().catch(async () => page.locator("body").innerText());
  console.log("--- Add Node dialog ---");
  console.log(
    text
      .split("\n")
      .slice(0, 45)
      .map((l) => `    ${l}`)
      .join("\n"),
  );

  const approve = page.getByRole("button", { name: /approve/i }).first();
  console.log(`--- an approve control is present: ${(await approve.count()) > 0}`);
});

test("approve the machine that is waiting, and watch it come up", async ({ page }) => {
  await page.goto("/admin/nodes");
  await settle(page);

  await page.getByRole("button", { name: /add a machine/i }).first().click();
  await settle(page);

  const approve = page.getByRole("button", { name: /approve/i }).first();
  if (!(await approve.count())) {
    console.log("  no approve control -- nothing pending, or it is not surfaced here");
    return;
  }

  await shoot(page, "03-pending-join-request");
  await approve.click();
  await settle(page);
  await shoot(page, "04-after-approval");

  // Close whatever is open and look at the fleet.
  await page.keyboard.press("Escape");
  await page.goto("/admin/nodes");
  await settle(page);
  await shoot(page, "05-fleet-after-approval");

  const rows = await page.locator("tbody tr").allInnerTexts();
  console.log("--- fleet rows after approval ---");
  rows.forEach((r) => console.log(`    ${r.replace(/\s+/g, " ").trim()}`));
});
