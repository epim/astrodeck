// ephemerisRows.test.ts - satellites and comets as rows, without a DOM
// (wave U7b, T-U7b-1; decision D-SKY-1).
//
//   Run directly:  npx tsx src/next/hubs/sky/finder/__tests__/ephemerisRows.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc --noEmit`.
//
// WHAT IS WORTH A TEST HERE. Two of these guard a defect that would look
// entirely right on screen, which is the only reason any of them exist:
//
//   * THE ROW-SHAPE TRAP. A satellite and a comet use the solar-system naming
//     convention - `id` is the LABEL and `name` is a composed SENTENCE - and
//     they are the second and third row families to do it. A card that read
//     `name` for one would print a paragraph where a title goes, and it would
//     print it confidently. The assertion is that the SHORT name comes back.
//   * THE KIND TABLE IS COMPLETE. `SKY_KINDS` drives the lens dial's seats, the
//     stored lens preference and every per-kind count. A kind added without a
//     label or a glyph does not crash: it renders a blank caption and an
//     `undefined` icon name, which looks like a styling bug rather than a
//     missing table row. A table test is the only thing that catches it at the
//     moment the kind is added.
//   * THE SUNLIT CLAUSE. `sunlit_fraction` is a fraction of ONE pass, so a bare
//     percentage is a percentage of nothing; and a pass whose fraction is 0 is
//     not a dim pass, it is an invisible one. The three cases are three
//     different pieces of advice, and the null-shadow case must not compute a
//     duration out of nulls.
//   * SATELLITES ARE NOT MARKERS. `SATELLITE_MARKERS` is the one line that
//     decides whether a satellite can be drawn on the reticle. The server-side
//     half of the argument was fixed by S7L (`app.py:7473-7482` no longer
//     overwrites a satellite's topocentric alt/az); the client-side half was
//     not, because the rows are cached for two minutes and a low-orbit
//     satellite crosses the whole sky in five. Flipping this needs a
//     per-second position source, so a test pins it rather than a comment.
//
// Convention: inline test()/eq() helpers, printed tally plus the
// { passed, failed, total } export (shell-and-tests.md section 4).

/* eslint-disable @typescript-eslint/no-explicit-any */

// The modules under test are pure, but they are reached through the Sky hub's
// own barrels, and those barrels pass through `api.ts` / `lib/base.ts` /
// `store.ts`, which read `window.location` and `localStorage` AT MODULE SCOPE.
// So the browser globals go in first and every import below is dynamic - the
// same block `skyCards.test.ts` opens with, for the same reason.
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

import type { CatalogRowLike } from "../targets";
import type { SatellitePass } from "../../../../../types";

const {
  KIND_ICON, KIND_LABEL, SATELLITE_MARKERS, SKY_KINDS,
  displayName, fullName, kindOf, mergeRows,
} = await import("../targets");
const { LENS_COUNT_NOUN, LENS_LEARN, lensSeats } = await import("../../cards/lens");
const { azPoint, passDuration, sunlitClause } = await import("../../cards/PassesCard");
const { lockCta, NO_PASSES_REASON } = await import("../../cards/lockCta");
const { NX_ICON_NAMES } = await import("../../../../icons");

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
  if (got !== want) throw new Error(`${msg} expected ${String(want)}, got ${String(got)}`);
}

// ----------------------------------------------------------------- fixtures
//
// Both rows are transcribed from the server's own row builders
// (`ephemeris/satellites.py:421-441`, `ephemeris/comets.py:453-490`), including
// the thing that matters: `id` short, `name` a sentence.
const ISS: CatalogRowLike = {
  id: "ISS (ZARYA)",
  name: "ISS (ZARYA) is 62 degrees up in the south, 431 km away and sunlit.",
  type: "Satellite",
  kind: "satellite",
  ra_hours: 5.5,
  dec_deg: 12.0,
  alt: 62.0,
  az: 180.0,
  mag: null,
  size_arcmin: 0.0,
};

const COMET: CatalogRowLike = {
  id: "12P/Pons-Brooks",
  name: "12P/Pons-Brooks is 24 degrees up in the west at magnitude 4.8.",
  type: "Comet",
  kind: "comet",
  ra_hours: 1.25,
  dec_deg: 33.0,
  alt: 24.0,
  az: 270.0,
  mag: 4.8,
  size_arcmin: 0.0,
};

function pass(over: Partial<SatellitePass> = {}): SatellitePass {
  return {
    norad_id: 25544,
    name: "ISS (ZARYA)",
    start_unix: 1_757_000_000,
    peak_unix: 1_757_000_300,
    end_unix: 1_757_000_600,
    start_az: 270,
    peak_az: 180,
    end_az: 90,
    max_alt_deg: 62,
    duration_s: 600,
    sunlit_fraction: 1,
    enters_shadow_unix: null,
    leaves_shadow_unix: null,
    elements_age_days: 1.2,
    ...over,
  };
}

// ============================================================== the kind map

test("a satellite row and a comet row map to their own kinds, not to nebula", () => {
  eq(kindOf(ISS), "satellite", "satellite kind:");
  eq(kindOf(COMET), "comet", "comet kind:");
  // The failure this guards: neither `type` is in the DSO tables, so both used
  // to fall through to the `nebula` default and be drawn with a nebula glyph.
  assert(kindOf(ISS) !== "nebula" && kindOf(COMET) !== "nebula",
    "an ephemeris row fell through to the deep-sky default");
});

test("the label is the SHORT name for both - a card must not print the sentence", () => {
  eq(displayName(ISS), "ISS (ZARYA)", "satellite label:");
  eq(displayName(COMET), "12P/Pons-Brooks", "comet label:");
  // And the sentence is what the SECOND line gets, which is the other half of
  // the same convention.
  assert(fullName(ISS).startsWith("ISS (ZARYA) is 62 degrees"), "satellite sentence lost");
  assert(fullName(COMET).startsWith("12P/Pons-Brooks is 24 degrees"), "comet sentence lost");
  // A row whose `name` was read as the label would be longer than any title
  // slot on the lock card, which is the shape of the defect.
  assert(displayName(ISS).length < 40, "the satellite label is a paragraph");
  assert(displayName(COMET).length < 40, "the comet label is a paragraph");
});

test("seven kinds, and every one of them has a label, a glyph, a lesson and a noun", () => {
  eq(SKY_KINDS.length, 7, "kind count:");
  assert(SKY_KINDS.includes("satellite") && SKY_KINDS.includes("comet"),
    `the two ephemeris kinds are missing: ${SKY_KINDS.join(",")}`);
  for (const k of SKY_KINDS) {
    assert(typeof KIND_LABEL[k] === "string" && KIND_LABEL[k].length > 0, `${k} has no label`);
    assert(NX_ICON_NAMES.includes(KIND_ICON[k]), `${k}'s glyph ${KIND_ICON[k]} is not a real icon`);
    assert(typeof LENS_LEARN[k] === "string" && LENS_LEARN[k].length > 40, `${k} has no lesson`);
    assert(typeof LENS_COUNT_NOUN[k] === "string" && LENS_COUNT_NOUN[k].length > 0,
      `${k}'s count has no noun, so its number stands for nothing`);
  }
  eq(lensSeats().length, 7, "seat count:");
  eq(lensSeats().map((s) => s.kind).join(","), SKY_KINDS.join(","),
    "the seats must be the finder's own kind list:");
});

test("the two ephemeris kinds count something other than reach, and say so", () => {
  eq(LENS_COUNT_NOUN.satellite, "listed", "satellite count noun:");
  eq(LENS_COUNT_NOUN.comet, "listed", "comet count noun:");
  eq(LENS_COUNT_NOUN.galaxy, "in reach", "galaxy count noun:");
});

test("a satellite is a list target, not a marker", () => {
  // Flip this constant only with a per-second position source behind it. The
  // rows are fetched on the same two-minute cadence as the planets, and the
  // finder would draw a two-minute-old ISS as confidently as a galaxy.
  eq(SATELLITE_MARKERS, false, "SATELLITE_MARKERS:");
});

test("mergeRows keeps an ephemeris row's short label and sentence apart", () => {
  const merged = mergeRows([COMET]);
  eq(merged.length, 1, "merged rows:");
  eq(merged[0].name, "12P/Pons-Brooks", "merged label:");
  assert(merged[0].full.includes("magnitude 4.8"), "merged sentence lost");
  eq(merged[0].kind, "comet", "merged kind:");
});

// ============================================================== the pass row

test("azimuth becomes a compass point the reader can face", () => {
  eq(azPoint(0), "N", "0 deg:");
  eq(azPoint(90), "E", "90 deg:");
  eq(azPoint(180), "S", "180 deg:");
  eq(azPoint(270), "W", "270 deg:");
  eq(azPoint(359), "N", "359 deg wraps:");
});

test("a pass duration keeps its seconds - a pass is minutes long", () => {
  eq(passDuration(600), "10 min", "600 s:");
  eq(passDuration(260), "4 min 20 s", "260 s:");
  eq(passDuration(45), "45 s", "45 s:");
});

test("sunlit throughout, in shadow throughout, and the split - three sentences", () => {
  eq(sunlitClause(pass({ sunlit_fraction: 1 })), "sunlit throughout", "fully sunlit:");
  // 0 is not a dim pass. It is one nobody can see, and that is a sentence.
  eq(sunlitClause(pass({ sunlit_fraction: 0 })),
    "in shadow the whole pass - not visible", "fully shadowed:");
  const split = sunlitClause(pass({
    sunlit_fraction: 0.4,
    enters_shadow_unix: 1_757_000_240,
    leaves_shadow_unix: 1_757_000_500,
  }));
  eq(split, "sunlit for the first 4 of 10 minutes", "split pass:");
});

test("a partial pass with no refined crossing does NOT invent a minute", () => {
  const clause = sunlitClause(pass({
    sunlit_fraction: 0.4,
    enters_shadow_unix: null,
    leaves_shadow_unix: null,
  }));
  eq(clause, "partly sunlit", "both crossings null:");
  // The failure this guards: subtracting a null start yields NaN, and
  // "sunlit for the first NaN of 10 minutes" is what reaches the screen.
  assert(!/NaN/.test(clause), "a null shadow time reached the copy as NaN");
  assert(!/\d/.test(clause), "a duration was computed out of two nulls");
});

// ================================================================ the CTA

test("a locked satellite offers its passes, not a deep-sky night", () => {
  const cta = lockCta(
    { name: "ISS (ZARYA)", kind: "satellite", obstructed: false, clouded: false, passCount: 3 },
    true,
  );
  eq(cta.kind, "passes", "satellite cta kind:");
  assert(cta.label.startsWith("NEXT PASS"), `satellite cta label: ${cta.label}`);
});

test("no passes is a different label from passes we have not looked for", () => {
  const none = lockCta(
    { name: "ISS", kind: "satellite", obstructed: false, clouded: false, passCount: 0 },
    true,
  );
  eq(none.label, "NO PASSES IN 24 H", "empty list label:");
  const unknown = lockCta(
    { name: "ISS", kind: "satellite", obstructed: false, clouded: false, passCount: null },
    true,
  );
  assert(unknown.label.startsWith("NEXT PASS"),
    `an unanswered fetch must not read as an empty sky: ${unknown.label}`);
  assert(NO_PASSES_REASON.length > 20, "the empty-list lock reason says nothing");
});

test("a satellite's CTA does not need a connected rig - looking up is not a command", () => {
  const cta = lockCta(
    { name: "ISS", kind: "satellite", obstructed: false, clouded: false, passCount: 2 },
    false,
  );
  eq(cta.kind, "passes", "satellite cta with no rig:");
  // Every other target falls to CONNECT THE RIG FIRST here, which is right for
  // them and wrong for this one.
  eq(lockCta({ name: "M 31", kind: "galaxy", obstructed: false, clouded: false }, false).kind,
    "connect", "a galaxy with no rig:");
});

test("a comet keeps the ordinary IMAGE cta - it is a deep-sky-shaped target", () => {
  const cta = lockCta(
    { name: "12P/Pons-Brooks", kind: "comet", obstructed: false, clouded: false },
    true,
  );
  eq(cta.kind, "image", "comet cta kind:");
  assert(cta.label.includes("12P/Pons-Brooks"), `comet cta label: ${cta.label}`);
});

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`ephemerisRows: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) {
  (globalThis as unknown as { process?: { exitCode?: number } }).process!.exitCode = 1;
}

export default { passed, failed, total };
export { passed, failed, total };
