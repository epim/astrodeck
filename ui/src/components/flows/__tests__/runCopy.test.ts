// runCopy.test.ts - the RUN button's copy and the run readouts, graded on what
// the server really answers (#189 S5; spec 5.9 button copy, 5.10 published
// state and ETA, U-07).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/__tests__/runCopy.test.ts   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE ANSWERS ARE THE TWO RECORDED FILES flowRunState.test.ts reads, read here
// too, never copied:
//
//   server/tests/fixtures/flow_progress_continue.json
//     GET /api/flows/example-m31-mosaic/progress: the eighth Example (a 3x2
//     called "M31") with a dormant, armed session of two runs on two nights,
//     194 of 480 subs banked. Since S7 the route's `nights` counts OBSERVING
//     nights (#430, S7 orchestrator ruling 7), so the recorded 2 is two
//     nights. server/tests/test_s5_recorded_state.py also presses CONTINUE on
//     it, on an evening the session has not run, and checks the run route's
//     night is the route's `nights` plus one: the ROUTE'S RECORDED ANSWER for
//     the night this copy prints, 3.
//   server/tests/fixtures/sequence_state_mosaic.json
//     GET /api/sequence/state for a rotating 2x2, also called "M31", run under
//     that session's id: a panel being shot with the hops not yet costed, and
//     a meridian wait as an operator and as a viewer are served it.
//
// WHAT IS GUARDED
//
//   1. runCopy: CONTINUE <FLOW NAME> (night <nights + 1>, <banked>/<total>
//      subs) over a dormant session, the sums over the BLOCKS; RUN over no
//      session, an abandoned one (the route answers null), a complete one and
//      an active one that is not live here; STOP while live.
//   2. startOverBody: START OVER's confirm says what the press leaves, with
//      the numbers on the CONTINUE beside it.
//   3. runReadouts: fed from the sequence state only while the run is this
//      flow's, with "hops not yet costed" while `hops_costed` is false, the
//      mosaic's stage as "M31 1-1 · pass 3", and across a meridian wait the
//      same words for an operator and a viewer with nothing site-derived;
//      otherwise the idle values. Asked with the known sessions, as every
//      store reader asks (#449); since S7 it takes no progress answer.
//   4. THE NIGHT READS THE OBSERVING-NIGHT COUNT (#430, S7 orchestrator
//      ruling 7): the copy's night is the route's recorded `nights` plus one,
//      compared with the literal the run route answered (3), and runCopy.ts
//      and flowsApi.ts say it is the observing-night count, not the run
//      count.
//   5. runCopy.ts's NOTHING SITE-DERIVED paragraph names the ETA's flip term,
//      #166 item 1 (#510, B16).
//
// Every S5 mutant below was run in a private scratch copy of ui/
// (scratchpad/S5-RUNUI-mut, #254), from a byte backup of runCopy.ts restored
// and hash-compared after each run, and the failure it produced is quoted.
// The S7 ones (sections 4 and 5, and the re-runs marked S7) were run the
// same way in scratchpad S7-URUN-mut.

// @ts-ignore  no @types/node guaranteed; tsx supplies fs at runtime
import { readFileSync } from "node:fs";

import {
  HOPS_NOT_COSTED, MERIDIAN_WAIT_STAGE, runCopy, runReadouts, runStage, startOverBody,
  type RunIdle,
} from "../runCopy";
import type { FlowProgress, FlowProgressBlock } from "../../../lib/flowsApi";
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
// skipped fixture reads as a green copy.
const FIXTURES = "../../../../../server/tests/fixtures/";

function readFixture(name: string): Record<string, unknown> {
  const rel = FIXTURES + name;
  let text: string;
  try {
    text = readFileSync(new URL(rel, import.meta.url), "utf8") as string;
  } catch (e) {
    throw new Error(`cannot read ${rel}, a recorded answer this copy is graded `
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

/** The flow's name as the library stores the eighth Example: the name the
 *  sequence state's session carries, because a flow run's plan is named for
 *  its flow. Read off the recorded state rather than typed here. */
const FLOW_NAME = SHOOTING.session!.name;

/** The route's own numbers, summed here the way the spec says: over the
 *  blocks, nothing else. */
const NIGHT = PROGRESS.session!.nights + 1;
const BANKED = PROGRESS.blocks.reduce((n, b) => n + b.banked, 0);
const TOTAL = PROGRESS.blocks.reduce((n, b) => n + b.total, 0);

/** The sessions the slice knows as the flow's once this answer has landed
 *  (flowsSlice `fetchProgress` notes the answer's session in the same
 *  write), which is what `useFlowRunReadouts` hands `runReadouts`. */
const KNOWN: readonly string[] = [PROGRESS.session!.id];

/** What `flows.run` holds at boot (flowsSlice's initial value). */
const IDLE: RunIdle = { phase: "idle", etaS: null, curStage: "—", frames: 0, frameGoal: null };

function withSession(p: FlowProgress, patch: Record<string, unknown> | null): FlowProgress {
  return { ...p, session: patch === null ? null : { ...p.session!, ...patch } as FlowProgress["session"] };
}
function anotherFlowsRun(s: SequenceState): SequenceState {
  return { ...s, session: { ...s.session!, id: "another-flows-session" } };
}
function withProgress(s: SequenceState, patch: Record<string, unknown>): SequenceState {
  return { ...s, progress: { ...s.progress!, ...patch } as SequenceState["progress"] };
}

// ------------------------------------------------------------ the premises
test("premise: the recorded answer is the spec's CONTINUE case", () => {
  eq(FLOW_NAME, "M31 mosaic", "the flow's name the recorded run carries");
  eq(PROGRESS.session!.status, "dormant", "the session Run would continue");
  eq([NIGHT, BANKED, TOTAL], [3, 194, 480], "night, banked and total as recorded");
  // The panels hold the same subs as their block: summing both counts each
  // sub twice, which is why the copy must sum the blocks alone.
  const block = PROGRESS.blocks[0];
  eq(block.panels.reduce((n, p) => n + p.banked, 0), block.banked,
    "the block's banked is its panels' banked");
});

// ---------------------------------------------------------------- runCopy
// MUTANT "night = nights" (runCopy's `session.nights + 1` made
// `session.nights`). Observed, runCopy.test 8/12:
//   x runCopy: CONTINUE over a dormant session, computed from the progress route alone: the parenthetical, from the route's numbers
//     expected "(night 3, 194/480 subs)"
//     got      "(night 2, 194/480 subs)"
//   x runCopy: the sums are over the blocks, and only the blocks: two blocks
//     expected "(night 3, 234/720 subs)"
//     got      "(night 2, 234/720 subs)"
//   x runCopy: numbers that do not read as numbers are not put on the button: a flow with no name yet leaves the name out rather than print a gap
//     expected "CONTINUE (night 3, 194/480 subs)"
//     got      "CONTINUE (night 2, 194/480 subs)"
//   x startOverBody: START OVER says what it leaves, with CONTINUE's numbers: the numbers it walks away from
//     expected [3,194,480]
//     got      [2,194,480]
// MUTANT "banked summed over the panels twice" (runCopy's loop also adding
// every panel's banked to the block's). Observed, runCopy.test 8/12:
//   x runCopy: CONTINUE over a dormant session, computed from the progress route alone: the parenthetical, from the route's numbers
//     expected "(night 3, 194/480 subs)"
//     got      "(night 3, 388/480 subs)"
//   x runCopy: the sums are over the blocks, and only the blocks: two blocks
//     expected "(night 3, 234/720 subs)"
//     got      "(night 3, 468/720 subs)"
//   x runCopy: numbers that do not read as numbers are not put on the button: a flow with no name yet leaves the name out rather than print a gap
//     expected "CONTINUE (night 3, 194/480 subs)"
//     got      "CONTINUE (night 3, 388/480 subs)"
//   x startOverBody: START OVER says what it leaves, with CONTINUE's numbers: the numbers it walks away from
//     expected [3,194,480]
//     got      [3,388,480]
// (The third named copy mutant, "copy from the graph", lives in the hook that
// feeds this function, so it is graded mounted: phoneReadouts.test.tsx and
// flowsDom.test.tsx quote it. This file stays 12/12 under it.)
test("runCopy: CONTINUE over a dormant session, computed from the progress route alone", () => {
  const c = runCopy(FLOW_NAME, PROGRESS, false);
  eq(c.verb, "CONTINUE", "the verb");
  eq(c.name, "M31 MOSAIC", "the flow's name, in the button's capitals");
  eq(c.detail, `(night ${NIGHT}, ${BANKED}/${TOTAL} subs)`, "the parenthetical, from the route's numbers");
  eq(c.text, "CONTINUE M31 MOSAIC (night 3, 194/480 subs)", "the whole line");
  eq([c.night, c.banked, c.total], [NIGHT, BANKED, TOTAL], "the numbers it is made of");
});

test("runCopy: the sums are over the blocks, and only the blocks", () => {
  // A second block, a POOL of 40/240, and the things that are in no block's
  // sums: a skipped panel's frames and the orphaned ones. The route puts
  // neither in `banked`, because neither fills a quota (flowsApi.ts).
  const pool: FlowProgressBlock = {
    node_id: "n9", name: "tonight's best", kind: "pool", banked: 40, owed: 200, total: 240,
    panels: [
      { target_id: "a", name: "NGC 7331", row: null, col: null, banked: 25, owed: 95, total: 120, steps: [] },
      { target_id: "b", name: "NGC 891", row: null, col: null, banked: 15, owed: 105, total: 120, steps: [] },
    ],
  };
  const skipped = {
    ...PROGRESS.blocks[0],
    skipped: [{ target_id: "s", name: "M31 2-1", row: 1, col: 0, banked: 33 }],
  };
  const two: FlowProgress = {
    ...PROGRESS, blocks: [skipped, pool], orphaned: { frames: 57, steps: 3 },
  };
  eq(runCopy(FLOW_NAME, two, false).detail, `(night ${NIGHT}, ${BANKED + 40}/${TOTAL + 240} subs)`,
    "two blocks");
  eq(runCopy(FLOW_NAME, { ...PROGRESS, blocks: [] }, false).detail, `(night ${NIGHT}, 0/0 subs)`,
    "a flow whose saved graph holds no block");
});

// MUTANT "CONTINUE over any session" (runCopy's `session.status !==
// "dormant"` test dropped). Observed, runCopy.test 11/12:
//   x runCopy: RUN with no session, an abandoned one, a complete one or an active one not live here: a complete session starts fresh (I-30)
//     expected "RUN"
//     got      "CONTINUE M31 MOSAIC (night 3, 194/480 subs)"
test("runCopy: RUN with no session, an abandoned one, a complete one or an active one not live here", () => {
  // CONTROLS: `run_flow` continues ONLY a dormant session; each of these
  // starts fresh, and the button must not promise a continue.
  eq(runCopy(FLOW_NAME, null, false).text, "RUN", "no progress answer yet (or a failed read)");
  eq(runCopy(FLOW_NAME, withSession(PROGRESS, null), false).text, "RUN",
    "no session: never run, or the newest was abandoned (the route answers null for both)");
  eq(runCopy(FLOW_NAME, withSession(PROGRESS, { status: "complete" }), false).text, "RUN",
    "a complete session starts fresh (I-30)");
  eq(runCopy(FLOW_NAME, withSession(PROGRESS, { status: "active" }), false).text, "RUN",
    "an active session the rig is not running here starts fresh too");
  const run = runCopy(FLOW_NAME, null, false);
  eq([run.name, run.detail, run.night], ["", "", null], "RUN carries no name and no numbers");
});

// MUTANT "STOP forgotten" (runCopy's `if (live) return plain("STOP")`
// removed). Observed, runCopy.test 11/12:
//   x runCopy: STOP while live, whatever the route says: a live run over the dormant answer
//     expected "STOP"
//     got      "CONTINUE M31 MOSAIC (night 3, 194/480 subs)"
test("runCopy: STOP while live, whatever the route says", () => {
  eq(runCopy(FLOW_NAME, PROGRESS, true).text, "STOP", "a live run over the dormant answer");
  eq(runCopy(FLOW_NAME, null, true).text, "STOP", "a live run before any answer");
});

test("runCopy: numbers that do not read as numbers are not put on the button", () => {
  const c = runCopy(FLOW_NAME, withSession(PROGRESS, { nights: Number.NaN }), false);
  eq(c.text, "CONTINUE M31 MOSAIC", "the press still continues, so the verb stays");
  const bad: FlowProgress = { ...PROGRESS, blocks: [{ ...PROGRESS.blocks[0], total: "480" as never }] };
  eq(runCopy(FLOW_NAME, bad, false).detail, "", "a total that is not a number");
  eq(runCopy("  ", PROGRESS, false).text, "CONTINUE (night 3, 194/480 subs)",
    "a flow with no name yet leaves the name out rather than print a gap");
});

test("startOverBody: START OVER says what it leaves, with CONTINUE's numbers", () => {
  const c = runCopy(FLOW_NAME, PROGRESS, false);
  eq([c.night, c.banked, c.total], [3, 194, 480], "the numbers it walks away from");
  const body = startOverBody(c);
  assert(body.includes("new session") && body.includes("leaves this one on disk"),
    `the body says a new session starts and this one stays on disk: ${body}`);
  assert(body.includes("CONTINUE will not go back to it"),
    `and that CONTINUE never reopens it (spec 5.9): ${body}`);
  assert(body.includes("194 of 480 subs"), `and what this one holds: ${body}`);
  assert(!/\d+ of \d+/.test(startOverBody(runCopy(FLOW_NAME, withSession(PROGRESS, { nights: Number.NaN }), false))),
    "no numbers the button did not have");
});

// ------------------------------------------------------------- runReadouts
// MUTANT "hops_costed ignored" (runReadouts' etaNote always null). Observed,
// runCopy.test 10/12:
//   x runReadouts: a panel being shot, fed from the sequence state: the ETA's note while hops_costed is false
//     expected "hops not yet costed"
//     got      null
//   x runReadouts: across a meridian wait the stage names the mosaic and the wait, for an operator and a viewer alike: the rest of the wait's readouts
//     expected ["RUNNING",45,120,"hops not yet costed"]
//     got      ["RUNNING",45,120,null]
test("runReadouts: a panel being shot, fed from the sequence state", () => {
  const r = runReadouts(KNOWN, SHOOTING, IDLE);
  eq(r.fed, true, "the run is this flow's");
  eq(r.state, "RUNNING", "STATE, from the engine's state");
  eq(r.etaS, SHOOTING.progress!.eta_s, "ETA, the rig's own number");
  eq(SHOOTING.progress!.hops_costed, false, "premise: the recorded clock has not costed a hop");
  eq(r.etaNote, HOPS_NOT_COSTED, "the ETA's note while hops_costed is false");
  eq(r.stage, "M31 1-1 · pass 3", "STAGE, the mosaic, its panel and its pass");
  eq([r.frames, r.frameGoal], [6, 120], "FRAMES, from the sequence state");
});

test("runReadouts: no note once a hop is costed, nor from a server that sends no flag", () => {
  // CONTROLS: the note is the flag's, not the mosaic's.
  eq(runReadouts(KNOWN, withProgress(SHOOTING, { hops_costed: true }), IDLE).etaNote, null,
    "hops_costed true");
  const old = withProgress(SHOOTING, {});
  delete (old.progress as { hops_costed?: boolean }).hops_costed;
  eq(runReadouts(KNOWN, old, IDLE).etaNote, null, "a server older than U-07");
  eq(runReadouts(KNOWN, withProgress(SHOOTING, { eta_s: -5 }), IDLE).etaS, null,
    "a negative remaining time is not a countdown");
});

// MUTANT "the panel through the meridian wait" (runStage's meridian_wait
// branch removed). Observed, runCopy.test 11/12:
//   x runReadouts: across a meridian wait the stage names the mosaic and the wait, for an operator and a viewer alike: the operator's stage
//     expected "M31 · waiting for the meridian"
//     got      "M31 2-1 · pass 17"
test("runReadouts: across a meridian wait the stage names the mosaic and the wait, for an operator and a viewer alike", () => {
  const op = runReadouts(KNOWN, WAIT_OPERATOR, IDLE);
  const viewer = runReadouts(KNOWN, WAIT_VIEWER, IDLE);
  eq(op.stage, `M31 · ${MERIDIAN_WAIT_STAGE}`, "the operator's stage");
  eq(viewer.stage, op.stage, "the viewer's stage is the operator's: nothing more for either");
  // NOTHING SITE-DERIVED: the operator is still served the panel and pass,
  // and both carry the meridian countdown; none of it reaches a readout.
  const eta = WAIT_OPERATOR.live!.meridian_eta_s!;
  assert(eta > 0, "premise: the recorded wait carries a meridian countdown");
  for (const r of [op, viewer]) {
    const all = JSON.stringify(r);
    assert(!all.includes("2-1") && !all.includes("pass"), `the waiting panel or pass leaked: ${all}`);
    assert(!all.includes(String(eta)), `the meridian countdown leaked: ${all}`);
  }
  eq([op.state, op.frames, op.frameGoal, op.etaNote], ["RUNNING", 45, 120, HOPS_NOT_COSTED],
    "the rest of the wait's readouts");
});

// MUTANT "readouts fed by any run" (runReadouts asking runIsLive(sequence)
// in place of flowRunLive(progress, sequence)). Observed, runCopy.test 11/12
// (re-run in S7 on the list form, `flowRunLive(known, sequence)` made
// `flowRunLive([sequence.session?.id ?? ""], sequence)`: 13/14, this same
// failure):
//   x runReadouts: another flow's run, an ended run and no answer show the idle values: another flow's live run
//     expected {"fed":false,"state":"IDLE","etaS":null,"etaNote":null,"stage":"—","frames":0,"frameGoal":null}
//     got      {"fed":true,"state":"RUNNING","etaS":17328,"etaNote":"hops not yet costed","stage":"M31 1-1 · pass 3","frames":6,"frameGoal":120}
test("runReadouts: another flow's run, an ended run and no answer show the idle values", () => {
  const idle = { fed: false, state: "IDLE", etaS: null, etaNote: null, stage: "—", frames: 0, frameGoal: null };
  eq(runReadouts(KNOWN, anotherFlowsRun(SHOOTING), IDLE), idle, "another flow's live run");
  eq(runReadouts(KNOWN, { ...SHOOTING, state: "complete" }, IDLE), idle, "this flow's run, ended");
  eq(runReadouts(null, SHOOTING, IDLE), idle, "no known session, so no way to tie the run to this flow");
  eq(runReadouts([], SHOOTING, IDLE), idle, "an empty list ties no run to this flow either");
  eq(runReadouts(KNOWN, undefined, IDLE), idle, "no sequence state");
  // The idle values are the store's, as before S5: an optimistic phase and
  // the run route's frame goal still print.
  const optimistic: RunIdle = { ...IDLE, phase: "running", frameGoal: 48 };
  eq(runReadouts(KNOWN, anotherFlowsRun(SHOOTING), optimistic).state, "RUNNING",
    "the store's own phase, not the other run's");
  eq(runReadouts(KNOWN, anotherFlowsRun(SHOOTING), optimistic).frameGoal, 48,
    "the store's own goal, not the other run's 120");
});

test("runReadouts: the engine's live states in the flow's words, and a single target's stage", () => {
  eq(runReadouts(KNOWN, { ...SHOOTING, state: "aborting" }, IDLE).state, "STOPPING",
    "an abort's wind-down is STOPPING, the flow's phase word, the rig still moving");
  eq(runReadouts(KNOWN, { ...SHOOTING, state: "holding" }, IDLE).state, "HOLDING", "a cloud hold");
  eq(runReadouts(KNOWN, { ...SHOOTING, state: "paused" }, IDLE).state, "PAUSED", "a pause");
  const single = { ...SHOOTING, group: undefined, target: "NGC 7331" };
  eq(runStage(single), "NGC 7331", "a single target reads the target the engine names");
  eq(runStage({ ...single, target: undefined }), null, "and nothing when it names none");
  const first = { ...SHOOTING, group: { ...SHOOTING.group!, panel: null } };
  eq(runStage(first), "M31 · pass 3", "a group before its first visit names no panel");
});

// ------------------------------------------------------- source, as text
/** A source file of this tree, read as text: runCopy.ts or flowsApi.ts. A
 *  file that cannot be read FAILS the case that asks for it. */
function source(rel: string): string {
  return (readFileSync(new URL(rel, import.meta.url), "utf8") as string).replace(/\r\n/g, "\n");
}
/** The comment text from `start` up to `end`, its comment markers dropped
 *  and its whitespace collapsed, so a sentence reads the same across a
 *  re-wrap. */
function commentBetween(src: string, start: string, end: string, what: string): string {
  const i = src.indexOf(start);
  if (i < 0) throw new Error(`${what}: no "${start}" in the file`);
  const j = src.indexOf(end, i + start.length);
  if (j < 0) throw new Error(`${what}: nothing ends it ("${end}")`);
  return src.slice(i, j).split("\n")
    .map((l) => l.replace(/^\s*(\/\/|\/\*\*|\*\/|\*)\s?/, "")).join(" ").replace(/\s+/g, " ");
}

// ------------------------------------------- 4. the observing-night count
// THE ROUTE'S RECORDED ANSWER. The recorded session has run two observing
// nights (the route's `nights` since S7), and the run route answered night 3
// for a CONTINUE pressed on the evening after (test_s5_recorded_state.py,
// CONTINUE_AT, `out["night"] == recorded nights + 1`). So the literal below is
// the run route's number and the input is the file's: a re-recording that
// moved the count turns this red, and so would a route that counted runs over
// a session whose second night held a restart.
//
// MUTANT "a run-counting route's recording" (S7; the scratch copy's fixture
// `nights` made 3, which is what the route answered before S7 for this
// session with one restart more on its second night). Observed, runCopy.test
// 9/14:
//   x premise: the recorded answer is the spec's CONTINUE case: night, banked and total as recorded
//     expected [3,194,480]
//     got      [4,194,480]
//   x runCopy: CONTINUE over a dormant session, computed from the progress route alone: the whole line
//     expected "CONTINUE M31 MOSAIC (night 3, 194/480 subs)"
//     got      "CONTINUE M31 MOSAIC (night 4, 194/480 subs)"
//   x runCopy: numbers that do not read as numbers are not put on the button: a flow with no name yet leaves the name out rather than print a gap
//     expected "CONTINUE (night 3, 194/480 subs)"
//     got      "CONTINUE (night 4, 194/480 subs)"
//   x startOverBody: START OVER says what it leaves, with CONTINUE's numbers: the numbers it walks away from
//     expected [3,194,480]
//     got      [4,194,480]
//   x the night reads the observing-night count, against the route's recorded answer: premise: the route recorded two observing nights
//     expected 2
//     got      3
// A run-counting COPY cannot be written against this route: since S7 nothing
// on the progress answer counts runs, and the recorded session's two runs
// fall on two nights. What the copy is SAID to read is held below instead.
//
// MUTANT "copy reads the run count" (S7; runCopy.ts's runCopy docstring back
// to S5's "`nights` counts the session's RUNS, so a night that held a
// restart counts twice until #430 is settled"). Observed, runCopy.test 13/14
// (the docstring it quotes cut here):
//   x the night reads the observing-night count, against the route's recorded answer: runCopy's docstring does not say the night reads the observing-night count: The RUN button's copy. STOP while `live` (the engine owns the rig for this flow): the button then aborts, whatever the progress route says. CONTINUE when the route names a DORMANT session, the one case in which `run_flow` continues rather than starts fresh (server `run_flow`: "it is continued only if it is dormant"). The night is the session's `nights` plus one, as the run route answers it (`_continue_flow_session`), so the button and the run's own log line agree; `nights` counts the session's RUNS, so a night that held a restart counts twice until #430 is settled, on both of them alike. ...
// MUTANT "flowsApi says runs" (S7; flowsApi.ts's FlowProgressSession.nights
// doc back to S5's "How many RUNS the session has had"). Observed,
// runCopy.test 13/14:
//   x the night reads the observing-night count, against the route's recorded answer: flowsApi.ts does not say nights counts observing nights: export interface FlowProgressSession { id: string; status: "active" | "dormant" | "complete"; How many RUNS the session has had: one report per engine start, so a restart on the same night counts again (#430). Equal to the nights it has run only while no night held a restart; CONTINUE's `night` is this plus one. */ nights: number;
test("the night reads the observing-night count, against the route's recorded answer", () => {
  eq(PROGRESS.session!.nights, 2, "premise: the route recorded two observing nights");
  const c = runCopy(FLOW_NAME, PROGRESS, false);
  eq(c.night, 3, "the night the run route answered for a CONTINUE on the evening after");
  eq(c.detail, "(night 3, 194/480 subs)", "the parenthetical the button prints");

  const doc = commentBetween(source("../runCopy.ts"), "/** The RUN button's copy.",
    "export function runCopy(", "runCopy's docstring");
  assert(doc.includes("THE NIGHT READS THE OBSERVING-NIGHT COUNT"),
    `runCopy's docstring does not say the night reads the observing-night count: ${doc}`);
  assert(!/counts the session's RUNS/.test(doc),
    `runCopy's docstring still says the copy reads the run count: ${doc}`);
  assert(doc.includes("#511"), `runCopy's docstring does not name the same-night gap (#511): ${doc}`);

  const api = commentBetween(source("../../../lib/flowsApi.ts"), "export interface FlowProgressSession {",
    "count_mode:", "FlowProgressSession.nights");
  assert(api.includes("How many OBSERVING NIGHTS the session has run"),
    `flowsApi.ts does not say nights counts observing nights: ${api}`);
  assert(!/How many RUNS the session has had/.test(api),
    `flowsApi.ts still says nights counts runs: ${api}`);
});

// ----------------------------------- 5. NOTHING SITE-DERIVED, and #166 item 1
// The header paragraph used to end "The readouts read none of them." with no
// word on the ETA, which carries a flip's cost while a flip falls inside the
// run (#166 item 1). It must name that field and that item (#510, B16).
//
// MUTANT "old sentence restored" (S7; runCopy.ts's paragraph back to S5's
// six lines under "NOTHING SITE-DERIVED."). Observed, runCopy.test 13/14:
//   x the NOTHING SITE-DERIVED paragraph names the ETA's flip term, #166 item 1: the paragraph does not cite #166 item 1: NOTHING SITE-DERIVED. The published state carries `live.meridian_eta_s` and a meridian wait's `schedule.reason`, and for an operator the panel and pass across that wait: each timestamps a transit, which gives away the site's longitude (spec 6.9). The readouts read none of them. Across a meridian wait the stage names the mosaic and the wait, the same words for an operator and a viewer.
test("the NOTHING SITE-DERIVED paragraph names the ETA's flip term, #166 item 1", () => {
  const src = source("../runCopy.ts");
  const para = commentBetween(src, "// NOTHING SITE-DERIVED", "\n//\n", "the NOTHING SITE-DERIVED paragraph");
  assert(para.includes("#166 item 1"), `the paragraph does not cite #166 item 1: ${para}`);
  assert(para.includes("`progress.eta_s`"), `the paragraph does not name progress.eta_s: ${para}`);
  assert(!/^\/\/ NOTHING SITE-DERIVED\.\s/m.test(src),
    "the paragraph still opens on the bare claim \"NOTHING SITE-DERIVED.\"");
});

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`runCopy.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) (globalThis as any).process.exitCode = 1;
export const result = { passed, failed, total };
export default result;
