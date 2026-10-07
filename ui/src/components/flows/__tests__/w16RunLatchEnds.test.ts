// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w16RunLatchEnds.test.ts - when the client's optimistic `flows.run.phase` is let
// go (#717; WP-145, wave 16).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/__tests__/w16RunLatchEnds.test.ts   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT (read from the code, #717). `flowsRun` sets `flows.run.phase` to a
// live value the instant its POST returns, and `flowsSlice.onSequence` clears it
// back to idle when the run ENDS, but only when `ended && openFlowOwns(was)`:
// the ended run's session has to be one the OPEN flow knows (`knownSessions`),
// and `knownSessions` answers none while no record is open. So: start RUN, close
// the editor, let the run end, open any flow, and `run.phase` is still
// 'running'. Four readers take the phase raw, with no bridge of their own
// (`FlowEditor.tsx`'s `data-flows-run` marker, `FlowWireLayer.tsx` and
// `FlowWires.tsx`, which march the wires on any phase but idle, and
// `FlowCanvasToolbar.tsx`'s marker), so a flow that has no run drew its wires
// marching.
//
// THE FIX IS IN TWO PIECES, because `flowsSlice.ts` is another work package's
// this wave. This file is the decision, a pure function in `flowRunState.ts`
// (`runLatchEnds`): the latch ends when the open flow's own run ended (what the
// slice already decided) OR when a live run ended at all, whoever's it was and
// whichever flow is open. The slice's `onSequence` asks it in place of its own
// `ended && isRunPhaseLive(phase)`; the one-line change is in the return of
// WP-145 (`blocked_on`), and was run against the slice's own cases in a scratch
// copy of ui/ (scratchpad/WP-145/ui-copy), where the closed-flow case that is red
// on the slice as it stands is green with it.
//
// WHAT IS WORTH ASSERTING
//
//   1. A LIVE RUN ENDING ENDS THE LATCH WITH NO FLOW OPEN. Every live state
//      (running, paused, holding, aborting) ending in every terminal one, and in
//      idle.
//   2. THE OWN-FLOW RULE IS KEPT. `ownFlowRunEnded` still ends the latch on its
//      own, as the slice has always decided it, so a run switching sessions in
//      one write (live to live, which `liveRunEnded` does not call an end) is not
//      a regression.
//   3. NOTHING ELSE ENDS IT. A write inside a live run (a frame landing, a hold
//      starting, paused to aborting), the engine's first publish (idle to
//      running), a terminal state with no live run before it (a reconnect's
//      snapshot of last night) and a write with nothing before it all leave a
//      latch that `flowsRun` has just set. Ending it on those would bring back
//      the stale-RUN flicker the bridge window exists for.
//   4. AN IDLE LATCH IS NEVER REWRITTEN. The subscription fires on every
//      sequence write, so a latch that is already idle must answer false or the
//      slice rewrites `flows.run` on every status tick.
//
// MUTANTS RUN (each from a byte backup of flowRunState.ts, restored
// byte-identically with sha256 compared, the mutant text grepped out). The
// failing assertion each produced, verbatim (the file is 23 cases):
//
//   V1 "only the open flow's own run ends the latch" (`|| liveRunEnded(prev,
//      next)` removed from `runLatchEnds`: the slice as it stands), 5/23: "x a run
//      running -> complete ends a live latch with no flow open: the latch
//      survived a run's end (running -> complete) with no open flow to own it
//      (expected true, got false)"
//   V2 "any write ends the latch" (`liveRunEnded` made `true`), 18/23: "x the
//      open flow's own run ending ends the latch on its own, as the slice decided
//      it: premise: live -> live is not an end (expected false, got true)", "x a
//      write inside a live run leaves the latch alone: liveRunEnded(running ->
//      running) (expected false, got true)"
//   V3 "an idle latch is rewritten" (the `isRunPhaseLive(phase)` guard removed),
//      22/23: "x an idle latch is not rewritten by any write, however the run
//      ended: an already-idle latch was rewritten (running -> complete): the slice
//      would write flows.run on every tick (expected false, got true)"
//   V4 "a terminal state with no live run before it ends the latch"
//      (`runIsLive(prev) &&` removed from `liveRunEnded`), 21/23: "x a terminal
//      state with no live run before it leaves the latch (a reconnect's snapshot
//      of last night): a terminal write with no live run before it (complete ->
//      complete) ended a fresh latch (expected false, got true)", "x a write with
//      nothing before it leaves the latch: undefined -> complete (expected false,
//      got true)"
//   V5 "the own-flow rule is dropped" (`ownFlowRunEnded ||` removed), 22/23: "x
//      the open flow's own run ending ends the latch on its own, as the slice
//      decided it: the own-flow end no longer ends the latch (expected true, got
//      false)"
//
// THE WIRING (the slice asking `runLatchEnds`) is graded by
// `scratchpad/WP-145/w16RunLatchClosedFlow.test.ts`, through the real slice behind
// a miniature store, in a scratch copy of ui/ with the one-line change applied
// (see WP-145's `blocked_on`). Against the slice as it stands it reads 3/5, the
// two closed-flow cases red: "x #717: a run that ends with NO FLOW OPEN clears the
// latch: start RUN, close the editor, let the run end: run.phase is still
// 'running' / expected "idle" / got "running"" and the same for "while ANOTHER
// flow is open"; with the change, 5/5.

import { isRunPhaseLive, liveRunEnded, runLatchEnds } from "../flowRunState";
import type { SequenceState } from "../../../types";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg} (expected ${String(want)}, got ${String(got)})`);
}

const seq = (state: SequenceState["state"], extra: Partial<SequenceState> = {}): SequenceState =>
  ({ state, ...extra } as SequenceState);

const LIVE: SequenceState["state"][] = ["running", "paused", "holding", "aborting"];
const ENDED: SequenceState["state"][] = ["complete", "aborted", "error", "idle"];

// ============================================ 1. a live run ending ends the latch
for (const was of LIVE) {
  for (const now of ENDED) {
    test(`a run ${was} -> ${now} ends a live latch with no flow open`, () => {
      eq(liveRunEnded(seq(was), seq(now)), true, `liveRunEnded(${was} -> ${now})`);
      eq(runLatchEnds("running", false, seq(was), seq(now)), true,
        `the latch survived a run's end (${was} -> ${now}) with no open flow to own it`);
    });
  }
}

test("every phase the client latches is let go (running, holding, stopping)", () => {
  for (const phase of ["running", "holding", "stopping"]) {
    eq(isRunPhaseLive(phase), true, `premise: ${phase} is a live latch`);
    eq(runLatchEnds(phase, false, seq("running"), seq("complete")), true,
      `a '${phase}' latch survived the run's end`);
  }
});

// ================================================== 2. the own-flow rule is kept
test("the open flow's own run ending ends the latch on its own, as the slice decided it", () => {
  // live -> live in one write: a session switch. `liveRunEnded` does not call
  // that an end, so only the own-flow flag can.
  eq(liveRunEnded(seq("running"), seq("running")), false, "premise: live -> live is not an end");
  eq(runLatchEnds("running", true, seq("running"), seq("running")), true,
    "the own-flow end no longer ends the latch");
  eq(runLatchEnds("running", true, undefined, undefined), true,
    "the own-flow end depends on the sequence states, which it never did");
});

// ========================================================== 3. nothing else ends it
test("a write inside a live run leaves the latch alone", () => {
  for (const a of LIVE) {
    for (const b of LIVE) {
      eq(liveRunEnded(seq(a), seq(b)), false, `liveRunEnded(${a} -> ${b})`);
      eq(runLatchEnds("running", false, seq(a), seq(b)), false,
        `a write inside the run (${a} -> ${b}) ended the latch`);
    }
  }
});

test("the engine's first publish (idle -> running) leaves the latch flowsRun just set", () => {
  eq(runLatchEnds("running", false, seq("idle"), seq("running")), false,
    "the run starting ended the latch that announces it");
});

test("a terminal state with no live run before it leaves the latch (a reconnect's snapshot of last night)", () => {
  for (const was of [...ENDED, "nina_native" as const]) {
    for (const now of ENDED) {
      eq(runLatchEnds("running", false, seq(was), seq(now)), false,
        `a terminal write with no live run before it (${was} -> ${now}) ended a fresh latch`);
    }
  }
});

test("a write with nothing before it leaves the latch", () => {
  eq(runLatchEnds("running", false, undefined, seq("complete")), false, "undefined -> complete");
  eq(runLatchEnds("running", false, null, seq("idle")), false, "null -> idle");
  eq(runLatchEnds("running", false, seq("running"), undefined), true,
    "a run that vanished from the store (live -> nothing) is over");
});

// ================================================== 4. an idle latch is never rewritten
test("an idle latch is not rewritten by any write, however the run ended", () => {
  for (const was of LIVE) {
    for (const now of ENDED) {
      eq(runLatchEnds("idle", false, seq(was), seq(now)), false,
        `an already-idle latch was rewritten (${was} -> ${now}): the slice would write flows.run on every tick`);
    }
  }
  eq(runLatchEnds("idle", true, seq("running"), seq("complete")), false,
    "an idle latch was rewritten on an own-flow end");
});

const total = passed + failed;
console.log(`w16RunLatchEnds.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
