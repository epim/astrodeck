// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// The column tracer, ported to degrees (SPEC-v2 5.1, 5.2). It finds, in every one of the 1080 columns of the
// panorama raster, the altitude where the sky ends and something that blocks it begins, and says how far that
// number can be trusted.
//
// It is the tracer of photosphere.ts (372-1174: `columnRuns` with its `walkBack` closure, `traceSkyCoverage`,
// `applyAzimuthSupport`, `robustSpread` and the tolerances) with every constant in degrees. That file stays as it
// is until T32; nothing here imports it. The old tracer read 30 azimuth bins of five sampled columns, 101 rows
// of one degree; this one reads the raster itself, 3 columns and 3 rows per degree. What carries over unchanged is
// the walk down a column with a sky model that follows the sky, the 0.12 candidate pass that the neighbouring
// azimuths must confirm, and the rule that azimuth support may only RAISE a column. What is new (5.1):
//   - the sky model is a window model, pooled from the top 6 degrees of coverage across +-15 degrees of azimuth,
//     from smooth pixels only. It replaces the single pooled zenith seed and everything that existed to repair
//     it (#100, #129): a column is judged against the sky beside it, and a lone odd exposure has no majority;
//   - the ring sky: a window model is itself checked against what the whole compass agrees the sky is (the fix
//     round of T10, see RING SKY below), because an obstruction wider than the window IS its own window;
//   - a third departure channel, texture: foliage can match the sky in brightness and colour and not in texture;
//   - the column states of 5.2, which say why a column is not a measurement, and the placement-sigma rule;
//   - `lowLight`.
//
// UNITS. A raster row is 100 / 299 = 0.3344 degrees, a column 1/3 degree. TRACE is written in degrees, as the
// spec states it; the walk converts at TRACE.rowsPerDeg = 3 rows a degree, which is the spec's own figure. A
// published altitude is converted at the exact 100 / 299.
//
// WHAT IS NOT PORTED: the zenith rule (the raster has no shared zenith sample), the bin-level seeding of #100
// (replaced by the window), `lensInDoubt`, `complete` (replaced by the coverage run), and the bins themselves.
//
// RING SKY. SPEC-v2 5.1 item 2 pools the window model from +-15 degrees, 5.2 calls a column Tall when "the top rows do
// not match sky", and 4.4 says a span of truth above the photo top is Tall. The first two cannot deliver the third
// for any obstruction wider than the window: past about 15 degrees it fills more than half of the pool, the model
// becomes the obstruction, and the top rows match it exactly. Every test of Tall and of Measured then compares the
// obstruction with itself, and the traced line lands on the ground under it. A reference the obstruction cannot be
// is needed, and the only one in a raster is the rest of the compass. So when 72 degrees are observed (the gate of
// azimuth support, for the same reason: with less, a majority is a guess) the sky of the ring is the per-field median
// of the window models of every ninth column; a column whose own model departs from it by the same test a row of the
// walk is held to (luma beyond max(0.32 of the level, 3 spreads), or blueness beyond max(8, 3 spreads, 0.32 of the
// level)) is walked against the RING sky instead, and an obstruction there departs from its first row and is Tall.
// A ring with no consensus (fewer than half of its models within the allowance of the median) has no ring sky and
// every column keeps its own. The cost is the direction the spec always chose: a stretch of sky exposed more than 30
// per cent differently from the rest of the compass (the exposure arcs of #100) is Tall, published 90, not open.
// This replaces nothing in 5.1 or 5.2; it is the one place where this file chooses between two clauses of the spec
// that disagree, and the choice is for the supervisor to confirm (the T10 fix-round report states it).
//
// WHERE THE SPEC IS SILENT, and what this file does (each is a named constant or a short comment below):
//   - the boundary pixel whose raster sigma is read is the first blocked row of the deciding run; for an open
//     column (A = 0) it is the pixel at altitude 0, because "open to the horizon" is a claim about that altitude;
//   - contrast is the larger of the luma, blueness and texture differences between the body of the departure and the
//     sky just above it, each in the raster's own noise (`noiseSigma` of that plane, never under one 8-bit level);
//     an open column has no boundary and so no contrast;
//   - the states are decided in the order UnknownUnseen, UnknownDark, Tall, UnknownContrast, Low, Measured; the
//     first three need no contrast, and Tall (the top of the evidence) outranks everything about the boundary;
//   - `columns` is half-open, [x0, x1), and wraps; `lowLight` is read over the columns asked for;
//   - texture is the mean of the 3 x 3 Immerkaer response over 5 rows, read against the window model's roughness
//     rather than followed down the column, and never below 4 sigma of the response to white noise;
//   - the "smooth pixels" of 5.1 item 2 are those with a small |response| of the Immerkaer [1 -2 1] x [1 -2 1] mask
//     (`immerkaer`, the one kernel of this module), which is what `noiseSigma` is built on. It is blind to a pure
//     horizontal or vertical edge, so a pixel on one counts as smooth; the spec's "3 x 3 Laplacian" is not that
//     kernel, which would answer to every edge.
//
// WHERE THIS IS CHEAPER THAN THE LITERAL TEXT, and the constant that restores it (SPEC-v2 4.15 budgets 8.1 Mflop):
//   - the window pool takes every third column of the +-15 degrees (MODEL_COL_STRIDE);
//   - the mosaic median is read from every ninth column, at rows to the degree (MOSAIC_STRIDE, MOSAIC_ROW_STEP).
//
// CONSTANTS THAT ARE NOT IN `TRACE`. TRACE is the surface T30 tunes and its type is fixed by SPEC-v2 3.4.
// These stay private because the spec gives them no home: the mosaic threshold and its two ramp ratios (all
// ratios, so they carry no unit), the floating-departure floor of 6 degrees, the 4 degree minimum for the
// follow model to settle, the texture constants, and the noise floor.
import { DEG, pixelBlueness, pixelLuminance } from '../../photosphereGeometry';
import {
  ColState, PANO_H, PANO_W,
  type BandPanoramaLike, type ColumnHorizon, type ExtractOptions, type HorizonExtractor,
} from '../types';
import { immerkaer, noiseSigma } from './noise';

export const TRACE: Readonly<{
  rowsPerDeg: 3; skyWindowDeg: 6; skyPoolDeg: 15; followDeg: 12; persistDeg: 12;
  lumaFrac: 0.32; blueMin: 8; blueFrac: 0.32; spreads: 3; localTol: 0.12;
  azSupportDeg: 4; azSlopRows: 18; azMinObservedDeg: 72;
  darkLuma: 40; lowLightLuma: 20; measuredContrast: 6; lowContrast: 3;
  skyAboveMeasuredDeg: 6; skyAboveLowDeg: 2; tallWithinDeg: 2; coverBottomDeg: -2; measuredMaxSigmaDeg: 1.0;
}> = Object.freeze({
  rowsPerDeg: 3, skyWindowDeg: 6, skyPoolDeg: 15, followDeg: 12, persistDeg: 12,
  lumaFrac: 0.32, blueMin: 8, blueFrac: 0.32, spreads: 3, localTol: 0.12,
  azSupportDeg: 4, azSlopRows: 18, azMinObservedDeg: 72,
  darkLuma: 40, lowLightLuma: 20, measuredContrast: 6, lowContrast: 3,
  skyAboveMeasuredDeg: 6, skyAboveLowDeg: 2, tallWithinDeg: 2, coverBottomDeg: -2, measuredMaxSigmaDeg: 1.0,
} as const);

// ---- Constants and conversions -----------------------------------------------------------------

/** The tolerances the old file derived from one measured fact (#107): the largest re-exposure a MINORITY of the
 *  compass can show without being an obstruction is 30 per cent; the wide allowance sits the whole #74 band
 *  above it, and the mosaic threshold in the middle of that band. TRACE.lumaFrac and blueFrac are the wide
 *  allowance, written as the literals its declared type demands; the test pins the relation. */
export const RE_EXPOSURE_PCT = 30;
export const RE_EXPOSURE_BAND_PCT = 2;
/** How far a candidate's departure must sit from what the rest of the compass does at the same rows, before it
 *  is published. A comparison of departures (body over the sky above it), never of levels, which is the whole of
 *  #98: two columns of the same empty sky differ in level because every frame is exposed on its own. */
export const AZ_DEPARTURE = (RE_EXPOSURE_PCT * 2 + RE_EXPOSURE_BAND_PCT) / 200;

const R = TRACE.rowsPerDeg;
/** Degrees of altitude per raster row: row y is altitude 90 - y x ROW_DEG. */
const ROW_DEG = 100 / (PANO_H - 1);
const COLS_PER_DEG = PANO_W / 360;
const rowAlt = (y: number): number => 90 - y * ROW_DEG;
const altRow = (alt: number): number => Math.round((90 - alt) / ROW_DEG);
/** The rows of accepted sky a row is tested against: the follow model is their median. */
const SKY_WINDOW = TRACE.followDeg * R;
/** Below this many accepted rows (4 degrees) the pooled window statistics stand in for the follow model. */
const SKY_WINDOW_MIN = 4 * R;
/** How far a departure must persist to be a structure rather than something the sky carries. A departure that
 *  reaches the bottom of the coverage needs no such length: the ground is under it. */
const PERSIST_ROWS = TRACE.persistDeg * R;
/** How tall a FLOATING departure must stand before the neighbours may vouch for it (6 rows of the old file).
 *  The mosaic can lower the persistence floor and cannot abolish it: 2 or 3 degrees of departure with sky under
 *  it is a wire or a marking whatever its neighbours do. */
const AZ_PERSIST_ROWS = 6 * R;
/** Ramp discriminator of #102: how much of a candidate's own drop the rows below it may repeat before it reads
 *  as a sky still darkening rather than a surface, and how large the drop must be for that to mean anything. */
const AZ_RAMP_SHARE = 0.5, AZ_RAMP_FLOOR = 0.02;
/** The window-model rows: the top 6 degrees of a column's coverage. */
const MODEL_ROWS = TRACE.skyWindowDeg * R;
/** The window-model columns: +-15 degrees of azimuth. */
const MODEL_COLS = Math.round(TRACE.skyPoolDeg * COLS_PER_DEG);
/** The pool takes every third column of the window, a degree apart: neighbouring columns of a smooth sky repeat each
 *  other, and 31 columns (558 pixels) cost a third of 91. They are symmetric about the column's own, so at any
 *  straight edge between two skies a column's own side holds 16 of the 31 and is the median: the property the
 *  seed cases rest on. Set to 1 for the literal pool of 91 columns. */
const MODEL_COL_STRIDE = 3;
/** Neighbours that may vouch for a floating candidate: +-4 degrees of azimuth. */
const AZ_REACH = Math.round(TRACE.azSupportDeg * COLS_PER_DEG);
/** Azimuth support is silent until this many columns are observed (72 degrees). */
const AZ_MIN_COLS = Math.round(TRACE.azMinObservedDeg * COLS_PER_DEG);
/** The mosaic median is read from every Nth observed column: 3 degrees, 120 columns of the ring (the old file read
 *  150 sub-columns). All 1080 would be 9 times the work for a median that does not move. The ring sky (see the
 *  header) is read from the same columns, for the same reason. */
const MOSAIC_STRIDE = 9;
/** How far a window model may sit from the ring sky and still be the ring's sky: the walk's own allowance for a row
 *  (one measured fact, #107), named apart so that the realism gate of T30 can tune the ring test without moving the
 *  walk. Luma is a fraction of the ring's level, blueness a fraction of its size (with the floor of TRACE.blueMin). */
const RING_LUMA_FRAC = TRACE.lumaFrac, RING_BLUE_FRAC = TRACE.blueFrac;
/** ...and at rows quantised to a degree: the windows it measures are 12 and 24 degrees deep, and runs whose edges
 *  differ by a row or two ask the compass the same question. */
const MOSAIC_ROW_STEP = R;
/** The top rows that must match the sky for a column not to be Tall (2 degrees). */
const TALL_ROWS = Math.round(TRACE.tallWithinDeg / ROW_DEG);
/** A run of coverage that ends within half a row of -2 degrees reaches it: the row grid is 0.33 degrees. */
const COVER_BOTTOM_ALT = TRACE.coverBottomDeg + ROW_DEG / 2;
/** The raster's measured noise is never read below one 8-bit level: quantisation and the camera's own
 *  compression put a floor under it, and a perfectly clean synthetic raster would otherwise measure every
 *  boundary at infinite contrast. */
const NOISE_FLOOR = 1;
/** A texture departure must clear this many standard deviations of the Immerkaer response to white noise
 *  (6 x the noise sigma) whatever the sky's own statistics say; a perfectly still sky has p95 and spread of 0. */
const TEX_FLOOR_SIGMAS = 4;
/** The texture channel is the mean of the raw response over 2 x this + 1 rows. */
const TEX_HALF = 2;
const MAD_TO_SIGMA = 1.4826;
/** Luma and blueness are held to 1/1024 of a level. `pixelBlueness` of an exact grey is 3e-14, not 0, because
 *  0.299 + 0.587 + 0.114 is not 1 in floating point; and the walk compares a row with its own sky at tolerance 0
 *  in places (`betweenSkyAnd` in the trace-back), where that residue is the difference between a grey row and
 *  one that "overshoots". A thousandth of a level is far under the 8-bit step of the data. */
const level = (v: number): number => Math.round(v * 1024) / 1024;

// ---- Statistics ----------------------------------------------------------------------------------

/** The value at percentile `p` (0..1), nearest-rank. Empty input is 0. */
export function percentile(values: number[], p: number): number {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.min(sorted.length - 1, Math.max(0, Math.floor(p * (sorted.length - 1))))];
}

/** A robust spread: the median absolute deviation scaled so that on normal noise it reads as a standard
 *  deviation. Robust because the pool it is asked about is not pure sky: a roof or a disc in the top rows must
 *  widen the sky's tolerance by nothing at all. */
export function robustSpread(values: number[]): number {
  if (values.length === 0) return 0;
  const middle = percentile(values, 0.5);
  return MAD_TO_SIGMA * percentile(values.map(v => Math.abs(v - middle)), 0.5);
}

/** The k-th smallest of a[0..n) (0-based), reordering `a`. Wirth's selection; every value must be finite. */
function kth(a: Float64Array, n: number, k: number): number {
  let l = 0, m = n - 1;
  while (l < m) {
    const x = a[k];
    let i = l, j = m;
    do {
      while (a[i] < x) i++;
      while (x < a[j]) j--;
      if (i <= j) { const t = a[i]; a[i] = a[j]; a[j] = t; i++; j--; }
    } while (i <= j);
    if (j < k) l = i;
    if (k < i) m = j;
  }
  return a[k];
}
/** Nearest-rank index of percentile `p` in `n` samples, as `percentile` reads it. */
const rank = (n: number, p: number): number => Math.floor(p * (n - 1));

const scratch = new Float64Array(PANO_H);
/** Median (nearest rank) of the finite samples of `a[from..to]`, NaN when there are none. */
function medianRange(a: Float32Array, from: number, to: number): number {
  let n = 0;
  for (let i = Math.max(0, from); i <= to && i < a.length; i++) if (Number.isFinite(a[i])) scratch[n++] = a[i];
  return n === 0 ? NaN : kth(scratch, n, rank(n, 0.5));
}

/** The last `cap` values pushed, kept sorted, so the follow model's median, p95 and MAD cost a few dozen
 *  operations a row instead of a sort. Nearest-rank, like `percentile`. */
class RollingWindow {
  private readonly sorted: Float64Array;
  private readonly ring: Float64Array;
  n = 0;
  private head = 0;
  constructor(private readonly cap: number) {
    this.sorted = new Float64Array(cap);
    this.ring = new Float64Array(cap);
  }
  clear(): void { this.n = 0; this.head = 0; }
  private lowerBound(v: number): number {
    let lo = 0, hi = this.n;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (this.sorted[mid] < v) lo = mid + 1; else hi = mid;
    }
    return lo;
  }
  push(v: number): void {
    if (this.n === this.cap) {
      const at = this.lowerBound(this.ring[this.head]);
      this.sorted.copyWithin(at, at + 1, this.n);
      this.n--;
    }
    this.ring[this.head] = v;
    this.head = (this.head + 1) % this.cap;
    const at = this.lowerBound(v);
    this.sorted.copyWithin(at + 1, at, this.n);
    this.sorted[at] = v;
    this.n++;
  }
  at(p: number): number { return this.sorted[rank(this.n, p)]; }
  /** 1.4826 x the median absolute deviation, the k-th smallest deviation found by walking out from the median. */
  spread(): number {
    const n = this.n;
    if (n === 0) return 0;
    const s = this.sorted, mi = rank(n, 0.5), m = s[mi];
    let l = mi - 1, r = mi + 1, dev = 0;
    for (let step = 0; step < mi; step++) {
      const dl = l >= 0 ? m - s[l] : Infinity, dr = r < n ? s[r] - m : Infinity;
      if (dl <= dr) { dev = dl; l--; } else { dev = dr; r++; }
    }
    return MAD_TO_SIGMA * dev;
  }
}

// ---- The placement-sigma rule (5.2) ----------------------------------------------------------------

/**
 * sqrt(placement^2 + focalTerm^2) with focalTerm = |atan(tan u x (1 + sdF)) - u|, u the boundary's angle off the
 * optical axis and sdF the focal estimate's relative sd. A focal error moves a point by more the further it
 * sits from the axis. `placementSigmaDeg` is the raster sigma at the boundary pixel, Infinity when it is unknown
 * (the raster byte 255).
 */
export function boundarySigmaDeg(placementSigmaDeg: number, focalSdPct: number, offAxisDeg: number): number {
  const u = Math.min(Math.abs(offAxisDeg), 89.9) * DEG;
  const focalTerm = Math.abs(Math.atan(Math.tan(u) * (1 + focalSdPct / 100)) - u) / DEG;
  return Math.hypot(placementSigmaDeg, focalTerm);
}

// ---- Types ------------------------------------------------------------------------------------------

/** One column of the raster, by absolute row (0..299). Rows outside the painted run holding altitude 0, and
 *  texture where a 3 x 3 window is not fully painted, are NaN. `lap` is the raw Immerkaer response of a pixel;
 *  `tex` is its mean over 5 rows, the texture channel the walk reads. */
interface ColData {
  x: number;
  first: number; last: number;        // coverTop and coverBottom rows
  topAlt: number; bottomAlt: number;
  lum: Float32Array; blue: Float32Array; lap: Float32Array; tex: Float32Array;
}
/** The window sky model of one column: the medians of luma and blueness of the smooth pooled pixels, their robust
 *  spreads, and the texture statistics of the whole pool. */
interface SkyModel {
  lum: number; blue: number; lumSpread: number; blueSpread: number; texLevel: number; texSpread: number;
}
/** The sky as it is at ONE row of one column: its level in both channels, how far from it still counts as sky,
 *  and how far its own samples scatter. */
interface SkyHere {
  lum: number; blue: number; lumTol: number; blueTol: number; lumNoise: number; blueNoise: number;
}
/** One departure from the sky in ONE column: the rows it covers, and the row its boundary would be placed at,
 *  already traced back to the top of the transition and refined onto the surface below it. `qualifies` is what
 *  the column can vouch for alone: it reaches the bottom of the coverage, or it stands `PERSIST_ROWS` tall.
 *  Everything else is a candidate that only the neighbouring azimuths can settle. */
interface ColumnRun {
  top: number; end: number; grounded: boolean; qualifies: boolean;
  /** The row the departure was NOTICED at, at or below `top`. The sky lies above `top`, the surface below
   *  `from`; between them is the transition, which is neither. */
  from: number;
}
interface Noise { lum: number; blue: number; texFloor: number }
interface Analysis {
  col: ColData | null; model: SkyModel | null;
  wide: ColumnRun[]; candidates: ColumnRun[]; vouched: ColumnRun | null;
}

/** The altitude a boundary at row `top` publishes: the row above the first blocked one, the lowest still open. */
const altOfTop = (top: number): number => Math.max(0, Math.min(90, rowAlt(top - 1)));

// ---- The column walk -----------------------------------------------------------------------------

const windowLum = new RollingWindow(SKY_WINDOW), windowBlue = new RollingWindow(SKY_WINDOW);
const slotPool: SkyHere[] = Array.from({ length: PANO_H }, () => (
  { lum: 0, blue: 0, lumTol: 0, blueTol: 0, lumNoise: 0, blueNoise: 0 }));
const history: SkyHere[] = new Array(PANO_H);

/** Would a row at `model`'s levels depart from `sky`? The test `off` applies to a row in the walk, with the wide
 *  allowance of TRACE, applied to a whole window model: the ring sky is the sky, and a model that is not within
 *  its allowance in luma or in blueness is not. Texture is not compared: whether a rough pool is sky needs a
 *  threshold measured on real rasters (a canopy that matches the sky in brightness and colour and wraps the whole
 *  ring has no sky to be compared with), which is for the realism gate of T30. */
function departsFromSky(model: SkyModel, sky: SkyModel): boolean {
  const lumTol = Math.max(RING_LUMA_FRAC * Math.abs(sky.lum), TRACE.spreads * sky.lumSpread);
  const blueTol = Math.max(TRACE.blueMin, TRACE.spreads * sky.blueSpread, RING_BLUE_FRAC * Math.abs(sky.blue));
  return Math.abs(model.lum - sky.lum) > lumTol || Math.abs(model.blue - sky.blue) > blueTol;
}

/** Is `value` between the sky and the surface below the boundary, give or take the channel's tolerance? The
 *  transition itself is (a blended edge pixel is part sky and part wall); a bright stripe that abuts the wall is
 *  not, and neither is anything else that overshoots past both. */
function betweenSkyAnd(value: number, sky: number, body: number, tolerance: number): boolean {
  return !Number.isFinite(value) || !Number.isFinite(body)
    || (value >= Math.min(sky, body) - tolerance && value <= Math.max(sky, body) + tolerance);
}

/**
 * Every departure from the sky in ONE column, top-down, stopping at the first one the column can vouch for on its
 * own: that one is the boundary, and the candidates above it are what the neighbours are asked about. `lumFrac`
 * and `blueFrac` are the allowance on the sky model's own level: TRACE.lumaFrac and TRACE.blueFrac for the
 * verdict a single column may publish, TRACE.localTol for both in the candidate pass the neighbours then confirm.
 *
 * The walk carries the sky with it. Every row is measured against the median of the last `SKY_WINDOW` rows
 * ACCEPTED AS SKY, in luma and blueness, so a gradient or an exposure seam is absorbed while a departure is what
 * the trend does not explain. Rows inside a departure never enter the window, so a wall with a hard edge
 * cannot teach the model to accept itself. A row departs when ANY channel does:
 *   - luma, by more than max(lumFrac x level, 3 spreads);
 *   - blueness, by more than max(8, 3 spreads, blueFrac x |level|);
 *   - texture, above the window model's p95 by 3 spreads (and above the white-noise floor).
 *
 * A following model has one blind spot (#74): an edge soft enough that every row of it is within tolerance walks
 * the model down into the obstruction one row at a time. So the model is watched as well as used: when it has
 * drifted from the sky it had `SKY_WINDOW` rows above, and everything from here down stays away from that older
 * sky, the column has walked into something, and the boundary is traced back to the row where it left.
 */
function columnRuns(col: ColData, seed: SkyModel, lumFrac: number, blueFrac: number, noise: Noise): ColumnRun[] {
  const { lum, blue, tex, first, last } = col;
  const runs: ColumnRun[] = [];
  if (!(last >= first)) return runs;
  windowLum.clear(); windowBlue.clear();
  // Texture is read against the window model's statistics and not followed down the column: the sky's roughness
  // is the sensor's, it does not trend the way its brightness does, and a rough run must not teach the model to
  // accept roughness. The floor is the white-noise floor, because a perfectly still pool has a p95 and spread of 0.
  const texTol = Math.max(noise.texFloor, seed.texLevel + TRACE.spreads * seed.texSpread);
  let histMax = first - 1;
  const fill = (slot: SkyHere): SkyHere => {
    const settled = windowLum.n >= SKY_WINDOW_MIN;
    const level = settled ? windowLum.at(0.5) : seed.lum;
    const settledBlue = settled && windowBlue.n > 0;
    const levelBlue = settledBlue ? windowBlue.at(0.5) : seed.blue;
    const spread = settled ? windowLum.spread() : seed.lumSpread;
    const spreadBlue = settledBlue ? windowBlue.spread() : seed.blueSpread;
    slot.lum = level; slot.blue = levelBlue;
    slot.lumTol = Math.max(lumFrac * Math.abs(level), TRACE.spreads * spread);
    slot.blueTol = Math.max(TRACE.blueMin, TRACE.spreads * spreadBlue,
      Number.isFinite(levelBlue) ? blueFrac * Math.abs(levelBlue) : 0);
    slot.lumNoise = TRACE.spreads * spread; slot.blueNoise = TRACE.spreads * spreadBlue;
    return slot;
  };
  // Sign agnostic, and any one channel is enough: a wall can be darker than the sky, brighter, the same
  // brightness in a different colour (#58), or the same in both and rougher.
  const off = (row: number, sky: SkyHere): boolean => {
    if (!Number.isFinite(lum[row])) return false;
    if (Math.abs(lum[row] - sky.lum) > sky.lumTol) return true;
    if (Number.isFinite(blue[row]) && Number.isFinite(sky.blue) && Math.abs(blue[row] - sky.blue) > sky.blueTol) return true;
    return tex[row] > texTol;
  };
  /** Has the model been walked away from the sky it had a window ago? */
  const walked = (here: SkyHere, then: SkyHere): boolean =>
    Math.abs(here.lum - then.lum) > then.lumTol
    || (Number.isFinite(here.blue) && Number.isFinite(then.blue) && Math.abs(here.blue - then.blue) > then.blueTol);
  /** Is this row still the sky the transition started from? Either it sits inside that sky's own noise, or the
   *  model AT this row has not been walked away from it and the row matches THAT. The second clause is what stops
   *  the trace-back climbing an ordinary sky gradient: there the model follows and every row matches it. Both
   *  noises come from the frozen sky, because a model already inside the transition has a spread that would
   *  excuse anything. */
  const backAtSky = (row: number, frozen: SkyHere): boolean => {
    const matches = (level: number, levelBlue: number) =>
      Math.abs(lum[row] - level) <= frozen.lumNoise
      && (!Number.isFinite(blue[row]) || !Number.isFinite(levelBlue) || Math.abs(blue[row] - levelBlue) <= frozen.blueNoise);
    if (matches(frozen.lum, frozen.blue)) return true;
    const near = history[Math.min(row, histMax)];
    return !walked(near, frozen) && matches(near.lum, near.blue);
  };
  /** From a detected departure, step UP to the row where the column left the sky: the first row of the
   *  transition, not the row where the tolerance was finally exceeded. Bounded at two windows, which is as far
   *  back as a lagged reference can see. */
  const walkBack = (from: number, frozen: SkyHere, bodyLum: number, bodyBlue: number): number => {
    let top = from;
    const limit = Math.max(first, from - 2 * SKY_WINDOW);
    while (top > limit) {
      const row = top - 1;
      if (!Number.isFinite(lum[row])) break;
      if (!betweenSkyAnd(lum[row], frozen.lum, bodyLum, 0)) break;
      if (!betweenSkyAnd(blue[row], frozen.blue, bodyBlue, 0)) break;
      if (backAtSky(row, frozen)) break;
      top = row;
    }
    return top;
  };
  for (let start = first; start <= last; start++) {
    const here = fill(slotPool[start]);
    history[start] = here; histMax = start;
    const lagged = history[Math.max(first, start - SKY_WINDOW)];
    if (off(start, here)) {
      let end = start;
      while (end < last && off(end + 1, here)) end++;
      // It reaches the bottom, or it is tall enough to be a thing. A run that is neither is still recorded: it
      // is the candidate #71 is about, and the neighbours decide it.
      const grounded = end >= last;
      const qualifies = grounded || end - start + 1 >= PERSIST_ROWS;
      const body = Math.min(end, start + PERSIST_ROWS - 1);
      const bodyLum = medianRange(lum, start, body);
      const bodyBlue = medianRange(blue, start, body);
      let top = walkBack(start, lagged, bodyLum, bodyBlue);
      // Where the surface actually starts, coming the other way. The rows above it inside the run departed from
      // the sky in some other direction (a bright stripe over a wall put the boundary 4 degrees too high), and a
      // row that matches neither the sky nor the surface is not where one becomes the other. The cost is
      // pinned by a test: a glint on top of a dark roof is skipped the same way.
      while (top < end && !(betweenSkyAnd(lum[top], here.lum, bodyLum, here.lumTol)
        && betweenSkyAnd(blue[top], here.blue, bodyBlue, here.blueTol))) top++;
      runs.push({ top, end, from: Math.max(top, start), grounded, qualifies });
      if (qualifies) return runs;
      // Stepped over: the run's rows never enter the window, and the model at each is the one standing when the
      // run began.
      for (let skipped = start; skipped <= end; skipped++) history[skipped] = here;
      histMax = end;
      start = end;
      continue;
    }
    if (walked(here, lagged)) {
      // The model has drifted. That is only a boundary if what is below stays away from the older sky: a sky
      // that wandered and came back has not walked anywhere.
      let stays = true;
      for (let row = start; row <= Math.min(start + PERSIST_ROWS - 1, last); row++) {
        if (!off(row, lagged)) { stays = false; break; }
      }
      if (stays) {
        runs.push({ top: walkBack(start, lagged, here.lum, here.blue), end: last, from: start, grounded: true, qualifies: true });
        return runs;
      }
    }
    windowLum.push(lum[start]);
    if (Number.isFinite(blue[start])) windowBlue.push(blue[start]);
  }
  return runs;
}

// ---- The extraction ------------------------------------------------------------------------------

/** Planes are padded by one column each side, so the 3 x 3 mask never has to wrap by hand. */
const PW = PANO_W + 2;
const planeAt = (x: number, y: number): number => y * PW + x + 1;

/** The column indices an `ExtractOptions.columns` pair names: [x0, x1) wrapping, x1 < x0 meaning past 1080. */
function requestedColumns(range: readonly [number, number] | undefined): number[] {
  const out: number[] = [];
  if (range === undefined) { for (let x = 0; x < PANO_W; x++) out.push(x); return out; }
  const x0 = Math.floor(range[0]), x1 = Math.floor(range[1]);
  let n = x1 - x0;
  if (n < 0) n += PANO_W;
  for (let i = 0; i < Math.min(n, PANO_W); i++) out.push((((x0 + i) % PANO_W) + PANO_W) % PANO_W);
  return out;
}

class Extraction {
  private readonly lumP = new Float32Array(PW * PANO_H);
  private readonly blueP = new Float32Array(PW * PANO_H);
  private readonly paintedP = new Uint8Array(PW * PANO_H);
  private readonly runs: ({ topAlt: number; bottomAlt: number } | null)[] = new Array(PANO_W);
  private readonly cols: (ColData | null | undefined)[] = new Array(PANO_W);
  private readonly models: (SkyModel | null | undefined)[] = new Array(PANO_W);
  private readonly analyses: (Analysis | undefined)[] = new Array(PANO_W);
  private readonly mosaicMemo = new Map<number, number>();
  readonly noise: Noise;
  private readonly observedCols: number;
  private sampledCols: ColData[] | null = null;
  private ring: SkyModel | null | undefined;
  /** Pool scratch: the top-window pixels of the columns the window model takes (31 at the stride of 3). */
  private readonly poolL = new Float64Array(Math.ceil((2 * MODEL_COLS + 1) / MODEL_COL_STRIDE) * MODEL_ROWS);
  private readonly poolB = new Float64Array(this.poolL.length);
  private readonly poolT = new Float64Array(this.poolL.length);
  private readonly poolS = new Float64Array(this.poolL.length);
  private readonly poolU = new Float64Array(this.poolL.length);

  constructor(p: BandPanoramaLike) {
    const rgba = p.rgba;
    for (let y = 0; y < PANO_H; y++) {
      for (let x = 0; x < PANO_W; x++) {
        const i = (y * PANO_W + x) * 4;
        if (rgba[i + 3] <= 127) continue;
        const at = planeAt(x, y);
        this.lumP[at] = level(pixelLuminance(rgba[i], rgba[i + 1], rgba[i + 2]));
        this.blueP[at] = level(pixelBlueness(rgba[i], rgba[i + 1], rgba[i + 2]));
        this.paintedP[at] = 1;
      }
      const row = y * PW;   // wrap padding: column -1 is column 1079, column 1080 is column 0
      this.lumP[row] = this.lumP[row + PANO_W]; this.blueP[row] = this.blueP[row + PANO_W]; this.paintedP[row] = this.paintedP[row + PANO_W];
      this.lumP[row + PW - 1] = this.lumP[row + 1]; this.blueP[row + PW - 1] = this.blueP[row + 1]; this.paintedP[row + PW - 1] = this.paintedP[row + 1];
    }
    const sigmaL = noiseSigma(this.lumP, PW, PANO_H, this.paintedP);
    const sigmaB = noiseSigma(this.blueP, PW, PANO_H, this.paintedP);
    this.noise = {
      lum: Math.max(NOISE_FLOOR, sigmaL), blue: Math.max(NOISE_FLOOR, sigmaB),
      texFloor: TEX_FLOOR_SIGMAS * 6 * Math.max(NOISE_FLOOR, sigmaL),
    };
    let observed = 0;
    for (let x = 0; x < PANO_W; x++) {
      this.runs[x] = p.observedRun(x);
      if (this.runs[x] !== null) observed++;
    }
    this.observedCols = observed;
  }

  get azimuthSupportActive(): boolean { return this.observedCols >= AZ_MIN_COLS; }

  /** The painted run holding altitude 0, as the raster reports it, read into absolute rows. */
  col(x: number): ColData | null {
    const held = this.cols[x];
    if (held !== undefined) return held;
    const run = this.runs[x];
    if (run === null) return (this.cols[x] = null);
    const first = Math.max(0, altRow(run.topAlt)), last = Math.min(PANO_H - 1, altRow(run.bottomAlt));
    const lum = new Float32Array(PANO_H).fill(NaN), blue = new Float32Array(PANO_H).fill(NaN);
    const lap = new Float32Array(PANO_H).fill(NaN), tex = new Float32Array(PANO_H).fill(NaN);
    const { lumP, blueP, paintedP } = this;
    for (let y = first; y <= last; y++) {
      const at = planeAt(x, y);
      lum[y] = lumP[at]; blue[y] = blueP[at];
      if (y < 1 || y > PANO_H - 2) continue;
      let whole = true;
      for (let dy = -PW; dy <= PW && whole; dy += PW) whole = paintedP[at + dy - 1] === 1 && paintedP[at + dy] === 1 && paintedP[at + dy + 1] === 1;
      if (whole) lap[y] = Math.abs(immerkaer(lumP, at, PW));
    }
    // One pixel of foliage is rough only at random (its Laplacian can land near zero); five rows of it are rough
    // reliably, and five rows of sky noise stay far under the floor.
    for (let y = first; y <= last; y++) {
      let sum = 0, n = 0;
      for (let k = Math.max(first, y - TEX_HALF); k <= Math.min(last, y + TEX_HALF); k++) {
        if (Number.isFinite(lap[k])) { sum += lap[k]; n++; }
      }
      if (n >= 3) tex[y] = sum / n;
    }
    return (this.cols[x] = { x, first, last, topAlt: run.topAlt, bottomAlt: run.bottomAlt, lum, blue, lap, tex });
  }

  /** The window sky model: the top 6 degrees of coverage pooled across +-15 degrees, smooth pixels only (a 3 x 3
   *  Laplacian below the pool's 30th percentile), medians and robust spreads. Null where nothing can be pooled. */
  model(x: number): SkyModel | null {
    const held = this.models[x];
    if (held !== undefined) return held;
    const { poolL, poolB, poolT, poolS, poolU } = this;
    let n = 0;
    for (let dx = -MODEL_COLS; dx <= MODEL_COLS; dx += MODEL_COL_STRIDE) {
      const c = this.col((((x + dx) % PANO_W) + PANO_W) % PANO_W);
      if (c === null) continue;
      const to = Math.min(c.last, c.first + MODEL_ROWS - 1);
      for (let y = c.first; y <= to; y++) {
        if (!Number.isFinite(c.lap[y])) continue;
        poolL[n] = c.lum[y]; poolB[n] = c.blue[y]; poolT[n] = c.lap[y]; n++;
      }
    }
    if (n < SKY_WINDOW_MIN) return (this.models[x] = null);
    poolS.set(poolT.subarray(0, n));
    const smooth = kth(poolS, n, rank(n, 0.3));
    let m = 0;
    for (let i = 0; i < n; i++) if (poolT[i] <= smooth) { poolS[m] = poolL[i]; poolU[m] = poolB[i]; m++; }
    const lum = kth(poolS, m, rank(m, 0.5));
    for (let i = 0; i < m; i++) poolS[i] = Math.abs(poolS[i] - lum);
    const lumSpread = MAD_TO_SIGMA * kth(poolS, m, rank(m, 0.5));
    const blue = kth(poolU, m, rank(m, 0.5));
    for (let i = 0; i < m; i++) poolU[i] = Math.abs(poolU[i] - blue);
    const blueSpread = MAD_TO_SIGMA * kth(poolU, m, rank(m, 0.5));
    poolS.set(poolT.subarray(0, n));
    const texLevel = kth(poolS, n, rank(n, 0.95));
    poolS.set(poolT.subarray(0, n));
    const texMid = kth(poolS, n, rank(n, 0.5));
    for (let i = 0; i < n; i++) poolS[i] = Math.abs(poolT[i] - texMid);
    const texSpread = MAD_TO_SIGMA * kth(poolS, n, rank(n, 0.5));
    return (this.models[x] = { lum, blue, lumSpread, blueSpread, texLevel, texSpread });
  }

  /** Every ninth observed column of the ring: what the mosaic median and the ring sky are read from. */
  private sampled(): ColData[] {
    if (this.sampledCols === null) {
      this.sampledCols = [];
      for (let x = 0; x < PANO_W; x += MOSAIC_STRIDE) { const c = this.col(x); if (c !== null) this.sampledCols.push(c); }
    }
    return this.sampledCols;
  }

  /** The sky the whole compass agrees on (see RING SKY in the header): each field of the window models of the sampled
   *  columns, at its median. Null while fewer than 72 degrees are observed, and null when the models do not agree:
   *  fewer than half of them within the allowance of the median means there is no majority sky to hold a column to. */
  ringSky(): SkyModel | null {
    if (this.ring !== undefined) return this.ring;
    this.ring = null;
    if (!this.azimuthSupportActive) return null;
    const models: SkyModel[] = [];
    for (const c of this.sampled()) { const m = this.model(c.x); if (m !== null) models.push(m); }
    if (models.length === 0) return null;
    const middle = (field: (m: SkyModel) => number): number => percentile(models.map(field), 0.5);
    const sky: SkyModel = {
      lum: middle(m => m.lum), blue: middle(m => m.blue),
      lumSpread: middle(m => m.lumSpread), blueSpread: middle(m => m.blueSpread),
      texLevel: middle(m => m.texLevel), texSpread: middle(m => m.texSpread),
    };
    let agree = 0;
    for (const m of models) if (!departsFromSky(m, sky)) agree++;
    return (this.ring = agree * 2 >= models.length ? sky : null);
  }

  /** A column's placement among the states that need no walk: no run at all, a coverage gap, a dark sky. */
  private readyToWalk(col: ColData | null, model: SkyModel | null): boolean {
    return col !== null && model !== null && col.bottomAlt <= COVER_BOTTOM_ALT && model.lum >= TRACE.darkLuma;
  }

  analysis(x: number): Analysis {
    const held = this.analyses[x];
    if (held !== undefined) return held;
    const col = this.col(x);
    // A window model that is not the sky of the ring is an obstruction wider than the window (or an exposure the rest
    // of the compass does not share): the column is walked against the ring sky, and what departs from it at the very
    // top is Tall. The dark test, the walk and `lowLight` all read the model the column is walked against.
    let model = this.model(x);
    const ring = model !== null ? this.ringSky() : null;
    if (model !== null && ring !== null && departsFromSky(model, ring)) model = ring;
    let a: Analysis = { col, model, wide: [], candidates: [], vouched: null };
    if (this.readyToWalk(col, model)) {
      const wide = columnRuns(col!, model!, TRACE.lumaFrac, TRACE.blueFrac, this.noise);
      const narrow = this.azimuthSupportActive ? columnRuns(col!, model!, TRACE.localTol, TRACE.localTol, this.noise) : [];
      a = { col, model, wide, candidates: [...wide, ...narrow], vouched: wide.find(r => r.qualifies) ?? null };
    }
    return (this.analyses[x] = a);
  }

  // ---- Azimuth support (4) ----

  private departure(col: ColData, run: ColumnRun): number {
    const above = medianRange(col.lum, run.top - SKY_WINDOW, run.top - 1);
    const body = medianRange(col.lum, run.from, Math.min(run.end, run.from + 2 * SKY_WINDOW - 1));
    return above > 0 ? body / above : NaN;
  }

  private mosaicDeparture(run: ColumnRun): number {
    const q = (row: number) => row - (row % MOSAIC_ROW_STEP);
    const asked: ColumnRun = { ...run, top: q(run.top), from: q(run.from), end: q(run.end) };
    const key = (asked.top * 512 + asked.from) * 512 + asked.end;
    const held = this.mosaicMemo.get(key);
    if (held !== undefined) return held;
    const values: number[] = [];
    for (const c of this.sampled()) { const d = this.departure(c, asked); if (Number.isFinite(d)) values.push(d); }
    const value = values.length ? percentile(values, 0.5) : NaN;
    this.mosaicMemo.set(key, value);
    return value;
  }

  /** Is this departure the column's own, or what the whole mosaic is doing at these rows? A seam, a sky gradient and
   *  an arc exposed differently are the second, whatever their amplitude. The median is what the MAJORITY of the
   *  compass does: where more than half of it is blocked at those rows nothing can be promoted, and the rule turns
   *  itself off rather than misfiring. `darkOnly` is asked of a departure with SKY UNDER IT, and it is the one place
   *  this rule is not sign agnostic: a bright surface standing in the air is a cloud, a glint or a marking far more
   *  often than a structure. Anything reaching the ground is judged in either direction. */
  private standsOut(col: ColData, run: ColumnRun, darkOnly: boolean): boolean {
    const mine = this.departure(col, run), mosaic = this.mosaicDeparture(run);
    if (!Number.isFinite(mine) || !Number.isFinite(mosaic)) return false;
    return (darkOnly ? mosaic - mine : Math.abs(mine - mosaic)) > AZ_DEPARTURE;
  }

  /** Does this candidate have a SURFACE under it, or a sky that simply goes on getting darker (#102)? `carry` is
   *  what the body does across one window, the second window under `from` over the first; `drop` is the departure
   *  that made this a candidate. A surface carries none of its drop onward; a gradient carries all of it. Dividing
   *  keeps the sign, so a body moving the other way cannot be refused here. A run without two full windows of its
   *  own below `from` is unmeasurable, not ramping: reading past `end` would read the sky under a floating
   *  obstruction. */
  private keepsRamping(col: ColData, run: ColumnRun): boolean {
    if (run.from + 2 * SKY_WINDOW - 1 > run.end) return false;
    const first = medianRange(col.lum, run.from, run.from + SKY_WINDOW - 1);
    const second = medianRange(col.lum, run.from + SKY_WINDOW, run.from + 2 * SKY_WINDOW - 1);
    const drop = 1 - this.departure(col, run);
    if (!(first > 0) || !Number.isFinite(second) || !Number.isFinite(drop)) return false;
    if (Math.abs(drop) < AZ_RAMP_FLOOR) return false;
    return (1 - second / first) / drop >= AZ_RAMP_SHARE;
  }

  /** Does a departure at this row stand on its own ANYWHERE within `AZ_REACH` columns of here, reaching the ground
   *  or `PERSIST_ROWS` tall? A structure does: a roof compressed to nine degrees in one column is twenty tall, or
   *  on the ground, a few degrees along, because a roofline moves smoothly with azimuth. A disc in the sky is
   *  flanked by open sky and a chart stripe by more chart stripes, neither of which vouches for anything. A
   *  grounded candidate anchors itself, so for #74's half only `standsOut` decides. */
  private anchored(x: number, run: ColumnRun): boolean {
    for (let step = 0; step <= AZ_REACH; step++) {
      for (let side = step === 0 ? 1 : 0; side < 2; side++) {
        const c = (((x + (side === 0 ? step : -step)) % PANO_W) + PANO_W) % PANO_W;
        for (const r of this.analysis(c).candidates) {
          if (r.qualifies && Math.abs(r.top - run.top) <= TRACE.azSlopRows) return true;
        }
      }
    }
    return false;
  }

  /** The altitude and the run that decide a column once the neighbours have been heard. Azimuth support raises a
   *  column to a candidate and never lowers it: what a single column can vouch for is published whatever the
   *  neighbours say, and the mosaic is asked only about what would otherwise have been reported as open sky. */
  settle(x: number): { alt: number; chosen: ColumnRun | null } {
    const a = this.analysis(x);
    let chosen = a.vouched;
    let alt = chosen ? altOfTop(chosen.top) : 0;
    if (!this.azimuthSupportActive || a.col === null) return { alt, chosen };
    for (const run of a.candidates) {
      const candidate = altOfTop(run.top);
      if (candidate <= alt) continue;
      // The mosaic can lower the persistence floor; it cannot abolish it.
      if (!run.grounded && run.end - run.top + 1 < AZ_PERSIST_ROWS) continue;
      // Cheapest first: what the neighbours vouch for, then the ramp, then the comparison with the whole compass.
      if (!this.anchored(x, run) || this.keepsRamping(a.col, run) || !this.standsOut(a.col, run, !run.grounded)) continue;
      alt = candidate; chosen = run;
    }
    return { alt, chosen };
  }

  // ---- Classification (5.2) ----

  /** The boundary's contrast in noise sigmas: the larger of luma, blueness and texture, each the body of the
   *  departure against the sky just above it. Texture is in units of the response to white noise (6 sigma). NaN when
   *  there is no sky above the boundary to take a difference against (a boundary at the top of the coverage): that
   *  is "unknown", which is not the same as "no contrast". */
  contrast(col: ColData, run: ColumnRun): number {
    const aboveFrom = run.top - SKY_WINDOW, aboveTo = run.top - 1;
    const bodyTo = Math.min(run.end, run.from + SKY_WINDOW - 1);
    const dl = Math.abs(medianRange(col.lum, run.from, bodyTo) - medianRange(col.lum, aboveFrom, aboveTo)) / this.noise.lum;
    const db = Math.abs(medianRange(col.blue, run.from, bodyTo) - medianRange(col.blue, aboveFrom, aboveTo)) / this.noise.blue;
    const dt = (medianRange(col.tex, run.from, bodyTo) - medianRange(col.tex, aboveFrom, aboveTo)) / (6 * this.noise.lum);
    let best = NaN;   // and NaN reads as "unknown contrast" in the state rules below, never as Measured
    for (const v of [dl, db, dt]) {
      if (Number.isFinite(v) && (Number.isNaN(best) || v > best)) best = v;
    }
    return best;
  }
}

/** Rows of the wide pass that are inside a departure, by absolute row: the rows that do NOT match the sky. */
function offRows(runs: readonly ColumnRun[]): Uint8Array {
  const marked = new Uint8Array(PANO_H);
  for (const r of runs) for (let y = Math.min(r.top, r.from); y <= r.end; y++) marked[y] = 1;
  return marked;
}

/**
 * The traced horizon of the raster. Every array is 1080 long. A column outside `o.columns` is left as UnknownUnseen
 * with NaN everywhere; a column asked for carries the traced altitude A in `alt` wherever a walk ran (UnknownUnseen and
 * UnknownDark have none), 0 for an open column (whose `contrastSigma` is NaN: there is no boundary to take a contrast
 * of), and the state of 5.2. The caller publishes `alt` for Measured and 90 for everything else. A column whose
 * window model is not the ring's sky (RING SKY, above) is walked against the ring sky: it is Tall, and its `alt` is
 * where the obstruction reaches the top of the photo, not the ground under it.
 */
function extract(p: BandPanoramaLike, o: ExtractOptions): ColumnHorizon {
  const out: ColumnHorizon = {
    alt: new Float32Array(PANO_W).fill(NaN),
    state: new Uint8Array(PANO_W).fill(ColState.UnknownUnseen),
    top: new Float32Array(PANO_W).fill(NaN), bottom: new Float32Array(PANO_W).fill(NaN),
    contrastSigma: new Float32Array(PANO_W).fill(NaN), sigmaDeg: new Float32Array(PANO_W).fill(NaN),
    lowLight: false,
  };
  const ex = new Extraction(p);
  const wanted = requestedColumns(o.columns);
  const skyLumas: number[] = [];
  for (const x of wanted) {
    const a = ex.analysis(x);
    if (a.col !== null) { out.top[x] = a.col.topAlt; out.bottom[x] = a.col.bottomAlt; }
    if (a.model !== null) skyLumas.push(a.model.lum);
    // No run holding altitude 0, nothing to pool, or a coverage gap between -2 degrees and the boundary: unseen.
    if (a.col === null || a.model === null || a.col.bottomAlt > COVER_BOTTOM_ALT) continue;
    if (a.model.lum < TRACE.darkLuma) { out.state[x] = ColState.UnknownDark; continue; }
    const { alt, chosen } = ex.settle(x);
    out.alt[x] = alt;
    // The boundary pixel: the first blocked row of the deciding run, or the horizon row when the column is open.
    const boundaryRow = chosen !== null ? chosen.top : Math.max(a.col.first, Math.min(a.col.last, altRow(alt)));
    const byte = p.sigma[boundaryRow * PANO_W + x];
    const placement = byte === 255 ? Infinity : byte * 0.02;
    const sigma = boundarySigmaDeg(placement, o.focalSdPct, Math.abs(alt - o.axisAltDeg));
    out.sigmaDeg[x] = sigma;
    const contrast = chosen !== null ? ex.contrast(a.col, chosen) : NaN;
    out.contrastSigma[x] = contrast;
    // How much sky lies directly above the boundary, and does the very top of the coverage match it? The rows counted
    // begin with the row of A itself (the one above the first blocked row, or the horizon row of an open column), so
    // the altitude the sky reaches above A is measured from the topmost of them: 18 rows are 17 rows of height.
    const marked = offRows(a.wide);
    let skyRows = 0;
    for (let y = boundaryRow - 1; y >= a.col.first && !marked[y]; y--) skyRows++;
    const topMatches = !marked.subarray(a.col.first, a.col.first + TALL_ROWS).some(v => v === 1);
    const skyAbove = skyRows > 0 ? rowAlt(boundaryRow - skyRows) - alt : 0;
    // Tall is about the TOP of the evidence: a boundary within 2 degrees of the photo top, or a top that does not match
    // the sky. Less than 2 degrees of sky above a boundary that is not at the top (a wire, a disc) is a boundary found
    // and not measured, which is Low; TRACE.skyAboveLowDeg is the foot of the Low range and coincides with the Tall rule
    // wherever the sky run ends at the photo top.
    if (!topMatches || boundaryRow - a.col.first < TALL_ROWS) {
      out.state[x] = ColState.Tall;
    } else if (chosen !== null && !(contrast >= TRACE.lowContrast)) {
      out.state[x] = ColState.UnknownContrast;
    } else if ((chosen !== null && !(contrast >= TRACE.measuredContrast)) || skyAbove < TRACE.skyAboveMeasuredDeg
      || sigma > TRACE.measuredMaxSigmaDeg) {
      out.state[x] = ColState.Low;
    } else {
      out.state[x] = ColState.Measured;
    }
  }
  out.lowLight = skyLumas.length > 0 && percentile(skyLumas, 0.5) < TRACE.lowLightLuma;
  return out;
}

export const tracer: HorizonExtractor = { name: 'tracer', extract };
