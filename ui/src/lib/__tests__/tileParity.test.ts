// tileParity.test.ts — pins pixUV2ang against 3 Python-generated M31 samples
// tagged "parity" in healpix.vectors.json (tile-engine spec §7 cross-check).
import { pixUV2ang } from "../healpix";
// @ts-ignore  no @types/node; tsx supplies fs at runtime
import { readFileSync } from "node:fs";

interface UvCase { order: number; npix: number; u: number; v: number; raDeg: number; decDeg: number; }
const parity = (JSON.parse(
  readFileSync(new URL("./healpix.vectors.json", import.meta.url), "utf8"),
) as { parity: UvCase[] }).parity;

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

test("pixUV2ang matches the 3 M31 parity samples within 1e-6 deg", () => {
  assert(parity.length === 3, "3 parity vectors present");
  for (const c of parity) {
    const p = pixUV2ang(c.order, c.npix, c.u, c.v);
    assert(angSepDeg(p.raDeg, p.decDeg, c.raDeg, c.decDeg) < 1e-6,
      `parity npix ${c.npix} u ${c.u} v ${c.v}`);
  }
});

console.log(`tileParity.test.ts: ${passed} passed, ${failed} failed`);
if (failed) {
  failures.forEach((f) => console.error(f));
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
