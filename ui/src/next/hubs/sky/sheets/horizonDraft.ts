// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// The review draft and the edits made to it (SPEC-v2 2.9, 5.3-5.5): the 720-bin `HorizonDraft` that a panorama scan
// hands to Review, and the pure functions the editor applies to it. What Save writes is always
// `draftToPoints(draft)`, the conservative polyline of `draft.alt` (5.4), so every function here keeps `alt` as the
// number Save must honour.
//
// Rules shared by the functions below:
//   * A function never writes its argument. It returns a copy, or the argument itself when nothing changed (as
//     `mergeKept` does), so a caller can bail out of a state update on `next === prev`.
//   * An edited bin takes the maximum of the user's line at the bin's two edges. That is the rule
//     `conservativePolyline` then keeps (G1: the published line is never below a bin), so a line drawn by hand and
//     published through the draft is at or above the line the user drew, wherever its vertices sit on bin edges.
//   * `reason` is the tracer's account of why a bin could not be published, and an edit does not change that
//     account: it changes who decides the bin (`state` becomes Edited). So `reason` is kept through every edit.
//   * `suggested` and `top` are facts about the photo and the trace, not decisions, and are kept too.
import { horizonAltAt } from '../../../lib/horizonModel';
import { conservativePolyline } from './pano/horizon/simplify';
import { BinState, ColState, PROFILE_BINS } from './pano/types';
import type { HorizonDraft, HorizonPoint } from './pano/types';

const BINS_PER_DEG = PROFILE_BINS / 360;
/** The most vertices Save may publish (server/astrodeck/locations.py MAX_HORIZON_POINTS). */
const MAX_POINTS = 180;
/** A line that moved by more than this many degrees at a bin edge has been edited there (5.4, T24). */
const MOVED_DEG = 1e-6;
/** A shift this close to a whole number of bins is a whole number of bins (0.1 degree steps add up to 0.2000...04). */
const SNAP_BINS = 1e-9;

function cloneDraft(d: HorizonDraft): HorizonDraft {
  return { alt: d.alt.slice(), suggested: d.suggested.slice(), state: d.state.slice(), reason: d.reason.slice(), top: d.top.slice(), lowLight: d.lowLight };
}

/** The line at the 721 bin edges (edge 720 is edge 0 again), read with `horizonAltAt`. An empty line reads 0 there. */
function edgeAlts(points: readonly HorizonPoint[]): Float64Array {
  const line = [...points];
  const edges = new Float64Array(PROFILE_BINS + 1);
  for (let e = 0; e < PROFILE_BINS; e++) edges[e] = horizonAltAt(line, e / BINS_PER_DEG);
  edges[PROFILE_BINS] = edges[0];
  return edges;
}

/**
 * A draft for a line that came from somewhere other than a scan (the saved line, or the line as the user left it):
 * every bin Edited, with the value of the line at the bin's two edges, whichever is higher, so the draft is never
 * below the line anywhere its vertices sit on bin edges. There is no trace behind it: `suggested` and `top` are NaN,
 * `reason` is Measured (no column was unseen, dark or unreadable) and `lowLight` is false. An empty line reads as
 * altitude 0 everywhere, as `horizonAltAt` reads it; Clear horizon does not go through the draft (2.9).
 */
export function draftFromPoints(points: readonly HorizonPoint[]): HorizonDraft {
  const e = edgeAlts(points);
  const alt = new Float32Array(PROFILE_BINS);
  for (let i = 0; i < PROFILE_BINS; i++) alt[i] = Math.max(e[i], e[i + 1]);
  return {
    alt,
    suggested: new Float32Array(PROFILE_BINS).fill(NaN),
    state: new Uint8Array(PROFILE_BINS).fill(BinState.Edited),
    reason: new Uint8Array(PROFILE_BINS).fill(ColState.Measured),
    top: new Float32Array(PROFILE_BINS).fill(NaN),
    lowLight: false,
  };
}

/** The line Save publishes for a draft, and the tolerance it needed (5.4). Never empty, at most 180 points. */
export function draftToPoints(d: HorizonDraft): { points: HorizonPoint[]; tau: number } {
  const { points, tau } = conservativePolyline(d.alt, MAX_POINTS);
  return { points, tau };
}

/**
 * Is azimuth `c` inside the arc that runs from `fromDeg` up to (not including) `toDeg`, going upward and wrapping at
 * 360? An arc of 360 degrees or more is the whole circle; an arc of zero width holds nothing.
 */
function inArc(c: number, fromDeg: number, toDeg: number): boolean {
  const span = toDeg - fromDeg;
  if (span >= 360) return true;
  const width = ((span % 360) + 360) % 360;
  return ((((c - fromDeg) % 360) + 360) % 360) < width;
}

/**
 * "Use the traced line for the uncertain parts" (2.9): every Low bin whose midpoint lies in the arc
 * `fromDeg`..`toDeg` (wrapping when `toDeg` is below `fromDeg`; default the whole circle) takes its traced
 * altitude `suggested` and becomes Edited, so Save publishes the trace there. Bins in any other state are left
 * alone: an Unknown, Tall or Measured bin has no suggestion to accept. The accepted value is the tracer's own and
 * is not checked again: the user has chosen to trust it. Returns `d` itself when no bin changes.
 */
export function acceptSuggested(d: HorizonDraft, fromDeg = 0, toDeg = 360): HorizonDraft {
  if (!Number.isFinite(fromDeg) || !Number.isFinite(toDeg)) {
    throw new RangeError(`acceptSuggested: ${String(fromDeg)}..${String(toDeg)} is not a range of degrees`);
  }
  let out: HorizonDraft | null = null;
  for (let i = 0; i < PROFILE_BINS; i++) {
    if (d.state[i] !== BinState.Low) continue;
    const s = d.suggested[i];
    if (!Number.isFinite(s) || !inArc((i + 0.5) / BINS_PER_DEG, fromDeg, toDeg)) continue;
    out ??= cloneDraft(d);
    out.alt[i] = s;
    out.state[i] = BinState.Edited;
  }
  return out ?? d;
}

/**
 * The draft after the user changed the line from `before` to `after`: every bin where `horizonAltAt` moved by more
 * than 1e-6 degrees at either bin edge becomes Edited, with the maximum of the new line at its two edges. Every other
 * bin is bit for bit what it was, so the Measured, Low and Unknown bins the edit did not reach keep their styles and
 * their notes. A comparison that cannot be made (a NaN altitude in either line) counts as moved, which blocks the bin
 * rather than leaving it. Returns `d` itself when no bin moved.
 */
export function editDraft(d: HorizonDraft, before: readonly HorizonPoint[], after: readonly HorizonPoint[]): HorizonDraft {
  const eb = edgeAlts(before), ea = edgeAlts(after);
  let out: HorizonDraft | null = null;
  for (let i = 0; i < PROFILE_BINS; i++) {
    const moved = !(Math.abs(ea[i] - eb[i]) <= MOVED_DEG) || !(Math.abs(ea[i + 1] - eb[i + 1]) <= MOVED_DEG);
    if (!moved) continue;
    out ??= cloneDraft(d);
    out.alt[i] = Math.max(ea[i], ea[i + 1]);
    out.state[i] = BinState.Edited;
  }
  return out ?? d;
}

/**
 * Blocking rank of a bin, lower is worse, used only to break a tie between two bins that publish the same altitude.
 * The reason orders the Unknown bins as `WORST_ORDER` does (unseen, dark, contrast). A state byte nobody defined
 * ranks as the worst, as profile.ts ranks an unknown ColState.
 */
const STATE_RANK: number[] = [];
STATE_RANK[BinState.Unknown] = 0;
STATE_RANK[BinState.Tall] = 1;
STATE_RANK[BinState.Overhead] = 2;
STATE_RANK[BinState.Low] = 3;
STATE_RANK[BinState.Kept] = 4;
STATE_RANK[BinState.Edited] = 5;
STATE_RANK[BinState.Measured] = 6;

function blockingRank(d: HorizonDraft, i: number): number {
  return (STATE_RANK[d.state[i]] ?? -1) * 256 + d.reason[i];
}

/**
 * The draft rotated by `deg` degrees (the north nudge, 4.12): what was at azimuth a is at a + deg, so a positive
 * shift moves the line toward higher azimuths and wraps at 360. A shift of a whole number of bins (a multiple of
 * 0.5 degree, which is every button of 2.9) moves each bin to its new place unchanged. Any other shift lands a target
 * bin across two source bins, and then it is conservative: the target takes the HIGHEST altitude of the source bins it
 * overlaps, together with that bin's state and reason (the more blocking one on a tie), the highest `suggested` and
 * the lowest `top` of the overlapped bins (a lower bound for a Tall obstruction, as 5.3 defines it). So a shift never
 * lowers a bin and never turns a blocked bin into an open one. `lowLight` is kept. A whole turn returns `d` itself.
 */
export function shiftDraft(d: HorizonDraft, deg: number): HorizonDraft {
  if (!Number.isFinite(deg)) throw new RangeError(`shiftDraft: ${String(deg)} is not a number of degrees`);
  const n = PROFILE_BINS;
  let k = deg * BINS_PER_DEG;                  // bins; the source range of target bin j is [j - k, j + 1 - k)
  const whole = Math.round(k);
  if (Math.abs(k - whole) < SNAP_BINS) k = whole;
  k = ((k % n) + n) % n;
  if (k === 0) return d;
  const out: HorizonDraft = {
    alt: new Float32Array(n), suggested: new Float32Array(n), state: new Uint8Array(n), reason: new Uint8Array(n),
    top: new Float32Array(n), lowLight: d.lowLight,
  };
  for (let j = 0; j < n; j++) {
    const start = j - k;
    const first = Math.floor(start), last = Math.ceil(start + 1) - 1;
    let dom = -1, domKey = -Infinity, sugMax = -Infinity, topMin = Infinity;
    for (let m = first; m <= last; m++) {
      const s = ((m % n) + n) % n;
      const key = Number.isFinite(d.alt[s]) ? d.alt[s] : Infinity;      // a bin with no altitude is blocked
      if (dom < 0 || key > domKey || (key === domKey && blockingRank(d, s) < blockingRank(d, dom))) { dom = s; domKey = key; }
      if (d.suggested[s] > sugMax) sugMax = d.suggested[s];            // NaN never compares greater
      if (d.top[s] < topMin) topMin = d.top[s];
    }
    out.alt[j] = d.alt[dom];
    out.state[j] = d.state[dom];
    out.reason[j] = d.reason[dom];
    out.suggested[j] = sugMax === -Infinity ? NaN : sugMax;
    out.top[j] = topMin === Infinity ? NaN : topMin;
  }
  return out;
}

/**
 * A run of bins, as the review notes name it. `fromDeg` is the start of the first bin, in [0, 360); `toDeg` is the end
 * of the last bin, in (0, 360], so the run covers [fromDeg, toDeg) and `bins` is its length in half degrees. A run
 * that crosses north has `toDeg < fromDeg`; the whole circle is 0..360 with 720 bins.
 */
export interface DraftSpan { fromDeg: number; toDeg: number; bins: number }

interface Run { first: number; count: number }

/** The maximal runs of bins for which `test(state, reason)` holds, joining the last bin to the first, ordered by start. */
function runsOf(d: HorizonDraft, test: (state: number, reason: number) => boolean): Run[] {
  const n = PROFILE_BINS;
  const hit = new Uint8Array(n);
  let total = 0;
  for (let i = 0; i < n; i++) if (test(d.state[i], d.reason[i])) { hit[i] = 1; total++; }
  if (total === 0) return [];
  if (total === n) return [{ first: 0, count: n }];
  let p = 0;
  while (hit[p]) p++;                          // begin after a bin that is not in a run, so no run is cut at the array end
  const runs: Run[] = [];
  let cur: Run | null = null;
  for (let step = 1; step <= n; step++) {      // the last step is p itself, which closes a run still open
    const b = (p + step) % n;
    if (hit[b]) {
      cur ??= { first: b, count: 0 };
      cur.count++;
    } else if (cur) {
      runs.push(cur);
      cur = null;
    }
  }
  return runs.sort((a, b) => a.first - b.first);
}

function spanOf(r: Run): DraftSpan {
  const last = (r.first + r.count - 1) % PROFILE_BINS;
  return { fromDeg: r.first / BINS_PER_DEG, toDeg: (last + 1) / BINS_PER_DEG, bins: r.count };
}

/** The runs of bins for which `test(state, reason)` holds, wrapping across north, in order of their start. */
export function draftSpans(d: HorizonDraft, test: (state: number, reason: number) => boolean): DraftSpan[] {
  return runsOf(d, test).map(spanOf);
}

/** The lowest finite photo top among a run's bins: the run is at least this tall everywhere. 0 when none is finite. */
function lowestTop(d: HorizonDraft, r: Run): number {
  let lowest = Infinity;
  for (let k = 0; k < r.count; k++) {
    const t = d.top[(r.first + k) % PROFILE_BINS];
    if (t < lowest) lowest = t;                // NaN never compares less
  }
  return lowest === Infinity ? 0 : lowest;
}

/**
 * The review notes of 2.9, as spans of bins:
 *   low     state Low: traced but not certain;
 *   unseen  state Unknown, reason UnknownUnseen: not photographed (a bin with one unseen and one measured column
 *           stays Unknown at 90 and is here, ruling S22; a bin that took the earlier line is Kept, not here);
 *   dark    state Unknown, reason UnknownDark or UnknownContrast: photographed, but the boundary could not be read.
 *           The summary has no field for low contrast, and 5.6 asks that every blocked bin say why, so it is told
 *           with the dark ones;
 *   tall    state Tall, each with `atLeastDeg`, the lowest photo top among its bins (0 if none has one);
 *   kept    state Kept: the earlier line stands there (5.5).
 * Measured and Edited bins are in no list.
 */
export function draftSummary(d: HorizonDraft): { low: DraftSpan[]; unseen: DraftSpan[]; dark: DraftSpan[];
  tall: (DraftSpan & { atLeastDeg: number })[]; kept: DraftSpan[] } {
  return {
    low: draftSpans(d, (s) => s === BinState.Low),
    unseen: draftSpans(d, (s, r) => s === BinState.Unknown && r === ColState.UnknownUnseen),
    dark: draftSpans(d, (s, r) => s === BinState.Unknown && (r === ColState.UnknownDark || r === ColState.UnknownContrast)),
    tall: runsOf(d, (s) => s === BinState.Tall).map((r) => ({ ...spanOf(r), atLeastDeg: lowestTop(d, r) })),
    kept: draftSpans(d, (s) => s === BinState.Kept),
  };
}
