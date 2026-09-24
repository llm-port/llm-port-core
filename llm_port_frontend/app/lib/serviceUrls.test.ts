/**
 * The gateway's Swagger UI: same-origin behind nginx, the exposed port in the dev stack.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { GATEWAY_DOCS_PATH, resolveApiDocsUrl } from "./serviceUrls";

function answers(body: string, status = 200) {
  return vi.fn<typeof fetch>().mockResolvedValue(new Response(body, { status }));
}

afterEach(() => vi.unstubAllEnvs());

describe("resolveApiDocsUrl", () => {
  it("uses the same origin when nginx serves the docs (a full install publishes no :8001)", async () => {
    const fetcher = answers('<div id="swagger-ui"></div><script src="/gateway-static/docs/swagger-ui-bundle.js">');
    expect(await resolveApiDocsUrl(fetcher)).toBe(`${window.location.origin}${GATEWAY_DOCS_PATH}`);
    expect(fetcher).toHaveBeenCalledWith(GATEWAY_DOCS_PATH, expect.objectContaining({ credentials: "same-origin" }));
  });

  it("uses the gateway's own port when that path is only the app, missing, or unreachable", async () => {
    const direct = `${window.location.protocol}//${window.location.hostname}:8001/api/docs`;
    expect(await resolveApiDocsUrl(answers('<!doctype html><div id="root"></div>'))).toBe(direct);
    expect(await resolveApiDocsUrl(answers("Not Found", 404))).toBe(direct);
    expect(await resolveApiDocsUrl(vi.fn<typeof fetch>().mockRejectedValue(new TypeError("offline")))).toBe(direct);
  });

  it("keeps an explicitly configured gateway address without asking", async () => {
    vi.stubEnv("VITE_LLM_PORT_API_PORT", "9001");
    const fetcher = answers("swagger-ui");
    expect(await resolveApiDocsUrl(fetcher)).toMatch(/:9001\/api\/docs$/);
    expect(fetcher).not.toHaveBeenCalled();
  });
});
