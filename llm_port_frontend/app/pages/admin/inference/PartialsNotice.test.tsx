/**
 * A notice that cries wolf is one nobody reads.
 *
 * Every metrics partial used to render as an orange "Partial metrics"
 * warning, including the one that says "these copy counts are from the last
 * cluster check rather than this instant". That is the normal steady state,
 * so a healthy deployment serving two of two replicas carried a warning on
 * the one card that says whether it is healthy — the page contradicting
 * itself.
 *
 * The fix is not to hide it. The sentence is true and worth saying: the
 * figures are a moment old. It is the severity that was wrong.
 */
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { PartialsNotice } from "./common";
import type { MetricsPartial } from "~/api/inference";

const steadyState: MetricsPartial = {
  tier: "serve",
  severity: "info",
  reason: "Copy counts are as of the last cluster check.",
};

const unreachable: MetricsPartial = {
  tier: "serve",
  severity: "warning",
  reason: "no head node reachable; replica counts are the last observed values",
};

/** MUI marks the severity on the alert's root element. */
function alertSeverity(container: HTMLElement): string | null {
  const alert = container.querySelector('[role="alert"]');
  if (!alert) return null;
  if (alert.className.includes("Warning")) return "warning";
  if (alert.className.includes("Info")) return "info";
  return "other";
}

describe("PartialsNotice", () => {
  it("does not warn about the normal steady state", () => {
    const { container } = render(<PartialsNotice partials={[steadyState]} />);
    expect(alertSeverity(container)).toBe("info");
    expect(screen.getByText("About these figures")).toBeInTheDocument();
  });

  it("still warns when something is actually wrong", () => {
    const { container } = render(<PartialsNotice partials={[unreachable]} />);
    expect(alertSeverity(container)).toBe("warning");
    expect(screen.getByText("Partial metrics")).toBeInTheDocument();
  });

  it("warns when any one of several partials warrants it", () => {
    // Downgrading a set because most of it is benign would bury the one that
    // is not.
    const { container } = render(
      <PartialsNotice partials={[steadyState, unreachable]} />,
    );
    expect(alertSeverity(container)).toBe("warning");
  });

  it("treats a partial with no severity as a warning", () => {
    // An older backend sends none. Silence is the wrong default: a partial
    // added without thinking about severity describes a fault far more often
    // than not.
    const { container } = render(
      <PartialsNotice partials={[{ tier: "serve", reason: "probe failed" }]} />,
    );
    expect(alertSeverity(container)).toBe("warning");
  });

  it("still says which tier and why, whatever the severity", () => {
    render(<PartialsNotice partials={[steadyState]} />);
    expect(screen.getByText("serve")).toBeInTheDocument();
    expect(
      screen.getByText(/Copy counts are as of the last cluster check/),
    ).toBeInTheDocument();
  });

  it("renders nothing when every tier reported", () => {
    const { container } = render(<PartialsNotice partials={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("lets a caller override the heading", () => {
    render(<PartialsNotice partials={[steadyState]} title="Cluster figures" />);
    expect(screen.getByText("Cluster figures")).toBeInTheDocument();
  });
});
