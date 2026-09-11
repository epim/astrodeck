// skyModel.test.ts - the finder's math, without a DOM.
//
//   Run directly:  npx tsx src/next/hubs/sky/finder/__tests__/skyModel.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT IS WORTH A TEST HERE. Every one of these is a rule whose failure looks
// entirely plausible on screen, which is exactly why the screen cannot be the
// check:
//
//   * the inverse coordinate transform - a sign error puts IMAGE THIS PATCH on
//     the wrong side of the meridian and the frames are filed under a position
//     the telescope never visited;
//   * the track classifier's ORDER - swap floor and mask and a low target reads
//     "behind your trees" at a site with no trees;
//   * the lock radius - too large and the reticle claims a target it is nowhere
//     near, which is the sabotage this file is checked against;
//   * a null cloud cell - folding it to 0 paints a clear patch of sky nobody
//     measured;
//   * the wind convention - meteorological direction is where the wind comes
//     FROM, and drawing it as a toward-bearing points every arrow backwards with
//     no visual tell at all;
//   * the solar-system row shape - `id` is the label and `name` is a whole
//     sentence, the opposite way round from a DSO, so a marker pill that reads
//     `name` prints a paragraph.
//
// Convention: inline test()/eq() helpers, printed tally plus the
// { passed, failed, total } export (shell-and-tests.md section 4).

import { altAzOf, lstHours } from "../../../../../lib/altaz";
import { D2R, eq as toEq, hz, raDecFromAltAz, raHmsStr, decDmsStr } from "../equatorial";
import {
  altLines,
  altStrOf,
  azStrOf,
  compassTicks,
  makeProjector,
  ppdFor,
  PPD,
} from "../projection";
import {
  classify,
  isObstructedAt,
  minutesAboveFloor,
  TRACK_COLORS,
  walkTrack,
  buildTrack,
  type TrackContext,
} from "../track";
import {
  cloudBlobLabels,
  cloudPctAt,
  cloudRects,
  domeToSamples,
  tilesFromDome,
} from "../clouds";
import { compassPoint, windArrows, windFrom, cloudBaseKm } from "../wind";
import {
  decorate,
  inReach,
  kindOf,
  mergeRows,
  narrowbandSlots,
  paletteFor,
  pickLock,
  type CatalogRowLike,
  type Marker,
  type SkyTarget,
} from "../targets";
import { panView } from "../gestures";
import { rankTargets, windowLabel } from "../../../../lib/reach";
import type { CloudmapDome } from "../../../../../api/cloudmap";

// ------------------------------------------------------------------ harness
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
function near(got: number, want: number, tol: number, msg: string): void {
  if (!(Math.abs(got - want) <= tol)) {
    throw new Error(`${msg} (expected ${want} +/- ${tol}, got ${got})`);
  }
}

// ================================================== 1. the coordinate inverse

test("eq/hz round-trip alt-az -> RA/Dec -> alt-az at three latitudes", () => {
  const unix = 1_756_000_000; // a fixed instant; the transform is the thing under test
  for (const lat of [47.61, 0.0, -33.9]) {
    for (const [alt, az] of [[58, 64], [12, 300], [76, 181]] as [number, number][]) {
      const rd = raDecFromAltAz(alt, az, lat, -122.33, unix);
      const back = altAzOf(rd.ra_hours, rd.dec_deg, lat, -122.33, unix);
      near(back.altDeg, alt, 0.01, `alt round-trip at lat ${lat} from ${alt}/${az}`);
      // Azimuth wraps, so compare the folded difference rather than the values.
      const dAz = ((back.azDeg - az + 540) % 360) - 180;
      near(dAz, 0, 0.01, `az round-trip at lat ${lat} from ${alt}/${az}`);
    }
  }
});

test("eq/hz are inverses of each other", () => {
  const lat = 47.61;
  const { decRad, haRad } = toEq(58, 64, lat);
  const back = hz(decRad, haRad, lat);
  near(back.alt, 58, 1e-6, "hz(eq(alt)) altitude");
  near(((back.az - 64 + 540) % 360) - 180, 0, 1e-6, "hz(eq(az)) azimuth");
});

test("RA wraps into [0,24) rather than emitting 24.0", () => {
  // A position whose RA lands a hair under 24 h must not round up to 24: the
  // server's Target.ra_hours is Field(ge=0, lt=24) and 24.0 422s the whole plan.
  const unix = 1_756_000_000;
  for (let az = 0; az < 360; az += 7) {
    const rd = raDecFromAltAz(40, az, 47.61, -122.33, unix);
    assert(rd.ra_hours >= 0 && rd.ra_hours < 24, `ra out of range at az ${az}: ${rd.ra_hours}`);
  }
});

test("sexagesimal strings match the coordinates sheet's placeholders", () => {
  eq(raHmsStr(22.965), "22h 57m 54s", "RA string");
  eq(decDmsStr(62.6183), "+62° 37′ 06″", "Dec string");
  eq(decDmsStr(-5.5), "-05° 30′ 00″", "negative Dec string");
});

// ========================================================== 2. the projection

test("proj puts the centre azimuth at x = W/2 and the centre altitude at y = H/2", () => {
  const p = makeProjector(370, 64, 58);
  const at = p.proj(64, 58);
  near(at.x, 185, 1e-9, "centre x");
  near(at.y, 186, 1e-9, "centre y");
});

test("proj scales at 6.1 px per degree and wraps the short way round", () => {
  const p = makeProjector(370, 10, 40);
  near(p.proj(20, 40).x, 185 + 10 * PPD, 1e-6, "ten degrees east");
  // az 350 with the view at 10 is twenty degrees WEST, not 340 degrees east.
  near(p.proj(350, 40).x, 185 - 20 * PPD, 1e-6, "wrap across north");
});

test("a wider box shows the SAME field, larger", () => {
  near(ppdFor(370), 6.1, 1e-9, "phone scale");
  near(ppdFor(740), 12.2, 1e-9, "double-width scale");
  const wide = makeProjector(740, 64, 58);
  // 30 degrees of azimuth still reaches the edge of the box at either width.
  near(wide.proj(64 + 370 / 12.2, 58).x, 740, 1e-6, "half a field is still half a box");
});

test("unproj is the inverse of proj", () => {
  const p = makeProjector(370, 64, 58);
  const at = p.proj(80, 30);
  const back = p.unproj(at.x, at.y);
  near(((back.az - 80 + 540) % 360) - 180, 0, 1e-9, "az back");
  near(back.alt, 30, 1e-9, "alt back");
});

test("compass ticks label the cardinals and the 30s and nothing else", () => {
  const p = makeProjector(370, 90, 40);
  const ticks = compassTicks(p);
  const east = ticks.find((t) => Math.abs(t.x - 185) < 0.001);
  assert(east != null, "no tick at the centre azimuth");
  eq(east?.label, "E", "az 90 is east");
  eq(east?.h, 9, "a cardinal gets the tall tick");
  const sixty = ticks.find((t) => t.label === "060");
  assert(sixty != null, "az 60 lost its zero-padded label");
  assert(ticks.some((t) => t.label === "" && t.h === 4), "the 10s should be short and unlabelled");
});

test("the altitude grid stops at the ground and at the zenith", () => {
  const low = altLines(makeProjector(370, 0, 5));
  assert(low.every((l) => Number(l.label.replace("°", "")) >= 0), "drew a line below the horizon");
  const high = altLines(makeProjector(370, 0, 85));
  assert(high.every((l) => Number(l.label.replace("°", "")) <= 90), "drew a line above the zenith");
});

test("FRAME mode draws no compass and no grid", () => {
  const p = makeProjector(370, 64, 58, true);
  eq(p.pp, 44, "FRAME mode is 44 px per degree");
  eq(compassTicks(p, true).length, 0, "compass survived FRAME mode");
  eq(altLines(p, true).length, 0, "grid survived FRAME mode");
});

test("the readouts are zero-padded so the chip does not reflow", () => {
  eq(azStrOf(64), "064°", "azimuth padding");
  eq(azStrOf(359.6), "000°", "azimuth wrap at 360");
  eq(altStrOf(57.6), "58°", "altitude rounding");
});

// ====================================================== 3. the pan and clamps

test("dragging pans the sky one degree per 6.1 px and clamps the poles", () => {
  const start = { az: 64, alt: 58 };
  const moved = panView(start, 61, 0, PPD);
  near(moved.az, 54, 1e-6, "61 px is ten degrees of azimuth");
  eq(panView(start, 0, 10_000, PPD).alt, 89, "altitude clamps below the zenith");
  eq(panView(start, 0, -10_000, PPD).alt, -12, "altitude clamps below the horizon");
  near(panView({ az: 2, alt: 40 }, 61, 0, PPD).az, 352, 1e-6, "azimuth wraps past north");
});

// ==================================================== 4. the track classifier

const flatCtx = (over: Partial<TrackContext> = {}): TrackContext => ({
  latDeg: 47.61,
  hoursToDawn: 7,
  horizon: [],
  horizonMinDeg: 0,
  maskOn: true,
  holdAt: () => false,
  ...over,
});

test("the classifier returns below / floor / mask / hold / ok in that priority", () => {
  eq(classify(-5, 100, 0, flatCtx()), "below", "under the ground is below");
  eq(classify(15, 100, 0, flatCtx()), "floor", "under 25 degrees is the floor");
  eq(classify(60, 100, 0, flatCtx()), "ok", "clear and high is ok");
  // A polyline 40 degrees high at az 100 masks a target at 30.
  const masked = flatCtx({ horizon: [{ az: 0, alt: 40 }, { az: 200, alt: 40 }] });
  eq(classify(30, 100, 0, masked), "mask", "behind the drawn horizon is mask");
  eq(classify(60, 100, 0, flatCtx({ holdAt: (t) => t > 1 })), "ok", "no hold before the breach");
  eq(classify(60, 100, 2, flatCtx({ holdAt: (t) => t > 1 })), "hold", "inside the breach is hold");
  // Order matters: a target BOTH under the floor and behind the horizon reads
  // floor, because you can wait for it to climb and you cannot move the trees.
  eq(classify(15, 100, 0, masked), "floor", "floor outranks mask");
});

test("the horizon fallback is applied by the CALLER, not by a synthetic polyline", () => {
  // horizonAltAt([]) returns 0, so an empty polyline plus a 25 degree
  // horizon_min_deg must still obstruct a target at 15 degrees.
  const ctx = flatCtx({ horizon: [], horizonMinDeg: 25 });
  assert(isObstructedAt(15, 90, ctx), "the flat fallback did not obstruct");
  assert(!isObstructedAt(35, 90, ctx), "the flat fallback obstructed a high target");
});

test("the colours are design tokens, not hex literals", () => {
  eq(TRACK_COLORS.ok, "var(--accent)", "clear stretch");
  eq(TRACK_COLORS.hold, "var(--warn)", "cloud hold");
  eq(TRACK_COLORS.mask, "var(--bad)", "behind the horizon");
  eq(TRACK_COLORS.floor, "var(--text-faint)", "under the floor");
});

test("minutes above the floor counts hold and mask, and not floor or below", () => {
  const samples = [
    { t: 0, alt: 60, az: 100, st: "ok" as const },
    { t: 0.25, alt: 55, az: 105, st: "hold" as const },
    { t: 0.5, alt: 20, az: 110, st: "floor" as const },
    { t: 0.75, alt: -5, az: 115, st: "below" as const },
    { t: 1, alt: 40, az: 120, st: "mask" as const },
  ];
  eq(minutesAboveFloor(samples), 45, "three usable quarter-hours");
});

test("a below stretch BREAKS the polyline rather than drawing through the ground", () => {
  const p = makeProjector(370, 100, 40);
  const samples = [
    { t: 0, alt: 60, az: 100, st: "ok" as const },
    { t: 0.25, alt: 50, az: 102, st: "ok" as const },
    { t: 0.5, alt: -5, az: 104, st: "below" as const },
    { t: 0.75, alt: 40, az: 106, st: "ok" as const },
    { t: 1, alt: 45, az: 108, st: "ok" as const },
  ];
  const r = buildTrack(samples, p, Date.UTC(2026, 8, 10, 5, 0, 0), 7);
  eq(r.segments.length, 2, "the arc should be two segments, not one line through the ground");
});

test("walkTrack steps the sidereal rate to dawn", () => {
  const ctx = flatCtx({ hoursToDawn: 2 });
  const samples = walkTrack(41.27 * D2R, 0, ctx);
  eq(samples.length, 9, "two hours in quarter-hour steps, inclusive");
  // Polaris-adjacent declination at this latitude never sets; the point of the
  // check is that the hour angle actually advanced.
  assert(samples[8].az !== samples[0].az, "the target never moved");
});

// ========================================================= 5. the lock rule

const fakeTarget = (id: string): SkyTarget => ({
  id, name: id, full: "", kind: "galaxy", ra_hours: 0, dec_deg: 0,
  altNow: 58, azNow: 64, cloudPct: 4, obstructed: false, clouded: false,
  color: "var(--accent)", statusTxt: "CLEAR · 4%", palette: "LRGB",
  transitLabel: "23:52", windowMinutes: 160, score: 0.8, moonSepDeg: 90,
});
const mk = (id: string, x: number, y: number): Marker => ({
  id, name: id, kind: "galaxy", color: "var(--accent)", x, y,
  altTag: "58°", target: fakeTarget(id),
});

test("the lock is the nearest marker inside 46 px, and nothing outside it", () => {
  const W = 370;
  const H = 372;
  eq(pickLock([mk("near", W / 2 + 20, H / 2)], W, H)?.id, "near", "20 px from centre should lock");
  eq(pickLock([mk("far", W / 2 + 60, H / 2)], W, H), null, "60 px from centre must NOT lock");
  eq(
    pickLock([mk("a", W / 2 + 40, H / 2), mk("b", W / 2 + 5, H / 2)], W, H)?.id,
    "b",
    "the NEAREST of two inside the square is the lock",
  );
  eq(pickLock([mk("edge", W / 2 + 46, H / 2)], W, H), null, "the radius is exclusive at 46");
});

// ======================================================= 6. the cloud dome

const dome = (rows: (number | null)[][]): CloudmapDome => ({
  enabled: true, observed_at: null, stale: false, rows,
  alt_start: 6, alt_step: 6, az_step: 6,
});

test("a null cloud cell is a HOLE and never zero cloud", () => {
  const d = dome([[0.65, null, 0.1]]);
  const samples = domeToSamples(d);
  eq(samples.length, 2, "the null cell should have been dropped, not folded to 0");
  assert(!samples.some((s) => s.pct === 0), "a null cell became 0% cloud");
  const tiles = tilesFromDome(d);
  eq(cloudPctAt(tiles, 7, 1), 65, "probability 0.65 is 65 percent");
  eq(cloudPctAt(tiles, 7, 7), null, "the unread bin must answer null, not 0");
  eq(cloudPctAt(tiles, 7, 13), 10, "the third cell survived");
});

test("probabilities are converted to percent exactly once", () => {
  const tiles = tilesFromDome(dome([[1.0, 0.5, 0.0]]));
  eq(cloudPctAt(tiles, 8, 2), 100, "1.0 is 100 percent");
  eq(cloudPctAt(tiles, 8, 8), 50, "0.5 is 50 percent");
  eq(cloudPctAt(tiles, 8, 14), 0, "an explicit 0.0 IS zero cloud, unlike a null");
});

test("faint bins are not tiled, and a contiguous bank gets ONE label", () => {
  // Three adjacent 6-degree bins at 65% plus one far away, all at the same
  // altitude band, viewed from the middle of the bank.
  const rows: (number | null)[][] = [
    [0.65, 0.65, 0.65, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02],
  ];
  const tiles = tilesFromDome(dome(rows));
  const p = makeProjector(370, 9, 10);
  const rects = cloudRects(tiles, p);
  assert(rects.length > 0, "nothing was tiled - the fixture is wrong");
  assert(rects.every((r) => r.pct >= 12), "a 2% bin was tiled");
  const labels = cloudBlobLabels(rects, p);
  eq(labels.length, 1, "three touching bins should be one labelled bank");
  eq(labels[0].text, "CLOUD 65%", "the label quotes the blob's peak");
});

// ============================================================== 7. the wind

test("meteorological wind direction is turned around before it is drawn", () => {
  const m = windFrom(
    { ts: "", temp_c: 12, dewpoint_c: 4, humidity_pct: null,
      wind_kmh: 12, wind_dir_deg: 225, gust_kmh: null, cloud_base_m: null },
    null,
  );
  assert(m != null, "no wind model from a complete feed");
  eq(m?.towardDeg, 45, "wind FROM 225 blows TOWARD 45");
  eq(compassPoint(m?.towardDeg ?? 0), "NE", "toward 45 is NE");
  eq(m?.measured, false, "a forecast wind must not claim to be measured");
});

test("a measured cloud motion is already a toward-bearing and wins", () => {
  const m = windFrom(
    { ts: "", temp_c: null, dewpoint_c: null, humidity_pct: null,
      wind_kmh: 12, wind_dir_deg: 225, gust_kmh: null, cloud_base_m: null },
    { speed_kmh: 14, toward_deg: 45, corroborated: true, peak: 0.4, dt_s: 600, reason: "" },
  );
  eq(m?.towardDeg, 45, "the measured bearing passes through untouched");
  eq(m?.kmh, 14, "the measured speed wins over the forecast");
  eq(m?.measured, true, "a measured drift says so");
});

test("cloud base falls back through the feed, the spread, then the prototype's 2.2 km", () => {
  eq(cloudBaseKm({ ts: "", temp_c: null, dewpoint_c: null, humidity_pct: null,
    wind_kmh: null, wind_dir_deg: null, gust_kmh: null, cloud_base_m: 1800 }).km, 1.8, "feed value");
  const spread = cloudBaseKm({ ts: "", temp_c: 20, dewpoint_c: 8, humidity_pct: null,
    wind_kmh: null, wind_dir_deg: null, gust_kmh: null, cloud_base_m: null });
  near(spread.km, 1.5, 1e-9, "125 m per degree of spread");
  eq(spread.source, "spread", "the estimate says it is an estimate");
  eq(cloudBaseKm(null).km, 2.2, "the last resort");
});

test("wind arrows point DOWNWIND on screen - a flipped convention reverses every one", () => {
  // The view is centred on az 45; the wind blows toward az 45 (i.e. FROM 225).
  // Every sky point in the box then drifts to a LOWER altitude, so every arrow
  // has a positive (downward) screen rotation.
  const p = makeProjector(370, 45, 40);
  const right = windFrom(
    { ts: "", temp_c: null, dewpoint_c: null, humidity_pct: null,
      wind_kmh: 12, wind_dir_deg: 225, gust_kmh: null, cloud_base_m: 2200 },
    null,
  );
  assert(right != null, "no wind model");
  const arrows = windArrows(p, (right as NonNullable<typeof right>).wind, []);
  assert(arrows.length >= 8, `too few arrows to be a field (${arrows.length})`);
  assert(arrows.every((a) => a.rot > 0 && a.rot < 180), "an arrow pointed upwind");

  // Now the same wind with the conversion DROPPED (toward 225 instead of 45):
  // every arrow reverses. This is the check that a plausible-looking picture
  // cannot pass.
  const flipped = windFrom(null, {
    speed_kmh: 12, toward_deg: 225, corroborated: true, peak: 0.4, dt_s: 600, reason: "",
  });
  const back = windArrows(p, (flipped as NonNullable<typeof flipped>).wind, []);
  assert(back.every((a) => a.rot < 0 && a.rot > -180), "the flipped wind did not reverse the field");
});

test("arrows keep clear of the reticle and the two round buttons", () => {
  const p = makeProjector(370, 45, 40);
  const w = windFrom(null, {
    speed_kmh: 12, toward_deg: 45, corroborated: true, peak: 0.4, dt_s: 600, reason: "",
  });
  const arrows = windArrows(p, (w as NonNullable<typeof w>).wind, []);
  assert(
    !arrows.some((a) => Math.abs(a.x - 185) < 60 && Math.abs(a.y - 186) < 60),
    "an arrow landed under the reticle",
  );
  assert(!arrows.some((a) => a.x > 300 && a.y < 145), "an arrow landed under the funnel button");
  eq(windArrows(p, (w as NonNullable<typeof w>).wind, [], true).length, 0, "FRAME mode draws none");
});

// ========================================== 8. row shapes and the decorations

test("a solar-system row's id is the LABEL and its name is a SENTENCE", () => {
  const rows: CatalogRowLike[] = [{
    id: "Jupiter",
    name: "Jupiter, 44 arcseconds across, 34 degrees up in the south-east.",
    type: "Planet",
    kind: "solar_system",
    ra_hours: 4.2,
    dec_deg: 21.1,
  }];
  const merged = mergeRows(rows);
  eq(merged.length, 1, "the planet was dropped");
  eq(merged[0].name, "Jupiter", "the marker pill would have printed a whole sentence");
  assert(merged[0].full.startsWith("Jupiter, 44"), "the sentence did not become the second line");
  eq(merged[0].kind, "planet", "kind mapping");
});

test("the Sun is dropped and a DSO keeps its own field order", () => {
  eq(
    kindOf({ id: "Sun", type: "Sun", kind: "solar_system", ra_hours: 0, dec_deg: 0 }),
    null,
    "the Sun has no place on a deep-sky finder",
  );
  const dso = mergeRows([{ id: "m31", name: "M31", type: "Galaxy", ra_hours: 0.71, dec_deg: 41.27 }]);
  eq(dso[0].name, "M31", "a DSO's name is its short label");
  eq(dso[0].kind, "galaxy", "Galaxy maps to galaxy");
  eq(dso[0].full, "", "a ranked pick carries no sentence, and none is invented");
  eq(dso[0].type, "Galaxy", "the readable type is kept as the caller's fallback");
});

test("a planetary nebula is a nebula, not a planet", () => {
  eq(kindOf({ id: "m57", type: "Planetary Nebula", kind: "dso", ra_hours: 0, dec_deg: 0 }), "nebula",
    "q=planet also matches every Planetary Nebula, which is why type refines kind");
  eq(kindOf({ id: "m13", type: "Globular Cluster", kind: "dso", ra_hours: 0, dec_deg: 0 }), "cluster",
    "cluster mapping");
  eq(kindOf({ id: "vega", type: "Star", kind: "star", ra_hours: 0, dec_deg: 0 }), "nebula",
    "the prototype's own fallback keeps a star drawable");
});

test("merging fills gaps without overwriting anything with a blank", () => {
  const tonight: CatalogRowLike[] = [
    { id: "m31", name: "M31", type: "Galaxy", ra_hours: 0.71, dec_deg: 41.27,
      moon_sep_deg: 74, transit_unix: 1_756_000_000 },
  ];
  const region: CatalogRowLike[] = [
    { id: "m31", label: "M31", type: "Galaxy", kind: "dso", ra_hours: 0.71, dec_deg: 41.27,
      describe: "Spiral galaxy in Andromeda, magnitude 3.4." },
  ];
  const merged = mergeRows(tonight, region);
  eq(merged.length, 1, "the same object appeared twice");
  eq(merged[0].moonSepDeg, 74, "the ranked row's moon separation was lost");
  assert(merged[0].full.startsWith("Spiral galaxy"), "the region row's sentence was not adopted");
});

test("the catalogued extent survives the merge, so FRAME can draw the ellipse", () => {
  // Every source sends `size_arcmin` - objects.py for a ranked pick, region.py
  // for a region row, solar_system.py for a body - and dropping it here is what
  // made SkyCanvas draw M33 (178' across) as a point.
  const tonight: CatalogRowLike[] = [
    { id: "m33", name: "M33", type: "Galaxy", ra_hours: 1.5641, dec_deg: 30.66, size_arcmin: 178 },
  ];
  const region: CatalogRowLike[] = [
    { id: "m33", label: "M33", kind: "dso", type: "Galaxy", ra_hours: 1.5641, dec_deg: 30.66,
      size_arcmin: 178, describe: "Spiral galaxy in Triangulum, magnitude 5.7." },
  ];
  eq(mergeRows(tonight)[0].sizeArcmin, 178, "the ranked pick's own extent was dropped");
  eq(mergeRows(tonight, region)[0].sizeArcmin, 178, "the merge lost the extent");
  eq(
    mergeRows([{ id: "x", name: "X", type: "Galaxy", ra_hours: 0, dec_deg: 0 }])[0].sizeArcmin,
    null,
    "a row that carried no extent must read as absent, not as a zero-sized object",
  );
});

test("the status chip never claims CLEAR without a reading", () => {
  eq(decorate(4, false).statusTxt, "CLEAR · 4%", "a clear reading");
  eq(decorate(55, false).statusTxt, "CLOUD 55%", "a clouded reading");
  eq(decorate(55, false).clouded, true, "40 percent and over is clouded");
  eq(decorate(39, false).clouded, false, "under 40 is not");
  eq(decorate(4, true).statusTxt, "BEHIND HORIZON", "obstruction outranks cloud");
  eq(decorate(null, false).statusTxt, "UP · cloud -", "no reading must not read as CLEAR");
  eq(decorate(null, false).clouded, false, "an absent reading is not a cloud");
});

test("the palette is derived from the wheel the rig reports, never assumed", () => {
  const three = { names: ["L", "R", "G", "B", "Ha", "OIII", "SII"],
                  narrowband: [false, false, false, false, true, true, true] };
  eq(paletteFor("nebula", "Emission Nebula", three), "SHO", "three narrowband slots");
  eq(paletteFor("galaxy", "Galaxy", three), "LRGB", "a galaxy is broadband whatever the wheel");
  eq(paletteFor("nebula", "Emission Nebula",
    { names: ["L", "Ha", "OIII"], narrowband: [false, true, true] }), "HOO", "two narrowband slots");
  eq(paletteFor("nebula", "Emission Nebula",
    { names: ["L", "R", "G", "B", "Ha"], narrowband: [false, false, false, false, true] }),
    "Ha + LRGB", "one narrowband slot");
  eq(paletteFor("nebula", "Emission Nebula", null), "OSC", "no wheel means one-shot colour");
  eq(paletteFor("planet", "Planet", three), "RGB video", "a planet is video, not a filter cycle");
  eq(paletteFor("nebula", "Reflection Nebula", three), "LRGB",
    "a reflection nebula shows nothing through an Ha filter");
});

test("blackout slots do not count as filters", () => {
  eq(narrowbandSlots({ names: ["Ha", "Dark"], narrowband: [true, true], opaque: [false, true] }), 1,
    "a blackout slot holds no glass");
  eq(paletteFor("nebula", "Emission Nebula", { names: ["Dark"], opaque: [true] }), "OSC",
    "a wheel whose only slot is a blackout is not a wheel");
});

// ============================================================ 9. the ranking

test("rankTargets drops hidden kinds and orders by score", () => {
  const rows = [
    { id: "low", altNow: 10, minutesAboveFloorToDawn: 20, cloudPct: 70, moonSepDeg: 10 },
    { id: "high", altNow: 70, minutesAboveFloorToDawn: 240, cloudPct: 2, moonSepDeg: 90 },
    { id: "hidden", altNow: 80, minutesAboveFloorToDawn: 240, cloudPct: 0, moonSepDeg: 90, hidden: true },
  ];
  const ranked = rankTargets(rows);
  eq(ranked.length, 2, "a hidden kind stayed in the ranking");
  eq(ranked[0].id, "high", "a clear high target should outrank a clouded low one");
  assert(ranked[0].score > ranked[1].score, "the scores did not separate them");
});

test("the reach count is the clear, unobstructed subset", () => {
  // THIS CALLS THE MODULE. It used to write the predicate INSIDE the test, over
  // fixtures written inside the test, and assert that the two agreed - which
  // they always would, because no module function was reached at all. Deleting
  // every reach rule in the finder left it green (review #45). `inReach` is now
  // the one predicate behind the status row's count, the reach strip, the lens
  // dial's per-kind counts and the auto-aim, so breaking it breaks this.
  const ts: SkyTarget[] = [
    { ...fakeTarget("a") },
    { ...fakeTarget("b"), clouded: true, cloudPct: 55 },
    { ...fakeTarget("c"), obstructed: true },
    { ...fakeTarget("d"), clouded: true, obstructed: true },
  ];
  eq(ts.filter(inReach).length, 1, "only the clear, unobstructed target is in reach");
  eq(inReach(ts[0]), true, "clear and unobstructed:");
  eq(inReach(ts[1]), false, "clouded:");
  eq(inReach(ts[2]), false, "behind the horizon:");
  eq(inReach(ts[3]), false, "both at once:");
  // The seeing floor is NOT part of it: a low target is in reach and merely
  // low, and folding the floor in here would silently empty the strip at a site
  // whose objects all transit under 25 degrees.
  const low: SkyTarget = { ...fakeTarget("low"), altNow: 4 };
  eq(inReach(low), true, "a low but clear target is still in reach");
});

test("windowLabel renders the design's own window string", () => {
  eq(windowLabel(160), "2h 40m", "the lock card's window");
  eq(windowLabel(45), "45m", "under an hour");
  eq(windowLabel(0), "0m", "nothing left");
});

test("the local sidereal time is pinned to the server's own arithmetic", () => {
  // AN ABSOLUTE VALUE, not a round trip. This assertion used to read
  // `near(lstHours(lon,t) % 24, lstHours(lon,t), 1e-12)` - a number compared to
  // itself, green for any implementation whatsoever. Proven: replacing
  // `lib/altaz.ts`'s `lstHours` with `return 3` left this file 40/40 (#45), and
  // every other coordinate test above it is an INVERSE test, which survives a
  // wrong LST because both directions use the same wrong one.
  //
  // The expected values are `server/astrodeck/catalog/coords.py:120 lst_hours`
  // evaluated at these instants:
  //
  //     lst_hours(-122.33, 1_756_000_000) -> 15.795756820235324
  //     lst_hours(   0.0 , 1_756_000_000) -> 23.951090153568657
  //     lst_hours(-122.33, 1_700_000_000) -> 17.650500248152454
  //
  // 1e-9 hours is 3.6 microseconds of sidereal time - far tighter than the
  // "good to about a second" this port claims, and loose enough that the last
  // ulp of the positive-modulo differs harmlessly between the two languages.
  near(lstHours(-122.33, 1_756_000_000), 15.795756820235324, 1e-9,
    "LST at the rig's own longitude");
  near(lstHours(0, 1_756_000_000), 23.951090153568657, 1e-9,
    "GMST at Greenwich, the term the longitude is added to");
  near(lstHours(-122.33, 1_700_000_000), 17.650500248152454, 1e-9,
    "a second epoch, so a constant offset cannot pass");
  // The range contract every caller depends on: hour angle is `lst - ra`, and a
  // 24.0 or a negative would put a target half a day out with no visual tell.
  for (const t of [0, 1_700_000_000, 1_756_000_000, 2_000_000_000]) {
    for (const lon of [-179.9, -122.33, 0, 122.33, 179.9]) {
      const v = lstHours(lon, t);
      assert(v >= 0 && v < 24, `lstHours(${lon}, ${t}) left [0,24): ${v}`);
    }
  }
});

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`skyModel.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) {
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}

export default { passed, failed, total };
export { passed, failed, total };
