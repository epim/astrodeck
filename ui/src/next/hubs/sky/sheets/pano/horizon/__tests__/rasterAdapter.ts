// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// Test support for the column tracer (SPEC-v2 7.2, tracer row). A `BandPanoramaLike` stub that renders the columns
// of the old tracer suites as raster columns, so that `photosphereVertical`, `photosphereSeed` and
// `photosphereGradient` can run against `horizonTrace.ts` unchanged in what they ask.
//
// An old column is 101 samples of luma and blueness, sample r at altitude 90 - r. The raster is 1080 x 300, 3
// columns and 2.99 rows a degree: raster row y shows old sample round(y x 100 / 299), so each old sample becomes
// about 3 identical rows, and every raster column of an old azimuth bin shows that bin's column. Raster sigma is
// 5 (0.1 degrees) unless a case says otherwise. The stub implements what the tracer reads (`rgba`, `sigma`,
// `observedRun`) and the rest of the interface inertly; `paint` throws, because nothing here may use it.
//
// NOT production code, and nothing in production may import it.
import {
  ColState, PANO_H, PANO_W, PixClass, type BandPanoramaLike, type ColumnHorizon, type DirtyRect, type SliceSource,
} from '../../types';
import { tracer } from '../horizonTrace';

/** One old sampled column: per-row luma and blueness, 101 rows from the zenith down. NaN is unpainted. Copied
 *  from photosphere.ts 379-387 so that the port does not import the file T32 deletes. */
export interface SkyColumn { lum: number[]; blue: number[] }
/** The columns sampled across ONE old azimuth bin. */
export type SkyBin = SkyColumn[];

const ROW_DEG = 100 / (PANO_H - 1);
/** Raster row to the old sample it shows, and back. */
export const oldRowOf = (y: number): number => Math.round(y * ROW_DEG);
/** The altitude of raster row y. */
export const rowAltitude = (y: number): number => 90 - y * ROW_DEG;
/** The raster row nearest an altitude. */
export const rowOfAltitude = (alt: number): number => Math.round((90 - alt) / ROW_DEG);

/** A luma and a blueness as the bytes of an opaque pixel: b = luma + blueness, r = g chosen so the luma holds. */
export function encodePixel(lum: number, blue = 0): [number, number, number] {
  const b = Math.max(0, Math.min(255, lum + blue));
  const c = Math.max(0, Math.min(255, (lum - 0.114 * b) / 0.886));
  return [Math.round(c), Math.round(c), Math.round(b)];
}

/** What a case draws at one pixel: a luma and an optional blueness, or null for unpainted. */
export type PixelFn = (x: number, y: number, alt: number) => { lum: number; blue?: number } | null;
export interface RasterOptions {
  /** Placement sigma byte (0.02 degree units; 255 = unknown). A number for all, or a function of the pixel. */
  sigma?: number | ((x: number, y: number) => number);
}

export interface StubPanorama extends BandPanoramaLike {
  /** The altitude 0 row is painted and the run around it, as the stub reports it. */
  readonly runs: ({ topAlt: number; bottomAlt: number } | null)[];
}

/** The stub: a raster drawn by a function of the pixel. */
export function rasterFromPixels(fn: PixelFn, opts: RasterOptions = {}): StubPanorama {
  const rgba = new Uint8ClampedArray(PANO_W * PANO_H * 4);
  const cls = new Uint8Array(PANO_W * PANO_H);
  const sigma = new Uint8Array(PANO_W * PANO_H).fill(255);
  const firstSeen = new Uint16Array(PANO_W * PANO_H);
  for (let y = 0; y < PANO_H; y++) {
    const alt = rowAltitude(y);
    for (let x = 0; x < PANO_W; x++) {
      const px = fn(x, y, alt);
      if (px === null) continue;
      const i = y * PANO_W + x;
      const [r, g, b] = encodePixel(px.lum, px.blue ?? 0);
      rgba[i * 4] = r; rgba[i * 4 + 1] = g; rgba[i * 4 + 2] = b; rgba[i * 4 + 3] = 255;
      cls[i] = PixClass.Aligned; firstSeen[i] = 1;
      const s = opts.sigma ?? 5;
      sigma[i] = typeof s === 'number' ? s : s(x, y);
    }
  }
  const painted = (x: number, y: number) => rgba[(y * PANO_W + x) * 4 + 3] === 255;
  const y0 = rowOfAltitude(0);
  const runs = Array.from({ length: PANO_W }, (_, x) => {
    if (!painted(x, y0)) return null;
    let top = y0, bottom = y0;
    while (top > 0 && painted(x, top - 1)) top--;
    while (bottom < PANO_H - 1 && painted(x, bottom + 1)) bottom++;
    return { topAlt: rowAltitude(top), bottomAlt: rowAltitude(bottom) };
  });
  const coverage = new Uint8Array(720);
  for (let c = 0; c < 720; c++) if (runs[Math.floor((c + 0.5) * 1.5)] !== null) coverage[c] = PixClass.Aligned;
  return {
    rgba, cls, sigma, firstSeen, coverage, runs,
    begin(): void { /* inert */ },
    paint(_s: SliceSource, _nowMs: number): DirtyRect { throw new Error('rasterAdapter: the stub raster cannot be painted'); },
    clear(): void { /* inert */ },
    takeDirty(): DirtyRect[] { return []; },
    observedRun(x: number) { return runs[x]; },
  };
}

// ---- The old suites' view: azimuth bins of columns ----------------------------------------------------

export interface BinsOptions extends RasterOptions {
  /** Degrees of azimuth one bin occupies, from azimuth 0. Default 360 / bins.length (12 for the product's 30). */
  binDeg?: number;
}

/** The raster columns [x0, x1) of old bin `bin`. */
export function binColumns(bin: number, binDeg: number): [number, number] {
  const w = Math.round(binDeg * PANO_W / 360);
  return [bin * w, (bin + 1) * w];
}

/** The old column each raster column of `bins` shows, null where no bin paints it. A bin with several sampled
 *  columns shows them side by side, in list order, each across an equal share of the bin's width. */
function layout(bins: readonly (number[] | SkyBin)[], binDeg: number): (SkyColumn | null)[] {
  const binW = Math.round(binDeg * PANO_W / 360);
  const asBin = (entry: number[] | SkyBin): SkyBin =>
    entry.length === 0 || typeof entry[0] === 'number' ? [{ lum: entry as number[], blue: [] }] : entry as SkyBin;
  const shown = bins.map(asBin);
  return Array.from({ length: PANO_W }, (_, x) => {
    const bin = Math.floor(x / binW);
    if (bin >= shown.length || shown[bin].length === 0) return null;
    const columns = shown[bin];
    return columns[Math.min(columns.length - 1, Math.floor(((x % binW) / binW) * columns.length))];
  });
}

/** Render old bins as raster columns. Bins outside the list are unpainted. */
export function rasterFromBins(bins: readonly (number[] | SkyBin)[], opts: BinsOptions = {}): StubPanorama {
  const shown = layout(bins, opts.binDeg ?? 360 / bins.length);
  return rasterFromPixels((x, y) => {
    const col = shown[x];
    if (col === null) return null;
    const r = oldRowOf(y);
    const lum = col.lum[r];
    if (!Number.isFinite(lum)) return null;
    const blue = col.blue[r];
    return { lum, blue: Number.isFinite(blue) ? blue : 0 };
  }, opts);
}

/** Per-bin result in the old tracer's terms: the altitude each bin publishes and the bins that are not a
 *  measurement. A column publishes its altitude when it is Measured and 90 otherwise; a bin publishes the highest
 *  of its columns ("each bin answers for the whole of its own width"); a bin is uncertain when a column of it is
 *  in any state other than Measured, which is what "published 90 and not a measurement" meant.
 *
 *  SEAM COLUMNS ARE NOT READ. Where two unlike old columns meet, the 3 x 3 texture mask answers to the vertical
 *  edge between them (a real corner response: the two sides disagree about where their own horizontal edges
 *  are), in the one column on each side of it. No old bin contained such an edge, so the reduction skips those
 *  columns; every column inside a segment, the whole of what an old column was, is read. This is not only an
 *  artefact of the stub: a real vertical edge (a pole, a building corner) gives the same response in a real
 *  raster. What it costs, measured on a wall of 36 degrees against open sky: the wall's own edge column publishes
 *  2 rows (0.67 degrees) higher than the wall, and the open column beside it 1 row (0.37 degrees) higher than the
 *  horizon. Both errors are toward blocked, and the columns past them read exactly. */
export interface BinTrace {
  points: { az: number; alt: number }[];
  uncertainBins: number[];
  horizon: ColumnHorizon;
}

export function traceBins(
  bins: readonly (number[] | SkyBin)[],
  opts: BinsOptions & { focalSdPct?: number; axisAltDeg?: number } = {},
): BinTrace {
  const binDeg = opts.binDeg ?? 360 / bins.length;
  const shown = layout(bins, binDeg);
  const horizon = tracer.extract(rasterFromBins(bins, opts), { focalSdPct: opts.focalSdPct ?? 0, axisAltDeg: opts.axisAltDeg ?? 20 });
  const seam = (x: number) => shown[x] !== shown[(x + PANO_W - 1) % PANO_W] || shown[x] !== shown[(x + 1) % PANO_W];
  const points: { az: number; alt: number }[] = [], uncertainBins: number[] = [];
  bins.forEach((_, bin) => {
    const [x0, x1] = binColumns(bin, binDeg);
    let alt = 0, uncertain = false;
    for (let x = x0; x < x1; x++) {
      if (seam(x)) continue;
      const state = horizon.state[x];
      if (state !== ColState.Measured) alt = 90;
      else if (horizon.alt[x] > alt) alt = horizon.alt[x];
      if (state !== ColState.Measured) uncertain = true;
    }
    points.push({ az: Math.round((bin + 0.5) * binDeg), alt });
    if (uncertain) uncertainBins.push(bin);
  });
  return { points, uncertainBins, horizon };
}

/** Run one extraction over bins, for a case that wants the per-column answer. */
export function extractBins(bins: readonly (number[] | SkyBin)[], opts: BinsOptions = {}): ColumnHorizon {
  return tracer.extract(rasterFromBins(bins, opts), { focalSdPct: 0, axisAltDeg: 20 });
}
