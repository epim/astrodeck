// moon.test.ts - which target the hub is allowed to be about, and what the
// MOON tile is allowed to say.
//
//   Run directly:  npx tsx src/next/hubs/weather/__tests__/moon.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT THIS GUARDS:
//
//  * A SEPARATION IS ONLY EVER FROM A TARGET THE USER PICKED. `moonSub` must
//    drop the "61 degrees from X" clause when there is no X, or the tile prints
//    a real number about nothing - and `contextTarget` must return null rather
//    than guessing, because `/api/visibility` needs ra/dec and any placeholder
//    would come back with a plausible, meaningless separation.
//  * Both ends of the rise/set pair. `rise_unix` and `set_unix` are BOTH null
//    when the moon is up (or down) all night, so the altitude is what
//    disambiguates them; reading a null as "sets --:--" is the shape of a
//    readout that quietly means nothing.
//  * The notes clauses drop individually. A path line that prints "not
//    applicable" for the parts it cannot compute reads as data.

/* eslint-disable @typescript-eslint/no-explicit-any */

// `moon.ts` reaches `api.ts`, which reads `window.location.pathname` at module
// load to derive the relay mount base. Stub before importing, per the
// store-touching test convention in shell-and-tests.md section 4.
const g = globalThis as any;
if (typeof g.window === "undefined") {
  g.window = {
    location: { pathname: "/", protocol: "http:", host: "test" },
    setTimeout: globalThis.setTimeout.bind(globalThis),
    clearTimeout: globalThis.clearTimeout.bind(globalThis),
    addEventListener() {},
    removeEventListener() {},
  };
}

const { contextTarget, fmtClock, moonSetClause, moonSub, targetNotes } =
  await import("../conditions/moon");
type MoonInfo = import("../../../../types").MoonInfo;
type Target = import("../../../../types").Target;
type VisibilityNight = import("../../../../types").VisibilityNight;

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

const NOW = 1_757_000_000;

function target(name: string, ra = 0.7, dec = 41.3): Target {
  return {
    name, ra_hours: ra, dec_deg: dec,
    center: true, autofocus_first: false, calibration: false, steps: [],
  };
}
const PLAN = [target("M31"), target("NGC 7000", 20.9, 44.5)];

function moon(over: Partial<MoonInfo> = {}): MoonInfo {
  return {
    illumination: 0.71,
    phase_name: "Waning Gibbous",
    alt: 27,
    az: 168,
    separation_deg: 61.4,
    rise_unix: null,
    set_unix: NOW + 3600,
    ...over,
  };
}

// ------------------------------------------------------------ context target

test("the running session's target wins, resolved for its coordinates", () => {
  const t = contextTarget("M31", {}, PLAN);
  assert(t != null, "no context target - the fixture is wrong, not the code");
  eq(t!.name, "M31");
  eq(t!.source, "running");
  eq(t!.ra_hours, 0.7);
});

test("the hash names one when nothing is running, under either key", () => {
  eq(contextTarget(null, { target: "NGC 7000" }, PLAN)?.name, "NGC 7000");
  eq(contextTarget(null, { t: "NGC 7000" }, PLAN)?.name, "NGC 7000");
  eq(contextTarget(null, { target: "ngc 7000" }, PLAN)?.name, "NGC 7000", "case-insensitive:");
});

test("a name the plan does not carry resolves to NOTHING, not to a guess", () => {
  eq(contextTarget("M101", {}, PLAN), null, "a running name with no coordinates:");
  eq(contextTarget(null, { target: "M101" }, PLAN), null, "a routed name with no coordinates:");
  eq(contextTarget(null, {}, PLAN), null, "nothing picked:");
  eq(contextTarget(null, {}, null), null, "no plan at all:");
});

// ------------------------------------------------------------------ the moon

test("a moon that sets tonight says when", () => {
  assert(/^sets \d\d:\d\d$/.test(moonSetClause(moon())), moonSetClause(moon()));
});

test("a moon that has not risen yet says when it will", () => {
  const m = moon({ set_unix: null, rise_unix: NOW + 7200 });
  assert(/^rises \d\d:\d\d$/.test(moonSetClause(m)), moonSetClause(m));
});

test("neither rise nor set is disambiguated by the altitude, not left blank", () => {
  eq(moonSetClause(moon({ set_unix: null, rise_unix: null, alt: 30 })), "up all night");
  eq(moonSetClause(moon({ set_unix: null, rise_unix: null, alt: -12 })),
    "below the horizon all night");
});

test("the separation clause appears ONLY with a named target", () => {
  const withTarget = moonSub(moon(), "M31");
  assert(/· 61° from M31$/.test(withTarget), `got "${withTarget}"`);
  const without = moonSub(moon(), null);
  assert(!/from/.test(without), `a separation from nothing leaked: "${without}"`);
  assert(!/°/.test(without), `a bare degrees figure leaked: "${without}"`);
});

test("a missing time renders as --:--, never as a number", () => {
  eq(fmtClock(null), "--:--");
  eq(fmtClock(undefined), "--:--");
  eq(fmtClock(Number.NaN), "--:--");
});

// ------------------------------------------------------------- target notes

function night(over: Partial<VisibilityNight> = {}): VisibilityNight {
  const samples = [];
  // 6 samples at 30 min, dropping through the 30 deg limit at the 4th
  const alts = [55, 50, 42, 34, 22, 10];
  for (let i = 0; i < alts.length; i++) {
    samples.push({ t_unix: NOW + i * 1800, alt: alts[i], moon_alt: 10, sun_alt: -20 });
  }
  return {
    date: "2026-09-10",
    transit_unix: NOW + 900,
    transit_alt: 56,
    transit_in_daylight: false,
    dark_start_unix: NOW - 3600,
    dark_end_unix: NOW + 6 * 3600,
    darkness_kind: "astronomical",
    samples,
    moon: moon(),
    best_window: null,
    alt_limit_deg: 30,
    never_rises_above_limit: false,
    ...over,
  };
}

test("no ephemeris means no notes line, not an empty scaffold", () => {
  eq(targetNotes(null, NOW), "");
});

test("the path names transit, the drop through the limit and the time left", () => {
  const n = targetNotes(night(), NOW);
  assert(/transit \d\d:\d\d · 56°/.test(n), `got "${n}"`);
  assert(/below 30° at \d\d:\d\d/.test(n), `got "${n}"`);
  assert(/usable/.test(n), `got "${n}"`);
});

test("a target that never clears the limit says so instead of a transit", () => {
  const n = targetNotes(night({ never_rises_above_limit: true }), NOW);
  assert(/never reaches 30°/.test(n), `got "${n}"`);
  assert(!/transit/.test(n), `a transit was claimed for an object that never rises: "${n}"`);
});

test("a transit already behind us is not printed as a future one", () => {
  const n = targetNotes(night({ transit_unix: NOW - 7200 }), NOW);
  assert(/transited \d\d:\d\d/.test(n), `got "${n}"`);
});

console.log(`moon.test: ${passed}/${passed + failed} passed`);
for (const f of failures) console.log("  " + f);
if (failed) {
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}

const total = passed + failed;
export default { passed, failed, total };
export { passed, failed, total };
