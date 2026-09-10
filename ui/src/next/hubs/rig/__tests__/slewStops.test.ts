// slewStops.test.ts - the two ladders the mount sheet offers, and the sentence
// that says what the deadman costs on THIS mount (D-RIG-4).
//
//   Run directly:  npx tsx src/next/hubs/rig/__tests__/slewStops.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by tsc.
//
// WHAT THIS GUARDS, and why each one has a name:
//   1. A mount that did not report a ceiling gets EXACTLY the three stops that
//      shipped. `null` means unknown, not unlimited, and the classic pad reads
//      the same table.
//   2. No stop above the ceiling, ever. A stop the server would silently clamp
//      is a control that shows one number and sends another - the defect
//      D-RIG-4 exists to end.
//   3. Every step stop is inside `parse_nudge`'s 1..600, so the picker can
//      never earn the 422 it would then have to apologise for.
//   4. Every step label round-trips to its own arcminutes. A tile reading
//      `1 deg` over a 60-arcminute value is fine; one reading `1 deg` over 600
//      is a mount 10 degrees from where the operator meant.
//
// Pure module, printed tally + the counts export (shell-and-tests.md section 4).

import { SLEW_RATES, TOUCH_MAX_RATE_DEG_S } from "../../../../lib/slewController";
import {
  MOVE_DEADMAN_MS, ceilingNote, deadmanNote, effectiveCeiling, nudgeStopLabel,
  nudgeStops, rateStopLabel, slewStops,
} from "../lib/slewStops";

// `api/mount.ts` reaches `api.ts`, which reads `window.location` at module load.
// This file has no DOM and wants none; the two-field stub is enough to import
// the wrapper for its BOUNDS, which is the whole reason it is imported here -
// the ladder holds its own copy of 1..600 and this is what stops the copies
// drifting. Dynamic, and after the stub, because static imports are hoisted.
(globalThis as { window?: unknown }).window = { location: { pathname: "/" } };
const { NUDGE_MIN_ARCMIN, NUDGE_MAX_ARCMIN } = await import("../../../../api/mount");

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

/** The inverse of `nudgeStopLabel`, written HERE and not in the module, because
 *  a round-trip graded by the module's own inverse grades nothing: both halves
 *  would be wrong together. This reads the label the way a person does. */
function arcminOfLabel(label: string): number {
  const m = /^(\d+(?:\.\d+)?)(′| deg)$/.exec(label);
  if (!m) throw new Error(`a step label nobody can read: ${JSON.stringify(label)}`);
  return m[2] === "′" ? Number(m[1]) : Number(m[1]) * 60;
}

// ------------------------------------------- 1. a mount that did not say
test("no ceiling reported means exactly the three stops that shipped", () => {
  // Vacuity guard for the whole file: if SLEW_RATES were empty every "no stop
  // above the ceiling" assertion below would hold over nothing.
  eq(SLEW_RATES.length, 3, "the shipped ladder is not three stops - the fixture is wrong");
  for (const nothing of [null, undefined]) {
    const got = slewStops(nothing);
    eq(got.length, SLEW_RATES.length, `slewStops(${String(nothing)}) changed the stop count`);
    got.forEach((s, i) => {
      eq(s.id, SLEW_RATES[i].id, "a shipped stop's id moved");
      eq(s.label, SLEW_RATES[i].label, "a shipped stop's label moved");
      eq(s.rateDegS, SLEW_RATES[i].rateDegS, "a shipped stop's rate moved");
    });
  }
});

test("a driver that answers 0 or a nonsense number falls back like the server does", () => {
  // `getattr(tel, "max_rate_deg_s", None) or TOUCH_MAX_RATE_DEG_S` - the `or`
  // treats 0.0 as "did not say". Taking 0 literally would pin the pad at zero.
  for (const junk of [0, -1, Number.NaN, Number.POSITIVE_INFINITY]) {
    eq(slewStops(junk).length, SLEW_RATES.length, `slewStops(${junk}) built a ladder from junk`);
    eq(effectiveCeiling(junk), TOUCH_MAX_RATE_DEG_S, `effectiveCeiling(${junk}) is not the fallback`);
  }
  eq(effectiveCeiling(null), TOUCH_MAX_RATE_DEG_S, "an unreported ceiling is not the 0.6 fallback");
  eq(effectiveCeiling(1.44), 1.44, "a reported ceiling was not used");
});

// --------------------------------------------- 2. the AM5N, and the bound
test("the AM5N's 1.44 deg/s ends the ladder, and nothing on it exceeds the ceiling", () => {
  const got = slewStops(1.44);
  assert(got.length > SLEW_RATES.length, "a 1.44 deg/s mount was offered no more than the shipped 0.5");
  eq(got[got.length - 1].rateDegS, 1.44, "the ladder does not end at the mount's own ceiling");
  for (const s of got) {
    assert(s.rateDegS <= 1.44,
      `a stop above the ceiling: ${s.label} is ${s.rateDegS} deg/s, the mount tops out at 1.44`);
  }
  // The three shipped stops are still there, in order, ahead of the new ones.
  SLEW_RATES.forEach((want, i) => eq(got[i].rateDegS, want.rateDegS, "a shipped stop was dropped or moved"));
  // The middle stop: half the ceiling, because 0.5 -> 1.44 in one jump is a
  // ladder with a hole in it.
  eq(got[3].rateDegS, 0.72, "no intermediate stop between the shipped top and the ceiling");
});

test("a mount slower than the shipped top is not offered the shipped top", () => {
  const got = slewStops(0.3);
  for (const s of got) assert(s.rateDegS <= 0.3, `${s.label} exceeds a 0.3 deg/s mount's ceiling`);
  eq(got[got.length - 1].rateDegS, 0.3, "a slow mount's ladder does not reach its own ceiling");
  assert(got.every((s) => s.rateDegS !== 0.5), "a 0.3 deg/s mount is still offered 0.5 deg/s");
});

test("a ceiling a thumb cannot tell from 0.5 adds no stop", () => {
  // 0.6 is the fallback itself. Offering `0.5` and `0.6` side by side teaches
  // nobody anything and makes the ladder longer for no gain.
  eq(slewStops(0.6).length, SLEW_RATES.length, "0.6 grew a stop nobody can distinguish from 0.5");
});

test("a rate label never rounds UP past the number it stands for", () => {
  // The top stop's label is read as the mount's ceiling. A label that rounds up
  // is a mount believed to be faster than it is.
  eq(rateStopLabel(1.44), "1.44 deg/s", "the ceiling label is not the mount's own number");
  eq(rateStopLabel(0.729), "0.72 deg/s", "a rate label rounded up");
  eq(rateStopLabel(1), "1 deg/s", "a whole number grew a decimal point");
});

// ------------------------------------------------ 3/4. the step ladder
test("every step stop is inside the route's own 1..600 bound", () => {
  const stops = nudgeStops();
  assert(stops.length >= 8, `the step ladder is ${stops.length} stops - too few to be the ladder`);
  for (const s of stops) {
    assert(s.arcmin >= NUDGE_MIN_ARCMIN && s.arcmin <= NUDGE_MAX_ARCMIN,
      `${s.label} is ${s.arcmin} arcmin, outside parse_nudge's ${NUDGE_MIN_ARCMIN}..${NUDGE_MAX_ARCMIN}`);
  }
  eq(NUDGE_MIN_ARCMIN, 1, "the wrapper's lower bound moved away from mount_offset.py's");
  eq(NUDGE_MAX_ARCMIN, 600, "the wrapper's upper bound moved away from mount_offset.py's");
  eq(stops[0].arcmin, NUDGE_MIN_ARCMIN, "the ladder does not start at the smallest legal nudge");
  eq(stops[stops.length - 1].arcmin, NUDGE_MAX_ARCMIN, "the ladder does not reach the largest legal nudge");
  // Strictly ascending: a dial whose stops are not ordered scrubs backwards.
  for (let i = 1; i < stops.length; i++) {
    assert(stops[i].arcmin > stops[i - 1].arcmin, `the ladder is not ascending at ${stops[i].label}`);
  }
});

test("every step label round-trips to the arcminutes it will actually send", () => {
  for (const s of nudgeStops()) {
    eq(arcminOfLabel(s.label), s.arcmin,
      `${s.label} does not stand for ${s.arcmin} arcminutes`);
  }
  eq(nudgeStopLabel(10), "10′", "an arcminute step is not labelled in arcminutes");
  eq(nudgeStopLabel(60), "1 deg", "60 arcminutes is not labelled as a degree");
  eq(nudgeStopLabel(600), "10 deg", "the top step is not labelled as 10 degrees");
});

// ------------------------------------------------------- the deadman sentence
test("the deadman sentence carries THIS mount's ceiling and its own arithmetic", () => {
  eq(MOVE_DEADMAN_MS, 1200, "the client's copy of the server deadman window drifted");
  eq(deadmanNote(1.44),
    "If the link drops mid-slew the rig stops the mount within 1.2 seconds"
    + " - at 1.44 deg/s that is up to 1.73 degrees of travel.",
    "the AM5N's worst-case travel sentence is not the one ruling 5 fixed");
  // The whole point of shipping the driver's number: the sentence is different
  // on a mount that did not report one, and 1.73 would be a promise nothing keeps.
  assert(deadmanNote(null).includes("0.72 degrees"),
    `an unreported ceiling gives the wrong worst case: ${deadmanNote(null)}`);
  assert(deadmanNote(null).includes("0.6 deg/s"), "the fallback sentence does not name 0.6 deg/s");
  assert(!/—/.test(deadmanNote(1.44)), "an em-dash reached the sheet copy");
});

test("the ceiling line says where the number came from, both ways", () => {
  assert(ceilingNote(1.44).includes("1.44"), "the reported ceiling is not named");
  assert(/did not report/.test(ceilingNote(null)),
    "an unreported ceiling is not called out as unreported - it would read as a limit the mount set");
  assert(ceilingNote(null).includes("0.6"), "the fallback line does not say what it is held at");
});

const total = passed + failed;
console.log(`slewStops.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
