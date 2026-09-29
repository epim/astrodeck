// flowRunState.test.ts - the run-mode readers, graded on what the server
// really answers (#189 S5, #449, #451; spec 2.6 run mode, 5.8 holds, 5.9
// CONTINUE, 5.10 published state, 6.9 privacy).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/__tests__/flowRunState.test.ts   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE ANSWERS ARE TWO RECORDED FILES, READ, NOT COPIED:
//
//   server/tests/fixtures/flow_progress_continue.json
//     GET /api/flows/example-m31-mosaic/progress: the eighth Example (a 3x2
//     called "M31") with a dormant session of two runs.
//   server/tests/fixtures/sequence_state_mosaic.json
//     GET /api/sequence/state on the clocked simulator for a rotating 2x2,
//     also called "M31", whose 2-2 never centres and is set aside: a panel
//     being shot, and a meridian wait as an operator and as a viewer is
//     served it (the viewer's without `panel` and `pass`); and, from a
//     second night of the same plan (S7, #451), the run paused, the run
//     holding for cloud and the run winding down from an Abort pressed in
//     that hold, each with the group on 1-1 and 2-2 set aside.
//
// server/tests/test_s5_recorded_state.py rebuilds both and grades them byte
// for byte, so a server that stops answering this way turns that test red
// rather than leaving this one grading an answer nobody gives. The night
// runs under the progress file's session id, as a CONTINUE of it would, so
// "this flow's run is live" is graded on the files as recorded; the two
// blocks share a name and not a group id, which is what the group reader
// must tell apart.
//
// WHAT IS GUARDED
//
//   1. flowRunLive: a run is this flow's only when the session it writes is
//      one the slice knows as the flow's (#449: the progress answer's
//      session, as the slice notes it, graded here as a one-entry list, and
//      any other the list holds), and only while it is live (runIsLive's
//      states: a cloud hold and an abort's wind-down are the run too).
//      knownSessions: the list while a record is open, none while none is.
//   2. groupForBlock: the live group is a block's only by id, never by name,
//      and never once the run is over.
//   3. panelStateOf: the panel being shot, or set aside with its reason in
//      the engine's words; no panel is "being shot" across a meridian wait,
//      for an operator or a viewer alike; and the panel the group is on
//      while the run is paused, holding or stopping is CURRENT, with the
//      run's state, never shot (#451), worded by the state even when an
//      Abort in a hold still carries the hold's key (#513).
//
// Every mutant below was run in a private scratch copy of ui/ (scratchpad
// s5-feed-mut, from a byte backup of flowRunState.ts, restored and
// hash-compared after each run), and the failure it produced is quoted. The
// flowRunLive and knownSessions mutants were run again for #449, when the
// reader moved from the progress answer to the known sessions, in
// scratchpad S7-USLICE-mut the same way, and are quoted as that run saw them.
// The panelStateOf mutants were run again for #451, once the reader took the
// run's state, in scratchpad S7-URUNHOLD-mut the same way (2026-09-28), and
// are quoted as that run saw them, beside #451's own.

// @ts-ignore  no @types/node guaranteed; tsx supplies fs at runtime
import { readFileSync } from "node:fs";

import { flowRunLive, groupForBlock, knownSessions, panelStateOf } from "../flowRunState";
import type { FlowProgress } from "../../../lib/flowsApi";
import type { SequenceState } from "../../../types";

// ---------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];

function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function eq<T>(got: T, want: T, msg: string): void {
  const g = JSON.stringify(got);
  const w = JSON.stringify(want);
  if (g !== w) throw new Error(`${msg}\n    expected ${w}\n    got      ${g}`);
}
function assert(cond: unknown, msg: string): void {
  if (!cond) throw new Error(msg);
}

// ---------------------------------------------------------------- fixtures
// A missing or unreadable file FAILS the whole file, never skips it: a
// skipped fixture reads as a green run mode.
// MUTANT "fixture unreadable" (the state file's name pointed at
// sequence_state_mosaic.missing.json). Observed: the file stops before its
// first case with exit code 1, which run-tests.mjs scores as a failure:
//   Error: cannot read ../../../../../server/tests/fixtures/
//     sequence_state_mosaic.missing.json, a recorded answer these readers
//     are graded against: ENOENT: no such file or directory, open '...'
const FIXTURES = "../../../../../server/tests/fixtures/";

function readFixture(name: string): Record<string, unknown> {
  const rel = FIXTURES + name;
  let text: string;
  try {
    text = readFileSync(new URL(rel, import.meta.url), "utf8") as string;
  } catch (e) {
    throw new Error(`cannot read ${rel}, a recorded answer these readers are graded `
      + `against: ${(e as Error).message}`);
  }
  return JSON.parse(text) as Record<string, unknown>;
}

const PROGRESS = readFixture("flow_progress_continue.json").response as FlowProgress;
const STATES = readFixture("sequence_state_mosaic.json").states as Record<string, SequenceState>;
const SHOOTING = STATES.shooting;
const WAIT_OPERATOR = STATES.meridian_wait_operator;
const WAIT_VIEWER = STATES.meridian_wait_viewer;
const PAUSED = STATES.paused;
const HOLDING = STATES.holding;
const ABORTING = STATES.aborting;
if (!PROGRESS?.session || !SHOOTING?.group || !WAIT_OPERATOR?.group || !WAIT_VIEWER?.group
    || !PAUSED?.group || !HOLDING?.group || !ABORTING?.group) {
  throw new Error("the recorded fixtures do not hold the progress answer and the six states");
}
const BLOCK = PROGRESS.blocks[0];
const LIVE_GROUP_ID = SHOOTING.group!.id;
/** What the slice knows once the recorded answer lands (flowsSlice
 *  `fetchProgress` notes the answer's session into `sessionIds`). */
const KNOWN: readonly string[] = [PROGRESS.session!.id];

/** A recorded state with its run's session id replaced: another flow's run. */
function anotherFlowsRun(s: SequenceState): SequenceState {
  return { ...s, session: { ...s.session!, id: "another-flows-session" } };
}
function inState(s: SequenceState, state: SequenceState["state"]): SequenceState {
  return { ...s, state };
}

// ---------------------------------------------------------- the premises
test("premise: the two files are one session, and two blocks of one name", () => {
  eq(SHOOTING.session?.id, PROGRESS.session!.id,
    "the night does not run under the progress file's session");
  eq(PROGRESS.session!.status, "dormant", "the progress file's session status");
  eq(BLOCK.name, SHOOTING.group!.name, "the two blocks were meant to share a name");
  assert(typeof BLOCK.group_id === "string" && BLOCK.group_id !== LIVE_GROUP_ID,
    `the Example's block names its own group: ${BLOCK.group_id} vs ${LIVE_GROUP_ID}`);
  eq(WAIT_OPERATOR.group!.meridian_wait, true, "the operator's wait");
  assert(WAIT_OPERATOR.group!.panel === "2-1" && WAIT_OPERATOR.group!.pass === 17,
    `the operator is served the panel and the pass: ${JSON.stringify(WAIT_OPERATOR.group)}`);
  assert(!("panel" in WAIT_VIEWER.group!) && !("pass" in WAIT_VIEWER.group!),
    "the viewer is served neither the panel nor the pass");
});

test("premise: the held night's states are this flow's run, each with its group on 1-1", () => {
  for (const [kind, s] of [["paused", PAUSED], ["holding", HOLDING], ["aborting", ABORTING]] as const) {
    eq(s.state, kind, `the recorded ${kind} state`);
    eq(s.session?.id, PROGRESS.session!.id, `the ${kind} state's session`);
    eq(s.group!.id, LIVE_GROUP_ID, `the ${kind} state's group`);
    eq(s.group!.panel, "1-1", `the ${kind} state's panel`);
    eq(s.group!.meridian_wait, false, `the ${kind} state's meridian wait`);
    eq(s.group!.set_aside.map((a) => a.panel), ["2-2"], `the ${kind} state's set-aside panels`);
  }
  eq(HOLDING.hold, "clouds", "the hold's reason");
  eq(HOLDING.sky?.holding, true, "the hold's own flag");
  // #513, as the engine publishes it today: an Abort pressed in the hold
  // carries the hold's key into "aborting".
  eq(ABORTING.hold, "clouds", "the aborting state's leftover hold");
});

// ------------------------------------------------------------ flowRunLive
// Asked of the sessions the slice knows as the open flow's, never of the
// progress answer (#449): the answer is blanked by every save's re-read, and
// run mode read off it let go of a running flow for that round trip. KNOWN is
// the list the slice holds once the recorded answer has landed; the round
// trip itself is graded mounted, in runModeAcrossSave.test.tsx.
//
// MUTANT "live when any run is live" (flowRunLive answering runIsLive(sequence)
// alone, the list never asked). Observed, 11/15:
//   x flowRunLive: another flow's live run is not this flow's: another flow's run
//     expected false
//     got      true
//   x flowRunLive: no session known means no run of this flow: the flow never ran, or no answer has named its session
//     expected false
//     got      true
//   x flowRunLive: a session id that is empty matches nothing: an empty session id on both sides
//     expected false
//     got      true
//   x knownSessions: the open flow's list, and none while no flow is open: the join once the flow is closed
//     expected false
//     got      true
test("flowRunLive: the recorded run is the recorded flow's while it is live", () => {
  eq(flowRunLive(KNOWN, SHOOTING), true, "the panel being shot");
  eq(flowRunLive(KNOWN, WAIT_OPERATOR), true, "the operator's meridian wait");
  eq(flowRunLive(KNOWN, WAIT_VIEWER), true,
    "the viewer's meridian wait: liveness does not ride the withheld panel");
});

test("flowRunLive: another flow's live run is not this flow's", () => {
  eq(flowRunLive(KNOWN, anotherFlowsRun(SHOOTING)), false, "another flow's run");
});

// MUTANT "live whatever the state" (the runIsLive test dropped, the list
// alone asked). Observed, 13/15:
//   x flowRunLive: a run that has ended is not live, a hold and a wind-down are: complete
//     expected false
//     got      true
//   x flowRunLive: the same session on a state with no run: idle
//     expected false
//     got      true
test("flowRunLive: a run that has ended is not live, a hold and a wind-down are", () => {
  eq(flowRunLive(KNOWN, inState(SHOOTING, "complete")), false, "complete");
  eq(flowRunLive(KNOWN, inState(SHOOTING, "aborted")), false, "aborted");
  eq(flowRunLive(KNOWN, inState(SHOOTING, "error")), false, "error");
  eq(flowRunLive(KNOWN, inState(SHOOTING, "holding")), true, "a cloud hold is the run");
  eq(flowRunLive(KNOWN, inState(SHOOTING, "aborting")), true, "an abort's wind-down is the run");
  eq(flowRunLive(KNOWN, inState(SHOOTING, "paused")), true, "a pause is the run");
});

test("flowRunLive: the same session on a state with no run", () => {
  eq(flowRunLive(KNOWN, inState(SHOOTING, "idle")), false, "idle");
  eq(flowRunLive(KNOWN, undefined), false, "no sequence state at all");
});

test("flowRunLive: no session known means no run of this flow", () => {
  eq(flowRunLive([], SHOOTING), false, "the flow never ran, or no answer has named its session");
  eq(flowRunLive(null, SHOOTING), false, "no list at all");
});

// MUTANT "only the latest session" (flowRunLive comparing the run's session
// with the list's LAST entry alone, as a reader that kept "the session" would).
// Observed, 14/15:
//   x flowRunLive: any session the list knows is this flow's, not only the newest: the answer's session, with a newer one after it
//     expected true
//     got      false
test("flowRunLive: any session the list knows is this flow's, not only the newest", () => {
  // RUN named a fresh session after an answer named the dormant one (START
  // OVER): the list holds both, and a run of either is this flow's.
  eq(flowRunLive([PROGRESS.session!.id, "fresh-after-start-over"], SHOOTING), true,
    "the answer's session, with a newer one after it");
  eq(flowRunLive(["fresh-after-start-over", PROGRESS.session!.id], SHOOTING), true,
    "the answer's session last");
});

test("flowRunLive: a session id that is empty matches nothing", () => {
  const bare = { ...SHOOTING, session: { ...SHOOTING.session!, id: "" } };
  eq(flowRunLive([""], bare), false, "an empty session id on both sides");
  eq(flowRunLive(KNOWN, { ...SHOOTING, session: undefined }), false,
    "a live state that names no session");
});

// ---------------------------------------------------------- knownSessions
// MUTANT "a list without a flow" (knownSessions returning `sessionIds`
// whatever `record` says). Observed, 14/15:
//   x knownSessions: the open flow's list, and none while no flow is open: no flow open: a list left beside no record belongs to no flow
//     expected []
//     got      ["319caf668a27569db2239f9e8fa83396"]
test("knownSessions: the open flow's list, and none while no flow is open", () => {
  const rec = { id: PROGRESS.flow_id };
  eq(knownSessions({ record: rec, sessionIds: KNOWN }), KNOWN, "a flow open, one session known");
  assert(knownSessions({ record: rec, sessionIds: KNOWN }) === KNOWN,
    "the list is handed back as it is, so a selector over it keeps its identity");
  eq(knownSessions({ record: null, sessionIds: KNOWN }), [],
    "no flow open: a list left beside no record belongs to no flow");
  eq(knownSessions({ record: rec }), [], "a hand-built state with no list");
  eq(knownSessions(null), [], "no flows state at all");
  // The reader and the join: the recorded run is live for the flow that
  // knows its session, and not once the flow is closed.
  eq(flowRunLive(knownSessions({ record: rec, sessionIds: KNOWN }), SHOOTING), true,
    "the join while the flow is open");
  eq(flowRunLive(knownSessions({ record: null, sessionIds: KNOWN }), SHOOTING), false,
    "the join once the flow is closed");
});

// ---------------------------------------------------------- groupForBlock
// MUTANT "group matched by name" (groupForBlock comparing the group's NAME to
// the id it is handed, `group.name === groupId`). Observed, 10/13:
//   x groupForBlock: the live group is found by its id: the recorded group
//     expected {"id":"m31-mosaic","name":"M31","mode":"rotate","pass":3,"panel":"1-1",...}
//     got      null
//   x groupForBlock: a group is never a block's by name: the name as an id
//     expected null
//     got      {"id":"m31-mosaic","name":"M31","mode":"rotate","pass":3,"panel":"1-1",...}
//   x panelStateOf: a block reads the live panels only when the live group is its own: 1-1 through the block's own group
//     expected {"kind":"shooting"}
//     got      null
test("groupForBlock: the live group is found by its id", () => {
  eq(groupForBlock(SHOOTING, LIVE_GROUP_ID), SHOOTING.group!, "the recorded group");
  eq(groupForBlock(WAIT_VIEWER, LIVE_GROUP_ID), WAIT_VIEWER.group!,
    "the viewer's group, without its panel and pass");
});

test("groupForBlock: a group is never a block's by name", () => {
  eq(groupForBlock(SHOOTING, BLOCK.group_id), null,
    "the Example's block, also called M31, is not the live group's");
  eq(groupForBlock(SHOOTING, SHOOTING.group!.name), null, "the name as an id");
});

// MUTANT "a finished run's group" (groupForBlock without its runIsLive
// test). Observed, 12/13:
//   x groupForBlock: no group once the run is over, nor for a block with none: a complete run's last group
//     expected null
//     got      {"id":"m31-mosaic","name":"M31","mode":"rotate","pass":3,"panel":"1-1",...}
test("groupForBlock: no group once the run is over, nor for a block with none", () => {
  eq(groupForBlock(inState(SHOOTING, "complete"), LIVE_GROUP_ID), null,
    "a complete run's last group");
  eq(groupForBlock(SHOOTING, null), null, "a block whose plan has no group");
  eq(groupForBlock(SHOOTING, undefined), null, "a single target's block");
  eq(groupForBlock({ ...SHOOTING, group: undefined }, LIVE_GROUP_ID), null,
    "a live run on a target in no group");
});

// ----------------------------------------------------------- panelStateOf
// Asked with the run's state since #451, as the sheet's reader asks it once
// it passes one. The three S5 mutants below were run again against these
// cases (S7-URUNHOLD-mut); the dash in the engine's reason is elided as "...".
//
// MUTANT "set aside without its reason" (panelStateOf answering the set-aside
// kind with an empty reason). Observed, 18/21:
//   x panelStateOf: the panel being shot, and the one set aside with the engine's reason: 2-2 while 1-1 is shot
//     expected {"kind":"set_aside","reason":"centring failed on 2-2 on 3 consecutive visits: plate solve failed ... used raw GoTo"}
//     got      {"kind":"set_aside","reason":""}
//   x panelStateOf: across a meridian wait no panel is being shot, for an operator or a viewer: 2-2 stays set aside for the operator
//     (the same two lines)
//   x panelStateOf: the held states keep the set-aside panel and its reason, and the join is the flow's: 2-2 while paused
//     (the same two lines)
// MUTANT "never shooting" (panelStateOf answering null for every panel that
// is not set aside). Observed, 15/21, the first two lines of six (the other
// four are the held cases below, and the transitional one, each got null):
//   x panelStateOf: the panel being shot, and the one set aside with the engine's reason: 1-1 is being shot
//     expected {"kind":"shooting"}
//     got      null
//   x panelStateOf: a block reads the live panels only when the live group is its own: 1-1 through the block's own group
//     expected {"kind":"shooting"}
//     got      null
test("panelStateOf: the panel being shot, and the one set aside with the engine's reason", () => {
  const g = SHOOTING.group!;
  const reason = g.set_aside[0].reason;
  assert(reason.startsWith("centring failed on 2-2"), `premise: the engine's reason: ${reason}`);
  eq(panelStateOf("1-1", g, SHOOTING), { kind: "shooting" }, "1-1 is being shot");
  eq(panelStateOf("2-2", g, SHOOTING), { kind: "set_aside", reason }, "2-2 while 1-1 is shot");
  eq(panelStateOf("1-2", g, SHOOTING), null, "1-2 waits its turn");
  eq(panelStateOf("2-1", g, SHOOTING), null, "2-1 waits its turn");
});

// MUTANT "shooting through a meridian wait" (the meridian_wait test dropped:
// the panel the group last named reads as being shot). Observed, 20/21; the
// viewer's half passes, since a viewer is never served the panel, which is
// why the operator's recorded state is in the file:
//   x panelStateOf: across a meridian wait no panel is being shot, for an operator or a viewer: 2-1, the operator's stale panel
//     expected null
//     got      {"kind":"shooting"}
test("panelStateOf: across a meridian wait no panel is being shot, for an operator or a viewer", () => {
  for (const [who, s] of [["operator", WAIT_OPERATOR], ["viewer", WAIT_VIEWER]] as const) {
    const g = s.group!;
    const reason = g.set_aside[0].reason;
    eq(panelStateOf("2-1", g, s), null,
      who === "operator" ? "2-1, the operator's stale panel" : "2-1, for a viewer");
    eq(panelStateOf("2-2", g, s), { kind: "set_aside", reason }, `2-2 stays set aside for the ${who}`);
    for (const label of ["1-1", "1-2"]) eq(panelStateOf(label, g, s), null, `${label} for the ${who}`);
  }
});

test("panelStateOf: a block reads the live panels only when the live group is its own", () => {
  // The Example's block against the recorded night: the live group is the
  // other M31's, so none of the Example's panels has a run state.
  const other = groupForBlock(SHOOTING, BLOCK.group_id);
  for (const p of BLOCK.panels) {
    eq(panelStateOf(`${p.row! + 1}-${p.col! + 1}`, other, SHOOTING), null, `the Example's ${p.name}`);
  }
  // The same block, were the live group its own: the join the sheet makes.
  const own = groupForBlock(SHOOTING, LIVE_GROUP_ID);
  eq(panelStateOf("1-1", own, SHOOTING), { kind: "shooting" }, "1-1 through the block's own group");
  eq(panelStateOf("2-2", own, SHOOTING)?.kind, "set_aside", "2-2 through the block's own group");
  eq(panelStateOf("1-1", null, SHOOTING), null, "no group, no state");
});

// ------------------------------------------- paused, holding, stopping (#451)
// The engine publishes the group through a pause, a cloud hold and an Abort's
// wind-down, each still the run (flowRunLive above), and the panel it names
// is the visit's. None of the three is exposing it.
//
// MUTANT "state check dropped" (panelStateOf answering `shooting` for the
// group's panel whatever the run's state, `run` read by nothing). Observed,
// 17/21 (and framingSections.test.tsx's PANELS case red on the recorded
// pause, "1-1: shooting now"):
//   x panelStateOf: while the run is paused, holding for cloud or stopping, its panel is current, never shot: 1-1 in the recorded cloud hold
//     expected {"kind":"current","run":"holding","hold":"clouds"}
//     got      {"kind":"shooting"}
//   x panelStateOf: an Abort pressed in a cloud hold is worded by its state, not by the hold it still carries: 1-1 while the run stops, the hold's key still on it
//     expected {"kind":"current","run":"aborting","hold":null}
//     got      {"kind":"shooting"}
//   x panelStateOf: a run that is not live shoots nothing; a run nobody knows shoots nothing: 1-1 once the run is complete
//     expected null
//     got      {"kind":"shooting"}
//   x panelStateOf: the held states keep the set-aside panel and its reason, and the join is the flow's: 1-1 through groupForBlock in the hold
//     expected {"kind":"current","run":"holding","hold":"clouds"}
//     got      {"kind":"shooting"}
test("panelStateOf: while the run is paused, holding for cloud or stopping, its panel is current, never shot", () => {
  eq(panelStateOf("1-1", HOLDING.group!, HOLDING), { kind: "current", run: "holding", hold: "clouds" },
    "1-1 in the recorded cloud hold");
  eq(panelStateOf("1-1", PAUSED.group!, PAUSED), { kind: "current", run: "paused", hold: null },
    "1-1 in the recorded pause");
  for (const s of [PAUSED, HOLDING, ABORTING]) {
    for (const label of ["1-2", "2-1"]) eq(panelStateOf(label, s.group!, s), null, `${label} while ${s.state}`);
  }
  // CONTROL: the same group, the run shooting, is shooting its panel.
  eq(panelStateOf("1-1", HOLDING.group!, inState(HOLDING, "running")), { kind: "shooting" },
    "1-1 in the hold's group once the run is running again");
});

// MUTANT "worded by the hold" (panelStateOf asking `run.hold` before the
// state: any state carrying a hold reads as holding). Observed, 19/21, the
// first line being the control above, whose state still carries the hold's
// key once it is made "running":
//   x panelStateOf: while the run is paused, holding for cloud or stopping, its panel is current, never shot: 1-1 in the hold's group once the run is running again
//     expected {"kind":"shooting"}
//     got      {"kind":"current","run":"holding","hold":"clouds"}
//   x panelStateOf: an Abort pressed in a cloud hold is worded by its state, not by the hold it still carries: 1-1 while the run stops, the hold's key still on it
//     expected {"kind":"current","run":"aborting","hold":null}
//     got      {"kind":"current","run":"holding","hold":"clouds"}
test("panelStateOf: an Abort pressed in a cloud hold is worded by its state, not by the hold it still carries", () => {
  assert(ABORTING.hold === "clouds", "premise: the recorded abort still carries the hold's key (#513)");
  eq(panelStateOf("1-1", ABORTING.group!, ABORTING), { kind: "current", run: "aborting", hold: null },
    "1-1 while the run stops, the hold's key still on it");
  // A hold whose reason is not one the words know is still a hold.
  eq(panelStateOf("1-1", HOLDING.group!, { ...HOLDING, hold: undefined }),
    { kind: "current", run: "holding", hold: null }, "1-1 in a hold that names no reason");
});

// MUTANT "any other state shoots" (panelStateOf's last line answering
// `shooting` in place of null, so only the three held states read as not
// shot). Observed, 20/21:
//   x panelStateOf: a run that is not live shoots nothing; a run nobody knows shoots nothing: 1-1 once the run is complete
//     expected null
//     got      {"kind":"shooting"}
test("panelStateOf: a run that is not live shoots nothing; a run nobody knows shoots nothing", () => {
  const g = SHOOTING.group!;
  for (const state of ["complete", "aborted", "error", "idle"] as const) {
    eq(panelStateOf("1-1", g, inState(SHOOTING, state)), null, `1-1 once the run is ${state}`);
  }
  eq(panelStateOf("1-1", g, null), null, "1-1 with no run state known");
  // The set-aside panel stays set aside whatever the run is doing.
  eq(panelStateOf("2-2", g, null)?.kind, "set_aside", "2-2 with no run state known");
});

test("panelStateOf: the held states keep the set-aside panel and its reason, and the join is the flow's", () => {
  for (const s of [PAUSED, HOLDING, ABORTING]) {
    const reason = s.group!.set_aside[0].reason;
    eq(panelStateOf("2-2", s.group!, s), { kind: "set_aside", reason }, `2-2 while ${s.state}`);
    eq(flowRunLive(KNOWN, s), true, `the ${s.state} run is this flow's live run`);
  }
  // The join the sheet makes: the block's group from the hold, and its panel.
  eq(panelStateOf("1-1", groupForBlock(HOLDING, LIVE_GROUP_ID), HOLDING),
    { kind: "current", run: "holding", hold: "clouds" }, "1-1 through groupForBlock in the hold");
});

// The TRANSITIONAL case that stood here pinned S5's `shooting` for a call
// with two arguments, which kept framingModel `runPanelsOf` drawing a held
// run's panel as shot (#451). The S7 integration made `run` required there
// and here, so a caller that leaves it out no longer compiles, and the case
// that held the old answer went with it.

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`flowRunState.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) (globalThis as any).process.exitCode = 1;
export const result = { passed, failed, total };
export default result;
