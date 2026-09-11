// wheelRing.ts - where every filter slot sits on the 236 px ring, and when the
// ring stops being the right drawing at all (plan hub-rig.md B.6 item 1, E19).
//
// Pure: numbers in, numbers out. It exists as its own module for two reasons.
//
// FIRST, the geometry is transcribed rather than invented. The prototype
// (`<s>/seams/proto/logic.js:699`) draws the carousel by rotating a <g> and
// then placing the tap targets at the ROTATED angle, so the circles turn while
// the labels stay upright:
//
//   const n = W.length; const R = 74, C = 118; const rot = -(s.fwSlot / n) * 360;
//   const a  = (i / n) * 2 * Math.PI - Math.PI / 2;            // 12 o'clock first
//   const cx = C + R * Math.cos(a), cy = C + R * Math.sin(a);  // inside the <g>
//   const ar = a + rot * D2R;                                  // after rotation
//   const bx = C + R * Math.cos(ar) - 24, by = C + R * Math.sin(ar) - 24;
//
// Two frames of reference, one of which is only correct because the other one
// moved. Getting that wrong puts the labels on the wrong glass, which on this
// screen is a wrong filter name over a wheel that is about to move - so it is
// worth a test that can read the numbers without a DOM.
//
// SECOND, the design draws exactly seven slots and real wheels do not. A ZWO
// EFW is 5, 7 or 8; a Starlight Xpress Maxi is 9; there are 12-slot carousels.
// The circles are sized to the gap between neighbours here, and past twelve the
// ring is abandoned for a list: at 236 px a thirteenth 11 px circle cannot
// carry a legible filter name, and a ring whose labels cannot be read is worse
// than the table it was drawn instead of.

/** The SVG viewBox is this square, and the drawing is scaled to it. */
export const RING_PX = 236;
/** Centre of the carousel, in viewBox units. */
export const RING_C = 118;
/** Radius of the circle the slots sit on. */
export const RING_R = 74;
/** The tap target over each slot: 48 px, so it clears the 44 px floor even
 *  where the drawn circle is 24. Placed by its top-left corner, hence the
 *  half-width offset in the maths below. */
export const SLOT_HIT_PX = 48;
/** Largest and smallest a slot circle may be drawn. 22 is the design's; 12 is
 *  the floor at which a two-character name still fits inside one. */
export const SLOT_R_MAX = 22;
export const SLOT_R_MIN = 12;
/** Above this many slots the ring is not drawn at all - see the header. */
export const RING_MAX_SLOTS = 12;

const D2R = Math.PI / 180;

/**
 * The radius to draw a slot circle at on an `n`-slot wheel.
 *
 * `74 * sin(PI/n)` is HALF the straight-line gap between two neighbouring slot
 * centres, so a circle of that radius touches its neighbour exactly; the -3
 * leaves a visible lane between them and the clamp keeps the design's 22 on a
 * 7-slot wheel (74*sin(PI/7) = 32.1 -> 29 -> 22) and a legible minimum on a
 * crowded one.
 */
export function slotRadius(n: number): number {
  if (!Number.isFinite(n) || n <= 1) return SLOT_R_MAX;
  const touching = Math.floor(RING_R * Math.sin(Math.PI / n)) - 3;
  return Math.max(SLOT_R_MIN, Math.min(SLOT_R_MAX, touching));
}

export interface RingSlot {
  index: number;
  /** Centre of the drawn circle, INSIDE the rotating group. */
  cx: number;
  cy: number;
  /** Top-left of the 48 px hit button, in the page's own frame - i.e. where
   *  the circle has ended up AFTER the group's rotation. */
  bx: number;
  by: number;
  /** The slot's angle before rotation and after it, in degrees, measured from
   *  12 o'clock clockwise. Exported for the test, and for anyone debugging a
   *  wheel that lands one slot out. */
  angleDeg: number;
  rotatedDeg: number;
  /** This is the slot under the light-path marker. */
  current: boolean;
}

export interface RingModel {
  /** `ring` draws the carousel; `list` means the table is the whole surface. */
  mode: "ring" | "list";
  n: number;
  /** Degrees the slot group turns so `current` sits under the top marker. */
  rot: number;
  radius: number;
  slots: RingSlot[];
}

/**
 * The whole ring, for `n` slots with slot `current` at the light path.
 *
 * `current` is clamped rather than validated: a wheel mid-move reports -1 on
 * ASCOM and the store clamps that to 0, and a slot index past the end means the
 * names array and the position disagree - neither is a reason to draw nothing.
 */
export function wheelRing(n: number, current: number): RingModel {
  const count = Number.isFinite(n) ? Math.max(0, Math.floor(n)) : 0;
  const cur = Number.isFinite(current)
    ? Math.max(0, Math.min(count - 1, Math.floor(current)))
    : 0;
  const mode: RingModel["mode"] =
    count >= 1 && count <= RING_MAX_SLOTS ? "ring" : "list";
  if (count === 0) return { mode: "list", n: 0, rot: 0, radius: SLOT_R_MAX, slots: [] };

  const rot = -(cur / count) * 360;
  const half = SLOT_HIT_PX / 2;
  const slots: RingSlot[] = [];
  for (let i = 0; i < count; i++) {
    const a = (i / count) * 2 * Math.PI - Math.PI / 2;
    const ar = a + rot * D2R;
    slots.push({
      index: i,
      cx: RING_C + RING_R * Math.cos(a),
      cy: RING_C + RING_R * Math.sin(a),
      bx: RING_C + RING_R * Math.cos(ar) - half,
      by: RING_C + RING_R * Math.sin(ar) - half,
      angleDeg: (i / count) * 360,
      rotatedDeg: (i / count) * 360 + rot,
      current: i === cur,
    });
  }
  return { mode, n: count, rot, radius: slotRadius(count), slots };
}

/** Where the light-path marker is, so a caller does not re-derive 12 o'clock.
 *  The marker is a fixed tick at the top of the ring; the CHOSEN slot's
 *  rotated centre must land here, and that is the one thing the rotation is
 *  for. */
export const MARKER_POINT = { x: RING_C, y: RING_C - RING_R } as const;

/** The derived slot type (plan E21). The engine has no type enum on the wire -
 *  `server-routes.md` 4.2: "There is no separate 'type' enum ... beyond the
 *  boolean `narrowband` flag and the free-text `name`" - so the word under a
 *  filter name is derived from the two flags that ARE real, in this order:
 *  a slot with no light path is a blackout whatever else it claims. */
export function slotType(
  opaque: boolean | undefined,
  narrowband: boolean | undefined,
): "blackout" | "narrowband" | "broadband" {
  if (opaque) return "blackout";
  if (narrowband) return "narrowband";
  return "broadband";
}

/** The eight filter colours are CSS custom properties (`next.css`, README
 *  "Design tokens") keyed by the filter's own name. A name with no token falls
 *  back to the body text colour rather than to an arbitrary hue - inventing a
 *  colour for "Astrodon 5nm" would put it in the same visual class as a real
 *  token. Case-insensitive, because wheels report "ha", "Ha" and "HA". */
const FILTER_TOKENS = ["L", "R", "G", "B", "Ha", "OIII", "SII", "OSC"] as const;

export function filterColor(name: string | null | undefined): string {
  const n = (name ?? "").trim();
  if (!n) return "var(--text)";
  const hit = FILTER_TOKENS.find((t) => t.toLowerCase() === n.toLowerCase());
  return hit ? `var(--nx-filter-${hit})` : "var(--text)";
}
