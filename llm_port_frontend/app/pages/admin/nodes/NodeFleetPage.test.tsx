/**
 * The dashboard's "Onboard a node" opens the add-machine drawer.
 *
 * Found in an end-to-end run: it linked to /admin/nodes/onboarding, a page that
 * does not exist, which fell through to the machine detail route and showed
 * "API 400: Invalid node id."
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { nodesApi } from "~/api/nodes";
import NodeFleetRow from "~/components/dashboard/NodeFleetRow";

import NodeFleetPage from "./NodeFleetPage";

beforeEach(() => {
  vi.spyOn(nodesApi, "list").mockResolvedValue([]);
  vi.spyOn(nodesApi, "listJoinRequests").mockResolvedValue([]);
  vi.spyOn(nodesApi, "installAddress").mockResolvedValue(null as never);
});
afterEach(() => vi.restoreAllMocks());

describe("adding a machine from the dashboard", () => {
  it("links to the machines page with the drawer asked for", () => {
    render(
      <MemoryRouter>
        <NodeFleetRow nodes={[]} onRefreshNode={() => undefined} refreshNode={vi.fn()} />
      </MemoryRouter>,
    );
    expect(screen.getByRole("link", { name: "Onboard a node" })).toHaveAttribute("href", "/admin/nodes?add=1");
  });

  it("opens the drawer when the machines page is asked for it", async () => {
    render(
      <MemoryRouter initialEntries={["/admin/nodes?add=1"]}>
        <NodeFleetPage />
      </MemoryRouter>,
    );
    expect(await screen.findByText("Add a machine", { selector: "h6" })).toBeInTheDocument();
  });

  it("stays closed otherwise", async () => {
    render(
      <MemoryRouter initialEntries={["/admin/nodes"]}>
        <NodeFleetPage />
      </MemoryRouter>,
    );
    await screen.findByText("Machines");
    expect(screen.queryByText("Waiting for approval")).not.toBeInTheDocument();
  });
});
