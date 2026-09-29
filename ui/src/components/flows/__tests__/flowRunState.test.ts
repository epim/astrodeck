// flowRunState.test.ts - the run-mode readers, graded on what the server
// really answers (#189 S5; spec 2.6 run mode, 5.9 CONTINUE, 5.10 published
// state, 6.9 privacy).
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
//     served it (the viewer's without `panel` and `pass`).
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
//      the one the progress route counts, and only while it is live
//      (runIsLive's states: a cloud hold and an abort's wind-down are the
//      run too).
//   2. groupForBlock: the live group is a block's only by id, never by name,
//      and never once the run is over.
//   3. panelStateOf: the panel being shot, or set aside with its reason in
//      the engine's words; no panel is "being shot" across a meridian wait,
//      for an operator or a viewer alike.
//
// Every mutant below was run in a private scratch copy of ui/ (scratchpad
// s5-feed-mut, from a byte backup of flowRunState.ts, restored and
// hash-compared after each run), and the failure it produced is quoted.

// @ts-ignore  no @types/node guaranteed; tsx supplies fs at runtime
import { readFileSync } from "node:fs";

import { flowRunLive, groupForBlock, panelStateOf } from "../flowRunState";
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
if (!PROGRESS?.session || !SHOOTING?.group || !WAIT_OPERATOR?.group || !WAIT_VIEWER?.group) {
  throw new Error("the recorded fixtures do not hold the progress answer and the three states");
}
const BLOCK = PROGRESS.blocks[0];
const LIVE_GROUP_ID = SHOOTING.group!.id;

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

// ------------------------------------------------------------ flowRunLive
// MUTANT "live when any run is live" (flowRunLive answering runIsLive(sequence)
// alone, the session ids never compared). Observed, 10/13:
//   x flowRunLive: another flow's live run is not this flow's: another flow's run
//     expected false
//     got      true
//   x flowRunLive: no session in the progress answer means no run of this flow: the flow never ran
//     expected false
//     got      true
//   x flowRunLive: a session id that is empty matches nothing: an empty session id on both sides
//     expected false
//     got      true
test("flowRunLive: the recorded run is the recorded flow's while it is live", () => {
  eq(flowRunLive(PROGRESS, SHOOTING), true, "the panel being shot");
  eq(flowRunLive(PROGRESS, WAIT_OPERATOR), true, "the operator's meridian wait");
  eq(flowRunLive(PROGRESS, WAIT_VIEWER), true,
    "the viewer's meridian wait: liveness does not ride the withheld panel");
});

test("flowRunLive: another flow's live run is not this flow's", () => {
  eq(flowRunLive(PROGRESS, anotherFlowsRun(SHOOTING)), false, "another flow's run");
});

// MUTANT "live whatever the state" (the runIsLive test dropped, the ids alone
// compared). Observed, 11/13:
//   x flowRunLive: a run that has ended is not live, a hold and a wind-down are: complete
//     expected false
//     got      true
//   x flowRunLive: the same session on a state with no run: idle
//     expected false
//     got      true
test("flowRunLive: a run that has ended is not live, a hold and a wind-down are", () => {
  eq(flowRunLive(PROGRESS, inState(SHOOTING, "complete")), false, "complete");
  eq(flowRunLive(PROGRESS, inState(SHOOTING, "aborted")), false, "aborted");
  eq(flowRunLive(PROGRESS, inState(SHOOTING, "error")), false, "error");
  eq(flowRunLive(PROGRESS, inState(SHOOTING, "holding")), true, "a cloud hold is the run");
  eq(flowRunLive(PROGRESS, inState(SHOOTING, "aborting")), true, "an abort's wind-down is the run");
  eq(flowRunLive(PROGRESS, inState(SHOOTING, "paused")), true, "a pause is the run");
});

test("flowRunLive: the same session on a state with no run", () => {
  eq(flowRunLive(PROGRESS, inState(SHOOTING, "idle")), false, "idle");
  eq(flowRunLive(PROGRESS, undefined), false, "no sequence state at all");
});

test("flowRunLive: no session in the progress answer means no run of this flow", () => {
  eq(flowRunLive({ ...PROGRESS, session: null }, SHOOTING), false, "the flow never ran");
  eq(flowRunLive(null, SHOOTING), false, "no progress answer yet");
});

test("flowRunLive: a session id that is empty matches nothing", () => {
  const empty = { ...PROGRESS, session: { ...PROGRESS.session!, id: "" } };
  const bare = { ...SHOOTING, session: { ...SHOOTING.session!, id: "" } };
  eq(flowRunLive(empty, bare), false, "an empty session id on both sides");
  eq(flowRunLive(PROGRESS, { ...SHOOTING, session: undefined }), false,
    "a live state that names no session");
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
// MUTANT "set aside without its reason" (panelStateOf answering the set-aside
// kind with an empty reason). Observed, 11/13 (the reason's dash printed as
// the console's replacement character; elided here):
//   x panelStateOf: the panel being shot, and the one set aside with the engine's reason: 2-2 while 1-1 is shot
//     expected {"kind":"set_aside","reason":"centring failed on 2-2 on 3 consecutive visits: plate solve failed ... used raw GoTo"}
//     got      {"kind":"set_aside","reason":""}
//   x panelStateOf: across a meridian wait no panel is being shot, for an operator or a viewer: 2-2 stays set aside for the operator
//     (the same two lines)
// MUTANT "never shooting" (panelStateOf answering null for every panel that
// is not set aside). Observed, 11/13:
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
  eq(panelStateOf("1-1", g), { kind: "shooting" }, "1-1 is being shot");
  eq(panelStateOf("2-2", g), { kind: "set_aside", reason }, "2-2 while 1-1 is shot");
  eq(panelStateOf("1-2", g), null, "1-2 waits its turn");
  eq(panelStateOf("2-1", g), null, "2-1 waits its turn");
});

// MUTANT "shooting through a meridian wait" (the meridian_wait test dropped:
// the panel the group last named reads as being shot). Observed, 12/13; the
// viewer's half passes, since a viewer is never served the panel, which is
// why the operator's recorded state is in the file:
//   x panelStateOf: across a meridian wait no panel is being shot, for an operator or a viewer: 2-1, the operator's stale panel
//     expected null
//     got      {"kind":"shooting"}
test("panelStateOf: across a meridian wait no panel is being shot, for an operator or a viewer", () => {
  for (const [who, s] of [["operator", WAIT_OPERATOR], ["viewer", WAIT_VIEWER]] as const) {
    const g = s.group!;
    const reason = g.set_aside[0].reason;
    eq(panelStateOf("2-1", g), null,
      who === "operator" ? "2-1, the operator's stale panel" : "2-1, for a viewer");
    eq(panelStateOf("2-2", g), { kind: "set_aside", reason }, `2-2 stays set aside for the ${who}`);
    for (const label of ["1-1", "1-2"]) eq(panelStateOf(label, g), null, `${label} for the ${who}`);
  }
});

test("panelStateOf: a block reads the live panels only when the live group is its own", () => {
  // The Example's block against the recorded night: the live group is the
  // other M31's, so none of the Example's panels has a run state.
  const other = groupForBlock(SHOOTING, BLOCK.group_id);
  for (const p of BLOCK.panels) {
    eq(panelStateOf(`${p.row! + 1}-${p.col! + 1}`, other), null, `the Example's ${p.name}`);
  }
  // The same block, were the live group its own: the join the sheet makes.
  const own = groupForBlock(SHOOTING, LIVE_GROUP_ID);
  eq(panelStateOf("1-1", own), { kind: "shooting" }, "1-1 through the block's own group");
  eq(panelStateOf("2-2", own)?.kind, "set_aside", "2-2 through the block's own group");
  eq(panelStateOf("1-1", null), null, "no group, no state");
});

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`flowRunState.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) (globalThis as any).process.exitCode = 1;
export const result = { passed, failed, total };
export default result;
