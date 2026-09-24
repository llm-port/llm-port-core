/**
 * The Hugging Face token: saved once, then only whom it belongs to is shown.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { llmSettings, type HFTokenStatus } from "~/api/llm";

import { HuggingFaceAccessButton, HuggingFaceTokenPanel } from "./HuggingFaceAccess";

const TOKEN = "hf_abcdefghijklmnopqrstuvwxyz0123456789";

const NONE: HFTokenStatus = {
  configured: false, source: null, storage_safe: true, check: null, username: null, token_name: null, role: null,
};
const SIGNED_IN: HFTokenStatus = {
  configured: true, source: "database", storage_safe: true, check: "ok",
  username: "sachith", token_name: "llm-port", role: "read",
};

afterEach(() => vi.restoreAllMocks());

describe("HuggingFaceTokenPanel", () => {
  it("checks and saves a token, then forgets it on the page too", async () => {
    vi.spyOn(llmSettings, "getHFToken").mockResolvedValue(NONE);
    const save = vi.spyOn(llmSettings, "setHFToken").mockResolvedValue(SIGNED_IN);
    const onChanged = vi.fn();
    render(<HuggingFaceTokenPanel onChanged={onChanged} />);

    expect(await screen.findByTestId("hf-status-none")).toBeInTheDocument();
    const input = screen.getByTestId("hf-token-input");
    expect(input).toHaveAttribute("type", "password");
    fireEvent.change(input, { target: { value: `  ${TOKEN}  ` } });
    await userEvent.click(screen.getByTestId("hf-token-save"));

    await waitFor(() => expect(save).toHaveBeenCalledWith(TOKEN));
    expect(await screen.findByTestId("hf-status-ok")).toHaveTextContent("sachith");
    expect(screen.getByTestId("hf-token-input")).toHaveValue("");
    expect(document.body.innerHTML).not.toContain(TOKEN);
    expect(onChanged).toHaveBeenCalledWith(SIGNED_IN);
  });

  it("shows why Hugging Face refused a token", async () => {
    vi.spyOn(llmSettings, "getHFToken").mockResolvedValue(NONE);
    vi.spyOn(llmSettings, "setHFToken").mockRejectedValue(new Error("Hugging Face does not accept this token."));
    render(<HuggingFaceTokenPanel />);

    fireEvent.change(await screen.findByTestId("hf-token-input"), { target: { value: TOKEN } });
    await userEvent.click(screen.getByTestId("hf-token-save"));
    expect(await screen.findByText("Hugging Face does not accept this token.")).toBeInTheDocument();
  });

  it("warns about a write token, and removes one on confirmation", async () => {
    vi.spyOn(llmSettings, "getHFToken").mockResolvedValue({ ...SIGNED_IN, role: "write" });
    const remove = vi.spyOn(llmSettings, "removeHFToken").mockResolvedValue(NONE);
    render(<HuggingFaceTokenPanel />);

    expect(await screen.findByTestId("hf-role-write")).toBeInTheDocument();
    await userEvent.click(screen.getByTestId("hf-token-remove"));
    await userEvent.click(screen.getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(remove).toHaveBeenCalled());
    expect(await screen.findByTestId("hf-status-none")).toBeInTheDocument();
  });

  it("refuses to take a token the server could not protect", async () => {
    vi.spyOn(llmSettings, "getHFToken").mockResolvedValue({ ...NONE, storage_safe: false });
    render(<HuggingFaceTokenPanel />);
    expect(await screen.findByTestId("hf-storage-unsafe")).toBeInTheDocument();
    expect(screen.getByTestId("hf-token-input")).toBeDisabled();
  });
});

describe("HuggingFaceAccessButton", () => {
  it("says whose access the server uses", async () => {
    vi.spyOn(llmSettings, "getHFToken").mockResolvedValue(SIGNED_IN);
    render(<HuggingFaceAccessButton />);
    expect(await screen.findByTestId("hf-access-chip")).toHaveTextContent("Hugging Face: sachith");
  });

  it("stays out of the way for someone who may not read the setting", async () => {
    const spy = vi.spyOn(llmSettings, "getHFToken").mockRejectedValue(new Error("Forbidden"));
    const { container } = render(<HuggingFaceAccessButton />);
    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });
});
