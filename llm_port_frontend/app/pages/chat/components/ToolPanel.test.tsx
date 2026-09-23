/**
 * The tool panel fetches the catalogue only while it is on screen.
 *
 * It lives in a drawer that stays mounted when closed. Fetching regardless
 * cost three tool-catalogue requests per new chat, sent in the same instant
 * as the first message.
 */
import { render, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import * as toolsApi from "~/api/tools";

import ToolPanel from "./ToolPanel";

const EMPTY = { tools: [] } as unknown as toolsApi.ToolAvailabilityResponse;

function panel(active: boolean) {
  return (
    <ToolPanel
      active={active}
      sessionId="s1"
      executionMode="server_only"
      localOverrides={new Map()}
      onLocalOverride={() => {}}
    />
  );
}

describe("ToolPanel", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("does not fetch while the drawer is closed", async () => {
    const available = vi.spyOn(toolsApi, "getAvailableTools").mockResolvedValue(EMPTY);
    render(panel(false));
    await new Promise((r) => setTimeout(r, 20));
    expect(available).not.toHaveBeenCalled();
  });

  it("fetches when it is opened", async () => {
    const available = vi.spyOn(toolsApi, "getAvailableTools").mockResolvedValue(EMPTY);
    const { rerender } = render(panel(false));
    rerender(panel(true));
    await waitFor(() => expect(available).toHaveBeenCalledTimes(1));
    expect(available).toHaveBeenCalledWith("s1");
  });
});
