/**
 * Whether this viewer has asked their system for less motion.
 *
 * CSS can answer this with a media query, but SVG's own animation elements
 * cannot be reached that way -- `<animate>` runs regardless of any stylesheet
 * -- so a diagram that moves has to ask in JavaScript and leave the elements
 * out.
 *
 * It is a live subscription rather than a single read, because the setting can
 * change while a page is open (a system theme switch, an accessibility toggle)
 * and a diagram that keeps pulsing after the operator turned motion off is
 * exactly the thing the setting exists to stop.
 */
import { useEffect, useState } from "react";

const QUERY = "(prefers-reduced-motion: reduce)";

export function useReducedMotion(): boolean {
  // Starts false so the server render and the first client render agree; a
  // viewer who wants stillness gets it on the effect that runs immediately
  // after, before anything has had time to move.
  const [reduced, setReduced] = useState(false);

  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const mql = window.matchMedia(QUERY);
    setReduced(mql.matches);

    const onChange = (event: MediaQueryListEvent) => setReduced(event.matches);
    // Safari below 14 only has the deprecated form, and this is a progressive
    // enhancement -- not worth failing over.
    if (mql.addEventListener) {
      mql.addEventListener("change", onChange);
      return () => mql.removeEventListener("change", onChange);
    }
    mql.addListener(onChange);
    return () => mql.removeListener(onChange);
  }, []);

  return reduced;
}
