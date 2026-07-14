// tileView.ts — tile order pick, visible set, screen mesh, and parent-crop UVs
// for the Atlas tile engine (tile-engine spec §3). Pure, npx-tsx testable.
// Screen mapping is North-up / East-left, matching FovOverlay:
//   x = W/2 - xi*pxPerDeg,  y = H/2 - eta*pxPerDeg   (project() from framing.ts).
import { project, deproject } from "./framing";
import { ang2pixNested, pixUV2ang, childUVRect } from "./healpix";

const ORDER0_TILE_ARCSEC = 58.6324 * 3600; // 211076.64: healpix order-0 pixel side
const TILE_PX = 512;                        // DSS2/2MASS HiPS tile width
const MAX_ORDER = 9;
const SUBDIV = 4;                           // 4x4 quad -> 25 verts, 32 tris

/** Smallest order k whose tile scale <= the viewport scale, clamped [0,9].
 *  Same rule as hips_local._order_for (tile_width=512, max_order=9). */
export function tileOrderFor(fovDeg: number, viewportPx: number): number {
  const sOut = (fovDeg * 3600) / viewportPx;
  let k = 0;
  while (k < MAX_ORDER && ORDER0_TILE_ARCSEC / (TILE_PX * 2 ** k) > sOut) k++;
  return k;
}

/** Nested pixels covering the viewport +15% margin (the margin ring doubles as
 *  the prefetch set). Samples a 24x24 grid of ξ/η, deprojects to sky, ang2pix,
 *  dedupes. (At the picked order a tile spans >= ~500 px so 24x24 cannot skip
 *  one.) viewportPx is positionally required (order after it is used); the grid
 *  is angular, so it is not read. */
export function visibleTiles(
  centerRaDeg: number, centerDecDeg: number, fovDeg: number,
  viewportPx: number, order: number,
): number[] {
  void viewportPx; // positional-signature parity; the sample grid is angular (deg), not pixels
  const ra0h = centerRaDeg / 15;
  const half = 0.5 * fovDeg * 1.15;
  const N = 24;
  const seen = new Set<number>();
  for (let i = 0; i < N; i++) {
    for (let j = 0; j < N; j++) {
      const xi = -half + 2 * half * (i / (N - 1));
      const eta = -half + 2 * half * (j / (N - 1));
      const sky = deproject(xi, eta, ra0h, centerDecDeg);
      seen.add(ang2pixNested(order, sky.ra_hours * 15, sky.dec_deg));
    }
  }
  return [...seen];
}

/** A 4x4 subdivided quad for one tile: vertex (u,v) -> pixUV2ang -> project ->
 *  screen px. 25 vertices, 32 triangles. UVs are the tile's own [0,1] texcoords
 *  (u=col, v=row) — texture upload must not flip Y so v=0 = tile top. */
export function tileMesh(
  order: number, npix: number, centerRaDeg: number, centerDecDeg: number,
  fovDeg: number, viewportPx: number,
): { positions: Float32Array; uvs: Float32Array; indices: Uint16Array } {
  const ra0h = centerRaDeg / 15;
  const pxPerDeg = viewportPx / fovDeg;
  const half = viewportPx / 2;
  const n = SUBDIV + 1;
  const positions = new Float32Array(n * n * 2);
  const uvs = new Float32Array(n * n * 2);
  for (let r = 0; r <= SUBDIV; r++) {
    for (let c = 0; c <= SUBDIV; c++) {
      const uu = c / SUBDIV;
      const vv = r / SUBDIV;
      const sky = pixUV2ang(order, npix, uu, vv);
      const p = project(sky.raDeg / 15, sky.decDeg, ra0h, centerDecDeg);
      const idx = (r * n + c) * 2;
      positions[idx] = half - p.xi * pxPerDeg;      // East-left
      positions[idx + 1] = half - p.eta * pxPerDeg; // North-up
      uvs[idx] = uu;
      uvs[idx + 1] = vv;
    }
  }
  const indices = new Uint16Array(SUBDIV * SUBDIV * 6);
  let t = 0;
  for (let r = 0; r < SUBDIV; r++) {
    for (let c = 0; c < SUBDIV; c++) {
      const a = r * n + c;
      const b = r * n + c + 1;
      const d = (r + 1) * n + c;
      const e = (r + 1) * n + c + 1;
      indices[t++] = a; indices[t++] = b; indices[t++] = d;
      indices[t++] = b; indices[t++] = e; indices[t++] = d;
    }
  }
  return { positions, uvs, indices };
}

/** Rescale a tile's own UVs into its ancestor's texture rect (parent
 *  upsampling): u' = u0 + u*size, v' = v0 + v*size, via childUVRect. */
export function ancestorUV(npix: number, levelsUp: number, uvs: Float32Array): Float32Array {
  const { u0, v0, size } = childUVRect(npix, levelsUp);
  const out = new Float32Array(uvs.length);
  for (let i = 0; i < uvs.length; i += 2) {
    out[i] = u0 + uvs[i] * size;
    out[i + 1] = v0 + uvs[i + 1] * size;
  }
  return out;
}
