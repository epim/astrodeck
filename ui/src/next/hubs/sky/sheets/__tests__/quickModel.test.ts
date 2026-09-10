// quickModel.test.ts - the quick-session sheet's arithmetic, on its own.
//
//   Run directly:  npx tsx src/next/hubs/sky/sheets/__tests__/quickModel.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE ONE NUMBER THAT MATTERS is `passesFor`. It is what `subs` is posted as,
// and `subs` is what the engine shoots. Everything else here guards the two ways
// that number can be quietly wrong: a blackout slot counted as a filter (the
// pass gets longer, the count drops, and the wheel spends the night rotating to
// a piece of metal), and an unchecked slot still contributing its exposure.

import {
  OSC_LABEL, filterColor, hourStops, hoursLabel, nextExposure, oscCount, passesFor,
  planLine, quickRows, snapHours, wheelModel,
} from "../quickModel";

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

// The rig's own wheel, with a blackout slot in it. `Dark` is opaque: it holds no
// glass, and a "filter" that blocks the light path is not one.
const WHEEL = {
  names: ["L", "R", "G", "B", "Ha", "Dark"],
  opaque: [false, false, false, false, false, true],
  narrowband: [false, false, false, false, true, false],
  exposures: [60, 60, 60, 60, 180, null] as (number | null)[],
};

// ---------------------------------------------------------------- the wheel

test("the wheel model is the rig's, and the opaque slot is not in it", () => {
  const w = wheelModel(WHEEL, {}, {});
  eq(w.fromRig, true, "these names came off the rig");
  eq(w.slots.length, 5, "five usable slots");
  eq(w.slots.map((s) => s.name).join(","), "L,R,G,B,Ha", "wheel order, minus the blackout slot");
  assert(!w.slots.some((s) => s.name === "Dark"), "the blackout slot must not be offered");
  eq(w.oneChannel, false, "five slots is not one channel");
});

test("exposures come from the rig's pinned defaults, matched by NAME not index", () => {
  const w = wheelModel(WHEEL, {}, {});
  eq(w.slots.find((s) => s.name === "Ha")?.exposure, 180, "Ha is the narrowband default");
  eq(w.slots.find((s) => s.name === "L")?.exposure, 60, "L is the broadband default");
  // The index trap: `slots` has had the opaque entry removed, so slot 4 of the
  // usable list is not slot 4 of the carousel.
  eq(w.slots[4].name, "Ha", "the fifth usable slot is Ha");
});

test("no wheel at all is one channel, and its names are flagged as assumed", () => {
  const w = wheelModel({ names: [] }, {}, {});
  eq(w.fromRig, false, "nothing came off the rig");
  eq(w.oneChannel, true, "the sheet shows a single EXPOSURE row");
});

test("a wheel reporting only unnamed slots is not this rig's wheel", () => {
  const w = wheelModel({ names: ["Slot 1", "Slot 2", ""] }, {}, {});
  eq(w.fromRig, false, "unnamed slots cannot name a filter");
});

test("the operator's own ticks and exposures beat the defaults", () => {
  const w = wheelModel(WHEEL, { Ha: false }, { Ha: 300 });
  const ha = w.slots.find((s) => s.name === "Ha");
  eq(ha?.checked, false, "unticked stays unticked");
  eq(ha?.exposure, 300, "and keeps the exposure that was chosen for it");
});

// -------------------------------------------------------------- the passes

test("six hours of L/R/G/B at 60 s and Ha at 180 s is 51 passes", () => {
  const w = wheelModel(WHEEL, {}, {});
  // 4 x 60 + 180 = 420 s per pass; 6 h = 21600 s; floor(21600 / 420) = 51.
  eq(passesFor(6, w.slots), 51, "passes that fit the window");
});

test("every checked row shows the SAME count - one sub per filter per pass", () => {
  const w = wheelModel(WHEEL, {}, {});
  const rows = quickRows(6, w.slots);
  eq(rows.length, 5, "one row per usable slot");
  for (const r of rows) eq(r.count, 51, `${r.name} gets one sub per pass`);
  eq(rows.find((r) => r.name === "Ha")?.totalS, 51 * 180, "Ha banks its own exposure x passes");
  eq(rows.find((r) => r.name === "L")?.totalS, 51 * 60, "L banks less time for the same count");
});

test("an unticked slot contributes nothing to the pass and gets no count", () => {
  const w = wheelModel(WHEEL, { Ha: false }, {});
  // 4 x 60 = 240 s per pass; floor(21600 / 240) = 90.
  eq(passesFor(6, w.slots), 90, "dropping Ha lengthens the count, not the pass");
  const rows = quickRows(6, w.slots);
  eq(rows.find((r) => r.name === "Ha")?.count, 0, "an unticked filter shoots nothing");
  eq(rows.find((r) => r.name === "L")?.count, 90, "the ticked ones share the whole window");
});

test("nothing ticked is zero passes, not a division by zero", () => {
  const w = wheelModel(WHEEL, { L: false, R: false, G: false, B: false, Ha: false }, {});
  eq(passesFor(6, w.slots), 0, "no filters, no passes");
  eq(passesFor(0, w.slots), 0, "no window, no passes");
});

test("a pass longer than the whole window is zero, which the CTA then refuses", () => {
  const slots = [{ name: "Ha", exposure: 1800, narrowband: true, checked: true }];
  eq(passesFor(0.25, slots), 0, "one 30-minute sub does not fit a 15-minute window");
});

test("the one-channel branch and passesFor agree exactly", () => {
  const one = [{ name: OSC_LABEL, exposure: 120, narrowband: false, checked: true }];
  eq(oscCount(6, 120), passesFor(6, one), "a single filter IS one sub per pass");
  eq(oscCount(6, 120), 180, "21600 / 120");
});

// ------------------------------------------------------------- the window

test("the hour stops end at dawn, and stops past dawn are dropped", () => {
  eq(hourStops(3.5).join(","), "1,2,3,3.5", "1 2 3 then dawn");
  eq(hourStops(null).join(","), "1,2,3,4", "with no dawn the four fixed stops stand");
});

test("a dragged duration snaps to a stop, so drag and tap agree", () => {
  eq(snapHours(2.4, 6), 2, "nearer 2 than 3");
  eq(snapHours(5.9, 6), 6, "the dawn stop");
});

test("the last stop reads 'until dawn', not a number", () => {
  eq(hoursLabel(6, 6), "until dawn", "at the dawn stop");
  eq(hoursLabel(6, null), "6h 00m", "and a zero-padded minute otherwise");
  eq(hoursLabel(2, null), "2h 00m", "the design's own capture reads 6h 00m, not 6h");
  eq(hoursLabel(0.5, null), "30 min", "under an hour");
});

// ---------------------------------------------------------------- the CTA

test("with nothing ticked the CTA says what is missing", () => {
  eq(
    planLine({
      oneChannel: false, checkedCount: 0, oscExposure: 120, oscCount: 0,
      hoursLabel: "2h 00m", poolCount: 0, panels: 0,
    }),
    "pick a filter · 2h 00m",
    "the label names the gap",
  );
});

test("the CTA counts filters, targets and panels", () => {
  eq(
    planLine({
      oneChannel: false, checkedCount: 4, oscExposure: 0, oscCount: 0,
      hoursLabel: "6h 00m", poolCount: 0, panels: 0,
    }),
    "4 filters · 6h 00m",
    "the design's own capture",
  );
  eq(
    planLine({
      oneChannel: false, checkedCount: 4, oscExposure: 0, oscCount: 0,
      hoursLabel: "6h 00m", poolCount: 3, panels: 0,
    }),
    "4 filters · 6h 00m · 3 targets",
    "a pool says how many",
  );
  eq(
    planLine({
      oneChannel: true, checkedCount: 0, oscExposure: 120, oscCount: 180,
      hoursLabel: "6h 00m", poolCount: 0, panels: 2,
    }),
    "120s × 180 · 6h 00m · 2 panels",
    "one channel, framed as a mosaic",
  );
});

// -------------------------------------------------------------- the extras

test("the exposure button cycles the five design values and wraps", () => {
  eq(nextExposure(30), 60, "");
  eq(nextExposure(300), 30, "wraps");
  eq(nextExposure(45), 30, "an unrecognised value restarts the cycle");
});

test("an unknown filter name gets the neutral colour, never a guessed hue", () => {
  eq(filterColor("L"), "var(--nx-filter-L)", "");
  eq(filterColor("Oiii"), "var(--nx-filter-OIII)", "case and spelling differ across wheels");
  eq(filterColor("S2"), "var(--nx-filter-SII)", "");
  assert(!filterColor("Clear").startsWith("var("), "an unknown slot must not borrow L's white");
});

const total = passed + failed;
console.log(`quickModel.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) {
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}

export default { passed, failed, total };
export { passed, failed, total };
