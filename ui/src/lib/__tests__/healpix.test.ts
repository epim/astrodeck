// healpix.test.ts — golden-vector gate for lib/healpix.ts (tile-engine spec §2).
// Inline-assert harness (no vitest); runs via `npx tsx`. Reads the committed
// astropy-healpix vectors via Node fs at runtime (resolveJsonModule is off and
// MUST NOT be added; @ts-ignore keeps `tsc -b` clean without @types/node).
import { ang2pixNested, pixUV2ang, parentOf, childUVRect } from "../healpix";
// @ts-ignore  no @types/node in this browser-targeted UI project; tsx supplies fs at runtime
import { readFileSync } from "node:fs";

interface AngCase { order: number; raDeg: number; decDeg: number; npix: number; }
interface UvCase { order: number; npix: number; u: number; v: number; raDeg: number; decDeg: number; }
interface Vectors { ang2pix: AngCase[]; pixUV2ang: UvCase[]; parity: UvCase[]; }

const vectors = JSON.parse(
  readFileSync(new URL("./healpix.vectors.json", import.meta.url), "utf8"),
) as Vectors;

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

const DEG = Math.PI / 180;
function angSepDeg(ra1: number, d1: number, ra2: number, d2: number): number {
  const c = Math.sin(d1 * DEG) * Math.sin(d2 * DEG)
    + Math.cos(d1 * DEG) * Math.cos(d2 * DEG) * Math.cos((ra1 - ra2) * DEG);
  return Math.acos(Math.min(1, Math.max(-1, c))) / DEG;
}

let maxAngSep = 0;
test(`ang2pixNested matches all ${vectors.ang2pix.length} astropy vectors`, () => {
  for (const c of vectors.ang2pix) {
    const got = ang2pixNested(c.order, c.raDeg, c.decDeg);
    assert(got === c.npix,
      `order ${c.order} ra ${c.raDeg} dec ${c.decDeg}: expected ${c.npix}, got ${got}`);
  }
});

test(`pixUV2ang within 1e-6 deg of all ${vectors.pixUV2ang.length} astropy vectors`, () => {
  for (const c of vectors.pixUV2ang) {
    const p = pixUV2ang(c.order, c.npix, c.u, c.v);
    const sep = angSepDeg(p.raDeg, p.decDeg, c.raDeg, c.decDeg);
    if (sep > maxAngSep) maxAngSep = sep;
    assert(sep < 1e-6,
      `order ${c.order} npix ${c.npix} u ${c.u} v ${c.v}: sep ${sep} deg`);
  }
});

// Parity vectors (M31) share pixUV2ang's contract — hold them to the same gate.
test(`pixUV2ang within 1e-6 deg of all ${vectors.parity.length} parity vectors`, () => {
  for (const c of vectors.parity) {
    const p = pixUV2ang(c.order, c.npix, c.u, c.v);
    const sep = angSepDeg(p.raDeg, p.decDeg, c.raDeg, c.decDeg);
    if (sep > maxAngSep) maxAngSep = sep;
    assert(sep < 1e-6,
      `parity order ${c.order} npix ${c.npix} u ${c.u} v ${c.v}: sep ${sep} deg`);
  }
});

// Hand cases: nested parent is npix >> 2.
test("parentOf strips the two low bits (nested)", () => {
  assert(parentOf(0b1101) === 0b11, "parentOf(13) == 3");
  assert(parentOf(47) === 11, "parentOf(47) == 11");
  assert(parentOf(0) === 0, "parentOf(0) == 0");
});

// Hand cases: childUVRect quadrant walk. size = 2^-levelsUp; even bits -> v (row),
// odd bits -> u (col). One level up, low 2 bits (ix=even=bit0, iy=odd=bit1):
//   sub 0b00 -> (u0,v0)=(0,0); 0b01 -> ix=1,iy=0 -> v0=0.5,u0=0;
//   0b10 -> ix=0,iy=1 -> v0=0,u0=0.5; 0b11 -> v0=0.5,u0=0.5.
test("childUVRect one level: quadrant corners", () => {
  const s = 0.5;
  assert(childUVRect(0b00, 1).u0 === 0 && childUVRect(0b00, 1).v0 === 0 && childUVRect(0b00, 1).size === s, "00");
  assert(childUVRect(0b01, 1).v0 === 0.5 && childUVRect(0b01, 1).u0 === 0, "01 -> v0=.5");
  assert(childUVRect(0b10, 1).u0 === 0.5 && childUVRect(0b10, 1).v0 === 0, "10 -> u0=.5");
  assert(childUVRect(0b11, 1).u0 === 0.5 && childUVRect(0b11, 1).v0 === 0.5, "11 -> (.5,.5)");
});

test("childUVRect two levels: size is 0.25 and offsets are quarter-grid", () => {
  const r = childUVRect(0b1011, 2); // low4: ix from bits0,2; iy from bits1,3
  assert(r.size === 0.25, "size 0.25");
  assert(Number.isInteger(r.u0 / 0.25) && Number.isInteger(r.v0 / 0.25), "on the quarter grid");
});

console.log(`healpix.test.ts: ${passed} passed, ${failed} failed (max pixUV2ang angsep ${maxAngSep.toExponential(3)} deg)`);
if (failed) {
  failures.forEach((f) => console.error(f));
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
