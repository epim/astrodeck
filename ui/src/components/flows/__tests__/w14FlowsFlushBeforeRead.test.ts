// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w14FlowsFlushBeforeRead.test.ts - Tonight and RUN read the CANVAS, not the
// stored flow: the edit is saved first (#688, part 1 of 3; WP-86).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/__tests__/w14FlowsFlushBeforeRead.test.ts   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE OWNER'S REPORT. A flow made by the wizard had one target block. The
// owner deleted it, added a LRGB filter cycle and a SHO filter cycle in
// series, and pressed TONIGHT, then STORY: the sentences described the old
// flow (one target, one capture), and PLAN described it too. Leaving the flow
// and opening it again showed the right ones. The editor has no autosave, so
// the canvas and the stored flow were two graphs, and `GET /tonight` (the
// STORY, TIMELINE and CAMPAIGN tabs) reads the STORED one; PLAN reads
// `flows.compiled`, which is refreshed on open, on DONE and after a save and
// so described the flow as last saved or compiled. In the classic editor RUN
// posts the stored flow too, so the same gap started a night on the graph the
// operator had just replaced.
//
// THE FIX under test (this WP only; autosave and undo are later WPs). Before
// Tonight reads, and before RUN posts, the slice SAVES the canvas
// (race-safe through the save already in flight) and, for Tonight, waits for
// a compile of what is on screen. RUN refuses with a logged sentence when the
// edit still did not save, and a read-only Example, which cannot be saved at
// all, keeps RUN locked while edited.
//
// A SERVER THAT REMEMBERS. `flowsApi.save` is held open until a test answers
// it, and `flowsApi.tonight` answers from "whatever was PUT last": a fake
// store that is exactly as stale as the real one when the PUT never happened.
// Without that, a test of the order of two calls would pass against a store
// that sent them in either order.
//
// Every case names the mutant it kills and quotes what that mutant produced
// when it was run from a byte-for-byte backup of the file named (restored and
// sha256-compared after each run, the mutant's marker grepped absent). The
// first case, against the code as it stood before this WP (no flush at all),
// was red for the owner's reason:
//   x the owner's report (#688): ... the edit was never saved, so Tonight was
//     read off the stored flow
//     expected "PUT"
//     got      "tonight"

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------- globals first
(globalThis as any).window = {
  location: { pathname: "/", origin: "http://local" },
};
(globalThis as any).localStorage = {
  getItem: () => null, setItem() {}, removeItem() {},
};

const { createFlowsActions, FLOWS_INIT, compiledIsCurrent } = await import("../flowsSlice");
const { flowsApi } = await import("../../../lib/flowsApi");
const { NODE_DEFS } = await import("../nodeDefs");
const { RUN_UNSAVED_EXAMPLE_REASON } = await import("../flowsTypes");
type FlowsHost = import("../flowsSlice").FlowsHost;
type FlowsState = import("../flowsSlice").FlowsState;

// ------------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) {
    throw new Error(`${msg}\n  expected ${JSON.stringify(want)}\n  got      ${JSON.stringify(got)}`);
  }
}
/** One macrotask: every promise chain that can finish without a timer has
 *  finished when this resolves, so a NEGATIVE ("nothing was asked yet") is
 *  read after it. Not a duration: no assertion depends on a clock's tick. */
const settle = () => new Promise<void>((r) => setTimeout(r, 0));
/** A POSITIVE wait, polled against a deadline rather than slept for. */
async function until(cond: () => boolean, what: string, ms = 3000): Promise<void> {
  const end = Date.now() + ms;
  while (!cond()) {
    if (Date.now() > end) throw new Error(`timed out waiting for ${what}`);
    await settle();
  }
}

// ------------------------------------------------------------------- stubs
/** The one block a saved flow starts with: the owner's wizard flow, reduced to
 *  the thing the report turns on. */
function rec(id: string, readonly = false) {
  return {
    id, name: `Flow ${id}`, folder: "My flows", tagline: "",
    graph: { nodes: [{ id: "t1", type: "target", x: 0, y: 0,
                       params: { ...NODE_DEFS.target.params } }], edges: [] },
    created_ts: 1, updated_ts: 1, last_run: null, last_result: "", readonly,
  };
}

interface Put { id: string; flow: any; resolve: (v: unknown) => void; reject: (e: unknown) => void }
let puts: Put[] = [];
/** Everything that reached the server, in order. */
let events: string[] = [];
/** What the fake server holds for the flow: the last PUT it answered. */
let stored: any = null;
/** What `compiledIsCurrent` said at the moment `tonight` was asked, or null. */
let currentAtTonight: boolean | null = null;
let compileCalls = 0;
let runCalls = 0;
let runAnswer: () => Promise<unknown> = async () => ({ started: true, flow_id: "f1", frames: 10, unmapped: [] });
let readonlyRecord = false;
/** While set, a compile is held open until the test releases it: the way to
 *  say "RUN did not wait for this" without a clock. */
let holdCompile = false;
let heldCompiles: Array<() => void> = [];
/** While set, the tonight request is held open until the test releases it. */
let holdTonight = false;
let heldTonight: Array<() => void> = [];

(flowsApi as any).save = (id: string, flow: any) => {
  events.push("PUT");
  return new Promise((resolve, reject) => { puts.push({ id, flow, resolve, reject }); });
};
(flowsApi as any).get = async (id: string) => { const r = rec(id, readonlyRecord); stored = r; return r; };
// The compile answers on a TIMER, not a microtask, so a caller that does not
// wait for it reads a `compiled` that is not there yet.
(flowsApi as any).compileDraft = (graph: unknown) => new Promise((resolve) => {
  compileCalls++;
  events.push("compile");
  const answer = () => resolve({ plan: { nodes: (graph as any).nodes.length }, structural: [], issues: [], unmapped: [] });
  if (holdCompile) heldCompiles.push(answer); else setTimeout(answer, 0);
});
(flowsApi as any).progress = async () => { throw new Error("Not Found"); };
(flowsApi as any).list = async () => [];
(flowsApi as any).folders = async () => [];
(flowsApi as any).tonight = async () => {
  events.push("tonight");
  currentAtTonight = latest ? compiledIsCurrent(latest().flows) : null;
  if (holdTonight) await new Promise<void>((resolve) => { heldTonight.push(resolve); });
  return { ok: true, brief: describe(stored?.graph), story: [] };
};
(flowsApi as any).run = async () => { runCalls++; events.push("run"); return runAnswer(); };

/** The server's answer to a PUT: what it was sent, stored, with a new stamp. */
function answerPut(p: Put, ts: number): void {
  stored = { ...p.flow, id: p.id, updated_ts: ts };
  p.resolve(stored);
}

/** A flow as one phrase: the block types in order. What a STORY would be built
 *  from, reduced so a wrong flow is a visibly different string. */
function describe(graph: any): string {
  return (graph?.nodes ?? []).map((n: any) => n.type).join(",");
}

let latest: (() => FlowsHost) | null = null;
let toasts: any[] = [];

/** The slice alone behind a miniature store: the set/get contract zustand
 *  hands it. */
function slice() {
  let state: FlowsHost;
  const set = (fn: (s: FlowsHost) => Partial<FlowsHost>) => {
    state = { ...state, ...fn(state) } as FlowsHost;
  };
  const get = () => state;
  const actions = createFlowsActions(set, get);
  toasts = [];
  state = { ...actions, flows: { ...FLOWS_INIT }, enqueueToast: (t: any) => { toasts.push(t); } } as FlowsHost;
  puts = []; events = []; stored = null; currentAtTonight = null;
  compileCalls = 0; runCalls = 0; readonlyRecord = false;
  holdCompile = false; heldCompiles = [];
  holdTonight = false; heldTonight = [];
  runAnswer = async () => ({ started: true, flow_id: "f1", frames: 10, unmapped: [] });
  latest = get;
  return { get flows(): FlowsState { return state.flows; }, a: actions };
}

/** The owner's edit: the only block deleted, two filter cycles added. */
function ownersEdit(s: ReturnType<typeof slice>): void {
  s.a.flowsSelect({ kind: "node", id: "t1" });
  s.a.flowsDeleteSel();
  s.a.flowsAddNode("cycle", { x: 0, y: 0 });
  s.a.flowsAddNode("cycle", { x: 0, y: 200 });
}
const logLines = (f: FlowsState) => f.logs.map((l) => `${l.tone}: ${l.msg}`);

// ======================================================= 1. TONIGHT'S READ

// MUTANT "flowsFetchTonight skips the flush" (flowsSlice.ts: the `await
// saveBeforeRead();` ahead of the first `superseded()` check in
// flowsFetchTonight made `void 0;`). Observed, this file 10/15 passed (the
// PLAN-current, closed-flow, payload-cleared and one-PUT-at-a-time cases fail
// too, by timing out on a PUT that is never sent), and the mounted twins 3/6:
//   x the owner's report (#688): STORY after deleting the block and adding two
//     filter cycles describes the canvas, and the edit was PUT before the read:
//     the edit was never saved, so Tonight was read off the stored flow
//     expected "PUT"
//     got      "tonight"
await test("the owner's report (#688): STORY after deleting the block and adding two filter cycles describes the canvas, and the edit was PUT before the read", async () => {
  const s = slice();
  await s.a.flowsOpen("f1");
  ownersEdit(s);
  eq(s.flows.dirty, true, "precondition: the edit made the graph dirty");
  eq(describe(s.flows.graph), "cycle,cycle", "precondition: the canvas holds the two cycles and not the target");

  const fetching = s.a.flowsFetchTonight();
  await settle();
  eq(events.filter((e) => e !== "compile").join(", "), "PUT",
    "the edit was never saved, so Tonight was read off the stored flow");
  eq(describe(puts[0]?.flow?.graph), "cycle,cycle", "the PUT did not carry the post-edit graph");
  answerPut(puts[0], 2);
  await fetching;

  eq(events.filter((e) => e !== "compile").join(", "), "PUT, tonight",
    "Tonight was read before the edit was saved");
  eq(String((s.flows.tonight as any)?.brief), "cycle,cycle",
    "STORY read the stored flow: it describes the flow as it was last saved, not as drawn");
  eq(s.flows.tonightLoading, false, "the fetch left Tonight loading");
  eq(s.flows.dirty, false, "the flush left the saved edit marked unsaved");
});

// MUTANT "the flush does not wait for a compile" (flowsSlice.ts: the `await
// get().flowsCompile()` in flowsFetchTonight made `void get().flowsCompile()`).
// Observed, 14/15 passed; the compile answers on a timer in this file, so an
// unawaited one lands after Tonight was asked:
//   x PLAN is current when Tonight is read: the compile of the canvas has landed
//     before Tonight is asked, so the PLAN tab is not last round's: the PLAN tab
//     was still the compile of an earlier graph when Tonight was asked
//     expected "current"
//     got      "not current"
await test("PLAN is current when Tonight is read: the compile of the canvas has landed before Tonight is asked, so the PLAN tab is not last round's", async () => {
  const s = slice();
  await s.a.flowsOpen("f1");
  ownersEdit(s);
  eq(compiledIsCurrent(s.flows), false, "precondition: an edit leaves the compile in hand stale");
  const staleFrom = s.flows.compiled?.from;

  const fetching = s.a.flowsFetchTonight();
  await until(() => puts.length === 1, "the flush's PUT");
  answerPut(puts[0], 2);
  await fetching;

  eq(currentAtTonight === null ? "never asked" : currentAtTonight ? "current" : "not current", "current",
    "the PLAN tab was still the compile of an earlier graph when Tonight was asked");
  eq(compiledIsCurrent(s.flows), true, "PLAN is not the compile of what is on screen");
  eq(s.flows.compiled?.from === staleFrom, false, "the compile in hand is still the one from before the edit");
  eq((s.flows.compiled?.plan as any)?.nodes, 2, "PLAN's plan is not the two-block canvas");
});

// MUTANT "always compile" (flowsSlice.ts: `if (!compiledIsCurrent(get().flows))
// await get().flowsCompile();` made an unconditional `await
// get().flowsCompile();`). Observed, 14/15 passed:
//   x CONTROL: nothing edited, so Tonight sends no PUT and no second compile: a
//     clean, current flow was saved or compiled again for a Tonight read
//     expected "0 PUTs, 1 compiles"
//     got      "0 PUTs, 2 compiles"
await test("CONTROL: nothing edited, so Tonight sends no PUT and no second compile", async () => {
  const s = slice();
  await s.a.flowsOpen("f1");
  eq(compiledIsCurrent(s.flows), true, "precondition: the open's compile is current");
  eq(compileCalls, 1, "precondition: the open compiled once");
  await s.a.flowsFetchTonight();
  eq(`${puts.length} PUTs, ${compileCalls} compiles`, "0 PUTs, 1 compiles",
    "a clean, current flow was saved or compiled again for a Tonight read");
  eq(String((s.flows.tonight as any)?.brief), "target", "Tonight did not read the flow");
});

// MUTANT "no stale guard on the answer" (flowsSlice.ts: `superseded` made to
// return false at once). Observed, 13/15 passed (the older-read case below fails
// too):
//   x a flow closed while Tonight's flush is out: nothing is written for it and
//     Tonight is not left loading: Tonight's answer was written for a flow that
//     is no longer open, or the panel is left loading
//     expected "tonight null, loading false"
//     got      "tonight set, loading false"
await test("a flow closed while Tonight's flush is out: nothing is written for it and Tonight is not left loading", async () => {
  const s = slice();
  await s.a.flowsOpen("f1");
  ownersEdit(s);
  const fetching = s.a.flowsFetchTonight();
  await until(() => puts.length === 1, "the flush's PUT");
  // The operator leaves: the close's own save is the PUT already out (carried
  // by nothing: the edit is the same graph), so it waits on it.
  const closing = s.a.flowsCloseEditor();
  await settle();
  answerPut(puts[0], 2);
  if (puts[1]) answerPut(puts[1], 3);
  await closing;
  await fetching;
  eq(s.flows.record, null, "precondition: the editor closed");
  eq(`tonight ${s.flows.tonight === null ? "null" : "set"}, loading ${s.flows.tonightLoading}`,
    "tonight null, loading false",
    "Tonight's answer was written for a flow that is no longer open, or the panel is left loading");
});

// MUTANT "the payload in hand is kept while the flush runs" (flowsSlice.ts: the
// `...(start.dirty && !start.record?.readonly ? { tonight: null } : {}),` line
// of flowsFetchTonight removed). Observed, 14/15 passed:
//   x the answer in hand describes the flow as last saved, so a flush of an edit
//     clears it rather than show it while the edit is being saved: Tonight kept
//     drawing the old flow's story while its replacement was being saved
//     expected "cleared"
//     got      "still the old answer while the PUT was out"
await test("the answer in hand describes the flow as last saved, so a flush of an edit clears it rather than show it while the edit is being saved", async () => {
  const s = slice();
  await s.a.flowsOpen("f1");
  await s.a.flowsFetchTonight();
  eq(String((s.flows.tonight as any)?.brief), "target", "precondition: the first read describes the one block");
  ownersEdit(s);
  const fetching = s.a.flowsFetchTonight();
  await until(() => puts.length === 1, "the flush's PUT");
  eq(s.flows.tonight === null ? "cleared" : "still the old answer while the PUT was out", "cleared",
    "Tonight kept drawing the old flow's story while its replacement was being saved");
  answerPut(puts[0], 2);
  await fetching;
  eq(String((s.flows.tonight as any)?.brief), "cycle,cycle", "the new answer did not land");
});

// MUTANT "a superseded read still writes" (flowsSlice.ts: the `if (ticket !==
// tonightTicket) return true;` line of `superseded` removed). Observed, 14/15
// passed (the no-stale-guard mutant above fails it too):
//   x an older Tonight read that lands after a newer one started does not end
//     the newer one's loading: the older read cleared the flag while the newer
//     read, the one that owns it, was still out
//     expected "still loading"
//     got      "loading ended by the older read"
await test("an older Tonight read that lands after a newer one started does not end the newer one's loading", async () => {
  const s = slice();
  await s.a.flowsOpen("f1");
  holdTonight = true;
  const older = s.a.flowsFetchTonight();
  await until(() => heldTonight.length === 1, "the older read of the route");
  const newer = s.a.flowsFetchTonight();
  await until(() => heldTonight.length === 2, "the newer read of the route");
  heldTonight[0]();
  await older;
  eq(s.flows.tonightLoading ? "still loading" : "loading ended by the older read", "still loading",
    "the older read cleared the flag while the newer read, the one that owns it, was still out");
  heldTonight[1]();
  await newer;
  eq(s.flows.tonightLoading, false, "the newer read did not end its own loading");
});

// MUTANT "a save already out is not waited for" (flowsSlice.ts: the `if
// (savingPromise) await savingPromise;` line of saveBeforeRead removed).
// Observed, 14/15 passed:
//   x Tonight pressed while a SAVE is out waits for it, then sends the edit made
//     meanwhile: one PUT at a time: two PUTs were in flight together: their
//     answers can arrive out of order and put the older record back
//     expected "1 PUT out, then a second carrying the edit made during the first"
//     got      "2 PUTs out at once"
await test("Tonight pressed while a SAVE is out waits for it, then sends the edit made meanwhile: one PUT at a time", async () => {
  const s = slice();
  await s.a.flowsOpen("f1");
  s.a.flowsSetParam("t1", "name", "M 31");
  const saving = s.a.flowsSave();
  eq(puts.length, 1, "precondition: the SAVE sent one PUT");
  s.a.flowsSetParam("t1", "name", "M 33");
  const fetching = s.a.flowsFetchTonight();
  await settle();
  eq(puts.length === 1 ? "1 PUT out, then a second carrying the edit made during the first" : `${puts.length} PUTs out at once`,
    "1 PUT out, then a second carrying the edit made during the first",
    "two PUTs were in flight together: their answers can arrive out of order and put the older record back");
  answerPut(puts[0], 2);
  await until(() => puts.length === 2, "the second PUT");
  eq(String(puts[1].flow.graph.nodes.find((n: any) => n.id === "t1")?.params?.name), "M 33",
    "the second PUT did not carry the edit made while the first was out");
  answerPut(puts[1], 3);
  await saving;
  await fetching;
  eq(s.flows.dirty, false, "both edits were saved and the flow is still marked unsaved");
});

// MUTANT "an example's answer is cleared too" (flowsSlice.ts: `&&
// !start.record?.readonly` removed from the clearing write of flowsFetchTonight).
// Observed, 14/15 passed:
//   x an edited example keeps its answer in hand while Tonight is re-read: its
//     edits are never saved, so nothing is about to replace the flow it
//     describes: an example's story was blanked for a save that never happens
//     expected "kept"
//     got      "cleared"
await test("an edited example keeps its answer in hand while Tonight is re-read: its edits are never saved, so nothing is about to replace the flow it describes", async () => {
  const s = slice();
  readonlyRecord = true;
  await s.a.flowsOpen("f1");
  await s.a.flowsFetchTonight();
  eq(String((s.flows.tonight as any)?.brief), "target", "precondition: the first read describes the example");
  ownersEdit(s);
  holdTonight = true;
  const fetching = s.a.flowsFetchTonight();
  await until(() => heldTonight.length === 1, "the second read of the route");
  eq(s.flows.tonight === null ? "cleared" : "kept", "kept",
    "an example's story was blanked for a save that never happens");
  for (const release of heldTonight) release();
  await fetching;
  eq(puts.length, 0, "an example was PUT");
});

// ============================================================== 2. RUN

// MUTANT "flowsRun skips the flush" (flowsSlice.ts: the `await
// saveBeforeRead();` ahead of `const after = get().flows;` in flowsRun made
// `void 0;`). Observed, 10/15 passed (the cases that wait for the PUT time out):
//   x RUN on an edited flow waits for the save: the run is not posted until the
//     PUT has settled, and what it runs is the canvas: RUN went to the rig with
//     the edit still unsaved: it would have started the stored flow
//     expected "PUT"
//     got      ""
await test("RUN on an edited flow waits for the save: the run is not posted until the PUT has settled, and what it runs is the canvas", async () => {
  const s = slice();
  await s.a.flowsOpen("f1");
  ownersEdit(s);
  const running = s.a.flowsRun();
  await settle();
  eq(events.filter((e) => e !== "compile").join(", "), "PUT",
    "RUN went to the rig with the edit still unsaved: it would have started the stored flow");
  answerPut(puts[0], 2);
  const answer = await running;
  eq(answer, null, "the run did not start");
  eq(events.filter((e) => e !== "compile").join(", "), "PUT, run", "the run was not posted after the save");
  eq(describe(stored?.graph), "cycle,cycle", "the rig holds a flow that is not the canvas when the run starts");
  eq(s.flows.run.phase, "running", "the run's optimistic phase was not set");
});

// RUN does not wait for a compile: the server compiles the STORED record, so a
// client compile gives the run nothing and would put a round trip on the press.
// The compile is held open for the whole case, so a RUN that waited on it never
// reaches the rig, with no clock in the assertion.
// MUTANT "RUN compiles before it posts" (flowsSlice.ts: `await
// get().flowsCompile();` added after the save in flowsRun). Observed, 14/15
// passed:
//   x RUN does not wait on a client compile of the flow: the run is posted as
//     soon as the save settles: timed out waiting for RUN to be posted while the
//     compile is still out
await test("RUN does not wait on a client compile of the flow: the run is posted as soon as the save settles", async () => {
  const s = slice();
  await s.a.flowsOpen("f1");
  ownersEdit(s);
  holdCompile = true;
  const running = s.a.flowsRun();
  await until(() => puts.length === 1, "the flush's PUT");
  answerPut(puts[0], 2);
  await until(() => runCalls === 1, "RUN to be posted while the compile is still out");
  eq(heldCompiles.length > 0 ? "compile still out" : "no compile was asked", "compile still out",
    "precondition: the save's own compile is the one held");
  for (const release of heldCompiles) release();
  await running;
});

// MUTANT "the refusal removed" (flowsSlice.ts: `if (after.dirty) {` in flowsRun
// made `if (after.dirty && false) {`). Observed, 12/15 passed (this case, the
// example case and the edit-during-the-PUT case):
//   x RUN on a flow whose save failed refuses, logs why, and posts nothing: a
//     flow that did not save was started: the rig ran the stored flow under an
//     edit that was on screen
//     expected "null, 0 runs, bad: could not start: this flow has unsaved changes that did not save"
//     got      "null, 1 runs, "
await test("RUN on a flow whose save failed refuses, logs why, and posts nothing", async () => {
  const s = slice();
  await s.a.flowsOpen("f1");
  ownersEdit(s);
  const running = s.a.flowsRun();
  await until(() => puts.length === 1, "the flush's PUT");
  puts[0].reject(new Error("HTTP 422: a stage has no name"));
  const answer = await running;
  const refusal = logLines(s.flows).filter((l) => l.startsWith("bad: could not start"));
  eq(`${answer}, ${runCalls} runs, ${refusal.join(" | ")}`,
    "null, 0 runs, bad: could not start: this flow has unsaved changes that did not save",
    "a flow that did not save was started: the rig ran the stored flow under an edit that was on screen");
  eq(s.flows.dirty, true, "the failed save cleared dirty");
  eq(s.flows.run.phase, "idle", "the refused run still set the optimistic run phase");
});

// MUTANT "example refused with the failed-save sentence" (flowsSlice.ts: the
// choice of sentence in flowsRun's refusal made `const detail =
// RUN_NOT_SAVED_REFUSAL;`). Observed, 14/15 passed:
//   x RUN on an edited read-only example refuses with the example's own
//     sentence, sends no PUT, and posts nothing: an edited example was run, or
//     was refused in words that send the operator to a SAVE it does not have
//     expected "no PUT, 0 runs, bad: could not start: This example flow cannot be saved, so RUN would start the stored version, not the one drawn here. Leave the flow and open it again to drop these edits."
//     got      "no PUT, 0 runs, bad: could not start: this flow has unsaved changes that did not save"
await test("RUN on an edited read-only example refuses with the example's own sentence, sends no PUT, and posts nothing", async () => {
  const s = slice();
  readonlyRecord = true;
  await s.a.flowsOpen("f1");
  eq(s.flows.record?.readonly, true, "precondition: the record is an example");
  ownersEdit(s);
  const answer = await s.a.flowsRun();
  const refusal = logLines(s.flows).filter((l) => l.startsWith("bad: could not start"));
  eq(`${answer === null ? "no PUT" : "answer"}${puts.length ? ", PUT" : ""}, ${runCalls} runs, ${refusal.join(" | ")}`,
    `no PUT, 0 runs, bad: could not start: ${RUN_UNSAVED_EXAMPLE_REASON}`,
    "an edited example was run, or was refused in words that send the operator to a SAVE it does not have");
});

// An edit made while the flush's PUT is out is not on the rig when the PUT
// settles (#215 keeps it dirty), so RUN refuses: the press was for a flow that
// is no longer the one on screen. Killed by the "refusal removed" mutant:
//   x RUN refuses when an edit lands while its save is out: the rig would start
//     the flow before that edit: RUN started the flow as saved, which lacks the
//     block drawn while the save was out
//     expected "0 runs, dirty true"
//     got      "1 runs, dirty true"
await test("RUN refuses when an edit lands while its save is out: the rig would start the flow before that edit", async () => {
  const s = slice();
  await s.a.flowsOpen("f1");
  ownersEdit(s);
  const running = s.a.flowsRun();
  await until(() => puts.length === 1, "the flush's PUT");
  s.a.flowsAddNode("capture", { x: 300, y: 0 });
  answerPut(puts[0], 2);
  await running;
  eq(`${runCalls} runs, dirty ${s.flows.dirty}`, "0 runs, dirty true",
    "RUN started the flow as saved, which lacks the block drawn while the save was out");
});

// CONTROL: a clean flow runs at once, one POST and no PUT. It keeps the refusal
// honest: a refusal that fired on every flow would pass every case above.
// MUTANT "the refusal fires on every flow" (flowsSlice.ts: `if (after.dirty) {`
// made `if (true) {`). Observed, 12/15 passed:
//   x CONTROL: a clean flow runs at once, with no PUT: a flow with nothing to
//     save was held, saved or refused
//     expected "null, 0 PUTs, 1 runs"
//     got      "null, 0 PUTs, 0 runs"
await test("CONTROL: a clean flow runs at once, with no PUT", async () => {
  const s = slice();
  await s.a.flowsOpen("f1");
  const answer = await s.a.flowsRun();
  eq(`${answer}, ${puts.length} PUTs, ${runCalls} runs`, "null, 0 PUTs, 1 runs",
    "a flow with nothing to save was held, saved or refused");
  eq(s.flows.run.phase, "running", "the optimistic phase was not set");
});

// A flow closed or replaced while RUN's save is out is not started: the operator
// left it, and an unattended night is the wrong thing to start from a screen
// that is gone.
// MUTANT "RUN posts for the flow that was open" (flowsSlice.ts: `if
// (after.record?.id !== id) {` in flowsRun made `if (false) {`). Observed, 14/15
// passed:
//   x RUN while the flow is closed during its save starts nothing: a flow the
//     operator had left was started
//     expected "0 runs"
//     got      "1 runs"
await test("RUN while the flow is closed during its save starts nothing", async () => {
  const s = slice();
  await s.a.flowsOpen("f1");
  ownersEdit(s);
  const running = s.a.flowsRun();
  await until(() => puts.length === 1, "the flush's PUT");
  const closing = s.a.flowsCloseEditor();
  await settle();
  answerPut(puts[0], 2);
  if (puts[1]) answerPut(puts[1], 3);
  await closing;
  await running;
  eq(s.flows.record, null, "precondition: the editor closed");
  eq(`${runCalls} runs`, "0 runs", "a flow the operator had left was started");
  eq(toasts.some((t) => t.title === "Run not started"), true, "the press that started nothing said nothing");
});

// ------------------------------------------------------------------- report
const total = passed + failed;
console.log(`w14FlowsFlushBeforeRead.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) (globalThis as any).process.exitCode = 1;
export default { passed, failed, total };
export { passed, failed, total };
