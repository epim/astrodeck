// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w16QuickDarksCopy.test.ts - WP-144 (#745): the quick sheet's DARKS AFTER card
// says what the queue it adds actually does.
//
//   Run directly:  npx tsx src/next/hubs/sky/sheets/__tests__/w16QuickDarksCopy.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT (#745). The `darks` entry of quickCopy's INFO table read "Queues
// darks and bias at the end of the night, with the wheel rotated to a blackout
// slot." Three claims, and the engine keeps none of them as written:
//
//   * BIAS. The queue's bias and flat legs are not wired into the engine
//     (`to_plan`'s calibration-queue note), so nothing takes a bias frame.
//   * AT THE END OF THE NIGHT. The engine's end-of-night darks are the
//     wind-down's day-darks lane (`SequenceEngine._day_darks`), funded only
//     when a calibration queue is wired to SHUTDOWN COMPLETE (a PARK + CLOSE
//     node's `closed` port; `to_plan.plan_extras`). `withDarksAfter` wires the
//     queue from the REPORT's `done` event instead (`on_target_complete ->
//     calib`), a rule the engine has no action for ("this rule will not
//     run"). So the lane is never funded for the flow this chip builds. The
//     issue's own fix shape ("say darks only") assumed it was; reading the
//     compile of the real quick graph showed it is not, which is why this card
//     says more than the issue asked.
//   * WHAT DOES RUN. The queue's quota does reach the plan as the cloud hold's
//     dark quota (`cloud_hold_darks`), so a cloud hold takes darks matched to
//     the frame it interrupted, capped at what the library lacks. A clear
//     night takes none.
//
// `server/tests/test_w16_quick_darks_after_wiring.py` holds the server half of
// that (the compile of this exact wiring funds `cloud_hold_darks` and not
// `day_darks`). This file holds the UI half: the wiring `withDarksAfter` draws
// is the one the card describes, and the card's words.
//
// THE PREMISE TEST IS THE GUARD AGAINST THE COPY OUTLIVING A FIX. The day
// `withDarksAfter` is rewired to SHUTDOWN COMPLETE the end-of-night lane is
// funded and the card may say so again; the premise below goes red then, and
// its message says which sentence to revisit.
//
// MUTATION RECORD, 2026-10-07, run from a byte backup of quickCopy.ts inside
// this worktree and restored byte-identically (sha256 compared):
//
//   MUTANT "the old card restored" (quickCopy.ts INFO.darks.b put back to
//   "Queues darks and bias at the end of the night, with the wheel rotated to a
//   blackout slot. They are shot at the same temperature and exposure as
//   tonight's lights, which is the only way the library matches them later."):
//     Observed ("w16QuickDarksCopy.test: 2/5 passed"):
//     x the card says darks only, and that bias is not run: the card still says the queue takes bias: Queues darks and bias at the end of the night, with the wheel rotated to a blackout slot. [...]
//     x the card does not promise darks at the end of the night, and says what does run: the card promises darks at the end of the night, a lane this chip's wiring never funds: Queues darks and bias at the end of the night, [...]
//     x the card, word for word: INFO.darks.b: expected Adds a calibration queue that takes darks only; bias is not run yet. [...], got Queues darks and bias at the end of the night, [...]
//   The premise test (the wiring) is green under it, as it must be: the mutant
//   changes the words, not the wire. A mutant on the wire itself would be a
//   change to flowGraphExtras.ts, which this work package does not own; the
//   server file's mutants (`test_w16_quick_darks_after_wiring.py`) cover the
//   funding half of the premise.

import { INFO } from "../quickCopy";
import { withDarksAfter } from "../flowGraphExtras";
import type { FlowGraphRec } from "../../../../../components/flows/flowsTypes";

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

/** The card's words, word for word. */
const DARKS_CARD =
  "Adds a calibration queue that takes darks only; bias is not run yet. Each dark is shot "
  + "with the wheel rotated to a blackout slot, at the same temperature and exposure as "
  + "tonight's lights, which is the only way the library matches them later. They are taken "
  + "while a cloud hold lasts, up to what the library still lacks, so a clear night takes "
  + "none and nothing is queued for the end of the night.";

/** A server-generated lane's two ends: the quick graph always ends in a report. */
function lane(): FlowGraphRec {
  return {
    nodes: [
      { id: "n1", type: "dusk", x: 30, y: 60, params: {} },
      { id: "n7", type: "report", x: 258, y: 60, params: {} },
    ],
    edges: [{ id: "we0", from: "n1", fromPort: "window", to: "n7", toPort: "session" }],
  };
}

test("the queue DARKS AFTER adds is wired from the report's done event, not from SHUTDOWN COMPLETE", () => {
  const g = withDarksAfter(lane());
  const calib = g.nodes.find((n) => n.type === "calib");
  assert(calib != null, "no CALIBRATION QUEUE node was added");
  const wire = g.edges.find((e) => e.to === calib!.id && e.toPort === "do");
  assert(wire != null, "nothing starts the queue");
  const source = g.nodes.find((n) => n.id === wire!.from);
  eq(`${source?.type}.${wire!.fromPort}`, "report.done",
    "the quick queue is no longer wired from the report (premise of the card's copy: if it "
    + "is now wired to a PARK + CLOSE node's `closed` port the end-of-night lane is funded "
    + "and quickCopy.ts INFO.darks may say so again, with this test updated with it):");
  assert(!g.nodes.some((n) => n.type === "parkclose"),
    "a PARK + CLOSE node is now added: the card may be able to promise end-of-night darks");
});

test("the card says darks only, and that bias is not run", () => {
  const b = INFO.darks.b;
  assert(!/darks and bias/i.test(b) && !/and bias/i.test(b),
    `the card still says the queue takes bias: ${b}`);
  assert(/bias is not run yet/.test(b), `the card does not say bias is not run: ${b}`);
});

test("the card does not promise darks at the end of the night, and says what does run", () => {
  const b = INFO.darks.b;
  assert(!/at the end of the night/i.test(b),
    `the card promises darks at the end of the night, a lane this chip's wiring never funds: ${b}`);
  assert(/cloud hold/.test(b), `the card does not say a cloud hold is when the darks are taken: ${b}`);
  assert(/clear night takes none/.test(b), `the card does not say a clear night takes none: ${b}`);
});

test("the card, word for word", () => {
  eq(INFO.darks.b, DARKS_CARD, "INFO.darks.b:");
});

test("control: the chip's topic and title are unchanged, and the card has no dash", () => {
  // quick.tsx keys the hold-to-learn brief by `info: "darks"`, and the title is
  // the chip's own name; only the paragraph is this change.
  eq(INFO.darks.t, "DARKS AFTER", "the brief's title:");
  assert(!/[—–]/.test(INFO.darks.b), "UI strings use hyphens, never em or en dashes");
});

const total = passed + failed;
console.log(`w16QuickDarksCopy.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) {
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}

export default { passed, failed, total };
export { passed, failed, total };
