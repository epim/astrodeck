// track.ts - a target's path from now to dawn, and the four colours that say why
// each stretch of it is or is not worth shooting (hub-sky plan B.4).
//
// The track is the finder's one honest answer to "should I point at this?". A
// target 58 degrees up right now may be behind a tree in forty minutes, under a
// forecast cloud band at midnight, and under the 25-degree floor by two. Drawing
// the arc in one colour would hide all three.
//
// The classifier's ORDER is load-bearing and is the prototype's
// (proto/logic.js:485): below, then floor, then horizon mask, then cloud hold,
// then ok. Floor before mask means a target under 25 degrees reads "too low"
// rather than "behind your trees" even where both are true, which is the more
// actionable of the two - you cannot move the trees, but you can wait for it to
// climb.
//
// `below` BREAKS the polyline. It is not a colour: there is no segment drawn
// across a stretch where the target is under the horizon, because a line there
// would say the target travelled through the ground.

import { hz, D2R, SIDEREAL_DEG_PER_HOUR } from "./equatorial";
import { isObstructed, type HorizonPoint } from "../../../lib/horizonModel";
import { fmtClock } from "../../../lib/format";
import type { Projector } from "./projection";

/** The seeing floor, degrees (proto/logic.js:114). Below it the air path is long
 *  enough that the frames are worse than the time spent on them. */
export const FLOOR_DEG = 25;

/** Sampling interval, hours. Quarter-hour steps put an hour dot on every fourth
 *  sample, which is what the design draws. */
export const STEP_HOURS = 0.25;

export type TrackState = "below" | "floor" | "mask" | "hold" | "ok";

/** Design tokens, not hex literals - night mode swaps the token and a literal
 *  would stay cyan on a dark-adapted screen (ARCHITECTURE section 6). */
export const TRACK_COLORS: Record<Exclude<TrackState, "below">, string> = {
  ok: "var(--accent)",
  hold: "var(--warn)",
  mask: "var(--bad)",
  floor: "var(--text-faint)",
};

export interface TrackContext {
  latDeg: number;
  /** Hours from now to the end of astronomical dark. */
  hoursToDawn: number;
  /** The site's drawn polyline. Empty means none drawn - see `horizonMinDeg`. */
  horizon: HorizonPoint[];
  /** The flat fallback the engine gates on when no polyline exists. */
  horizonMinDeg: number;
  /** The horizon layer is switched off, so do not colour by it. */
  maskOn: boolean;
  /** Is the forecast in sustained breach `t` hours from now? Returns false
   *  everywhere when there is no forecast - an invented hold is worse than none. */
  holdAt: (hoursFromNow: number) => boolean;
}

/**
 * Is this point behind the horizon?
 *
 * The empty-polyline case is handled by the CALLER, not by passing a synthetic
 * flat line: `horizonAltAt([])` returns 0, so a site with a 25-degree
 * `horizon_min_deg` and no drawn line would report everything above 0 as clear.
 */
export function isObstructedAt(altDeg: number, azDeg: number, ctx: TrackContext): boolean {
  return ctx.horizon.length > 0
    ? isObstructed(ctx.horizon, altDeg, azDeg)
    : altDeg < ctx.horizonMinDeg;
}

export function classify(
  altDeg: number,
  azDeg: number,
  hoursFromNow: number,
  ctx: TrackContext,
): TrackState {
  if (altDeg < -2) return "below";
  if (altDeg < FLOOR_DEG) return "floor";
  if (ctx.maskOn && isObstructedAt(altDeg, azDeg, ctx)) return "mask";
  if (ctx.holdAt(hoursFromNow)) return "hold";
  return "ok";
}

export interface TrackSample {
  /** Hours from now. */
  t: number;
  alt: number;
  az: number;
  st: TrackState;
}

/**
 * Step the hour angle to dawn and classify every sample. `haRad0` is the hour
 * angle NOW; the sky advances 15.041 degrees per hour of clock time, not 15 -
 * over a seven-hour night the difference is ten arcminutes, which is more than
 * the width of the arc being drawn.
 */
export function walkTrack(decRad: number, haRad0: number, ctx: TrackContext): TrackSample[] {
  const out: TrackSample[] = [];
  if (!(ctx.hoursToDawn > 0)) return out;
  for (let t = 0; t <= ctx.hoursToDawn + 1e-9; t += STEP_HOURS) {
    const h = hz(decRad, haRad0 + t * SIDEREAL_DEG_PER_HOUR * D2R, ctx.latDeg);
    out.push({ t, alt: h.alt, az: h.az, st: classify(h.alt, h.az, t, ctx) });
  }
  return out;
}

/**
 * Minutes the target spends usable between now and dawn - the `reachScore`
 * input. `floor` and `below` do not count; `mask` and `hold` do, because a tree
 * that clears at midnight and a cloud band that passes are both time the engine
 * can still use (hub-sky plan B.6).
 */
export function minutesAboveFloor(samples: TrackSample[]): number {
  let n = 0;
  for (const s of samples) if (s.st !== "below" && s.st !== "floor") n++;
  return n * STEP_HOURS * 60;
}

export interface TrackSegment {
  /** "x,y x,y ..." for an SVG polyline. */
  points: string;
  color: string;
  /** "3 4" under the floor, "none" otherwise. */
  dash: string;
}

export interface TrackLabel {
  x: number;
  y: number;
  label: string;
}

export interface TrackRender {
  segments: TrackSegment[];
  dots: { x: number; y: number }[];
  labels: TrackLabel[];
}

const EMPTY_RENDER: TrackRender = { segments: [], dots: [], labels: [] };

/** HH:MM with no day marker. `fmtClock` appends "(+1d)" when the stamp is on the
 *  next calendar day, which is true of most of a night and is noise inside a
 *  16 px label on the arc itself - passing the same instant as "now" suppresses
 *  it without duplicating the formatter. */
function hhmm(ms: number): string {
  return fmtClock(ms, ms);
}

/**
 * Project the samples into segments, hour dots and the three labels. Segments
 * carry the PREVIOUS sample as their first point where there is one, so adjacent
 * runs of different colour join rather than leaving a gap (proto/logic.js:487).
 */
export function buildTrack(
  samples: TrackSample[],
  p: Projector,
  nowMs: number,
  hoursToDawn: number,
): TrackRender {
  if (samples.length === 0) return EMPTY_RENDER;
  const pts = samples.map((s) => ({ ...s, ...p.proj(s.az, s.alt) }));

  type Pt = (typeof pts)[number];
  const runs: { st: Exclude<TrackState, "below">; list: Pt[] }[] = [];
  let run: { st: Exclude<TrackState, "below">; list: Pt[] } | null = null;
  pts.forEach((q, i) => {
    if (q.st === "below") { run = null; return; }
    const st = q.st as Exclude<TrackState, "below">;
    if (run === null || run.st !== st) {
      const prev = pts[i - 1];
      run = { st, list: prev && prev.st !== "below" ? [prev, q] : [q] };
      runs.push(run);
    } else {
      run.list.push(q);
    }
  });
  const segments: TrackSegment[] = runs.map((r) => ({
    points: r.list.map((q) => `${q.x.toFixed(1)},${q.y.toFixed(1)}`).join(" "),
    color: TRACK_COLORS[r.st],
    dash: r.st === "floor" ? "3 4" : "none",
  }));

  const dots = pts
    .filter((q, i) => q.st !== "below" && i % 4 === 0)
    .map((q) => ({ x: q.x, y: q.y }));

  const up = pts.filter((q) => q.st !== "below");
  const labels: TrackLabel[] = [{ x: pts[0].x, y: pts[0].y, label: "now" }];
  if (up.length > 0) {
    const best = up.reduce((m, q) => (q.alt > m.alt ? q : m), up[0]);
    // A transit at the very start or the very end of the window is not a transit
    // worth labelling - it is the target already past its best, or still rising
    // at dawn, and the label would sit on top of "now" or "dawn".
    if (best.t > 0.3 && best.t < hoursToDawn - 0.3) {
      labels.push({ x: best.x, y: best.y, label: `transit ${hhmm(nowMs + best.t * 3600_000)}` });
    }
    const last = up[up.length - 1];
    labels.push({
      x: last.x,
      y: last.y,
      label: last.t >= hoursToDawn - 0.3 ? "dawn" : `sets ${hhmm(nowMs + last.t * 3600_000)}`,
    });
  }

  return {
    segments: segments.filter((s) => s.points !== ""),
    dots,
    labels: labels.filter((l) => l.x > -40 && l.x < p.W + 40 && l.y > -20 && l.y < p.H + 20),
  };
}
