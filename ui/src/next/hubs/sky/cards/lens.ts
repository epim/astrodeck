// lens.ts - the lens dial's geometry and its hold-to-learn copy, kept pure so
// both can be tested without mounting a 300 px stage (hub-sky plan A.6, H.1).
//
// FIVE kinds, not the README's seven. The engine carries ERFA ephemerides for
// the Sun, the Moon and seven planets and NOTHING for satellites or comets, so
// SATELLITES and COMETS have no row to filter, no marker to hide and no count to
// print. They are not rendered greyed either: a kind with no data source behind
// it is not a filter anybody can usefully turn on, and drawing one would promise
// a list that can never arrive. See plan H.1.
//
// The seat arithmetic is the prototype's own (proto/logic.js:493): the buttons
// sit on a circle of radius 108 about the stage centre at 150,150, and the
// captions sit 44 px further out. Keeping it here rather than inline in the
// component is what lets a test assert that five kinds come back evenly spaced
// and that the first one is at the top - which is the only part a reader of the
// screenshot can check.

import { SKY_KINDS, KIND_LABEL, type SkyKind } from "../finder";

/** The dial stage. 300x300 with a dashed accent ring at r=115. */
export const LENS_STAGE_PX = 300;
export const LENS_RING_D = 230;

const ORBIT_R = 108;
const ORBIT_C = 150;
const SEAT_PX = 56;
const LABEL_R = ORBIT_R + 44;

/** How long a press must be held before it opens the kind's explanation. The
 *  same 450 ms every hold-to-learn control in the design uses. */
export const LENS_HOLD_MS = 450;

export interface LensSeat {
  kind: SkyKind;
  label: string;
  /** Top-left of the 56 px button, px within the stage. */
  x: number;
  y: number;
  /** Centre of the caption (the caption is translated -50%,-50%). */
  lx: number;
  ly: number;
}

/** Seats for `kinds`, first at the top and going clockwise. */
export function lensSeats(kinds: readonly SkyKind[] = SKY_KINDS): LensSeat[] {
  return kinds.map((kind, i) => {
    const a = (i / kinds.length) * 2 * Math.PI - Math.PI / 2;
    return {
      kind,
      label: KIND_LABEL[kind],
      x: ORBIT_C + ORBIT_R * Math.cos(a) - SEAT_PX / 2,
      y: ORBIT_C + ORBIT_R * Math.sin(a) - SEAT_PX / 2,
      lx: ORBIT_C + LABEL_R * Math.cos(a),
      ly: ORBIT_C + LABEL_R * Math.sin(a),
    };
  });
}

/** One sentence per kind, verbatim from the prototype's INFO table
 *  (proto/logic.js:37-45). Each says what the kind needs from the night, which
 *  is the thing a ring of glyphs cannot. */
export const LENS_LEARN: Record<SkyKind, string> = {
  galaxy:
    "Broadband LRGB targets. Most want 1-3 minute subs and dark skies; moonlight hurts them.",
  nebula:
    "Emission nebulae love narrowband (Ha, OIII, SII) and shrug off moonlight; reflection nebulae need broadband.",
  cluster:
    "Bright and forgiving. Short subs, any filter - great first-night targets.",
  planet:
    "Video, not subs: thousands of millisecond frames, keep the sharpest. Best near transit when they are highest.",
  moon:
    "Video or very short frames. Bright enough to image through thin cloud - and to spoil faint targets near it.",
};

export const LENS_OVERLAY_NOTE =
  "cloud · horizon · wind overlays live under the layers button";
export const LENS_FOOTER =
  "filter: tap a kind to show or hide it · hold to learn · tap outside to close";
export const LENS_FLOOR_CHIP = "above 25° only";
