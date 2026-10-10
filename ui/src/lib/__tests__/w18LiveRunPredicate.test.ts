// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w18LiveRunPredicate.test.ts - the ONE is-live predicate the rig-teardown
// confirms and run locks share, pinned over the whole `SequenceState["state"]`
// union (#821, WP-158, wave 18).
//
//   Run directly:  npx tsx src/lib/__tests__/w18LiveRunPredicate.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// `runIsLive` (lib/lastSessionFrame.ts) is the predicate `types.ts` points every
// is-live check at: the rig's `engine.running` is true for "running", "paused",
// "holding" (a cloud hold) and "aborting" (an abort's wind-down), and for no
// other state. #821 found five sites that each wrote `state === "running" ||
// state === "paused"` instead. Each site now has its own mounted test
// (`w18RigConnectLiveRun`, `w18ProfilesEditorLiveRun`, `w18ProfileListLiveRun`,
// `w18EquipmentViewLiveRun`, `w18QuickActionsLiveRun`); this file holds the
// table they share, so a change to the predicate is judged against the whole
// union in one place.
//
// (A sixth site, `DevicesScreen`'s `RosterInput.sequenceRunning`, had no reader
// at all, so no behavioural test could grade it and this file carried a
// text-reading case for it. #923 removed the dead field and that case with it.)
//
// MUTANT "running or paused only" (lib/lastSessionFrame.ts `runIsLive` made
// `return s === "running" || s === "paused";`). Run from a byte backup,
// restored byte-identically (md5sum compared): see the report for the failing
// lines.

import { runIsLive } from "../lastSessionFrame";
import type { SequenceState } from "../../types";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

type State = SequenceState["state"];
/** The whole union. The `Record` forces a new member to be classified here: a
 *  state added to `SequenceState` without a row fails `tsc -b`. */
const IS_LIVE: Record<State, boolean> = {
  running: true,
  paused: true,
  holding: true,
  aborting: true,
  idle: false,
  complete: false,
  aborted: false,
  error: false,
  nina_native: false,
};

for (const [state, live] of Object.entries(IS_LIVE) as Array<[State, boolean]>) {
  test(`${state} is ${live ? "" : "not "}a live run`, () => {
    assert(runIsLive({ state }) === live,
      `runIsLive({ state: "${state}" }) is ${!live}: the rig's engine.running is ${live} in it`);
  });
}

test("no sequence at all is not a live run", () => {
  assert(!runIsLive(null), "null");
  assert(!runIsLive(undefined), "undefined");
});

console.log(`w18LiveRunPredicate: ${passed}/${passed + failed} passed`);
for (const f of failures) console.log(f);
export const result = { passed, failed, total: passed + failed };
if (failed) process.exitCode = 1;
