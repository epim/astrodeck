// frameModel.test.ts - FRAME mode's restored arithmetic and copy, without a DOM.
//
//   Run directly:  npx tsx src/next/hubs/sky/frame/__tests__/frameModel.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc --noEmit`.
//
// WHAT IS WORTH A TEST HERE. Every one of these is a rule whose failure is
// invisible on screen - the picture still draws, the sentence still reads:
//
//   * the PINCH DIRECTION. Fingers apart must show LESS sky. A sign flip still
//     zooms, still feels responsive, and does the opposite of every other zoom
//     control in the app;
//   * "fit object" over a SIZELESS row. 1,823 catalogue rows publish no size,
//     and `1.6 * 0` is a legal number that crops the survey to nothing;
//   * the DEGRADED CAUSE. Four different failures, four different fixes; the
//     shipped code named one cause for all of them and sent a rig with online
//     fetch ON to download a pack it does not need (review #30);
//   * `framingMatches`. The framing slice is GLOBAL, so without this a mosaic
//     kept for one target is drawn over - and queued under - another (#3);
//   * the MOSAIC HASH. A malformed `?mosaic=` must produce NO card rather than
//     a card claiming panels nobody computed.
//
// Convention: inline test()/eq() helpers, printed tally plus the
// { passed, failed, total } export (shell-and-tests.md section 4).

/* eslint-disable @typescript-eslint/no-explicit-any */

// `mosaic.ts` and `flowLane.ts` are pure, but they are reached through modules
// that pass `api.ts` / `lib/base.ts`, which read `window.location` AT MODULE
// SCOPE. So the browser globals go in first and the imports are dynamic - the
// same convention `skyCards.test.ts` uses, for the same reason: importing too
// early captures undefined globals permanently.
{
  const g = globalThis as any;
  if (typeof g.window === "undefined") {
    g.window = {
      location: { pathname: "/", protocol: "http:", host: "test", hash: "" },
      addEventListener() {}, removeEventListener() {},
      matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }),
    };
  }
  if (typeof g.localStorage === "undefined") {
    const m = new Map<string, string>();
    g.localStorage = {
      getItem: (k: string) => (m.has(k) ? (m.get(k) as string) : null),
      setItem: (k: string, v: string) => { m.set(k, String(v)); },
      removeItem: (k: string) => { m.delete(k); },
      clear: () => { m.clear(); },
    };
  }
  if (typeof g.document === "undefined") {
    g.document = {
      documentElement: {
        classList: { toggle() {}, add() {}, remove() {}, contains() { return false; } },
        style: { setProperty() {}, getPropertyValue() { return ""; } },
      },
    };
  }
}

import type { PackStatus } from "../../../../../types";

const {
  ZOOM_MAX, ZOOM_MIN, clampZoom, fitObjectZoom, frameFovDeg, matchCameraZoom,
  pinchZoom, pointerDist, zoomLabel, zoomStep,
} = await import("../zoom");
const {
  CATALOG_DEGRADED, CATALOG_DENSE, DEGRADED_NO_SOURCE, DEGRADED_ONLINE,
  DEGRADED_PACK_PRESENT, DEGRADED_PACK_UNKNOWN, regionNotes, shouldPollPack,
  surveyDegradedText,
} = await import("../degraded");
const { commandedPa, framingMatches, mosaicBaseName, mosaicGroupId } = await import("../mosaic");
const { parseMosaicParam } = await import("../../sheets/flowLane");
const { floorLegend, mosaicPlanNote } = await import("../../sheets/quickCopy");

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
function near(got: number, want: number, tol: number, msg: string): void {
  if (!(Math.abs(got - want) <= tol)) {
    throw new Error(`${msg} (expected ${want} +/- ${tol}, got ${got})`);
  }
}

const pack = (over: Partial<PackStatus>): PackStatus => ({
  present: false, slug: "dss2-order4", survey: "CDS/P/DSS2/color",
  order: null, bytes: null, tile_count: null, fetched_at: null, fetching: null,
  ...over,
});

// ==================================================================== 1. zoom

test("clampZoom holds the survey's own bounds and refuses a non-number", () => {
  eq(clampZoom(0.001), ZOOM_MIN, "under the floor:");
  eq(clampZoom(400), ZOOM_MAX, "over the ceiling:");
  eq(clampZoom(1.5), 1.5, "inside:");
  eq(clampZoom(Number.NaN), ZOOM_MIN, "a NaN must not reach SkyCanvas as a crop:");
});

test("the stepper moves multiplicatively, so both ends of the range are usable", () => {
  // A FIXED step cannot serve 0.1 and 10 at once: 0.5 deg is five whole views
  // at the bottom and a rounding error at the top. Both directions are checked
  // by name because a `+` that widens is a control doing the opposite of its
  // label, and the value still changes.
  near(zoomStep(1, 1), 0.8, 1e-9, "+ tightens:");
  near(zoomStep(1, -1), 1.25, 1e-9, "- widens:");
  assert(zoomStep(0.4, 1) < 0.4, "+ at 0.4 deg must still tighten");
  assert(zoomStep(8, -1) > 8, "- at 8 deg must still widen");
  eq(zoomStep(ZOOM_MIN, 1), ZOOM_MIN, "+ at the floor stays at the floor:");
  eq(zoomStep(ZOOM_MAX, -1), ZOOM_MAX, "- at the ceiling stays at the ceiling:");
});

test("a pinch APART shows less sky, which is the direction every other zoom takes", () => {
  const wider = pinchZoom(2, 200, 100);   // fingers together
  const tighter = pinchZoom(2, 100, 200); // fingers apart
  assert(tighter < 2, `fingers apart must reduce the field of view, got ${tighter}`);
  assert(wider > 2, `fingers together must widen the field of view, got ${wider}`);
  near(tighter, 1, 1e-9, "double the span is half the field:");
  eq(pinchZoom(2, 0, 120), 2, "a zero start span leaves the field alone:");
  eq(pinchZoom(2, 120, 0), 2, "so does a zero current span:");
  eq(pointerDist({ x: 0, y: 0 }, { x: 3, y: 4 }), 5, "pointer distance:");
});

test("fit object falls back for a row nobody published a size for", () => {
  // 1,823 catalogue rows carry `mag: null` and plenty carry no size either.
  // `1.6 * 0` is a perfectly legal number that crops the survey to nothing.
  near(fitObjectZoom({ size_arcmin: 190 }, 1.68), 1.6 * (190 / 60), 1e-9, "M31 at 1.6x its extent:");
  eq(fitObjectZoom({ size_arcmin: 0 }, 1.68), clampZoom(1.68), "a sizeless row falls back to the frame:");
  eq(fitObjectZoom(null, 0), 0.5, "no object and no optics falls back to half a degree:");
  eq(fitObjectZoom({ size_arcmin: 100000 }, 1.68), ZOOM_MAX, "an absurd size is clamped, not obeyed:");
});

test("match camera zooms to the frame with the Atlas's own margin", () => {
  near(matchCameraZoom(1.68), 1.68 * 1.6, 1e-9, "1.6x the single frame:");
  eq(frameFovDeg(1.68, 1.12), 1.68, "the LONG axis is what must fit:");
  eq(frameFovDeg(0, 0), 0, "no optics is zero, which is what locks the control:");
  eq(frameFovDeg(-3, 1.1), 1.1, "a nonsense axis does not become the frame:");
});

test("the field readout keeps two decimals where a mosaic is framed", () => {
  eq(zoomLabel(0.34), "0.34°", "under a degree:");
  eq(zoomLabel(1.68), "1.7°", "over a degree:");
});

// ============================================================== 2. degraded

test("the degraded banner names the cause, not one cause for four failures", () => {
  eq(surveyDegradedText(true, null), DEGRADED_ONLINE,
    "with online fetch ON the pack is not the problem:");
  eq(surveyDegradedText(true, pack({ present: true })), DEGRADED_ONLINE,
    "and it is still not the problem when the pack happens to be installed:");
  eq(surveyDegradedText(false, null), DEGRADED_PACK_UNKNOWN,
    "with no pack status yet, no claim about the pack:");
  eq(surveyDegradedText(false, pack({})), DEGRADED_NO_SOURCE,
    "no pack and no online fetch is the one case the shipped sentence was right about:");
  eq(surveyDegradedText(false, pack({ present: true })), DEGRADED_PACK_PRESENT,
    "an installed pack that did not draw is a coverage answer, not a download one:");
  const p = surveyDegradedText(false, pack({ fetching: { done: 412, total: 1024, failed: 0 } }));
  assert(/412\/1024/.test(p), `the download progress must carry its numbers: "${p}"`);
  // Never undefined: SkyCanvas's OWN fallback is the offline-pack sentence, so
  // falling through would put the same wrong cause back on the online case.
  for (const s of [
    surveyDegradedText(true, null), surveyDegradedText(false, null),
    surveyDegradedText(false, pack({ present: true })),
  ]) assert(typeof s === "string" && s.length > 0, "a state answered with nothing");
});

test("the pack is polled only in the state whose copy depends on it", () => {
  eq(shouldPollPack(true, false), true, "degraded and offline:");
  eq(shouldPollPack(true, true), false, "degraded with an online source:");
  eq(shouldPollPack(false, false), false, "not degraded at all:");
});

test("region notes separate three absences with three different fixes", () => {
  eq(regionNotes({ degraded: false, truncated: false, error: null }).length, 0,
    "a healthy region says nothing:");
  const all = regionNotes({ degraded: true, truncated: true, error: "the server said no" });
  eq(all.length, 3, "three absences:");
  eq(all[0], CATALOG_DEGRADED, "the broken catalogue leads - it is the one that empties the sky:");
  eq(all[1], CATALOG_DENSE, "then the dense field:");
  eq(all[2], "the server said no", "then the request's own sentence, verbatim:");
  assert(/64 built-in/.test(CATALOG_DEGRADED),
    "the degraded note must say HOW MUCH is left, or an empty sky and a broken one still look alike");
});

// =========================================================== 3. whose framing

const M31 = { target: { id: "m31", name: "M31" } };
const ROAM = { freeroamId: "Sky 20.97h +30.7°" };

test("a framing belongs to the target it was framed for, and to no other", () => {
  eq(framingMatches(M31, "m31"), true, "its own id:");
  eq(framingMatches(M31, "M31"), true, "its own display name, which is what a typed target carries:");
  eq(framingMatches(M31, "m42"), false, "another target must not inherit it:");
  eq(framingMatches(null, "m31"), false, "no session at all:");
  eq(framingMatches(M31, null), false, "no target at all:");
  eq(framingMatches(ROAM, "Sky 20.97h +30.7°"), true, "a free-roam session matches its own group id:");
  eq(framingMatches(ROAM, "m31"), false, "and nothing else:");
});

test("the plan's group id and panel names follow the Atlas's own rule", () => {
  eq(mosaicGroupId(M31), "m31", "a catalogued object groups by id:");
  eq(mosaicGroupId(ROAM), "Sky 20.97h +30.7°", "free-roam groups by the stable session id:");
  eq(mosaicGroupId({}), undefined, "nothing to group by is undefined, not a made-up key:");
  eq(mosaicBaseName(M31), "m31", "panel names are built from the id an operator finds in the plan:");
  eq(mosaicBaseName({ target: { name: "Typed position" } }), "Typed position", "name when there is no id:");
  eq(mosaicBaseName({}), "Sky", "free-roam:");
});

test("the 0.5 degree dead-band decides whether an angle is COMMANDED", () => {
  // Load-bearing: `rotation_deg` starts at 0 for every framing session, so
  // treating 0 as a command bolts a rotate-to-PA loop onto every "show me this"
  // tap - minutes of motion nobody asked for.
  eq(commandedPa(0), null, "a fresh session commands nothing:");
  eq(commandedPa(0.4), null, "inside the dead-band:");
  eq(commandedPa(30), 30, "a real angle:");
});

// ============================================================= 4. the hash

test("the flow card's MOSAIC row comes from the hash, and refuses nonsense", () => {
  eq(parseMosaicParam("2x1")?.cols, 2, "cols:");
  eq(parseMosaicParam("2x1")?.rows, 1, "rows:");
  eq(parseMosaicParam("3x2")?.rows, 2, "a deeper grid:");
  eq(parseMosaicParam("1x1"), null, "a single frame is not a mosaic and gets no card:");
  eq(parseMosaicParam(""), null, "a deep link with no mosaic must not invent one:");
  eq(parseMosaicParam("lots"), null, "a malformed hash:");
  eq(parseMosaicParam("0x4"), null, "a zero dimension:");
  eq(parseMosaicParam("-2x3"), null, "a negative dimension:");
});

// ============================================================== 5. the copy

test("the horizon legend prints the SITE's number, and says when there is none", () => {
  assert(/30°/.test(floorLegend(30)), "the legend must carry the site's own limit");
  assert(!/25°/.test(floorLegend(30)), "the hardcoded 25 must not survive anywhere in the legend");
  assert(/No horizon limit set/.test(floorLegend(0)),
    "zero is the absence of a limit, not a 0 degree one");
});

test("the mosaic note says where the panels actually go", () => {
  const n = mosaicPlanNote(6, 3, 2);
  assert(/6 panels/.test(n), `the note must name the count: "${n}"`);
  assert(/3×2/.test(n), "and the grid");
  assert(/plan targets/.test(n),
    "the note must name the PLAN, because that is the surprising half of H.6");
});

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`frameModel.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
