// mercator.test.ts — tile math round-trips, destPoint golden vectors, pierce
// distances (zenith / 45° / 20° / clamps) — weather spec §11/§14.
// Run with:  npx tsx src/lib/__tests__/mercator.test.ts   (from ui/)

import {
  CLOUD_DECKS_KM, clampLat, clampZoom, destPoint, latToTileY, lonToTileX,
  pierceDistanceKm, tileXToLon, tileYToLat,
} from "../mercator";

let passed = 0;
let failed = 0;
const failures: string[] = [];

function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) {
    failed++; failures.push(`x ${name}: ${(e as Error).message}`);
  }
}
function assert(cond: boolean, msg: string): void {
  if (!cond) throw new Error(msg);
}
function near(a: number, b: number, tol: number, msg: string): void {
  if (Math.abs(a - b) > tol) throw new Error(`${msg}: ${a} !~ ${b} (tol ${tol})`);
}

test("tile x/y round-trips at z=7", () => {
  near(tileXToLon(lonToTileX(-118.1, 7), 7), -118.1, 1e-9, "lon round-trip");
  near(tileYToLat(latToTileY(34.2, 7), 7), 34.2, 1e-9, "lat round-trip");
});

test("known anchor: lon 0 / lat 0 land mid-grid at z=1", () => {
  near(lonToTileX(0, 1), 1, 1e-12, "lon 0 @ z1");
  near(latToTileY(0, 1), 1, 1e-12, "lat 0 @ z1");
});

test("clampZoom (server range 3-11) + mercator clampLat", () => {
  assert(clampZoom(1) === 3 && clampZoom(15) === 11 && clampZoom(7.4) === 7,
    "zoom clamp/round");
  near(clampLat(89), 85.0511, 1e-9, "lat clamp");
  near(clampLat(-89), -85.0511, 1e-9, "lat clamp south");
});

test("destPoint bearing 0/90/180/270 sanity at mid-latitude", () => {
  const p0 = destPoint(34.2, -118.1, 0, 100); // due north
  near(p0.lat, 34.2 + 100 / 111.195, 0.01, "north dlat ~0.9deg/100km");
  near(p0.lon, -118.1, 1e-6, "north lon unchanged");
  const p90 = destPoint(34.2, -118.1, 90, 100); // due east
  near(
    p90.lon,
    -118.1 + 100 / (111.195 * Math.cos((34.2 * Math.PI) / 180)),
    0.02,
    "east dlon scaled by cos(lat)",
  );
  assert(p90.lat < 34.21 && p90.lat > 34.1, "east lat ~unchanged");
  const p180 = destPoint(34.2, -118.1, 180, 100);
  assert(p180.lat < 34.2, "south decreases lat");
  const p270 = destPoint(34.2, -118.1, 270, 100);
  assert(p270.lon < -118.1, "west decreases lon");
});

test("pierce distances: zenith -> ~0; 45deg/9km -> 9; 20deg/9km -> ~24.7", () => {
  const z = pierceDistanceKm(90, CLOUD_DECKS_KM.high);
  assert(z !== null && Math.abs(z) < 1e-9, `zenith ${z}`);
  near(pierceDistanceKm(45, 9)!, 9, 1e-9, "45deg high deck = 9 km");
  near(pierceDistanceKm(20, 9)!, 24.727, 0.01, "20deg high deck ~24.7 km");
});

test("pierce clamps: below 3deg altitude and beyond 150 km hidden", () => {
  assert(pierceDistanceKm(2.9, 2) === null, "below 3deg hidden");
  assert(pierceDistanceKm(3, 9) === null, "high deck at 3deg (~171 km) hidden");
  const low = pierceDistanceKm(3, 2);
  assert(low !== null && low < 150, "low deck at 3deg still visible");
  assert(pierceDistanceKm(Number.NaN, 9) === null, "NaN altitude hidden");
});

console.log(`mercator.test: ${passed} passed, ${failed} failed`);
if (failed > 0) {
  for (const f of failures) console.error(f);
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
