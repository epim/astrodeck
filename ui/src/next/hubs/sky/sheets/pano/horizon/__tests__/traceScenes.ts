// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// Test support for horizonTrace.test.ts and horizonTraceRules.test.ts, which were one file until the runner's 60 s
// per-file limit made it two: the scene helpers both draw with. NOT production code, and nothing in production may
// import it.
import assert from 'node:assert/strict';
import { tracer } from '../horizonTrace';
import type { ColumnHorizon } from '../../types';
import { rasterFromPixels, type PixelFn, type RasterOptions, type StubPanorama } from './rasterAdapter';

export const near = (actual: number, expected: number, tol: number, what = 'value') =>
  assert.ok(Math.abs(actual - expected) <= tol, `${what} ${actual} is not within ${tol} of ${expected}`);

/** An integer hash of three numbers to [0, 1): the same pixel is the same noise on every run, in any order. */
export function unit(a: number, b: number, salt: number): number {
  let h = Math.imul(a + 0x9E3779B1, 0x85EBCA6B) ^ Math.imul(b + 0x7F4A7C15, 0xC2B2AE35) ^ Math.imul(salt + 0x165667B1, 0x27D4EB2F);
  h = Math.imul(h ^ (h >>> 15), 0x2C1B3C6D); h = Math.imul(h ^ (h >>> 12), 0x297A2D39); h ^= h >>> 15;
  return (h >>> 0) / 4294967296;
}
export const gauss = (a: number, b: number, salt: number) =>
  Math.sqrt(-2 * Math.log(1 - unit(a, b, salt))) * Math.cos(2 * Math.PI * unit(a, b, salt + 1));

/** What a scene draws at an azimuth (degrees) and altitude (degrees). */
export type SceneFn = (az: number, alt: number, x: number, y: number) => { lum: number; blue?: number } | null;
export const scene = (fn: SceneFn, opts?: RasterOptions): StubPanorama =>
  rasterFromPixels(((x, y, alt) => fn((x + 0.5) / 3, alt, x, y)) as PixelFn, opts);
export const extract = (pano: StubPanorama, o: { focalSdPct?: number; axisAltDeg?: number; columns?: [number, number] } = {}): ColumnHorizon =>
  tracer.extract(pano, { focalSdPct: o.focalSdPct ?? 0, axisAltDeg: o.axisAltDeg ?? 20, columns: o.columns });
/** The raster column of an azimuth. */
export const col = (az: number) => Math.floor(az * 3);
export const inSector = (az: number, from: number, to: number) => az >= from && az < to;

export const SKY = 130, WALL = 40;

/** A ring of open sky with a wall in azimuth [from, to) from `top` degrees down. */
export const wallScene = (from: number, to: number, top: number, extra?: { coverTop?: number; coverBottom?: number; sky?: number }): SceneFn =>
  (az, alt) => {
    if (alt > (extra?.coverTop ?? 90) || alt < (extra?.coverBottom ?? -10)) return null;
    return { lum: inSector(az, from, to) && alt < top ? WALL : (extra?.sky ?? SKY) };
  };
