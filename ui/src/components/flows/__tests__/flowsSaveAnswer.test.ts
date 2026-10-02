// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// flowsSaveAnswer.test.ts - what a SAVE did to the counts reaches the flow log
// (#189; spec 3.3 and Revision 2, rulings 2 and 3).
//
//   Run:  node --import tsx src/components/flows/__tests__/flowsSaveAnswer.test.ts   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT IS WORTH GUARDING HERE
//
// The server's save (`_persist_flow`) can change what a flow's counts mean,
// and the canvas shows neither change:
//
//   - `migrated: [{key: "counts", ...}]`: the save switched every TARGET and
//     POOL to counting accepted subs only (ruling 2). Both editors said
//     beforehand that saving would; the log says that it did.
//   - `reanchored: [{node_id, max_move_deg, threshold_deg, reason}]`: a
//     block's framing moved too far for its counts to carry, so they start
//     from zero (ruling 3), and `reason` says which rule decided (S4
//     orchestrator ruling 6, #352). A raw RA nudge in the inspector does
//     this, and unsaid the operator meets it nights later as CONTINUE's
//     dropped-steps question. The server's own rows are read from its
//     fixture below, never only written here.
//
// One line for the switch, one per re-anchored block, on the log both editors
// draw (`components/flows/FlowLogStrip`, the #/next `canvas/FlowLogStrip`).
// Nothing for an answer that carries neither, which is every older server's.
//
// Every case names the mutant it kills and quotes the failure that mutant
// produced when it was run in a private scratch copy of ui/ (#254).

/* eslint-disable @typescript-eslint/no-explicit-any */

// @ts-ignore  node built-ins; tsx supplies them at runtime
import { readFileSync } from "node:fs";

// The slice imports lib/flowsApi -> lib/api -> lib/base, and base.ts reads
// `window.location.pathname` AT MODULE SCOPE. No request is made: the flowsApi
// calls `flowsSave` reaches are replaced below.
(globalThis as any).window = { location: { pathname: "/", origin: "http://local" } };
(globalThis as any).localStorage = { getItem: () => null, setItem() {}, removeItem() {} };

const { createFlowsActions, FLOWS_INIT, COUNTS_SWITCHED_LINE } = await import("../flowsSlice");
const { flowsApi } = await import("../../../lib/flowsApi");
type FlowsHost = import("../flowsSlice").FlowsHost;
type FlowsState = import("../flowsSlice").FlowsState;
type FlowGraphRec = import("../flowsTypes").FlowGraphRec;

let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) {
    throw new Error(`${msg}\n  expected ${JSON.stringify(want)}\n  got      ${JSON.stringify(got)}`);
  }
}

/** A mosaic, an unnamed single target and a stage, as the editor holds them. */
const GRAPH: FlowGraphRec = {
  nodes: [
    { id: "m31", type: "target", x: 0, y: 0, params: { name: "M31", rows: 3, cols: 2 } },
    { id: "coords", type: "target", x: 0, y: 200, params: { name: "  ", ra: "05h 35m", dec: "-05 23" } },
    { id: "cy", type: "cycle", x: 300, y: 0, params: {} },
  ],
  edges: [{ id: "k1", from: "m31", fromPort: "target", to: "cy", toPort: "run" }],
};

function record(extra: Record<string, unknown> = {}) {
  return {
    id: "f1", name: "Flow f1", folder: "My flows", tagline: "",
    graph: JSON.parse(JSON.stringify(GRAPH)),
    created_ts: 1, updated_ts: 1, last_run: null, last_result: "", readonly: false,
    ...extra,
  };
}

/** What the next `PUT /api/flows/{id}` answers: the flow it was sent, plus
 *  `answer` (the save's notes). `hold` keeps the PUT open until released. */
let answer: Record<string, unknown> = {};
let hold: Promise<void> | null = null;
/** Every flow a PUT was handed, in order, as it went over the wire. */
const sent: any[] = [];
(flowsApi as any).save = async (_id: string, flow: any) => {
  sent.push(JSON.parse(JSON.stringify(flow)));
  if (hold) await hold;
  return JSON.parse(JSON.stringify({ ...flow, migrated: [], ...answer }));
};
(flowsApi as any).get = async (id: string) => record({ id });
(flowsApi as any).compileDraft = async () => ({ plan: {}, structural: [], issues: [], unmapped: [] });
(flowsApi as any).progress = async () => { throw new Error("Not Found"); };

/** A store holding flow f1, open and dirty, so SAVE has something to send. */
function harness() {
  let state: FlowsHost;
  const set = (fn: (s: FlowsHost) => Partial<FlowsHost>) => {
    state = { ...state, ...fn(state) } as FlowsHost;
  };
  const get = () => state;
  const actions = createFlowsActions(set, get);
  const rec = record();
  state = {
    ...actions,
    flows: { ...FLOWS_INIT, record: rec as never, graph: rec.graph, dirty: true },
  } as FlowsHost;
  return { get flows(): FlowsState { return state.flows; }, a: actions };
}

const lines = (f: FlowsState) => f.logs.map((l) => `[${l.tone}] ${l.msg}`);

// ============================================================ the switch

// MUTANT "ignore migrated on save" (flowsSlice.ts saveAnswerLines: the
// `migrated` read deleted). Observed (4/9 passed; this case, the bare
// spelling, the two-entries case, the both-at-once case and, on its
// precondition, the not-sent-back case failed):
//   x a save that switched the counts says so on the log, once, at info: the counts switch never reached the flow log
//   expected 1
//   got      0
await test("a save that switched the counts says so on the log, once, at info", async () => {
  answer = { migrated: [{ key: "counts", note: "counts: switched to Accepted subs on 2 blocks" }] };
  const h = harness();
  await h.a.flowsSave();
  eq(h.flows.dirty, false, "precondition: the save completed");
  eq(h.flows.logs.length, 1, "the counts switch never reached the flow log");
  eq(h.flows.logs[0].msg, COUNTS_SWITCHED_LINE, "the line is not the editor's counts sentence");
  eq(h.flows.logs[0].tone, "info", "the switch was announced before the save; it is news, not a fault");
  eq(/now counts accepted subs only/.test(h.flows.logs[0].msg), true,
    "ruling 2 fixes the words: the UI says it \"now counts accepted subs only\"");
});

await test("the spec's own spelling, migrated: [\"counts\"], says the same one line", async () => {
  // The spec writes the answer as `migrated: ["counts"]` and the record model
  // as MigrationNote objects; whichever the server sends, the log says it.
  answer = { migrated: ["counts"] };
  const h = harness();
  await h.a.flowsSave();
  eq(lines(h.flows).join(" | "), `[info] ${COUNTS_SWITCHED_LINE}`, "the bare spelling was not read");
});

// MUTANT "a line per entry" (saveAnswerLines: one line pushed for EACH
// counts entry instead of one for any). Observed (8/9 passed):
//   x two counts entries still make one line: the switch is one fact, said once
//   expected 1
//   got      2
await test("two counts entries still make one line", async () => {
  answer = { migrated: [{ key: "counts", note: "a" }, "counts"] };
  const h = harness();
  await h.a.flowsSave();
  eq(h.flows.logs.length, 1, "the switch is one fact, said once");
});

// ========================================================= the re-anchor

// MUTANT "ignore reanchored on save" (saveAnswerLines: the `reanchored` loop
// skipped). Observed (5/9 passed; this case, the reasons case, the
// both-at-once case and, on its precondition, the not-sent-back case
// failed):
//   x each re-anchored block gets its own warn line, named, with the move and the limit: a re-anchored block never reached the log
//   expected "[warn] TARGET \"M31\" starts counting from zero: its framing moved 14.8', and its grid carries counts over only for a move under 10.0'. The subs it banked stay on disk. | [warn] a TARGET with no name starts counting from zero: its framing moved 3.0', and with no camera field recorded no move carries counts over. The subs it banked stay on disk."
//   got      ""
//
// MUTANT "minutes as degrees" (saveAnswerLines: `arcmin` prints the degrees
// it is handed). Observed (8/9 passed):
//   x each re-anchored block gets its own warn line, named, with the move and the limit: a re-anchored block never reached the log
//   expected (as above)
//   got      "[warn] TARGET \"M31\" starts counting from zero: its framing moved 0.2', and its grid carries counts over only for a move under 0.2'. The subs it banked stay on disk. | [warn] a TARGET with no name starts counting from zero: its framing moved 0.1', and with no camera field recorded no move carries counts over. The subs it banked stay on disk."
//
// MUTANT "no zero-limit words" (reanchorWhy: the `limit === 0` branch made
// unreachable). Observed (8/9 passed):
//   x each re-anchored block gets its own warn line, named, with the move and the limit: a re-anchored block never reached the log
//   expected (as above)
//   got      "[warn] TARGET \"M31\" starts counting from zero: its framing moved 14.8', and its grid carries counts over only for a move under 10.0'. The subs it banked stay on disk. | [warn] a TARGET with no name starts counting from zero: its framing moved 3.0', and its grid carries counts over only for a move under 0.0'. The subs it banked stay on disk."
await test("each re-anchored block gets its own warn line, named, with the move and the limit", async () => {
  answer = {
    reanchored: [
      { node_id: "m31", max_move_deg: 14.8 / 60, threshold_deg: 10 / 60 },
      // A block with no field of view has a threshold of 0 (spec 3.3).
      { node_id: "coords", max_move_deg: 0.05, threshold_deg: 0 },
    ],
  };
  const h = harness();
  await h.a.flowsSave();
  eq(lines(h.flows).join(" | "),
    "[warn] TARGET \"M31\" starts counting from zero: its framing moved 14.8', and its grid carries "
    + "counts over only for a move under 10.0'. The subs it banked stay on disk."
    + " | [warn] a TARGET with no name starts counting from zero: its framing moved 3.0', and with "
    + "no camera field recorded no move carries counts over. The subs it banked stay on disk.",
    "a re-anchored block never reached the log");
});

// MUTANT "every re-anchor is a move" (reanchorWhy: the switch's three cases
// deleted, so an entry with no numbers falls to the generic words). Observed
// (7/9 passed; this case and the both-at-once case failed):
//   x a re-anchor with no move measured says why in words: the grid and angle reasons lost their words
//   expected "its rows or columns changed | its angle changed between any angle and a set one | it now names a different object | its framing changed"
//   got      "its framing changed | its framing changed | its framing changed | its framing changed"
await test("a re-anchor with no move measured says why in words", async () => {
  answer = {
    reanchored: [
      { node_id: "m31", max_move_deg: null, threshold_deg: 10 / 60, reason: "grid" },
      { node_id: "m31", max_move_deg: null, threshold_deg: null, reason: "angle" },
      { node_id: "m31", max_move_deg: null, threshold_deg: null, reason: "identity" },
      // A reason this build does not know claims nothing it cannot back.
      { node_id: "m31", max_move_deg: null, threshold_deg: null, reason: "tides" },
    ],
  };
  const h = harness();
  await h.a.flowsSave();
  const why = h.flows.logs.map((l) => /from zero: (.*)\. The subs/.exec(l.msg)?.[1] ?? l.msg);
  eq(why.join(" | "),
    "its rows or columns changed | its angle changed between any angle and a set one"
    + " | it now names a different object | its framing changed",
    "the grid and angle reasons lost their words");
});

// ================================================ the server's own rows
//
// THE ROWS BELOW ARE THE SERVER'S, READ, NOT COPIED (#352, S4 orchestrator
// ruling 6). The case above writes its own rows, and until ruling 6 the
// server sent no `reason` at all, so its grid, angle and identity words could
// not happen in production: every such restart read "its framing changed".
// server/tests/fixtures/save_answer_rows.json holds `prepare_save`'s rows for
// one save of each reason, and test_flows_save_answer_fixture.py grades the
// file against `prepare_save` exactly; this reads the same file, as
// panelLane.test.ts reads its fixture, so a row shape that drifts goes red on
// one side or the other. Do not replace the read with a literal.
//
// MUTANT "row drops reason" (server/astrodeck/flows/save_rules.py
// `_anchor_on_save`: the row built without `reason`, and the fixture recorded
// again from the mutated server, as a hand "fixing" the red grader would;
// scratch copies of server/ and ui/ side by side). The server grader was red
// first (test_flows_save_answer_fixture.py, and still red after the re-record,
// on `KeyError: 'reason'`); with the fixture re-recorded, this case read the
// generic words for all three. Observed (9/10 passed):
//   x the server's recorded rows say why in words: the recorded grid, angle and identity rows lost their words
//   expected "grid: its rows or columns changed | angle: its angle changed between any angle and a set one | identity: it now names a different object | move: its framing moved 5400.0', and with no camera field recorded no move carries counts over"
//   got      "grid: its framing changed | angle: its framing changed | identity: its framing changed | move: its framing moved 5400.0', and with no camera field recorded no move carries counts over"
//
// MUTANT "fixture hand-edited" (the recorded grid row's reason made "angle"
// in the scratch copy) is the server grader's to catch, and it did; this case
// read the edit as the truth (9/10 passed), which is why the grader exists:
//   got      "grid: its angle changed between any angle and a set one | angle: ..."

const FIXTURE_REL = "../../../../../server/tests/fixtures/save_answer_rows.json";

/** The server's recorded rows by reason. A missing or unreadable file must
 *  FAIL the case, never skip it: a skipped fixture reads as a green mirror. */
function serverRows(): Record<string, unknown[]> {
  let text: string;
  try {
    text = readFileSync(new URL(FIXTURE_REL, import.meta.url), "utf8") as string;
  } catch (e) {
    throw new Error(`cannot read ${FIXTURE_REL}, the rows this case is graded against: ${(e as Error).message}`);
  }
  const cases = (JSON.parse(text) as { cases?: Record<string, unknown[]> }).cases;
  if (!cases || typeof cases !== "object" || Object.keys(cases).length === 0) {
    throw new Error(`${FIXTURE_REL} holds no cases`);
  }
  return cases;
}

await test("the server's recorded rows say why in words", async () => {
  const rows = serverRows();
  const got: string[] = [];
  for (const reason of ["grid", "angle", "identity", "move"]) {
    const recorded = rows[reason];
    eq(Array.isArray(recorded) && recorded.length, 1, `the fixture records one ${reason} row`);
    // The server's node id is "t": name it in the graph the save answers for.
    answer = { reanchored: recorded };
    const h = harness();
    h.flows.graph!.nodes.push({ id: "t", type: "target", x: 0, y: 400, params: { name: "M31 3x2" } });
    await h.a.flowsSave();
    eq(h.flows.logs.length, 1, `the recorded ${reason} row did not reach the log`);
    const why = /from zero: (.*)\. The subs/.exec(h.flows.logs[0].msg)?.[1] ?? h.flows.logs[0].msg;
    got.push(`${reason}: ${why}`);
  }
  eq(got.join(" | "),
    "grid: its rows or columns changed"
    + " | angle: its angle changed between any angle and a set one"
    + " | identity: it now names a different object"
    // Control: a measured move is worded from its numbers, not its reason.
    + " | move: its framing moved 5400.0', and with no camera field recorded no move carries counts over",
    "the recorded grid, angle and identity rows lost their words");
});

await test("the switch and the re-anchors together: the switch first, then each block", async () => {
  answer = {
    migrated: [{ key: "counts", note: "x" }],
    reanchored: [{ node_id: "m31", max_move_deg: null, threshold_deg: null, reason: "grid" }],
  };
  const h = harness();
  await h.a.flowsSave();
  eq(lines(h.flows).join(" | "),
    `[info] ${COUNTS_SWITCHED_LINE} | [warn] TARGET "M31" starts counting from zero: `
    + "its rows or columns changed. The subs it banked stay on disk.",
    "the two notes were not both said, in order");
});

// The notes are the ANSWER'S, said once, and not part of the flow. flowsSave
// keeps the answer as the open record and the next SAVE sends that record
// back, so a note kept on it goes back to the server. `FlowRecord.migrated`
// takes MigrationNote objects, and the bare "counts" the spec writes is
// refused there (checked against models.py: "Input should be a valid
// dictionary or instance of MigrationNote"): every later save would be a 422,
// and a close, which does not wait for a save to succeed, would drop the edit.
//
// MUTANT "store the answer as it came" (flowsSlice.ts flowsSave: `record:
// saved`, the notes kept on the record). Observed (8/9 passed):
//   x the answer's notes are not sent back: the next SAVE carries neither: the second SAVE sent the first answer's notes back
//   expected ""
//   got      "migrated [\"counts\"]; reanchored [{\"node_id\":\"m31\",\"max_move_deg\":null,\"threshold_deg\":null,\"reason\":\"grid\"}]"
await test("the answer's notes are not sent back: the next SAVE carries neither", async () => {
  answer = {
    migrated: ["counts"],
    reanchored: [{ node_id: "m31", max_move_deg: null, threshold_deg: null, reason: "grid" }],
  };
  const h = harness();
  await h.a.flowsSave();
  eq(h.flows.logs.length, 2, "precondition: the first SAVE said both notes");
  answer = {};
  h.a.flowsSetParam("cy", "cycles", "7");
  sent.length = 0;
  await h.a.flowsSave();
  eq(sent.length, 1, "precondition: the second SAVE sent a PUT");
  const back = sent[0];
  const echoed = [
    ...(Array.isArray(back.migrated) && back.migrated.length ? [`migrated ${JSON.stringify(back.migrated)}`] : []),
    ...("reanchored" in back ? [`reanchored ${JSON.stringify(back.reanchored)}`] : []),
  ];
  eq(echoed.join("; "), "", "the second SAVE sent the first answer's notes back");
  eq(h.flows.logs.length, 2, "a note was said again by a save that did not make it");
});

// ============================================================== controls

// CONTROL. An older server's answer has neither key, and a save that changed
// neither answers `migrated: []` and no `reanchored`.
//
// MUTANT "no guard" (saveAnswerLines: `Array.isArray(reanchored)` replaced by
// `reanchored` being iterated as it is). The throw fails the whole save, so
// the three counts cases go red with this one. Observed (5/9 passed):
//   x control: a save answer without either note logs nothing: an answer with no notes made the save fail
//   expected null
//   got      "reanchored is not iterable"
await test("control: a save answer without either note logs nothing", async () => {
  for (const a of [{}, { migrated: [] }, { migrated: [], reanchored: [] },
                   { migrated: [{ key: "rotation", note: "a read's note" }] }]) {
    answer = a;
    const h = harness();
    await h.a.flowsSave();
    eq(h.flows.libraryError, null, "an answer with no notes made the save fail");
    eq(h.flows.dirty, false, "precondition: the save completed");
    eq(h.flows.logs.length, 0, `the answer ${JSON.stringify(a)} put a line on the log`);
  }
});

// CONTROL. The answer arrives after another flow was opened: its notes are
// about a flow no longer on screen, and would land on the other flow's log.
//
// MUTANT "log before the stale check" (flowsSlice.ts flowsSave: the
// saveAnswerLines loop moved above `if (!cur || cur.id !== record.id)`).
// Observed (8/9 passed):
//   x control: a save answered after another flow opened says nothing on that flow's log: the answer for f1 wrote on f2's log
//   expected 0
//   got      1
// RE-PINNED (W2 integration, #500/#162 backlog WP-16 (b), owner-approved
// 2026-09-30): `flowsOpen` now awaits a carried save's own promise instead
// of sending a second PUT, so the held PUT must answer BEFORE the open is
// awaited -- after, as this test used to, deadlocks the open on it, since
// the save IS the thing the open is now waiting on.
//
// THIS CHANGES WHAT THE CASE PROVES, the same way as flowsReanchorToast
// .test.ts's analogous control: the save no longer arrives "after another
// flow opened" -- it settles on f1 while f1 is STILL open (not stale), so
// it correctly writes its own log line THEN, before f2 ever opens. The log
// strip is not cleared by an open (`logs` persists across the switch), so
// that line is still there once f2 is the open record, which is right: it
// is f1's own history, not a leak of f1's answer onto f2's graph or counts.
// The length assertion moves from 0 to 1, and a second assertion checks the
// one line is f1's switch note, not a phantom one.
await test("control: a save answered after another flow opened says nothing on that flow's log", async () => {
  answer = { migrated: [{ key: "counts", note: "x" }] };
  let release!: () => void;
  hold = new Promise<void>((r) => { release = r; });
  const h = harness();
  const saving = h.a.flowsSave();
  release();
  await h.a.flowsOpen("f2");
  eq(h.flows.record?.id, "f2", "precondition: the other flow opened");
  await saving;
  hold = null;
  eq(h.flows.logs.length, 1,
    "the carried save settles on f1 before the switch, so it is no longer "
    + "stale and logs once, correctly, for f1");
  eq(h.flows.logs[0]?.msg, COUNTS_SWITCHED_LINE, "the one line is f1's own switch note");
});

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`flowsSaveAnswer.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) (globalThis as any).process.exitCode = 1;
export default { passed, failed, total };
export { passed, failed, total };
