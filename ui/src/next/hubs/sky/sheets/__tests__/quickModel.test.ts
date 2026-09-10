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
  OSC_LABEL, channelLabel, filterColor, hourStops, hoursLabel, nextExposure, oscCount,
  oscLabel, passesFor, planLine, quickRows, resolveColour, snapHours, wheelModel,
} from "../quickModel";
import { ONE_CHANNEL_FOOTER, OSC_FOOTER, oscFooter } from "../quickCopy";

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
  const w = wheelModel({ names: [] }, {}, {}, true);
  eq(w.fromRig, false, "nothing came off the rig");
  eq(w.oneChannel, true, "the sheet shows a single EXPOSURE row");
  eq(w.source, "no-wheel", "a camera and no wheel IS a one-channel rig");
  eq(w.slots.length, 0, "the seven assumed names are not offered - none of them can be shot");
});

test("a wheel reporting only unnamed slots is not this rig's wheel", () => {
  const w = wheelModel({ names: ["Slot 1", "Slot 2", ""] }, {}, {}, true);
  eq(w.fromRig, false, "unnamed slots cannot name a filter");
  // AND it must not say "no wheel": there is one, bolted to the telescope.
  eq(w.source, "unnamed-wheel", "a wheel that names nothing is still a wheel");
  eq(w.oneChannel, true, "nothing in it can be cycled");
});

test("a wheel whose every slot is opaque names nothing shootable either", () => {
  const w = wheelModel({ names: ["Dark", "Dark2"], opaque: [true, true] }, {}, {}, true);
  eq(w.source, "unnamed-wheel", "two blackout slots are not two filters");
  eq(w.slots.length, 0, "and neither may be offered");
});

// THE SPLIT THAT MAKES `ASSUMED_WHEEL_NOTE` REACHABLE. "This rig has no filter
// wheel" and "there is no rig here to ask" were one boolean, so the branch that
// renders the assumed seven could never run and the branch that did run printed
// a colour claim ("RGB") about a camera nobody had asked.
test("with no camera connected there is no rig to describe, so the seven are assumed", () => {
  const w = wheelModel({ names: undefined }, {}, {}, false);
  eq(w.source, "assumed", "a laptop planner, not a one-channel rig");
  eq(w.oneChannel, false, "so it gets the checklist, with the note over it");
  eq(w.slots.length, 7, "the assumed seven");
});

test("the rig's own wheel wins even with the camera unplugged", () => {
  const w = wheelModel(WHEEL, {}, {}, false);
  eq(w.source, "wheel", "these names came off the wheel, whatever the camera is doing");
  eq(w.slots.length, 5, "and they are still the rig's own");
});

test("one clear slot is one channel that keeps its own name", () => {
  const w = wheelModel({ names: ["L", "Dark"], opaque: [false, true] }, {}, {}, true);
  eq(w.source, "one-slot", "one usable slot");
  eq(w.oneChannel, true, "nothing to cycle");
  eq(w.slots.length, 1, "");
  eq(w.slots[0].name, "L", "the wheel's own name, never the OSC label");
});

// ------------------------------------------------------------- what it says

// THE FOUR-WAY PRECEDENCE (ruling Q7 / WAVE2-RULINGS line 19): camera.
// bayer_pattern, then camera.is_color, then preview.bayer_pattern, then
// neither. `resolveColour` is the one place this order lives - `oscLabel`
// only ever reads the answer it hands back.

test("status pattern beats everything, even a different frame pattern", () => {
  // Sabotage: read the preview first and this goes red - the pattern would
  // come back "GBRG" (the frame's) instead of "RGGB" (the status bus's).
  const c = resolveColour({ bayer_pattern: "RGGB", is_color: false }, "GBRG");
  eq(c.pattern, "RGGB", "the status pattern, not the frame's");
  eq(c.isColor, true, "a named matrix is always colour");
  eq(c.source, "status", "");
});

test("status is_color beats a frame pattern too, not just a bare bayer_pattern", () => {
  const c = resolveColour({ is_color: true }, "RGGB");
  eq(c.pattern, null, "is_color alone names no matrix");
  eq(c.isColor, true, "still one-shot colour");
  eq(c.source, "status", "the status bus answered before the frame was ever asked");
});

test("is_color: false is MONO, and beats a stale frame pattern - not re-derived from silence", () => {
  // Sabotage: drop the is_color false branch (fall through to the frame
  // check instead of returning here) and this goes red - the stale "RGGB"
  // frame would win and a mono camera would be called one-shot colour.
  const c = resolveColour({ bayer_pattern: null, is_color: false }, "RGGB");
  eq(c.isColor, false, "the rig said mono, in so many words");
  eq(c.pattern, null, "mono names no matrix");
  eq(c.source, "status", "the false itself is the answer, not the frame underneath it");
});

test("a frame pattern answers only once the status bus has said nothing at all", () => {
  const c = resolveColour({}, "RGGB");
  eq(c.pattern, "RGGB", "the only signal available is the frame's");
  eq(c.isColor, true, "");
  eq(c.source, "frame", "weaker: a fact about a sub already banked");
});

test("an engine older than S7c (no camera colour fields at all) still reads the frame", () => {
  const c = resolveColour(undefined, "RGGB");
  eq(c.source, "frame", "no status bus fields at all, so the frame is all there is");
});

test("nobody has said anything is 'none', not 'mono' - the two must not share a sentence", () => {
  const c = resolveColour({}, null);
  eq(c.pattern, null, "");
  eq(c.isColor, null, "null, not false: nobody answered, the rig did not say mono");
  eq(c.source, "none", "");
  eq(resolveColour(undefined, undefined).source, "none", "no camera and no frame is still 'none'");
});

test("blank strings do not count as an answer, from either source", () => {
  eq(resolveColour({ bayer_pattern: "   " }, "RGGB").source, "frame",
    "a whitespace-only status pattern is not a pattern");
  eq(resolveColour({}, "  ").source, "none", "nor is a whitespace-only frame pattern");
});

test("the colour claim needs a real signal; nothing else may make it", () => {
  const fromFrame = resolveColour({}, "RGGB");
  eq(oscLabel(fromFrame).title, "ONE-SHOT COLOUR - NO WHEEL", "a frame said RGGB");
  assert(oscLabel(fromFrame).sub.includes("RGGB"), "and the sub names the matrix the rig reported");
  assert(oscLabel(fromFrame).sub.includes("the last frame carried"),
    "a frame's claim is a weaker confidence than the status bus's, and says so");

  const fromStatus = resolveColour({ bayer_pattern: "RGGB" }, null);
  assert(oscLabel(fromStatus).sub.includes("the camera reports"),
    "the live status claim reads differently from a frame's");

  const none = resolveColour({}, null);
  eq(oscLabel(none).title, "ONE CHANNEL - NO WHEEL", "nobody has said colour at all");
  eq(oscLabel(resolveColour({}, undefined)).title, "ONE CHANNEL - NO WHEEL", "no frame yet is not a colour signal");
  eq(oscLabel(resolveColour({ bayer_pattern: "  " }, "  ")).title, "ONE CHANNEL - NO WHEEL",
    "blank strings from either source are not a signal");

  for (const c of [fromFrame, fromStatus, none]) {
    assert(!/\bRGB\b/.test(oscLabel(c).title), "must never print a bare RGB");
  }
});

test("is_color: true with no pattern is still one-shot colour, named to nobody", () => {
  const c = resolveColour({ is_color: true }, null);
  eq(oscLabel(c).title, "ONE-SHOT COLOUR - NO WHEEL", "a true is a colour claim on its own");
  assert(!/[A-Z]{4}/.test(oscLabel(c).sub), "and the sub must not invent a matrix to name");
});

test("is_color: false is MONO - NO WHEEL, its own sentence, not the 'nobody answered' one", () => {
  const c = resolveColour({ is_color: false }, "RGGB");
  eq(oscLabel(c).title, "MONO - NO WHEEL", "the rig said mono, so the card says mono");
  eq(oscLabel(c).sub, "the camera reports a mono sensor, so every sub is the same channel", "");
  assert(oscLabel(c).title !== oscLabel(resolveColour({}, null)).title,
    "mono and 'nobody answered' must not share a title");
});

test("a wheel that is there is never described as absent", () => {
  const one = wheelModel({ names: ["L", "Dark"], opaque: [false, true] }, {}, {}, true);
  eq(channelLabel(one, resolveColour({}, "RGGB")).title, "L - ONE SLOT", "the slot's real name, not the sensor's");
  const unnamed = wheelModel({ names: ["Slot 1"] }, {}, {}, true);
  eq(channelLabel(unnamed, resolveColour({}, null)).title, "ONE CHANNEL - NO USABLE SLOT", "");
  assert(!/NO WHEEL/.test(channelLabel(unnamed, resolveColour({}, null)).title), "there IS a wheel");
  const none = wheelModel({ names: undefined }, {}, {}, true);
  eq(channelLabel(none, resolveColour({}, "RGGB")).title, "ONE-SHOT COLOUR - NO WHEEL", "");
});

test("the OSC footer never claims a colour the rig has not made - including mono", () => {
  eq(oscFooter(resolveColour({ bayer_pattern: "RGGB" }, null)), OSC_FOOTER, "a named matrix");
  eq(oscFooter(resolveColour({ is_color: true }, null)), OSC_FOOTER, "colour, even unnamed");
  eq(oscFooter(resolveColour({ is_color: false }, "RGGB")), ONE_CHANNEL_FOOTER,
    "mono must not inherit the frame's stale colour claim");
  eq(oscFooter(resolveColour({}, null)), ONE_CHANNEL_FOOTER, "nobody has said colour at all");
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

// The same equality over the model the SHEET builds, not a hand-written slot.
// A one-slot rig wheel takes the one-channel card (which counts with `oscCount`)
// while its payload is a real filter list (which the cycle would count with
// `passesFor`). If those two ever disagreed the card would promise one night
// and the flow would shoot another.
test("a one-slot rig wheel counts the same either way it is counted", () => {
  const w = wheelModel({ names: ["L", "Dark"], opaque: [false, true] }, {}, {}, true);
  const exp = w.slots[0].exposure;
  eq(exp, 60, "L's broadband default");
  for (const h of [1, 2.5, 6]) {
    eq(oscCount(h, exp), passesFor(h, w.slots), `${h} h of one slot is one count, not two`);
  }
  eq(oscCount(6, exp), 360, "21600 / 60");
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
