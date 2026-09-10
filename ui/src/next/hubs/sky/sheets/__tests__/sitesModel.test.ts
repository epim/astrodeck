// sitesModel.test.ts - pure tests for the Sites sheet's distance/coordinate/
// horizon-summary helpers (T-SKY-4 plan G, siteModel.test.ts renamed
// `sitesModel.test.ts` to match this task's file-prefix rule).
//
//   Run directly:  npx tsx src/next/hubs/sky/sheets/__tests__/sitesModel.test.ts
//   Also run by `npm test` and type-checked by `tsc -b`.

import {
  applyToastText, coordLine, distanceMeters, distanceTone, fmtDist,
  horizonIsOpen, horizonSummaryLine, isActiveLocation, nearestName,
  sortByDistance,
} from "../sitesModel";
import type { SavedLocation } from "../../../../../types";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg} (expected ${String(want)}, got ${String(got)})`);
}

const loc = (over: Partial<SavedLocation>): SavedLocation => ({
  id: "id", name: "Site", latitude: 0, longitude: 0, elevation_m: 0,
  horizon_min_deg: null, horizon_points: null,
  created_ts: 0, updated_ts: 0,
  ...over,
});

// ------------------------------------------------------------- distance sort
test("distance sort puts a 6 m site above a 47 km one", () => {
  // Seattle-ish fix; one location a few metres away, one ~47 km away.
  const near = loc({ id: "near", name: "Near", latitude: 47.6062, longitude: -122.3321 });
  const far = loc({ id: "far", name: "Far", latitude: 47.9, longitude: -122.0 });
  const fix = { lat: 47.60625, lon: -122.33205, accuracyM: 5 };
  const d6 = distanceMeters(fix.lat, fix.lon, near.latitude, near.longitude);
  assert(d6 < 10, `fixture is not actually ~6 m away (${d6.toFixed(1)} m) - the test proves nothing`);
  const sorted = sortByDistance([far, near], fix);
  eq(sorted[0].id, "near", "the nearer site did not sort first");
  eq(sorted[1].id, "far", "the farther site did not sort second");
});

test("with no fix, server order survives unchanged (never re-sorted by name)", () => {
  const b = loc({ id: "b", name: "B" });
  const a = loc({ id: "a", name: "A" });
  const sorted = sortByDistance([b, a], null);
  eq(sorted[0].id, "b", "server order was reshuffled with no phone fix");
  eq(sorted[1].id, "a", "server order was reshuffled with no phone fix");
  assert(sorted.every((s) => s.distanceM === null), "a distance appeared with no fix to compute it from");
});

test("nearestName finds the closest of several locations", () => {
  const a = loc({ id: "a", name: "A", latitude: 10, longitude: 10 });
  const b = loc({ id: "b", name: "B", latitude: 0.001, longitude: 0.001 });
  eq(nearestName([a, b], { lat: 0, lon: 0, accuracyM: 5 }), "B", "the wrong nearest name won");
});

// ------------------------------------------------------------------ fmtDist
test("fmtDist switches unit at 1000 m", () => {
  eq(fmtDist(999), "999 m", "just under the boundary should still read in metres");
  eq(fmtDist(1000), "1.0 km", "the boundary itself should already read in km");
  eq(fmtDist(1500), "1.5 km", "km form is not one decimal place");
});

test("distanceTone is good under 60 m and dim otherwise", () => {
  eq(distanceTone(59), "good", "59 m should read close (green)");
  eq(distanceTone(60), "dim", "60 m is the boundary and should NOT be green");
  eq(distanceTone(null), "dim", "no distance should never read as close");
});

// --------------------------------------------------------------- coordLine
test("coordLine shows numbers only with view.site_precise", () => {
  const l = loc({ latitude: 47.610812, longitude: -122.332071, elevation_m: 52 });
  const shown = coordLine(l, true);
  assert(/47\.6108/.test(shown), `precise coords should carry the numbers: "${shown}"`);
  const hidden = coordLine(l, false);
  eq(hidden, "site set · precise location hidden for this role", "the derived line is not verbatim");
  assert(!/\d/.test(hidden), "a digit leaked into the derived-privacy line - that IS a coordinate");
});

// ---------------------------------------------------------- horizon summary
test("horizonSummaryLine: open horizon reads as open, not zero points", () => {
  eq(horizonSummaryLine(null), "horizon: open · nothing marked yet", "null points should read open");
  eq(horizonSummaryLine([]), "horizon: open · nothing marked yet", "empty points should read open");
  assert(horizonIsOpen(null) && horizonIsOpen([]), "horizonIsOpen disagrees with the summary line");
});

test("horizonSummaryLine: a drawn horizon states the count and the peak", () => {
  const line = horizonSummaryLine([[10, 5], [90, 38], [200, 12]]);
  eq(line, "horizon: 3 points · up to 38°", "the count/peak summary is wrong");
  assert(!horizonIsOpen([[10, 5]]), "one point should not read as open");
});

// --------------------------------------------------------------- isActive
test("isActiveLocation matches the persisted site within 1e-6 deg", () => {
  const l = loc({ name: "Ridge", latitude: 40.5, longitude: -70.25, elevation_m: 100 });
  const site = { is_default: false, name: "Ridge", latitude: 40.5, longitude: -70.25, elevation_m: 100 };
  assert(isActiveLocation(l, site), "an exact coordinate/name match was not recognised as active");
  assert(!isActiveLocation(l, { ...site, latitude: 40.6 }), "a different latitude was still read as active");
});

test("isActiveLocation never claims a match with no precise coordinates to compare", () => {
  const l = loc({ name: "Ridge", latitude: 40.5, longitude: -70.25, elevation_m: 100 });
  // A stripped site (no view.site_precise): coordinates are simply absent.
  const stripped = { is_default: false, name: undefined, latitude: undefined, longitude: undefined, elevation_m: undefined };
  assert(!isActiveLocation(l, stripped), "a stripped site (no coordinates) was read as matching a saved location");
  assert(!isActiveLocation(l, { is_default: true }), "the unset default site was read as matching a saved location");
});

// ------------------------------------------------------------- apply toast
test("applyToastText names where the horizon is armed", () => {
  eq(
    applyToastText("Back Lawn", true),
    "Back Lawn selected - its horizon is on the finder and the dome.",
    "the drawn-horizon toast text is wrong",
  );
  eq(
    applyToastText("Back Lawn", false),
    "Back Lawn selected - open horizon: capture a photosphere or draw one with EDIT.",
    "the open-horizon toast text is wrong",
  );
});

const total = passed + failed;
console.log(`sitesModel.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
