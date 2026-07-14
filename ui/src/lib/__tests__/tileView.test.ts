// tileView.test.ts — pure tests for lib/tileView.ts (tile-engine spec §3).
// Inline-assert harness (no vitest); runs via `npx tsx`.
import { tileOrderFor, visibleTiles, tileMesh, ancestorUV } from "../tileView";
import { ang2pixNested, pixUV2ang } from "../healpix";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function near(a: number, b: number, tol: number, msg = ""): void {
  if (Math.abs(a - b) > tol) throw new Error(`${msg} expected ~${b}, got ${a}`);
}

const DEG = Math.PI / 180;
function angSepDeg(ra1: number, d1: number, ra2: number, d2: number): number {
  const c = Math.sin(d1 * DEG) * Math.sin(d2 * DEG)
    + Math.cos(d1 * DEG) * Math.cos(d2 * DEG) * Math.cos((ra1 - ra2) * DEG);
  return Math.acos(Math.min(1, Math.max(-1, c))) / DEG;
}

// --- tileOrderFor: honest expected values. Tile scale = 211076.64/(512*2^k)
//     arcsec/px (211076.64 = 58.6324*3600); viewport scale = fov*3600/px.
//   fov 1.5, px 768 -> sOut=7.03125; 412.26/2^k <= 7.03 -> k=6 (6.44).
//   fov 0.5, px 768 -> sOut=2.34375; -> k=8 (1.61).
//   fov 10,  px 360 -> sOut=100;     -> k=3 (51.5).
//   fov 60,  px 512 -> sOut=421.9;   -> k=0 (412.26 <= 421.9).
//   fov 0.02,px 768 -> tiny sOut     -> clamps to k=9.
test("tileOrderFor table", () => {
  assert(tileOrderFor(1.5, 768) === 6, "1.5/768 -> 6");
  assert(tileOrderFor(0.5, 768) === 8, "0.5/768 -> 8");
  assert(tileOrderFor(10, 360) === 3, "10/360 -> 3");
  assert(tileOrderFor(60, 512) === 0, "60/512 -> 0");
  assert(tileOrderFor(0.02, 768) === 9, "0.02/768 -> clamp 9");
});

// --- visibleTiles: known view. Assert center tile present, all tiles within an
//     angular bound of the view center, and a sane count.
test("visibleTiles contains the center tile and is bounded", () => {
  const ra = 45, dec = 20, fov = 1.5, px = 768;
  const order = tileOrderFor(fov, px);
  const set = visibleTiles(ra, dec, fov, px, order);
  const center = ang2pixNested(order, ra, dec);
  assert(set.includes(center), "includes center tile");
  assert(set.length >= 4 && set.length <= 30, `count sane, got ${set.length}`);
  const bound = fov * 1.15; // margin-diagonal upper bound (deg)
  for (const npix of set) {
    const c = pixUV2ang(order, npix, 0.5, 0.5);
    assert(angSepDeg(c.raDeg, c.decDeg, ra, dec) < bound + fov,
      `tile ${npix} within angular bound`);
  }
});

// --- tileMesh: for the tile containing the view center, the (0.5,0.5) middle
//     vertex lands at the viewport center. 4x4 subdiv => 25 verts, index 12*2.
test("tileMesh center vertex at viewport center", () => {
  const order = 6, px = 512, fov = 1.0;
  // Choose the tile whose center IS the view center so the middle vertex maps
  // exactly to (W/2, H/2).
  const npix = ang2pixNested(order, 45, 20);
  const c = pixUV2ang(order, npix, 0.5, 0.5); // the tile's true center
  const mesh = tileMesh(order, npix, c.raDeg, c.decDeg, fov, px);
  assert(mesh.positions.length === 25 * 2, "25 vertices");
  assert(mesh.uvs.length === 25 * 2, "25 uvs");
  assert(mesh.indices.length === 32 * 3, "32 triangles");
  const mid = (2 * 5 + 2) * 2; // r=2,c=2 vertex (n=5 across)
  near(mesh.positions[mid], px / 2, 0.5, "mid x");
  near(mesh.positions[mid + 1], px / 2, 0.5, "mid y");
});

// --- tileMesh: North-up / East-left orientation via two known offset points.
//     A vertex NORTH of center (higher dec) has smaller screen y (up); a vertex
//     EAST of center (higher RA) has smaller screen x (left).
test("tileMesh orientation: North up, East left", () => {
  const order = 5, px = 600, fov = 2.0;
  const npix = ang2pixNested(order, 80, 10);
  const c = pixUV2ang(order, npix, 0.5, 0.5);
  const mesh = tileMesh(order, npix, c.raDeg, c.decDeg, fov, px);
  // Sample two mesh vertices and compare against their sky positions.
  let north = -1;
  let east = -1;
  for (let i = 0; i < 25; i++) {
    const uu = (i % 5) / 4;
    const vv = Math.floor(i / 5) / 4;
    const sky = pixUV2ang(order, npix, uu, vv);
    if (sky.decDeg > c.decDeg + 0.05 && north < 0) north = i;
    // East = higher RA (small wrap-safe delta)
    const dRa = ((sky.raDeg - c.raDeg + 540) % 360) - 180;
    if (dRa > 0.05 && east < 0) east = i;
  }
  assert(north >= 0 && east >= 0, "found north + east sample verts");
  assert(mesh.positions[north * 2 + 1] < px / 2, "north vertex is ABOVE center (up)");
  assert(mesh.positions[east * 2] < px / 2, "east vertex is LEFT of center");
});

// --- ancestorUV: quadrant remap. Child npix ...b01 one level up sits in the
//     v-high half (even bit set) at u-low; its UVs rescale into [v0,v0+0.5].
test("ancestorUV rescales child UVs into the ancestor rect", () => {
  const uvs = new Float32Array([0, 0, 1, 0, 0, 1, 1, 1]);
  const out = ancestorUV(0b01, 1, uvs); // childUVRect: u0=0, v0=0.5, size=0.5
  near(out[0], 0.0, 1e-9, "u corner");
  near(out[1], 0.5, 1e-9, "v corner -> v0");
  near(out[6], 0.5, 1e-9, "u=1 -> u0+size");
  near(out[7], 1.0, 1e-9, "v=1 -> v0+size");
});

console.log(`tileView.test.ts: ${passed} passed, ${failed} failed`);
if (failed) {
  failures.forEach((f) => console.error(f));
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
