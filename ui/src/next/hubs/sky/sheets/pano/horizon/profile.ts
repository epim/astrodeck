// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// The 720-bin profile (SPEC-v2 5.3) and the partial-scan merge (5.5, owner ruling O1).
//
// The tracer gives one altitude and one state per raster column (1080 columns, 3 per degree). The profile that
// Save works from has 720 bins of 0.5 degree, so a bin covers 1.5 columns: every bin intersects two columns, and
// every column feeds one or two bins. A bin takes the MAXIMUM altitude of the columns it intersects and the WORST
// state, so a pole narrower than a bin still fills its bin, and a bin is never more certain than its worst column.
import { horizonAltAt } from '../../../../../lib/horizonModel';
import { BinState, ColState, PROFILE_BINS } from '../types';
import type { ColumnHorizon, HorizonDraft, HorizonPoint } from '../types';

/** ColState, worst first. A bin's `reason` is the first of these found among its columns. */
export const WORST_ORDER: readonly number[] = [
  ColState.UnknownUnseen, ColState.UnknownDark, ColState.UnknownContrast, ColState.Tall, ColState.Low, ColState.Measured,
];

/** Rank of every byte value in `WORST_ORDER`; a value that is not a ColState ranks as the worst, never as Measured. */
const RANK = new Uint8Array(256);
for (let s = 0; s < 256; s++) {
  const at = WORST_ORDER.indexOf(s);
  RANK[s] = at < 0 ? 0 : at;
}

const BINS_PER_DEG = PROFILE_BINS / 360;
/** What a bin publishes when it is not Measured: blocked to the zenith. */
const BLOCKED_ALT = 90;

function binStateOf(worst: number): BinState {
  switch (worst) {
    case ColState.Measured: return BinState.Measured;
    case ColState.Low: return BinState.Low;
    case ColState.Tall: return BinState.Tall;
    default: return BinState.Unknown;      // unseen, dark and contrast; `reason` tells them apart
  }
}

/** The bin that contains `azDeg`: floor(wrap(az) * 2), 0..719. */
export function binOfAz(azDeg: number): number {
  const wrapped = ((azDeg % 360) + 360) % 360;
  return Math.min(PROFILE_BINS - 1, Math.floor(wrapped * BINS_PER_DEG));
}

/**
 * Column horizon to draft (5.3). Bin i covers [i / 2, (i + 1) / 2) degrees and so the columns from
 * floor(1.5 i) to ceil(1.5 (i + 1)) - 1. Per bin:
 *   alt        the maximum A for a Measured bin, 90 for every other state;
 *   suggested  the maximum A of the bin's columns that have one, NaN when none has;
 *   state/reason  the worst column by `WORST_ORDER`, mapped to a BinState; `reason` keeps the ColState;
 *   top        the lowest coverage top of the columns that have one, NaN when none does (nothing photographed).
 * A Measured column with no altitude cannot be measured, so it counts as unseen.
 */
export function binProfile(c: ColumnHorizon): HorizonDraft {
  const cols = c.alt.length;
  if (cols === 0 || c.state.length !== cols || c.top.length !== cols) {
    throw new RangeError(`binProfile: ${cols} altitudes, ${c.state.length} states and ${c.top.length} tops are not one column set`);
  }
  const alt = new Float32Array(PROFILE_BINS), suggested = new Float32Array(PROFILE_BINS), top = new Float32Array(PROFILE_BINS);
  const state = new Uint8Array(PROFILE_BINS), reason = new Uint8Array(PROFILE_BINS);
  for (let i = 0; i < PROFILE_BINS; i++) {
    const first = Math.floor(i * cols / PROFILE_BINS);
    const last = Math.ceil((i + 1) * cols / PROFILE_BINS) - 1;
    let amax = -Infinity, tmin = Infinity;
    let worst: number = ColState.Measured;
    for (let x = first; x <= last; x++) {
      const a = c.alt[x], t = c.top[x];
      const finite = Number.isFinite(a);
      if (finite && a > amax) amax = a;
      if (t < tmin) tmin = t;              // NaN never compares less
      let s = WORST_ORDER[RANK[c.state[x]]];
      if (s === ColState.Measured && !finite) s = ColState.UnknownUnseen;
      if (RANK[s] < RANK[worst]) worst = s;
    }
    reason[i] = worst;
    state[i] = binStateOf(worst);
    suggested[i] = amax !== -Infinity ? amax : NaN;
    alt[i] = worst === ColState.Measured ? amax : BLOCKED_ALT;
    top[i] = tmin !== Infinity ? tmin : NaN;
  }
  return { alt, suggested, state, reason, top, lowLight: c.lowLight };
}

function cloneDraft(d: HorizonDraft): HorizonDraft {
  return { alt: d.alt.slice(), suggested: d.suggested.slice(), state: d.state.slice(), reason: d.reason.slice(), top: d.top.slice(), lowLight: d.lowLight };
}

/**
 * Owner ruling O1 (5.5): a bin that was never photographed takes the previous line instead of blocking, in state
 * Kept, with its `reason` still UnknownUnseen. The value is the highest the previous line reaches over the closed
 * bin: `horizonAltAt` at the bin's two edges, and any vertex of the previous line between them. (On a line whose
 * vertices sit on half degrees, as Save writes it, that is the larger of the two edges.)
 *
 * "Never photographed" is a bin whose worst column is unseen and none of whose columns has a coverage top. `reason`
 * is only the worst column, so it alone cannot tell a bin of unseen columns from a bin with one unseen column
 * beside a measured, dark or contrast column; `top` can, and those bins stay Unknown at 90 because part of them was
 * photographed. Dark and contrast bins always stay Unknown. With no previous line, or an empty one, `d` is returned
 * unchanged (the same object); otherwise a copy, and `d` is never written.
 */
export function mergeKept(d: HorizonDraft, previous: readonly HorizonPoint[] | null): HorizonDraft {
  if (!previous || previous.length === 0) return d;
  const line = [...previous];
  // The highest previous vertex on each closed bin: a vertex on an edge belongs to the bin on either side.
  const vertexMax = new Float64Array(PROFILE_BINS).fill(-Infinity);
  for (const p of line) {
    const x = (((p.az % 360) + 360) % 360) * BINS_PER_DEG;
    const bin = Math.floor(x);
    if (p.alt > vertexMax[bin]) vertexMax[bin] = p.alt;
    if (x === bin) {
      const left = (bin + PROFILE_BINS - 1) % PROFILE_BINS;
      if (p.alt > vertexMax[left]) vertexMax[left] = p.alt;
    }
  }
  let out: HorizonDraft | null = null;
  for (let i = 0; i < PROFILE_BINS; i++) {
    if (d.state[i] !== BinState.Unknown || d.reason[i] !== ColState.UnknownUnseen || !Number.isNaN(d.top[i])) continue;
    const v = Math.max(horizonAltAt(line, i / BINS_PER_DEG), horizonAltAt(line, (i + 1) / BINS_PER_DEG), vertexMax[i]);
    if (!Number.isFinite(v)) continue;
    out ??= cloneDraft(d);
    out.alt[i] = v;
    out.state[i] = BinState.Kept;
  }
  return out ?? d;
}
