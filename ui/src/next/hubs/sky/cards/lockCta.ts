// lockCta.ts - what the lock card's 56 px button says, what colour it is and
// what pressing it does (hub-sky plan A.7, lifted from proto/logic.js:359-360).
//
// WHY THIS IS A PURE FUNCTION AND NOT FIVE TERNARIES IN THE CARD. The five
// cases are a PRIORITY ORDER, and the order is the whole design: a target that
// is both behind a tree and under cloud must say BEHIND OBSTRUCTION, because the
// cloud will pass and the tree will not. Written inline, that order is invisible
// and the next edit reorders it by accident. Written here, a test pins it.
//
// The Moon and the planets do not route to the quick-session sheet at all
// (plan H.2): `POST /api/flows/quick` builds a deep-sky night, and a two-hour
// LRGB cycle on Jupiter is not a thing anybody wants. They go to Rig - Capture
// in video mode, which is what the prototype does too.
//
// A SATELLITE DOES NOT ROUTE THERE EITHER, and for exactly the same reason
// (D-SKY-1). A pass is minutes long and crosses the whole sky; a quick session
// would queue a deep-sky night on an object that has left the frame before the
// first sub finishes. Its CTA opens the passes list, which is the only answer
// the rig can actually give about a satellite: when to look, and where.
//
// A COMET keeps the ordinary IMAGE cta. It IS a deep-sky-shaped target - it
// takes subs through the same filters, and the only thing that makes it
// different is that it moves, which is a plate-solve problem and not a CTA one.

import type { SkyTarget } from "../finder";

export type LockCtaKind =
  | "connect" | "obstructed" | "video" | "passes" | "clouded" | "image";

/** No pass clears the horizon inside the search window. Honest-disabled with
 *  this as the reason: the button would open a list with nothing in it. */
export const NO_PASSES_REASON = "No pass above the horizon in the next 24 hours.";

export interface LockCta {
  kind: LockCtaKind;
  label: string;
  /** Which `ActionButton` kind draws it. There is no `warn` kind in the
   *  primitives library, so the clouded case renders as `secondary` and carries
   *  its warning in the LABEL - which is the rule anyway (shape and text carry
   *  state, never hue alone). */
  button: "primary" | "secondary" | "danger";
}

export interface LockCtaInput {
  name: string;
  kind: SkyTarget["kind"];
  obstructed: boolean;
  clouded: boolean;
  /**
   * How many passes the rig found in the search window, or `null` when nobody
   * has answered yet. Read ONLY for a satellite. `0` and `null` are different
   * answers - "there are none" against "we have not looked" - and only the
   * first is a reason to lock the button.
   */
  passCount?: number | null;
}

/** True for the bodies that want video rather than a stack of subs. */
export function isVideoTarget(kind: SkyTarget["kind"]): boolean {
  return kind === "moon" || kind === "planet";
}

export function lockCta(lock: LockCtaInput, equipConnected: boolean): LockCta {
  // BEFORE the connect check, and that is the point: a satellite pass is
  // something you look at, and the answer to "when does the ISS come over" does
  // not depend on whether a camera is plugged in. Every other CTA on this card
  // commands the rig; this one does not.
  if (lock.kind === "satellite") {
    return {
      kind: "passes",
      label: lock.passCount === 0
        ? "NO PASSES IN 24 H"
        : `NEXT PASS - ${lock.name.toUpperCase()}`,
      button: "secondary",
    };
  }
  if (!equipConnected) {
    return { kind: "connect", label: "CONNECT THE RIG FIRST", button: "secondary" };
  }
  if (lock.obstructed) {
    return {
      kind: "obstructed",
      label: "BEHIND OBSTRUCTION · PICK ANOTHER",
      button: "danger",
    };
  }
  if (isVideoTarget(lock.kind)) {
    return {
      kind: "video",
      label: `RECORD ${lock.name.toUpperCase()} · VIDEO`,
      button: "primary",
    };
  }
  if (lock.clouded) {
    return { kind: "clouded", label: "CLOUDED NOW · IMAGE ANYWAY", button: "secondary" };
  }
  return { kind: "image", label: `IMAGE ${lock.name}`, button: "primary" };
}

/** The toast a press raises before it navigates, or null when it just goes. */
export function ctaToast(kind: LockCtaKind): { level: "warning" | "info"; title: string } | null {
  if (kind === "connect") {
    return { level: "info", title: "Connect the rig once - the profile remembers your gear." };
  }
  if (kind === "clouded") {
    return {
      level: "warning",
      title:
        "Cloud hold armed - the run pauses at a frame boundary until it clears, " +
        "then re-centers and resumes.",
    };
  }
  return null;
}

/** The honest-locked reason for a target below the horizon the user drew, or
 *  null. Names the site, because the horizon belongs to the site and not to the
 *  app - the fix is one tap away under the site pill. */
export function obstructedReason(name: string, siteName: string): string {
  return (
    `${name} is below the horizon you drew for ${siteName}. ` +
    "Pick another target, or edit the horizon under the site pill."
  );
}
