/**
 * The pages arrive in pieces, and the cluster looks alive when it is.
 *
 * Read-only, unlike the rest of this suite: it visits an existing fleet and
 * changes nothing, so it is safe to run repeatedly against the hardware the
 * operator is using.
 *
 * Two complaints drove it, and both were structural rather than cosmetic:
 *
 *  * **Pages were not responsive.** Each one gathered every call it needed in
 *    a single `Promise.all` behind a single spinner, so the screen showed
 *    nothing until the slowest call returned. The slow calls are the ones
 *    that reach hardware -- and a machine being slow to answer is exactly
 *    when the operator opened the page.
 *
 *  * **The cluster diagram never moved and never showed colour.** Its ring
 *    colour comes from `member_status`, which had no writer at all on the
 *    backend, and its allocation arc from `latest_utilization`, which the
 *    fleet list dropped. The picture was right about the shape of the cluster
 *    and silent about its state.
 *
 * So the assertions are about *what is on screen before the slow part
 * arrives*, not about final content. A page that eventually renders
 * everything still fails these if it renders nothing first.
 */
import { expect, test } from "@playwright/test";

/** Generous: the point is that the frame beats the data, not that it is fast. */
const FRAME_BUDGET_MS = 8_000;

test.describe("a page's frame does not wait for its slowest call", () => {
  test("the provider list appears before the runtime reconcile finishes", async ({
    page,
  }) => {
    // `runtimes.list()` reconciles every local runtime against Docker, one
    // container at a time, before it answers. It used to be inside the same
    // `Promise.all` as the providers, so one unresponsive container held up
    // the whole table.
    let runtimesAnswered = false;
    await page.route("**/api/llm/runtimes*", async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 6_000));
      runtimesAnswered = true;
      await route.continue();
    });

    await page.goto("/admin/llm/providers");
    await expect(
      page.getByRole("heading", { name: /providers/i }),
    ).toBeVisible({ timeout: FRAME_BUDGET_MS });
    expect(
      runtimesAnswered,
      "the page waited for the runtime reconcile before drawing anything",
    ).toBe(false);
  });

  test("the cluster list appears before the membership fan-out finishes", async ({
    page,
  }) => {
    // One membership call per cluster. Putting that fan-out in front of the
    // list made the list as slow as the slowest cluster.
    let membersAnswered = false;
    await page.route("**/api/inference/environments/*/nodes", async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 6_000));
      membersAnswered = true;
      await route.continue();
    });

    await page.goto("/admin/clusters");
    await expect(page.getByText("e2e-muaua9sj")).toBeVisible({
      timeout: FRAME_BUDGET_MS,
    });
    expect(
      membersAnswered,
      "the cluster list waited for every cluster's membership call",
    ).toBe(false);
  });

  test("a cluster page draws its frame before its metrics arrive", async ({
    page,
  }) => {
    // Metrics are the call most likely to hang on a sick cluster, and were
    // awaited last of six -- so they were serial on top of everything else.
    let metricsAnswered = false;
    await page.route("**/api/inference/environments/*/metrics*", async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 6_000));
      metricsAnswered = true;
      await route.continue();
    });

    await page.goto("/admin/clusters");
    await page.getByText("e2e-muaua9sj").first().click();
    await expect(
      page.getByRole("heading", { name: "e2e-muaua9sj" }),
    ).toBeVisible({ timeout: FRAME_BUDGET_MS });
    expect(
      metricsAnswered,
      "the cluster page waited for metrics before drawing its frame",
    ).toBe(false);
  });
});

test.describe("the diagram shows the cluster's state", () => {
  test("every machine in the cluster has a coloured, pulsing ring", async ({
    page,
  }) => {
    await page.goto("/admin/clusters");
    await page.getByText("e2e-muaua9sj").first().click();

    const diagram = page.getByRole("img", { name: "Cluster topology" });
    await expect(diagram).toBeVisible({ timeout: 30_000 });

    // Grey was the symptom: `nodeTone` returns "idle" for a null
    // `member_status`, and nothing ever wrote that column.
    const machines = diagram.locator("g[data-address]");
    await expect(machines).toHaveCount(2);

    const pulsing = diagram.locator(
      'circle > animate[attributeName="stroke-opacity"]',
    );
    await expect(pulsing).toHaveCount(2);
  });

  test("the link between the machines carries a pulse", async ({ page }) => {
    await page.goto("/admin/clusters");
    await page.getByText("e2e-muaua9sj").first().click();

    const diagram = page.getByRole("img", { name: "Cluster topology" });
    await expect(diagram).toBeVisible({ timeout: 30_000 });
    // Solid and moving, rather than dashed and still: Ray reports the worker
    // alive, so the edge is live.
    await expect(
      diagram.locator('animate[attributeName="cx"]'),
    ).toHaveCount(1);
  });

  test("each machine says how much of it is committed", async ({ page }) => {
    // The allocation arc reads `latest_utilization`, which the fleet list did
    // not carry -- so it was null for every machine and no arc ever drew.
    await page.goto("/admin/clusters");
    await page.getByText("e2e-muaua9sj").first().click();

    const diagram = page.getByRole("img", { name: "Cluster topology" });
    await expect(diagram).toBeVisible({ timeout: 30_000 });
    await expect(diagram.getByText(/% in use/).first()).toBeVisible();
  });
});

test.describe("navigation is not blocked by a slow page", () => {
  test("the operator can leave a page whose data has not arrived", async ({
    page,
  }) => {
    // The original report: "the ui hangs ... i cannot navigate to other tabs
    // like Node. need to reload the browser". A page that holds the main
    // thread, or that saturates the browser's six connections per origin
    // with stacked polls, takes the whole site with it.
    await page.route("**/api/inference/environments/*/metrics*", async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 20_000));
      await route.continue();
    });

    await page.goto("/admin/clusters");
    await page.getByText("e2e-muaua9sj").first().click();
    await expect(
      page.getByRole("heading", { name: "e2e-muaua9sj" }),
    ).toBeVisible({ timeout: 30_000 });

    // Straight to another section while that call is still outstanding.
    await page.goto("/admin/nodes");
    await expect(page.getByText("10.88.10.49").first()).toBeVisible({
      timeout: 15_000,
    });
  });
});

test.describe("the metrics are reachable from the product", () => {
  test("the cluster page links to its own dashboard", async ({ page }) => {
    // The dashboard was rendered from the template and nothing in the
    // product linked to it, so the panels existed and could not be found.
    await page.goto("/admin/clusters");
    await page.getByText("e2e-muaua9sj").first().click();

    const link = page.getByRole("link", { name: /metrics dashboard/i });
    await expect(link).toBeVisible({ timeout: 30_000 });
    await expect(link).toHaveAttribute("href", /\/d\/vllm-rt-/);
  });

  test("that dashboard exists in Grafana and is this cluster's", async ({
    page,
  }) => {
    await page.goto("/admin/clusters");
    await page.getByText("e2e-muaua9sj").first().click();
    const link = page.getByRole("link", { name: /metrics dashboard/i });
    await expect(link).toBeVisible({ timeout: 30_000 });

    const href = await link.getAttribute("href");
    // Derived from the link rather than hardcoded, so a change to the
    // configured Grafana address cannot leave this passing against an
    // address the product no longer uses.
    const [base, rest] = href!.split("/d/");
    const uid = rest.split("/")[0];

    // The link itself must resolve. It did not: Grafana runs with
    // `serve_from_sub_path`, so a link at the root 301'd to its configured
    // root_url -- http://localhost:3000/grafana/ -- which nothing in the
    // stack listens on. curl and the API were fine; a browser following the
    // link got ERR_CONNECTION_REFUSED, which is the only way anybody
    // actually uses it.
    const page_ = await page.request.get(href!, { maxRedirects: 0 });
    expect(
      page_.status(),
      `the dashboard link redirected to ${page_.headers()["location"]}`,
    ).toBe(200);

    const response = await page.request.get(
      `${base}/api/dashboards/uid/${uid}`,
      { headers: { Authorization: `Basic ${btoa("admin:devpassword")}` } },
    );
    expect(response.ok()).toBeTruthy();
    const body = await response.json();
    expect(body.dashboard.title).toContain("e2e-muaua9sj");
  });

  test("the cluster page shows the counts that come from the cluster", async ({
    page,
  }) => {
    await page.goto("/admin/clusters");
    await page.getByText("e2e-muaua9sj").first().click();
    // `nodes_alive` comes from the observation, which is also what now
    // colours the diagram -- so a real number here and grey rings would be a
    // contradiction rather than two separate bugs.
    await expect(page.getByText("2 of 2 up")).toBeVisible({ timeout: 30_000 });
  });
});

test.describe("what the node agent reports", () => {
  test("the deployment's logs panel shows the replicas' own output", async ({
    page,
  }) => {
    // Under Ray the runtime container idles on `sleep infinity` and each
    // replica writes to its own file inside it, so reading the container's
    // console returned an empty page -- which reads as "this deployment is
    // silent" rather than "this reader is looking in the wrong place".
    await page.goto("/admin/deployments");
    await page.getByText("Qwen2.5-0.5B-Instruct").first().click();
    await expect(page.getByText("Logs").first()).toBeVisible({
      timeout: 30_000,
    });

    // Every line Ray writes names the Serve deployment that produced it; the
    // runtime container's own console carries nothing at all, which is what
    // the panel used to show.
    await expect(
      page.getByText(/LLMServer|OpenAiIngress/).first(),
    ).toBeVisible({ timeout: 90_000 });
  });

  test("every exporter the cluster runs is a scrape target", async ({ page }) => {
    // Derived from `ray.nodes()` this was one endpoint per node -- two here.
    // Ray publishes four: the two node exporters plus the autoscaler and the
    // dashboard/component exporter, neither of which has a node record to be
    // derived from.
    const response = await page.request.get(
      "/api/inference/environments/98c4a8ba-2e53-492a-99a3-f0666d00ddd2/metrics",
    );
    expect(response.ok()).toBeTruthy();
    const body = await response.json();
    expect(body.scrape_targets.length).toBeGreaterThanOrEqual(4);
  });
});

test.describe("the deployment card says whether the hardware is working", () => {
  test("per-request figures appear after the model has been used", async ({
    page,
  }) => {
    // The card could say how many copies were running and nothing about
    // whether they were doing anything. These come from the gateway's own
    // request log, so they survive Prometheus being down -- which is the
    // whole reason for measuring them at the front door.
    await page.goto("/admin/deployments");
    await page.getByText("Qwen2.5-0.5B-Instruct").first().click();

    await expect(page.getByText(/Measured at the gateway/)).toBeVisible({
      timeout: 60_000,
    });
    await expect(page.getByText("Generation speed")).toBeVisible();
    await expect(page.getByText("Time to first token")).toBeVisible();
    // A real number, not a dash: the deployment has served requests.
    await expect(page.getByText(/\d+(\.\d+)? tok\/s/)).toBeVisible();
  });
});

test.describe("the providers page answers on its own", () => {
  test("a cluster-backed provider shows live stat cards, not a link away", async ({
    page,
  }) => {
    // These used to be a link to the deployment, which is a dead end for
    // anybody whose role reaches this page and not that one — and makes
    // "is the hardware working" a two-screen question.
    await page.goto("/admin/llm/providers");
    await page.getByRole("heading", { name: /providers/i }).waitFor({
      timeout: 30_000,
    });

    const row = page.locator("tbody tr").filter({ hasText: "e2e-muaubjj4" }).first();
    await row.locator("button").first().click();

    await expect(page.getByText("Live Metrics")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText("Generation tok/s")).toBeVisible();
    await expect(page.getByText("Prefix Cache Hit")).toBeVisible();
    // The dashboard this links to is the cluster's, not a runtime's.
    await expect(page.getByText("Open Grafana dashboard")).toBeVisible();
  });

  test("a cluster-backed provider is labelled as one, with its model", async ({
    page,
  }) => {
    await page.goto("/admin/llm/providers");
    const row = page.locator("tbody tr").filter({ hasText: "e2e-muaubjj4" }).first();
    await expect(row).toBeVisible({ timeout: 30_000 });
    // It is ours, on our hardware — it was being labelled "Remote Endpoint",
    // and its model column read "No runtime" because a derived provider has
    // no runtime row to join a model through.
    await expect(row.getByText("Cluster")).toBeVisible();
    await expect(row.getByText("Qwen2.5-0.5B-Instruct")).toBeVisible();
    await expect(row.getByText("running")).toBeVisible();
  });
});

test("the cards still offer a way through to the deployment", async ({ page }) => {
  // The cards answer "is the hardware working". Copies, logs and scaling live
  // on the deployment, and somebody reading these numbers is exactly who wants
  // to go there next — so the figures replaced the link, they did not remove
  // the way through.
  await page.goto("/admin/llm/providers");
  await page.getByRole("heading", { name: /providers/i }).waitFor({
    timeout: 30_000,
  });
  const row = page.locator("tbody tr").filter({ hasText: "e2e-muaubjj4" }).first();
  await row.locator("button").first().click();

  const open = page.getByRole("button", { name: "Open the deployment" });
  await expect(open).toBeVisible({ timeout: 30_000 });
  await open.click();

  await expect(
    page.getByRole("heading", { name: "e2e-muaubjj4" }),
  ).toBeVisible({ timeout: 30_000 });
});

test("both screens show the engine's figures in the same shape", async ({
  page,
}) => {
  // The providers page and the deployment page describe the same cluster and
  // used to look like different products doing it — compact cards on one,
  // label/value pairs on the other. They render the same component now.
  await page.goto("/admin/deployments");
  await page.getByText("Qwen2.5-0.5B-Instruct").first().click();

  await expect(page.getByText("Reported by the engine")).toBeVisible({
    timeout: 30_000,
  });
  // The same seven cards the providers expander shows.
  await expect(page.getByText("Prefix Cache Hit")).toBeVisible();
  await expect(page.getByText("KV Cache Usage")).toBeVisible();
  await expect(page.getByText("Generation tok/s")).toBeVisible();
  // And the gateway row beside it, in the same card language.
  await expect(page.getByText(/Measured at the gateway/)).toBeVisible();
});
