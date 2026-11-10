// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w5PanelOverridesModel.test.tsx - the modal's reading of a TARGET's
// `panelExposure` text, held to the server's (#186, WP 5.4).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/framing/__tests__/w5PanelOverridesModel.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE CASES ARE server/tests/fixtures/w5_panel_exposure_cases.json, READ, NOT
// COPIED. test_w5_panel_overrides.py grades compile.py `parse_panel_exposure`
// against the same file, so the PANELS list draws a panel's own exposure from
// the reading the run shoots by.
//
// Each mutant below was run in a private copy of the file in the WP scratch
// directory, and the failure it produced is quoted.
//
// disk-reads: server/tests/fixtures/w5_panel_exposure_cases.json, ui/src/components/flows/flowRunState.ts
// disk-reads: ui/src/components/flows/framing/framingModel.ts, ui/src/components/flows/nodeDefs.ts
// disk-reads: ui/src/lib/frameView.ts, ui/src/lib/framing.ts, ui/src/lib/lastSessionFrame.ts
// disk-reads: ui/src/lib/objectShape.ts, ui/src/lib/optics.ts

/* eslint-disable @typescript-eslint/no-explicit-any */

// @ts-ignore  node built-ins; tsx supplies them at runtime
import { readFileSync } from "node:fs";

import {
  draftFromParams, framingPatch, panelExposureProblem, parsePanelExposure, setPanelExposure,
} from "../framingModel";
import type { Params } from "../framingModel";
import { NODE_DEFS } from "../../nodeDefs";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
const js = (v: unknown) => JSON.stringify(v);
function eq(got: unknown, want: unknown, what: string): void {
  assert(js(got) === js(want), `${what}: expected ${js(want)}, got ${js(got)}`);
}

// ------------------------------------------------------------- the cases
const FIXTURE_REL = "../../../../../../server/tests/fixtures/w5_panel_exposure_cases.json";
interface Case { name: string; text: string; rows: number; cols: number; panels: [number, number, number, number | null][]; unread: string[] }

/** A missing or unreadable file must FAIL, never skip: a skipped fixture
 *  reads as a green mirror. */
function readCases(): Case[] {
  let text: string;
  try {
    text = readFileSync(new URL(FIXTURE_REL, import.meta.url), "utf8") as string;
  } catch (e) {
    throw new Error(`cannot read ${FIXTURE_REL}, the cases this mirror is graded against: ${(e as Error).message}`);
  }
  const cases = (JSON.parse(text) as { cases?: Case[] }).cases;
  if (!Array.isArray(cases) || cases.length < 15) throw new Error(`${FIXTURE_REL} holds too few cases`);
  return cases;
}

// MUTANT "mirror allows zero seconds" (parsePanelExposure: `seconds > 0` made
// `seconds >= 0`). Observed:
//   x the mirror reads every server panel-exposure case as parse_panel_exposure does: zero seconds
//     ("1-1:0" on 2x2): expected {"panels":[],"unread":["1-1:0"]}, got
//     {"panels":[[1,1,0,null]],"unread":[]}
test("the mirror reads every server panel-exposure case as parse_panel_exposure does", () => {
  const bad: string[] = [];
  for (const c of readCases()) {
    const got = parsePanelExposure(c.text, c.rows, c.cols);
    const flat = { panels: got.panels.map((p) => [p.row, p.col, p.seconds, p.count]), unread: got.unread };
    const want = { panels: c.panels, unread: c.unread };
    if (js(flat) !== js(want)) bad.push(`${c.name} (${js(c.text)} on ${c.rows}x${c.cols}): expected ${js(want)}, got ${js(flat)}`);
  }
  assert(bad.length === 0, bad.join("; "));
});

// The cases the shared table cannot hold: past CPython's 4300-digit limit
// `int()` raises on the server, and 400 nines is a float's infinity.
// MUTANT "no digit limit" (framingModel `pyInt`: the PY_INT_MAX_DIGITS test
// deleted). Observed (the count reads as Infinity, which JSON prints as null,
// and the entry is read; the 4301 nines are elided):
//   x an entry the server cannot read as a number is unread: a count of 4301 digits: expected
//     [[],1], got [[{"row":1,"col":1,"seconds":30,"secondsText":"30","count":null,"countText":"99...9"}],0]
test("an entry the server cannot read as a number is unread", () => {
  const count = parsePanelExposure("1-1:30x" + "9".repeat(4301), 2, 2);
  eq([count.panels, count.unread.length], [[], 1], "a count of 4301 digits");
  const row = parsePanelExposure("0".repeat(4300) + "1-1:30", 2, 2);
  eq([row.panels, row.unread.length], [[], 1], "a row of 4301 digits whose value is 1");
  const seconds = parsePanelExposure("1-1:" + "9".repeat(400), 2, 2);
  eq([seconds.panels, seconds.unread.length], [[], 1], "seconds past a float");
});

test("the typed digits are kept, so what is written back is what was typed", () => {
  const got = parsePanelExposure("1-1:030.50x007", 2, 2);
  eq(got.panels.map((p) => [p.secondsText, p.countText, p.seconds, p.count]), [["030.50", "007", 30.5, 7]],
    "the digits and the numbers");
});

// ------------------------------------------------------ setting and clearing

// MUTANT "unread entries dropped" (setPanelExposure: `...unread` left out of
// the join). Observed:
//   x setting a panel keeps the entries the grid cannot read, and puts the rest in grid order: a
//     first panel: expected "1-1:30, 2-2:90, 4-1:30", got "1-1:30, 2-2:90"
test("setting a panel keeps the entries the grid cannot read, and puts the rest in grid order", () => {
  eq(setPanelExposure("2-2:90, 4-1:30", 2, 2, 1, 1, "30", ""), "1-1:30, 2-2:90, 4-1:30", "a first panel");
  eq(setPanelExposure("1-1:30, 4-1:30", 2, 2, 2, 1, "60", "5"), "1-1:30, 2-1:60x5, 4-1:30", "setting 2-1 beside a kept 4-1");
  eq(setPanelExposure("", 2, 2, 2, 2, "0.5", ""), "2-2:0.5", "from blank");
});

test("setting a panel again replaces its entry, and a blank pair clears it", () => {
  eq(setPanelExposure("1-1:30x4, 2-2:90", 2, 2, 1, 1, "45", ""), "1-1:45, 2-2:90", "changed, count dropped");
  eq(setPanelExposure("1-1:30x4, 2-2:90", 2, 2, 1, 1, "", ""), "2-2:90", "cleared");
  eq(setPanelExposure("1-1:30", 2, 2, 1, 1, "", ""), "", "the last one cleared leaves blank text");
  eq(setPanelExposure("2-2:90", 2, 2, 1, 1, " ", " "), "2-2:90", "clearing a panel that has none");
});

test("a pair that is not an override writes nothing, and says why", () => {
  eq(setPanelExposure("1-1:30", 2, 2, 1, 1, "abc", ""), null, "seconds not a number");
  eq(setPanelExposure("1-1:30", 2, 2, 1, 1, "0", ""), null, "zero seconds");
  eq(setPanelExposure("1-1:30", 2, 2, 1, 1, "30", "0"), null, "a count of zero");
  eq(setPanelExposure("1-1:30", 2, 2, 1, 1, "", "5"), null, "a count with no exposure");
  eq(setPanelExposure("1-1:30", 2, 2, 9, 9, "30", ""), null, "a panel off the grid");
  eq(panelExposureProblem("30", "5"), null, "a good pair");
  eq(panelExposureProblem("", ""), null, "blank, which is a clear");
  assert(/above zero/.test(panelExposureProblem("abc", "") ?? ""), "the words for bad seconds");
  assert(/needs an exposure/.test(panelExposureProblem("", "5") ?? ""), "the words for a lone count");
  assert(/whole number/.test(panelExposureProblem("30", "x") ?? ""), "the words for a bad count");
});

// ------------------------------------------------------------ the draft

// MUTANT "draft keys omit panelExposure" (framingModel DRAFT_KEYS without
// "panelExposure"). Observed:
//   x the draft carries panelExposure and DONE's patch writes it only when it changed: a node that
//     never carried it opens blank: expected "", got undefined
// MUTANT "clear keeps the entry" (setPanelExposure: `others` is every panel when
// the exposure is blank). Observed:
//   x setting a panel again replaces its entry, and a blank pair clears it: cleared: expected
//     "2-2:90", got "1-1:30x4, 2-2:90"
test("the draft carries panelExposure and DONE's patch writes it only when it changed", () => {
  const params: Params = { ...NODE_DEFS.target.params, rows: 2, cols: 2, name: "M31" };
  const draft = draftFromParams(params);
  eq(draft.panelExposure, "", "a node that never carried it opens blank");
  eq(framingPatch(params, draft), {}, "an untouched draft writes nothing");
  eq(framingPatch(params, { ...draft, panelExposure: "1-1:30" }), { panelExposure: "1-1:30" }, "the patch for a set override");
  const set: Params = { ...params, panelExposure: "1-1:30" };
  eq(framingPatch(set, draftFromParams(set)), {}, "an untouched draft over a set override writes nothing");
  eq(framingPatch(set, { ...draftFromParams(set), panelExposure: "" }), { panelExposure: "" }, "the patch for a cleared one");
});

// ------------------------------------------------------------------ report
const total = passed + failed;
console.log(`w5PanelOverridesModel.test: ${passed}/${total} passed`);
for (const f of failures) console.log(f);
export const result = { passed, failed, total };
export default result;
