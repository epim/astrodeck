// skyRegion.test.ts — the rule that decides whether the Atlas hits the network.
//
//   Run directly:  npx tsx src/lib/__tests__/skyRegion.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// This is the whole performance argument for fetching the catalogue instead of
// shipping it. A per-view query would be a request on every pointer move; a
// per-REGION query is a request when the view leaves the circle it already
// holds. The difference is entirely in `regionNeedsFetch`, and if it ever
// starts saying "yes" too often NOTHING about the picture would look wrong
// while the rig was being hammered — which is exactly why it is a pure
// function with its own test rather than a condition inside an effect.

/* eslint-disable @typescript-eslint/no-explicit-any */

// lib/base.ts reads window.location at module scope to derive the relay mount
// prefix, so the smallest possible window has to exist before the import.
(globalThis as any).window = { location: { pathname: "/" } };

const {
  DRIFT_FRAC, EPHEMERAL_TTL_MS, RADIUS_FACTOR, ZOOM_FACTOR,
  regionNeedsFetch, regionUrl,
} = await import("../skyRegion");
type CachedRegion = import("../skyRegion").CachedRegion;

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

const T0 = 1_786_255_200_000;

function cached(over: Partial<CachedRegion> = {}): CachedRegion {
  return {
    ra_hours: 5.0,
    dec_deg: 10.0,
    radius_deg: 2 * RADIUS_FACTOR,
    fov_deg: 2,
    rows: [],
    truncated: false,
    degraded: false,
    notes: [],
    at: T0,
    ephemeral: false,
    ...over,
  };
}

test("nothing cached always fetches", () => {
  assert(regionNeedsFetch(null, { ra_hours: 5, dec_deg: 10 }, 2, T0),
    "the first view must fetch");
});

test("a small pan inside the cached circle costs no request", () => {
  const c = cached();
  const drift = DRIFT_FRAC * c.radius_deg * 0.5;      // half the allowance
  assert(!regionNeedsFetch(c, { ra_hours: 5, dec_deg: 10 + drift }, 2, T0),
    "a pan of half the hysteresis radius re-fetched — during a drag that is a " +
    "request per frame");
});

test("panning past the hysteresis radius fetches once", () => {
  const c = cached();
  const drift = DRIFT_FRAC * c.radius_deg * 1.2;
  assert(regionNeedsFetch(c, { ra_hours: 5, dec_deg: 10 + drift }, 2, T0),
    "the view left the cached circle's comfortable middle and kept drawing " +
    "labels from it — objects near the new edge simply would not exist");
});

test("a nudge of the zoom keeps the region; a real zoom change replaces it", () => {
  const c = cached();
  const here = { ra_hours: 5, dec_deg: 10 };
  assert(!regionNeedsFetch(c, here, 2 * 1.2, T0), "a 1.2x zoom refetched");
  assert(!regionNeedsFetch(c, here, 2 / 1.2, T0), "a 1/1.2x zoom refetched");
  assert(regionNeedsFetch(c, here, 2 * (ZOOM_FACTOR + 0.1), T0),
    "zooming out past the factor kept a region that no longer covers the view");
  assert(regionNeedsFetch(c, here, 2 / (ZOOM_FACTOR + 0.1), T0),
    "zooming IN past the factor kept a region selected for the wrong band — " +
    "the server's zoom band decides which objects are offered at all, so the " +
    "rows would be the wide-field selection at close range");
});

test("a region of fixed stars and galaxies never expires", () => {
  const c = cached({ ephemeral: false });
  assert(!regionNeedsFetch(c, { ra_hours: 5, dec_deg: 10 }, 2, T0 + 86_400_000),
    "a day-old region of J2000 coordinates was refetched — those positions " +
    "were true in 2000 and will be true in 2050");
});

test("a region holding the Moon or a planet ages out", () => {
  const c = cached({ ephemeral: true });
  const here = { ra_hours: 5, dec_deg: 10 };
  assert(!regionNeedsFetch(c, here, 2, T0 + EPHEMERAL_TTL_MS / 2),
    "an ephemeris a minute old was thrown away — the Moon moves 0.5' in that time");
  assert(regionNeedsFetch(c, here, 2, T0 + EPHEMERAL_TTL_MS + 1),
    "a stale planet position was kept. Fixed objects are where the catalogue " +
    "says forever; a planet is not, and a marker that stops tracking it is a " +
    "confident drawing of the wrong place.");
});

test("the request asks for a circle wider than the view, and says which view it is for", () => {
  const url = regionUrl({ ra_hours: 5.25, dec_deg: -10.5 }, 2.5, 80);
  const q = new URLSearchParams(url.split("?")[1]);
  assert(Number(q.get("ra_hours")) === 5.25, "ra");
  assert(Number(q.get("dec_deg")) === -10.5, "dec");
  assert(Math.abs(Number(q.get("radius_deg")) - 2.5 * RADIUS_FACTOR) < 1e-6,
    `radius ${q.get("radius_deg")} is not ${RADIUS_FACTOR}x the view width — ` +
    "the whole point is to hold more sky than is on screen");
  assert(Number(q.get("radius_deg")) > 2.5 / 2,
    "the fetched circle is smaller than the visible one: the corners of the " +
    "canvas would never have labels");
  assert(Number(q.get("fov_deg")) === 2.5,
    "fov_deg is what selects the server's zoom band; without it the server " +
    "would default to the circle's diameter and offer the wrong selection");
});

test("a pan past 24h or over the pole is normalised, not turned into a 422", () => {
  const wrapped = new URLSearchParams(
    regionUrl({ ra_hours: 24.2, dec_deg: 10 }, 2, 80).split("?")[1]);
  assert(Math.abs(Number(wrapped.get("ra_hours")) - 0.2) < 1e-6,
    `RA 24.2h went to the server as ${wrapped.get("ra_hours")}. The route `
    + `validates 0 <= ra < 24 and 422s outside it, so the sky would simply `
    + `stop being named the moment a pan crossed midnight.`);
  const under = new URLSearchParams(
    regionUrl({ ra_hours: -0.5, dec_deg: 10 }, 2, 80).split("?")[1]);
  assert(Math.abs(Number(under.get("ra_hours")) - 23.5) < 1e-6,
    `RA -0.5h went to the server as ${under.get("ra_hours")}`);
  const pole = new URLSearchParams(
    regionUrl({ ra_hours: 5, dec_deg: 95 }, 2, 80).split("?")[1]);
  assert(Number(pole.get("dec_deg")) === 90,
    `dec 95 went to the server as ${pole.get("dec_deg")}`);
});

const total = passed + failed;
console.log(`skyRegion.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
export default result;
