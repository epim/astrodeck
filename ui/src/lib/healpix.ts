// healpix.ts — nested HEALPix math in TypeScript (tile-engine spec §2). Pure,
// no DOM. Ported from the canonical chealpix ang2pix_nest / pix2ang_nest, made
// continuous for fractional (u,v). Golden-vector gated against astropy-healpix
// (__tests__/healpix.test.ts). Within-tile convention is pinned to
// server/astrodeck/catalog/hips_local.py (column<-odd bits, row<-even bits, no
// vertical flip; lines 32-33, 52-59, 106-107, 115): u runs along columns (odd
// bits -> iy), v along rows (even bits -> ix).

const DEG = Math.PI / 180;
const RAD = 180 / Math.PI;
const HALFPI = Math.PI / 2;
// Face longitude/ring base indices (Gorski et al. 2005; chealpix jpll/jrll).
const JRLL = [2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4];
const JPLL = [1, 3, 5, 7, 0, 2, 4, 6, 1, 3, 5, 7];

function interleave(ix: number, iy: number): number {
  // even bits <- ix, odd bits <- iy (standard nested sub-index)
  let b = 0;
  for (let i = 0; i < 16; i++) {
    b |= ((ix >> i) & 1) << (2 * i);
    b |= ((iy >> i) & 1) << (2 * i + 1);
  }
  return b;
}

/** ang2pix (nested). raDeg/decDeg in degrees; returns the nested pixel index. */
export function ang2pixNested(order: number, raDeg: number, decDeg: number): number {
  const nside = 2 ** order;
  const z = Math.sin(decDeg * DEG);           // z = cos(colatitude) = sin(dec)
  const za = Math.abs(z);
  const phi = (((raDeg % 360) + 360) % 360) * DEG;
  let tt = (phi / HALFPI) % 4;                  // in [0,4)
  if (tt < 0) tt += 4;

  let ix: number;
  let iy: number;
  let face: number;
  if (za <= 2 / 3) {                            // equatorial region
    const temp1 = nside * (0.5 + tt);
    const temp2 = nside * (z * 0.75);
    const jp = Math.floor(temp1 - temp2);      // ascending edge index
    const jm = Math.floor(temp1 + temp2);      // descending edge index
    const ifp = Math.floor(jp / nside);
    const ifm = Math.floor(jm / nside);
    if (ifp === ifm) face = (ifp & 3) + 4;
    else if (ifp < ifm) face = ifp & 3;
    else face = (ifm & 3) + 8;
    ix = jm & (nside - 1);
    iy = (nside - 1) - (jp & (nside - 1));
  } else {                                      // polar region
    let ntt = Math.floor(tt);
    if (ntt >= 4) ntt = 3;
    const tp = tt - ntt;
    const tmp = nside * Math.sqrt(3 * (1 - za));
    let jp = Math.floor(tp * tmp);
    let jm = Math.floor((1 - tp) * tmp);
    if (jp >= nside) jp = nside - 1;
    if (jm >= nside) jm = nside - 1;
    if (z >= 0) { face = ntt; ix = (nside - 1) - jm; iy = (nside - 1) - jp; }
    else { face = ntt + 8; ix = jp; iy = jm; }
  }
  return face * nside * nside + interleave(ix, iy);
}

/** parentOf — the nested parent one order up (npix >> 2). */
export function parentOf(npix: number): number {
  return npix >> 2;
}

/** pixUV2ang — sky point of a FRACTIONAL (u,v) in [0,1]^2 inside nested pixel
 *  `npix` at `order`. u along columns (odd bits/iy/dy), v along rows
 *  (even bits/ix/dx). Returns raDeg in [0,360), decDeg in [-90,90]. */
export function pixUV2ang(
  order: number, npix: number, u: number, v: number,
): { raDeg: number; decDeg: number } {
  const nside = 2 ** order;
  const npface = nside * nside;
  const face = Math.floor(npix / npface);
  const ipf = npix - face * npface;
  let ix = 0;
  let iy = 0;
  for (let b = 0; b < order; b++) {
    ix |= ((ipf >> (2 * b)) & 1) << b;         // even bits -> ix (row / v)
    iy |= ((ipf >> (2 * b + 1)) & 1) << b;     // odd bits  -> iy (col / u)
  }
  const fx = ix + v;                            // dx = v
  const fy = iy + u;                            // dy = u
  const jr = JRLL[face] * nside - fx - fy;      // continuous ring coordinate
  const jpt = fx - fy;                          // continuous horizontal
  const nl4 = 4 * nside;
  let z: number;
  let nr: number;
  if (jr < nside) {                             // north polar cap
    nr = jr;
    z = 1 - (nr * nr) / (3 * npface);
  } else if (jr <= 3 * nside) {                 // equatorial belt
    nr = nside;
    z = ((2 * nside - jr) * 2) / (3 * nside);
  } else {                                      // south polar cap
    nr = nl4 - jr;
    z = (nr * nr) / (3 * npface) - 1;
  }
  const phi = nr <= 1e-12 ? 0 : (JPLL[face] + jpt / nr) * (Math.PI / 4);
  let raDeg = phi * RAD;
  raDeg = ((raDeg % 360) + 360) % 360;
  const decDeg = 90 - Math.acos(Math.min(1, Math.max(-1, z))) * RAD;
  return { raDeg, decDeg };
}

/** childUVRect — where this pixel's [0,1]^2 lands inside its ancestor `levelsUp`
 *  levels up: size = 2^-levelsUp; the low 2*levelsUp bits deinterleave into
 *  (subx = even -> v, suby = odd -> u). Used for parent-texture crops. */
export function childUVRect(
  npix: number, levelsUp: number,
): { u0: number; v0: number; size: number } {
  const size = 1 / (1 << levelsUp);
  const sub = npix & ((1 << (2 * levelsUp)) - 1);
  let subx = 0;
  let suby = 0;
  for (let b = 0; b < levelsUp; b++) {
    subx |= ((sub >> (2 * b)) & 1) << b;       // even bits -> row (v)
    suby |= ((sub >> (2 * b + 1)) & 1) << b;   // odd bits  -> col (u)
  }
  return { u0: suby * size, v0: subx * size, size };
}
