// coolerCurve.ts - the camera sheet's ring arc and its cooling curve, as pure
// maths (no React, no store, no fetch) so both can be asserted without mounting
// a sheet.
//
// WHY THIS FILE EXISTS AT ALL. The design's cooler card draws a curve, and the
// prototype fills it from an exponential: `12 - 22 * (1 - exp(-t * 3.2))`
// (seams/proto/logic.js:701). That line is a picture of cooling in general, not
// of THIS camera - it descends smoothly whatever the sensor is doing, so a TEC
// that stalled at -2 would still be drawn arriving at the set-point. Plan
// hub-rig.md E25: plot the temperatures this client has actually received, and
// when there are not enough of them yet, say so instead of drawing something.
//
// The ring's numbers are the prototype's, verbatim, because they are the
// design's geometry rather than a claim about the camera:
//   pct = clamp((25 - tempC) / 40, 0, 1);  dasharray = (pct * 264) + " 264"
// 25 C is the top of the drawn range and -15 C the bottom; a camera colder than
// -15 pins the ring full rather than over-drawing it.

/** The r=42 ring's circumference, as the prototype writes it. */
export const RING_CIRCUMFERENCE = 264;
/** Temperature at which the ring reads empty. */
export const RING_TOP_C = 25;
/** How many degrees of cooling fill the whole ring. */
export const RING_SPAN_C = 40;

/** The curve's viewBox, so the sheet and this module cannot disagree on it. */
export const CURVE_W = 160;
export const CURVE_H = 34;
/** Keeps the 1.5 px stroke and the round joins inside the box. */
export const CURVE_PAD = 3;

/** The client-side ring buffer's ceiling - the prototype draws 24 points. */
export const CURVE_MAX_SAMPLES = 24;
/** Below this, two points are a straight line between two readings and a
 *  straight line is not a curve; we say what is happening instead. */
export const CURVE_MIN_SAMPLES = 3;

/** What the card prints in place of a curve it cannot honestly draw. */
export const BUILDING_NOTE = "building the curve";

/** 0..1 of the ring that is filled, or `null` when the sensor reported no
 *  temperature at all. Null is NOT zero: zero is a legitimate arc (a sensor at
 *  or above +25 C), and drawing one for a camera that said nothing is the
 *  fabricated-arc bug this returns null to prevent. */
export function ringFraction(tempC: number | null | undefined): number | null {
  if (tempC == null || !Number.isFinite(tempC)) return null;
  return Math.max(0, Math.min(1, (RING_TOP_C - tempC) / RING_SPAN_C));
}

/** The `stroke-dasharray` for that fraction. A null fraction draws NOTHING. */
export function ringDash(frac: number | null): string {
  const f = frac == null ? 0 : Math.max(0, Math.min(1, frac));
  return `${(f * RING_CIRCUMFERENCE).toFixed(1)} ${RING_CIRCUMFERENCE}`;
}

/** Append one received sensor temperature to the ring buffer.
 *
 *  Returns the SAME array when there is nothing to add, so a `setState(buf =>
 *  pushTemp(buf, t))` on every 2 s status frame cannot spin the tree on a
 *  camera that is reporting nothing. */
export function pushTemp(
  buf: readonly number[],
  tempC: number | null | undefined,
  cap: number = CURVE_MAX_SAMPLES,
): number[] {
  if (tempC == null || !Number.isFinite(tempC)) return buf as number[];
  const next = [...buf, tempC];
  return next.length > cap ? next.slice(next.length - cap) : next;
}

export interface CoolerCurve {
  /** `"x,y x,y ..."` for the polyline. EMPTY while `ready` is false - the
   *  caller must not render a `<polyline>` at all then. */
  points: string;
  /** Where the dashed set-point line sits, or null when there is no set-point.
   *  Present even before the curve is, because the set-point is a fact about
   *  the request, not about the readings. */
  setpointY: number | null;
  /** At least `CURVE_MIN_SAMPLES` readings have arrived. */
  ready: boolean;
  /** `BUILDING_NOTE` while not ready, else null. */
  note: string | null;
  /** The temperature window the curve is drawn over, coldest first. Exposed so
   *  the card can label the axis rather than leave the shape unscaled. */
  lo: number;
  hi: number;
}

/** Map the received temperatures onto the design's 160x34 box.
 *
 *  Hotter is HIGHER on screen, so a cool-down descends left to right the way
 *  the screenshot shows. The window always contains the set-point, so the
 *  dashed line and the trace are on one scale - a curve drawn against its own
 *  min/max would show a camera "arriving" at a line it is nowhere near. */
export function coolerCurve(
  samples: readonly number[],
  setpointC: number | null | undefined,
): CoolerCurve {
  const pts = samples.filter((t) => Number.isFinite(t));
  const set = setpointC != null && Number.isFinite(setpointC) ? setpointC : null;

  let lo: number;
  let hi: number;
  if (pts.length === 0) {
    lo = set == null ? 0 : set - 1;
    hi = set == null ? 1 : set + 1;
  } else {
    lo = Math.min(...pts);
    hi = Math.max(...pts);
    if (set != null) { lo = Math.min(lo, set); hi = Math.max(hi, set); }
  }
  // A flat reading would divide by zero and put every point on one edge; give
  // it a one-degree window centred on itself instead.
  if (hi - lo < 1) { const mid = (hi + lo) / 2; lo = mid - 0.5; hi = mid + 0.5; }

  const top = CURVE_PAD;
  const bottom = CURVE_H - CURVE_PAD;
  const y = (t: number): number => {
    const v = top + ((hi - t) / (hi - lo)) * (bottom - top);
    return Math.max(top, Math.min(bottom, v));
  };

  const ready = pts.length >= CURVE_MIN_SAMPLES;
  const points = ready
    ? pts
      .map((t, i) => `${((i * CURVE_W) / (pts.length - 1)).toFixed(1)},${y(t).toFixed(1)}`)
      .join(" ")
    : "";

  return {
    points,
    // With no readings at all there is no scale to place the set-point on, so
    // it sits mid-box: the line says "this is the target", not "the target is
    // this far from the sensor".
    setpointY: set == null ? null : (pts.length === 0 ? CURVE_H / 2 : y(set)),
    ready,
    note: ready ? null : BUILDING_NOTE,
    lo,
    hi,
  };
}
