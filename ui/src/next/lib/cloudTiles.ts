// cloudTiles.ts — IR cloud tiles: bin raw samples into 6-degree alt/az tiles
// (README "1. Sky (home)": "IR cloud tiles (6 deg alt/az bins)"; "Formulas to
// lift" -> IR tiles: "the cloud layer is the binned IR satellite image (6 deg
// alt/az bins), not blobs. Draw as tiles."), the tile fill opacity, and the
// +30 min "ghost tiles" overlay (README "7. Weather": "+30 min ghost tiles
// dashed").
//
// Bin size and the opacity formula are lifted from the prototype's reference
// logic (scratchpad seams/proto/logic.js `dome()`, tile/ghost blocks: `STEP =
// 6` and `op = (0.12 + v/160) * (back ? 0.4 : 1)`); the `back` factor there is
// 3D-dome backside dimming (screen-space), out of scope for this pure module,
// so `tileOpacity` returns the flat 0.12 + pct/160 term for a 2D renderer to
// use directly.

import { advect, type WindParams } from "./advection";

export interface CloudSample {
  alt: number;
  az: number;
  pct: number;
}

export interface CloudTile {
  /** Tile's low-altitude corner, degrees. */
  alt0: number;
  /** Tile's low-azimuth corner, degrees (0..360). */
  az0: number;
  /** Cloud cover in this tile, 0..100 (averaged across samples that land in it). */
  pct: number;
}

/** Bin samples into `stepDeg`-square alt/az tiles (README default 6 deg). */
export function binTiles(samples: CloudSample[], stepDeg = 6): CloudTile[] {
  const bins = new Map<string, { sum: number; n: number; alt0: number; az0: number }>();
  for (const s of samples) {
    if (!(s.alt >= 0) || !(s.alt < 90)) continue;
    const alt0 = Math.floor(s.alt / stepDeg) * stepDeg;
    const az0 = (((Math.floor(s.az / stepDeg) * stepDeg) % 360) + 360) % 360;
    const key = `${alt0}:${az0}`;
    const cur = bins.get(key) ?? { sum: 0, n: 0, alt0, az0 };
    cur.sum += s.pct;
    cur.n += 1;
    bins.set(key, cur);
  }
  return Array.from(bins.values()).map((b) => ({ alt0: b.alt0, az0: b.az0, pct: b.sum / b.n }));
}

/** Tile fill alpha (0..1) for a given cloud percent, for the README's
 *  `rgba(200,208,228,op)` tile fill. */
export function tileOpacity(pct: number): number {
  return Math.max(0, Math.min(1, 0.12 + pct / 160));
}

/** The same tiles, advected `minutes` forward by `wind` and re-binned onto the
 *  same `stepDeg` grid — the "+30 min ghost" overlay. A tile advected below
 *  the horizon (or off the bottom of the sky) is dropped. */
export function ghostTiles(
  tiles: CloudTile[],
  minutes: number,
  wind: WindParams,
  stepDeg = 6,
): CloudTile[] {
  const out: CloudTile[] = [];
  for (const t of tiles) {
    const centerAlt = t.alt0 + stepDeg / 2;
    const centerAz = t.az0 + stepDeg / 2;
    const moved = advect(centerAlt, centerAz, minutes, wind);
    if (!(moved.alt > 0)) continue;
    const alt0 = Math.floor(moved.alt / stepDeg) * stepDeg;
    const az0 = (((Math.floor(moved.az / stepDeg) * stepDeg) % 360) + 360) % 360;
    out.push({ alt0, az0, pct: t.pct });
  }
  return out;
}
