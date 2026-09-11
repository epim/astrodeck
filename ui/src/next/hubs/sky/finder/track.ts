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

import { hz, D2R, SIDEREAL_DEG_PER_HOUR, wrapRaHours } from "./equatorial";
import { isObstructed, type HorizonPoint } from "../../../lib/horizonModel";
import { fmtClock } from "../../../lib/format";
import { lstHours } from "../../../../lib/altaz";
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

// ------------------------------------------------ tracks for the hemisphere
//
// Everything above this line walks ONE object - whatever the finder has
// tracked - and projects it into the finder's flat sky box. The dome needs
// neither restriction: it draws several arcs at once, and one of them may be a
// point in the sky with no catalogue object under it at all (the reticle aimed
// at empty sky, which is what a survey for something new looks like).
//
// SO THE SUBJECT IS A PARAMETER AND SO IS THE CONTEXT. `trackSamplesFor` takes
// any RA/Dec and any `TrackContext`, which is what lets the two dome screens
// share one walk while still disagreeing - deliberately - about what each is
// entitled to colour:
//
//   * The Sky hub's card runs the FINDER's context: the horizon mask forced on
//     (the overlay draws the profile on that dome unconditionally, so an arc
//     coloured as if there were no mask would run clear over a tree line) and
//     the finder's own forecast `holdAt`, so an amber stretch means the same
//     thing on the card as it does on the finder six cards above it.
//   * WEATHER > SKY runs `holdAt: () => false`. The cloud on that screen is the
//     dome itself - a measurement - and the amber would be a forecast painted
//     over one with nothing on screen to say which of the two it came from.
//
// What neither may do is re-derive the WALK. Both call this function over the
// same site and the same horizon, so two domes can never disagree about where
// an object goes - only about what each is allowed to say about it.

/** The id the reticle's own patch is published under. A constant because three
 *  files have to agree on it: the model that makes the track, the card that
 *  turns it into a `?ra=&dec=` link, and the test that asserts it is there. */
export const AIM_TRACK_ID = "aim";

/** How many arcs may be on one dome. Six is a measurement, not a taste: the
 *  phone card's canvas is 280 px tall, an arc is a third of the dome wide, and
 *  the seventh one turns the near half into hatching. The cut is by rank, and
 *  the bright subject is always first, so what falls off the end is the least
 *  worth pointing at. */
export const MAX_DOME_TRACKS = 6;

/** Anything a dome arc can be drawn for: a catalogue target, the locked target,
 *  or a bare point in the sky. `name` is null for the last of those - a patch
 *  of empty sky HAS no name, and inventing one ("target", "aim point") would be
 *  a label that says nothing the arc does not. */
export interface TrackSubject {
  id: string;
  name: string | null;
  ra_hours: number;
  dec_deg: number;
  /** The one the reader came for: drawn solid and labelled, where the rest are
   *  dim and unlabelled. */
  bright?: boolean;
}

/** A `TrackContext` plus the two things a walk needs that the finder's own
 *  context carries implicitly, because the finder walks from its own clock and
 *  its own site: the longitude the hour angle is measured from, and when NOW
 *  is. Passed rather than read so the whole thing stays pure and a test can
 *  move the clock without moving the machine's. */
export interface DomeTrackContext extends TrackContext {
  /** Degrees east. */
  lonDeg: number;
  nowMs: number;
}

export interface DomeTrack {
  id: string;
  /** Drawn beside the arc on the bright ones, and listed under the dome. An
   *  empty string draws no label, which is what an unnamed arc gets. */
  label: string;
  bright: boolean;
  /**
   * The bare position this arc is for, when no catalogue object sits at it -
   * and null for every named target, whose id is what a link carries.
   *
   * IT IS THE COORDINATES AND NOT A FLAG because the one thing a caller wants
   * to do with an unnamed arc is turn it back into a link (`?ra=&dec=`), and a
   * boolean would send them looking for the numbers somewhere else.
   */
  point: { ra_hours: number; dec_deg: number } | null;
  samples: TrackSample[];
}

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

/**
 * What a bare point in the sky is called, since it has nothing else.
 *
 * COMPACT ON PURPOSE. `raHmsStr` + `decDmsStr` is 26 characters, which beside
 * an arc on a 280 px dome is a band of text across the sky rather than a label.
 * Minutes are also as fine as this picture can mean anything at: one cloud cell
 * on the dome under it is 6 x 10 degrees.
 */
export function pointLabel(raHours: number, decDeg: number): string {
  const ra = wrapRaHours(raHours);
  let rh = Math.floor(ra);
  let rm = Math.round((ra - rh) * 60);
  if (rm === 60) { rm = 0; rh = (rh + 1) % 24; }
  const sign = decDeg < 0 ? "-" : "+";
  const ad = Math.abs(decDeg);
  let dd = Math.floor(ad);
  let dm = Math.round((ad - dd) * 60);
  if (dm === 60) { dm = 0; dd += 1; }
  return `${pad2(rh)}h${pad2(rm)}m ${sign}${pad2(dd)}°${pad2(dm)}′`;
}

/** The label an arc carries: the object's name, or the point's coordinates. */
export function domeTrackLabel(s: TrackSubject): string {
  return s.name ?? pointLabel(s.ra_hours, s.dec_deg);
}

/**
 * Walk ANY subject from now to dawn, in the caller's own context.
 *
 * The hour angle is `lst - ra`, and `lst` is a function of longitude and the
 * clock - which is why both are in the context rather than read here. An empty
 * list is returned for a subject with no usable coordinates and for a night
 * that is already over; neither is an error, and both must draw nothing rather
 * than an arc from a position nobody computed.
 */
export function trackSamplesFor(
  subject: { ra_hours: number; dec_deg: number },
  ctx: DomeTrackContext,
): TrackSample[] {
  if (!Number.isFinite(subject.ra_hours) || !Number.isFinite(subject.dec_deg)) return [];
  if (!Number.isFinite(ctx.lonDeg) || !Number.isFinite(ctx.latDeg)) return [];
  const lst = lstHours(ctx.lonDeg, ctx.nowMs / 1000);
  return walkTrack(subject.dec_deg * D2R, (lst - subject.ra_hours) * 15 * D2R, ctx);
}

/**
 * The arcs a dome draws, in the order they were handed over (bright first).
 *
 * A SUBJECT WITH NO WALK IS DROPPED HERE rather than passed on empty. An arc
 * with no samples draws nothing, and a legend counting it would name a mark the
 * reader then goes looking for - the same defect `DrawnMarks` exists to prevent
 * one layer further down.
 */
export function buildDomeTracks(
  subjects: TrackSubject[],
  ctx: DomeTrackContext,
  limit: number = MAX_DOME_TRACKS,
): DomeTrack[] {
  const out: DomeTrack[] = [];
  const seen = new Set<string>();
  for (const s of subjects) {
    if (out.length >= limit) break;
    if (seen.has(s.id)) continue;
    seen.add(s.id);
    const samples = trackSamplesFor(s, ctx);
    if (samples.length === 0) continue;
    out.push({
      id: s.id,
      label: domeTrackLabel(s),
      bright: s.bright === true,
      point: s.name === null ? { ra_hours: s.ra_hours, dec_deg: s.dec_deg } : null,
      samples,
    });
  }
  return out;
}
