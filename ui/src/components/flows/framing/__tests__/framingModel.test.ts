// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// framingModel.test.ts - the Target modal's pure model held to the spec
// (#189 S4 item 1; spec 2026-09-23 flows mosaic, 2.3-2.5, 2.7, 3.1, 3.2, A.2).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/framing/__tests__/framingModel.test.ts   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE SKIP CASES ARE server/tests/fixtures/skip_cases.json, READ, NOT COPIED.
// test_flows_skip_fixture.py grades compile.py `parse_skip` against the same
// file, so the modal's reading of a skip list and the run's are held to one
// table: a panel the modal draws as live is a panel the run shoots.
//
// THE COERCION IS HELD AGAINST THE REAL SLICE ACTION. `coerceParam` copies
// `flowsSetParam`'s rule; the parity test below drives `flowsSetParam` itself
// through the same miniature store flowsSlice.test.ts uses and compares the
// stored values, so the copy cannot drift from the rule it copies.
//
// Every mutant named below was run in a private scratch copy of ui/, never in
// the shared tree (#254), and the failure it produced is quoted verbatim:
// wrapped at spaces, and with each non-ASCII character written <U+XXXX> so
// this file stays ASCII (the strip's middle dot is <U+00B7>). Each quote is
// the failing line of the test it sits on; a mutant that failed other tests
// too is quoted where it was aimed. S5's mutants (#408, #411, #413) were run
// in the scratch copy s5-modal-mut (2026-09-28).

/* eslint-disable @typescript-eslint/no-explicit-any */

// @ts-ignore  node built-ins; tsx supplies them at runtime
import { readFileSync } from "node:fs";

import {
  ANY_ANGLE_ON_A_GRID, NO_ANGLE_ON_A_GRID, NO_OPTICS, NO_ROTATOR, OFFLINE_MIRROR, SERVER_DECIDES,
  VIEW_PREFS_DEFAULT, WAITING_FOR_PANELS,
  angleLocks, angleOffer, bankedSubs, cameraFieldLine, coerceParam, doneState, draftCentre,
  draftFromParams, driftBanner, formatSkip, framingPatch, gridAngleLock, gridLock, hopDuration,
  layoutKey, loadViewPrefs, matchCamera, matchCameraLock, moveLine, mosaicRequest,
  panelDrawState, parseDecDeg, parseRaHours, parseSkip, readoutStrip, reframeDecision, requestKey,
  runLines, runPanelsOf, saveViewPrefs, setAngleMode, stripAngle, stripRa, suggestAxis, suggestGrid,
  takeOffer, toggleSkip, toleranceLine, useMeasured, useMeasuredLine, viewPrefsKey,
} from "../framingModel";
import type {
  FramingDraft, MeasuredAngle, MosaicRequest, PanelAnswer, Params, ReframeAnswer, RigBlock,
  RunReadouts, ServerView, StorageLike,
} from "../framingModel";
import type { SequenceState, SkyAngleRecord } from "../../../../types";

const proc = (globalThis as any).process;

// ------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
const js = (v: unknown) => JSON.stringify(v);
/** A value as a failure should print it: JSON writes an infinity or NaN as
 *  null, which would hide the very value #358 is about. */
const shown = (v: unknown) => (typeof v === "number" && !Number.isFinite(v) ? String(v) : js(v));
function eq(got: unknown, want: unknown, what: string): void {
  assert(js(got) === js(want), `${what}: expected ${js(want)}, got ${js(got)}`);
}
const cp = (...codes: number[]) => String.fromCodePoint(...codes);

// ------------------------------------------------------------- fixtures

/** The reference block of the spec's worked numbers: M31, 3 columns by 2
 *  rows of a 2.00 x 1.33 deg camera at 25%, laid out at PA 30. */
const M31: Params = {
  name: "M31", ra: "00h 42m 44s", dec: "+41" + cp(0xb0) + " 16" + cp(0x2032) + " 09" + cp(0x2033),
  rotation: 30, angle: "Rotate to PA", rows: 2, cols: 3, overlap: 25,
  fovX: 2.0, fovY: 1.33, fovFrom: "profile Refractor, matched 2026-09-23", skip: "",
  frameAnchor: '{"ra_hours":0.712222}',
};
const draftOf = (p: Params, over: Partial<FramingDraft> = {}): FramingDraft =>
  ({ ...draftFromParams(p), ...over });

/** The route's `rig` block (`rig_readout`), every key it answers: the
 *  shape test below holds these keys to the route's recorded answer. */
const RIG: Required<RigBlock> = {
  fov_deg: [2.0, 1.33], fov_from: "profile Refractor, matched 2026-09-26",
  has_rotator: true, hop_s: null, hop_samples: 0, hop_measured: false,
};

/** A route answer for exactly this draft's request. */
function answerFor(d: FramingDraft, stored: Params, extra: Partial<PanelAnswer> = {}): PanelAnswer {
  const req = mosaicRequest(d, stored.frameAnchor);
  if (req === null) throw new Error("the fixture draft makes no request");
  return { key: requestKey(req), ok: true, ...extra };
}
function viewOf(d: FramingDraft, stored: Params, answer: PanelAnswer | null, o: Partial<ServerView> = {}): ServerView {
  return { request: mosaicRequest(d, stored.frameAnchor), answer, offline: false, canViewSiteDerived: true, ...o };
}
const MOVE_14_8: ReframeAnswer = { carry: false, max_move_deg: 14.8 / 60, threshold_deg: 9.98 / 60, reason: "move" };
const MOVE_4_2: ReframeAnswer = { carry: true, max_move_deg: 4.2 / 60, threshold_deg: 9.98 / 60, reason: "move" };

// ================================================= the draft and the patch

// MUTANT "angle not derived" (draftFromParams: `effective` loses its angle branch, so `angle`,
// which has no default, reads undefined). Observed:
//   x a draft reads what the node MEANS: missing keys as their defaults, the angle derived: angle:
//     expected "Rotate to PA", got undefined
// MUTANT "draft keys include counts" (DRAFT_KEYS gains "counts"). Observed:
//   x a draft reads what the node MEANS: missing keys as their defaults, the angle derived: counts
//     and frameAnchor are not the modal's to write
test("a draft reads what the node MEANS: missing keys as their defaults, the angle derived", () => {
  const d = draftFromParams({ name: "NGC 7331", ra: "22h 37m 04s", dec: "+34 24 56", rotation: 12.5 });
  eq(d.angle, "Rotate to PA", "angle");
  eq([d.rows, d.cols, d.overlap, d.fovX, d.skip, d.passes, d.order],
    [1, 1, 25, 0, "", 1, "Least complete first"], "missing-key defaults");
  eq(draftFromParams({ rotation: -1 }).angle, "Any angle", "a negative rotation is any angle");
  assert(!("counts" in d) && !("frameAnchor" in d), "counts and frameAnchor are not the modal's to write");
});

// MUTANT "patch not minimal" (framingPatch: the `!==` test deleted, so every key is written).
// Observed:
//   x an untouched draft is an empty patch, so DONE on it writes nothing: untouched patch:
//     expected {}, got {"name":"M31","ra":"00h 42m 44s","dec":"+41<U+00B0> 16<U+2032>
//     09<U+2033>","rows":2,"cols":3,"overlap":25,"fovX":2,"fovY":1.33,"fovFrom":"profile
//     Refractor, matched 2026-09-23","angle":"Rotate to
//     PA","rotation":30,"skip":"","passes":1,"minVisit":0,"order":"Least complete
//     first","centerTol":1.2,"centerTries":3,"ifNotCentred":"Auto"}
test("an untouched draft is an empty patch, so DONE on it writes nothing", () => {
  eq(framingPatch(M31, draftFromParams(M31)), {}, "untouched patch");
  // A key the node never carried is not written for showing its default.
  const bare: Params = { name: "M 33", ra: "01h 33m 51s", dec: "+30 39 37" };
  eq(framingPatch(bare, draftFromParams(bare)), {}, "a bare node's untouched patch");
  // A hand-edited file that stores a number as text means the same number,
  // so the untouched draft still writes nothing: the comparison is between
  // two COERCED values, never the coerced draft against the raw stored text.
  // MUTANT "patch compares raw effective" (framingPatch: `next !==
  // effective(params, key)`, the stored value uncoerced). Observed:
  //   x an untouched draft is an empty patch, so DONE on it writes nothing: numbers stored as text:
  //     expected {}, got {"rows":2,"overlap":25}
  const texty: Params = { ...M31, rows: "2", overlap: "25" };
  eq(framingPatch(texty, draftFromParams(texty)), {}, "numbers stored as text");
});

// MUTANT "coercion to string" (coerceParam: `return text` for a numeric default, so the patch
// writes "35" for a number). Observed:
//   x the patch coerces by the default's type, as flowsSetParam does: overlap typed "35": expected
//     35 (number), got "35" (string)
test("the patch coerces by the default's type, as flowsSetParam does", () => {
  const p = framingPatch(M31, draftOf(M31, { overlap: "35", rows: "3", name: "M31 core" }));
  assert(p.overlap === 35 && typeof p.overlap === "number",
    `overlap typed "35": expected 35 (number), got ${js(p.overlap)} (${typeof p.overlap})`);
  assert(p.rows === 3 && typeof p.rows === "number", `rows typed "3": got ${js(p.rows)}`);
  eq(p.name, "M31 core", "a text field stores its text");
  // Unparseable input reverts to the MISSING-KEY default (rows 1), not NaN.
  eq(framingPatch(M31, draftOf(M31, { rows: "three" })).rows, 1, "rows typed 'three'");
  // parseFloat's leniency is kept: the inspector stores 12 for "12abc".
  eq(framingPatch(M31, draftOf(M31, { passes: "12abc" })).passes, 12, "passes typed '12abc'");
  // Only what changed: the one edited key, nothing else.
  eq(Object.keys(framingPatch(M31, draftOf(M31, { overlap: "35" }))), ["overlap"], "keys written");
});

// Top-level await: the slice imports lib/base, which reads window.location at
// module scope, so the window exists before the import (flowsSlice.test.ts).
(globalThis as any).window = { location: { pathname: "/", origin: "http://local" } };
const { createFlowsActions, FLOWS_INIT } = await import("../../flowsSlice");

// MUTANT "coercion to string" (the same mutant, graded against the real slice action). Observed
// (re-run in scratch copy S7-MODAL-mut when the infinities joined the table):
//   x coerceParam stores exactly what flowsSetParam stores: overlap "25": slice 25, model "25";
//     overlap "abc": slice 25, model "abc"; overlap " 7 ": slice 7, model " 7 "; overlap "": slice
//     25, model ""; overlap "12abc": slice 12, model "12abc"; overlap "1e1": slice 10, model "1e1";
//     rows "3": slice 3, model "3"; rows "2.5": slice 2.5, model "2.5"; fovX "2.0": slice 2, model
//     "2.0"; rotation "-1": slice -1, model "-1"; overlap "Infinity": slice 25, model "Infinity";
//     overlap "-Infinity": slice 25, model "-Infinity"; overlap "1e999": slice 25, model "1e999";
//     rows "1e999": slice 1, model "1e999"; rotation "-Infinity": slice -1, model "-Infinity"
//
// The infinities are #358's rows. `parseFloat` reads "Infinity", "-Infinity"
// and "1e999" as an infinity, which is not NaN; the store keeps only a FINITE
// number since S5, and the modal's copy kept anything but NaN until S7, so
// the modal's patch held an infinity the store then wrote as the default
// (and `framedGraph` sent the draft compile null for it). A text default
// takes "Infinity" as the text it is: a TARGET may be named that.
//
// MUTANT "Number.isNaN" (framingModel.ts coerceParam: `return
// Number.isFinite(n) ? n : base` put back to `return Number.isNaN(n) ? base :
// n`). Observed (scratch copy S7-MODAL-mut; framingModel.test 49/50, and
// coerceParamFinite.test goes red with it too, 7/9; flowsSlice.test,
// flowsApplyFraming.test and typedCoordinatesFixture.test stay green):
//   x coerceParam stores exactly what flowsSetParam stores: overlap "Infinity": slice 25, model
//     Infinity; overlap "-Infinity": slice 25, model -Infinity; overlap "1e999": slice 25, model
//     Infinity; rows "1e999": slice 1, model Infinity; rotation "-Infinity": slice -1, model
//     -Infinity
// The table grades the store too, now that it holds the infinities.
// MUTANT "Number.isNaN restored" (flowsSlice.ts coerceParam: `return
// Number.isFinite(v) ? v : base` put back to `return Number.isNaN(v) ? base :
// v`, coerceParamFinite.test.ts's mutant). Observed (scratch copy
// S7-MODAL-mut; framingModel.test 49/50):
//   x coerceParam stores exactly what flowsSetParam stores: overlap "Infinity": slice Infinity,
//     model 25; overlap "-Infinity": slice -Infinity, model 25; overlap "1e999": slice Infinity,
//     model 25; rows "1e999": slice Infinity, model 1; rotation "-Infinity": slice -Infinity, model
//     -1
test("coerceParam stores exactly what flowsSetParam stores", () => {
  const cases: [string, string][] = [
    ["overlap", "25"], ["overlap", "abc"], ["overlap", " 7 "], ["overlap", ""], ["overlap", "12abc"],
    ["overlap", "1e1"], ["rows", "3"], ["rows", "2.5"], ["fovX", "2.0"], ["rotation", "-1"],
    ["name", "M 33"], ["skip", "3-1, 3-2"], ["angle", "Camera fixed at PA"], ["order", "Grid order"],
    ["overlap", "Infinity"], ["overlap", "-Infinity"], ["overlap", "1e999"], ["rows", "1e999"],
    ["rotation", "-Infinity"], ["name", "Infinity"],
  ];
  const bad: string[] = [];
  for (const [key, raw] of cases) {
    let state: any;
    const set = (fn: (s: any) => any) => { state = { ...state, ...fn(state) }; };
    const actions = createFlowsActions(set as any, () => state);
    state = { ...actions, flows: { ...FLOWS_INIT, graph: { nodes: [{ id: "t", type: "target", x: 0, y: 0, params: { ...M31 } }], edges: [] } } };
    state.flowsSetParam("t", key, raw);
    const slice = state.flows.graph.nodes[0].params[key];
    const model = coerceParam(key, raw);
    if (slice !== model) bad.push(`${key} ${js(raw)}: slice ${shown(slice)}, model ${shown(model)}`);
  }
  assert(bad.length === 0, bad.join("; "));
});

// MUTANT "any angle keeps its PA" (setAngleMode: the Any branch drops `rotation: -1`). Observed:
//   x ANY ANGLE writes rotation -1; a set mode turns none into north up: any: expected
//     {"angle":"Any angle","rotation":-1}, got {"angle":"Any angle"}
test("ANY ANGLE writes rotation -1; a set mode turns none into north up", () => {
  const stored: Params = { ...M31, rows: 1, cols: 1 };
  delete stored.angle;       // a block saved before S3: angle derived from rotation
  eq(framingPatch(stored, setAngleMode(draftFromParams(stored), "Any angle")),
    { angle: "Any angle", rotation: -1 }, "any");
  const anyDraft = draftFromParams({ rotation: -1 });
  eq(setAngleMode(anyDraft, "Camera fixed at PA").rotation, 0, "fixed from none");
  eq(setAngleMode(draftOf(M31), "Camera fixed at PA").rotation, 30, "fixed keeps a set PA");
});

// ============================================================ SUGGEST GRID

// MUTANT "floor for ceil" (suggestAxis: Math.floor for Math.ceil). Observed:
//   x SUGGEST GRID is ceil((size - fov*ov) / (fov*(1-ov))), held to 1..10: M31 178' on 2.00 x 1.33
//     at 25%: expected {"cols":2,"rows":3}, got {"cols":1,"rows":2}
// MUTANT "no epsilon" (suggestAxis: the `- 1e-9` deleted). Observed:
//   x SUGGEST GRID is ceil((size - fov*ov) / (fov*(1-ov))), held to 1..10: an exact fit of five
//     1.33 deg rows: expected 5, got 6
// MUTANT "suggest unclamped" (suggestAxis: the 1..GRID_MAX clamp deleted). Observed:
//   x SUGGEST GRID is ceil((size - fov*ov) / (fov*(1-ov))), held to 1..10: an object smaller than
//     one frame: expected 1, got 0
test("SUGGEST GRID is ceil((size - fov*ov) / (fov*(1-ov))), held to 1..10", () => {
  // Worked by hand: cols (2.967 - 0.5) / 1.5 = 1.64 -> 2; rows (2.967 -
  // 0.3325) / 0.9975 = 2.64 -> 3.
  eq(suggestGrid(178, draftOf(M31)), { cols: 2, rows: 3 }, "M31 178' on 2.00 x 1.33 at 25%");
  // 1.33 x (5 - 4 x 0.25) = 5.32 deg is exactly five rows; the float division
  // gives 5.000000000000001.
  eq(suggestAxis(5.32, 1.33, 0.25), 5, "an exact fit of five 1.33 deg rows");
  eq(suggestAxis(0.2, 2.0, 0.25), 1, "an object smaller than one frame");
  eq(suggestAxis(90, 2.0, 0.25), 10, "an object past ten frames");
  eq(suggestGrid(178, draftOf(M31, { fovX: 0 })), null, "no field, no suggestion");
  eq(suggestAxis(0, 2.0, 0.25), null, "no size, no suggestion");
});

// ================================================================= locks

// MUTANT "ANY ANGLE unlocked on a grid" (angleLocks: the Any entry always null). Observed:
//   x ANY ANGLE locks on a grid larger than 1x1, with its reason: 3x2: expected "a grid is laid
//     out at one camera angle", got null
// MUTANT "grid reads 2.5 as 2" (gridDim: `v >= 1 ? Math.floor(v) : 1`, not compile.py's
// whole-number rule). Observed:
//   x ANY ANGLE locks on a grid larger than 1x1, with its reason: rows 2.5 reads as 1: expected
//     null, got "a grid is laid out at one camera angle"
test("ANY ANGLE locks on a grid larger than 1x1, with its reason", () => {
  eq(angleLocks(draftOf(M31), RIG)["Any angle"], ANY_ANGLE_ON_A_GRID, "3x2");
  eq(angleLocks(draftOf(M31, { rows: 1, cols: 2 }), RIG)["Any angle"], ANY_ANGLE_ON_A_GRID, "1x2");
  eq(angleLocks(draftOf(M31, { rows: 1, cols: 1 }), RIG)["Any angle"], null, "1x1 (control)");
  // The compile's grid reading: rows 2.5 is one row, so 2.5 x 1 is one panel.
  eq(angleLocks(draftOf(M31, { rows: "2.5", cols: 1 }), RIG)["Any angle"], null, "rows 2.5 reads as 1");
  eq(ANY_ANGLE_ON_A_GRID, "a grid is laid out at one camera angle", "the spec's words");
});

// MUTANT "unknown rotator locks" (angleLocks: `=== false` made `!rig?.has_rotator`). Observed:
//   x ROTATE TO locks only when the rig says there is no rotator: unknown rotator: expected null,
//     got "the active profile has no rotator: set the camera's angle by hand and choose CAMERA
//     FIXED AT"
test("ROTATE TO locks only when the rig says there is no rotator", () => {
  eq(angleLocks(draftOf(M31), { ...RIG, has_rotator: false })["Rotate to PA"], NO_ROTATOR, "no rotator");
  eq(angleLocks(draftOf(M31), RIG)["Rotate to PA"], null, "a rotator (control)");
  eq(angleLocks(draftOf(M31), { ...RIG, has_rotator: null })["Rotate to PA"], null, "unknown rotator");
  eq(angleLocks(draftOf(M31), null)["Rotate to PA"], null, "no rig block");
  eq(angleLocks(draftOf(M31), { ...RIG, has_rotator: false })["Camera fixed at PA"], null, "fixed never locks");
});

// MUTANT "grid lock ignores the snapshot" (gridLock: `hasField(draft) ||` deleted). Observed:
//   x the grid locks with the Settings sentence only when there is no field to tile with:
//     snapshot, rig away: expected null, got "set the camera and focal length in Settings >
//     Optics"
test("the grid locks with the Settings sentence only when there is no field to tile with", () => {
  const noRig: RigBlock = { ...RIG, fov_deg: null };
  eq(gridLock(draftOf(M31, { fovX: 0, fovY: 0 }), noRig), NO_OPTICS, "no snapshot, no optics");
  eq(gridLock(draftOf(M31, { fovX: 0, fovY: 0 }), null), NO_OPTICS, "no snapshot, no rig block");
  eq(gridLock(draftOf(M31), noRig), null, "snapshot, rig away");
  eq(gridLock(draftOf(M31, { fovX: 0, fovY: 0 }), RIG), null, "live optics to match");
  eq(NO_OPTICS, "set the camera and focal length in Settings > Optics", "the spec's words");
  eq(matchCameraLock(noRig), NO_OPTICS, "MATCH CAMERA with nothing to match");
  eq(matchCameraLock({ ...RIG, fov_deg: [0, 0] }), NO_OPTICS, "a 0 x 0 field is no field");
  eq(matchCameraLock(RIG), null, "MATCH CAMERA with optics");
});

// MUTANT "match writes no provenance" (matchCamera: fovFrom left as it was). Observed:
//   x MATCH CAMERA snapshots the live bin-1 field and says where it came from: snapshot: expected
//     [2.1,1.4,"profile Refractor, matched 2026-09-26"], got [2.1,1.4,"profile Refractor, matched
//     2026-09-23"]
test("MATCH CAMERA snapshots the live bin-1 field and says where it came from", () => {
  const m = matchCamera(draftOf(M31), { ...RIG, fov_deg: [2.1, 1.4] });
  eq([m.fovX, m.fovY, m.fovFrom], [2.1, 1.4, "profile Refractor, matched 2026-09-26"], "snapshot");
  const same = draftOf(M31);
  assert(matchCamera(same, { ...RIG, fov_deg: null }) === same, "no optics leaves the draft alone");
});

// ============================================================ DONE (2.5)

// MUTANT "any answer unlocks DONE" (currentAnswer: the key comparison deleted, so a stale spec's
// answer counts). Observed:
//   x DONE stays locked until an answer for the CURRENT spec arrives: stale answer: expected
//     {"locked":true,"reason":"waiting for the server's panel positions","chip":null}, got
//     {"locked":false,"reason":null,"chip":null}
test("DONE stays locked until an answer for the CURRENT spec arrives", () => {
  const before = draftOf(M31);
  const after = draftOf(M31, { overlap: 35 });
  const stale = answerFor(before, M31);
  eq(doneState(viewOf(after, M31, null)), { locked: true, reason: WAITING_FOR_PANELS, chip: null }, "no answer yet");
  eq(doneState(viewOf(after, M31, stale)), { locked: true, reason: WAITING_FOR_PANELS, chip: null }, "stale answer");
  eq(doneState(viewOf(after, M31, answerFor(after, M31))), { locked: false, reason: null, chip: null }, "current answer (control)");
  eq(WAITING_FOR_PANELS, "waiting for the server's panel positions", "the spec's words");
});

// MUTANT "offline waits" (doneState: the unreachable-server branch deleted). Observed:
//   x offline, as a viewer, or after a failed request, DONE opens on the mirror and says so:
//     offline: expected {"locked":false,"reason":null,"chip":"panels from the offline mirror; the
//     run computes them on the server"}, got {"locked":true,"reason":"waiting for the server's
//     panel positions","chip":null}
// MUTANT "failed answer unlocks silently" (doneState: a failed answer opens DONE with no chip).
// Observed:
//   x offline, as a viewer, or after a failed request, DONE opens on the mirror and says so:
//     failed request for this spec: expected {"locked":false,"reason":null,"chip":"panels from the
//     offline mirror; the run computes them on the server"}, got
//     {"locked":false,"reason":null,"chip":null}
test("offline, as a viewer, or after a failed request, DONE opens on the mirror and says so", () => {
  const d = draftOf(M31);
  const mirror = { locked: false, reason: null, chip: OFFLINE_MIRROR };
  eq(doneState(viewOf(d, M31, null, { offline: true })), mirror, "offline");
  eq(doneState(viewOf(d, M31, null, { canViewSiteDerived: false })), mirror, "viewer");
  eq(doneState(viewOf(d, M31, answerFor(d, M31, { ok: false }))), mirror, "failed request for this spec");
  eq(OFFLINE_MIRROR, "panels from the offline mirror; the run computes them on the server", "the spec's words");
});

// MUTANT "skip in the request key" (mosaicRequest and requestKey carry the skip text). Observed:
//   x a skip toggle keeps DONE open: skip is not part of the layout the server answers: after a
//     skip: expected {"locked":false,"reason":null,"chip":null}, got
//     {"locked":true,"reason":"waiting for the server's panel positions","chip":null}
test("a skip toggle keeps DONE open: skip is not part of the layout the server answers", () => {
  const d = draftOf(M31);
  const answered = answerFor(d, M31);
  const skipped = { ...d, skip: toggleSkip(d.skip, 2, 3, 1, 2) };
  eq(skipped.skip, "1-2", "the toggle wrote the panel");
  eq(doneState(viewOf(skipped, M31, answered)), { locked: false, reason: null, chip: null }, "after a skip");
});

// "The CURRENT spec" is every field the route is asked about. The DONE test
// above moves only the overlap, so a key that dropped the angle, the field or
// the anchor would pass it while a RotateHandle drag let the answer for the
// OLD angle unlock DONE: 'any answer unlocks DONE' again, one field at a time.
// MUTANT "requestKey drops rotation" (requestKey: `req.rotation_deg` left out). Observed:
//   x every field of the request is in its key, so a turned grid waits for its own answer: request
//     fields not in the key: rotation_deg
// MUTANT "requestKey drops the anchor" (requestKey: `req.anchor` left out). Observed:
//   x every field of the request is in its key, so a turned grid waits for its own answer: request
//     fields not in the key: anchor
test("every field of the request is in its key, so a turned grid waits for its own answer", () => {
  const d = draftOf(M31);
  const base = mosaicRequest(d, M31.frameAnchor);
  assert(base !== null, "a request");
  const k = requestKey(base!);
  eq(requestKey(mosaicRequest(draftOf(M31), M31.frameAnchor)!), k, "one spec, one key (control)");
  const moved: [string, MosaicRequest][] = [
    ["ra_hours", { ...base!, ra_hours: base!.ra_hours + 1e-4 }],
    ["dec_deg", { ...base!, dec_deg: base!.dec_deg + 1e-4 }],
    ["rows", { ...base!, rows: 3 }],
    ["cols", { ...base!, cols: 4 }],
    ["overlap", { ...base!, overlap: 0.3 }],
    ["rotation_deg", { ...base!, rotation_deg: 45 }],
    ["fov_x_deg", { ...base!, fov_x_deg: 2.1 }],
    ["fov_y_deg", { ...base!, fov_y_deg: 1.4 }],
    ["anchor", { ...base!, anchor: '{"ra_hours":1.5}' }],
  ];
  const unkeyed = moved.filter(([, r]) => requestKey(r) === k).map(([name]) => name);
  assert(unkeyed.length === 0, `request fields not in the key: ${unkeyed.join(", ")}`);
  // The RotateHandle drag: turned after the answer for PA 30 came back.
  eq(doneState(viewOf(draftOf(M31, { rotation: 45 }), M31, answerFor(d, M31))),
    { locked: true, reason: WAITING_FOR_PANELS, chip: null }, "turned to PA 45 after PA 30's answer");
});

// MUTANT "any angle as PA 0" (mosaicRequest: `l.rotation_deg ?? 0`). Observed:
//   x the request is MosaicSpecIn's shape, any angle as -1, the anchor only when stored: any-angle
//     1x1 rotation: expected -1, got 0
// MUTANT "layout any angle keeps PA" (layoutOf: only a negative rotation is any angle). Observed:
//   x the request is MosaicSpecIn's shape, any angle as -1, the anchor only when stored: any angle
//     over a stored PA 30: expected -1, got 30
// MUTANT "request with no field" (mosaicRequest: the field check deleted). Observed:
//   x the request is MosaicSpecIn's shape, any angle as -1, the anchor only when stored: no field:
//     nothing to ask: expected null, got
//     {"ra_hours":0.7122222222222222,"dec_deg":41.26916666666666,"rows":2,"cols":3,"overlap":0.25,"rotation_deg":30,"fov_x_deg":0,"fov_y_deg":1.33}
// MUTANT "grid max ignored" (mosaicRequest: the GRID_MAX check deleted). Observed:
//   x the request is MosaicSpecIn's shape, any angle as -1, the anchor only when stored: past
//     GRID_MAX: nothing the route answers: expected null, got
//     {"ra_hours":0.7122222222222222,"dec_deg":41.26916666666666,"rows":2,"cols":11,"overlap":0.25,"rotation_deg":30,"fov_x_deg":2,"fov_y_deg":1.33}
// MUTANT "blank anchor sent" (mosaicRequest: `req.anchor = String(anchor ?? "")` always).
// Observed:
//   x the request is MosaicSpecIn's shape, any angle as -1, the anchor only when stored: a blank
//     anchor is not sent
// MUTANT "no request waits" (doneState: the no-request branch deleted). Observed:
//   x the request is MosaicSpecIn's shape, any angle as -1, the anchor only when stored: no
//     request: nothing to wait for: expected {"locked":false,"reason":null,"chip":null}, got
//     {"locked":true,"reason":"waiting for the server's panel positions","chip":null}
test("the request is MosaicSpecIn's shape, any angle as -1, the anchor only when stored", () => {
  const r = mosaicRequest(draftOf(M31), M31.frameAnchor);
  assert(r !== null, "a request");
  eq([r!.rows, r!.cols, r!.overlap, r!.rotation_deg, r!.fov_x_deg, r!.fov_y_deg, r!.anchor],
    [2, 3, 0.25, 30, 2.0, 1.33, M31.frameAnchor], "the reference block");
  assert(Math.abs(r!.ra_hours - (0 + 42 / 60 + 44 / 3600)) < 1e-9, `ra ${r!.ra_hours}`);
  assert(Math.abs(r!.dec_deg - (41 + 16 / 60 + 9 / 3600)) < 1e-9, `dec ${r!.dec_deg}`);
  const one = setAngleMode(draftOf(M31, { rows: 1, cols: 1 }), "Any angle");
  eq(mosaicRequest(one, "")?.rotation_deg, -1, "any-angle 1x1 rotation");
  // "Any angle" is any angle whatever rotation the node still carries
  // (save_rules.block_shape), so a stored PA is not a layout angle.
  eq(mosaicRequest(draftOf(M31, { rows: 1, cols: 1, angle: "Any angle" }), "")?.rotation_deg, -1, "any angle over a stored PA 30");
  assert(mosaicRequest(one, "   ")?.anchor === undefined, "a blank anchor is not sent");
  eq(mosaicRequest(draftOf(M31, { fovX: 0 }), ""), null, "no field: nothing to ask");
  eq(mosaicRequest(draftOf(M31, { ra: "" }), ""), null, "no typed coordinates: nothing to ask");
  eq(mosaicRequest(draftOf(M31, { cols: 11 }), ""), null, "past GRID_MAX: nothing the route answers");
  eq(doneState({ request: null, answer: null, offline: false, canViewSiteDerived: true }),
    { locked: false, reason: null, chip: null }, "no request: nothing to wait for");
  // The route is asked about the layout the compile will lay out: the overlap
  // clamped to [0, 0.5] as save_rules.block_shape clamps it (a hand-edited 60
  // is a 50% grid), and the RA folded into [0, 24), which `MosaicSpecIn`
  // requires (`lt=24`), as the server folds `ra % 24`.
  // MUTANT "overlap unclamped" (layoutOf: the [0, 0.5] clamp deleted). Observed:
  //   x the request is MosaicSpecIn's shape, any angle as -1, the anchor only when stored: overlaps
  //     past the ends are laid out at the ends: expected [0.5,0], got [0.6,-0.05]
  // MUTANT "RA not wrapped" (draftCentre: `wrapRaHours` deleted). Observed:
  //   x the request is MosaicSpecIn's shape, any angle as -1, the anchor only when stored: RA 24h
  //     42m 44s folds to 00h 42m 44s: got 24.71222222222222
  eq([mosaicRequest(draftOf(M31, { overlap: 60 }), "")?.overlap, mosaicRequest(draftOf(M31, { overlap: -5 }), "")?.overlap],
    [0.5, 0], "overlaps past the ends are laid out at the ends");
  const past24 = mosaicRequest(draftOf(M31, { ra: "24h 42m 44s" }), "")?.ra_hours;
  assert(past24 !== undefined && Math.abs(past24 - (42 / 60 + 44 / 3600)) < 1e-9, `RA 24h 42m 44s folds to 00h 42m 44s: got ${past24}`);
});

// MUTANT "Python float is parseFloat" (pyFloat: Number.parseFloat). Observed:
//   x coordinates parse as coords.py parses them, typographic marks included: RA "1.2.3": expected
//     null, got 1.2
// MUTANT "typographic fold off" (fold: returns its input). Observed:
//   x coordinates parse as coords.py parses them, typographic marks included: Dec with a true
//     minus and primes
// MUTANT "Dec sign lost" (parseDecDeg: `const sign = 1`). Observed:
//   x coordinates parse as coords.py parses them, typographic marks included: Dec with a true
//     minus and primes
test("coordinates parse as coords.py parses them, typographic marks included", () => {
  const near = (a: number | null, b: number) => a !== null && Math.abs(a - b) < 1e-9;
  assert(near(parseRaHours("00h 42m 44s"), 42 / 60 + 44 / 3600), "RA h m s");
  assert(near(parseRaHours("00:42:44"), 42 / 60 + 44 / 3600), "RA colons");
  assert(near(parseRaHours("5h 30m"), 5.5), "RA h m");
  assert(near(parseRaHours("5.5883"), 5.5883), "RA decimal hours");
  eq(parseRaHours("1.2.3"), null, `RA "1.2.3"`);
  assert(near(parseDecDeg(cp(0x2212) + "5" + cp(0xb0) + " 23" + cp(0x2032) + " 28" + cp(0x2033)), -(5 + 23 / 60 + 28 / 3600)), "Dec with a true minus and primes");
  assert(near(parseDecDeg("-05:23:28"), -(5 + 23 / 60 + 28 / 3600)), "Dec colons");
  assert(near(parseDecDeg("+41 16"), 41 + 16 / 60), "Dec d m");
  assert(near(parseDecDeg("-5.391"), -5.391), "Dec decimal");
  eq(parseDecDeg("north"), null, "Dec words");
  eq(draftCentre(draftOf(M31, { dec: "+95 0 0" })), null, "a Dec off the sphere");
});

// ================================================= re-frame carry (2.5)

// The spec's worked question, word for word, in the server's numbers.
// MUTANT "reframe asks with nothing banked" (reframeDecision: the `banked > 0` test deleted).
// Observed:
//   x the reframe question asks only with banked subs, in the server's numbers: nothing banked:
//     expected {"ask":false,"question":null}, got {"ask":true,"question":"Re-framing moves the
//     panels 14.8', more than the 10.0' this grid allows, so all 6 panels start from zero: 0
//     banked subs belong to the old layout and stay on disk."}
test("the reframe question asks only with banked subs, in the server's numbers", () => {
  const moved = draftOf(M31, { ra: "00h 43m 44s" });
  const view = viewOf(moved, M31, answerFor(moved, M31, { reframe: MOVE_14_8 }));
  eq(reframeDecision({ stored: M31, draft: moved, progress: { banked: 212 }, view }), {
    ask: true,
    question: "Re-framing moves the panels 14.8', more than the 10.0' this grid allows, so all 6 panels start from zero: 212 banked subs belong to the old layout and stay on disk.",
  }, "212 banked, moved 14.8'");
  eq(reframeDecision({ stored: M31, draft: moved, progress: { banked: 0 }, view }), { ask: false, question: null }, "nothing banked");
  eq(reframeDecision({ stored: M31, draft: moved, progress: null, view }), { ask: false, question: null }, "no progress answer");
  // Nothing banked never asks on the grid path either: a new grid restarts
  // counts that are not there, and "0 banked subs belong to the old layout"
  // is a question with nothing in it.
  // MUTANT "grid asks with nothing banked" (reframeDecision: the grid test
  // hoisted above the banked test). Observed:
  //   x the reframe question asks only with banked subs, in the server's numbers: a new grid with
  //     nothing banked: expected {"ask":false,"question":null}, got
  //     {"ask":true,"question":"Changing the grid from 3x2 to 4x2 means all 8 panels start from
  //     zero: 0 banked subs belong to the old layout and stay on disk."}
  const wider = draftOf(M31, { cols: 4 });
  eq(reframeDecision({ stored: M31, draft: wider, progress: { banked: 0 }, view: viewOf(wider, M31, null, { offline: true }) }),
    { ask: false, question: null }, "a new grid with nothing banked");
});

// Every reason the server's reframe can give reads as its own line, and a
// restart the server did not measure still asks. The server answers "move"
// only with a measured move today; the fallbacks are pinned so a future
// answer without one can never read as carried or pass without a question.
// MUTANT "grid move line deleted" (moveLine: the "grid" case deleted, so it
// falls to the move branch). Observed:
//   x every reframe reason reads as its own line, and an unmeasured restart still asks: grid:
//     expected "a new grid: counts restart", got "the server decides at save"
// MUTANT "identity move line deleted" (moveLine: the "identity" case deleted). Observed:
//   x every reframe reason reads as its own line, and an unmeasured restart still asks: identity:
//     expected "another object: counts restart", got "the server decides at save"
// MUTANT "unmeasured move reads as carried" (moveLine: a "move" with no
// max_move_deg reads as unchanged). Observed:
//   x every reframe reason reads as its own line, and an unmeasured restart still asks: a move with
//     no measured distance: expected "the server decides at save", got "unchanged against the
//     anchor: counts carry over"
// MUTANT "unmeasured restart does not ask" (reframeDecision: a no-carry verdict
// with no max_move_deg answers no question). Observed:
//   x every reframe reason reads as its own line, and an unmeasured restart still asks: an
//     unmeasured restart: expected {"ask":true,"question":"The server will restart the counts at
//     save: all 6 panels start from zero: 212 banked subs belong to the old layout and stay on
//     disk."}, got {"ask":false,"question":null}
test("every reframe reason reads as its own line, and an unmeasured restart still asks", () => {
  const verdict = (reason: ReframeAnswer["reason"]): ReframeAnswer =>
    ({ carry: false, max_move_deg: null, threshold_deg: 9.98 / 60, reason });
  const wider = draftOf(M31, { cols: 4 });
  eq(moveLine(viewOf(wider, M31, answerFor(wider, M31, { reframe: verdict("grid") }))), "a new grid: counts restart", "grid");
  const far = draftOf(M31, { ra: "01h 33m 51s" });
  eq(moveLine(viewOf(far, M31, answerFor(far, M31, { reframe: verdict("identity") }))), "another object: counts restart", "identity");
  const moved = draftOf(M31, { ra: "00h 43m 44s" });
  const unmeasured = viewOf(moved, M31, answerFor(moved, M31, { reframe: verdict("move") }));
  eq(moveLine(unmeasured), SERVER_DECIDES, "a move with no measured distance");
  eq(reframeDecision({ stored: M31, draft: moved, progress: { banked: 212 }, view: unmeasured }), {
    ask: true,
    question: "The server will restart the counts at save: all 6 panels start from zero: 212 banked subs belong to the old layout and stay on disk.",
  }, "an unmeasured restart");
});

// MUTANT "skip in the identity" (layoutKey: the skip text added to the key). Observed:
//   x a skip-only change never asks, even when the server's reframe says no carry: skip only:
//     expected {"ask":false,"question":null}, got {"ask":true,"question":"Re-framing moves the
//     panels 14.8', more than the 10.0' this grid allows, so all 5 panels start from zero: 212
//     banked subs belong to the old layout and stay on disk."}
test("a skip-only change never asks, even when the server's reframe says no carry", () => {
  // The reframe can say "no carry" on a skip-only change when the editor
  // graph is dirty with an inspector edit the save has not seen; the DONE
  // being pressed changes only the skip, and a skip is not in the identity.
  const skipped = draftOf(M31, { skip: "1-1" });
  const view = viewOf(skipped, M31, answerFor(skipped, M31, { reframe: MOVE_14_8 }));
  eq(reframeDecision({ stored: M31, draft: skipped, progress: { banked: 212 }, view }), { ask: false, question: null }, "skip only");
  eq(layoutKey(skipped), layoutKey(draftOf(M31)), "skip is not in the identity");
  assert(layoutKey(draftOf(M31, { overlap: 30 })) !== layoutKey(draftOf(M31)), "overlap is (control)");
});

// MATCH CAMERA onto a new camera is a re-frame too: a 6% change of field
// reaches a 3x2's threshold (A.5), and the counts it would restart deserve
// the same question as a move.
// MUTANT "field not in the identity" (layoutKey: fov_x and fov_y left out). Observed:
//   x a new camera field with banked subs asks when the server says no carry: a new field: expected
//     "Re-framing moves the panels 12.1', more than the 10.0' this grid allows, so all 6 panels
//     start from zero: 212 banked subs belong to the old layout and stay on disk.", got null
test("a new camera field with banked subs asks when the server says no carry", () => {
  const rematched = matchCamera(draftOf(M31), { ...RIG, fov_deg: [2.2, 1.46] });
  const reframe: ReframeAnswer = { carry: false, max_move_deg: 12.1 / 60, threshold_deg: 9.98 / 60, reason: "move" };
  const view = viewOf(rematched, M31, answerFor(rematched, M31, { reframe }));
  eq(reframeDecision({ stored: M31, draft: rematched, progress: { banked: 212 }, view }).question,
    "Re-framing moves the panels 12.1', more than the 10.0' this grid allows, so all 6 panels start from zero: 212 banked subs belong to the old layout and stay on disk.",
    "a new field");
});

// MUTANT "carry asks" (reframeDecision: `r.carry` dropped from the no-ask test). Observed:
//   x a carried move does not ask, and the move line says it carries: carried: expected
//     {"ask":false,"question":null}, got {"ask":true,"question":"Re-framing moves the panels 4.2',
//     more than the 10.0' this grid allows, so all 6 panels start from zero: 212 banked subs
//     belong to the old layout and stay on disk."}
test("a carried move does not ask, and the move line says it carries", () => {
  const nudged = draftOf(M31, { ra: "00h 42m 50s" });
  const view = viewOf(nudged, M31, answerFor(nudged, M31, { reframe: MOVE_4_2 }));
  eq(reframeDecision({ stored: M31, draft: nudged, progress: { banked: 212 }, view }), { ask: false, question: null }, "carried");
  eq(moveLine(view), "moved 4.2' of the 10.0' this grid allows: counts carry over", "move line");
  const far = viewOf(nudged, M31, answerFor(nudged, M31, { reframe: MOVE_14_8 }));
  eq(moveLine(far), "moved 14.8', more than the 10.0' this grid allows: counts restart", "restart line");
});

// MUTANT "stale reframe used" (currentReframe: reads v.answer, not currentAnswer(v)). Observed:
//   x a reframe for a spec the operator has moved on from is never read: stale verdict: expected
//     {"ask":false,"question":null}, got {"ask":true,"question":"Re-framing moves the panels
//     14.8', more than the 10.0' this grid allows, so all 6 panels start from zero: 212 banked
//     subs belong to the old layout and stay on disk."}
// MUTANT "failed answer's reframe read" (currentReframe: the `a.ok` test deleted). Observed:
//   x a reframe for a spec the operator has moved on from is never read: a failed answer's verdict:
//     expected {"ask":false,"question":null}, got {"ask":true,"question":"Re-framing moves the
//     panels 14.8', more than the 10.0' this grid allows, so all 6 panels start from zero: 212
//     banked subs belong to the old layout and stay on disk."}
test("a reframe for a spec the operator has moved on from is never read", () => {
  const first = draftOf(M31, { ra: "00h 43m 44s" });
  const now = draftOf(M31, { ra: "00h 42m 45s" });
  const stale = viewOf(now, M31, answerFor(first, M31, { reframe: MOVE_14_8 }));
  eq(reframeDecision({ stored: M31, draft: now, progress: { banked: 212 }, view: stale }), { ask: false, question: null }, "stale verdict");
  eq(moveLine(stale), null, "no move line until this spec is answered");
  // A request that FAILED for this very spec says nothing the modal may
  // trust, whatever it carried: the move is left to the save, as offline.
  const failed = viewOf(first, M31, answerFor(first, M31, { ok: false, reframe: MOVE_14_8 }));
  eq(reframeDecision({ stored: M31, draft: first, progress: { banked: 212 }, view: failed }), { ask: false, question: null }, "a failed answer's verdict");
});

// MUTANT "grid rule deleted" (reframeDecision: the grid test deleted). Observed:
//   x changing rows or cols always asks, offline too; offline a move is the save's: grid offline:
//     expected {"ask":true,"question":"Changing the grid from 3x2 to 4x2 means all 8 panels start
//     from zero: 212 banked subs belong to the old layout and stay on disk."}, got
//     {"ask":false,"question":null}
// MUTANT "grid written rows x cols" (reframeDecision: the sizes written rows x cols (S4
// orchestrator ruling 1 says columns x rows)). Observed:
//   x changing rows or cols always asks, offline too; offline a move is the save's: grid offline:
//     expected {"ask":true,"question":"Changing the grid from 3x2 to 4x2 means all 8 panels start
//     from zero: 212 banked subs belong to the old layout and stay on disk."}, got
//     {"ask":true,"question":"Changing the grid from 2x3 to 2x4 means all 8 panels start from
//     zero: 212 banked subs belong to the old layout and stay on disk."}
// MUTANT "offline move line waits" (moveLine: the unreachable-server line deleted). Observed:
//   x changing rows or cols always asks, offline too; offline a move is the save's: offline move
//     line: expected "the server decides at save", got null
// MUTANT "no anchor still measured" (moveLine: the no-anchor test deleted). Observed:
//   x changing rows or cols always asks, offline too; offline a move is the save's: no anchor, no
//     move line: expected null, got "the server decides at save"
test("changing rows or cols always asks, offline too; offline a move is the save's", () => {
  const wider = draftOf(M31, { cols: 4 });
  const off = viewOf(wider, M31, null, { offline: true });
  eq(reframeDecision({ stored: M31, draft: wider, progress: { banked: 212 }, view: off }), {
    ask: true,
    question: "Changing the grid from 3x2 to 4x2 means all 8 panels start from zero: 212 banked subs belong to the old layout and stay on disk.",
  }, "grid offline");
  const moved = draftOf(M31, { ra: "00h 43m 44s" });
  eq(reframeDecision({ stored: M31, draft: moved, progress: { banked: 212 }, view: viewOf(moved, M31, null, { offline: true }) }),
    { ask: false, question: null }, "a move offline");
  eq(moveLine(viewOf(moved, M31, null, { offline: true })), SERVER_DECIDES, "offline move line");
  eq(SERVER_DECIDES, "the server decides at save", "the spec's words");
  // Offline, where the line would otherwise defer to the save: a block with no
  // anchor has nothing to move against, so there is no line at all.
  eq(moveLine(viewOf(moved, { ...M31, frameAnchor: "" }, null, { offline: true })), null, "no anchor, no move line");
});

// MUTANT "skipped panels' subs uncounted" (bankedSubs: the skipped panels' loop deleted).
// Observed:
//   x the banked count includes what skipped panels hold, and the other reasons read in words:
//     banked: expected 212, got 200
// MUTANT "angle question as a move" (reframeDecision: the angle sentence deleted). Observed:
//   x the banked count includes what skipped panels hold, and the other reasons read in words:
//     angle, one panel, one sub: expected "Changing between any angle and a set angle means the
//     panel starts from zero: 1 banked sub belongs to the old layout and stays on disk.", got "The
//     server will restart the counts at save: the panel starts from zero: 1 banked sub belongs to
//     the old layout and stays on disk."
// MUTANT "identity question as a move" (reframeDecision: the identity sentence deleted). Observed:
//   x the banked count includes what skipped panels hold, and the other reasons read in words:
//     identity: expected "Framing another object means all 6 panels start from zero: 212 banked
//     subs belong to the old layout and stay on disk.", got "The server will restart the counts at
//     save: all 6 panels start from zero: 212 banked subs belong to the old layout and stay on
//     disk."
// MUTANT "unchanged read as a move" (moveLine: the unchanged case deleted). Observed:
//   x the banked count includes what skipped panels hold, and the other reasons read in words:
//     unchanged move line: expected "unchanged against the anchor: counts carry over", got "moved
//     0.0' of the 9.6' this grid allows: counts carry over"
// MUTANT "singular stays" (the singular tail says "stay"). Observed:
//   x the banked count includes what skipped panels hold, and the other reasons read in words:
//     angle, one panel, one sub: expected "Changing between any angle and a set angle means the
//     panel starts from zero: 1 banked sub belongs to the old layout and stays on disk.", got
//     "Changing between any angle and a set angle means the panel starts from zero: 1 banked sub
//     belongs to the old layout and stay on disk."
test("the banked count includes what skipped panels hold, and the other reasons read in words", () => {
  eq(bankedSubs({ banked: 200, skipped: [{ banked: 12 }, { banked: NaN }] }), 212, "banked");
  const one = draftOf(M31, { rows: 1, cols: 1, angle: "Any angle", rotation: -1 });
  const stored1: Params = { ...M31, rows: 1, cols: 1 };
  const angleView = viewOf(one, stored1, answerFor(one, stored1, { reframe: { carry: false, max_move_deg: null, threshold_deg: 0.16, reason: "angle" } }));
  eq(reframeDecision({ stored: stored1, draft: one, progress: { banked: 1 }, view: angleView }).question,
    "Changing between any angle and a set angle means the panel starts from zero: 1 banked sub belongs to the old layout and stays on disk.", "angle, one panel, one sub");
  eq(moveLine(angleView), "any angle against a set angle: counts restart", "angle move line");
  const renamed = draftOf(M31, { ra: "", dec: "", name: "M33" });
  const idView = viewOf(draftOf(M31, { ra: "01h 33m 51s" }), M31, null);
  const idAnswer = { ...idView, answer: answerFor(draftOf(M31, { ra: "01h 33m 51s" }), M31, { reframe: { carry: false, max_move_deg: null, threshold_deg: 0.16, reason: "identity" as const } }) };
  eq(reframeDecision({ stored: M31, draft: draftOf(M31, { ra: "01h 33m 51s" }), progress: { banked: 212 }, view: idAnswer }).question,
    "Framing another object means all 6 panels start from zero: 212 banked subs belong to the old layout and stay on disk.", "identity");
  assert(layoutKey(renamed) !== layoutKey(draftOf(M31)), "a block placed by name is keyed on its name");
  const same = draftOf(M31);
  eq(moveLine(viewOf(same, M31, answerFor(same, M31, { reframe: { carry: true, max_move_deg: 0, threshold_deg: 0.16, reason: "unchanged" } }))),
    "unchanged against the anchor: counts carry over", "unchanged move line");
});

// ============================================================== readouts

// MUTANT "rows for cols in the extent" (readoutStrip: mosaicTotalFov(l.rows, l.cols, ...)).
// Observed:
//   x the readout strip: extent, panels, centre: 3x2: expected "5.0 x 2.3 deg <U+00B7> 6 panels
//     <U+00B7> 00h42m44s +41 16'", got "3.5 x 3.3 deg <U+00B7> 6 panels <U+00B7> 00h42m44s +41
//     16'"
// MUTANT "skipped panels counted live" (readoutStrip: always the whole grid's panel count).
// Observed:
//   x the readout strip: extent, panels, centre: one skipped: expected "5.0 x 2.3 deg <U+00B7> 5
//     of 6 panels <U+00B7> 00h42m44s +41 16'", got "5.0 x 2.3 deg <U+00B7> 6 panels <U+00B7>
//     00h42m44s +41 16'"
// MUTANT "minus on a zero" (stripDec: the sign from the raw value). Observed:
//   x the readout strip: extent, panels, centre: rounds to zero: no minus: expected "1 panel
//     <U+00B7> 00h42m44s +00 00'", got "1 panel <U+00B7> 00h42m44s -00 00'"
// MUTANT "name part dropped" (readoutStrip: the name part deleted). Observed:
//   x the readout strip: extent, panels, centre: placed by name: expected "Jupiter", got undefined
test("the readout strip: extent, panels, centre", () => {
  const dot = " " + cp(0xb7) + " ";
  eq(readoutStrip(draftOf(M31)), ["5.0 x 2.3 deg", "6 panels", "00h42m44s +41 16'"].join(dot), "3x2");
  eq(readoutStrip(draftOf(M31, { skip: "1-1, 9-9" })), ["5.0 x 2.3 deg", "5 of 6 panels", "00h42m44s +41 16'"].join(dot), "one skipped");
  eq(readoutStrip(draftOf(M31, { rows: 1, cols: 1, fovX: 0, dec: "-00 00 20" })), ["1 panel", "00h42m44s +00 00'"].join(dot), "rounds to zero: no minus");
  eq(readoutStrip(draftOf(M31, { dec: "-29 59 45" })).split(dot)[2], "00h42m44s -30 00'", "carry into the degree");
  eq(readoutStrip(draftOf(M31, { ra: "", dec: "", name: "Jupiter" })).split(dot)[2], "Jupiter", "placed by name");
  // A block with only one coordinate typed is placed by its name, as the
  // server's `identity.typed_coordinates` needs BOTH an RA and a Dec.
  // MUTANT "either coordinate is typed" (typedCoordinates: `&&` made `||`). Observed:
  //   x the readout strip: extent, panels, centre: a Dec alone is placed by name: expected
  //     "Jupiter", got undefined
  eq(readoutStrip(draftOf(M31, { ra: "", name: "Jupiter" })).split(dot)[2], "Jupiter", "a Dec alone is placed by name");
  // Rounded to the second, 23h59m59.8s is 24h, which the strip writes as 0h.
  // MUTANT "strip RA past 24h" (stripRa: the fold into one day deleted). Observed:
  //   x the readout strip: extent, panels, centre: a second short of 24h: expected "00h00m00s", got
  //     "24h00m00s"
  eq(stripRa(24 - 0.2 / 3600), "00h00m00s", "a second short of 24h");
});

// MUTANT "camera line to one decimal" (cameraFieldLine: toFixed(1)). Observed:
//   x the camera-field line: framed: expected "Tiled for 2.00 x 1.33 deg at bin 1 (profile
//     Refractor, matched 2026-09-23)", got "Tiled for 2.0 x 1.3 deg at bin 1 (profile Refractor,
//     matched 2026-09-23)"
test("the camera-field line", () => {
  eq(cameraFieldLine(draftOf(M31)), "Tiled for 2.00 x 1.33 deg at bin 1 (profile Refractor, matched 2026-09-23)", "framed");
  eq(cameraFieldLine(draftOf(M31, { fovFrom: "" })), "Tiled for 2.00 x 1.33 deg at bin 1", "no provenance");
  assert(cameraFieldLine(draftOf(M31, { fovX: 0 })).startsWith("No camera field"), "not framed");
});

// MUTANT "drift threshold 20%" (FIELD_DRIFT_SHARE 0.2). Observed:
//   x the drift banner: over 2% on either axis, M5's gap sentence when the live field is under one
//     step: 3% wider: expected {"level":"warn","text":"framed for 2.00 x 1.33 deg; this camera now
//     images 2.06 x 1.37 deg, so the panels would overlap more than framed. Re-frame."}, got null
// MUTANT "gap test ignores overlap" (driftBanner: keep = 1). Observed:
//   x the drift banner: over 2% on either axis, M5's gap sentence when the live field is under one
//     step: narrower but over one step: expected {"level":"warn","text":"framed for 2.00 x 1.33
//     deg; this camera now images 1.60 x 1.10 deg, so the panels would overlap less than framed.
//     Re-frame."}, got {"level":"loss","text":"framed for 2.00 x 1.33 deg; this camera now images
//     1.60 x 1.10 deg, so the panels would leave gaps. Re-frame."}
// MUTANT "drift share of the live field" (driftBanner: 2% of the live field, not the snapshot).
// Observed:
//   x the drift banner: over 2% on either axis, M5's gap sentence when the live field is under one
//     step: just over 2% of the snapshot: expected "warn", got undefined
// MUTANT "drift on one axis only" (driftBanner: only the width compared). Observed:
//   x the drift banner: over 2% on either axis, M5's gap sentence when the live field is under one
//     step: only the height drifts: expected {"level":"warn","text":"framed for 2.00 x 1.33 deg;
//     this camera now images 2.00 x 1.40 deg, so the panels would overlap more than framed.
//     Re-frame."}, got null
// MUTANT "one panel reads gaps" (driftBanner: the single-panel branch deleted). Observed:
//   x the drift banner: over 2% on either axis, M5's gap sentence when the live field is under one
//     step: one panel has no seams: expected "warn", got "loss"
test("the drift banner: over 2% on either axis, M5's gap sentence when the live field is under one step", () => {
  const d = draftOf(M31);
  eq(driftBanner(d, { ...RIG, fov_deg: [1.4, 0.93] }), {
    level: "loss",
    text: "framed for 2.00 x 1.33 deg; this camera now images 1.40 x 0.93 deg, so the panels would leave gaps. Re-frame.",
  }, "M5's worked case");
  eq(driftBanner(d, { ...RIG, fov_deg: [2.03, 1.35] }), null, "within 2% (control)");
  // 2% OF THE SNAPSHOT, doctor M5's base: 0.0405 is over 2% of 2.00 (0.040)
  // and under 2% of the live 2.0405 (0.0408), so the two bases disagree here.
  eq(driftBanner(d, { ...RIG, fov_deg: [2.0405, 1.33] })?.level, "warn", "just over 2% of the snapshot");
  eq(driftBanner(d, { ...RIG, fov_deg: [2.06, 1.37] }), {
    level: "warn",
    text: "framed for 2.00 x 1.33 deg; this camera now images 2.06 x 1.37 deg, so the panels would overlap more than framed. Re-frame.",
  }, "3% wider");
  eq(driftBanner(d, { ...RIG, fov_deg: [1.6, 1.1] }), {
    level: "warn",
    text: "framed for 2.00 x 1.33 deg; this camera now images 1.60 x 1.10 deg, so the panels would overlap less than framed. Re-frame.",
  }, "narrower but over one step");
  // Either axis, as doctor M5 reads it: a camera of the same width and a
  // different height is a different field.
  eq(driftBanner(d, { ...RIG, fov_deg: [2.0, 1.4] }), {
    level: "warn",
    text: "framed for 2.00 x 1.33 deg; this camera now images 2.00 x 1.40 deg, so the panels would overlap more than framed. Re-frame.",
  }, "only the height drifts");
  eq(driftBanner(d, { ...RIG, fov_deg: [2.0, 0.9] })?.level, "loss", "only the height narrows past one step");
  eq(driftBanner(draftOf(M31, { rows: 1, cols: 1 }), { ...RIG, fov_deg: [1.4, 0.93] })?.level, "warn", "one panel has no seams");
  eq(driftBanner(d, { ...RIG, fov_deg: null }), null, "optics unknown");
  eq(driftBanner(draftOf(M31, { fovX: 0 }), RIG), null, "never framed (doctor M1's)");
});

// MUTANT "age in seconds" (ago: minutes are seconds). Observed:
//   x USE MEASURED: the angle, how long ago, which solve, which pier: spec's example: expected
//     "camera measured 37.2 deg, 14 min ago, by the centring solve, pier west", got "camera
//     measured 37.2 deg, 14 h ago, by the centring solve, pier west"
// MUTANT "solve phrase raw" (SOLVE_PHRASE: the centring solve spelled as its source). Observed:
//   x USE MEASURED: the angle, how long ago, which solve, which pier: spec's example: expected
//     "camera measured 37.2 deg, 14 min ago, by the centring solve, pier west", got "camera
//     measured 37.2 deg, 14 min ago, by the plate solve + sync solve, pier west"
// MUTANT "pier omitted" (useMeasuredLine: the pier part deleted). Observed:
//   x USE MEASURED: the angle, how long ago, which solve, which pier: spec's example: expected
//     "camera measured 37.2 deg, 14 min ago, by the centring solve, pier west", got "camera
//     measured 37.2 deg, 14 min ago, by the centring solve"
test("USE MEASURED: the angle, how long ago, which solve, which pier", () => {
  const now = 1_790_000_000;
  // Exposed 14 min ago and solved 15 s later, as a centring solve is: the
  // age is the frame's (#439, below).
  const rec: MeasuredAngle = {
    pa_deg: 37.2449, exposed_at: now - 840, solved_at: now - 825, source: "plate solve + sync", pier_side: "west",
  };
  eq(useMeasuredLine(rec, now), "camera measured 37.2 deg, 14 min ago, by the centring solve, pier west", "spec's example");
  eq(useMeasuredLine({ ...rec, exposed_at: now - 20, solved_at: now - 5, pier_side: null, source: "rotator sync" }, now),
    "camera measured 37.2 deg, under a minute ago, by the rotator sync solve", "fresh, no pier");
  eq(useMeasuredLine({ ...rec, exposed_at: now - 3 * 3600 }, now)?.split(", ")[1], "3 h ago", "hours");
  eq(useMeasuredLine(null, now), null, "no record");
  eq(useMeasuredLine({ ...rec, pa_deg: NaN }, now), null, "no finite angle");
});

// #439: USE MEASURED's age is the age of the FRAME the angle was measured on,
// from `exposed_at`, as types.ts `SkyAngleRecord.exposed_at` says freshness
// is judged ("a stale frame can finish solving late") and as the engine's
// angle check and the ruling 9 angle lock judge it (#292). Until S7 the modal
// aged the SOLVE, from `solved_at`, the one reader that did. The saved-frame
// WCS stamp solves a light frame after it has been saved, so its `solved_at`
// trails `exposed_at`; with 300 s subs the line could say "under a minute
// ago" for an angle five minutes old (found by reading the code, not on the
// rig), and the line is the only place the operator sees
// how old the offered angle is (the strip offer's tooltip, S5 ruling 1). A
// record with no finite `exposed_at` (null from a server, or absent from a
// hand-built record) falls back to `solved_at`; with neither, no age is said.
//
// MUTANT "age from solved_at" (useMeasuredLine's `measuredAt`: the
// `exposed_at` line deleted, so the age is always `solved_at`'s). Observed
// (scratch copy S7-MODAL-mut; framingModel.test 47/50: the spec's example
// above, exposed 15 s before it solved, reads "13 min ago" with it, and the
// Pick check below goes red; framingSections.test, runMode.test and
// targetFramingSheet.test stay green, since each of their records reads the
// same age from either time, or carries no `exposed_at` at all):
//   x USE MEASURED ages the frame: a saved frame's WCS exposed 300 s before it solved reads 5 min,
//     not under a minute: the saved-frame WCS record: expected "camera measured 212.0 deg, 5 min
//     ago, by a saved frame's WCS, pier east", got "camera measured 212.0 deg, under a minute ago,
//     by a saved frame's WCS, pier east"
// MUTANT "no fallback" (`measuredAt`: `return Number.isFinite(rec.exposed_at)
// ? rec.exposed_at : null`, the solved_at fallback deleted). Observed (scratch
// copy S7-MODAL-mut; framingModel.test 48/50, the Pick check red too, since
// `solved_at` is then read nowhere):
//   x USE MEASURED ages the frame: a saved frame's WCS exposed 300 s before it solved reads 5 min,
//     not under a minute: exposed_at NaN: from solved_at: expected "camera measured 212.0 deg, 14
//     min ago, by a saved frame's WCS, pier east", got "camera measured 212.0 deg, by a saved
//     frame's WCS, pier east"
// MUTANT "absent key reads no age" (`measuredAt`: `if (!("exposed_at" in
// rec)) return null;` put first, so a record without the key says no age).
// Found by the S7-MODAL verifier: the "absent" row then spread `exposed_at:
// undefined` into its record, so the key was present, and the file passed
// under this mutant, 50/50. With the key really absent, observed (scratch copy
// S7-MODAL-verify-mut; framingModel.test 49/50):
//   x USE MEASURED ages the frame: a saved frame's WCS exposed 300 s before it solved reads 5 min,
//     not under a minute: exposed_at absent: from solved_at: expected "camera measured 212.0 deg,
//     14 min ago, by a saved frame's WCS, pier east", got "camera measured 212.0 deg, by a saved
//     frame's WCS, pier east"
test("USE MEASURED ages the frame: a saved frame's WCS exposed 300 s before it solved reads 5 min, not under a minute", () => {
  const now = 1_790_000_000;
  const wcs: MeasuredAngle = {
    pa_deg: 212.04, exposed_at: now - 330, solved_at: now - 30, source: "saved-frame WCS", pier_side: "east",
  };
  eq(wcs.solved_at - wcs.exposed_at, 300, "precondition: exposed 300 s before it solved");
  eq(useMeasuredLine(wcs, now), "camera measured 212.0 deg, 5 min ago, by a saved frame's WCS, pier east",
    "the saved-frame WCS record");
  // No finite exposure time: the solve's is the best there is. "absent" has
  // no `exposed_at` key at all (a key present and undefined would not grade
  // that: see "absent key reads no age" above).
  const noExposure: Partial<MeasuredAngle> = { ...wcs };
  delete noExposure.exposed_at;
  const unexposed: [string, MeasuredAngle][] = [
    ["NaN", { ...wcs, exposed_at: NaN }],
    ["null", { ...wcs, exposed_at: null as unknown as number }],
    ["absent", noExposure as MeasuredAngle],
  ];
  for (const [label, rec] of unexposed) {
    const passed = { ...rec, solved_at: now - 840 };
    if (label === "absent") eq("exposed_at" in passed, false, "precondition: the absent record has no exposed_at key");
    eq(useMeasuredLine(passed, now), "camera measured 212.0 deg, 14 min ago, by a saved frame's WCS, pier east",
      `exposed_at ${label}: from solved_at`);
  }
  eq(useMeasuredLine({ ...wcs, exposed_at: NaN, solved_at: NaN }, now),
    "camera measured 212.0 deg, by a saved frame's WCS, pier east", "neither time: no age");
  // CONTROL: exposed and solved in the same second read as they always did.
  eq(useMeasuredLine({ ...wcs, exposed_at: now - 840, solved_at: now - 840 }, now)?.split(", ")[1], "14 min ago",
    "exposed_at equal to solved_at (control)");
});

// MUTANT "use measured keeps any angle" (useMeasured: the mode left as it was). Observed:
//   x one tap on USE MEASURED lays the grid out at the camera's angle: from any angle: expected
//     ["Camera fixed at PA",37.2], got ["Any angle",37.2]
test("one tap on USE MEASURED lays the grid out at the camera's angle", () => {
  const rec: MeasuredAngle = {
    pa_deg: 37.2449, exposed_at: 0, solved_at: 0, source: "plate solve + sync", pier_side: "west",
  };
  const u = useMeasured(draftFromParams({ rotation: -1 }), rec);
  eq([u.angle, u.rotation], ["Camera fixed at PA", 37.2], "from any angle");
  const r = useMeasured(draftOf(M31), rec);
  eq([r.angle, r.rotation], ["Rotate to PA", 37.2], "a set mode keeps its mode");
});

// ============================================== status.sky_angle, one type
//
// `status.sky_angle` is typed ONCE, in types.ts, whose SkyAngleRecord
// test_types_mirror_status.py holds to the record the server writes. S4's
// model declared a copy of its own, looser (`exposed_at` nullable, three keys
// optional, three missing) and held to nothing, which compiled only because it
// was looser (#408). The model now takes a Pick of the keys it reads, so a
// renamed server key fails tsc here as it does everywhere else, and a key the
// model starts reading must be one the server sends.
//
// MUTANT "local interface restored" (framingModel.ts: the types.ts import and
// the Pick replaced by S4's own `export interface SkyAngleRecord {...}`, with
// `MeasuredAngle` an alias of it). Observed (scratch copy s5-modal-mut):
//   x the model reads status.sky_angle through types.ts's SkyAngleRecord and declares no record of
//     its own: framingModel.ts does not import SkyAngleRecord from types.ts
//   (tsc also failed under it, on this file's records, which S4's copy made
//   carry `exposed_at`: "error TS2741: Property 'exposed_at' is missing in type
//   '{ pa_deg: number; solved_at: number; source: string; pier_side: "west";
//   }' but required in type 'SkyAngleRecord'", three times. With S4's
//   `exposed_at` made optional, tsc exited 0 and this test was red with the
//   same line: a looser copy is what tsc cannot see, which is why this test
//   reads the source.)
//
// Since S7 the Pick holds `exposed_at` too (#439), because the USE MEASURED
// line ages the frame. MUTANT "age from solved_at" (the #439 case above:
// `measuredAt`'s `exposed_at` line deleted, the Pick left as it is).
// Observed (scratch copy S7-MODAL-mut):
//   x the model reads status.sky_angle through types.ts's SkyAngleRecord and declares no record of
//     its own: the Pick's keys against the keys the model reads (`rec.x`): expected
//     ["pa_deg","pier_side","solved_at","source"], got
//     ["exposed_at","pa_deg","pier_side","solved_at","source"]
test("the model reads status.sky_angle through types.ts's SkyAngleRecord and declares no record of its own", () => {
  const src = readFileSync(new URL("../framingModel.ts", import.meta.url), "utf8") as string;
  assert(/^import type \{[^}]*\bSkyAngleRecord\b[^}]*\} from "\.\.\/\.\.\/\.\.\/types";$/m.test(src),
    "framingModel.ts does not import SkyAngleRecord from types.ts");
  assert(!/\binterface\s+SkyAngleRecord\b|\btype\s+SkyAngleRecord\s*=/.test(src),
    "framingModel.ts declares its own SkyAngleRecord");
  // A copy under another name is the same second truth: no property of the
  // record is declared in this file.
  const declared = src.match(/^\s+(?:pa_deg|solved_at|pier_side|exposed_at)\??:/gm) ?? [];
  eq(declared, [], "sky_angle keys declared in framingModel.ts");
  // The Pick names exactly the keys the model reads off a record, no more.
  const pick = /^export type MeasuredAngle = Pick<SkyAngleRecord, ([^>]*)>;$/m.exec(src);
  assert(pick !== null, "framingModel.ts has no `MeasuredAngle = Pick<SkyAngleRecord, ...>`");
  const picked = (pick![1].match(/"(\w+)"/g) ?? []).map((k) => k.slice(1, -1)).sort();
  const code = src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
  const read = [...new Set([...code.matchAll(/\brec\??\.(\w+)/g)].map((m) => m[1]))].sort();
  eq(picked, read, "the Pick's keys against the keys the model reads (`rec.x`)");
  // And the full record, as the status frame carries it, is one the model
  // takes: this line is tsc's (a MeasuredAngle parameter given the whole
  // types.ts record), so it holds the Pick to types.ts at every `tsc -b`.
  const full: SkyAngleRecord = {
    pa_deg: 37.2449, exposed_at: 1, solved_at: 2, source: "plate solve + sync", pier_side: "west",
    camera: "", calibrated: false, reason: "no rotator is connected",
    mechanical_deg: null, rotator_before_deg: null, offset_deg: null,
  };
  eq(useMeasuredLine(full, 2)?.startsWith("camera measured 37.2 deg"), true, "the full record read");
});

// ================================================ an angle for a grid (#411)
//
// S5 orchestrator ruling 1 (#411; spec 1.8, "a default angle nobody chose is
// exactly the I-04 defect"). S4 gave a draft at ANY ANGLE that became a grid
// ROTATE TO, whose "none" turns into 0, so a DONE after a column change
// commanded the rotator to PA 0, an angle nobody chose, off screen on a phone.
// Now the draft stays at ANY ANGLE and DONE is locked until an angle is
// chosen; with a measurement in `status.sky_angle` the modal OFFERS the
// measured angle, ROTATE TO, or CAMERA FIXED AT on a rig with no rotator, and
// it applies only when pressed. The sheet's half is framingSections.test.tsx.

const MEASURED: MeasuredAngle = {
  pa_deg: 37.2449, exposed_at: 0, solved_at: 0, source: "plate solve + sync", pier_side: "west",
};
const ANY_3x2 = (over: Partial<FramingDraft> = {}) => draftOf(M31, { angle: "Any angle", rotation: -1, ...over });
const ANY_1x1 = () => ANY_3x2({ rows: 1, cols: 1 });

// MUTANT "no lock without an angle" (gridAngleLock: always null). Observed
// (scratch copy s5-modal-mut):
//   x a grid with no angle locks DONE with the ruling's sentence; one panel, or a chosen angle,
//     does not: 3x2 at any angle: expected "a grid is laid out at one camera angle: choose one, or
//     USE MEASURED after a plate solve", got null
test("a grid with no angle locks DONE with the ruling's sentence; one panel, or a chosen angle, does not", () => {
  eq(NO_ANGLE_ON_A_GRID, "a grid is laid out at one camera angle: choose one, or USE MEASURED after a plate solve",
    "the ruling's words");
  eq(gridAngleLock(ANY_3x2()), NO_ANGLE_ON_A_GRID, "3x2 at any angle");
  eq(gridAngleLock(ANY_3x2({ rows: 1, cols: 2 })), NO_ANGLE_ON_A_GRID, "1x2 at any angle");
  // A set mode with a negative rotation holds no angle either (3.1 reads it
  // as none, and doctor M2 refuses it on a grid).
  eq(gridAngleLock(draftOf(M31, { rotation: -1 })), NO_ANGLE_ON_A_GRID, "ROTATE TO with rotation -1");
  eq(gridAngleLock(draftOf(M31)), null, "3x2 at PA 30 (control)");
  eq(gridAngleLock(ANY_1x1()), null, "1x1 at any angle (control)");
});

// MUTANT "the offer ignores the rotator" (angleOffer: `|| rig?.has_rotator ===
// false` deleted, so a rig with no rotator is offered ROTATE TO). Observed
// (scratch copy s5-modal-mut):
//   x the measured angle is offered only to a grid that owes one: ROTATE TO, or CAMERA FIXED AT
//     with no rotator: a rig with no rotator: expected {"mode":"Camera fixed at
//     PA","rotation":37.2,"label":"CAMERA FIXED AT 37.2 deg"}, got {"mode":"Rotate to
//     PA","rotation":37.2,"label":"ROTATE TO 37.2 deg"}
// MUTANT "offer to any draft" (angleOffer: the owed test deleted, so a single
// panel at any angle, and a grid with its angle, are offered one). Observed
// (scratch copy s5-modal-mut):
//   x the measured angle is offered only to a grid that owes one: ROTATE TO, or CAMERA FIXED AT
//     with no rotator: one panel owes no angle (control): expected null, got {"mode":"Rotate to
//     PA","rotation":37.2,"label":"ROTATE TO 37.2 deg"}
test("the measured angle is offered only to a grid that owes one: ROTATE TO, or CAMERA FIXED AT with no rotator", () => {
  eq(angleOffer(ANY_3x2(), MEASURED, RIG), { mode: "Rotate to PA", rotation: 37.2, label: "ROTATE TO 37.2 deg" },
    "a rig with a rotator");
  eq(angleOffer(ANY_3x2(), MEASURED, { ...RIG, has_rotator: false }),
    { mode: "Camera fixed at PA", rotation: 37.2, label: "CAMERA FIXED AT 37.2 deg" }, "a rig with no rotator");
  // Unknown is not no (rig.py), as ROTATE TO's own lock reads it.
  eq(angleOffer(ANY_3x2(), MEASURED, { ...RIG, has_rotator: null })?.mode, "Rotate to PA", "unknown rotator");
  eq(angleOffer(ANY_3x2(), MEASURED, null)?.mode, "Rotate to PA", "no rig block");
  // A block already set to CAMERA FIXED AT, with no usable angle, keeps the
  // mode the operator chose: the offer supplies only the angle.
  eq(angleOffer(draftOf(M31, { angle: "Camera fixed at PA", rotation: -1 }), MEASURED, RIG)?.mode,
    "Camera fixed at PA", "a fixed camera keeps its mode");
  eq(angleOffer(ANY_3x2(), null, RIG), null, "no measurement");
  eq(angleOffer(ANY_3x2(), { ...MEASURED, pa_deg: NaN }, RIG), null, "no finite angle");
  eq(angleOffer(ANY_1x1(), MEASURED, RIG), null, "one panel owes no angle (control)");
  eq(angleOffer(draftOf(M31), MEASURED, RIG), null, "a grid with its angle (control)");
  // Taking it changes the angle and nothing else, and pays the angle owed.
  const took = takeOffer(ANY_3x2(), angleOffer(ANY_3x2(), MEASURED, RIG)!);
  eq(framingPatch(M31, took), { rotation: 37.2 }, "the patch against the stored 3x2 at ROTATE TO 30");
  eq(gridAngleLock(took), null, "the lock once the offer is taken");
});

// MUTANT "strip angle without its mode" (stripAngle: the bare `${deg}` for
// every set mode). Observed (scratch copy s5-modal-mut):
//   x the readout strip's angle line says the angle the grid is laid out at: ROTATE TO: expected
//     "rotate to 30.0 deg", got "30.0 deg"
test("the readout strip's angle line says the angle the grid is laid out at", () => {
  eq(stripAngle(draftOf(M31)), "rotate to 30.0 deg", "ROTATE TO");
  eq(stripAngle(draftOf(M31, { angle: "Camera fixed at PA", rotation: 37.2 })), "camera fixed at 37.2 deg", "CAMERA FIXED AT");
  eq(stripAngle(ANY_1x1()), "any angle", "one panel at any angle");
  eq(stripAngle(ANY_3x2()), "no angle", "a grid at any angle");
  eq(stripAngle(draftOf(M31, { rotation: -1 })), "no angle", "ROTATE TO with no usable angle");
});

// MUTANT "no conservative clause" (toleranceLine: the single row or column branch never taken).
// Observed:
//   x the tolerance line from A.2: 1x4: expected "this layout tolerates a camera error of 1.7 deg;
//     a single row or column has no four-panel corner where a hole could open, so this is
//     conservative", got "this layout tolerates a camera error of 1.7 deg (convergence and angle
//     error together use at most half the overlap at a four-panel corner)"
// MUTANT "single-panel tolerance" (toleranceLine: the one-panel test deleted). Observed:
//   x the tolerance line from A.2: one panel: expected null, got "this layout tolerates a camera
//     error of 6.4 deg; a single row or column has no four-panel corner where a hole could open,
//     so this is conservative"
// MUTANT "M15 as a number" (toleranceLine: `< 0` for `<= 0`). Observed:
//   x the tolerance line from A.2: M15: got "this layout tolerates a camera error of 0.0 deg
//     (convergence and angle error together use at most half the overlap at a four-panel corner)"
test("the tolerance line from A.2", () => {
  eq(toleranceLine(5.97, draftOf(M31, { rows: 3, cols: 3 })),
    "this layout tolerates a camera error of 6.0 deg (convergence and angle error together use at most half the overlap at a four-panel corner)", "3x3 at Dec 41");
  eq(toleranceLine(1.73, draftOf(M31, { rows: 1, cols: 4 })),
    "this layout tolerates a camera error of 1.7 deg; a single row or column has no four-panel corner where a hole could open, so this is conservative", "1x4");
  const m15 = toleranceLine(0, draftOf(M31));
  assert(String(m15).startsWith("meridian convergence has used"), `M15: got ${js(m15)}`);
  eq(toleranceLine(6.36, draftOf(M31, { rows: 1, cols: 1 })), null, "one panel");
  eq(toleranceLine(null, draftOf(M31)), null, "unknown");
});

// THE RUN FIXTURES ARE THE SERVER'S OWN ANSWERS, never written by hand. Each
// block below was recorded from server `flows/readouts.py` `readouts()`, the
// function `_compile_payload` answers the `readouts` key with, for the graphs
// of server/tests/test_flows_readouts.py (`_looped()`: a 3x2 of 2.0 x 1.33
// deg at Dec +41 on the SHIPPED DEFAULT FILTER CYCLE, L R G B at 60 s and Ha
// OIII SII at 180 s, 45 cycles, one per pass, with the loop wire), by
// scratchpad S4-UMODEL-verify-srv's record.py. If the server's answer changes
// on purpose, record them again. The route's own recorded answer for the
// eighth Example (server/tests/fixtures/flow_readouts_m31.json) is READ, not
// copied, by the test after these, which also holds the two types' keys to
// it, so a renamed field on either side fails here instead of printing
// "undefined filters" in the modal.

/** `_looped()` with a temperature delta armed (refocus_on_temp_delta_c 1.0).
 *  Spec 5.5 and A.3: 7 x 45 = 315 subs a panel, 780 s a pass, 9.75 h a
 *  panel, 58.5 h and 1890 subs in all, 270 visits at one pass per visit. */
const DEFAULT_3x2: RunReadouts = {
  node_id: "t", mode: "rotate", panels: 6, steps: 7, rounds: 45,
  subs_per_panel: 315, subs_total: 1890, pass_s: 780.0, panel_s: 35100.0, total_s: 210600.0,
  passes: 1, visit_min_s: 0.0, visit_passes: 1, visits_per_panel: 45, visits_total: 270,
  hop_s: 150.0, hop_measured: false, preflip_idle_s: 0.0, angle_tolerance_deg: 5.974,
  focus: "temperature", autofocus_every: 0, refocus_delta_c: 1.0,
};
/** `_looped()` on a rig that has timed a 160 s hop over 6 hops, no focus
 *  setting armed: the server adds `efficiency`, 210600 / (210600 + 270 x
 *  160) = 0.8298. Typed Required, so tsc -b fails if RunReadouts gains a key
 *  this record does not carry, or loses one it does. */
const MEASURED_3x2: Required<RunReadouts> = {
  node_id: "t", mode: "rotate", panels: 6, steps: 7, rounds: 45,
  subs_per_panel: 315, subs_total: 1890, pass_s: 780.0, panel_s: 35100.0, total_s: 210600.0,
  passes: 1, visit_min_s: 0.0, visit_passes: 1, visits_per_panel: 45, visits_total: 270,
  hop_s: 160.0, hop_measured: true, preflip_idle_s: 0.0, angle_tolerance_deg: 5.974,
  efficiency: 0.8298, focus: "once", autofocus_every: 0, refocus_delta_c: 0.0,
};
/** `rig_readout` for that rig. */
const MEASURED_RIG: Required<RigBlock> = {
  fov_deg: null, fov_from: "", has_rotator: null, hop_s: 160.0, hop_samples: 6, hop_measured: true,
};
/** `_looped()` without the loop wire: panel-first, one visit per panel. */
const SEQUENTIAL_3x2: RunReadouts = {
  node_id: "t", mode: "sequential", panels: 6, steps: 7, rounds: 45,
  subs_per_panel: 315, subs_total: 1890, pass_s: 780.0, panel_s: 35100.0, total_s: 210600.0,
  passes: 1, visit_min_s: 0.0, visit_passes: null, visits_per_panel: 1, visits_total: 6,
  hop_s: 150.0, hop_measured: false, preflip_idle_s: 0.0, angle_tolerance_deg: 5.974,
  focus: "once", autofocus_every: 0, refocus_delta_c: 0.0,
};
/** A single TARGET (M42) owning one CAPTURE of 3 x 60 s: no hops, no group
 *  rule, no layout, so every group number is null, `hop_measured` too, as
 *  the server sends it (this record once said `false`, which hid the
 *  reader's refusal of the real answer: framingReadoutsFixture.test.ts now
 *  reads the route's own single-target answer). */
const SINGLE: RunReadouts = {
  node_id: "t", mode: "single", panels: 1, steps: 1, rounds: null,
  subs_per_panel: 3, subs_total: 3, pass_s: null, panel_s: 180.0, total_s: 180.0,
  passes: null, visit_min_s: null, visit_passes: null, visits_per_panel: null, visits_total: null,
  hop_s: null, hop_measured: null, preflip_idle_s: null, angle_tolerance_deg: null,
  focus: "once", autofocus_every: 0, refocus_delta_c: 0.0,
};

// MUTANT "hours in minutes" (hours(): seconds / 60). Observed:
//   x the RUN lines reproduce the spec's worked strings for the default cycle on a 3x2: default:
//     expected ["6 panels x 7 filters x 45 = 1890 subs","9.75 h per panel, 58.5 h in all","270
//     visits at 1 pass per visit","hop: not measured on this rig yet","meridian: no idle before the
//     flip","focus: refocus on temperature"], got ["6 panels x 7 filters x 45 = 1890 subs","585 h
//     per panel, 3510 h in all","270 visits at 1 pass per visit","hop: not measured on this rig
//     yet","meridian: no idle before the flip","focus: refocus on temperature"]
// MUTANT "passes never singular" (runLines: `${r.visit_passes} passes`). Observed:
//   x the RUN lines reproduce the spec's worked strings for the default cycle on a 3x2: default:
//     expected ["6 panels x 7 filters x 45 = 1890 subs","9.75 h per panel, 58.5 h in all","270
//     visits at 1 pass per visit","hop: not measured on this rig yet","meridian: no idle before the
//     flip","focus: refocus on temperature"], got ["6 panels x 7 filters x 45 = 1890 subs","9.75 h
//     per panel, 58.5 h in all","270 visits at 1 passes per visit","hop: not measured on this rig
//     yet","meridian: no idle before the flip","focus: refocus on temperature"]
// MUTANT "meridian in seconds" (runLines: the idle in seconds). Observed:
//   x the RUN lines reproduce the spec's worked strings for the default cycle on a 3x2: measured
//     hop, the compact 2x2's idle, no refocus: expected ["hop 2 m 40 s, measured over 6 hops; a
//     visit is 83% shutter","meridian: up to 10.6 min idle before the flip","focus: a sweep only at
//     the first panel; set a temperature delta to refocus as the night cools"], got ["hop 2 m 40 s,
//     measured over 6 hops; a visit is 83% shutter","meridian: up to 636.0 min idle before the
//     flip","focus: a sweep only at the first panel; set a temperature delta to refocus as the
//     night cools"]
// MUTANT "hop read as RigFacts names it" (runLines: `rig.hop_cost_s`, the name the model first
// declared, for the route's `rig.hop_s`). Observed:
//   x the RUN lines reproduce the spec's worked strings for the default cycle on a 3x2: measured
//     hop, the compact 2x2's idle, no refocus: expected ["hop 2 m 40 s, measured over 6 hops; a
//     visit is 83% shutter","meridian: up to 10.6 min idle before the flip","focus: a sweep only at
//     the first panel; set a temperature delta to refocus as the night cools"], got ["hop: not
//     measured on this rig yet","meridian: up to 10.6 min idle before the flip","focus: a sweep
//     only at the first panel; set a temperature delta to refocus as the night cools"]
// MUTANT "efficiency rounds down" (runLines: Math.floor for Math.round on 0.8298). Observed:
//   x the RUN lines reproduce the spec's worked strings for the default cycle on a 3x2: measured
//     hop, the compact 2x2's idle, no refocus: expected ["hop 2 m 40 s, measured over 6 hops; a
//     visit is 83% shutter","meridian: up to 10.6 min idle before the flip","focus: a sweep only at
//     the first panel; set a temperature delta to refocus as the night cools"], got ["hop 2 m 40 s,
//     measured over 6 hops; a visit is 82% shutter","meridian: up to 10.6 min idle before the
//     flip","focus: a sweep only at the first panel; set a temperature delta to refocus as the
//     night cools"]
test("the RUN lines reproduce the spec's worked strings for the default cycle on a 3x2", () => {
  eq(runLines(DEFAULT_3x2, RIG), [
    "6 panels x 7 filters x 45 = 1890 subs",
    "9.75 h per panel, 58.5 h in all",
    "270 visits at 1 pass per visit",
    "hop: not measured on this rig yet",
    "meridian: no idle before the flip",
    "focus: refocus on temperature",
  ], "default");
  // The spec's measured hop line, then the server's efficiency figure.
  const measured = runLines({ ...MEASURED_3x2, preflip_idle_s: 636 }, MEASURED_RIG);
  eq(measured.slice(3), [
    "hop 2 m 40 s, measured over 6 hops; a visit is 83% shutter",
    "meridian: up to 10.6 min idle before the flip",
    "focus: a sweep only at the first panel; set a temperature delta to refocus as the night cools",
  ], "measured hop, the compact 2x2's idle, no refocus");
});

// MUTANT "hop without the measured flag" (runLines: `rig.hop_measured === true &&` deleted).
// Observed:
//   x a hop line needs a measured hop; the efficiency is the server's; the product must be the
//     total: a hop the route does not call measured: expected "hop: not measured on this rig yet",
//     got "hop 2 m 40 s, measured over 6 hops"
// MUTANT "hop without samples" (runLines: `&& samples >= 1` deleted). Observed:
//   x a hop line needs a measured hop; the efficiency is the server's; the product must be the
//     total: no samples: expected "hop: not measured on this rig yet", got "hop 2 m 40 s, measured
//     over 0 hops"
// MUTANT "efficiency from the seed" (runLines: the efficiency clause printed from the block's
// `hop_s` whether or not it was measured). Observed:
//   x a hop line needs a measured hop; the efficiency is the server's; the product must be the
//     total: a hop the route does not call measured: expected "hop: not measured on this rig yet",
//     got "hop 2 m 30 s; a visit is 84% shutter"
// MUTANT "product line always" (runLines: the product-equals-total check deleted). Observed:
//   x a hop line needs a measured hop; the efficiency is the server's; the product must be the
//     total: steps owing different counts: expected "2010 subs over 6 panels and 8 filters", got "6
//     panels x 8 filters x 45 = 2010 subs"
test("a hop line needs a measured hop; the efficiency is the server's; the product must be the total", () => {
  eq(runLines(DEFAULT_3x2, { ...MEASURED_RIG, hop_measured: false })[3], "hop: not measured on this rig yet", "a hop the route does not call measured");
  eq(runLines(DEFAULT_3x2, { ...MEASURED_RIG, hop_samples: 0 })[3], "hop: not measured on this rig yet", "no samples");
  // A measured hop with no efficiency in the block (the server adds it only
  // with a measured hop, so this is the spec's bare line).
  eq(runLines(DEFAULT_3x2, MEASURED_RIG)[3], "hop 2 m 40 s, measured over 6 hops", "the spec's measured line");
  eq(runLines(MEASURED_3x2, RIG)[3], "hop: not measured on this rig yet", "no hop line from the block's own numbers");
  eq(runLines({ ...DEFAULT_3x2, preflip_idle_s: null }, RIG).some((l) => l.startsWith("meridian")), false, "no flip, no meridian line");
  // The default cycle plus a CAPTURE of Ha x 20 in the lane: 8 steps, 335
  // subs a panel, and 6 x 8 x 45 = 2160 is not the server's 2010.
  eq(runLines({ ...DEFAULT_3x2, steps: 8, subs_per_panel: 335, subs_total: 2010 }, RIG)[0],
    "2010 subs over 6 panels and 8 filters", "steps owing different counts");
  eq(runLines({ ...DEFAULT_3x2, focus: "frames", autofocus_every: 30 }, RIG)[5], "focus: a sweep every 30 frames", "every N frames");
  // "frames" with no count is not a cadence (readouts.py answers "frames" only
  // when `autofocus_every` is set, so this is the guard, pinned).
  // MUTANT "frames focus without a count" (runLines: the `autofocus_every > 0` test deleted). Observed:
  //   x a hop line needs a measured hop; the efficiency is the server's; the product must be the
  //     total: frames with no count: expected "focus: a sweep only at the first panel; set a
  //     temperature delta to refocus as the night cools", got "focus: a sweep every 0 frames"
  eq(runLines({ ...DEFAULT_3x2, focus: "frames", autofocus_every: 0 }, RIG)[5],
    "focus: a sweep only at the first panel; set a temperature delta to refocus as the night cools", "frames with no count");
});

// MUTANT "sequential as passes" (runLines: the panel-first line deleted, so a null bound prints
// as passes). Observed:
//   x a panel-first block and a single target read what the server answers for them: panel-first:
//     expected "6 visits: each panel runs to completion", got "6 visits at null passes per visit"
// MUTANT "single target hops" (runLines: the visits and hop lines written with no visits).
// Observed (re-run in scratch copy s5-modal-mut on the single target's #413 wording):
//   x a panel-first block and a single target read what the server answers for them: a single
//     target: no visits, no hops, no flip line: expected ["3 subs over 1 panel and 1 filter","0.05
//     h per panel, 0.05 h in all","focus: a sweep only at the start; set a temperature delta to
//     refocus as the night cools"], got ["3 subs over 1 panel and 1 filter","0.05 h per panel,
//     0.05 h in all","null visits: each panel runs to completion","hop 2 m 40 s, measured over 6
//     hops","focus: a sweep only at the start; set a temperature delta to refocus as the night
//     cools"]
test("a panel-first block and a single target read what the server answers for them", () => {
  eq(runLines(SEQUENTIAL_3x2, RIG)[2], "6 visits: each panel runs to completion", "panel-first");
  eq(runLines(SINGLE, MEASURED_RIG), [
    "3 subs over 1 panel and 1 filter",
    "0.05 h per panel, 0.05 h in all",
    "focus: a sweep only at the start; set a temperature delta to refocus as the night cools",
  ], "a single target: no visits, no hops, no flip line");
});

// A single target has no first panel (#413): "once" is a sweep at the start
// of the target, and the mosaic's wording told the operator about panels the
// block does not have. framingSections.test.tsx grades the same line on the
// route's recorded single-target answer (flow_readouts_single.json).
// MUTANT "single focus as a mosaic's" (runLines: the `mode === "single"`
// wording of the once line deleted). Observed (scratch copy s5-modal-mut):
//   x a single target's once-only focus is a sweep at the start; a mosaic's is at the first panel:
//     a single target: expected "focus: a sweep only at the start; set a temperature delta to
//     refocus as the night cools", got "focus: a sweep only at the first panel; set a temperature
//     delta to refocus as the night cools"
//   (The single target's case above went red with it too.)
test("a single target's once-only focus is a sweep at the start; a mosaic's is at the first panel", () => {
  const last = (l: string[]) => l[l.length - 1];
  const START = "focus: a sweep only at the start; set a temperature delta to refocus as the night cools";
  const FIRST = "focus: a sweep only at the first panel; set a temperature delta to refocus as the night cools";
  eq(last(runLines(SINGLE, RIG)), START, "a single target");
  eq(last(runLines({ ...SINGLE, focus: "frames", autofocus_every: 0 }, RIG)), START, "a single target, frames with no count");
  // Controls: a mosaic keeps its first panel, and a cadence is a cadence.
  eq(last(runLines(SEQUENTIAL_3x2, RIG)), FIRST, "a panel-first mosaic");
  eq(last(runLines({ ...MEASURED_3x2 }, MEASURED_RIG)), FIRST, "a rotating mosaic");
  eq(last(runLines({ ...SINGLE, focus: "temperature" }, RIG)), "focus: refocus on temperature", "a single target on temperature");
});

const READOUTS_REL = "../../../../../../server/tests/fixtures/flow_readouts_m31.json";

// The route's recorded answer for the eighth Example on a rig that knows
// nothing, written by the server's `readouts` and `rig_readout` and pinned by
// test_flows_readouts.py against both the function and the route. Its rig
// has timed no hop, so the hop name is held by the measured cases above.
// MUTANT "steps read as filters" (runLines: `r.filters`, the name the model first declared).
// Observed:
//   x the RUN lines read the route's recorded answer, and the types carry its keys: the eighth
//     Example: expected ["6 panels x 4 filters x 20 = 480 subs","2.67 h per panel, 16 h in
//     all","120 visits at 1 pass per visit","hop: not measured on this rig yet","meridian: up to
//     6.1 min idle before the flip","focus: a sweep only at the first panel; set a temperature
//     delta to refocus as the night cools"], got ["6 panels x undefined filters x 20 = 480
//     subs","2.67 h per panel, 16 h in all","120 visits at 1 pass per visit","hop: not measured on
//     this rig yet","meridian: up to 6.1 min idle before the flip","focus: a sweep only at the
//     first panel; set a temperature delta to refocus as the night cools"]
// MUTANT "rig renamed on this side only" (RigBlock's `hop_s` renamed `hop_cost_s` in the type,
// its reader and this file's records together, so tsc passes). Observed:
//   x the RUN lines read the route's recorded answer, and the types carry its keys: RigBlock keys:
//     expected ["fov_deg","fov_from","has_rotator","hop_cost_s","hop_measured","hop_samples"], got
//     ["fov_deg","fov_from","has_rotator","hop_measured","hop_s","hop_samples"]
// Renaming the type alone fails `tsc -b` instead, on both records. Observed:
//   error TS2353: Object literal may only specify known properties, and 'hop_s' does not exist in
//     type 'Required<RigBlock>'.
test("the RUN lines read the route's recorded answer, and the types carry its keys", () => {
  let text: string;
  try {
    text = readFileSync(new URL(READOUTS_REL, import.meta.url), "utf8") as string;
  } catch (e) {
    throw new Error(`cannot read ${READOUTS_REL}, the route answer these types copy: ${(e as Error).message}`);
  }
  const fx = JSON.parse(text) as { readouts: Record<string, RunReadouts>; rig: RigBlock };
  const block = fx.readouts.n2;
  assert(block !== undefined, `${READOUTS_REL} has no block n2`);
  eq(runLines(block, fx.rig), [
    "6 panels x 4 filters x 20 = 480 subs",
    "2.67 h per panel, 16 h in all",
    "120 visits at 1 pass per visit",
    "hop: not measured on this rig yet",
    "meridian: up to 6.1 min idle before the flip",
    "focus: a sweep only at the first panel; set a temperature delta to refocus as the night cools",
  ], "the eighth Example");
  // The keys: the route's block and rig against records tsc holds to the two
  // types (`efficiency` is absent from a rig with no measured hop).
  const keys = (o: object) => Object.keys(o).sort();
  eq(keys(block), keys(MEASURED_3x2).filter((k) => k !== "efficiency"), "RunReadouts keys");
  eq(keys(fx.rig), keys(RIG), "RigBlock keys");
});

// MUTANT "round half up" (hopDuration: Math.round). Observed:
//   x a hop is spelled as tonight.py _duration spells it, Python's rounding included: 2.5 s:
//     expected "2 s", got "3 s"
test("a hop is spelled as tonight.py _duration spells it, Python's rounding included", () => {
  eq(hopDuration(2.5), "2 s", "2.5 s");
  eq(hopDuration(3.5), "4 s", "3.5 s");
  eq(hopDuration(45), "45 s", "45 s");
  eq(hopDuration(160), "2 m 40 s", "160 s");
  eq(hopDuration(119.6), "2 m 0 s", "119.6 s");
});

// ============================================================ skip mirror

const FIXTURE_REL = "../../../../../../server/tests/fixtures/skip_cases.json";
interface SkipCase { id: string; text: string; rows: number; cols: number; skip: [number, number][]; unread: string[] }

/** The server's cases. A missing or unreadable file must FAIL, never skip:
 *  a skipped fixture reads as a green mirror. */
function readSkipCases(): SkipCase[] {
  let text: string;
  try {
    text = readFileSync(new URL(FIXTURE_REL, import.meta.url), "utf8") as string;
  } catch (e) {
    throw new Error(`cannot read ${FIXTURE_REL}, the cases this mirror is graded against: ${(e as Error).message}`);
  }
  const cases = (JSON.parse(text) as { cases?: SkipCase[] }).cases;
  if (!Array.isArray(cases) || cases.length < 20) throw new Error(`${FIXTURE_REL} holds too few cases`);
  return cases;
}

// MUTANT "mirror accepts 0-1" (parseSkip: `r >= 1` made `r >= 0`). Observed:
//   x the mirror reads every server skip case as parse_skip does: row zero names no panel ("0-1"
//     on 3x3): expected {"skip":[],"unread":["0-1"]}, got {"skip":[[0,1]],"unread":[]}
// MUTANT "JS trim" (parseSkip: pyStrip(chunk) made chunk.trim()). Observed:
//   x the mirror reads every server skip case as parse_skip does: Python strips the file
//     separator, JS trim does not ("\u001c1-2\u001c" on 3x3): expected
//     {"skip":[[1,2]],"unread":[]}, got {"skip":[],"unread":["\u001c1-2\u001c"]}; Python strips
//     NEL ("<U+0085>1-2" on 3x3): expected {"skip":[[1,2]],"unread":[]}, got
//     {"skip":[],"unread":["<U+0085>1-2"]}; Python keeps a BOM, JS trim strips it ("<U+FEFF>1-2"
//     on 3x3): expected {"skip":[],"unread":["<U+FEFF>1-2"]}, got {"skip":[[1,2]],"unread":[]}
// MUTANT "ASCII digits" (SKIP_RE: \p{Nd} made \d). Observed:
//   x the mirror reads every server skip case as parse_skip does: fullwidth digit three
//     ("<U+FF13>-1" on 3x3): expected {"skip":[[3,1]],"unread":[]}, got
//     {"skip":[],"unread":["<U+FF13>-1"]}; Arabic-Indic digits ("<U+0663>-<U+0661>" on 3x3):
//     expected {"skip":[[3,1]],"unread":[]}, got {"skip":[],"unread":["<U+0663>-<U+0661>"]};
//     mathematical bold digit two (outside the BMP) ("<U+1D7D0>-1" on 3x3): expected
//     {"skip":[[2,1]],"unread":[]}, got {"skip":[],"unread":["<U+1D7D0>-1"]}
// MUTANT "mirror keeps duplicates" (parseSkip: the seen-set deleted). Observed:
//   x the mirror reads every server skip case as parse_skip does: a panel named twice is skipped
//     once ("2-2,2-2" on 3x3): expected {"skip":[[2,2]],"unread":[]}, got
//     {"skip":[[2,2],[2,2]],"unread":[]}
// MUTANT "mirror in typed order" (parseSkip: the sort deleted). Observed:
//   x the mirror reads every server skip case as parse_skip does: grid order, not typed order
//     ("3-3, 1-1, 2-1" on 3x3): expected {"skip":[[1,1],[2,1],[3,3]],"unread":[]}, got
//     {"skip":[[3,3],[1,1],[2,1]],"unread":[]}
test("the mirror reads every server skip case as parse_skip does", () => {
  const bad: string[] = [];
  for (const c of readSkipCases()) {
    const got = parseSkip(c.text, c.rows, c.cols);
    const want = { skip: c.skip, unread: c.unread };
    if (js(got) !== js(want)) bad.push(`${c.id} (${js(c.text)} on ${c.rows}x${c.cols}): expected ${js(want)}, got ${js(got)}`);
  }
  assert(bad.length === 0, bad.join("; "));
});

// The one case the shared table cannot hold, because parse_skip has no
// answer for it: past CPython's 4300-digit limit `int()` raises, leading
// zeros counted (checked against the server's Python: 4299 zeros and a 1
// read as panel 1-1, 5000 zeros and a 1 raise ValueError). The server cannot
// read the entry, so the modal must not draw it as a skipped panel (#328).
// MUTANT "no digit limit" (pyInt: the PY_INT_MAX_DIGITS test deleted). Observed:
//   x an entry past Python's 4300-digit limit names no panel, as the server cannot read it: 5001
//     digits whose value is 1: expected [[],1], got [[[1,1]],0]
test("an entry past Python's 4300-digit limit names no panel, as the server cannot read it", () => {
  const past = parseSkip("0".repeat(5000) + "1-1", 3, 3);
  eq([past.skip, past.unread.length], [[], 1], "5001 digits whose value is 1");
  eq(parseSkip("0".repeat(4299) + "1-1", 3, 3).skip, [[1, 1]], "4300 digits (control)");
});

// MUTANT "toggle drops unread" (toggleSkip: returns formatSkip(next) alone). Observed:
//   x a tap toggles one panel, keeps grid order and keeps what the grid cannot read: keeps unread:
//     expected "1-1, 2-3, 4-1", got "1-1, 2-3"
// MUTANT "toggle off the grid adds" (toggleSkip: the off-grid test deleted). Observed:
//   x a tap toggles one panel, keeps grid order and keeps what the grid cannot read: a panel off
//     the grid changes nothing: expected "2-3", got "2-3, 3-1"
test("a tap toggles one panel, keeps grid order and keeps what the grid cannot read", () => {
  eq(toggleSkip("2-3", 2, 3, 1, 1), "1-1, 2-3", "adds in grid order");
  eq(toggleSkip("1-1, 2-3", 2, 3, 1, 1), "2-3", "takes one back");
  eq(toggleSkip("2-3, 4-1", 2, 3, 1, 1), "1-1, 2-3, 4-1", "keeps unread");
  eq(toggleSkip("2-3", 2, 3, 3, 1), "2-3", "a panel off the grid changes nothing");
  eq(toggleSkip("", 2, 3, 2, 2), "2-2", "from nothing");
  eq(toggleSkip("2-2", 2, 3, 2, 2), "", "back to nothing");
  eq(formatSkip([[3, 2], [1, 1], [3, 2]]), "1-1, 3-2", "format: grid order, once");
  // Round trip: what the formatter writes, the server's reader reads back.
  eq(parseSkip(formatSkip([[2, 1], [1, 3]]), 2, 3).skip, [[1, 3], [2, 1]], "round trip");
});

// ============================================== per-viewer storage (2.7)

function memoryStorage(): StorageLike & { data: Map<string, string> } {
  const data = new Map<string, string>();
  return { data, getItem: (k) => data.get(k) ?? null, setItem: (k, v) => { data.set(k, v); } };
}
const throwingStorage: StorageLike = {
  getItem: () => { throw new Error("SecurityError: storage is disabled"); },
  setItem: () => { throw new Error("QuotaExceededError"); },
};

// MUTANT "key without the flow" (viewPrefsKey: the flow id left out). Observed:
//   x survey and zoom are kept per viewer, keyed by flow and node: another flow's n1: expected
//     {"survey":"CDS/P/DSS2/color","zoomDeg":null}, got
//     {"survey":"CDS/P/2MASS/color","zoomDeg":2.5}
// MUTANT "unsaved flow keyed" (viewPrefsKey: an unsaved flow keyed). Observed:
//   x survey and zoom are kept per viewer, keyed by flow and node: an unsaved flow has no key:
//     expected null, got "astrodeck-framing-view:/n1"
// MUTANT "bad stored zoom accepted" (zoomOf: any number). Observed:
//   x survey and zoom are kept per viewer, keyed by flow and node: a stored value that is not one:
//     expected {"survey":"CDS/P/DSS2/color","zoomDeg":null}, got
//     {"survey":"CDS/P/DSS2/color","zoomDeg":900}
// MUTANT "partial save drops survey" (saveViewPrefs: a missing survey saved as the default).
// Observed:
//   x survey and zoom are kept per viewer, keyed by flow and node: a partial save keeps the
//     survey: expected {"survey":"CDS/P/2MASS/color","zoomDeg":4}, got
//     {"survey":"CDS/P/DSS2/color","zoomDeg":4}
test("survey and zoom are kept per viewer, keyed by flow and node", () => {
  const s = memoryStorage();
  assert(saveViewPrefs("flowA", "n1", { survey: "CDS/P/2MASS/color", zoomDeg: 2.5 }, s), "saved");
  eq(loadViewPrefs("flowA", "n1", s), { survey: "CDS/P/2MASS/color", zoomDeg: 2.5 }, "round trip");
  eq(loadViewPrefs("flowB", "n1", s), VIEW_PREFS_DEFAULT, "another flow's n1");
  eq(loadViewPrefs("flowA", "n2", s), VIEW_PREFS_DEFAULT, "another node on the flow");
  saveViewPrefs("flowA", "n1", { zoomDeg: 4 }, s);
  eq(loadViewPrefs("flowA", "n1", s), { survey: "CDS/P/2MASS/color", zoomDeg: 4 }, "a partial save keeps the survey");
  // MUTANT "survey-only save drops the zoom" (saveViewPrefs: a missing zoomDeg
  // saved as none). Observed:
  //   x survey and zoom are kept per viewer, keyed by flow and node: a partial save keeps the zoom:
  //     expected {"survey":"CDS/P/DSS2/red","zoomDeg":4}, got
  //     {"survey":"CDS/P/DSS2/red","zoomDeg":null}
  saveViewPrefs("flowA", "n1", { survey: "CDS/P/DSS2/red" }, s);
  eq(loadViewPrefs("flowA", "n1", s), { survey: "CDS/P/DSS2/red", zoomDeg: 4 }, "a partial save keeps the zoom");
  // Each id is encoded, so a slash in one cannot make two blocks one key.
  // MUTANT "key not encoded" (viewPrefsKey: encodeURIComponent deleted; it also
  // failed the lone-surrogate test below, which it stored). Observed:
  //   x survey and zoom are kept per viewer, keyed by flow and node: flow "a/b" node "c" and flow
  //     "a" node "b/c" share the key astrodeck-framing-view:a/b/c
  assert(viewPrefsKey("a/b", "c") !== viewPrefsKey("a", "b/c"), `flow "a/b" node "c" and flow "a" node "b/c" share the key ${viewPrefsKey("a", "b/c")}`);
  eq(viewPrefsKey("", "n1"), null, "an unsaved flow has no key");
  assert(!saveViewPrefs("", "n1", { zoomDeg: 3 }, s) && s.data.size === 1, "an unsaved flow writes nothing");
  s.data.set(viewPrefsKey("flowA", "n1")!, '{"survey":"","zoomDeg":900}');
  eq(loadViewPrefs("flowA", "n1", s), VIEW_PREFS_DEFAULT, "a stored value that is not one");
  s.data.set(viewPrefsKey("flowA", "n1")!, "{not json");
  eq(loadViewPrefs("flowA", "n1", s), VIEW_PREFS_DEFAULT, "text that is not JSON");
});

// MUTANT "storage read unguarded" (loadViewPrefs: the try/catch removed). Observed:
//   x a throwing storage returns the defaults and never throws: read threw: SecurityError: storage
//     is disabled
// MUTANT "storage write unguarded" (saveViewPrefs: the catch removed). Observed:
//   x a throwing storage returns the defaults and never throws: write threw: QuotaExceededError
test("a throwing storage returns the defaults and never throws", () => {
  let got: unknown;
  try { got = loadViewPrefs("flowA", "n1", throwingStorage); } catch (e) { throw new Error(`read threw: ${(e as Error).message}`); }
  eq(got, VIEW_PREFS_DEFAULT, "read");
  let ok: unknown;
  try { ok = saveViewPrefs("flowA", "n1", { zoomDeg: 2 }, throwingStorage); } catch (e) { throw new Error(`write threw: ${(e as Error).message}`); }
  eq(ok, false, "write");
});

// A node id is whatever string the flow file holds, and encodeURIComponent
// throws URIError on a lone surrogate. The sheet reads the prefs in a useState
// initializer, so a throw here would take the modal down: such a block gets
// no key, is not stored, and opens on the defaults (#386).
// MUTANT "key encoding unguarded" (viewPrefsKey: the try round the encoding
// removed, as first built). Observed:
//   x an id the key cannot spell is not stored, and never throws: threw: URIError: URI malformed
test("an id the key cannot spell is not stored, and never throws", () => {
  const s = memoryStorage();
  const id = "n" + String.fromCharCode(0xd800);
  let got: unknown;
  try {
    got = [viewPrefsKey("flowA", id), loadViewPrefs("flowA", id, s), saveViewPrefs("flowA", id, { zoomDeg: 2 }, s), s.data.size];
  } catch (e) { throw new Error(`threw: ${(e as Error).name}: ${(e as Error).message}`); }
  eq(got, [null, VIEW_PREFS_DEFAULT, false, 0], "a lone surrogate in the node id");
  eq(viewPrefsKey("flowA", "n1"), "astrodeck-framing-view:flowA/n1", "an ordinary id keeps its key (control)");
});

// MUTANT "global read outside try" (loadViewPrefs: resolveStorage hoisted above the try).
// Observed:
//   x reading localStorage itself may throw, and still lands on the defaults: global access threw:
//     SecurityError: The operation is insecure.
test("reading localStorage itself may throw, and still lands on the defaults", () => {
  const g = globalThis as any;
  const had = Object.getOwnPropertyDescriptor(g, "localStorage");
  Object.defineProperty(g, "localStorage", {
    configurable: true, get() { throw new Error("SecurityError: The operation is insecure."); },
  });
  try {
    let got: unknown;
    try { got = loadViewPrefs("flowA", "n1"); } catch (e) { throw new Error(`global access threw: ${(e as Error).message}`); }
    eq(got, VIEW_PREFS_DEFAULT, "global read");
    let ok: unknown;
    try { ok = saveViewPrefs("flowA", "n1", { zoomDeg: 2 }); } catch (e) { throw new Error(`global write threw: ${(e as Error).message}`); }
    eq(ok, false, "global write");
  } finally {
    if (had) Object.defineProperty(g, "localStorage", had); else delete g.localStorage;
  }
});

// ================================================== run mode (2.6, S5)
//
// What run mode draws of the live group, read off the rig's recorded state
// (server/tests/fixtures/sequence_state_mosaic.json, READ, NOT COPIED; built
// and graded byte for byte by test_s5_recorded_state.py): a rotating 2x2
// whose 1-1 is being shot while 2-2 is set aside, and a meridian wait as an
// operator and as a viewer is served it. The sheet's half, mounted on the
// store with the progress route's recorded answer, is runMode.test.tsx.
const STATE_REL = "../../../../../../server/tests/fixtures/sequence_state_mosaic.json";
function recordedStates(): Record<string, SequenceState> {
  let text: string;
  try {
    text = readFileSync(new URL(STATE_REL, import.meta.url), "utf8") as string;
  } catch (e) {
    throw new Error(`cannot read ${STATE_REL}, the rig's recorded state run mode reads: ${(e as Error).message}`);
  }
  return (JSON.parse(text) as { states: Record<string, SequenceState> }).states;
}

// MUTANT "grid unchecked" (runPanelsOf loses its `grid.rows !== rows ||
// grid.cols !== cols` test, so the live group's labels are drawn on any
// grid). Observed (scratch copy S5-RUNMODE-mut, 2026-09-28; the reason's em
// dash is <U+2014>):
//   x runPanelsOf reads the live group's panels on the grid it runs, and nothing on another: a 3x3
//     draft over a 2x2 run: expected {}, got {"1-1":{"kind":"shooting"},"2-2":{"kind":"set_aside",
//     "reason":"centring failed on 2-2 on 3 consecutive visits: plate solve failed <U+2014> used
//     raw GoTo"}}
test("runPanelsOf reads the live group's panels on the grid it runs, and nothing on another", () => {
  const st = recordedStates();
  const shooting = st.shooting.group!;
  const reason = shooting.set_aside[0].reason;
  const g2 = { rows: 2, cols: 2 };
  // Each call passes the sequence state the group came from, as the sheet
  // does (#451): the recorded shooting state is "running".
  const run = st.shooting;
  eq(runPanelsOf(shooting, 2, 2, g2, run),
    { "1-1": { kind: "shooting" }, "2-2": { kind: "set_aside", reason } }, "the recorded run");
  // The recorded panels, 1-1 and 2-2, sit on the diagonal, where a label read
  // column first names the same panel. The same group moved on to 1-3 of a
  // 2x3 (two rows, three columns) does not: there is no row 3. MUTANT "row and
  // column swapped in runPanelsOf" (each label built as `${c}-${r}`), green
  // here before this line; observed (scratch copy S5-RUNMODE-verify-mut,
  // 2026-09-28; the reason's em dash is <U+2014>):
  //   x runPanelsOf reads the live group's panels on the grid it runs, and nothing on another: a 2x3
  //     run on 1-3, off the diagonal: expected {"1-3":{"kind":"shooting"},"2-2":{"kind":"set_aside",
  //     "reason":"centring failed on 2-2 on 3 consecutive visits: plate solve failed <U+2014> used raw
  //     GoTo"}}, got {"2-2":{"kind":"set_aside","reason":"centring failed on 2-2 on 3 consecutive
  //     visits: plate solve failed <U+2014> used raw GoTo"}}
  eq(runPanelsOf({ ...shooting, panel: "1-3" }, 2, 3, { rows: 2, cols: 3 }, run),
    { "1-3": { kind: "shooting" }, "2-2": { kind: "set_aside", reason } }, "a 2x3 run on 1-3, off the diagonal");
  // A meridian wait shoots nothing, for the operator (served the panel) and
  // the viewer (not served it); the set-aside panel stays for both.
  for (const who of ["meridian_wait_operator", "meridian_wait_viewer"]) {
    eq(runPanelsOf(st[who].group, 2, 2, g2, st[who]), { "2-2": { kind: "set_aside", reason } }, who);
  }
  // No group, or a progress block for another grid than the draft's: the
  // labels name another piece of sky, and nothing is drawn.
  eq(runPanelsOf(null, 2, 2, g2, run), {}, "no group");
  eq(runPanelsOf(shooting, 3, 3, g2, run), {}, "a 3x3 draft over a 2x2 run");
  eq(runPanelsOf(shooting, 2, 2, { rows: 2, cols: 3 }, run), {}, "a progress block for a 2x3");
  eq(runPanelsOf(shooting, 2, 2, null, run), {}, "a progress block with no grid (a single target's)");
});

// THE RUN'S STATE REACHES THE PANELS (#451, the S7 integration). The recorded
// held night (test_s5_recorded_state.py): the group on 1-1 with 2-2 set aside,
// the run paused, holding for cloud, and stopping from an Abort pressed in that
// hold (which still carries `hold: "clouds"`, #513). `runPanelsOf` asked with
// two arguments until S7, and `panelStateOf` answered `shooting` for any
// caller that did, so the Target modal said "1-1: shooting now" all through.
//
// MUTANT "state not passed" (runPanelsOf calling `panelStateOf(label, group,
// { state: "running" })`, the old two-argument answer, whatever `run` says).
// Observed (the integration's private copy, scratchpad S7-INTEG-r2-mut, 50/51;
// the reason's em dash is <U+2014>):
//   x runPanelsOf: the panel a paused, held or stopping run is on is current, never shot: the
//     recorded paused state: expected {"1-1":{"kind":"current","run":"paused","hold":null},"2-2":
//     {"kind":"set_aside","reason":"centring failed on 2-2 on 3 consecutive visits: plate solve
//     failed <U+2014> used raw GoTo"}}, got {"1-1":{"kind":"shooting"},"2-2":{"kind":"set_aside",
//     "reason":"centring failed on 2-2 on 3 consecutive visits: plate solve failed <U+2014> used
//     raw GoTo"}}
// framingSections.test.tsx (22/23) and runMode.test.tsx (8/9) go red under it
// too, on PANELS' "1-1: shooting now" while the run is paused.
test("runPanelsOf: the panel a paused, held or stopping run is on is current, never shot", () => {
  const st = recordedStates();
  const g2 = { rows: 2, cols: 2 };
  const reason = st.holding.group!.set_aside[0].reason;
  const want: [string, unknown][] = [
    ["paused", { kind: "current", run: "paused", hold: null }],
    ["holding", { kind: "current", run: "holding", hold: "clouds" }],
    ["aborting", { kind: "current", run: "aborting", hold: null }],
  ];
  for (const [who, current] of want) {
    eq(runPanelsOf(st[who].group, 2, 2, g2, st[who]),
      { "1-1": current, "2-2": { kind: "set_aside", reason } }, `the recorded ${who} state`);
  }
  // A run nobody knows shoots nothing; the set-aside panel still holds.
  eq(runPanelsOf(st.holding.group, 2, 2, g2, null), { "2-2": { kind: "set_aside", reason } },
    "the held group with no run state");
});

// MUTANT "done ahead of shooting" (panelDrawState answers DONE before it
// asks the run, so a panel whose count says complete is never drawn as the
// panel being shot). Observed (S5-RUNMODE-mut; runMode.test.tsx is red under
// it too, 2/7, on the recorded 1-1, complete on the progress answer and the
// panel the group is on):
//   x panelDrawState: skipped, then set aside, then shooting, then done, then pending: the panel
//     being shot whose count says complete: expected "shooting", got "done"
test("panelDrawState: skipped, then set aside, then shooting, then done, then pending", () => {
  const aside = { kind: "set_aside" as const, reason: "centring failed" };
  const shot = { kind: "shooting" as const };
  const open = { skipped: false, banked: 10, total: 80 };
  const full = { skipped: false, banked: 80, total: 80 };
  const off = { skipped: true, banked: 80, total: 0 };
  eq(panelDrawState(off, shot), "skipped", "a skipped panel, whatever the run says");
  eq(panelDrawState(off, aside), "skipped", "a skipped panel set aside");
  eq(panelDrawState(full, aside), "set_aside", "a set-aside panel whose count says complete");
  eq(panelDrawState(open, aside), "set_aside", "a set-aside panel");
  // The count lags the run by up to a re-read and is never ahead of it, so
  // a panel both complete and current is still the one the visit is on.
  eq(panelDrawState(full, shot), "shooting", "the panel being shot whose count says complete");
  eq(panelDrawState(open, shot), "shooting", "the panel being shot");
  // The current panel of a held run is drawn as the one the visit is on, with
  // the corner ticks (#451); only the words say nothing is exposing it.
  // MUTANT "ticks lost" (panelDrawState drawing only `shooting` as shooting).
  // Observed (scratchpad S7-INTEG-r2-mut, 50/51; runMode.test.tsx 8/9 too):
  //   x panelDrawState: skipped, then set aside, then shooting, then done, then pending: the held
  //     run's panel whose count says complete: expected "shooting", got "done"
  const held = { kind: "current" as const, run: "holding" as const, hold: "clouds" };
  eq(panelDrawState(full, held), "shooting", "the held run's panel whose count says complete");
  eq(panelDrawState(open, held), "shooting", "the held run's panel");
  eq(panelDrawState(full, null), "done", "a complete panel");
  eq(panelDrawState(open, null), "pending", "a panel still owed subs");
  eq(panelDrawState({ skipped: false, banked: 0, total: 0 }, undefined), "pending",
    "a panel the route has no count for");
});

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`framingModel.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) proc.exitCode = 1;
export const result = { passed, failed, total };
export default result;
