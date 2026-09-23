/**
 * A new chat sends its first message once, and never "resumes" it.
 *
 * The window arrives with the message in navigation state and sends it. React
 * runs mount effects twice in development; the second run fell through to
 * loadHistory, which found the stream this window had just started and
 * reconnected to it -- the reply was read twice, in parallel with the send.
 */
import { StrictMode } from "react";
import { render } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { chatApi } from "~/api/chatClient";

const send = vi.fn();
const loadHistory = vi.fn();

vi.mock("../hooks/useChatStream", () => ({
  useChatStream: () => ({
    messages: [],
    streamingContent: "",
    streamingUsage: null,
    isStreaming: false,
    isLoading: false,
    error: null,
    getResponseMs: () => null,
    send,
    retry: vi.fn(),
    stop: vi.fn(),
    loadHistory,
  }),
}));

import ChatWindow from "./ChatWindow";

function renderAt(entry: { pathname: string; state?: unknown }) {
  return render(
    <StrictMode>
      <MemoryRouter initialEntries={[entry]}>
        <Routes>
          <Route
            path="/chat/:sessionId?"
            element={
              <ChatWindow
                sessionId="s1"
                selectedModel="m"
                onModelChange={() => {}}
                onSessionCreated={() => {}}
                onSessionUpdated={() => {}}
                onToggleSidebar={() => {}}
                sidebarOpen
                languages={[]}
                language="en"
                onLanguageChange={() => {}}
                isSuperuser={false}
                onToolsToggle={() => {}}
                toolsOpen={false}
              />
            }
          />
        </Routes>
      </MemoryRouter>
    </StrictMode>,
  );
}

describe("ChatWindow on mount", () => {
  beforeEach(() => {
    // jsdom has no layout, so no scrollIntoView.
    Element.prototype.scrollIntoView = vi.fn();
    send.mockReset();
    loadHistory.mockReset();
    vi.spyOn(chatApi, "listModels").mockResolvedValue([]);
  });

  it("sends a new chat's first message once and does not reload or resume it", () => {
    renderAt({ pathname: "/chat/s1", state: { initialMessage: "hi", initialModel: "m" } });
    expect(send).toHaveBeenCalledTimes(1);
    expect(send).toHaveBeenCalledWith("hi", "m", undefined);
    expect(loadHistory).not.toHaveBeenCalled();
  });

  it("loads history for an existing chat", () => {
    renderAt({ pathname: "/chat/s1" });
    expect(send).not.toHaveBeenCalled();
    expect(loadHistory).toHaveBeenCalled();
  });
});
