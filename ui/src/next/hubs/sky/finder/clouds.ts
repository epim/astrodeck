// clouds.ts - the rig's own cloud dome, turned into the finder's tiles
// (hub-sky plan B.8 / H.10).
//
// ONE CONVERSION, IN ONE PLACE. `GET /api/cloudmap/dome` returns `rows[i][j]` as
// a PROBABILITY in 0..1 (cloudmap/occlusion.py's `occlusion_at().probability`),
// while every string the design prints is a PERCENT. Multiplying by 100 twice, or
// not at all, produces a picture that looks plausible either way - so it happens
// here and nowhere else.
//
// A `null` CELL IS A HOLE, NOT ZERO CLOUD. The model has no reading for that ray
// (outside the satellite disc, or before two granules exist). Folding it to 0
// would paint a clear patch of sky that nobody measured, which is the exact
// defect class this repo names "a claim nothing keeps". Nulls are dropped from
// the sample list; a target whose bin has no sample gets `null` back, and the
// chip says `cloud -`.

import { binTiles, tileOpacity, type CloudSample, type CloudTile } from "../../../lib/cloudTiles";
import type { CloudmapDome } from "../../../../api/cloudmap";
import type { Projector } from "./projection";

/** The bin size the design draws and the server's own default alt step. */
export const TILE_DEG = 6;

/** Below this the tile is not drawn and does not join a labelled blob: a 12%
 *  probability is a clear sky with noise on it, and tiling the whole dome in
 *  faint grey hides the bands that matter. */
export const TILE_MIN_PCT = 12;

/** Convert one dome payload to samples. Drops every `null` cell. */
export function domeToSamples(dome: CloudmapDome | null | undefined): CloudSample[] {
  const out: CloudSample[] = [];
  if (!dome || !Array.isArray(dome.rows)) return out;
  const altStep = dome.alt_step > 0 ? dome.alt_step : TILE_DEG;
  const azStep = dome.az_step > 0 ? dome.az_step : 10;
  for (let i = 0; i < dome.rows.length; i++) {
    const row = dome.rows[i];
    if (!Array.isArray(row)) continue;
    for (let j = 0; j < row.length; j++) {
      const p = row[j];
      if (p == null || !Number.isFinite(p)) continue; // a hole is not zero cloud
      out.push({
        alt: dome.alt_start + i * altStep,
        az: j * azStep,
        pct: p * 100,
      });
    }
  }
  return out;
}

/** The 6-degree tiles the layer draws. */
export function tilesFromDome(dome: CloudmapDome | null | undefined): CloudTile[] {
  return binTiles(domeToSamples(dome), TILE_DEG);
}

/** Cloud percent in the bin containing (alt, az), or `null` when that bin has no
 *  reading at all. Never 0 as a stand-in for "do not know". */
export function cloudPctAt(
  tiles: CloudTile[],
  altDeg: number,
  azDeg: number,
  stepDeg = TILE_DEG,
): number | null {
  if (!(altDeg >= 0) || tiles.length === 0) return null;
  const alt0 = Math.floor(altDeg / stepDeg) * stepDeg;
  const az0 = (((Math.floor(azDeg / stepDeg) * stepDeg) % 360) + 360) % 360;
  for (const t of tiles) {
    if (t.alt0 === alt0 && t.az0 === az0) return t.pct;
  }
  return null;
}

export interface CloudRect {
  key: string;
  x: number;
  y: number;
  w: number;
  h: number;
  /** Fill alpha, from `tileOpacity`. */
  opacity: number;
  pct: number;
  alt0: number;
  az0: number;
}

/** Project the tiles into screen rectangles, culled to the box. The rectangle is
 *  the tile's own alt/az extent, so it grows toward the horizon exactly as the
 *  projection does - a tile drawn at a fixed pixel size would misplace its own
 *  edges by degrees. */
export function cloudRects(
  tiles: CloudTile[],
  p: Projector,
  stepDeg = TILE_DEG,
): CloudRect[] {
  const out: CloudRect[] = [];
  for (const t of tiles) {
    if (t.pct < TILE_MIN_PCT) continue;
    const a = p.proj(t.az0, t.alt0 + stepDeg);
    const b = p.proj(t.az0 + stepDeg, t.alt0);
    const x = Math.min(a.x, b.x);
    const y = Math.min(a.y, b.y);
    const w = Math.abs(b.x - a.x);
    const h = Math.abs(b.y - a.y);
    if (x > p.W + 20 || x + w < -20 || y > p.H + 20 || y + h < -20) continue;
    out.push({
      key: `${t.alt0}:${t.az0}`,
      x, y, w, h,
      opacity: tileOpacity(t.pct),
      pct: t.pct,
      alt0: t.alt0,
      az0: t.az0,
    });
  }
  return out;
}

export interface CloudLabel {
  key: string;
  x: number;
  y: number;
  /** "CLOUD 65%". */
  text: string;
}

/**
 * One label per contiguous blob rather than one per tile - the design's
 * `CLOUD 65%` sits on a bank of cloud, not on each of its thirty bins. Adjacency
 * is 4-connected on the (alt, az) grid and WRAPS at 360, so a bank straddling
 * north is one blob and not two.
 *
 * The percentage quoted is the blob's PEAK, not its mean: the number is there to
 * answer "can I shoot through that", and the thickest part is the one that
 * decides.
 */
export function cloudBlobLabels(
  rects: CloudRect[],
  p: Projector,
  stepDeg = TILE_DEG,
): CloudLabel[] {
  if (rects.length === 0) return [];
  const byKey = new Map<string, CloudRect>();
  for (const r of rects) byKey.set(`${r.alt0}:${r.az0}`, r);

  const seen = new Set<string>();
  const labels: CloudLabel[] = [];
  const wrapAz = (az: number): number => ((az % 360) + 360) % 360;

  for (const start of rects) {
    const startKey = `${start.alt0}:${start.az0}`;
    if (seen.has(startKey)) continue;
    const stack = [startKey];
    seen.add(startKey);
    const blob: CloudRect[] = [];
    while (stack.length) {
      const key = stack.pop() as string;
      const cell = byKey.get(key);
      if (!cell) continue;
      blob.push(cell);
      const neighbours = [
        `${cell.alt0 + stepDeg}:${cell.az0}`,
        `${cell.alt0 - stepDeg}:${cell.az0}`,
        `${cell.alt0}:${wrapAz(cell.az0 + stepDeg)}`,
        `${cell.alt0}:${wrapAz(cell.az0 - stepDeg)}`,
      ];
      for (const n of neighbours) {
        if (byKey.has(n) && !seen.has(n)) { seen.add(n); stack.push(n); }
      }
    }
    // Centre the label on the blob's own centroid in ALT/AZ, then project - a
    // pixel centroid would drift toward whichever end of the blob the projection
    // stretched.
    let peak = 0;
    let sumAlt = 0;
    let sinSum = 0;
    let cosSum = 0;
    for (const c of blob) {
      peak = Math.max(peak, c.pct);
      sumAlt += c.alt0 + stepDeg / 2;
      const rad = ((c.az0 + stepDeg / 2) * Math.PI) / 180;
      sinSum += Math.sin(rad);
      cosSum += Math.cos(rad);
    }
    const alt = sumAlt / blob.length;
    const az = wrapAz((Math.atan2(sinSum, cosSum) * 180) / Math.PI);
    const at = p.proj(az, alt);
    if (at.x < 0 || at.x > p.W || at.y < 0 || at.y > p.H) continue;
    labels.push({ key: startKey, x: at.x, y: at.y, text: `CLOUD ${Math.round(peak)}%` });
  }
  return labels;
}
