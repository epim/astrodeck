// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w6_5CoverageModel.test.ts - the pure reading of the coverage route's answer
// (#177 package 2, WP 6.5): which block it is about, what a panel's line says.
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/framing/__tests__/w6_5CoverageModel.test.ts   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// w6_5CoverageMap.test.tsx grades what the mounted sheet draws from it; this
// file holds the rules that need no sheet: the block is found by its group id
// and by the session its progress counted (never by name), the worst overlap
// is rounded DOWN so a line never says a light covers the threshold it missed,
// and an unchecked panel is never called covered.

import { blockCoverage, panelCoverage } from "../coverageModel";
import type { CoverageAnswer, CoveragePanel } from "../../../../types";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function eq<T>(got: T, want: T, msg: string): void {
  if (JSON.stringify(got) !== JSON.stringify(want)) {
    throw new Error(`${msg}: expected ${JSON.stringify(want)}, got ${JSON.stringify(got)}`);
  }
}

function panel(over: Partial<CoveragePanel> = {}): CoveragePanel {
  return {
    target_id: "t", name: "n", row: 0, col: 0, frames: 10, stamped: 10, unstamped: 0, flagged: 0,
    worst_overlap_frac: 0.95, worst_offset_arcmin: 1, worst_angle_off_deg: null, ...over,
  };
}
function answer(over: Partial<CoverageAnswer> = {}, threshold = 0.875): CoverageAnswer {
  return {
    session: { id: "s1", status: "active" }, stamping: true, note: null,
    groups: [{ group_id: "g1", name: "M31", threshold, panels: [panel()] },
      { group_id: "g2", name: "M31", threshold, panels: [panel({ row: 1, col: 1 })] }],
    ...over,
  };
}

test("a block is found by its group id, not its name, and only in the session its progress counted", () => {
  const b = blockCoverage(answer(), "g2", "s1");
  eq(Array.from(b!.panels.keys()), ["1,1"], "g2's panels, though g1 has the same name");
  eq(blockCoverage(answer(), "g1", "other"), null, "another session");
  eq(blockCoverage(answer(), "g3", "s1"), null, "a group the answer lacks");
  eq(blockCoverage(answer(), null, "s1"), null, "a block with no group");
  eq(blockCoverage(answer({ session: null }), "g1", "s1"), null, "no session");
  eq(blockCoverage(null, "g1", "s1"), null, "no answer");
  // The progress answer's session is not known yet: the group alone decides.
  eq(blockCoverage(answer(), "g1", undefined)?.threshold, 0.875, "no session asked for");
});

test("the note is the block's only with stamping off", () => {
  eq(blockCoverage(answer({ stamping: true, note: "n" }), "g1", "s1")?.note, null, "stamping on");
  eq(blockCoverage(answer({ stamping: false, note: "n" }), "g1", "s1")?.note, "n", "stamping off");
  eq(blockCoverage(answer({ stamping: false, note: null }), "g1", "s1")?.note, null, "off with no note");
});

test("the worst overlap is rounded down and the threshold shown as the server holds it", () => {
  const b = blockCoverage(answer({}, 0.6), "g1", "s1")!;
  // 59.96 percent must not read "60%" under "under 60%".
  eq(panelCoverage(panel({ flagged: 1, worst_overlap_frac: 0.5996 }), "1-1", b)?.line,
    "1 of 10 checked frames cover under 60% of panel 1-1; the worst is 59%, and the furthest is 1 arcmin off centre",
    "the worst, rounded down");
  eq(panelCoverage(panel({ flagged: 2, worst_overlap_frac: null, worst_offset_arcmin: null }), "1-1", b)?.line,
    "2 of 10 checked frames cover under 60% of panel 1-1", "no worst and no offset");
  eq(panelCoverage(panel({ flagged: 2, worst_overlap_frac: null }), "1-1", b)?.line,
    "2 of 10 checked frames cover under 60% of panel 1-1; the furthest is 1 arcmin off centre",
    "an offset with no worst");
});

test("a panel is short exactly when a stamped frame is flagged, and an unchecked one is never covered", () => {
  const on = blockCoverage(answer(), "g1", "s1")!;
  eq(panelCoverage(panel({ flagged: 1 }), "1-1", on)?.verdict, "short", "flagged");
  eq(panelCoverage(panel(), "1-1", on), null, "clean: plain");
  eq(panelCoverage(panel({ frames: 0, stamped: 0 }), "1-1", on), null, "no frames: nothing to say");
  eq(panelCoverage(undefined, "1-1", on), null, "not in the answer");
  eq(panelCoverage(panel({ stamped: 0, unstamped: 10 }), "1-1", on),
    { verdict: null, line: "not checked yet: 10 frames banked, none plate-solved" }, "unchecked");
  eq(panelCoverage(panel({ frames: 1, stamped: 0, unstamped: 1 }), "1-1", on)?.line,
    "not checked yet: 1 frame banked, none plate-solved", "one unchecked frame");
  // Stamping off: the note says it once, so no line per panel; a panel
  // checked while it was on still says what fell short.
  const off = blockCoverage(answer({ stamping: false, note: "n" }), "g1", "s1")!;
  eq(panelCoverage(panel({ stamped: 0, unstamped: 10 }), "1-1", off), null, "unchecked, stamping off");
  eq(panelCoverage(panel({ flagged: 1 }), "1-1", off)?.verdict, "short", "flagged, stamping off");
});

const total = passed + failed;
console.log(`w6_5CoverageModel.test: ${passed}/${total} passed`);
for (const f of failures) console.log(f);
if (failed) (globalThis as any).process.exitCode = 1;
export const result = { passed, failed, total };
export default result;
