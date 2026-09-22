/**
 * The shared stat cards.
 *
 * Extracted because the providers page and the deployment page were showing
 * the same kind of information in two different shapes — compact cards on one,
 * label/value pairs on the other — so the two screens looked like different
 * products describing the same cluster.
 *
 * What is worth pinning is not the layout but the two honesty rules it
 * carries, because both are easy to lose in a later edit and neither fails
 * loudly when it is:
 *
 *   * an absent value is shown as absent, never as `0`;
 *   * a value that has not arrived yet is shown as pending, not as absent.
 */
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { StatCardRow, type StatCardItem } from "./StatCardRow";

const cards: StatCardItem[] = [
  { key: "a", label: "Prefix Cache Hit", value: "73.0%" },
  { key: "b", label: "MTP Acceptance", value: null, emptyHint: "Not enabled." },
];

describe("StatCardRow", () => {
  it("shows the values it was given", () => {
    render(<StatCardRow cards={cards} />);
    expect(screen.getByText("Prefix Cache Hit")).toBeInTheDocument();
    expect(screen.getByText("73.0%")).toBeInTheDocument();
  });

  it("renders a missing value as missing, not as zero", () => {
    // The distinction the whole product turns on: an absent series and an
    // idle engine are different facts, and an operator acts on them
    // differently.
    render(<StatCardRow cards={cards} />);
    expect(screen.getByText("No data")).toBeInTheDocument();
    expect(screen.queryByText("0")).not.toBeInTheDocument();
  });

  it("does not say 'no data' before any data could have arrived", () => {
    // Otherwise every page flashes a wall of "No data" on its way to the
    // numbers, which reads as a broken cluster for as long as it lasts.
    const { container } = render(
      <StatCardRow cards={[{ key: "a", label: "Running", value: null }]} loading />,
    );
    expect(screen.queryByText("No data")).not.toBeInTheDocument();
    expect(container.querySelector(".MuiSkeleton-root")).toBeInTheDocument();
  });

  it("shows a value that has arrived even while others are still loading", () => {
    render(
      <StatCardRow
        cards={[
          { key: "a", label: "Running", value: "2" },
          { key: "b", label: "Waiting", value: null },
        ]}
        loading
      />,
    );
    expect(screen.getByText("2")).toBeInTheDocument();
  });

  it("lets the caller name the empty state", () => {
    render(
      <StatCardRow
        cards={[{ key: "a", label: "Requests", value: null }]}
        emptyLabel="—"
      />,
    );
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("renders a header when given one", () => {
    render(<StatCardRow cards={cards} header={<span>Live Metrics</span>} />);
    expect(screen.getByText("Live Metrics")).toBeInTheDocument();
  });

  it("renders nothing but the frame for no cards", () => {
    // A deployment with no figures at all should not draw an empty grid of
    // placeholders.
    render(<StatCardRow cards={[]} />);
    expect(screen.queryByText("No data")).not.toBeInTheDocument();
  });

  it("does not format anything itself", () => {
    // Percentages, token rates and millisecond figures all round differently,
    // and a component that guessed would be wrong somewhere. The caller
    // formats; this renders what it is handed.
    render(
      <StatCardRow cards={[{ key: "a", label: "Latency", value: "1.47 s" }]} />,
    );
    expect(screen.getByText("1.47 s")).toBeInTheDocument();
  });
});
