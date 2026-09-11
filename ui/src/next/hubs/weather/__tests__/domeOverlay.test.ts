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

const {
  ghostsFor, legendKeys, pathRuns, placeTrack, windSummary,
  GHOST_STEP_DEG, HORIZON_STEP_DEG,
} = await import("../dome/domeOverlay");
const { horizonAltAt } = await import("../../../lib/horizonModel");
const {
  buildDomeTracks, domeTrackLabel, pointLabel, trackSamplesFor,
  MAX_DOME_TRACKS, TRACK_COLORS,
} = await import("../../sky/finder/track");
type DomeTrack = import("../../sky/finder/track").DomeTrack;
type DomeTrackContext = import("../../sky/finder/track").DomeTrackContext;
type DrawnMarks = import("../dome/domeOverlay").DrawnMarks;
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

// ================================================ 6. many arcs, not one path
//
// WHAT THIS GUARDS. The dome used to take ONE track and one name. It now takes
// a list, and four things about that list are wrong in ways a rendered dome
// still looks perfectly fine with:
//
//   * AN ARC PER SUBJECT, IN ITS OWN COLOUR. Collapsing the list to its first
//     entry, or colouring every arc from the first one's state, draws a picture
//     that is right about one object and confidently wrong about five.
//   * A POINT HAS NO NAME. The whole reason the aimed arc exists is that the
//     reader is looking at sky nothing is catalogued in; a label reading
//     "target" there says nothing the arc does not, and one reading a nearby
//     object's name is a lie about what is being tracked.
//   * THE LEGEND COUNTS WHAT IS DRAWN. An arc with no samples, or one that
//     falls entirely round the back of the dome, contributes nothing - and a
//     legend still reading "3 tracks to dawn" over two arcs sends the reader
//     hunting for a third.
//   * THE CAP KEEPS THE BRIGHT ONE. A cut that took the first six of a list the
//     bright subject happened to be seventh in would drop the only arc the
//     reader asked for and keep six they did not.

/** A context with a real site and a real night, so the walks below are the ones
 *  the finder would make rather than a fixture's idea of one. */
const CTX: DomeTrackContext = {
  latDeg: 47.61,
  lonDeg: -122.33,
  nowMs: Date.UTC(2026, 8, 10, 6, 0, 0),
  hoursToDawn: 4,
  horizon: [],
  horizonMinDeg: 0,
  maskOn: true,
  holdAt: () => false,
};

/** A hand-built arc, so each run's STATE is the fixture's and not the
 *  geometry's - the assertion is about colour reaching the picture per track.
 *  Due south and well up is the near half of the dome at yaw 0 (see GEOM). */
function arc(id: string, st: TrackState, over: Partial<DomeTrack> = {}): DomeTrack {
  return {
    id,
    label: id.toUpperCase(),
    bright: false,
    point: null,
    samples: [sample(50, 178, st), sample(51, 182, st), sample(52, 186, st)],
    ...over,
  };
}

test("every track handed over becomes its own arc, in its own colour", () => {
  const tracks = [arc("a", "ok"), arc("b", "hold"), arc("c", "mask")];
  const placed = tracks.map((t) => placeTrack(GEOM, t));
  eq(placed.filter((p) => p !== null).length, 3, "three arcs in, three placed:");

  const tones = placed.map((p) => TRACK_COLORS[p!.runs[0][0].st]);
  eq(tones[0], TRACK_COLORS.ok, "the clear track's colour:");
  eq(tones[1], TRACK_COLORS.hold, "the clouded track's colour:");
  eq(tones[2], TRACK_COLORS.mask, "the obstructed track's colour:");
  assert(new Set(tones).size === 3,
    "two arcs came out the same colour, so the list is being coloured from one "
    + "subject rather than per track");
  for (const p of placed) {
    assert(p!.now !== null, "an arc that is up and facing drew no current-position dot");
  }
  eq(placed[2]!.now!.st, "mask", "the dot takes its own sample's state:");
});

test("a subject with no name is labelled with its coordinates", () => {
  const samples = trackSamplesFor({ ra_hours: 5.5, dec_deg: 12.25 }, CTX);
  assert(samples.length > 0, "precondition: the walk produced samples");

  const [named] = buildDomeTracks(
    [{ id: "m31", name: "M31", ra_hours: 5.5, dec_deg: 12.25, bright: true }], CTX);
  const [bare] = buildDomeTracks(
    [{ id: "aim", name: null, ra_hours: 5.5, dec_deg: 12.25, bright: true }], CTX);

  eq(named.label, "M31", "a catalogued object is called by its name:");
  eq(named.point, null, "a named object carries a bare point it should not have:");
  eq(bare.label, pointLabel(5.5, 12.25), "an unnamed point is called by its coordinates:");
  assert(/^[0-9]{2}h[0-9]{2}m [+-][0-9]{2}/.test(bare.label),
    `the aimed label is not a coordinate pair: "${bare.label}"`);
  assert(bare.point !== null && bare.point.ra_hours === 5.5,
    "the aimed arc dropped the coordinates, so nothing can link back to the point");
  eq(domeTrackLabel({ id: "x", name: null, ra_hours: 5.5, dec_deg: 12.25 }), bare.label,
    "the label helper and the builder disagree:");
});

test("a track with no samples draws nothing at all", () => {
  eq(placeTrack(GEOM, arc("empty", "ok", { samples: [] })), null,
    "an empty arc was placed anyway:");
  // And the builder never makes one: a subject whose night is already over.
  const over = buildDomeTracks(
    [{ id: "m31", name: "M31", ra_hours: 5.5, dec_deg: 12.25 }],
    { ...CTX, hoursToDawn: 0 });
  eq(over.length, 0, "a subject with no walk became a track anyway:");
});

test("an arc entirely round the back of the dome is not placed", () => {
  // Due north at 40 degrees is the FAR side at this tilt (see the pathRuns
  // tests above), so nothing of this walk is visible.
  const back = arc("far", "ok", {
    samples: [sample(40, 0, "ok"), sample(41, 4, "ok"), sample(42, 356, "ok")],
  });
  eq(placeTrack(GEOM, back), null, "an invisible arc reached the picture:");
});

test("the dome is capped, and the bright subject survives the cut", () => {
  const many = Array.from({ length: 9 }, (_, i) => ({
    id: `t${i}`, name: `T${i}`, ra_hours: (i * 2.5) % 24, dec_deg: 20 + i,
    bright: i === 0,
  }));
  const out = buildDomeTracks(many, CTX);
  assert(out.length <= MAX_DOME_TRACKS,
    `${out.length} arcs on one dome, past the ${MAX_DOME_TRACKS} cap`);
  assert(out.length > 1, "the cap swallowed the whole list");
  eq(out[0].id, "t0", "the bright subject must survive the cut, first:");
  eq(out[0].bright, true, "the first subject lost its brightness:");
});

// -------------------------------------------------- the legend names the arcs

const MARKS = (over: Partial<DrawnMarks> = {}): DrawnMarks => ({
  horizon: false, ghosts: false, tracks: 0, states: [], aimed: null, ...over,
});
const labels = (d: DrawnMarks): string[] => legendKeys(false, d).map((k) => k.label);

test("the legend counts the arcs that were drawn, and omits the entry at zero", () => {
  assert(!labels(MARKS()).some((l) => /track/.test(l)),
    "the legend names tracks over a dome with none on it");
  const three = labels(MARKS({ tracks: 3, states: ["ok"] }));
  assert(three.includes("3 tracks to dawn"),
    `the legend does not count the arcs: ${three.join(" | ")}`);
  const one = labels(MARKS({ tracks: 1, states: ["ok"] }));
  assert(one.includes("1 track to dawn"),
    `one arc is not "1 track": ${one.join(" | ")}`);
});

test("the legend names only the states an arc is actually drawn in", () => {
  const clear = labels(MARKS({ tracks: 2, states: ["ok"] }));
  assert(!clear.some((l) => /cloud hold/.test(l)),
    "the legend names a forecast hold on a dome with no held stretch on it");
  assert(!clear.some((l) => /behind your horizon/.test(l)),
    "the legend names a masked stretch that is not drawn");
  assert(!clear.some((l) => /degree floor/.test(l)),
    "the legend names a floor stretch that is not drawn");

  const all = labels(MARKS({ tracks: 2, states: ["ok", "hold", "mask", "floor"] }));
  assert(all.some((l) => /forecast cloud hold/.test(l)), `no hold entry: ${all.join(" | ")}`);
  assert(all.some((l) => /behind your horizon profile/.test(l)),
    `no mask entry: ${all.join(" | ")}`);
  assert(all.some((l) => /degree floor/.test(l)), `no floor entry: ${all.join(" | ")}`);
});

test("an aimed point puts its coordinates in the legend, and only then", () => {
  const none = labels(MARKS({ tracks: 2, states: ["ok"] }));
  assert(!none.some((l) => /aimed at/.test(l)),
    "the legend claims an aimed point on a dome that has none");
  const aimed = labels(MARKS({ tracks: 2, states: ["ok"], aimed: "05h30m +12d15m" }));
  assert(aimed.includes("aimed at 05h30m +12d15m"),
    `the aimed point is not named in the legend: ${aimed.join(" | ")}`);
});

console.log(`domeOverlay.test: ${passed}/${passed + failed} passed`);
for (const f of failures) console.log("  " + f);
if (failed) {
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}

const total = passed + failed;
export default { passed, failed, total };
export { passed, failed, total };
