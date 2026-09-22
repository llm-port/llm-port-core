/** Render a page component inside a memory router, at a chosen path. */
import type { ReactElement } from "react";
import { MemoryRouter, Route, Routes } from "react-router";
import { render, type RenderResult } from "@testing-library/react";

export interface RenderPageOptions {
  /** The route pattern the page is registered under, e.g. `/x/:id`. */
  path?: string;
  /** The URL to start at. Defaults to `path` with no params substituted. */
  initialPath?: string;
}

export function renderPage(
  element: ReactElement,
  { path = "/", initialPath = path }: RenderPageOptions = {},
): RenderResult {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route path={path} element={element} />
      </Routes>
    </MemoryRouter>,
  );
}
