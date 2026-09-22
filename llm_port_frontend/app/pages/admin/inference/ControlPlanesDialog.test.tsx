/**
 * Control-plane dialog tests (Phase 6, gap G-1).
 *
 * The gap this closes is that a control plane could only be created through
 * the API, so first-time setup needed a shell. These assert that the dialog
 * covers the whole lifecycle and that it offers only drivers the server has
 * actually registered.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { inferenceApi } from "~/api/inference";
import { CONTROL_PLANE_ID, controlPlane } from "~/test/inferenceFixtures";

import { ControlPlanesDialog } from "./ControlPlanesDialog";

function renderDialog() {
  const onChanged = vi.fn();
  render(
    <ControlPlanesDialog open onClose={() => {}} onChanged={onChanged} />,
  );
  return { onChanged };
}

describe("ControlPlanesDialog", () => {
  beforeEach(() => {
    vi.spyOn(inferenceApi, "listControlPlanes").mockResolvedValue([controlPlane]);
    vi.spyOn(inferenceApi, "listDrivers").mockResolvedValue(["ray"]);
  });

  it("lists the existing control planes", async () => {
    renderDialog();

    const row = (await screen.findByText("dgx-control-plane")).closest(
      "tr",
    ) as HTMLElement;
    // "ray" is also the default value of the driver Select below the table.
    expect(within(row).getByText("ray")).toBeInTheDocument();
  });

  it("creates a control plane with a registered driver", async () => {
    const create = vi
      .spyOn(inferenceApi, "createControlPlane")
      .mockResolvedValue(controlPlane);
    const { onChanged } = renderDialog();
    await screen.findByText("dgx-control-plane");

    await userEvent.type(screen.getByLabelText("Name"), "second-plane");
    await userEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() =>
      expect(create).toHaveBeenCalledWith({
        name: "second-plane",
        // Defaulted from the server's registered drivers, not hard-coded.
        driver: "ray",
        description: null,
      }),
    );
    expect(onChanged).toHaveBeenCalled();
  });

  it("warns when the server has no driver registered", async () => {
    vi.spyOn(inferenceApi, "listDrivers").mockResolvedValue([]);
    renderDialog();

    expect(
      await screen.findByText(/No driver is registered on this server/),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create" })).toBeDisabled();
  });

  it("flags a control plane whose driver is not registered", async () => {
    vi.spyOn(inferenceApi, "listControlPlanes").mockResolvedValue([
      { ...controlPlane, driver: "dynamo" },
    ]);
    renderDialog();

    // A warning-coloured chip: the plane exists but nothing can operate it.
    const chip = await screen.findByText("dynamo");
    expect(chip.closest(".MuiChip-colorWarning")).not.toBeNull();
  });

  it("toggles a control plane off without deleting it", async () => {
    const update = vi
      .spyOn(inferenceApi, "updateControlPlane")
      .mockResolvedValue({ ...controlPlane, enabled: false });
    renderDialog();
    await screen.findByText("dgx-control-plane");

    await userEvent.click(
      screen.getByRole("checkbox", { name: "Enable dgx-control-plane" }),
    );
    await waitFor(() =>
      expect(update).toHaveBeenCalledWith(CONTROL_PLANE_ID, { enabled: false }),
    );
  });

  it("deletes a control plane", async () => {
    const remove = vi
      .spyOn(inferenceApi, "deleteControlPlane")
      .mockResolvedValue(undefined);
    renderDialog();
    await screen.findByText("dgx-control-plane");

    await userEvent.click(
      screen.getByRole("button", { name: "Delete dgx-control-plane" }),
    );
    await waitFor(() => expect(remove).toHaveBeenCalledWith(CONTROL_PLANE_ID));
  });

  it("reports a failure instead of closing silently", async () => {
    vi.spyOn(inferenceApi, "deleteControlPlane").mockRejectedValue(
      new Error("API 409: environments still reference this control plane"),
    );
    renderDialog();
    await screen.findByText("dgx-control-plane");

    await userEvent.click(
      screen.getByRole("button", { name: "Delete dgx-control-plane" }),
    );
    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText(/still reference/)).toBeInTheDocument();
  });
});
