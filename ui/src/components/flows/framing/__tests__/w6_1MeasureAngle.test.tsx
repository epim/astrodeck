// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w6_1MeasureAngle.test.tsx - MEASURE AT FIRST SOLVE in the Target modal
// (WP 6.1, #187): the fourth ANGLE choice, what it does not lock, and the
// run-mode line that says which angle was measured and whether the counts
// carried (ruling 3).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/framing/__tests__/w6_1MeasureAngle.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE MODEL is framingModel.ts, graded without a mount; the SECTION is
// AngleSection.tsx rendered to static markup, so what a reader sees (the
// segment, its help line, the read-only degree field, the run line) is graded
// as drawn. The sheet's wiring of the two is `measuring` in
// TargetFramingSheet.tsx and is a type-checked passthrough.
//
// NAMED MUTANTS (each from a byte backup of the file named, restored and
// sha256 compared):
//   "a grid at measure owes an angle" (framingModel `angleOwed` loses its
//   MEASURE_ANGLE line): DONE would lock on "choose one, or USE MEASURED"
//     x a grid at MEASURE AT FIRST SOLVE owes no angle: DONE is open: expected
//       null, got "a grid is laid out at one camera angle: choose one, or USE
//       MEASURED after a plate solve"
//   "restart said as carry" (measuredAngleLine ignores `carried`):
//     x a restart with subs left behind says so: expected a line ending "stay
//       on disk", got "measured 37.0 deg on 2026-11-08: counts carried over"

/* eslint-disable @typescript-eslint/no-explicit-any */

const { createElement } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const { MEASURE_ANGLE, TARGET_ANGLES } = await import("../../nodeDefs");
const M = await import("../framingModel");
import type { Params } from "../framingModel";
const { AngleSection, SEGMENT_LABEL } = await import("../sections/AngleSection");
const { targetFooter } = await import("../../targetSummary");

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: unknown, msg: string): void { if (!cond) throw new Error(msg); }
const js = (v: unknown) => JSON.stringify(v);
function eq(got: unknown, want: unknown, what: string): void {
  assert(js(got) === js(want), `${what}: expected ${js(want)}, got ${js(got)}`);
}

const BLOCK: Params = {
  name: "M31", ra: "00h 42m 44s", dec: "+41 16 09", rows: 3, cols: 2, overlap: 25,
  fovX: 2.0, fovY: 1.33, angle: MEASURE_ANGLE, rotation: -1, order: "Grid order",
  passes: 1, minVisit: 0, skip: "", panelExposure: "", fovFrom: "",
  centerTol: 1.2, centerTries: 3, ifNotCentred: "Auto",
};
const draft = (over: Params = {}) => M.draftFromParams({ ...BLOCK, ...over });

// ----------------------------------------------------------------- the model

test("MEASURE AT FIRST SOLVE is the fourth choice, by the server's word", () => {
  eq([...TARGET_ANGLES], ["Any angle", "Rotate to PA", "Camera fixed at PA",
    "Measure at the first solve"], "the choices");
  eq(MEASURE_ANGLE, "Measure at the first solve", "the word");
  eq(SEGMENT_LABEL[MEASURE_ANGLE], "MEASURE AT FIRST SOLVE", "its label");
});

test("no segment of ANGLE is locked for it, and a grid owes no angle", () => {
  const locks = M.angleLocks(draft(), { has_rotator: false } as any);
  eq(locks[MEASURE_ANGLE], null, "the measure segment's lock");
  eq(M.gridAngleLock(draft()), null,
    "a grid at MEASURE AT FIRST SOLVE owes no angle: DONE is open");
  assert(M.gridAngleLock(draft({ angle: "Any angle" })) !== null,
    "control: a grid at any angle still owes one");
});

test("the measured angle is never offered to it", () => {
  const rec = { pa_deg: 37.2, exposed_at: 1, solved_at: 1, pier_side: "west", source: "centring" } as any;
  eq(M.angleOffer(draft(), rec, null), null, "the offer");
});

test("the strip says it measures, and the last angle once there is one", () => {
  eq(M.stripAngle(draft()), "measured at the first solve", "none yet");
  eq(M.stripAngle(draft({ rotation: 37.04 })), "measured at the first solve (last 37.0 deg)", "after a night");
});

test("choosing it keeps a set angle and turns none into 0, as the other set modes do", () => {
  eq(M.setAngleMode(draft({ angle: "Any angle", rotation: -1 }), MEASURE_ANGLE).rotation, 0, "from any angle");
  eq(M.setAngleMode(draft({ angle: "Camera fixed at PA", rotation: 30 }), MEASURE_ANGLE).rotation, 30, "from a set angle");
});

const RECORD = { pa_deg: 37.0, from_pa_deg: 30.0, night: "2026-11-08", carried: false,
  max_move_deg: 0.26, threshold_deg: 0.1663, orphaned_frames: 4 };

test("a restart with subs left behind says so, with the move and the limit", () => {
  eq(M.measuredAngleLine(RECORD),
    "measured 37.0 deg on 2026-11-08: moved 15.6', more than the 10.0' this grid allows: counts restarted; 4 subs shot before stay on disk",
    "the line");
  eq(M.measuredAngleLine({ ...RECORD, orphaned_frames: 1, max_move_deg: null }),
    "measured 37.0 deg on 2026-11-08: counts restarted; 1 sub shot before stay on disk", "one sub, no move");
});

test("a carry, and a restart that lost nothing, are each said", () => {
  eq(M.measuredAngleLine({ ...RECORD, carried: true, orphaned_frames: 0 }),
    "measured 37.0 deg on 2026-11-08: counts carried over", "carried");
  eq(M.measuredAngleLine({ ...RECORD, orphaned_frames: 0 }),
    "measured 37.0 deg on 2026-11-08: nothing had been shot, so nothing restarted", "nothing banked");
});

test("no record, or an angle that is no number, is no line", () => {
  eq(M.measuredAngleLine(null), null, "null");
  eq(M.measuredAngleLine(undefined), null, "undefined");
  eq(M.measuredAngleLine({ ...RECORD, pa_deg: Number.NaN }), null, "NaN");
});

// --------------------------------------------------------------- the section

const section = (over: Record<string, unknown> = {}) => renderToStaticMarkup(createElement(AngleSection, {
  mode: MEASURE_ANGLE, rotation: "", locks: M.angleLocks(draft(), null), measured: null, offer: null,
  tolerance: null, degreeLock: M.MEASURE_HOLDS_ANGLE, onMode() {}, onRotation() {}, onNudge() {},
  onUseMeasured() {}, onOffer() {}, explain() {}, ...over,
} as any));

test("the section offers the segment, marks it chosen and shows its help line", () => {
  const html = section({ help: M.MEASURE_HELP });
  assert(html.includes("MEASURE AT FIRST SOLVE"), "the segment's label");
  assert(html.includes(`data-angle="${MEASURE_ANGLE}" data-chosen="true"`), "the segment is chosen");
  assert(html.includes('data-testid="framing-measure-help"') && html.includes(M.MEASURE_HELP), "the help line");
  assert(html.includes("The rotator is never turned."), "it says the rotator is not turned");
});

test("the degree field cannot be typed in while the run owns the angle", () => {
  const html = section({ help: M.MEASURE_HELP });
  const field = /<input[^>]*id="tfs-angle-deg"[^>]*>/.exec(html)?.[0] ?? "";
  assert(field.includes('readonly=""') && field.includes('aria-readonly="true"'), `the degree field is read-only: ${field}`);
  assert(html.includes(M.MEASURE_HOLDS_ANGLE), "the reason is on the field");
});

test("the run line is drawn when there is one, and not otherwise", () => {
  const line = M.measuredAngleLine(RECORD)!;
  assert(section({ runLine: line }).includes('data-testid="framing-measured-line"'), "the run line");
  assert(section({ runLine: line }).includes("counts restarted"), "its words");
  assert(!section({ runLine: null }).includes("framing-measured-line"), "a line with nothing measured");
  assert(!section({ mode: "Camera fixed at PA", help: null, runLine: null, degreeLock: null })
    .includes("framing-measure-help"), "help outside the mode");
});

// ------------------------------------------------------------------ the card

const node = (params: Record<string, string | number>) =>
  ({ id: "t", type: "target", x: 0, y: 0, params: { name: "M31", ...params } }) as any;

test("the card says the block measures its angle, and the last one once it has", () => {
  const grid = { rows: 3, cols: 2, overlap: 25 };
  const none = targetFooter(node({ ...grid, angle: MEASURE_ANGLE, rotation: -1 }), true);
  assert(none.includes("measures its angle") && !none.includes("any angle"), `no angle yet: ${none}`);
  const lasted = targetFooter(node({ ...grid, angle: MEASURE_ANGLE, rotation: 37.04 }), true);
  assert(lasted.includes("measured, last PA 37.0"), `after a night: ${lasted}`);
  const one = targetFooter(node({ angle: MEASURE_ANGLE, rotation: -1 }), false);
  assert(one.includes("measures its angle"), `a single block: ${one}`);
});

const total = passed + failed;
console.log(`w6_1MeasureAngle.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) {
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}

export default { passed, failed, total };
export { passed, failed, total };
