// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// The live camera frame on the ribbon (SPEC-v2 4.11, D19). Display only.
//
// The ribbon is equirectangular, so a straight-edged camera frame is not straight on it. The central +-10 degrees
// of the frame is cut into a grid of cells, each cell into two triangles, and each triangle gets one affine
// transform that is exact at its three vertices. Every vertex goes through the live pose exactly; between the
// vertices the picture is off by at most 0.21 ribbon degrees (4 x 8, pitch 23). The overlay canvas draws each
// triangle with a clip, `setTransform(affineFromTriangle(t))` and `drawImage(liveSource, 0, 0)`.
import { qrotate, unprojectPixel } from './rotation';
import type { Intrinsics, LiveTriangle, Quat, RibbonView } from './types';

const DEG = Math.PI / 180;
/** The vertical limit of the covered part of a frame: 0.9 of the half-height, the `topFrac` of a slice (4.3). */
const VERTICAL_FRAC = 0.9;
/** A triangle wider than half the circle on the ribbon crosses the azimuth seam or the pole and is not a picture. */
const MAX_SPAN_DEG = 180;

/** Degrees into [-180, 180). */
const wrap180 = (deg: number) => (((deg + 180) % 360) + 360) % 360 - 180;

/** Azimuth and altitude (degrees) to ribbon pixels: x grows eastward from the view centre, y grows downward from
 *  `altTop`, and the azimuth is taken the short way round from `centreAz`, so a view centred on 350 shows 10 at +20. */
export function ribbonPoint(view: RibbonView, azDeg: number, altDeg: number): [x: number, y: number] {
  return [
    view.widthPx / 2 + wrap180(azDeg - view.centreAz) * view.pxPerDeg,
    (view.altTop - altDeg) * view.pxPerDeg,
  ];
}

/** The live frame as triangles from source-canvas pixels to ribbon pixels. `k` are the intrinsics of the source
 *  picture (L1 for the 90 x 160 live source) and `pose` is camera to scan frame. `srcW` x `srcH` is the canvas the
 *  triangles are drawn from: the source vertices are `k`-pixels scaled by `srcW / k.w` and `srcH / k.h`, which is the
 *  identity when the canvas is the level `k` describes. The grid covers +-`halfWidthDeg` (a tangent angle, as a slice
 *  does) by +-0.9 of the half-height. A triangle is dropped when any vertex is above `altCapDeg`, or when it is wider
 *  than 180 ribbon degrees (a triangle over the pole): near the zenith the ribbon stretches without bound and an
 *  affine cannot follow it. */
export function liveFrameMesh(pose: Quat, k: Intrinsics, srcW: number, srcH: number, view: RibbonView,
  opts: { halfWidthDeg?: number; cols?: number; rows?: number; altCapDeg?: number } = {}): LiveTriangle[] {
  const halfWidthDeg = opts.halfWidthDeg ?? 10, cols = opts.cols ?? 4, rows = opts.rows ?? 8, altCapDeg = opts.altCapDeg ?? 90;
  if (!Number.isInteger(cols) || cols < 1 || !Number.isInteger(rows) || rows < 1) {
    throw new RangeError('liveFrameMesh: cols and rows must be integers of at least 1');
  }
  const halfX = k.f * Math.tan(halfWidthDeg * DEG), halfY = VERTICAL_FRAC * k.h / 2;
  const sx = srcW / k.w, sy = srcH / k.h;

  // The (cols + 1) x (rows + 1) vertices, row-major: source pixels, ribbon pixels and altitude.
  const stride = cols + 1;
  const src: [number, number][] = [], dst: [number, number][] = [], alt: number[] = [];
  for (let j = 0; j <= rows; j++) {
    for (let i = 0; i <= cols; i++) {
      const x = k.cx - halfX + 2 * halfX * i / cols, y = k.cy - halfY + 2 * halfY * j / rows;
      const ray = qrotate(pose, unprojectPixel(k, x, y));
      const altDeg = Math.asin(Math.max(-1, Math.min(1, ray[2]))) / DEG;
      src.push([x * sx, y * sy]);
      dst.push(ribbonPoint(view, Math.atan2(ray[0], ray[1]) / DEG, altDeg));
      alt.push(altDeg);
    }
  }

  const spanLimit = MAX_SPAN_DEG * view.pxPerDeg;
  const out: LiveTriangle[] = [];
  const add = (a: number, b: number, c: number) => {
    if (alt[a] > altCapDeg || alt[b] > altCapDeg || alt[c] > altCapDeg) return;
    if (Math.max(dst[a][0], dst[b][0], dst[c][0]) - Math.min(dst[a][0], dst[b][0], dst[c][0]) > spanLimit) return;
    out.push({
      src: [src[a][0], src[a][1], src[b][0], src[b][1], src[c][0], src[c][1]],
      dst: [dst[a][0], dst[a][1], dst[b][0], dst[b][1], dst[c][0], dst[c][1]],
    });
  };
  for (let j = 0; j < rows; j++) {
    for (let i = 0; i < cols; i++) {
      const a = j * stride + i, b = a + 1, c = a + stride, d = c + 1;
      add(a, b, c);   // top-left, top-right, bottom-left
      add(d, c, b);   // bottom-right, bottom-left, top-right
    }
  }
  return out;
}

/** The six `setTransform(a, b, c, d, e, f)` arguments of the affine map that takes each source vertex to its
 *  destination vertex: x' = a x + c y + e and y' = b x + d y + f. A source triangle with no area cannot be drawn and
 *  gives the zero matrix at the first destination vertex, which draws nothing. */
export function affineFromTriangle(t: LiveTriangle): [a: number, b: number, c: number, d: number, e: number, f: number] {
  const [x0, y0, x1, y1, x2, y2] = t.src, [u0, v0, u1, v1, u2, v2] = t.dst;
  const det = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0);
  if (det === 0) return [0, 0, 0, 0, u0, v0];
  const a = ((u1 - u0) * (y2 - y0) - (u2 - u0) * (y1 - y0)) / det;
  const c = ((x1 - x0) * (u2 - u0) - (x2 - x0) * (u1 - u0)) / det;
  const b = ((v1 - v0) * (y2 - y0) - (v2 - v0) * (y1 - y0)) / det;
  const d = ((x1 - x0) * (v2 - v0) - (x2 - x0) * (v1 - v0)) / det;
  return [a, b, c, d, u0 - a * x0 - c * y0, v0 - b * x0 - d * y0];
}
