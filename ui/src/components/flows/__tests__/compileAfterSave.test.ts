// compileAfterSave.test.ts - a successful save refreshes the compile answer,
// and the answer says which graph it describes (#356; spec 2026-09-23 flows
// mosaic, 1.4 "its count withheld while stale", Revision 8 row 3).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/__tests__/compileAfterSave.test.ts   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT. `flowsCompile` was called from `flowsOpen` and from the modal's
// DONE (`flowsApplyFraming`) and from nothing else, so after any other edit
// and its save `flows.compiled` described the graph as it was opened. Every
// reader of it went stale: the loss marks on both canvases, the checks pill,
// the PLAN tab, and the loop wire's chip. The chip guards itself two ways (no
// count while `dirty`, none when the entry's grid is not the block's), and
// one case still got through: a skip-list edit, then SAVE. The save clears
// `dirty` and leaves the grid alone, so the chip drew the opened graph's
// live-panel count until the flow was reopened. And nothing on the answer
// said which graph it was for, so no reader could tell.
//
// WHAT IS GRADED, through the real slice behind a miniature store:
//
//   - a successful save starts ONE compile, of the graph the store holds
//     once the save's answer is written, and does not wait for it;
//   - `flows.compiled.from` is the graph object the compile SENT, so an
//     edit made inside the compile's round trip leaves an answer
//     `compiledIsCurrent` calls stale;
//   - with two compiles in flight (DONE, then SAVE), an older answer never
//     replaces a newer one, and the checking flag stays up until the newest
//     settles, whether the older one answers or fails;
//   - an answer for a flow no longer open is not written: a save's compile
//     can now outlive the editor it was started in (a close saves first),
//     and one that landed after sign-out would put the rig's plan behind the
//     login screen; the newest compile still brings the checking flag down
//     when its answer is dropped;
//   - the controls: a failed save, a save with nothing to send, and a save
//     answered after another flow opened each compile nothing;
//   - and, since an open saves a dirty flow first (#450), that it does not
//     send again a SAVE already carrying the graph on screen, and does save
//     an edit made after that SAVE went out before it reads the other flow.
//
// The fake server compile below answers FOR THE GRAPH IT WAS SENT (each
// TARGET's grid and skip), so a stale answer and a fresh one differ in the
// number the loop chip prints, and the chip is read through the real
// `loopChip`.
//
// Every mutant below was run in a private scratch copy of ui/, never in the
// shared tree (#254), and the failure it produced is quoted verbatim.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------- globals first
// The slice imports lib/flowsApi -> lib/api -> lib/base, and base.ts reads
// `window.location.pathname` at module scope. Nothing here fakes behaviour the
// tests then assert on; every request is replaced below.
(globalThis as any).window = { location: { pathname: "/", origin: "http://local" } };
(globalThis as any).localStorage = { getItem: () => null, setItem() {}, removeItem() {} };

const { createFlowsActions, FLOWS_INIT, compiledIsCurrent } = await import("../flowsSlice");
const { flowsApi } = await import("../../../lib/flowsApi");
const { NODE_DEFS } = await import("../nodeDefs");
const { loopChip, LOOP_CHIP_WORDS } = await import("../targetSummary");
type FlowsHost = import("../flowsSlice").FlowsHost;
type FlowsState = import("../flowsSlice").FlowsState;
type FlowGraphRec = import("../flowsTypes").FlowGraphRec;

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
/** Lets every settled stub resume the slice's awaits. */
const flush = () => new Promise<void>((r) => setTimeout(r, 0));

// ------------------------------------------------------------ the fake server

/** "2-3, 1-1" as the panels it names, [[2, 3], [1, 1]]. The stand-in for
 *  compile.py `parse_skip`: its grammar is not what is graded here, only
 *  that the answer describes the graph that was sent. */
function skipOf(v: unknown): number[][] {
  return String(v ?? "").split(",").map((s) => s.trim()).filter(Boolean)
    .map((s) => s.split("-").map(Number));
}

/** The compile answer for `graph`: one plan entry per TARGET, with its grid
 *  and parsed skip where it has more than one panel (compile.py
 *  `_target_entry`'s `mosaic`), and each TARGET's `name` and `counts` so a
 *  test can see which graph an answer is for. */
function answerFor(graph: FlowGraphRec) {
  const targets = graph.nodes.filter((n) => n.type === "target").map((n) => {
    const rows = Number(n.params.rows);
    const cols = Number(n.params.cols);
    return {
      node_id: n.id,
      name: n.params.name,
      counts: n.params.counts,
      mosaic: rows * cols > 1 ? { rows, cols, skip: skipOf(n.params.skip) } : null,
    };
  });
  return { plan: { targets }, structural: [], issues: [], unmapped: [] };
}

/** Every compile the slice started, in order. `holdCompiles` keeps each open
 *  until a test answers or fails it; otherwise it answers at once. */
interface Compile {
  graph: FlowGraphRec;
  answer: () => void;
  fail: (e: Error) => void;
}
let compiles: Compile[] = [];
let holdCompiles = false;
(flowsApi as any).compileDraft = (graph: FlowGraphRec) => new Promise((resolve, reject) => {
  const c: Compile = { graph, answer: () => resolve(answerFor(graph)), fail: reject };
  compiles.push(c);
  if (!holdCompiles) c.answer();
});

/** Every PUT the slice sent. `holdPuts` keeps each open; `failPuts` refuses
 *  it; otherwise the server stores what it was sent and answers with it,
 *  plus `putAnswer`'s keys. */
interface Put { flow: any; answer: () => void }
let puts: Put[] = [];
let holdPuts = false;
let failPuts = false;
let putAnswer: Record<string, unknown> = {};
(flowsApi as any).save = (id: string, flow: any) => new Promise((resolve, reject) => {
  const p: Put = {
    flow,
    answer: () => resolve(JSON.parse(JSON.stringify({ ...flow, id, migrated: [], ...putAnswer }))),
  };
  puts.push(p);
  if (failPuts) reject(new Error("the flow could not be saved"));
  else if (!holdPuts) p.answer();
});
(flowsApi as any).progress = async () => { throw new Error("Not Found"); };
(flowsApi as any).list = async () => [];
(flowsApi as any).folders = async () => [];

/** M31 at three columns by two rows, nothing skipped, and the dashed loop
 *  wire from the lane's tail (the FILTER CYCLE) back to "next panel". */
function mosaic(counts = "Accepted subs"): FlowGraphRec {
  return {
    nodes: [
      { id: "t", type: "target", x: 0, y: 0,
        params: { ...NODE_DEFS.target.params, name: "M31", rows: 2, cols: 3, skip: "", counts } },
      { id: "cy", type: "cycle", x: 250, y: 0, params: { ...NODE_DEFS.cycle.params } },
    ],
    edges: [
      { id: "lane", from: "t", fromPort: "target", to: "cy", toPort: "run" },
      { id: "loop", from: "cy", fromPort: "pass", to: "t", toPort: "next" },
    ],
  };
}
let graphFor: (id: string) => FlowGraphRec = () => mosaic();
(flowsApi as any).get = async (id: string) => ({
  id, name: `Flow ${id}`, folder: "My flows", tagline: "", graph: graphFor(id),
  created_ts: 1, updated_ts: 1, last_run: null, last_result: "", readonly: false,
});

/** The slice alone behind a miniature store, every stub reset. */
function slice() {
  let state: FlowsHost;
  const set = (fn: (s: FlowsHost) => Partial<FlowsHost>) => {
    state = { ...state, ...fn(state) } as FlowsHost;
  };
  const actions = createFlowsActions(set, () => state);
  state = { ...actions, flows: { ...FLOWS_INIT } } as FlowsHost;
  compiles = [];
  puts = [];
  holdCompiles = false;
  holdPuts = false;
  failPuts = false;
  putAnswer = {};
  graphFor = () => mosaic();
  return {
    get flows(): FlowsState { return state.flows; },
    a: actions,
    /** The sign-out gate's reset (lib/authGate.ts `clearedRigState`). */
    signOut: () => {
      state = { ...state, flows: { ...FLOWS_INIT, graph: { nodes: [], edges: [] },
                                   ui: { ...FLOWS_INIT.ui } } };
    },
  };
}

/** The loop wire's chip as both canvases draw it: the real `loopChip`, fed
 *  what FlowWireLayer.tsx and FlowWires.tsx feed it, which since S7 (#356) is
 *  `!compiledIsCurrent(f)` for its withheld count. The S7 integration moved
 *  this helper off `f.dirty`, which the layers no longer read. */
function chip(f: FlowsState): string | null {
  const edge = f.graph.edges.find((e) => e.id === "loop")!;
  return loopChip(f.graph, edge, f.compiled?.plan ?? null, !compiledIsCurrent(f));
}
const panels = (n: number) => `${LOOP_CHIP_WORDS} · ${n} panels`;

/** SAVE, awaited only as far as its PUT, for the cases that hold compiles
 *  open. A save that waited for its own compile would otherwise leave each
 *  of them on an await nothing settles, and the file would end on node's
 *  "unsettled top-level await" with no tally and no sentence saying why. */
async function savedWithoutItsCompile(s: ReturnType<typeof slice>): Promise<void> {
  const saved = await Promise.race([
    s.a.flowsSave().then(() => true),
    flush().then(flush).then(() => false),
  ]);
  if (!saved) throw new Error("the save is still waiting for its compile, which is held open");
}

/** `p`, or a failure saying what was left waiting: an open that waited on a
 *  PUT these cases hold would otherwise end the file on node's "unsettled
 *  top-level await", with no tally and no sentence saying why. */
async function settledOrHeld(p: Promise<unknown>, what: string): Promise<void> {
  const done = await Promise.race([p.then(() => true), flush().then(flush).then(flush).then(() => false)]);
  if (!done) throw new Error(`${what} is still waiting (on a PUT this case holds)`);
}

/** A flow opened (its compile answered), then its skip list edited. */
async function openedAndSkipped() {
  const s = slice();
  await s.a.flowsOpen("f1");
  s.a.flowsSetParam("t", "skip", "2-3");
  return s;
}

// ================================================= 1. THE SAVE COMPILES

// MUTANT "no compile after save" (flowsSave: the `void get().flowsCompile()`
// after the save's answer deleted). Observed (compileAfterSave.test: 3/14
// passed; every case but the three controls that expect no compile failed,
// nine of them on their "the save started its compile" precondition):
//   x a successful save starts one compile, of the graph it saved, and keeps the answer as that graph's: the save started no compile, so flows.compiled still describes the graph as it was opened
//   expected 1
//   got      0
await test("a successful save starts one compile, of the graph it saved, and keeps the answer as that graph's", async () => {
  const s = await openedAndSkipped();
  const opened = s.flows.compiled;
  compiles = [];
  await s.a.flowsSave();
  await flush();
  eq(puts.length, 1, "precondition: the save sent one PUT");
  eq(s.flows.dirty, false, "precondition: the save completed");
  eq(compiles.length, 1,
    "the save started no compile, so flows.compiled still describes the graph as it was opened");
  eq(JSON.stringify(compiles[0].graph), JSON.stringify(puts[0].flow.graph),
    "the save's compile is not of the graph the save sent");
  eq(compiles[0].graph === s.flows.graph, true,
    "the save's compile is not of the graph the store now holds");
  eq(s.flows.compiled !== opened && s.flows.compiled?.from === s.flows.graph, true,
    "the save's answer is not in hand as the answer for the graph on screen");
  eq(compiledIsCurrent(s.flows), true, "the fresh answer reads as stale");
});

// The one case the chip's own guards let through (#356): a skip-list edit,
// then SAVE. `dirty` is cleared and the grid is unchanged, so only a fresh
// answer can put the right count on the chip.
//
// MUTANT "no compile after save". Observed (compileAfterSave.test: 3/14
// passed):
//   x the loop chip's skip-list-then-save case shows the fresh live-panel count: the chip still counts the panels of the graph as it was opened
//   expected "every pass: next panel · 5 panels"
//   got      "every pass: next panel · 6 panels"
// That was with `chip()` fed `f.dirty`, as the layers were before S7. Fed
// `!compiledIsCurrent(f)`, as they are now, the old answer describes a graph
// no longer on screen and the chip withholds its count rather than showing a
// wrong one. The same mutant in the S7 integration's private copy (the
// session scratchpad's S7-INTEG-r2-mut, from a byte backup, sha256 checked
// after), 5/16:
//   x the loop chip's skip-list-then-save case shows the fresh live-panel count: the chip does not count the live panels of the graph SAVE stored
//   expected "every pass: next panel · 5 panels"
//   got      "every pass: next panel"
await test("the loop chip's skip-list-then-save case shows the fresh live-panel count", async () => {
  const s = slice();
  await s.a.flowsOpen("f1");
  eq(chip(s.flows), panels(6), "precondition: the opened 3x2 counts six live panels");
  s.a.flowsSetParam("t", "skip", "2-3");
  eq(chip(s.flows), LOOP_CHIP_WORDS, "precondition: no count while the skip edit is unsaved");
  await s.a.flowsSave();
  await flush();
  eq(chip(s.flows), panels(5), "the chip does not count the live panels of the graph SAVE stored");
});

// A save that switched the counts writes the server's switch into the graph
// the editor holds (`acceptCounts`), a new graph object. The compile must be
// of THAT graph, the one the server stored, or its answer describes a graph
// that is no longer on screen from the moment it lands.
//
// MUTANT "compile before the answer is written" (flowsSave: the compile
// started before the `set` that writes the save's answer, so it sends the
// graph from before the switch). Observed (compileAfterSave.test: 13/14
// passed):
//   x a save that switched the counts compiles the graph as the server stored it: the compile sent the graph from before the counts switch; its answer is stale the moment it lands
//   expected "Accepted subs, current true"
//   got      "Every sub taken, current false"
await test("a save that switched the counts compiles the graph as the server stored it", async () => {
  const s = slice();
  graphFor = () => mosaic("Every sub taken");
  await s.a.flowsOpen("f1");
  s.a.flowsSetParam("t", "skip", "2-3");
  putAnswer = { migrated: [{ key: "counts", note: "counts: switched to Accepted subs on 1 block" }] };
  compiles = [];
  await s.a.flowsSave();
  await flush();
  eq(s.flows.graph.nodes[0].params.counts, "Accepted subs", "precondition: the switch reached the canvas");
  eq(compiles.length, 1, "precondition: the save started one compile");
  const counted = (s.flows.compiled?.plan as any)?.targets?.[0]?.counts;
  eq(`${counted}, current ${compiledIsCurrent(s.flows)}`, "Accepted subs, current true",
    "the compile sent the graph from before the counts switch; its answer is stale the moment it lands");
});

// MUTANT "save awaits its compile" (flowsSave: `await get().flowsCompile()`
// for `void get().flowsCompile()`). Observed (compileAfterSave.test: 6/14
// passed; the seven cases that hold a compile open fail too, each through
// `savedWithoutItsCompile`: "the save is still waiting for its compile,
// which is held open"):
//   x a save does not wait for its compile: the save waited for its compile, which would hold a close (a close saves first) for up to the api's 15 s
//   expected "saved"
//   got      "still waiting"
await test("a save does not wait for its compile", async () => {
  const s = await openedAndSkipped();
  holdCompiles = true;
  compiles = [];
  const saved = s.a.flowsSave().then(() => "saved");
  const first = await Promise.race([saved, flush().then(() => flush()).then(() => "still waiting")]);
  eq(compiles.length, 1, "precondition: the save started its compile");
  eq(first, "saved",
    "the save waited for its compile, which would hold a close (a close saves first) for up to the api's 15 s");
  compiles[0].answer();
  await flush();
});

// ============================================== 2. CONTROLS: NO COMPILE

// MUTANT "compile in finally" (flowsSave: the compile moved into a `finally`
// after the try, so it runs whether the PUT succeeded or not). Observed
// (compileAfterSave.test: 12/14 passed; this case and the stale-completion
// control below):
//   x control: a failed save compiles nothing and keeps the answer in hand: a refused save started a compile
//   expected 0
//   got      1
await test("control: a failed save compiles nothing and keeps the answer in hand", async () => {
  const s = await openedAndSkipped();
  const before = s.flows.compiled;
  failPuts = true;
  compiles = [];
  await s.a.flowsSave();
  await flush();
  eq(s.flows.libraryError, "the flow could not be saved", "precondition: the save was refused");
  eq(s.flows.dirty, true, "precondition: the edit is still unsaved");
  eq(compiles.length, 0, "a refused save started a compile");
  eq(s.flows.compiled === before, true, "a refused save replaced the answer in hand");
});

// Nothing to send (a clean flow, or a read-only Example) is not a save.
// "compile in finally" leaves this case green, since the guard returns
// before the try; the mutant aimed at it moves the compile above the guard.
//
// MUTANT "compile before the guard" (flowsSave: the compile started as the
// action's first statement). Observed (compileAfterSave.test: 9/14 passed;
// this case, the failed-save control, the counts-switch case and both closed
// flow controls failed):
//   x control: a save with nothing to send, or of a read-only flow, compiles nothing: a save that sent nothing started a compile
//   expected "0 PUTs, 0 compiles"
//   got      "0 PUTs, 1 compiles"
await test("control: a save with nothing to send, or of a read-only flow, compiles nothing", async () => {
  const s = slice();
  await s.a.flowsOpen("f1");
  compiles = [];
  await s.a.flowsSave();                       // clean: nothing to send
  await flush();
  eq(`${puts.length} PUTs, ${compiles.length} compiles`, "0 PUTs, 0 compiles",
    "a save that sent nothing started a compile");
  const ro = slice();
  await ro.a.flowsOpen("f1");
  ro.a.flowsSetParam("t", "skip", "1-1");
  (ro.flows.record as any).readonly = true;    // an Example, edited on the canvas
  compiles = [];
  await ro.a.flowsSave();
  await flush();
  eq(`${puts.length} PUTs, ${compiles.length} compiles`, "0 PUTs, 0 compiles",
    "a read-only flow's save started a compile");
});

// A PUT that answers after ANOTHER flow was opened belongs to a record no
// longer open (#215's stale completion). The other flow's open compiled its
// own graph; a compile here would be a second, of the same graph, for
// nothing.
//
// SINCE #450 AN OPEN SAVES A DIRTY OPEN RECORD FIRST, but not one whose SAVE
// is already out carrying exactly the graph on screen: that PUT is not sent
// twice. The case after this one is the other half: an edit made after the
// PUT went out is saved again.
//
// MUTANT "compile before the stale check" (flowsSave: the compile started
// right after the PUT answers, above the `cur.id !== record.id` return).
// Observed (compileAfterSave.test: 12/14 passed; the counts-switch case
// failed too, the compile now preceding the answer's write):
//   x control: a save answered after another flow opened compiles nothing for it: the stale save compiled the other flow's graph a second time
//   expected 1
//   got      2
// Re-run on the #450 tree in scratchpad S7-USLICE-mut, the same two cases
// red with the same lines (compileAfterSave.test: 14/16 passed).
//
// RE-PINNED (W2 integration, #500/#162 backlog WP-16 (b), owner-approved
// 2026-09-30): a carried save is now AWAITED by `flowsOpen` rather than fired
// and forgotten -- this file's own comment above already named the shape of
// that fix and the hang it produces if the held PUT is answered too late
// ("MUTANT 'an in-flight save is never trusted'", observed when an earlier
// draft of the fix made `flowsOpen` wait without this file's `puts[0]
// .answer()` moving first). So `puts[0].answer()` now runs BEFORE the open
// is awaited, which changes what this control proves: the PUT is no longer
// "the stale completion" at all once answered -- f1 is still open when it
// settles, so `flowsSave`'s own `cur.id !== record.id` guard sees them equal
// and its own (correct, non-stale) compile for f1 runs BEFORE `flowsOpen`
// proceeds to read and compile f2. Two compiles are now the right answer,
// one per flow, in order; the old mutant's signature (a SECOND compile for
// the flow that is no longer open) cannot arise here any more because the
// open cannot start until the save it is carrying has already finished.
await test("control: a save answered after another flow opened compiles nothing for it", async () => {
  const s = await openedAndSkipped();
  holdPuts = true;
  const saving = s.a.flowsSave();
  eq(puts.length, 1, "precondition: the PUT is in flight");
  compiles = [];
  puts[0].answer();
  await settledOrHeld(s.a.flowsOpen("f2"), "opening f2 once f1's carried save settled");
  await saving;
  await flush();
  eq(puts.length, 1, "the open sent f1's graph again although its SAVE was carrying it");
  eq(s.flows.record?.id, "f2", "precondition: f2 is the flow open");
  eq(compiles.length, 2,
    "f1's own save, no longer stale, compiles once for f1, then the open compiles once for f2");
});

// THE OTHER HALF (#450): an edit made after the SAVE went out is on no PUT.
// The open saves it, waits for that PUT, and only then reads the other flow.
//
// MUTANT "an in-flight save covers any graph" (flowsSlice.ts flowsOpen:
// `carried` stops comparing the graph, `saving.graph === get().flows.graph`
// dropped). Observed (compileAfterSave.test: 15/16 passed):
//   x an edit made after the SAVE went out is saved again before another flow opens: the open did not save the edit made after the SAVE went out
//   expected 2
//   got      1
// MUTANT "replace without saving" (flowsSlice.ts flowsOpen: the save-first
// block deleted, #450's own mutant) is red here with the same line and the
// next case's (compileAfterSave.test: 14/16 passed), and in
// hubSwitchSavesFlow.test.tsx and sendToWizardSheet.test.tsx.
await test("an edit made after the SAVE went out is saved again before another flow opens", async () => {
  const s = await openedAndSkipped();
  holdPuts = true;
  const saving = s.a.flowsSave();
  eq(puts.length, 1, "precondition: the PUT is in flight");
  s.a.flowsSetParam("t", "skip", "2-3, 1-1");        // on no PUT yet
  const opening = s.a.flowsOpen("f2");
  eq(puts.length, 2, "the open did not save the edit made after the SAVE went out");
  eq(puts[1].flow.graph.nodes.find((n: any) => n.id === "t")?.params.skip, "2-3, 1-1",
    "the open's PUT does not carry the later edit");
  eq(s.flows.record?.id, "f1", "f2 was read before the later edit's PUT answered");
  puts[1].answer();
  await settledOrHeld(opening, "the open, once the later edit's PUT answered");
  eq(s.flows.record?.id, "f2", "f2 did not open once the later edit was saved");
  puts[0].answer();
  await saving;
});

// A SAVE THAT FAILED CARRIES NOTHING (#450). Its PUT settled, so no open may
// take it for one still carrying the edit: the open saves again, and when
// that fails too it refuses, f1 still open with its edit.
//
// MUTANT "a settled save still counts as carrying" (flowsSlice.ts flowsSave:
// the `finally` that clears `saving` deleted). Observed (compileAfterSave
// .test: 15/16 passed):
//   x a SAVE that failed carries nothing: the next open saves again and refuses: the open did not save the edit a refused SAVE had carried
//   expected 2
//   got      1
await test("a SAVE that failed carries nothing: the next open saves again and refuses", async () => {
  const s = await openedAndSkipped();
  failPuts = true;
  await s.a.flowsSave();
  eq(s.flows.dirty, true, "precondition: the refused save left the edit unsaved");
  eq(puts.length, 1, "precondition: the save sent its PUT");
  await settledOrHeld(s.a.flowsOpen("f2"), "opening f2 over the refused save");
  eq(puts.length, 2, "the open did not save the edit a refused SAVE had carried");
  eq(s.flows.record?.id, "f1", "the open replaced f1 over an edit no save kept");
  eq(s.flows.graph.nodes.find((n) => n.id === "t")?.params.skip, "2-3", "f1's edit after the refused open");
});

// ================================= 3. THE ANSWER SAYS WHICH GRAPH IT IS FOR

// An edit made while the compile is in flight: the answer that lands is
// true of the graph that was SENT, and that is no longer the graph on
// screen. It must say so, or a reader draws last round's verdict as this
// one's.
//
// MUTANT "stale answer read as current" (flowsCompile: the answer stamped
// `from: get().flows.graph`, the graph on screen when it LANDS, in place of
// the graph the request sent). Observed (compileAfterSave.test: 12/14
// passed; the checking-flag case failed too, on "DONE's answer, the first to
// land, is not in hand as the answer for DONE's graph"):
//   x flows.compiled says which graph it describes: an edit inside the compile's round trip leaves the answer stale: the answer for the graph the save sent reads as the answer for the edit made while it was compiling
//   expected "from the sent graph: true, current: false"
//   got      "from the sent graph: false, current: true"
//
// MUTANT "current by existence" (compiledIsCurrent: `f.compiled != null`,
// the `from` never compared). Observed (compileAfterSave.test: 13/14
// passed):
//   x flows.compiled says which graph it describes: an edit inside the compile's round trip leaves the answer stale: the answer for the graph the save sent reads as the answer for the edit made while it was compiling
//   expected "from the sent graph: true, current: false"
//   got      "from the sent graph: true, current: true"
await test("flows.compiled says which graph it describes: an edit inside the compile's round trip leaves the answer stale", async () => {
  const s = await openedAndSkipped();
  holdCompiles = true;
  compiles = [];
  await savedWithoutItsCompile(s);
  eq(compiles.length, 1, "precondition: the save started its compile");
  const sent = compiles[0].graph;
  s.a.flowsSetParam("t", "skip", "2-3, 1-1");
  eq(s.flows.graph !== sent, true, "precondition: the edit replaced the graph");
  compiles[0].answer();
  await flush();
  eq(`from the sent graph: ${s.flows.compiled?.from === sent}, current: ${compiledIsCurrent(s.flows)}`,
    "from the sent graph: true, current: false",
    "the answer for the graph the save sent reads as the answer for the edit made while it was compiling");
  // CONTROL: before any compile nothing is current, and neither is an answer
  // held for another graph.
  eq(compiledIsCurrent({ compiled: null, graph: s.flows.graph }), false, "no answer read as current");
});

// ================================================= 4. TWO COMPILES IN FLIGHT

// DONE, then SAVE before DONE's compile answers: two compiles, of two
// graphs. If the older answer lands last it must not replace the newer, or
// the refresh this issue is about is undone by the answer it replaced.
//
// MUTANT "an older answer replaces a newer one" (flowsCompile: the `ticket >
// compileKept` test deleted, so every answer for the open flow is kept).
// Observed (compileAfterSave.test: 13/14 passed):
//   x an older answer never replaces a newer one: DONE's answer, landing after the save's, replaced it
//   expected "the save's graph, current true"
//   got      "DONE's graph, current false"
await test("an older answer never replaces a newer one", async () => {
  const s = slice();
  await s.a.flowsOpen("f1");
  holdCompiles = true;
  compiles = [];
  void s.a.flowsApplyFraming("t", { skip: "2-3" });   // DONE: compile 0
  s.a.flowsSetParam("t", "name", "M31 core");          // then an edit
  await savedWithoutItsCompile(s);                     // SAVE: compile 1
  eq(compiles.length, 2, "precondition: DONE and the save each started a compile");
  compiles[1].answer();
  await flush();
  compiles[0].answer();
  await flush();
  const which = s.flows.compiled?.from === compiles[1].graph ? "the save's graph"
    : s.flows.compiled?.from === compiles[0].graph ? "DONE's graph" : "neither";
  eq(`${which}, current ${compiledIsCurrent(s.flows)}`, "the save's graph, current true",
    "DONE's answer, landing after the save's, replaced it");
});

// The other order: DONE's answer first is kept (it is newer than what was in
// hand), and the checking flag stays up because the save's compile is still
// out.
//
// MUTANT "any answer clears the checking flag" (flowsCompile: `compiling:
// false` written by every answer, not only the newest compile's). Observed
// (compileAfterSave.test: 13/14 passed):
//   x the checking flag stays up until the newest compile settles, and an older answer landing first is kept: the flag came down while the save's compile was still out
//   expected true
//   got      false
await test("the checking flag stays up until the newest compile settles, and an older answer landing first is kept", async () => {
  const s = slice();
  await s.a.flowsOpen("f1");
  holdCompiles = true;
  compiles = [];
  void s.a.flowsApplyFraming("t", { skip: "2-3" });
  s.a.flowsSetParam("t", "name", "M31 core");
  await savedWithoutItsCompile(s);
  eq(compiles.length, 2, "precondition: DONE and the save each started a compile");
  compiles[0].answer();
  await flush();
  eq(s.flows.compiling, true, "the flag came down while the save's compile was still out");
  eq(s.flows.compiled?.from === compiles[0].graph, true,
    "DONE's answer, the first to land, is not in hand as the answer for DONE's graph");
  compiles[1].answer();
  await flush();
  eq(`compiling ${s.flows.compiling}, current ${compiledIsCurrent(s.flows)}`, "compiling false, current true",
    "the newest answer did not settle the flag, or is not the one in hand");
});

// The same order with DONE's compile FAILING: the failure is not the newest
// compile settling either, so the flag stays up for the save's compile, which
// is still out. The case above grades only the answer path; the catch keeps
// its own newest-only guard.
//
// MUTANT "an older failure clears the flag" (flowsCompile: the catch's
// `compiling: false` written on every failure, its `newest()` guard dropped).
// Observed (compileAfterSave.test: 13/14 passed):
//   x the checking flag stays up when an older compile fails while the newest is out: DONE's failed compile brought the flag down while the save's compile was still out
//   expected true
//   got      false
await test("the checking flag stays up when an older compile fails while the newest is out", async () => {
  const s = slice();
  await s.a.flowsOpen("f1");
  holdCompiles = true;
  compiles = [];
  void s.a.flowsApplyFraming("t", { skip: "2-3" });   // DONE: compile 0
  s.a.flowsSetParam("t", "name", "M31 core");
  await savedWithoutItsCompile(s);                     // SAVE: compile 1
  eq(compiles.length, 2, "precondition: DONE and the save each started a compile");
  compiles[0].fail(new Error("timed out"));
  await flush();
  eq(s.flows.compiling, true,
    "DONE's failed compile brought the flag down while the save's compile was still out");
  compiles[1].answer();
  await flush();
  eq(`compiling ${s.flows.compiling}, current ${compiledIsCurrent(s.flows)}`, "compiling false, current true",
    "the newest answer did not settle the flag, or is not the one in hand");
});

// =========================================== 5. A FLOW NO LONGER OPEN

// A close saves first, so a save's compile can outlive its editor; and the
// sign-out gate resets `flows` without waiting for anything in flight. The
// answer is the rig's plan (target names, the filters), which the gate
// exists to keep off the login screen.
//
// MUTANT "answer kept for a flow no longer open" (flowsCompile: the
// `get().flows.record?.id === record?.id` test dropped from the keep).
// Observed (compileAfterSave.test: 12/14 passed; the closed flow's answer
// control below failed too, on "a closed flow's answer was written"):
//   x an answer that lands after sign-out is not written behind the login screen: the plan of a flow that is no longer open was written after sign-out
//   expected null
//   got      "M31"
await test("an answer that lands after sign-out is not written behind the login screen", async () => {
  const s = await openedAndSkipped();
  holdCompiles = true;
  compiles = [];
  await savedWithoutItsCompile(s);
  eq(compiles.length, 1, "precondition: the save started its compile");
  s.signOut();
  compiles[0].answer();
  await flush();
  eq((s.flows.compiled?.plan as any)?.targets?.[0]?.name ?? null, null,
    "the plan of a flow that is no longer open was written after sign-out");
});

// A failed compile for a flow no longer open says nothing: the flow log is
// one strip for whichever flow is open, and "could not check this flow" on
// it would be about another flow.
//
// MUTANT "failure logged for a flow no longer open" (flowsCompile: the
// catch's same-flow return deleted). Observed (compileAfterSave.test: 13/14
// passed):
//   x control: a compile that fails after its flow was closed says nothing on the log: a closed flow's failed compile wrote on the log
//   expected 0
//   got      1
//
// MUTANT "a failed compile leaves the flag up" (flowsCompile: the catch's
// `compiling: false` write deleted). Observed (compileAfterSave.test: 13/14
// passed):
//   x control: a compile that fails after its flow was closed says nothing on the log: the checking flag stayed up after the last compile settled
//   expected false
//   got      true
await test("control: a compile that fails after its flow was closed says nothing on the log", async () => {
  const s = await openedAndSkipped();
  holdCompiles = true;
  compiles = [];
  await savedWithoutItsCompile(s);
  eq(compiles.length, 1, "precondition: the save started its compile");
  await s.a.flowsCloseEditor();
  const logs = s.flows.logs.length;
  compiles[0].fail(new Error("timed out"));
  await flush();
  eq(s.flows.logs.length - logs, 0, "a closed flow's failed compile wrote on the log");
  eq(s.flows.compiling, false, "the checking flag stayed up after the last compile settled");
});

// The same close with the compile ANSWERING: the answer is dropped, since its
// flow is gone, but it is still the newest compile settling, and the close
// does not touch the checking flag. Dropped without bringing the flag down,
// it would leave every reader of `compiling` (the PLAN tab, the #/next plan
// block, the sky sheet's flow card) saying the flow is being checked until
// some later compile happened to clear it.
//
// MUTANT "a dropped answer leaves the flag up" (flowsCompile: `if (!keep &&
// !newest()) return` made `if (!keep) return`, so an answer that is not kept
// returns before the flag is written). Observed (compileAfterSave.test: 13/14
// passed):
//   x control: a compile that answers after its flow was closed writes nothing and brings the flag down: the checking flag stayed up after the newest compile, dropped for a closed flow, settled
//   expected false
//   got      true
await test("control: a compile that answers after its flow was closed writes nothing and brings the flag down", async () => {
  const s = await openedAndSkipped();
  holdCompiles = true;
  compiles = [];
  await savedWithoutItsCompile(s);
  eq(compiles.length, 1, "precondition: the save started its compile");
  await s.a.flowsCloseEditor();
  eq(s.flows.compiling, true, "precondition: the save's compile is still out after the close");
  const before = s.flows.compiled;
  compiles[0].answer();
  await flush();
  eq(s.flows.compiled === before, true, "a closed flow's answer was written");
  eq(s.flows.compiling, false,
    "the checking flag stayed up after the newest compile, dropped for a closed flow, settled");
});

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`compileAfterSave.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) (globalThis as any).process.exitCode = 1;
export const result = { passed, failed, total };
export default result;
