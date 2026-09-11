// projection.ts - screen geometry for the finder: alt/az to pixels, the compass
// strip's ticks and the altitude grid (hub-sky plan B.3).
//
// The projection is the prototype's, unchanged (proj/logic.js:334): a plate-
// carree tangent-ish mapping that is exact along the centre row and cheap
// everywhere. It is a FINDER, not a chart - the job is "which way do I turn",
// and at 6.1 px per degree a projection error smaller than a marker pill is not
// an error anybody can see.
//
// WIDTH SCALING. The design's box is 370 x 372 on a phone; at tablet and desktop
// it fills its column up to 720 px square. `ppdFor` scales pixels-per-degree with
// the box so the SAME 60 degrees of sky fills the wider square - a bigger box
// shows the same field, larger, rather than more sky at the same scale. Without
// that the tick spacing, the 46 px lock radius and the 52 px arrow grid would all
// mean different amounts of sky at different widths.

/** Pixels per degree at the design width (proto/logic.js:11). */
export const PPD = 6.1;
/** Pixels per degree in FRAME mode - the zoomed framing view (proto/logic.js:34). */
export const PPF = 44;

export const DESIGN_W = 370;
export const DESIGN_H = 372;

/** Half-width of the compass sweep, degrees. 36 covers the 30.3 degrees the box
 *  actually shows plus a margin, so a tick never pops in at the edge. */
const TICK_SPAN_DEG = 36;
/** Half-height of the altitude grid, degrees, on the same argument. */
const ALT_SPAN_DEG = 32;

/** Pixels per degree for a box `boxPx` wide. */
export function ppdFor(boxPx: number): number {
  return (PPD * boxPx) / DESIGN_W;
}

/** The box's height for a given width, keeping the design's 370:372 ratio. */
export function boxHeightFor(boxPx: number): number {
  return Math.round((boxPx * DESIGN_H) / DESIGN_W);
}

export interface Projector {
  /** Box width, px. */
  W: number;
  /** Box height, px. */
  H: number;
  /** Pixels per degree in force. */
  pp: number;
  /** Centre azimuth, degrees. */
  cAz: number;
  /** Centre altitude, degrees. */
  cAlt: number;
  proj(azDeg: number, altDeg: number): { x: number; y: number };
  unproj(x: number, y: number): { az: number; alt: number };
}

/**
 * A projector for one frame. `frameOn` swaps the scale to FRAME mode's fixed
 * 44 px/deg (which is deliberately NOT width-scaled: the framing view draws real
 * panels at true scale, and a panel rectangle that grew with the viewport would
 * describe a camera nobody owns).
 */
export function makeProjector(
  boxPx: number,
  cAz: number,
  cAlt: number,
  frameOn = false,
): Projector {
  const W = boxPx;
  const H = boxHeightFor(boxPx);
  const pp = frameOn ? PPF : ppdFor(boxPx);
  return {
    W,
    H,
    pp,
    cAz,
    cAlt,
    proj(azDeg: number, altDeg: number) {
      // Fold the azimuth difference into [-180,180) so a target at az 359 with
      // the view at az 1 draws two degrees left, not 358 degrees right.
      const d = ((azDeg - cAz + 540) % 360) - 180;
      return { x: W / 2 + d * pp, y: H / 2 - (altDeg - cAlt) * pp };
    },
    unproj(x: number, y: number) {
      const az = ((((cAz + (x - W / 2) / pp) % 360) + 360) % 360);
      return { az, alt: cAlt - (y - H / 2) / pp };
    },
  };
}

export interface CompassTick {
  x: number;
  /** Tick height, px: 9 at a multiple of 30 degrees, 4 otherwise. */
  h: number;
  /** "N"/"E"/"S"/"W" at the cardinals, "060" at the 30s, "" elsewhere. */
  label: string;
}

/** The compass strip along the top (proto/logic.js:352). Empty in FRAME mode -
 *  a zoomed framing view has no useful compass. */
export function compassTicks(p: Projector, frameOn = false): CompassTick[] {
  if (frameOn) return [];
  const out: CompassTick[] = [];
  const start = Math.round((p.cAz - TICK_SPAN_DEG) / 10) * 10;
  for (let d = start; d <= p.cAz + TICK_SPAN_DEG; d += 10) {
    const deg = ((d % 360) + 360) % 360;
    const x = p.proj(deg, 0).x;
    if (x < -10 || x > p.W + 10) continue;
    out.push({
      x,
      h: deg % 30 === 0 ? 9 : 4,
      label:
        deg % 90 === 0
          ? "NESW"[deg / 90]
          : deg % 30 === 0
            ? String(deg).padStart(3, "0")
            : "",
    });
  }
  return out;
}

export interface AltLine {
  y: number;
  /** "40°" - drawn at the right edge of the line. */
  label: string;
}

/** The dashed altitude grid, every 10 degrees (proto/logic.js:353). Nothing below
 *  0 or above 90 is drawn: there is no sky there. */
export function altLines(p: Projector, frameOn = false): AltLine[] {
  if (frameOn) return [];
  const out: AltLine[] = [];
  const start = Math.ceil((p.cAlt - ALT_SPAN_DEG) / 10) * 10;
  for (let a = start; a <= p.cAlt + ALT_SPAN_DEG; a += 10) {
    if (a < 0 || a > 90) continue;
    out.push({ y: p.proj(p.cAz, a).y, label: `${a}°` });
  }
  return out;
}

/** "064°" - the azimuth readout, zero-padded so the chip does not reflow as the
 *  user pans past 100 (proto/logic.js:543). */
export function azStrOf(azDeg: number): string {
  return `${String(Math.round(azDeg) % 360).padStart(3, "0")}°`;
}

/** "58°" - the altitude readout. */
export function altStrOf(altDeg: number): string {
  return `${Math.round(altDeg)}°`;
}
