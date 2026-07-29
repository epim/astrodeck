// stepDefaults.test.ts — the plan-editor authoring rules from the four-persona
// UX review (#29 inherit, #40 flat defaults, #30 silent count shrink, #1's
// editor half) plus SessionsPanel's date formatter (#35).
//
// Run directly:  npx tsx src/__tests__/stepDefaults.test.ts
import {
  BASE_STEP, FLAT_ADU_TARGET, FLAT_EXPOSURE_S, armedQualityGates,
  countShrinksSilently, defaultFilter, frameTypeDefaults, nextStep,
} from "../components/sequence/stepDefaults";
import { sessionDates } from "../components/sequence/sessionDates";
import type { ExposureStep, SequencePlan } from "../types";

// ---------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];

function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`✗ ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void {
  if (!cond) throw new Error(msg);
}
function eq<T>(a: T, b: T, msg: string): void {
  if (a !== b) throw new Error(`${msg} — got ${JSON.stringify(a)}, want ${JSON.stringify(b)}`);
}

const step = (o: Partial<ExposureStep> = {}): ExposureStep =>
  ({ ...BASE_STEP, ...o }) as ExposureStep;

const plan = (o: Partial<SequencePlan> = {}): SequencePlan =>
  ({ name: "p", targets: [], ...o }) as SequencePlan;

// ------------------------------------------------------------ defaultFilter
test("defaultFilter: no wheel => null (unchanged behaviour)", () => {
  eq(defaultFilter(undefined, undefined), null, "undefined names");
  eq(defaultFilter([], 0), null, "empty names");
});

test("defaultFilter: resolves the wheel's parked slot", () => {
  eq(defaultFilter(["L", "R", "G", "B"], 2), "G", "position 2");
  eq(defaultFilter(["L", "R"], undefined), "L", "no position => slot 0");
});

test("defaultFilter: out-of-range position falls back to slot 0, never null", () => {
  eq(defaultFilter(["L", "R"], 9), "L", "position past the end");
});

// A blackout slot is a carrier with no glass. The wheel sits parked on one for
// the whole of a dark run, so without this a step added straight afterwards
// would default to "shoot lights through the light block".
test("defaultFilter: never returns a blackout slot", () => {
  const names = ["Dark", "L", "R"];
  const opaque = [true, false, false];
  eq(defaultFilter(names, 0, opaque), "L", "parked ON the blackout slot");
  eq(defaultFilter(names, 2, opaque), "R", "parked elsewhere => unchanged");
  eq(defaultFilter(names, undefined, opaque), "L", "no position");
});

test("defaultFilter: an all-blackout wheel yields null, not a blackout name", () => {
  eq(defaultFilter(["A", "B"], 0, [true, true]), null, "nothing passes light");
});

test("defaultFilter: omitting opaque keeps the old answer exactly", () => {
  eq(defaultFilter(["Dark", "L"], 0), "Dark", "no flags => slot 0 as before");
});

test("nextStep: a first step skips a blackout slot the wheel is parked on", () => {
  eq(nextStep([], ["Dark", "L"], 0, [true, false]).filter, "L", "first step");
  // inheriting from a previous step is untouched — the user's own choice wins
  eq(nextStep([step({ filter: "Ha" })], ["Dark", "L"], 0, [true, false]).filter,
     "Ha", "inherited");
});

// ---------------------------------------------------------------- nextStep
test("#29 nextStep inherits every field of the last step", () => {
  const last = step({
    id: "s1", filter: "Ha", exposure_s: 300, gain: 200, offset: 50,
    binning: 2, count: 20, frame_type: "Light",
  });
  const n = nextStep([last], ["Ha", "OIII", "SII"], 0);
  eq(n.exposure_s, 300, "exposure inherited");
  eq(n.gain, 200, "gain inherited");
  eq(n.offset, 50, "offset inherited");
  eq(n.binning, 2, "binning inherited");
  eq(n.count, 20, "count inherited");
  eq(n.filter, "Ha", "filter inherited");
  eq(n.frame_type, "Light", "frame type inherited");
  assert(!("id" in n), "the clone must NOT carry the source step's id");
});

test("#29 nextStep inherits from the LAST step, not the first", () => {
  const n = nextStep([
    step({ filter: "Ha", exposure_s: 300 }),
    step({ filter: "OIII", exposure_s: 600 }),
  ]);
  eq(n.exposure_s, 600, "second step wins");
  eq(n.filter, "OIII", "second step wins");
});

test("#29 nextStep on an empty target falls back to BASE_STEP + wheel filter", () => {
  const n = nextStep([], ["L", "R", "G"], 1);
  eq(n.exposure_s, BASE_STEP.exposure_s, "base exposure");
  eq(n.count, BASE_STEP.count, "base count");
  eq(n.filter, "R", "wheel's parked slot, not null");
});

test("#1 (editor half) nextStep on an empty target with no wheel stays null", () => {
  eq(nextStep([], [], undefined).filter, null, "no wheel => no filter");
});

// -------------------------------------------------------- frameTypeDefaults
test("#40 switching to Flat clamps a light-length exposure and arms auto-exposure", () => {
  const p = frameTypeDefaults(step({ exposure_s: 120, frame_type: "Light" }), "Flat");
  eq(p.frame_type, "Flat", "type set");
  eq(p.exposure_s, FLAT_EXPOSURE_S, "120s against a panel is saturated");
  eq(p.adu_target, FLAT_ADU_TARGET, "auto-exposure armed");
});

test("#40 switching to Flat keeps an already-short exposure and an explicit ADU", () => {
  const p = frameTypeDefaults(step({ exposure_s: 2, adu_target: 30000 }), "Flat");
  eq(p.exposure_s, undefined, "2s is a plausible flat — left alone");
  eq(p.adu_target, undefined, "the user's own ADU target is never overwritten");
});

test("#40 Flat defaults never touch gain/binning/count the user set", () => {
  const p = frameTypeDefaults(step({ gain: 250, binning: 2, count: 40 }), "Flat");
  assert(!("gain" in p) && !("binning" in p) && !("count" in p),
    `only nonsensical fields are corrected, got ${JSON.stringify(p)}`);
});

test("Bias forces a zero exposure; Dark and Light are left alone", () => {
  eq(frameTypeDefaults(step({ exposure_s: 300 }), "Bias").exposure_s, 0, "bias");
  const d = frameTypeDefaults(step({ exposure_s: 300 }), "Dark");
  eq(d.exposure_s, undefined, "a dark MUST match its lights' exposure");
  eq(d.frame_type, "Dark", "type still set");
  eq(frameTypeDefaults(step({ exposure_s: 300 }), "Light").exposure_s, undefined, "light");
});

// -------------------------------------------------------------- count mode
test("#30 no gate armed => attempts is harmless, no warning", () => {
  eq(armedQualityGates(plan()).length, 0, "nothing armed by default");
  eq(countShrinksSilently(plan({ count_mode: "attempts" })), false, "no warning");
});

test("#30 any armed gate + attempts => the count shrinks silently", () => {
  eq(countShrinksSilently(plan({ min_stars: 40 })), true, "min stars (default mode)");
  eq(countShrinksSilently(plan({ max_guide_rms: 1.5 })), true, "guide rms");
  eq(countShrinksSilently(plan({ max_eccentricity: 0.6 })), true, "eccentricity");
  eq(countShrinksSilently(plan({ hfr_reject_factor: 1.4 })), true, "hfr spike");
});

test("#30 accepted mode is never a warning, however many gates are armed", () => {
  eq(countShrinksSilently(plan({ count_mode: "accepted", min_stars: 40, max_guide_rms: 1 })),
    false, "accepted mode is the fix");
});

test("#30 the warning names the gates that made it true", () => {
  const g = armedQualityGates(plan({ min_stars: 40, max_eccentricity: 0.6 }));
  eq(g.join(" + "), "min stars + max eccentricity", "named gates");
});

// ------------------------------------------------------------ sessionDates
test("#35 a single-night session reads as one date plus recency", () => {
  const t = 1_700_000_000;
  eq(sessionDates(t, t, t + 3600).includes("tonight"), true, "within 18h => tonight");
  eq(sessionDates(t, t, t + 86400).includes("last night"), true, "~24h => last night");
});

test("#35 a multi-night session shows its span, so two 'Tonight's differ", () => {
  const t = 1_700_000_000;
  const s = sessionDates(t, t + 3 * 86400, t + 3 * 86400 + 3600);
  eq(s.includes("→"), true, `expected a span, got ${s}`);
  const single = sessionDates(t, t, t + 3600);
  eq(single.includes("→"), false, `single night has no span, got ${single}`);
  assert(s !== single, "the two cards must not render identically");
});

test("#35 an old session falls back to its last-frame date, not a relative phrase", () => {
  const t = 1_700_000_000;
  const s = sessionDates(t, t, t + 30 * 86400);
  assert(!s.includes("tonight") && !s.includes("last night"), `stale recency in ${s}`);
});

// ------------------------------------------------------------------ report
console.log(`\nstepDefaults: ${passed} passed, ${failed} failed`);
for (const f of failures) console.log(f);
if (failed > 0) throw new Error(`${failed} test(s) failed`);
