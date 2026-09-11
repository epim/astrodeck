// domeOverlay.test.ts - the four things about the dome overlay that a rendered
// picture cannot tell you, because every one of them is wrong in a way that
// still looks right.
//
//   Run directly:  npx tsx src/next/hubs/weather/__tests__/domeOverlay.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc --noEmit`.
//
// WHAT THIS GUARDS:
//
//  1. THE WIND CONVENTION. `now.wind_dir_deg` is METEOROLOGICAL - where the
//     wind comes FROM. Every ghost tile is advected along `wind.towardDeg`,
//     which `windSummary` turned around. Feed the raw bearing through instead
//     and the dashed outlines land 180 degrees away, beside real cloud, on the
//     side the cloud is LEAVING - a picture nobody can tell is backwards
//     without knowing the answer already. So the test asserts a QUADRANT for
//     each of the two bearings, not a shape.
//  2. A GAP IS NOT A ZERO. `null * 100` is 0 in JavaScript, so dropping the
//     null-cell filter does not throw and does not print NaN: it advects
//     patches of clear sky nobody measured, and they read as holes in the
//     cloud. The count of ghosts and the absence of a 0 % ghost are what
//     separate the two.
//  3. THE HORIZON RING CLOSES. The fill is walked az 0..360 in steps, so a
//     lookup that did not wrap would put a seam at north - a wedge of missing
//     tree line exactly where the polyline's two ends meet.
//  4. NO GHOST WITHOUT A MEASUREMENT. A stale granule and a feed with a
//     direction but no speed each produce NO ghosts. The alternative in both
//     cases is a forecast drawn from something nobody measured, which is the
//     failure the whole overlay was held back for.

/* eslint-disable @typescript-eslint/no-explicit-any */

// `domeOverlay.tsx` reaches the primitives barrel and `api.ts` behind it, which
// reads `window.location.pathname` at module load. Stub before importing, per
// the store-touching test convention in shell-and-tests.md section 4.
const g = globalThis as any;
if (typeof g.window === "undefined") {
  g.window = {
    location: { pathname: "/", protocol: "http:", host: "test", hash: "" },
    setTimeout: globalThis.setTimeout.bind(globalThis),
    clearTimeout: globalThis.clearTimeout.bind(globalThis),
    addEventListener() {},
    removeEventListener() {},
    matchMedia: () => ({
      matches: false, addEventListener() {}, removeEventListener() {},
      addListener() {}, removeListener() {},
    }),
  };
}
if (typeof g.location === "undefined") g.location = g.window.location;

const { ghostsFor, pathRuns, windSummary, GHOST_STEP_DEG, HORIZON_STEP_DEG } =
  await import("../dome/domeOverlay");
const { horizonAltAt } = await import("../../../lib/horizonModel");
type WeatherNow = import("../../../../types").WeatherNow;
type CloudmapDome = import("../../../../api/cloudmap").CloudmapDome;
type TrackSample = import("../../sky/finder/track").TrackSample;
type TrackState = import("../../sky/finder/track").TrackState;

let passed = 0;
let failed = 0;
const failures: string[] = [];

function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg = ""): void {
  if (got !== want) throw new Error(`${msg} expected ${String(want)}, got ${String(got)}`);
}

/** A `now` block with the feed's own METEOROLOGICAL bearing. 225 = the wind
 *  comes FROM the south-west, so the cloud drifts TOWARD the north-east (45). */
function now(over: Partial<WeatherNow> = {}): WeatherNow {
  return {
    ts: "2026-09-10T04:00:00Z",
    temp_c: 11.2,
    dewpoint_c: 7.0,
    humidity_pct: 68,
    wind_kmh: 20,
    wind_dir_deg: 225,
    gust_kmh: 26,
    cloud_base_m: 2200,
    ...over,
  };
}

/** A dome payload whose cells land where the assertions expect them.
 *
 *  `alt_start` 63 with `alt_step` 6 puts the sample at altitude 63, which bins
 *  to the 60-degree tile; `az_step` 10 puts column j at azimuth j*10. */
function dome(rows: (number | null)[][]): CloudmapDome {
  return {
    enabled: true,
    observed_at: "2026-09-10T03:55:00Z",
    stale: false,
    rows,
    alt_start: 63,
    alt_step: 6,
    az_step: 10,
  };
}

const quadrant = (az: number): string => {
  const a = ((az % 360) + 360) % 360;
  if (a < 90) return "NE";
  if (a < 180) return "SE";
  if (a < 270) return "SW";
  return "NW";
};

// ------------------------------------------------------ 1. which way it drifts

test("a ghost drifts along the TOWARD bearing, into the north-east quadrant", () => {
  const wind = windSummary(now());
  assert(wind != null, "precondition: windSummary read the fixture");
  eq(wind!.towardDeg, 45, "225 FROM is 45 TOWARD:");
  eq(wind!.kmh, 20, "wind speed:");

  const ghosts = ghostsFor(dome([[0.9]]), false, wind);
  eq(ghosts.length, 1, "one cell in, one ghost out:");
  const az = ghosts[0].az0;
  eq(quadrant(az), "NE",
    `a cell due north advected toward 45 must land north-east, got az0 ${az}:`);
  assert(az > 0 && az < 90, `az0 ${az} is not strictly inside the NE quadrant`);
  // The tile stays on the 6-degree grid it came in on, or it is not the same
  // layer as the cloud underneath it.
  eq(az % GHOST_STEP_DEG, 0, `ghost az0 ${az} is off the ${GHOST_STEP_DEG} deg grid:`);
});

test("the FROM bearing fed through unturned puts the ghost in the opposite quadrant", () => {
  // This is the exact defect the turnaround exists to prevent, constructed:
  // a WindSummary carrying the raw `wind_dir_deg` as if it were a drift.
  const wind = windSummary(now());
  const unturned = { ...wind!, towardDeg: now().wind_dir_deg as number };
  eq(unturned.towardDeg, 225, "the un-turned bearing:");

  const right = ghostsFor(dome([[0.9]]), false, wind);
  const wrong = ghostsFor(dome([[0.9]]), false, unturned);
  eq(wrong.length, 1, "one ghost either way:");
  eq(quadrant(wrong[0].az0), "SW",
    `the un-turned bearing must land south-west, got az0 ${wrong[0].az0}:`);
  assert(quadrant(right[0].az0) !== quadrant(wrong[0].az0),
    "the two bearings produced ghosts in the SAME quadrant, so the turnaround "
    + "is not reaching the advection at all");
});

// ------------------------------------------------------------ 2. gaps vs zeros

test("null cells are dropped, not folded to zero cloud", () => {
  const wind = windSummary(now());
  // Three cells, one reading. `null * 100` is 0 in JavaScript, so a missing
  // filter yields three ghosts of which two claim a measured clear sky.
  const ghosts = ghostsFor(dome([[null, null, 0.9]]), false, wind);
  eq(ghosts.length, 1, "only the cell with a reading may become a ghost:");
  eq(ghosts[0].pct, 90, "the surviving ghost carries its own percentage:");
  assert(!ghosts.some((t) => t.pct === 0),
    "a 0 % ghost reached the picture, which is a gap drawn as measured clear sky");
});

// --------------------------------------------------------- 3. the ring closes

test("the horizon lookup closes the ring at north", () => {
  const points = [{ az: 0, alt: 12 }, { az: 180, alt: 30 }];
  const before = horizonAltAt(points, 359.9);
  const after = horizonAltAt(points, 0.1);
  assert(Number.isFinite(before) && Number.isFinite(after),
    `the lookup returned a non-number: ${before} / ${after}`);
  assert(Math.abs(before - after) < HORIZON_STEP_DEG,
    `a seam at north: h(359.9)=${before.toFixed(2)} vs h(0.1)=${after.toFixed(2)}, `
    + `which is more than the fill's own ${HORIZON_STEP_DEG} deg step`);
});

// ------------------------------------------- 4. no ghost without a measurement

test("a stale granule yields no ghosts", () => {
  const wind = windSummary(now());
  eq(ghostsFor(dome([[0.9]]), false, wind).length, 1, "the positive control:");
  eq(ghostsFor(dome([[0.9]]), true, wind).length, 0,
    "a stale reading was advected into a forecast:");
});

test("a wind with a direction but no speed yields no ghosts", () => {
  const wind = windSummary(now({ wind_kmh: null }));
  assert(wind != null, "a direction alone still makes a summary (the arrow needs it)");
  eq(wind!.kmh, null, "the feed carries no speed:");
  eq(ghostsFor(dome([[0.9]]), false, wind).length, 0,
    "ghosts were drawn for a wind speed nobody measured:");
});

test("no grid and no wind at all each yield no ghosts", () => {
  const wind = windSummary(now());
  eq(ghostsFor(null, false, wind).length, 0, "ghosts without a grid:");
  eq(ghostsFor(dome([[0.9]]), false, null).length, 0, "ghosts without a wind:");
});

// ------------------------------------------------- 5. what breaks the path line

/** The geometry a canvas of this size publishes. Only the numbers matter here;
 *  the point is that the runs are cut by the SAME projection the dome painted
 *  with, not by a screen-space guess. */
const GEOM = { cssW: 320, cssH: 280, cx: 160, cy: 140, r: 100, tiltDeg: 32, yawDeg: 0 };
const sample = (alt: number, az: number, st: TrackState): TrackSample =>
  ({ t: 0, alt, az, st });

test("a stretch below the horizon breaks the line rather than colouring it", () => {
  const runs = pathRuns(GEOM, [
    sample(40, 180, "ok"), sample(41, 182, "ok"),
    sample(-5, 184, "below"),
    sample(42, 186, "ok"),
  ]);
  eq(runs.length, 2, "the `below` sample must cut the polyline in two:");
  assert(runs.every((r) => r.length > 0), "an empty run reached the renderer");
});

test("a change of state starts a new run, so the colour can change", () => {
  const runs = pathRuns(GEOM, [sample(40, 180, "ok"), sample(20, 182, "floor")]);
  eq(runs.length, 2, "ok then floor is two runs:");
  eq(runs[0][0].st, "ok", "first run:");
  eq(runs[1][0].st, "floor", "second run:");
});

test("a sample round the back of the dome is dropped and splits the line", () => {
  // The camera sits due south, so azimuth 0 at 40 degrees is on the FAR side -
  // a segment drawn through it would appear to cross the sky the short way.
  const runs = pathRuns(GEOM, [
    sample(40, 180, "ok"), sample(40, 0, "ok"), sample(40, 175, "ok"),
  ]);
  eq(runs.length, 2, "the far-side sample must split the run:");
  eq(runs[0].length + runs[1].length, 2, "the far-side sample must not be drawn:");
});

console.log(`domeOverlay.test: ${passed}/${passed + failed} passed`);
for (const f of failures) console.log("  " + f);
if (failed) {
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}

const total = passed + failed;
export default { passed, failed, total };
export { passed, failed, total };
