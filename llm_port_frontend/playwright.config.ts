import { defineConfig, devices } from "@playwright/test";

/**
 * Browser tests against a running dev stack.
 *
 * ⚠ These specs are **destructive to the live fleet**. `01-cluster-journey`
 * creates a new cluster from the same two physical machines on every run and
 * nothing tears it down, so repeated runs leave behind clusters and
 * deployments that compete for the same nodes. On the DGX pair that stacked
 * three Ray control planes in one container and stopped the cluster serving
 * entirely.
 *
 * The agent now refuses to head a second cluster without stopping the first
 * (see `start_head` / `join_cluster`), which contains the damage — but the
 * suite still needs a teardown, and until it has one, run it against hardware
 * you are willing to disturb.
 *
 * Deliberately not `webServer`-managed: these run against the same backend,
 * gateway and node agents the operator uses, so the thing under test is the
 * real journey rather than a mocked one. Start the stack with
 * `llmport dev up` first.
 *
 * Kept out of the vitest run (`app/**` only) so component tests stay fast and
 * hermetic, and only this suite needs hardware.
 */
export default defineConfig({
  testDir: "./e2e",
  // Real hardware: a two-node fabric plan runs a live TCP challenge, and a
  // re-plan after a stale-plan refresh doubles it. 90s was not enough.
  timeout: 240_000,
  expect: { timeout: 30_000 },
  // The journey is inherently ordered — a cluster must exist before a model
  // can be deployed onto it — so the specs share one browser, in file order.
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:5173",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "off",
  },
  projects: [
    { name: "setup", testMatch: /auth\.setup\.ts/ },
    {
      name: "chromium",
      dependencies: ["setup"],
      use: {
        ...devices["Desktop Chrome"],
        storageState: "e2e/.auth/admin.json",
      },
    },
  ],
});
