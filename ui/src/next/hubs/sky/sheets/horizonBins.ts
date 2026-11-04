// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// horizonBins.ts - the per-bin horizon a scan publishes and a site saves (#900,
// #904, #1117).
//
// A scan answers for azimuth BINS: one altitude per bin and, since #1117, one
// STATUS per bin saying whether anything measured it. The consumers of a saved
// horizon (the finder, the dome, the ranked list, the engine floor) read a
// polyline and interpolate linearly between its points, so the bins are turned
// into a polyline once, here, and every reader - the saved line, the simulator's
// scorer, the editor - reads that one line through `horizonAltAt`.
//
// The line is the step the bins describe, not a slide between bin centres. A
// bin's value holds across its whole width, so each bin publishes its value at
// BOTH of its edges. Where two neighbours differ, the HIGHER bin keeps the
// shared edge and the lower one ramps up to it over `EDGE_RAMP_DEG` inside its
// own span: the line is never lower than the higher neighbour at the edge, and
// an unmeasured neighbour (published at 90) closes the edge the same way. That
// is the safe direction (#900): a false open is a wasted exposure on a wall, a
// false close is a slightly smaller sky.
import { horizonAltAt, type HorizonPoint } from '../../../lib/horizonModel';

/** What a bin is. `measured` is the only status a frame vouched for; `edited`
 *  and `kept` are a person's and a previous scan's values; the rest are bins no
 *  measurement answers for and publish blocked. */
export type BinStatus = 'measured' | 'low' | 'unknown' | 'tall' | 'edited' | 'kept';
export const BIN_STATUSES: readonly BinStatus[] = ['measured', 'low', 'unknown', 'tall', 'edited', 'kept'];

export interface HorizonBin { alt: number; status: BinStatus }

/** A horizon that blocks: the zenith. What an unmeasured bin publishes. */
export const BLOCKED_ALT_DEG = 90;
/** The most bins a stored horizon carries: the panorama scanner's 0.5 degree grid. */
export const MAX_HORIZON_BINS = 720;
/** How far inside the lower bin the line climbs to a higher neighbour. */
export const EDGE_RAMP_DEG = 0.02;

const hundredth = (v: number): number => Math.round(v * 100) / 100;

/** The altitude a bin publishes: its own, except that a bin nothing measured is
 *  closed whatever altitude it carries. */
export function binAlt(bin: HorizonBin): number {
  return bin.status === 'unknown' ? BLOCKED_ALT_DEG : bin.alt;
}

/** The polyline the bins publish. At most two points per bin plus the ramps, so
 *  the shipped 30 bins stay under the 180-point cap the server holds a drawn
 *  horizon to. An empty `bins` has no line. */
export function pointsFromBins(bins: readonly HorizonBin[]): HorizonPoint[] {
  const n = bins.length;
  if (!n) return [];
  const width = 360 / n;
  const alts = bins.map(binAlt);
  const raw: HorizonPoint[] = [];
  alts.forEach((alt, i) => {
    const before = alts[(i - 1 + n) % n], after = alts[(i + 1) % n];
    const left = i * width + (alt >= before ? 0 : EDGE_RAMP_DEG);
    const right = (i + 1) * width - (alt >= after ? 0 : EDGE_RAMP_DEG);
    raw.push({ az: hundredth(left), alt }, { az: hundredth(right) % 360, alt });
  });
  raw.sort((p, q) => p.az - q.az);
  const distinct = raw.filter((p, i) => i === 0 || p.az !== raw[i - 1].az || p.alt !== raw[i - 1].alt);
  const kept = distinct.filter((p, i) => {
    const prev = distinct[(i - 1 + distinct.length) % distinct.length];
    const next = distinct[(i + 1) % distinct.length];
    return !(p.alt === prev.alt && p.alt === next.alt);
  });
  return kept.length ? kept : [distinct[0]];
}

/** The altitude the bins publish at `az`: the one reading every consumer gives
 *  the saved line, spelled once. */
export function horizonAltAtBins(bins: readonly HorizonBin[], az: number): number {
  return horizonAltAt(pointsFromBins(bins), az);
}

/** What a finished scan hands the editor. A bin the tracer could not answer for
 *  is `unknown` and blocked; every other bin is `measured`. */
export function binsFromTrace(alts: readonly number[], uncertainBins: readonly number[]): HorizonBin[] {
  const uncertain = new Set(uncertainBins);
  return alts.map((alt, i) => (uncertain.has(i)
    ? { alt: BLOCKED_ALT_DEG, status: 'unknown' as const }
    : { alt, status: 'measured' as const }));
}

/** Per-bin status from a saved horizon, or null when `raw` is not one. A saved
 *  row that does not parse is no status at all, never a guess. */
export function parseSavedBins(raw: unknown): HorizonBin[] | null {
  if (!Array.isArray(raw) || !raw.length || raw.length > MAX_HORIZON_BINS) return null;
  const bins: HorizonBin[] = [];
  for (const item of raw) {
    const alt = (item as { alt?: unknown } | null)?.alt, status = (item as { status?: unknown } | null)?.status;
    if (typeof alt !== 'number' || !Number.isFinite(alt)) return null;
    if (!BIN_STATUSES.includes(status as BinStatus)) return null;
    bins.push({ alt, status: status as BinStatus });
  }
  return bins;
}

/** Do the saved bins still describe the saved line? A line edited by hand, or
 *  by another editor, since the bins were written no longer is what they say,
 *  and then the bins are dropped rather than drawn over it. */
export function binsDescribe(bins: readonly HorizonBin[], points: readonly HorizonPoint[]): boolean {
  const line = pointsFromBins(bins);
  return line.length === points.length
    && line.every((p, i) => Math.abs(p.az - points[i].az) < 0.005 && Math.abs(p.alt - points[i].alt) < 0.005);
}
