// bahtinovOverlay.ts — pure geometry for the live Bahtinov spike overlay
// (polish grab-bag (a)). No React, no DOM: the whole coordinate transform lives
// here so it can be unit-tested (`npx tsx src/lib/__tests__/bahtinovOverlay.test.ts`),
// leaving BahtinovOverlay a dumb <svg> shell.
//
// COORDINATE CONTRACT (the one thing that must not drift): the server emits geom
// in DATA pixel space — the exact space `star_list` marks use — so this module
// scales it by the SAME `displayScale = display_width / data_width` StarOverlay
// applies, and the result is drawn inside PreviewStage's shared `.preview-transform`
// layer. Lines therefore land ON the star at every zoom/pan, by construction.

import type { BahtinovGeom } from "../types";

export interface SpikeSegment {
  x1: number; y1: number; x2: number; y2: number;
  central: boolean;
}
export interface BahtinovOverlayGeom {
  spikes: SpikeSegment[];
  vertex: { x: number; y: number } | null;
}

const EMPTY: BahtinovOverlayGeom = { spikes: [], vertex: null };

function finite(...ns: number[]): boolean {
  return ns.every((n) => Number.isFinite(n));
}

/** Clip the infinite line `p + t·d` to the box [0,w]x[0,h] (Liang–Barsky).
 *  Returns null when the line misses the box entirely. */
function clipToBox(
  px: number, py: number, dx: number, dy: number, w: number, h: number,
): { x1: number; y1: number; x2: number; y2: number } | null {
  let t0 = -Infinity;
  let t1 = Infinity;
  const p = [-dx, dx, -dy, dy];
  const q = [px - 0, w - px, py - 0, h - py];
  for (let i = 0; i < 4; i++) {
    if (p[i] === 0) {
      if (q[i] < 0) return null; // parallel and outside this edge
      continue;
    }
    const r = q[i] / p[i];
    if (p[i] < 0) {
      if (r > t1) return null;
      if (r > t0) t0 = r;
    } else {
      if (r < t0) return null;
      if (r < t1) t1 = r;
    }
  }
  if (!finite(t0, t1) || t1 <= t0) return null;
  return {
    x1: px + t0 * dx, y1: py + t0 * dy,
    x2: px + t1 * dx, y2: py + t1 * dy,
  };
}

/** Display-space segments for the three spikes + the crossing vertex.
 *
 *  `geom` is the server's DATA-space block (null/undefined ⇒ abstain: empty
 *  result, the overlay renders nothing). Each spike is the full-length line
 *  through its point at `angle_deg`, clipped to the `dispW x dispH` frame; a
 *  line that misses the frame is dropped rather than drawn off-canvas. */
export function bahtinovSpikeSegments(
  geom: BahtinovGeom | null | undefined,
  dispW: number,
  dispH: number,
  displayScale: number,
): BahtinovOverlayGeom {
  if (!geom || !(dispW > 0) || !(dispH > 0) || !(displayScale > 0)) return EMPTY;
  const spikes: SpikeSegment[] = [];
  for (const s of geom.spikes ?? []) {
    const px = s.x * displayScale;
    const py = s.y * displayScale;
    const rad = (s.angle_deg * Math.PI) / 180;
    const dx = Math.cos(rad);
    const dy = Math.sin(rad);
    if (!finite(px, py, dx, dy)) continue;
    const seg = clipToBox(px, py, dx, dy, dispW, dispH);
    if (seg) spikes.push({ ...seg, central: !!s.central });
  }
  const v = geom.vertex;
  const vertex =
    v && finite(v[0], v[1])
      ? { x: v[0] * displayScale, y: v[1] * displayScale }
      : null;
  return { spikes, vertex };
}
