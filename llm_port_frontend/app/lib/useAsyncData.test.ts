/**
 * Polling: the screens that show moving state must not need a manual reload.
 *
 * The case that prompted this: a deployment sat at "Starting" while the model
 * was already answering requests, because the page had loaded once on mount
 * and nothing ever asked again. A screen that is merely stale looks exactly
 * like one that is stuck.
 *
 * On how this is tested: not by waiting for a clock. Fake timers replace the
 * globals for the whole worker and vitest runs several files per worker, so
 * they made unrelated suites fail about one run in three; real timers on a
 * short interval then raced under load. Both were testing setInterval rather
 * than this hook. What the hook actually owes its callers is narrower and
 * fully deterministic: schedule when asked, don't when not, refresh without
 * disturbing what is on screen, and clean up on unmount.
 */
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useAsyncData } from "./useAsyncData";

afterEach(() => vi.restoreAllMocks());

/**
 * The delays this hook scheduled, ignoring the test framework's own.
 *
 * `waitFor` polls on a 50ms interval, so anything at or below that is not
 * ours; every interval the hook creates is a UI refresh cadence in seconds.
 */
function hookIntervals(spy: { mock: { calls: unknown[][] } }): number[] {
  return spy.mock.calls
    .map((call: unknown[]) => Number(call[1]))
    .filter((delay: number) => Number.isFinite(delay) && delay > 1_000);
}

describe("useAsyncData", () => {
  it("schedules nothing unless asked", async () => {
    const setInterval = vi.spyOn(globalThis, "setInterval");
    const fetcher = vi.fn().mockResolvedValue("one");

    renderHook(() => useAsyncData(fetcher, [], { initialValue: "" }));
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));

    // Most admin screens describe things that do not move on their own.
    //
    // Filtered by delay: testing-library's own `waitFor` schedules a 50ms
    // poll, so a bare "was setInterval called" assertion catches the test
    // framework rather than the hook.
    expect(hookIntervals(setInterval)).toEqual([]);
  });

  it("schedules a repeat at the interval it was given", async () => {
    const setInterval = vi.spyOn(globalThis, "setInterval");
    const fetcher = vi.fn().mockResolvedValue("one");

    renderHook(() =>
      useAsyncData(fetcher, [], { initialValue: "", refreshMs: 10_000 }),
    );
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));

    expect(hookIntervals(setInterval)).toEqual([10_000]);
  });

  it("clears its schedule when the component goes away", async () => {
    const clearInterval = vi.spyOn(globalThis, "clearInterval");
    const fetcher = vi.fn().mockResolvedValue("one");

    const { unmount } = renderHook(() =>
      useAsyncData(fetcher, [], { initialValue: "", refreshMs: 10_000 }),
    );
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));

    unmount();
    expect(clearInterval).toHaveBeenCalled();
  });

  it("a silent refresh leaves the previous value on screen", async () => {
    let release: (v: string) => void = () => {};
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce("one")
      .mockImplementation(() => new Promise<string>((r) => (release = r)));

    const { result } = renderHook(() =>
      useAsyncData(fetcher, [], { initialValue: "" }),
    );
    await waitFor(() => expect(result.current.data).toBe("one"));

    // This is what the interval calls. While it is in flight the panel must
    // keep showing "one" and must not fall back to its loading state --
    // otherwise a polling section blinks to a skeleton every few seconds.
    const inFlight = result.current.refresh(true);
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
    expect(result.current.loading).toBe(false);
    expect(result.current.data).toBe("one");

    release("two");
    await inFlight;
    await waitFor(() => expect(result.current.data).toBe("two"));
  });

  it("never lets a poll overlap the request it is repeating", async () => {
    /**
     * The failure this prevents: several of these endpoints ask a node a
     * question and wait for the answer, which can take far longer than the
     * poll interval. Each tick then adds another in-flight request, and a
     * browser only opens about six connections per origin -- so the page
     * starves itself and every other tab, and only a reload (which aborts
     * them) gets the UI back.
     */
    let release: (v: string) => void = () => {};
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce("one")
      .mockImplementation(() => new Promise<string>((r) => (release = r)));

    const { result } = renderHook(() =>
      useAsyncData(fetcher, [], { initialValue: "" }),
    );
    await waitFor(() => expect(result.current.data).toBe("one"));

    // Two silent refreshes while the first is still outstanding: the second
    // and third must be skipped, not queued.
    void result.current.refresh(true);
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
    void result.current.refresh(true);
    void result.current.refresh(true);
    expect(fetcher).toHaveBeenCalledTimes(2);

    release("two");
    await waitFor(() => expect(result.current.data).toBe("two"));

    // Once it has landed, polling resumes normally.
    void result.current.refresh(true);
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(3));
  });

  it("an older, slower answer cannot land on top of a newer one", async () => {
    /**
     * The marketplace: the operator searches "llama", then "qwen", and the
     * Hub answers the second search first. When the first finally lands it
     * must not replace the qwen results under the qwen query.
     */
    const answers: Record<string, (v: string) => void> = {};
    const fetcher = vi.fn((q: string) => new Promise<string>((r) => (answers[q] = r)));

    const { result, rerender } = renderHook(
      ({ q }: { q: string }) => useAsyncData(() => fetcher(q), [q], { initialValue: "" }),
      { initialProps: { q: "llama" } },
    );
    await waitFor(() => expect(answers.llama).toBeDefined());
    rerender({ q: "qwen" });
    await waitFor(() => expect(answers.qwen).toBeDefined());

    await act(async () => {
      answers.qwen("qwen results");
    });
    expect(result.current.data).toBe("qwen results");
    expect(result.current.loading).toBe(false);

    await act(async () => {
      answers.llama("llama results");
    });
    expect(result.current.data).toBe("qwen results");
  });

  it("a loud refresh does show its loading state", async () => {
    let release: (v: string) => void = () => {};
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce("one")
      .mockImplementation(() => new Promise<string>((r) => (release = r)));

    const { result } = renderHook(() =>
      useAsyncData(fetcher, [], { initialValue: "" }),
    );
    await waitFor(() => expect(result.current.data).toBe("one"));

    // An explicit refresh (a button, a mutation) may say so.
    const inFlight = result.current.refresh();
    await waitFor(() => expect(result.current.loading).toBe(true));

    release("two");
    await inFlight;
    await waitFor(() => expect(result.current.loading).toBe(false));
  });
});
