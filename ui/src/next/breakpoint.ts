// breakpoint.ts - which of the three layouts is on the glass
// (ARCHITECTURE.md section 4).
//
// Phone < 768 <= tablet < 1200 <= desktop. The thresholds are the design's, and
// they are asked as media QUERIES rather than measured from innerWidth so the
// answer changes with a rotation, a split-screen drag or a desktop window
// resize without this module owning a resize listener of its own.
//
// jsdom has no matchMedia, and the DOM tests stub one that answers `false` to
// everything, so a test renders the PHONE layout - the layout the design is
// written for and the one every hub must be correct in first.

import { useSyncExternalStore } from "react";

export type Breakpoint = "phone" | "tablet" | "desktop";

export const TABLET_MIN_PX = 768;
export const DESKTOP_MIN_PX = 1200;

const TABLET_Q = `(min-width: ${TABLET_MIN_PX}px)`;
const DESKTOP_Q = `(min-width: ${DESKTOP_MIN_PX}px)`;

type MQL = {
  matches: boolean;
  addEventListener?: (t: string, l: () => void) => void;
  removeEventListener?: (t: string, l: () => void) => void;
  addListener?: (l: () => void) => void;
  removeListener?: (l: () => void) => void;
};

function mq(q: string): MQL | null {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return null;
  try {
    return window.matchMedia(q) as unknown as MQL;
  } catch {
    // Some embedded webviews throw on an unsupported query rather than
    // answering false. A thrown query is not a wide screen.
    return null;
  }
}

/** The current breakpoint, readable outside React (the shell stamps it on the
 *  root element before the first paint). */
export function getBreakpoint(): Breakpoint {
  if (mq(DESKTOP_Q)?.matches) return "desktop";
  if (mq(TABLET_Q)?.matches) return "tablet";
  return "phone";
}

function subscribe(onChange: () => void): () => void {
  const lists = [mq(TABLET_Q), mq(DESKTOP_Q)].filter((m): m is MQL => m != null);
  for (const m of lists) {
    // `addEventListener` on a MediaQueryList is the modern spelling; Safari
    // below 14 - which is what an old iPad in the field runs - only has the
    // deprecated `addListener`. Both, or the layout never changes on rotate.
    if (typeof m.addEventListener === "function") m.addEventListener("change", onChange);
    else if (typeof m.addListener === "function") m.addListener(onChange);
  }
  return () => {
    for (const m of lists) {
      if (typeof m.removeEventListener === "function") m.removeEventListener("change", onChange);
      else if (typeof m.removeListener === "function") m.removeListener(onChange);
    }
  };
}

export function useBreakpoint(): Breakpoint {
  // The snapshot is a string, so identity is value equality and no cache is
  // needed (unlike the router's Route object).
  return useSyncExternalStore(subscribe, getBreakpoint, () => "phone" as Breakpoint);
}
