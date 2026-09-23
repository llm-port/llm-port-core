/**
 * The runtime image transfer is the longest part of starting a cluster, and it
 * showed a bar with no number and a sentence that never changed. Model
 * downloads already had the right answer on the Jobs page -- a thick bar with
 * its percentage beside it -- so the transfer uses the same, one per machine.
 */
import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";

import { NextStepBanner } from "./NextStepBanner";
import type { NextStep } from "./readiness";

const starting: NextStep = {
  stage: "starting",
  title: "Starting the cluster",
  detail: "Receiving the runtime image",
  tone: "progress",
  progressPct: 33,
};

describe("NextStepBanner progress", () => {
  it("draws a bar and the percentage for each machine", () => {
    render(
      <NextStepBanner
        step={starting}
        rows={[
          { label: "spark-ts3202", pct: 33, message: "4.0 GiB of 12.0 GiB at 21 MiB/s" },
          { label: "spark-3201", pct: 70, message: "8.4 GiB of 12.0 GiB at 25 MiB/s" },
        ]}
      />,
    );

    const box = screen.getByTestId("machine-progress");
    expect(within(box).getByText("spark-ts3202")).toBeTruthy();
    expect(within(box).getByText("33%")).toBeTruthy();
    expect(within(box).getByText("70%")).toBeTruthy();
    expect(within(box).getAllByRole("progressbar")).toHaveLength(2);
    expect(within(box).getByText(/8.4 GiB of 12.0 GiB/)).toBeTruthy();
  });

  it("still shows the overall figure when no machine has reported per-row", () => {
    render(<NextStepBanner step={starting} />);
    expect(screen.getByText("33%")).toBeTruthy();
    const bar = screen.getByRole("progressbar");
    expect(bar.getAttribute("aria-valuenow")).toBe("33");
  });

  it("says it does not know yet rather than inventing a number", () => {
    render(<NextStepBanner step={{ ...starting, progressPct: null }} />);
    expect(screen.getByText("—")).toBeTruthy();
    expect(screen.getByRole("progressbar").getAttribute("aria-valuenow")).toBeNull();
  });
});
