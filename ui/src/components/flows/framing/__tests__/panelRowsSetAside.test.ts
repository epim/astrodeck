// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// panelRowsSetAside.test.ts - in run mode, a panel set aside tonight has no
// place in tonight's order (#528; #189 S7, found by the real-page probe's
// forced-solve-failure walk, tools/ui_probe/routes_s7.json scenario 2).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/framing/__tests__/panelRowsSetAside.test.ts   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT THE WALK SAW. A rotating 2x2, least complete first, whose panel 2-2
// the engine set aside at its third failed pass: PANELS read "1 2-2" and the
// sky's dotted 2-2 carried a "1" badge, while the run went on 1-1, 1-2, 2-1.
// 2-2 banked nothing, so least complete first ranks it 1 on every such
// mosaic, and the engine owes it no visit for the rest of the night (spec
// 5.10, `_visits_owed`).
//
// WHAT IS GRADED. With a run state that sets 2-2 aside, `panelRows` numbers
// the other three 1 to 3 in the order the run takes them, lists 2-2 after
// them unnumbered and not marked skipped, and puts a skipped panel after
// that. The CONTROL is the same grid with no run (the editor, or run mode
// before anything is set aside): 2-2 is numbered 1, as least complete first
// has always ranked it, so the change is the run state and nothing else.
//
// Every mutant below was run in a private scratch copy of ui/ (scratchpad
// S7-PROBE-mut, #254), never in the shared tree, and the failure it produced
// is quoted verbatim.
//
// MUTANT "set-aside numbered" (PanelsSection.tsx `panelRows`: the
// `!asideTonight(c)` filter on the live panels removed), 1/3. Observed:
//   x a panel set aside tonight is listed after the panels that run, with no number: tonight's order: expected [["1-1",1],["1-2",2],["2-1",3],["2-3",4],["2-2",null],["1-3",null]], got [["2-2",1],["1-1",2],["1-2",3],["2-1",4],["2-3",5],["2-2",1],["1-3",null]]
//   x the numbers are 1 to n with no gap, so the sky's badges read as a sequence: the badges: expected [1,2,3,4], got [1,2,3,4,5,1]
//
// MUTANT "set-aside dropped" (the `aside` rows left out of the result), 2/3.
// Observed:
//   x a panel set aside tonight is listed after the panels that run, with no number: tonight's order: expected [["1-1",1],["1-2",2],["2-1",3],["2-3",4],["2-2",null],["1-3",null]], got [["1-1",1],["1-2",2],["2-1",3],["2-3",4],["1-3",null]]

/* eslint-disable @typescript-eslint/no-explicit-any */

import { panelRows, runRowText } from "../sections/PanelsSection";
import type { FlowProgressBlock } from "../../../../lib/flowsApi";
import type { PanelRunState } from "../../flowRunState";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: unknown, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (JSON.stringify(got) !== JSON.stringify(want)) {
    throw new Error(`${msg}: expected ${JSON.stringify(want)}, got ${JSON.stringify(got)}`);
  }
}

// The walk's grid, 2 x 3 here so a skipped panel (1-3) sits beside the
// set-aside one: every panel banked 2 of 20 but 2-2, which banked none.
const PROGRESS: FlowProgressBlock = {
  node_id: "n2", name: "M31", kind: "target", banked: 10, owed: 110, total: 120,
  grid: { rows: 2, cols: 3 },
  panels: [
    { target_id: "a", name: "M31 1-1", row: 0, col: 0, banked: 2, owed: 18, total: 20, steps: [] },
    { target_id: "b", name: "M31 1-2", row: 0, col: 1, banked: 2, owed: 18, total: 20, steps: [] },
    { target_id: "d", name: "M31 2-1", row: 1, col: 0, banked: 2, owed: 18, total: 20, steps: [] },
    { target_id: "e", name: "M31 2-2", row: 1, col: 1, banked: 0, owed: 20, total: 20, steps: [] },
    { target_id: "f", name: "M31 2-3", row: 1, col: 2, banked: 4, owed: 16, total: 20, steps: [] },
  ],
  skipped: [{ row: 0, col: 2, banked: 0 }],
} as any;

const REASON = "centring failed on 2-2 on 3 consecutive visits: plate solve failed \u2014 used raw GoTo";
// forNow: false -- a panel struck out a second time, set aside for the rest
// of the night (#573, #534 follow-up): this file grades the NUMBERING rule,
// which is the same whichever wording the row carries (panelRowsSetAside
// only, not the wording itself: w7SetAsideForNow.test.ts grades that).
const RUN: Record<string, PanelRunState> = {
  "1-1": { kind: "shooting" } as PanelRunState,
  "2-2": { kind: "set_aside", reason: REASON, forNow: false },
};

function rowsOf(run?: Record<string, PanelRunState>) {
  return panelRows({
    rows: 2, cols: 3, skip: [[1, 3]], progress: PROGRESS, answerPanels: null,
    order: "Least complete first", run,
  });
}

test("CONTROL: with no run, least complete first numbers 2-2 first, as it always has", () => {
  const rows = rowsOf();
  eq(rows.map((r) => [r.label, r.order]),
     [["2-2", 1], ["1-1", 2], ["1-2", 3], ["2-1", 4], ["2-3", 5], ["1-3", null]],
     "the editor's order");
});

test("a panel set aside tonight is listed after the panels that run, with no number", () => {
  const rows = rowsOf(RUN);
  eq(rows.map((r) => [r.label, r.order]),
     [["1-1", 1], ["1-2", 2], ["2-1", 3], ["2-3", 4], ["2-2", null], ["1-3", null]],
     "tonight's order");
  const aside = rows.find((r) => r.label === "2-2")!;
  assert(!aside.skipped, "set aside is not skipped: its toggle stays ON, the run owes it frames");
  eq(runRowText(aside.label, aside.run), `set aside tonight: ${REASON}`,
     "its row still says why, in the engine's words");
  eq(rows.find((r) => r.label === "1-3")!.skipped, true, "the skipped panel stays skipped, last");
});

test("the numbers are 1 to n with no gap, so the sky's badges read as a sequence", () => {
  const nums = rowsOf(RUN).map((r) => r.order).filter((n): n is number => n !== null);
  eq(nums, [1, 2, 3, 4], "the badges");
});

// ------------------------------------------------------------------ report
const total = passed + failed;
console.log(`panelRowsSetAside.test: ${passed}/${total} passed`);
for (const f of failures) console.log(f);
export const result = { passed, failed, total };
export default result;
