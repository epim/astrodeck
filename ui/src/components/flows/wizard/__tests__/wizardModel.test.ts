// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// wizardModel.test.ts - Send to Flow Wizard's pure rules (#196; spec
// 2026-09-23 flows mosaic, Revision 2 ruling 4, D13, section 8 S6).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/wizard/__tests__/wizardModel.test.ts   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT IS WORTH GUARDING HERE
//
//   1. THE ONE BODY. `wizardBody` builds what `POST /api/flows/wizard`
//      receives from the prefill and the answers, graded byte for byte
//      against the route's recorded request
//      (server/tests/fixtures/wizard_mosaic_answer.json, recorded by
//      test_flows_wizard_door_answers.py and never edited by hand). A
//      prefilled value reaches the body from the PREFILL, so an answer can
//      never overwrite it; a 1 x 1 framing, or a mosaic planned as one target,
//      sends no grid key at all, which the generator would refuse with the
//      Deep-sky kind.
//   2. WHAT EACH STEP STILL ASKS (`stepReason`): only what did not arrive.
//   3. THE REVIEW PRINTS THE SERVER'S NUMBERS, never a product of the
//      answers: a compile whose numbers no product of the answers can make is
//      printed as it came.
//   4. THE RUN LOCK: a danger or a loss locks RUN with its reason; a warning
//      and a note do not.
//   5. THE SERVER'S WORDS: every constant mirrored from wizard.py is read out
//      of wizard.py and compared.
//   6. THE ROUTE PARAMS round-trip the prefill, and a garbled link reads as
//      "did not arrive".
//   7. NIGHT AND RESUME (backlog WP-100, #196): the six answers open on the
//      DUSK WINDOW node's defaults, `wizardBody` leaves a key out when it
//      equals that default (so the recorded request stays byte for byte),
//      a Clock time with no HH:MM and an altitude outside 0 to 90 stop NIGHT in
//      words, the constants mirrored from wizard.py equal wizard.py's, and the
//      server's brief is read as its sentence and nothing else.
//
// Every mutant below was run in a private scratch copy of ui/
// (scratchpad/S6-WIZ-UI-mut), never in the shared tree (#254), and the
// failure it produced is quoted verbatim.

/* eslint-disable @typescript-eslint/no-explicit-any */

// @ts-ignore  node built-ins; tsx supplies them at runtime
import { readFileSync } from "node:fs";

// wizardModel imports framingApi (the one readouts reader), which imports
// lib/api -> lib/base, and base reads `window.location` AT MODULE SCOPE.
(globalThis as any).window = { location: { pathname: "/", origin: "http://local" } };

const M = await import("../wizardModel");
const { FALLBACK_WHEEL, toggleSlot } = await import("../../cyclePlanRows");
const { NODE_DEFS, TARGET_ANGLES } = await import("../../nodeDefs");
type WizardPrefill = import("../wizardModel").WizardPrefill;
type WizardAnswers = import("../wizardModel").WizardAnswers;

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: unknown, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (JSON.stringify(got) !== JSON.stringify(want)) {
    throw new Error(`${msg}\n  expected ${JSON.stringify(want)}\n  got      ${JSON.stringify(got)}`);
  }
}
/** Deep equality with the keys sorted, so a difference is a difference of
 *  value and not of the order a literal was written in. */
function canon(v: any): any {
  if (Array.isArray(v)) return v.map(canon);
  if (v && typeof v === "object") {
    return Object.fromEntries(Object.keys(v).sort().map((k) => [k, canon(v[k])]));
  }
  return v;
}

function readText(rel: string): string {
  try {
    return readFileSync(new URL(rel, import.meta.url), "utf8") as string;
  } catch (e) {
    throw new Error(`cannot read ${rel}: ${(e as Error).message}`);
  }
}
const FX = JSON.parse(readText("../../../../../../server/tests/fixtures/wizard_mosaic_answer.json"));
const WIZARD_PY = readText("../../../../../../server/astrodeck/flows/wizard.py");

// ------------------------------------------------------------------ fixtures

const WHEEL = FALLBACK_WHEEL.map((s) => s.filter);

/** The door's prefill for the recorded request: a 3 x 2 of M31 at PA 30,
 *  rotating, with 2-3 skipped, framed with the fixture's IMX571 at 1000 mm.
 *  NO OVERLAP: the recorded request carries no `overlap_pct` (it took the
 *  generator's default), so the prefill that makes it has none either; the
 *  control below shows a door's overlap reaching the body. */
function fxPrefill(over: Partial<WizardPrefill> = {}): WizardPrefill {
  return {
    name: FX.request.target, ra: FX.request.ra, dec: FX.request.dec,
    angleMode: "Rotate to PA", paDeg: 30, rows: 2, cols: 3, overlapPct: null,
    skip: "2-3", fov: { xDeg: 1.346, yDeg: 0.9 },
    ...over,
  };
}

/** The operator's answers for the recorded request: L, R, G and B ticked on
 *  the assumed wheel (the fixture's rig has no wheel connected), each at its
 *  60 s default, 10 subs of each, guided. Ticked through cyclePlanRows'
 *  `toggleSlot`, the writer the FILTERS step uses. */
function fxAnswers(p: WizardPrefill, over: Partial<WizardAnswers> = {}): WizardAnswers {
  let plan = "";
  for (const f of ["L", "R", "G", "B"]) plan = toggleSlot(WHEEL, plan, f);
  return { ...M.initialAnswers(p), plan, cycles: "10", guiding: true, ...over };
}

const FIELD = { field: { xDeg: 1.346, yDeg: 0.9 } };
const NO_OPTICS = { field: null };

// ============================================================ 1. the body
//
// MUTANT "prefilled skip not sent" (wizardBody: the line
// `if (!blank(p.skip)) body.skip = p.skip;` deleted).
// Observed ("wizardModel.test: 24/26 passed"):
//   x the body for the recorded prefill and answers is the recorded request, byte for byte: wizardBody is not the recorded request:
//     expected {"angle_mode":"Rotate to PA","cols":3,"cycle_plan":"L 60, R 60, G 60, B 60","cycles":10,"dec":"+41° 30' 00\"","guiding":true,"kind":"Mosaic","options":["HFR watchdog"],"pa_deg":30,"ra":"00h 43m 15.0s","rows":2,"skip":"2-3","target":"M31"}
//     got      {"angle_mode":"Rotate to PA","cols":3,"cycle_plan":"L 60, R 60, G 60, B 60","cycles":10,"dec":"+41° 30' 00\"","guiding":true,"kind":"Mosaic","options":["HFR watchdog"],"pa_deg":30,"ra":"00h 43m 15.0s","rows":2,"target":"M31"}
//   x a door's overlap reaches the body as overlap_pct, and nothing else moves: the overlap control moved another key:
//     expected {"angle_mode":"Rotate to PA","cols":3,"cycle_plan":"L 60, R 60, G 60, B 60","cycles":10,"dec":"+41° 30' 00\"","guiding":true,"kind":"Mosaic","options":["HFR watchdog"],"pa_deg":30,"ra":"00h 43m 15.0s","rows":2,"skip":"2-3","target":"M31"}
//     got      {"angle_mode":"Rotate to PA","cols":3,"cycle_plan":"L 60, R 60, G 60, B 60","cycles":10,"dec":"+41° 30' 00\"","guiding":true,"kind":"Mosaic","options":["HFR watchdog"],"pa_deg":30,"ra":"00h 43m 15.0s","rows":2,"target":"M31"}
// (The DOM half, sendToWizardSheet.test.tsx, is red under the same mutant
// on the request the sheet actually posts.)

test("the body for the recorded prefill and answers is the recorded request, byte for byte", () => {
  const p = fxPrefill();
  const body = M.wizardBody(p, fxAnswers(p));
  eq(canon(body), canon(FX.request), "wizardBody is not the recorded request:");
  // Byte for byte: the same keys in the same order, so the JSON the sheet
  // posts is the JSON the route recorded.
  eq(JSON.stringify(body), JSON.stringify(FX.request), "the body's keys are not in the recorded order:");
});

test("a door's overlap reaches the body as overlap_pct, and nothing else moves", () => {
  const p = fxPrefill({ overlapPct: 25 });
  const body: any = M.wizardBody(p, fxAnswers(p));
  eq(body.overlap_pct, 25, "the door's overlap was not sent");
  const rest = { ...body };
  delete rest.overlap_pct;
  eq(canon(rest), canon(FX.request), "the overlap control moved another key:");
});

test("no skip sends no skip key, never an empty one", () => {
  const p = fxPrefill({ skip: "  " });
  const body: any = M.wizardBody(p, fxAnswers(p));
  assert(!("skip" in body), `a blank skip was sent: ${JSON.stringify(body.skip)}`);
});

test("a 1 x 1 framing is one target: the Deep-sky kind, its centre, and no grid key", () => {
  const p = fxPrefill({ rows: 1, cols: 1, skip: "", overlapPct: 25 });
  const body: any = M.wizardBody(p, fxAnswers(p));
  eq(body.kind, M.KIND_DEEP_SKY, "a single target was sent as a mosaic");
  for (const k of ["rows", "cols", "overlap_pct", "angle_mode", "pa_deg", "skip", "use_measured"]) {
    assert(!(k in body), `a single target's body carries ${k}, which the generator refuses with this kind`);
  }
  eq([body.ra, body.dec], [FX.request.ra, FX.request.dec], "the framing's centre did not travel");
});

// MUTANT "single target sends the grid" (wizardBody: `mosaic = isMosaicFraming(p)`,
// which ignores PLAN AS ONE TARGET).
// Observed ("wizardModel.test: 25/26 passed"):
//   x a mosaic planned as one target sends the same single-target body: PLAN AS ONE TARGET still sent a mosaic
//     expected "Deep-sky target"
//     got      "Mosaic"
// (Red in the DOM test too, on the request the sheet posts.)

test("a mosaic planned as one target sends the same single-target body", () => {
  const p = fxPrefill();
  const body: any = M.wizardBody(p, fxAnswers(p, { single: true }));
  eq(body.kind, M.KIND_DEEP_SKY, "PLAN AS ONE TARGET still sent a mosaic");
  assert(!("rows" in body) && !("skip" in body) && !("angle_mode" in body),
    `a mosaic planned as one target still carries grid keys: ${JSON.stringify(body)}`);
});

test("an angle that arrived is the one sent, whatever the answers hold", () => {
  const p = fxPrefill();
  const body: any = M.wizardBody(p, fxAnswers(p, { angleMode: "Camera fixed at PA", pa: "77" }));
  eq([body.angle_mode, body.pa_deg], ["Rotate to PA", 30], "an answer overwrote the door's angle");
});

test("an angle that did not arrive is the one asked", () => {
  const p = fxPrefill({ angleMode: null, paDeg: null });
  const body: any = M.wizardBody(p, fxAnswers(p, { angleMode: "Camera fixed at PA", pa: "12.5" }));
  eq([body.angle_mode, body.pa_deg], ["Camera fixed at PA", 12.5], "the asked angle was not sent");
});

// MUTANT "an answer overwrites an arrived field" (targetOf: the name read
// `blank(a.name) ? p.name : a.name`, the answer first).
// Observed ("wizardModel.test: 25/26 passed"):
//   x a name or coordinates typed where none arrived are sent, trimmed; arrived ones are not replaced: a typed answer replaced a prefilled one
//     expected ["M31","00h 43m 15.0s"]
//     got      ["M33","00h 43m 15.0s"]

test("a name or coordinates typed where none arrived are sent, trimmed; arrived ones are not replaced", () => {
  const p = fxPrefill({ name: "", ra: "", dec: "" });
  const body: any = M.wizardBody(p, fxAnswers(p, { name: " NGC 7000 ", ra: " 20h 59m 17s", dec: "+44 31 44 " }));
  eq([body.target, body.ra, body.dec], ["NGC 7000", "20h 59m 17s", "+44 31 44"], "typed target not sent");
  const q = fxPrefill();
  const kept: any = M.wizardBody(q, fxAnswers(q, { name: "M33", ra: "01h 33m", dec: "+30" }));
  eq([kept.target, kept.ra], [FX.request.target, FX.request.ra], "a typed answer replaced a prefilled one");
});

test("no ticked filter sends neither cycle_plan nor cycles; guiding false is sent as false", () => {
  const p = fxPrefill();
  const body: any = M.wizardBody(p, fxAnswers(p, { plan: "", guiding: false }));
  assert(!("cycle_plan" in body) && !("cycles" in body), `rows sent with none ticked: ${JSON.stringify(body)}`);
  eq(body.guiding, false, "guiding off was not sent");
  eq(body.options, ["HFR watchdog"], "the door's chips are not the watchdog alone");
});

// ============================================== 2. what each step still asks

test("TARGET asks nothing when all three arrived, and asks for what did not", () => {
  const p = fxPrefill();
  eq(M.stepReason("target", p, fxAnswers(p), FIELD), null, "an arrived target was asked again");
  const noName = fxPrefill({ name: "" });
  eq(M.stepReason("target", noName, fxAnswers(noName), FIELD), M.NEED_NAME, "a missing name was not asked");
  eq(M.stepReason("target", noName, fxAnswers(noName, { name: "M31" }), FIELD), null, "a typed name was not taken");
  const noDec = fxPrefill({ dec: "" });
  eq(M.stepReason("target", noDec, fxAnswers(noDec), FIELD), M.NEED_COORDS, "a missing Dec was not asked");
});

test("FRAMING asks a mosaic for an angle only when none arrived", () => {
  const p = fxPrefill();
  eq(M.stepReason("framing", p, fxAnswers(p), FIELD), null, "an arrived angle was asked again");
  for (const over of [
    { angleMode: null, paDeg: null },
    { angleMode: "Any angle" as const, paDeg: 30 },
    { angleMode: "Rotate to PA" as const, paDeg: null },
  ]) {
    const q = fxPrefill(over);
    eq(M.stepReason("framing", q, fxAnswers(q), FIELD), M.NEED_ANGLE, `no angle asked for ${JSON.stringify(over)}`);
  }
  const q = fxPrefill({ angleMode: null, paDeg: null });
  eq(M.stepReason("framing", q, fxAnswers(q, { angleMode: "Rotate to PA", pa: "abc" }), FIELD), M.NEED_ANGLE,
    "a PA that is not a number was taken");
  eq(M.stepReason("framing", q, fxAnswers(q, { angleMode: "Rotate to PA", pa: "0" }), FIELD), null,
    "a PA of 0 (a real angle) was refused");
});

// MUTANT "a single target is asked an angle" (stepReason's FRAMING case:
// `if (!plansMosaic(p, a)) return null;` became `if (a.single) return null;`).
// Observed ("wizardModel.test: 25/26 passed"):
//   x FRAMING never asks a single target for an angle: a 1 x 1 framing was asked for an angle or optics
//     expected null
//     got      "This rig has no camera field, so the grid cannot be laid out: set the camera and focal length in Settings > Optics to plan a mosaic, or plan it as one target."
// (The DOM test stays green under it: its single target arrives with an
// angle on a rig with optics, which this rule never reaches.)

test("FRAMING never asks a single target for an angle", () => {
  const p = fxPrefill({ rows: 1, cols: 1, angleMode: null, paDeg: null });
  eq(M.stepReason("framing", p, fxAnswers(p), NO_OPTICS), null, "a 1 x 1 framing was asked for an angle or optics");
});

// MUTANT "angle asked before optics" (stepReason's FRAMING case: the angle checked
// before the camera field).
// Observed ("wizardModel.test: 25/26 passed"):
//   x FRAMING with no camera field offers one target before it asks any angle: no optics was not the first thing said
//     expected "This rig has no camera field, so the grid cannot be laid out: set the camera and focal length in Settings > Optics to plan a mosaic, or plan it as one target."
//     got      "A mosaic is laid out at one camera angle: choose ROTATE TO or CAMERA FIXED AT, and type the PA in degrees."
// (The DOM test stays green under it: its no-optics prefill arrives with
// an angle, so the swapped order reaches the same sentence.)

test("FRAMING with no camera field offers one target before it asks any angle", () => {
  const p = fxPrefill({ angleMode: null, paDeg: null });
  eq(M.stepReason("framing", p, fxAnswers(p), NO_OPTICS), M.NEED_OPTICS,
    "no optics was not the first thing said");
  eq(M.stepReason("framing", p, fxAnswers(p, { single: true }), NO_OPTICS), null,
    "taking the one-target offer did not clear the step");
});

// MUTANT "unguided cap not asked" (stepReason's GUIDING case: `if (a.guiding)`
// became `if (a.guiding || true)`).
// Observed ("wizardModel.test: 25/26 passed"):
//   x FILTERS asks for a row and a count; GUIDING refuses unguided rows past the cap: unguided 60 s rows were not refused by name
//     expected "Unguided, a sub is held to 30 s, and L 60 s, R 60 s, G 60 s, B 60 s run longer: turn guiding on, or shorten them on FILTERS."
//     got      null

test("FILTERS asks for a row and a count; GUIDING refuses unguided rows past the cap", () => {
  const p = fxPrefill();
  eq(M.stepReason("filters", p, fxAnswers(p, { plan: "" }), FIELD), M.NEED_FILTER, "no filter was not asked");
  for (const bad of ["0", "abc", "10001", "2.5", ""]) {
    eq(M.stepReason("filters", p, fxAnswers(p, { cycles: bad }), FIELD), M.NEED_CYCLES, `cycles ${JSON.stringify(bad)} taken`);
  }
  eq(M.stepReason("filters", p, fxAnswers(p), FIELD), null, "a good filter step was refused");
  eq(M.stepReason("guiding", p, fxAnswers(p), FIELD), null, "guided was refused");
  eq(M.stepReason("guiding", p, fxAnswers(p, { guiding: false }), FIELD),
    M.capReason(["L 60 s", "R 60 s", "G 60 s", "B 60 s"]), "unguided 60 s rows were not refused by name");
  eq(M.stepReason("guiding", p, fxAnswers(p, { guiding: false, plan: "L 30, Ha 20" }), FIELD), null,
    "rows inside the cap were refused");
});

// MUTANT "a cleared exposure box goes unnoticed" (stepReason's FILTERS case: the
// `badDraft` check deleted).
// Observed ("wizardModel.test: 25/26 passed"):
//   x a ticked filter whose box holds what the plan did not take stops FILTERS, naming it: a cleared box went unnoticed
//     expected "Type L's exposure in whole seconds, at least 1."
//     got      null

test("a ticked filter whose box holds what the plan did not take stops FILTERS, naming it", () => {
  const p = fxPrefill();
  // The box cleared on the way from 60 to 180: the plan still says L 60.
  const cleared = fxAnswers(p, { drafts: { L: "" } });
  eq(M.planRows(cleared.plan)[0], ["L", 60], "precondition: the plan did not keep L 60");
  eq(M.stepReason("filters", p, cleared, FIELD), M.exposureReason("L"), "a cleared box went unnoticed");
  eq(M.stepReason("filters", p, fxAnswers(p, { drafts: { R: "0" } }), FIELD), M.exposureReason("R"),
    "a zero-second box went unnoticed");
  // Controls: a box the plan took, and a box of a filter no longer ticked.
  eq(M.stepReason("filters", p, fxAnswers(p, { drafts: { L: "60" } }), FIELD), null, "a taken box was refused");
  eq(M.stepReason("filters", p, fxAnswers(p, { drafts: { Ha: "" } }), FIELD), null,
    "an unticked filter's box was held against FILTERS");
});

// MUTANT "field change never said" (fieldChanged: returns null whatever the two
// fields are), run by the S6-WIZ-UI verifier in its own private scratch copy
// (scratchpad/S6-WIZ-UI-verify-mut). Nothing graded the positive case before.
// Observed ("wizardModel.test: 26/27 passed"):
//   x a framing made at another camera's field says so; one camera, or a field not known, says nothing: a field twice the framed one was not said: null

test("a framing made at another camera's field says so; one camera, or a field not known, says nothing", () => {
  const p = fxPrefill();
  const twice = M.fieldChanged(p, { field: { xDeg: 2.692, yDeg: 1.8 } });
  assert(twice !== null && twice.includes("1.35 x 0.90 deg") && twice.includes("2.69 x 1.80 deg"),
    `a field twice the framed one was not said: ${twice}`);
  // Controls: the same camera, a rounding inside FIELD_TOLERANCE, and either
  // field unknown.
  eq(M.fieldChanged(p, FIELD), null, "one camera read as two");
  eq(M.fieldChanged(p, { field: { xDeg: 1.346 * 1.005, yDeg: 0.9 } }), null, "a rounding inside 1% read as another camera");
  eq(M.fieldChanged(p, NO_OPTICS), null, "a rig with no field was compared");
  eq(M.fieldChanged(fxPrefill({ fov: null }), FIELD), null, "a framing with no field was compared");
});

test("firstGap names the first step with something missing, and none on a complete answer", () => {
  const p = fxPrefill({ name: "" });
  eq(M.firstGap(p, fxAnswers(p, { plan: "" }), FIELD), { step: "target", reason: M.NEED_NAME }, "wrong first gap");
  const q = fxPrefill();
  eq(M.firstGap(q, fxAnswers(q), FIELD), null, "a complete answer has a gap");
});

// ============================================================ 3. the review
//
// MUTANT "review computes subs locally" (reviewLines: `r.subs_per_panel`
// replaced by `r.steps * (r.rounds ?? 1)` and `r.subs_total` by
// `r.panels * r.steps * (r.rounds ?? 1)`, the product of the answers).
// Observed ("wizardModel.test: 25/26 passed"):
//   x the review prints the compile's numbers as they came, even where no product of the answers makes them: a doctored compile was not printed as it came:
//     expected ["5 panels: 37 subs per panel, 185 in all","0.5 h per panel, 2.5 h in all"]
//     got      ["5 panels: 40 subs per panel, 200 in all","0.5 h per panel, 2.5 h in all"]
// (The recorded compile's own case stays green under it, because there the
// product and the server agree; that is exactly why the doctored case exists.)

const flowRead = M.savedFlowOf(FX.answer);

test("the recorded answer is a saved flow with its TARGET block and its notes", () => {
  assert(flowRead.ok, `the recorded answer did not read: ${(flowRead as any).why}`);
  const f = (flowRead as any).flow;
  eq([f.id, f.name, f.targets, f.notes], [FX.answer.id, "M31", [{ id: "n2", name: "M31" }], []], "wrong read");
  const bad = M.savedFlowOf({ name: "x" });
  assert(!bad.ok, "an answer with no id read as a flow");
});

test("the review prints the recorded compile's subs and hours, per panel and in all", () => {
  const blocks = M.reviewBlocks((flowRead as any).flow, FX.compile);
  eq(blocks, [{ id: "n2", name: "M31", ok: true, lines: [
    "5 panels: 40 subs per panel, 200 in all",
    "0.67 h per panel, 3.33 h in all",
  ] }], "the review of the recorded compile:");
});

test("the review prints the compile's numbers as they came, even where no product of the answers makes them", () => {
  const doctored = JSON.parse(JSON.stringify(FX.compile));
  Object.assign(doctored.readouts.n2, { subs_per_panel: 37, subs_total: 185, panel_s: 1800, total_s: 9000 });
  const blocks: any = M.reviewBlocks((flowRead as any).flow, doctored);
  eq(blocks[0].lines, ["5 panels: 37 subs per panel, 185 in all", "0.5 h per panel, 2.5 h in all"],
    "a doctored compile was not printed as it came:");
});

test("a single target's block says its subs and hours once; a block with no readout says why", () => {
  const one = JSON.parse(JSON.stringify(FX.compile));
  Object.assign(one.readouts.n2, { mode: "single", panels: 1, subs_per_panel: 40, subs_total: 40,
    panel_s: 2400, total_s: 2400, passes: null, visit_passes: null, visits_per_panel: null,
    visits_total: null, hop_s: null, hop_measured: null, preflip_idle_s: null, angle_tolerance_deg: null });
  const blocks: any = M.reviewBlocks((flowRead as any).flow, one);
  eq(blocks[0].lines, ["40 subs on one panel", "0.67 h in all"], "a single target's lines");
  const none: any = M.reviewBlocks((flowRead as any).flow, { ...FX.compile, readouts: {} });
  assert(none[0].ok === false && /no numbers/.test(none[0].why), `no readout was not said: ${JSON.stringify(none)}`);
});

// ============================================================ 4. the RUN lock
//
// MUTANT "RUN open on a loss" (runLock: the `const loss = f.losses[0]; if
// (loss) return ...` branch deleted).
// Observed ("wizardModel.test: 25/26 passed"):
//   x a loss locks RUN with its reason; a note-level entry does not: a loss did not lock RUN:
//     expected "Part of this flow will not run as drawn: GUIDE: settle 1.5 s is not carried. OPEN IN EDITOR to fix it or accept it there."
//     got      null

test("the recorded compile opens RUN; a missing or failed compile keeps it locked with a reason", () => {
  eq(M.runLock(FX.compile, null), null, "the recorded compile (no issue, no loss) locked RUN");
  eq(M.runLock(null, null), M.RUN_WAITING, "RUN opened before the compile answered");
  const failedLock = M.runLock(null, "503 Service Unavailable");
  assert(failedLock !== null && failedLock.includes("503 Service Unavailable"), `a failed compile: ${failedLock}`);
});

test("a danger locks RUN with its reason; a warning does not", () => {
  const danger = { ...FX.compile, issues: [{ text: "Dome without a safety monitor", level: "danger" }] };
  eq(M.runLock(danger, null),
    "The doctor reports a danger: Dome without a safety monitor. OPEN IN EDITOR to fix it.", "danger lock");
  const warn = { ...FX.compile, issues: [{ text: "Unguided 120 s subs will trail", level: "warn" }] };
  eq(M.runLock(warn, null), null, "a warning locked RUN");
});

test("a loss locks RUN with its reason; a note-level entry does not", () => {
  const loss = { ...FX.compile, unmapped: [{ key: "g", detail: "GUIDE: settle 1.5 s is not carried", level: "warn" }] };
  eq(M.runLock(loss, null),
    "Part of this flow will not run as drawn: GUIDE: settle 1.5 s is not carried. OPEN IN EDITOR to fix it or accept it there.",
    "a loss did not lock RUN:");
  const note = { ...FX.compile, unmapped: [{ key: "r", detail: "REPORT is written by the engine", level: "note" }] };
  eq(M.runLock(note, null), null, "a note locked RUN");
  const wire = { ...FX.compile, structural: ["edge we9 names a node that does not exist"] };
  assert((M.runLock(wire, null) ?? "").includes("edge we9"), "a broken wire did not lock RUN");
});

// ============================================================ 5. the server's words
//
// MUTANT "a mirrored constant drifts" (wizardModel: UNGUIDED_CAP_S = 45).
// Observed ("wizardModel.test: 25/26 passed"):
//   x every constant mirrored from wizard.py equals wizard.py's: UNGUIDED_EXPOSURE_DEFAULT:
//     expected 30
//     got      45

/** A Python string constant, including the parenthesised run of adjacent
 *  literals wizard.py writes long sentences as. */
function pyString(name: string): string {
  const m = new RegExp(`^${name}(?::[^=]*)? = \\(?((?:\\s*"[^"]*"\\s*)+)\\)?$`, "m").exec(WIZARD_PY);
  assert(m, `wizard.py has no string constant ${name}`);
  return Array.from(m![1].matchAll(/"([^"]*)"/g)).map((x) => x[1]).join("");
}
function pyNumber(name: string): number {
  const m = new RegExp(`^${name} = ([0-9_]+)$`, "m").exec(WIZARD_PY);
  assert(m, `wizard.py has no numeric constant ${name}`);
  return Number(m![1].replace(/_/g, ""));
}

test("every constant mirrored from wizard.py equals wizard.py's", () => {
  eq(M.KIND_MOSAIC, pyString("KIND_MOSAIC"), "KIND_MOSAIC:");
  eq(M.KIND_DEEP_SKY, pyString("KIND_DEEP_SKY"), "KIND_DEEP_SKY:");
  eq(M.OPT_WATCHDOG, pyString("OPT_WATCHDOG"), "OPT_WATCHDOG:");
  eq(M.NO_OPTICS_REASON, pyString("NO_OPTICS_REASON"), "NO_OPTICS_REASON:");
  eq(M.UNGUIDED_CAP_S, pyNumber("UNGUIDED_EXPOSURE_DEFAULT"), "UNGUIDED_EXPOSURE_DEFAULT:");
  eq(M.CYCLES_MAX, pyNumber("CYCLES_MAX"), "CYCLES_MAX:");
  // DEFAULT_OPTIONS less Guiding, which this wizard asks as its own step.
  const opts = /^DEFAULT_OPTIONS: frozenset\[str\] = frozenset\(\{([^}]*)\}\)$/m.exec(WIZARD_PY);
  assert(opts, "wizard.py has no DEFAULT_OPTIONS line in the form this test reads");
  const names = opts![1].split(",").map((s) => s.trim()).filter((s) => s !== "OPT_GUIDING");
  eq([...M.DOOR_OPTIONS].sort(), names.map(pyString).sort(), "DOOR_OPTIONS is not DEFAULT_OPTIONS less Guiding:");
  // The two mosaic angles, unpacked from TARGET_ANGLES there as here.
  assert(/^ANY_ANGLE, ROTATE_TO_PA, CAMERA_FIXED_AT_PA = TARGET_ANGLES$/m.test(WIZARD_PY),
    "wizard.py no longer unpacks TARGET_ANGLES as (any, rotate, fixed)");
  assert(/^MOSAIC_ANGLES: tuple\[str, \.\.\.\] = \(ROTATE_TO_PA, CAMERA_FIXED_AT_PA\)$/m.test(WIZARD_PY),
    "wizard.py's MOSAIC_ANGLES is no longer (ROTATE_TO_PA, CAMERA_FIXED_AT_PA)");
  eq([...M.MOSAIC_ANGLES], [TARGET_ANGLES[1], TARGET_ANGLES[2]], "MOSAIC_ANGLES:");
});

// ============================================================ 6. the route params

test("the prefill round-trips through the route params, and the door's own keys survive", () => {
  const p = fxPrefill({ overlapPct: 25 });
  const params = M.wizardParams(p);
  eq(M.prefillFromParams(params), p, "the prefill did not round-trip");
  eq(M.withoutWizardParams({ ...params, frame: "1", open: "f9" }), { frame: "1", open: "f9" },
    "the door's own params did not survive, or a wizard key did");
  eq(M.prefillFromParams({}), M.EMPTY_PREFILL, "no params is not the empty prefill");
});

// MUTANT "garbled grid read as a value" (sideParam: `n !== null ? n : 1`, with no
// whole-number or 1..GRID_MAX bound).
// Observed ("wizardModel.test: 25/26 passed"):
//   x a garbled link reads as did not arrive, never as a grid nobody drew: a garbled param was read as a value
//     expected [1,1,null,null,null,null]
//     got      [50,2.5,null,null,null,null]

test("a garbled link reads as did not arrive, never as a grid nobody drew", () => {
  const got = M.prefillFromParams({
    wz_rows: "50", wz_cols: "2.5", wz_pa: "north", wz_angle: "Sideways", wz_fovx: "-1", wz_fovy: "0.9",
    wz_overlap: "",
  });
  eq([got.rows, got.cols, got.paDeg, got.angleMode, got.fov, got.overlapPct], [1, 1, null, null, null, null],
    "a garbled param was read as a value");
});

// ================================================ 7. NIGHT and RESUME (WP-100)
//
// Backlog WP-100 (#196, wave 15) built the two steps the S6 sheet left "not
// built" (#191, #195): NIGHT (when the night starts and stops, the lowest
// altitude a target is shot at) and RESUME (the owner's automatic resume). The
// mutants named below were applied from a byte backup inside the worktree, the
// file restored byte-identically (sha256 compared) and the mutant text grepped
// gone; the failure each produced is quoted.

const NIGHT_KEYS = ["stop", "stop_clock", "start", "start_clock", "min_alt", "auto_resume"];

test("the steps are the S6 five with NIGHT and RESUME before the review", () => {
  eq([...M.STEPS], ["target", "framing", "filters", "guiding", "night", "resume", "review"], "STEPS:");
  eq([M.STEP_TITLE.night, M.STEP_TITLE.resume], ["NIGHT", "RESUME"], "the new steps' titles:");
});

test("the sheet opens on the DUSK WINDOW node's own defaults", () => {
  const a = M.initialAnswers(fxPrefill());
  eq([a.stop, a.stopClock, a.start, a.startClock, a.minAlt, a.autoResume],
    ["Dawn", "", "Astro dusk", "", "30", true], "the NIGHT and RESUME answers did not open on the node's defaults");
});

// MUTANT "always send auto_resume" (wizardBody: the `if (a.autoResume !==
// autoResumeOn(DUSK_DEFAULTS.autoResume))` guard made `if (true)`, so the
// answer On is sent as "On").
// Observed ("wizardModel.test: 36/40 passed"):
//   x the body for the recorded prefill and answers is the recorded request, byte for byte: wizardBody is not the recorded request:
//     expected {"angle_mode":"Rotate to PA","cols":3,"cycle_plan":"L 60, R 60, G 60, B 60","cycles":10,...,"target":"M31"}
//     got      {"angle_mode":"Rotate to PA","auto_resume":"On","cols":3,"cycle_plan":"L 60, R 60, G 60, B 60","cycles":10,...,"target":"M31"}
//   x a door's overlap reaches the body as overlap_pct, and nothing else moves: the overlap control moved another key:
//   x body leaves default answers out: a sheet nobody changed NIGHT or RESUME in posts none of the six keys: a default answer was sent as auto_resume: "On"
//   x each changed answer sends its own key, and no other key moves: a Clock time stop is sent with its clock, trimmed
//     expected {"stop":"Clock time","stop_clock":"22:30"}
//     got      {"stop":"Clock time","stop_clock":"22:30","auto_resume":"On"}
// (The DOM half, sendToWizardSheet.test.tsx, is red under the same mutant on
// the request the sheet posts, "31/34 passed": the recorded walk, "a sheet
// nobody changed NIGHT or RESUME in posts the recorded request", and "every
// NIGHT answer reaches the body".)
//
// MUTANT "the default is sent too" (wizardBody: the stop's guard `if (a.stop
// !== DUSK_DEFAULTS.stop) {` made `if (true) {`).
// Observed ("wizardModel.test: 36/40 passed", "sendToWizardSheet.test: 31/34
// passed"): the same cases, with "stop":"Dawn" in the body, for example
//   x body leaves default answers out: a sheet nobody changed NIGHT or RESUME in posts none of the six keys: a default answer was sent as stop: "Dawn"

test("body leaves default answers out: a sheet nobody changed NIGHT or RESUME in posts none of the six keys", () => {
  const p = fxPrefill();
  const body: any = M.wizardBody(p, fxAnswers(p));
  for (const k of NIGHT_KEYS) assert(!(k in body), `a default answer was sent as ${k}: ${JSON.stringify(body[k])}`);
  eq(JSON.stringify(body), JSON.stringify(FX.request), "the default NIGHT and RESUME answers moved the recorded request:");
  // An answer typed back to the default is still the default: a minimum
  // altitude of "30.0" is the node's 30, and Dawn after a clock is Dawn.
  const same: any = M.wizardBody(p, fxAnswers(p, { minAlt: "30.0", stop: "Dawn", stopClock: "03:30", start: "Astro dusk", startClock: "21:00" }));
  eq(JSON.stringify(same), JSON.stringify(FX.request), "an answer equal to the default was sent:");
});

test("each changed answer sends its own key, and no other key moves", () => {
  const p = fxPrefill();
  const sent = (over: Partial<WizardAnswers>) => {
    const body: any = M.wizardBody(p, fxAnswers(p, over));
    const extra: Record<string, unknown> = {};
    for (const k of NIGHT_KEYS) if (k in body) extra[k] = body[k];
    const rest = { ...body };
    for (const k of NIGHT_KEYS) delete rest[k];
    eq(JSON.stringify(rest), JSON.stringify(FX.request), `${JSON.stringify(over)} moved a key that is not NIGHT's or RESUME's:`);
    return extra;
  };
  eq(sent({ stop: "Clock time", stopClock: " 22:30 " }), { stop: "Clock time", stop_clock: "22:30" },
    "a Clock time stop is sent with its clock, trimmed");
  eq(sent({ start: "Clock time", startClock: "21:15" }), { start: "Clock time", start_clock: "21:15" },
    "a Clock time start is sent with its clock");
  eq(sent({ start: "Civil dusk" }), { start: "Civil dusk" }, "a sun-based start is sent alone");
  eq(sent({ minAlt: "45" }), { min_alt: 45 }, "an altitude is sent as a number");
  eq(sent({ minAlt: "45.5" }), { min_alt: 45.5 }, "a fractional altitude is sent as a number");
  eq(sent({ minAlt: "0" }), { min_alt: 0 }, "a floor of 0 is an answer, not a blank");
  eq(sent({ autoResume: false }), { auto_resume: "Off" }, "Off is sent as the server's own word");
  eq(sent({
    stop: "Clock time", stopClock: "03:30", start: "Clock time", startClock: "21:15", minAlt: "42.5", autoResume: false,
  }), { stop: "Clock time", stop_clock: "03:30", start: "Clock time", start_clock: "21:15", min_alt: 42.5, auto_resume: "Off" },
  "all six");
  // The keys are in the server model's order, after `guiding`.
  const all: any = M.wizardBody(p, fxAnswers(p, {
    stop: "Clock time", stopClock: "03:30", start: "Clock time", startClock: "21:15", minAlt: "42.5", autoResume: false,
  }));
  eq(Object.keys(all).slice(-7), ["guiding", ...NIGHT_KEYS], "the keys are not in FlowWizardBody's order");
});

test("a clock goes with its Clock time and with nothing else", () => {
  const p = fxPrefill();
  // A time typed and then abandoned for Dawn is not sent: the server refuses a
  // clock beside any other choice.
  const dawn: any = M.wizardBody(p, fxAnswers(p, { stop: "Dawn", stopClock: "03:30", start: "Nautical dusk", startClock: "21:00" }));
  assert(!("stop_clock" in dawn) && !("start_clock" in dawn), `an abandoned clock was sent: ${JSON.stringify(dawn)}`);
  eq(dawn.start, "Nautical dusk", "the start was not sent");
  // A Clock time whose clock is not a time sends the choice and no clock; the
  // NIGHT step's lock keeps the body from ever being posted (below).
  const bad: any = M.wizardBody(p, fxAnswers(p, { stop: "Clock time", stopClock: "25:00" }));
  eq([bad.stop, "stop_clock" in bad], ["Clock time", false], "an invalid clock was sent");
  // An altitude that is not a number is not sent.
  const nan: any = M.wizardBody(p, fxAnswers(p, { minAlt: "abc" }));
  assert(!("min_alt" in nan), "an altitude that is not a number was sent");
});

// MUTANT "night step not required" (stepReason's NIGHT case returns null for a
// Clock time with no HH:MM: the two `clockOf(...) === null` lines deleted).
// Observed ("wizardModel.test: 38/40 passed"):
//   x NIGHT refuses a Clock time with no HH:MM, in words, start before stop: a Clock time start with no time was not refused
//     expected "Type the time the night starts, as HH:MM on the 24-hour clock (21:30): a Clock time start has no other way to say when."
//     got      null
//   x RESUME has no gap, and firstGap names NIGHT before the review: firstGap did not name NIGHT
//     expected {"step":"night","reason":"Type the time the night stops, as HH:MM on the 24-hour clock (03:30): a Clock time stop has no other way to say when."}
//     got      null
// (The DOM half, sendToWizardSheet.test.tsx, is red under the same mutant,
// "33/34 passed": NIGHT opens on DUSK WINDOW's defaults ... and locks NEXT
// with the reason: NEXT is open on a Clock time start with no time.)
//
// MUTANT "an altitude of 0 is not sent" (wizardBody: `if (alt !== null && alt
// !== ...)` made `if (alt && alt !== ...)`, so a floor of 0 reads as blank).
// Observed ("wizardModel.test: 39/40 passed"):
//   x each changed answer sends its own key, and no other key moves: a floor of 0 is an answer, not a blank
//
// MUTANT "an abandoned start clock is sent" (wizardBody: `a.start ===
// CLOCK_TIME && clock !== null` made `clock !== null`, so a time typed and
// then left for Nautical dusk travels beside it).
// Observed ("wizardModel.test: 39/40 passed"):
//   x a clock goes with its Clock time and with nothing else: an abandoned clock was sent: {"kind":"Mosaic",...
// (The stop's own copy of that guard is redundant: DUSK_STOPS has two
// choices, so inside the `stop !== default` branch the stop is always
// "Clock time". It is kept for the day a third is offered, and has no mutant.)

test("NIGHT refuses a Clock time with no HH:MM, in words, start before stop", () => {
  const p = fxPrefill();
  const at = (over: Partial<WizardAnswers>) => M.stepReason("night", p, fxAnswers(p, over), FIELD);
  eq(at({}), null, "the defaults were refused");
  eq(at({ start: "Clock time" }), M.NEED_START_CLOCK, "a Clock time start with no time was not refused");
  eq(at({ stop: "Clock time" }), M.NEED_STOP_CLOCK, "a Clock time stop with no time was not refused");
  eq(at({ start: "Clock time", stop: "Clock time", startClock: "21:00" }), M.NEED_STOP_CLOCK,
    "the stop's clock was not asked once the start's was given");
  eq(at({ start: "Clock time", stop: "Clock time" }), M.NEED_START_CLOCK, "the start is asked first");
  for (const bad of ["25:00", "24:00", "22:60", "7:30", "22:30:00", "2230", "22.30", "noon", "", " ",
    "\u0662\u0662:\u0663\u0660"]) {
    eq(at({ stop: "Clock time", stopClock: bad }), M.NEED_STOP_CLOCK, `${JSON.stringify(bad)} was taken as a time`);
  }
  for (const good of ["00:00", "03:30", "21:15", "23:59", " 22:30 "]) {
    eq(at({ stop: "Clock time", stopClock: good }), null, `${JSON.stringify(good)} was refused`);
  }
  // A clock beside a choice that does not read it is not asked.
  eq(at({ start: "Astro dusk", startClock: "garbage", stop: "Dawn", stopClock: "garbage" }), null,
    "a clock under a choice that does not read it held the step");
  // The wording names the form and gives an example, as NEED_COORDS does.
  assert(/HH:MM/.test(M.NEED_START_CLOCK) && /21:30/.test(M.NEED_START_CLOCK), "the start clock reason has no form or example");
  assert(/HH:MM/.test(M.NEED_STOP_CLOCK) && /03:30/.test(M.NEED_STOP_CLOCK), "the stop clock reason has no form or example");
});

test("NIGHT refuses a minimum altitude that is not a number from 0 to 90", () => {
  const p = fxPrefill();
  const at = (minAlt: string) => M.stepReason("night", p, fxAnswers(p, { minAlt }), FIELD);
  for (const bad of ["", "  ", "abc", "-1", "-0.5", "90.5", "91", "NaN", "Infinity", "-Infinity", "1e1", "0x1A", "30 deg", "3,5"]) {
    eq(at(bad), M.NEED_MIN_ALT, `${JSON.stringify(bad)} was taken as an altitude`);
  }
  for (const good of ["0", "90", "45", "45.5", ".5", "30.", "+30", " 30 ", "89.99"]) {
    eq(at(good), null, `${JSON.stringify(good)} was refused`);
  }
  assert(/0 to 90/.test(M.NEED_MIN_ALT), "the altitude reason does not say the range");
});

test("RESUME has no gap, and firstGap names NIGHT before the review", () => {
  const p = fxPrefill();
  eq(M.stepReason("resume", p, fxAnswers(p, { autoResume: false }), FIELD), null, "RESUME refused an answer");
  eq(M.stepReason("resume", p, fxAnswers(p), FIELD), null, "RESUME refused the default");
  const q = fxAnswers(p, { stop: "Clock time" });
  eq(M.firstGap(p, q, FIELD), { step: "night", reason: M.NEED_STOP_CLOCK }, "firstGap did not name NIGHT");
});

test("the words the review prints restate the answers and compute nothing", () => {
  const p = fxPrefill();
  eq(M.nightWords(fxAnswers(p)), "from astro dusk to dawn, min altitude 30 deg", "the default night");
  eq(M.nightWords(fxAnswers(p, { start: "Clock time", startClock: "21:15", stop: "Clock time", stopClock: "03:30", minAlt: "42.5" })),
    "from 21:15 (clock time) to 03:30 (clock time), min altitude 42.5 deg", "a night between two clocks");
  eq(M.nightWords(fxAnswers(p, { stop: "Clock time", stopClock: "25:00" })),
    "from astro dusk to 25:00 (clock time), min altitude 30 deg", "an invalid clock shows as typed, never as a time");
  assert(/subsequent nights/.test(M.resumeWords(fxAnswers(p))), "On was not worded as resuming");
  assert(/off/.test(M.resumeWords(fxAnswers(p, { autoResume: false }))) && /CONTINUE/.test(M.resumeWords(fxAnswers(p, { autoResume: false }))),
    "Off was not worded as one night");
});

// ------------------------------------------------- the constants, and the node
//
// MUTANT "a mirrored NIGHT constant drifts" (wizardModel: DUSK_STOPS gained
// "None").
// Observed ("wizardModel.test: 38/40 passed"):
//   x every NIGHT and RESUME constant mirrored from wizard.py equals wizard.py's: DUSK_STOPS:
//     expected ["Dawn","Clock time"]
//     got      ["Dawn","Clock time","None"]
//   x the wizard's choices are the editor's DUSK WINDOW options, less the stop the server refuses: the stop choices are not the editor's less None:
//     expected ["Dawn","Clock time"]
//     got      ["Dawn","Clock time","None"]

/** A Python tuple of string literals, as wizard.py writes them (one line, or a
 *  parenthesised run that wraps). */
function pyTuple(name: string): string[] {
  const m = new RegExp(`^${name}: tuple\\[str, \\.\\.\\.\\] = \\(([^)]*)\\)$`, "m").exec(WIZARD_PY);
  assert(m, `wizard.py has no string tuple ${name}`);
  return Array.from(m![1].matchAll(/"([^"]*)"/g)).map((x) => x[1]);
}

test("every NIGHT and RESUME constant mirrored from wizard.py equals wizard.py's", () => {
  eq([...M.DUSK_STARTS], pyTuple("DUSK_STARTS"), "DUSK_STARTS:");
  eq([...M.DUSK_STOPS], pyTuple("DUSK_STOPS"), "DUSK_STOPS:");
  eq([...M.AUTO_RESUME_CHOICES], pyTuple("AUTO_RESUME_CHOICES"), "AUTO_RESUME_CHOICES:");
  eq(M.CLOCK_TIME, pyString("CLOCK_TIME"), "CLOCK_TIME:");
  eq([M.MIN_ALT_MIN_DEG, M.MIN_ALT_MAX_DEG], [pyNumber("MIN_ALT_MIN_DEG"), pyNumber("MIN_ALT_MAX_DEG")], "the altitude range:");
});

test("the wizard's choices are the editor's DUSK WINDOW options, less the stop the server refuses", () => {
  const dusk = NODE_DEFS.dusk;
  const options = (key: string) => [...(dusk.fields.find((f) => f.key === key)?.options ?? [])];
  eq([...M.DUSK_STARTS], options("start"), "the start choices are not the editor's:");
  eq([...M.DUSK_STOPS], options("stop").filter((s) => s !== "None"), "the stop choices are not the editor's less None:");
  assert(options("stop").includes("None"), "premise: the editor no longer offers a stop of None, so the wizard's note about it is stale");
  eq([...M.AUTO_RESUME_CHOICES], options("autoResume"), "the resume choices are not the editor's:");
  // The defaults a sheet opens on are choices the wizard offers.
  assert((M.DUSK_STARTS as readonly string[]).includes(String(dusk.params.start)), "DUSK's default start is not a wizard choice");
  assert((M.DUSK_STOPS as readonly string[]).includes(String(dusk.params.stop)), "DUSK's default stop is not a wizard choice");
  assert((M.AUTO_RESUME_CHOICES as readonly string[]).includes(String(dusk.params.autoResume)), "DUSK's default autoResume is not a choice");
  const alt = Number(dusk.params.minAlt);
  assert(Number.isFinite(alt) && alt >= M.MIN_ALT_MIN_DEG && alt <= M.MIN_ALT_MAX_DEG, "DUSK's default minAlt is outside the wizard's range");
});

// ----------------------------------------------------------- the server's brief
//
// MUTANT "the brief prints the whole answer" (briefOf: `b.trim()` made
// `JSON.stringify(answer)`, so everything the Tonight answer derives from the
// site would be on the review).
// Observed ("wizardModel.test: 39/40 passed"):
//   x the brief is the Tonight answer's own sentence, and only that key is read: the brief was not read, or was not trimmed
//     expected "This flow arms at astro dusk."
//     got      "{\"brief\":\"  This flow arms at astro dusk.  \",\"windows\":[{\"at\":\"x\"}]}"
// (sendToWizardSheet.test.tsx is red under it too, "31/34 passed", on the
// review's first line and on the marker the rest of the answer carries.)

test("the brief is the Tonight answer's own sentence, and only that key is read", () => {
  eq(M.briefOf({ brief: "  This flow arms at astro dusk.  ", windows: [{ at: "x" }] }), "This flow arms at astro dusk.",
    "the brief was not read, or was not trimmed");
  for (const none of [null, undefined, "text", 3, [], {}, { brief: "" }, { brief: "   " }, { brief: 7 }, { brief: null }]) {
    eq(M.briefOf(none), null, `${JSON.stringify(none)} read as a brief`);
  }
});

test("the review's first line is the brief, or why there is none", () => {
  eq(M.briefLine({ state: "ok", text: "This flow arms at astro dusk." }), "This flow arms at astro dusk.", "an ok read");
  eq(M.briefLine({ state: "waiting" }), M.BRIEF_WAITING, "a read in flight");
  eq(M.briefLine({ state: "unavailable" }), M.BRIEF_UNAVAILABLE, "a read that failed");
  eq(M.BRIEF_UNAVAILABLE, "The brief is unavailable.", "the failure sentence moved");
});

// ------------------------------------------------------------------ report
const total = passed + failed;
console.log(`wizardModel.test: ${passed}/${total} passed`);
for (const f of failures) console.log(f);
export const result = { passed, failed, total };
export default result;
