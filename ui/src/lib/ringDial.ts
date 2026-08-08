// ringDial.ts — the seat geometry behind BOTH camera-dial layouts: the quarter
// arc that fans out of the parked disc, and the full ring a crowded category
// opens instead (2026-08-08).
//
// WHY THIS FILE EXISTS AT ALL. The dial shipped with a header that stated its
// own limit honestly — "five items over a quarter turn are 22.5° apart and at
// R=140 two 48px items clear each other by 6.6px" — and was then handed 13
// exposures and 10 gains. 13 items over 90° are 7.5° apart, a chord of
// 2·140·sin(3.75°) = 18.3px between 48px chips: about three deep. The operator:
// "the exposures are too densely populated. I can't actually read any of them."
// Nothing in the code disagreed, because the limit lived in a comment and not
// in a function anything called. So the limit lives here now, the layouts are
// derived from it, and a test asserts no two seats are closer than a chip.
//
// ─────────────────────────────────────────────────────────── the two layouts
//
// THE ARC (unchanged, ≤5 seats). A disc parked in a corner, options fanned over
// the quarter that a right thumb pivoting at that corner can sweep without the
// hand leaving the phone. Everything about it is right except how much it holds.
//
// THE RING (new, for anything the arc cannot seat). Asked for directly: "it
// should pop up a new dialog that has the exposure icon in the middle and have
// a ring of exposures around it. Think of the trefoil design of the radiation
// icon… The same goes for anything under the speed dial that has more than 4
// options."
//
// ────────────────────────────────────── why the ring is NOT drawn in the corner
//
// The obvious move — keep the corner anchor and inflate the arc to a full
// circle — is arithmetically worse than the bug. A right thumb pivoting at the
// bottom-right corner sweeps a quarter-disc; the largest circle you can inscribe
// in a 90° sector of radius Rq has radius Rq·sin45°/(1+sin45°) = 0.414·Rq. The
// arc already commits the thumb to Rq = DIAL_R + DIAL_ITEM_PX/2 = 164px, so a
// circle wholly inside that sweep has radius 0.414·164 = 68px including the chip
// halo — a RING radius of 44px, whose 13-item chord is 2·44·sin(13.85°) = 21px.
// That is DENSER than the 18.3px we are fixing. Two concentric quarter arcs fail
// for the same reason from the other side: a quarter arc seats 5 chips at R=140
// and 3 at R=84, so 8, not 13; the whole reachable quarter-disc is π·164²/4 =
// 21,124px², which is 9 chips of 48×48 even packed perfectly. THIRTEEN READABLE
// CHIPS DO NOT FIT IN THE THUMB'S SWEEP, IN ANY ARRANGEMENT. So the ring cannot
// be corner-anchored, and pretending otherwise would just ship a smaller version
// of the same bug.
//
// ───────────────────────────────────────────── how the reach is paid back
//
// Not by shrugging. Three things, each of which the arc did not do:
//
//  1. THE RING IS THE SMALLEST CIRCLE THAT SEATS ITS OPTIONS LEGIBLY, never the
//     biggest the screen allows — because radius IS thumb travel. `ringRadius`
//     returns a minimum, not a fit-to-stage maximum.
//  2. ITS CENTRE IS CLAMPED TOWARD THE THUMB. `ringPlacement` puts the hub at
//     the point NEAREST the disc the operator just touched at which the whole
//     ring is still on screen. It never centres on the stage. On a phone the
//     disc usually ends up inside the ring, so the near arc lands under the
//     thumb where the old arc was.
//  3. THE CURRENT VALUE SITS AT THE NEAR POLE — the point of the ring closest to
//     that same disc — and list order runs clockwise from it. So the two picks
//     that actually happen in the dark, one stop longer and one stop shorter,
//     are the two chips either side of where the thumb already is, every time,
//     for every category. Only a deliberate jump (0.3s → 300s) costs a reach,
//     and that is a decision you make with your eyes anyway.
//
// The honest cost, stated: worst-case travel goes from 164px (arc) to
// |hub−disc| + r + 24. Item (2) is precisely the rule that minimises that
// quantity subject to the ring being readable at all.
//
// ─────────────────────────────────────────────────────────────── not grouped
//
// Exposures are logarithmic (0.3s…300s, three decades) and sectoring the ring by
// decade was considered and dropped. The dial owns no camera state and is handed
// `{id,label}` pairs — it would have to PARSE "0.3s"/"2m" to know a decade, i.e.
// teach a generic control what an exposure is, which is the coupling this
// component was built to avoid. The magnitude is already carried, twice, without
// that: the order is monotonic and runs clockwise, and the current value is
// pinned at the near pole. "Longer is clockwise from your thumb" needs no sectors.
//
// No React, no `window`, no I/O — for the reason rotatorDial.ts gives: the part
// that can be wrong in a way nobody notices until they are at the scope in the
// dark is the part that must be testable without a DOM.

/** Disc diameter — 56px, the house one-tap hero size (`.tap-lg`). */
export const DIAL_DISC_PX = 56;

/** Chip footprint on the arc or the ring. 48 is the spec's chip minimum, above
 *  the app's 44px tap floor because these are aimed at over a live image. */
export const DIAL_ITEM_PX = 48;

/** Clearance two neighbouring chips must keep.
 *
 *  NOT invented: it is the shipped arc's own worst case. Five chips over a
 *  quarter turn at R=140 are 22.5° apart, a chord of 2·140·sin(11.25°) = 54.6px
 *  between 48px chips — 6.6px of air, at the density the operator accepted.
 *  Floored to a whole pixel so the constant is the guarantee and the arc clears
 *  it rather than defining it. Every layout here is checked against this. */
export const DIAL_CLEAR_PX = 6;

/** Centre-to-centre distance two chips must keep: footprint plus clearance. */
export const SEAT_PITCH_PX = DIAL_ITEM_PX + DIAL_CLEAR_PX;

/** Radius the quarter arc sits at, and the radius below which it is abandoned
 *  rather than drawn overlapping. See DIAL_CLEAR_PX for where 140 comes from. */
export const DIAL_R = 140;
export const DIAL_MIN_R = 124;

/** The ring's hub: the category's icon, its name, its current value, and the way
 *  back. It is the recovery control, so it is the biggest target in the overlay
 *  — the `.tap-lg` 56px hero plus the ~24px a second readout line needs. */
export const RING_HUB_PX = 80;

/** How much air the ring keeps from the edge of the screen: the same 12px inset
 *  the disc itself parks at, so the overlay and the control that opened it agree
 *  about where the screen ends. */
export const RING_EDGE_PX = 12;

const clamp = (v: number, lo: number, hi: number): number =>
  hi < lo ? (lo + hi) / 2 : Math.min(hi, Math.max(lo, v));
const round1 = (v: number): number => Math.round(v * 10) / 10;

export interface Pt { x: number; y: number }
export interface Box { w: number; h: number }

/* ============================================================ the quarter arc */

/**
 * Where one seat sits on the quarter arc that opens up-and-left from the disc.
 * `frac` 0 is due LEFT, 1 is due UP. Offsets in px from the disc centre, y
 * negative = upward, ready to drop into a translate().
 */
export function arcXY(frac: number, radius: number): Pt {
  const a = (Math.max(0, Math.min(1, frac)) * Math.PI) / 2;
  return { x: round1(-radius * Math.cos(a)), y: round1(-radius * Math.sin(a)) };
}

/** Evenly spaced arc fractions for n seats — one seat sits at the top. */
export function dialFractions(n: number): number[] {
  if (n <= 1) return [1];
  return Array.from({ length: n }, (_, i) => i / (n - 1));
}

/**
 * How many chips a `arcDeg`-wide arc of this radius can seat with DIAL_CLEAR_PX
 * between neighbours. Seats are the ENDPOINTS of the arc's divisions, so n seats
 * leave n−1 gaps: 90° at R=140 admits floor(90 / 2·asin(54/280)) = 4 gaps, hence
 * 5 seats — the number the original header asserted by hand.
 */
export function arcSeats(radius: number, arcDeg = 90): number {
  if (radius <= 0) return 1;
  const step = 2 * Math.asin(Math.min(1, SEAT_PITCH_PX / (2 * radius)));
  if (!(step > 0)) return 1;
  return 1 + Math.floor(((arcDeg * Math.PI) / 180) / step);
}

/** Seats on the shipped quarter arc: 5. */
export const ARC_SEATS = arcSeats(DIAL_R);

/**
 * The most OPTIONS the arc can show — one fewer than its seats, because Back
 * needs a seat of its own.
 *
 * This is where the operator's "more than 4 options is too dense" comes from,
 * and it is derived rather than agreed with: the arc seats five 48px chips, one
 * of them is Back, so four options. A fifth option would put six chips on a 90°
 * arc, 18° apart, a 43.8px chord between 48px chips — overlapping.
 */
export const ARC_MAX_OPTIONS = ARC_SEATS - 1;

/**
 * Which layout a ring of `n` options gets. `hasBack` because Back occupies a
 * seat exactly like an option does — the bug that made this function necessary
 * was that it did not.
 */
export function needsRing(n: number, hasBack: boolean): boolean {
  return n + (hasBack ? 1 : 0) > ARC_SEATS;
}

/**
 * The arc's seat allocation, Back included.
 *
 * THE BUG THIS FIXES: Back was rendered unconditionally at frac 0 and the
 * options were laid out with `dialFractions(n)`, which also puts option 0 at
 * frac 0. Back is appended after the options, so it sat on top of the first
 * option and swallowed it — visually and for hit-testing. On the filter ring
 * that ate L; it ate the first exposure, the first gain and the first bin too.
 * Nothing caught it because the DOM test read `textContent` and never asked
 * where anything was on screen.
 *
 * So Back gets a seat, not a coordinate. It keeps frac 0 — due left, the
 * lateral sweep a corner-pivoting thumb reaches most easily, and the right
 * place for the control you hit when you opened the wrong thing — and the
 * options take the remaining seats of an (n+1)-seat arc.
 */
export function arcSeatFracs(n: number, hasBack: boolean):
    { back: number | null; options: number[] } {
  if (!hasBack) return { back: null, options: dialFractions(n) };
  if (n <= 0) return { back: 0, options: [] };
  return { back: 0, options: dialFractions(n + 1).slice(1) };
}

/**
 * The radius this stage can give the arc. The dial measures the box it overlays
 * at runtime; `null` (not yet measured, or server-rendered) assumes the full
 * arc, and the arc is closed on first paint anyway so the correction lands
 * before anything is drawn.
 */
export function dialRadius(box: Box | null,
                           inset: { right: number; bottom: number }): number {
  if (box == null) return DIAL_R;
  const half = DIAL_ITEM_PX / 2;
  const room = Math.min(box.h - (inset.bottom + DIAL_DISC_PX / 2) - half,
                        box.w - (inset.right + DIAL_DISC_PX / 2) - half);
  return Math.max(0, Math.min(DIAL_R, Math.floor(room)));
}

/* =============================================================== the full ring */

/** Distance between neighbouring chips on a full ring of n chips at radius r. */
export function ringChord(n: number, r: number): number {
  if (n <= 1) return Infinity;
  return 2 * r * Math.sin(Math.PI / n);
}

/**
 * The smallest radius that seats n chips on a full turn.
 *
 * A full ring divides 360° into n gaps (not n−1 — it closes), so neighbours are
 * 360/n apart and their chord is 2r·sin(180/n). Setting that to SEAT_PITCH_PX
 * gives r = 27/sin(180/n): 13 exposures need 113px, 10 gains need 88px, 7
 * filters need 63px. Floored by the hub, which the chips must also clear.
 *
 * Deliberately a MINIMUM. Every extra pixel of radius is a pixel of thumb
 * travel, so the ring never grows to fill the screen just because it can.
 */
export function ringRadius(n: number): number {
  const hubClear = RING_HUB_PX / 2 + DIAL_ITEM_PX / 2 + DIAL_CLEAR_PX;
  if (n <= 1) return hubClear;
  return Math.ceil(Math.max(hubClear, SEAT_PITCH_PX / (2 * Math.sin(Math.PI / n))));
}

/** Half-width the ring needs on screen: radius, chip halo, and the edge inset. */
export function ringPad(n: number): number {
  return ringRadius(n) + DIAL_ITEM_PX / 2 + RING_EDGE_PX;
}

/**
 * The smallest screen the ring fits on, in its shorter dimension. 13 exposures
 * want 298px, 10 gains 248px — both inside a phone held in landscape, whose
 * short side is ~375px, which is why the fallback below is a floor and not a
 * routine. A ring wants a SQUARE of this size; the quarter arc wanted only a
 * corner of it, which is the whole difference between the two fit checks.
 */
export function ringMinViewport(n: number): number {
  return 2 * ringPad(n);
}

export interface RingPlacement {
  /** Hub centre, in the coordinates of the box passed in. */
  cx: number;
  cy: number;
  r: number;
  /** Angle (radians, screen axes: +x right, +y DOWN, so increasing = clockwise)
   *  at which the selected chip is pinned — the point of the ring nearest the
   *  disc the operator just touched. */
  near: number;
  /** False when the screen cannot hold a legible ring; the caller must fall back
   *  rather than draw a tighter one. */
  fits: boolean;
}

/**
 * Where to draw the ring for `n` options, given the screen and the disc that
 * opened it.
 *
 * The hub is CLAMPED to the anchor, not centred on the screen: it goes to the
 * nearest point to the disc at which the whole ring is still on screen. On a
 * phone that leaves the disc inside the ring, so the near arc is under the thumb
 * that just tapped, and the reach only grows in the direction the arc already
 * asked the thumb to travel.
 */
export function ringPlacement(view: Box, anchor: Pt, n: number): RingPlacement {
  const r = ringRadius(n);
  const pad = ringPad(n);
  const fits = view.w >= 2 * pad && view.h >= 2 * pad;
  const cx = fits ? clamp(anchor.x, pad, view.w - pad) : view.w / 2;
  const cy = fits ? clamp(anchor.y, pad, view.h - pad) : view.h / 2;
  const dx = anchor.x - cx;
  const dy = anchor.y - cy;
  // A hub sitting exactly on the anchor has no "toward the thumb" direction;
  // straight DOWN is the reachable pole on any phone, so that is the default.
  const near = Math.hypot(dx, dy) < 1 ? Math.PI / 2 : Math.atan2(dy, dx);
  return { cx, cy, r, near, fits };
}

/**
 * Chip offsets from the hub, in list order, with `selected` pinned at `near` and
 * the rest running CLOCKWISE from it (screen axes have y down, so increasing
 * angle turns clockwise). One stop longer is always the next chip clockwise from
 * where the thumb already is.
 */
export function ringSlots(n: number, r: number, near: number, selected = 0): Pt[] {
  if (n <= 0) return [];
  const step = (2 * Math.PI) / n;
  const base = near - Math.max(0, selected) * step;
  return Array.from({ length: n }, (_, i) => {
    const a = base + i * step;
    return { x: round1(r * Math.cos(a)), y: round1(r * Math.sin(a)) };
  });
}

/**
 * Roving-focus movement around the ring. Returns null for a key the ring does
 * not own, so the caller knows whether to preventDefault.
 *
 * WRAPS, where stepDial.stepByKey deliberately clamps. The difference is what a
 * keypress costs: the step dial commits on release, so wrapping from 1000 to 1
 * is how you move a focuser a thousand steps by mistake. Here nothing is applied
 * until Enter or a tap, so wrapping is free — and on a circle, stopping dead at
 * a seam the operator cannot see would be the surprising behaviour.
 */
export function ringStep(i: number, n: number, key: string): number | null {
  if (n <= 0) return null;
  if (key === "ArrowRight" || key === "ArrowDown") return (i + 1) % n;
  if (key === "ArrowLeft" || key === "ArrowUp") return (i - 1 + n) % n;
  if (key === "Home") return 0;
  if (key === "End") return n - 1;
  return null;
}

/**
 * The smallest gap between any two chips in a layout, chips and Back alike.
 *
 * This is the invariant the whole file exists to keep, so it is a function and
 * not a comment: whatever the layout, no two seats may be closer than
 * DIAL_ITEM_PX. Both layouts are checked against it by the tests, INCLUDING
 * option-against-Back — the pair that was overlapping in production while the
 * DOM test read labels and reported green.
 */
export function minSeatGap(seats: readonly Pt[]): number {
  let min = Infinity;
  for (let i = 0; i < seats.length; i++) {
    for (let j = i + 1; j < seats.length; j++) {
      const d = Math.hypot(seats[i].x - seats[j].x, seats[i].y - seats[j].y);
      if (d < min) min = d;
    }
  }
  return min;
}

/** Every seat of the arc layout — options AND Back — as offsets from the disc. */
export function arcSeatXY(n: number, hasBack: boolean, radius = DIAL_R): Pt[] {
  const { back, options } = arcSeatFracs(n, hasBack);
  const out = options.map((f) => arcXY(f, radius));
  if (back != null) out.push(arcXY(back, radius));
  return out;
}
