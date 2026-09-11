// Pure-lib test for cloudTiles.ts. Sabotage check: using `Math.round` instead
// of `Math.floor` for the bin index turns "bins a sample into its 6deg tile"
// red at a boundary; dropping the `/160` divisor in tileOpacity turns the
// opacity worked example red; forgetting to re-bin after advecting turns
// "ghostTiles keeps the same tile count and re-bins onto the grid" red.
import { binTiles, ghostTiles, tileOpacity } from "../cloudTiles";

let passed = 0, failed = 0; const failures: string[] = [];
function test(n: string, fn: () => void) { try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${n}: ${(e as Error).message}`); } }
function eq<T>(a: T, b: T, m = ""): void { if (a !== b) throw new Error(`${m} expected ${String(b)}, got ${String(a)}`); }
function near(a: number, b: number, eps: number, m = ""): void {
  if (Math.abs(a - b) > eps) throw new Error(`${m} expected ~${b}, got ${a}`);
}
function ok(cond: boolean, m: string): void { if (!cond) throw new Error(m); }

test("binTiles: a sample at alt 32 az 63 lands in the 30/60 tile (6deg bins)", () => {
  const tiles = binTiles([{ alt: 32, az: 63, pct: 40 }], 6);
  eq(tiles.length, 1);
  eq(tiles[0].alt0, 30);
  eq(tiles[0].az0, 60);
  eq(tiles[0].pct, 40);
});

test("binTiles: two samples in the same bin average their cloud percent", () => {
  const tiles = binTiles([
    { alt: 31, az: 61, pct: 20 },
    { alt: 34, az: 64, pct: 60 },
  ], 6);
  eq(tiles.length, 1);
  eq(tiles[0].pct, 40);
});

test("binTiles: samples below the horizon or at/above zenith are dropped", () => {
  const tiles = binTiles([{ alt: -1, az: 10, pct: 50 }, { alt: 90, az: 10, pct: 50 }], 6);
  eq(tiles.length, 0);
});

test("tileOpacity: README's rgba(200,208,228,op) alpha term is 0.12 + pct/160", () => {
  near(tileOpacity(0), 0.12, 1e-9);
  near(tileOpacity(50), 0.12 + 50 / 160, 1e-9);
  eq(tileOpacity(-1000), 0, "clamped at 0");
  eq(tileOpacity(1000), 1, "clamped at 1");
});

test("ghostTiles: same tile count, re-binned onto the stepDeg grid, pct preserved", () => {
  const tiles = binTiles([{ alt: 40, az: 270, pct: 65 }], 6);
  const ghost = ghostTiles(tiles, 30, { baseKm: 2.2, windKmh: 12, windTowardDeg: 45 }, 6);
  eq(ghost.length, tiles.length);
  eq(ghost[0].pct, 65, "cloud percent carries through unchanged");
  ok(ghost[0].alt0 % 6 === 0, "ghost tile stays snapped to the 6deg grid");
  ok(((ghost[0].az0 % 6) + 6) % 6 === 0, "ghost tile stays snapped to the 6deg grid");
});

console.log(`${passed} passed, ${failed} failed`);
if (failed) {
  for (const f of failures) console.error(f);
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
export { passed, failed };
export const total = passed + failed;
