// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w16FlowsUndo.test.ts - the flow editor can undo and redo an edit (#688,
// part 3 of 3; WP-117). Store level: no DOM. The buttons and the keys are
// graded in w16FlowsUndoDom.test.tsx and, for #/next, in
// next/hubs/session/flows/canvas/__tests__/w16FlowCanvasUndo.test.tsx.
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/__tests__/w16FlowsUndo.test.ts   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE OWNER'S REPORT. "We need to be dynamically saving flow changes as we go.
// Along with supporting undo options during an editing session." Parts 1 and 2
// (WP-86, WP-99) made Tonight and RUN read the canvas and the flow save
// itself; an editor that saves itself two seconds after the last edit also
// makes a slip permanent two seconds after it is made, so this part is what
// lets the operator take it back.
//
// THE RULINGS UNDER TEST (the orchestrator's, for this WP)
//
//   1. CAP 50. The 51st edit drops the oldest.
//   2. COALESCE a node dragged (a `flowsMoveNode` per pointer move) and
//      same-key param edits (a `flowsSetParam` per keystroke) when the next
//      comes within 800 ms of the last: one entry for the gesture. The flow's
//      name typed into the inspector is the same shape and joins them.
//   3. A SAVE DOES NOT CLEAR THE HISTORY: the autosave fires two seconds after
//      the last edit, and an undo that stopped at the last autosave would
//      reach back two seconds.
//   4. OPENING A FLOW DOES (so does closing the editor), and a wizard-made
//      flow arrives through an open, so it is not undoable.
//   5. UNDO MARKS THE FLOW DIRTY, so the autosave picks the undone graph up.
//   6. Ctrl/Cmd+Z in a text field stays the browser's: DOM files.
//
// FAKE TIME. The slice reads `Date.now` and `setTimeout` at call time, so this
// file replaces both on `globalThis` with a virtual clock (`advance`): the
// 800 ms is a value the tests assert, not a delay they sit through. The
// harness's own waits go through the real `setTimeout`, saved first.
//
// Every guarded case names the mutant it kills and quotes what that mutant
// produced when it was run from a byte-for-byte backup of flowsSlice.ts
// (restored and sha256-compared after each run, the mutant's marker grepped
// absent).

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------- globals first
(globalThis as any).window = {
  location: { pathname: "/", origin: "http://local" },
};
(globalThis as any).localStorage = {
  getItem: () => null, setItem() {}, removeItem() {},
};

// ---------------------------------------------------------------- fake time
const realSetTimeout = globalThis.setTimeout;
interface Armed { id: number; due: number; fn: () => void }
let armed: Armed[] = [];
let nextTimerId = 1;
let CLOCK = 1_700_000_000_000;
Date.now = () => CLOCK;
(globalThis as any).setTimeout = (fn: () => void, ms = 0): number => {
  const id = nextTimerId++;
  armed.push({ id, due: CLOCK + ms, fn });
  return id;
};
(globalThis as any).clearTimeout = (id?: number): void => {
  armed = armed.filter((a) => a.id !== id);
};
/** One macrotask of the REAL clock: every promise chain that can finish
 *  without a timer has finished when this resolves. */
const flush = () => new Promise<void>((r) => realSetTimeout(r, 0));
/** Move the virtual clock forward, running every timer that falls due on the
 *  way in order, and letting what each one started run to its next wait. */
async function advance(ms: number): Promise<void> {
  const end = CLOCK + ms;
  for (;;) {
    const next = armed.filter((a) => a.due <= end).sort((a, b) => a.due - b.due)[0];
    if (!next) break;
    armed = armed.filter((a) => a !== next);
    CLOCK = Math.max(CLOCK, next.due);
    next.fn();
    await flush();
  }
  CLOCK = end;
  await flush();
}
/** Move the clock without running a timer: for the cases with no autosave. */
const tick = (ms: number): void => { CLOCK += ms; };

const {
  createFlowsActions, FLOWS_INIT, COUNTS_SWITCHED_LINE,
} = await import("../flowsSlice");
const { flowsApi } = await import("../../../lib/flowsApi");
const { NODE_DEFS } = await import("../nodeDefs");
const { FLOW_HISTORY_CAP, FLOW_COALESCE_MS } = await import("../flowsTypes");
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

// ------------------------------------------------------------------- stubs
const OPENED = () => ({
  nodes: [
    { id: "d1", type: "dusk", x: 0, y: 0, params: { ...NODE_DEFS.dusk.params } },
    { id: "t1", type: "target", x: 300, y: 0, params: { ...NODE_DEFS.target.params } },
    { id: "c1", type: "capture", x: 600, y: 0, params: { ...NODE_DEFS.capture.params } },
  ],
  edges: [{ id: "ed1", from: "d1", fromPort: "window", to: "t1", toPort: "arm" }],
});
function rec(id: string, graph: unknown = OPENED()) {
  return {
    id, name: `Flow ${id}`, folder: "My flows", tagline: "", graph,
    created_ts: 1, updated_ts: 1, last_run: null, last_result: "", readonly: false,
  };
}

interface Put { id: string; flow: any; resolve: () => void }
/** Every PUT the slice sent, in order. */
let sent: Array<{ id: string; flow: any }> = [];
/** PUTs held open (hold mode). */
let held: Put[] = [];
let saveMode: "answer" | "hold" | "fail" = "answer";
/** What a save's answer adds to the record (the counts switch's `migrated`). */
let answerExtra: Record<string, unknown> = {};
let compiles = 0;
(flowsApi as any).save = (id: string, flow: any) => {
  sent.push({ id, flow });
  if (saveMode === "fail") return Promise.reject(new Error("the server refused it"));
  const answer = () => ({ ...flow, id, updated_ts: 2, ...answerExtra });
  if (saveMode === "hold") {
    return new Promise((resolve) => { held.push({ id, flow, resolve: () => resolve(answer()) }); });
  }
  return Promise.resolve(answer());
};
let opened: (id: string) => unknown = (id) => rec(id);
(flowsApi as any).get = async (id: string) => opened(id);
(flowsApi as any).compileDraft = async () => {
  compiles++;
  return { plan: {}, structural: [], issues: [], unmapped: [] };
};
(flowsApi as any).progress = async () => { throw new Error("Not Found"); };
(flowsApi as any).list = async () => [];
(flowsApi as any).folders = async () => [];

type Host = FlowsHost & { sequence: any };

/** The slice behind a miniature store that keeps zustand's contract. With
 *  `autosave: false` no `api` is handed in, so no timer is ever armed. */
function slice(opts: { autosave?: boolean } = {}) {
  let state: Host;
  const listeners = new Set<(s: Host, prev: Host) => void>();
  const set = (fn: (s: any) => any) => {
    const prev = state;
    state = { ...state, ...fn(state) } as Host;
    for (const l of listeners) l(state, prev);
  };
  const get = () => state;
  const api = {
    subscribe(l: (s: Host, prev: Host) => void) {
      listeners.add(l);
      return () => { listeners.delete(l); };
    },
  };
  const actions = createFlowsActions(set, get, opts.autosave ? api : undefined);
  state = { ...actions, flows: { ...FLOWS_INIT }, sequence: { state: "idle" } } as Host;
  sent = []; held = []; armed = []; saveMode = "answer"; answerExtra = {}; compiles = 0;
  opened = (id) => rec(id);
  CLOCK = 1_700_000_000_000;
  return {
    get flows(): FlowsState { return state.flows; },
    a: actions,
    patchFlows(p: Partial<FlowsState>) { set((s: any) => ({ flows: { ...s.flows, ...p } })); },
  };
}
type S = ReturnType<typeof slice>;

async function openF1(s: S): Promise<void> {
  await s.a.flowsOpen("f1");
  await flush();
  eq(s.flows.record?.id, "f1", "precondition: f1 opened");
}
const json = (v: unknown): string => JSON.stringify(v);
const past = (s: S): number => s.flows.history.past.length;
const future = (s: S): number => s.flows.history.future.length;
const posOf = (s: S, id: string): string => {
  const n = s.flows.graph.nodes.find((x) => x.id === id);
  return n ? `${n.x},${n.y}` : "gone";
};
const nameParam = (s: S, id = "t1"): string =>
  String(s.flows.graph.nodes.find((x) => x.id === id)?.params.name ?? "-");

// ===================================================== 1. THE EDITS, BACK

await test("a flow just opened has nothing to undo or redo, and undo says nothing", async () => {
  const s = slice();
  await openF1(s);
  eq(`${past(s)} past, ${future(s)} future`, "0 past, 0 future",
    "a flow just opened already has history");
  const before = s.flows;
  s.a.flowsUndo();
  s.a.flowsRedo();
  eq(s.flows === before, true,
    "an undo or redo with nothing to step wrote the flows state, which wakes every subscriber for nothing");
  eq(s.flows.dirty, false, "an undo with nothing to undo marked the flow edited");
  await flush();
  eq(compiles, 1, "an undo with nothing to undo asked for a compile (the open's own is the one)");
});

// MUTANT "delete bypasses commit" (flowsDeleteSel writes `graph` and `dirty`
// directly, as it did before this WP, instead of going through `commit`).
// Observed:
//   3 of 27 cases red:
//   x add, delete, rename, wire, then 4 undos restore the opened flow byte for byte; 4 redos reapply: four distinct edits must be four entries
//     expected 4
//     got      3
//   x undoing a delete brings the block back with every wire it took: the deleted block did not come back
//     expected true
//     got      false
// MUTANT "rename bypasses commit" (flowsSetName writes `record` and `dirty`
// directly). Observed:
//   2 of 27 cases red:
//   x add, delete, rename, wire, then 4 undos restore the opened flow byte for byte; 4 redos reapply: four distinct edits must be four entries
//     expected 4
//     got      3
//   x typing a flow's name is one entry, and undo restores the old name: four keystrokes into the name made more than one entry
//     expected 1
//     got      0
await test("add, delete, rename, wire, then 4 undos restore the opened flow byte for byte; 4 redos reapply", async () => {
  const s = slice();
  await openF1(s);
  const openedGraph = json(s.flows.graph);
  const openedName = s.flows.record!.name;

  const a1 = s.a.flowsAddNode("autofocus", { x: 900, y: 0 });
  s.a.flowsSelect({ kind: "node", id: "c1" });
  s.a.flowsDeleteSel();
  s.a.flowsSetName("Renamed night");
  s.a.flowsConnect("t1", "target", a1, "run");
  const editedGraph = json(s.flows.graph);
  eq(past(s), 4, "four distinct edits must be four entries");
  eq(s.flows.graph.nodes.some((n) => n.id === "c1"), false, "precondition: the delete took");

  for (let i = 0; i < 4; i++) s.a.flowsUndo();
  eq(json(s.flows.graph), openedGraph,
    "4 undos did not restore the graph that was opened, byte for byte");
  eq(s.flows.record!.name, openedName, "4 undos did not restore the flow's name");
  eq(`${past(s)} past, ${future(s)} future`, "0 past, 4 future", "the stacks after 4 undos");
  eq(s.flows.dirty, true, "an undo leaves the flow marked edited: the autosave must see it");

  for (let i = 0; i < 4; i++) s.a.flowsRedo();
  eq(json(s.flows.graph), editedGraph, "4 redos did not reapply the edits, byte for byte");
  eq(s.flows.record!.name, "Renamed night", "4 redos did not reapply the rename");
  eq(`${past(s)} past, ${future(s)} future`, "4 past, 0 future", "the stacks after 4 redos");
});

// MUTANT "delete bypasses commit" again, in the case that shows what it costs
// the operator: the block and its wires are not brought back.
await test("undoing a delete brings the block back with every wire it took", async () => {
  const s = slice();
  await openF1(s);
  s.a.flowsSelect({ kind: "node", id: "t1" });
  s.a.flowsDeleteSel();
  eq(s.flows.graph.edges.length, 0, "precondition: the delete took the wire with it");
  s.a.flowsUndo();
  eq(s.flows.graph.nodes.some((n) => n.id === "t1"), true, "the deleted block did not come back");
  eq(s.flows.graph.edges.some((e) => e.id === "ed1"), true, "its wire did not come back");
});

await test("undo and redo of an edge deletion, and of a settings write", async () => {
  const s = slice();
  await openF1(s);
  s.a.flowsSelect({ kind: "edge", id: "ed1" });
  s.a.flowsDeleteSel();
  eq(s.flows.graph.edges.length, 0, "precondition: the wire is gone");
  s.a.flowsUndo();
  eq(s.flows.graph.edges.length, 1, "the deleted wire did not come back");
  const before = json(s.flows.graph);
  eq(s.a.flowsSetSetting("whenWaiting", "Wait for the mosaic"), true, "precondition: the setting took");
  s.a.flowsUndo();
  eq(json(s.flows.graph), before, "undo of a settings write did not restore the settings");
});

await test("the TARGET modal's DONE (flowsApplyFraming) is ONE entry however many params it writes", async () => {
  const s = slice();
  await openF1(s);
  const before = json(s.flows.graph);
  await s.a.flowsApplyFraming("t1", { name: "M 31", rows: 3, cols: 2 });
  eq(past(s), 1, "one DONE wrote more than one history entry");
  s.a.flowsUndo();
  eq(json(s.flows.graph), before, "one undo did not take the whole DONE back");
});

// ===================================================== 2. COALESCING

// MUTANT "coalescing off" (the coalesce test in `commit` made `false`, so
// every write pushes). Observed:
//   5 of 27 cases red:
//   x a 20-step drag is one history entry, and one undo puts the block back: a 20-step drag made more than one entry
//     expected 1
//     got      20
//   x a drag longer than 800 ms in total is still one entry while no gap reaches 800 ms: a slow drag, 4 s long with 500 ms between moves, was split int...
//     expected 1
//     got      8
await test("a 20-step drag is one history entry, and one undo puts the block back", async () => {
  const s = slice();
  await openF1(s);
  const start = posOf(s, "t1");
  for (let i = 1; i <= 20; i++) {
    tick(15);
    s.a.flowsMoveNode("t1", 300 + i * 10, i * 5);
  }
  eq(past(s), 1, "a 20-step drag made more than one entry");
  eq(posOf(s, "t1"), "500,100", "precondition: the drag moved the block");
  s.a.flowsUndo();
  eq(posOf(s, "t1"), start, "one undo did not put the dragged block back where the drag began");
  eq(past(s), 0, "the drag left more than one entry behind");
});

// MUTANT "the window never slides" (a coalesced write leaves `lastCommit.at`
// alone, so the window runs from the FIRST move). Observed:
//   1 of 27 cases red:
//   x a drag longer than 800 ms in total is still one entry while no gap reaches 800 ms: a slow drag, 4 s long with 500 ms between moves, was split int...
//     expected 1
//     got      4
await test("a drag longer than 800 ms in total is still one entry while no gap reaches 800 ms", async () => {
  const s = slice();
  await openF1(s);
  for (let i = 1; i <= 8; i++) {
    tick(500);
    s.a.flowsMoveNode("t1", 300 + i * 10, 0);
  }
  eq(past(s), 1, "a slow drag, 4 s long with 500 ms between moves, was split into several entries");
});

// MUTANT "gap not checked" (the 800 ms test dropped from the coalesce
// condition). Observed:
//   1 of 27 cases red:
//   x a move 801 ms after the last starts a new entry; a gap of exactly 800 ms still joins: a move 801 ms after the last joined the earlier gesture
//     expected 2
//     got      1
await test("a move 801 ms after the last starts a new entry; a gap of exactly 800 ms still joins", async () => {
  const s = slice();
  await openF1(s);
  eq(FLOW_COALESCE_MS, 800, "the ruling: 800 ms");
  s.a.flowsMoveNode("t1", 310, 0);
  tick(800);
  s.a.flowsMoveNode("t1", 320, 0);
  eq(past(s), 1, "a gap of exactly 800 ms did not join the gesture");
  tick(801);
  s.a.flowsMoveNode("t1", 330, 0);
  eq(past(s), 2, "a move 801 ms after the last joined the earlier gesture");
  s.a.flowsUndo();
  eq(posOf(s, "t1"), "320,0", "undo did not stop at the end of the first gesture");
});

// MUTANT "any move joins any move" (the node id left out of the coalesce
// comparison). Observed:
//   1 of 27 cases red:
//   x a move of ANOTHER block, or a different kind of edit, is its own entry: moving a second block joined the first block's drag
//     expected 2
//     got      1
await test("a move of ANOTHER block, or a different kind of edit, is its own entry", async () => {
  const s = slice();
  await openF1(s);
  s.a.flowsMoveNode("t1", 310, 0);
  s.a.flowsMoveNode("c1", 610, 0);
  eq(past(s), 2, "moving a second block joined the first block's drag");
  s.a.flowsSetParam("t1", "name", "A");
  eq(past(s), 3, "a param edit joined a drag");
  s.a.flowsMoveNode("t1", 320, 0);
  eq(past(s), 4, "a move straight after a param edit joined it");
});

// MUTANT "any param joins any param" (the key left out of the coalesce
// comparison). Observed:
//   1 of 27 cases red:
//   x same-key param edits inside 800 ms are one entry; another key or block is not: an edit of a different key joined the name's entry
//     expected 2
//     got      1
await test("same-key param edits inside 800 ms are one entry; another key or block is not", async () => {
  const s = slice();
  await openF1(s);
  const before = nameParam(s);
  for (const v of ["M", "M 3", "M 31"]) {
    tick(100);
    s.a.flowsSetParam("t1", "name", v);
  }
  eq(past(s), 1, "three keystrokes into one field made more than one entry");
  s.a.flowsSetParam("t1", "dec", "+41 16");
  eq(past(s), 2, "an edit of a different key joined the name's entry");
  s.a.flowsSetParam("c1", "count", "30");
  eq(past(s), 3, "an edit of the same kind on another block joined the entry");
  s.a.flowsUndo();
  s.a.flowsUndo();
  s.a.flowsUndo();
  eq(nameParam(s), before, "one undo per burst did not take the typed name back to what it was");
});

// MUTANT "a param write that changes nothing is an entry" (the no-change
// test of flowsSetParam removed). Observed:
//   1 of 27 cases red:
//   x a param written to the value it already has is no entry: re-writing a value as it is made an undo step that changes nothing
//     expected 0
//     got      1
await test("a param written to the value it already has is no entry", async () => {
  const s = slice();
  await openF1(s);
  s.a.flowsSetParam("t1", "name", nameParam(s));
  s.a.flowsSetParam("t1", "name", nameParam(s));
  eq(past(s), 0, "re-writing a value as it is made an undo step that changes nothing");
});

// MUTANT "a move that goes nowhere is an entry" (the no-move test of
// flowsMoveNode reduced to the missing-block test). Observed:
//   1 of 27 cases red:
//   x a move to where the block already is, or of a block that is gone, is no entry: a move that changes nothing made an undo step that changes nothing
//     expected 0
//     got      1
await test("a move to where the block already is, or of a block that is gone, is no entry", async () => {
  const s = slice();
  await openF1(s);
  s.a.flowsMoveNode("t1", 300, 0);
  s.a.flowsMoveNode("gone", 10, 10);
  eq(past(s), 0, "a move that changes nothing made an undo step that changes nothing");
});

// MUTANT "name typing is an entry per keystroke" (the name edit given no
// coalescing key). Observed:
//   1 of 27 cases red:
//   x typing a flow's name is one entry, and undo restores the old name: four keystrokes into the name made more than one entry
//     expected 1
//     got      4
await test("typing a flow's name is one entry, and undo restores the old name", async () => {
  const s = slice();
  await openF1(s);
  const old = s.flows.record!.name;
  for (const v of ["R", "Re", "Ren", "Rena"]) {
    tick(120);
    s.a.flowsSetName(v);
  }
  eq(past(s), 1, "four keystrokes into the name made more than one entry");
  s.a.flowsUndo();
  eq(s.flows.record!.name, old, "undo did not restore the name");
  s.a.flowsRedo();
  eq(s.flows.record!.name, "Rena", "redo did not reapply the typed name");
});

// ===================================================== 3. THE CAP

// MUTANT "no cap" (the `slice(-FLOW_HISTORY_CAP)` of `commit` removed).
// Observed:
//   1 of 27 cases red:
//   x the history is capped at 50 and the oldest entries are the ones dropped: 60 edits left something other than 50 entries
//     expected 50
//     got      60
// MUTANT "the cap drops the newest" (`slice(0, FLOW_HISTORY_CAP)`).
// Observed:
//   1 of 27 cases red:
//   x the history is capped at 50 and the oldest entries are the ones dropped: after 50 undos the graph is not the one 50 edits back: the newest entrie...
//     expected 13
//     got      3
await test("the history is capped at 50 and the oldest entries are the ones dropped", async () => {
  const s = slice();
  await openF1(s);
  eq(FLOW_HISTORY_CAP, 50, "the ruling: 50");
  for (let i = 0; i < 60; i++) s.a.flowsAddNode("notify", { x: i, y: 0 });
  eq(past(s), 50, "60 edits left something other than 50 entries");
  for (let i = 0; i < 50; i++) s.a.flowsUndo();
  eq(past(s), 0, "50 undos did not empty the history");
  // The entries dropped are the OLDEST: the graph 50 undos back is the one
  // after the first 10 adds, not the opened one.
  eq(s.flows.graph.nodes.length, 3 + 10,
    "after 50 undos the graph is not the one 50 edits back: the newest entries were dropped, or none were");
  s.a.flowsUndo();
  eq(s.flows.graph.nodes.length, 13, "an undo past the cap changed the graph");
});

// ===================================================== 4. REDO

// MUTANT "a new edit keeps the redo" (the `future: []` of `commit` dropped).
// Observed:
//   1 of 27 cases red:
//   x a new edit after an undo clears the redo: an edit made after an undo left the old redo standing
//     expected 0
//     got      1
await test("a new edit after an undo clears the redo", async () => {
  const s = slice();
  await openF1(s);
  s.a.flowsAddNode("notify", { x: 1, y: 1 });
  s.a.flowsUndo();
  eq(future(s), 1, "precondition: one redo");
  s.a.flowsAddNode("notify", { x: 2, y: 2 });
  eq(future(s), 0, "an edit made after an undo left the old redo standing");
  const before = json(s.flows.graph);
  s.a.flowsRedo();
  eq(json(s.flows.graph), before, "a redo with nothing to redo changed the graph");
});

// MUTANT "undo does not break the burst" (undo leaves `lastCommit` alone).
// Observed:
//   1 of 27 cases red:
//   x an edit right after an undo is its own entry, not part of the burst the undo ended: the move after the undo is the only entry
//     expected 1
//     got      0
await test("an edit right after an undo is its own entry, not part of the burst the undo ended", async () => {
  const s = slice();
  await openF1(s);
  s.a.flowsMoveNode("t1", 310, 0);
  s.a.flowsUndo();
  s.a.flowsMoveNode("t1", 320, 0);
  eq(past(s), 1, "the move after the undo is the only entry");
  s.a.flowsUndo();
  eq(posOf(s, "t1"), "300,0", "the move made straight after an undo could not be undone");
});

// ===================================================== 5. WHAT UNDO TOUCHES

// MUTANT "selection kept" (undo leaves `sel` and `editNode` as they were).
// Observed:
//   1 of 27 cases red:
//   x undo clears the selection and the edit sheet of a block that is gone, and keeps one that is not: the selection still names a block the undo removed
//     expected null
//     got      {"kind":"node","id":"n64_loyw3v28"}
await test("undo clears the selection and the edit sheet of a block that is gone, and keeps one that is not", async () => {
  const s = slice();
  await openF1(s);
  const a1 = s.a.flowsAddNode("notify", { x: 1, y: 1 });
  s.a.flowsSelect({ kind: "node", id: a1 });
  s.a.flowsSetEditNode(a1);
  s.a.flowsUndo();
  eq(s.flows.sel, null, "the selection still names a block the undo removed");
  eq(s.flows.editNode, null, "the edit sheet still names a block the undo removed");

  s.a.flowsSelect({ kind: "node", id: "t1" });
  s.a.flowsSetEditNode("t1");
  s.a.flowsRedo();
  eq(s.flows.sel?.id, "t1", "undo or redo dropped a selection that is still valid");
  eq(s.flows.editNode, "t1", "undo or redo dropped an edit sheet that is still valid");

  s.a.flowsConnect("t1", "target", "c1", "run");
  const e2 = s.flows.graph.edges.find((e) => e.id !== "ed1")!.id;
  s.a.flowsSelect({ kind: "edge", id: e2 });
  s.a.flowsUndo();
  eq(s.flows.sel, null, "the selection still names a wire the undo removed");
});

await test("undo drops a half-made wire from a block that is gone", async () => {
  const s = slice();
  await openF1(s);
  const a1 = s.a.flowsAddNode("notify", { x: 1, y: 1 });
  s.a.flowsTapPort(a1, "done", "out");
  eq(s.flows.tapWire?.from, a1, "precondition: the tap wire is armed on the new block");
  s.a.flowsUndo();
  eq(s.flows.tapWire, null, "a tap wire from a block the undo removed stayed armed");
});

// MUTANT "a dragged wire kept by undo" (flowsSlice.ts `stepHistory`'s
// `wire: f.wire !== null && nodes.has(f.wire.from) ? f.wire : null,` made
// `wire: f.wire,`). Added by the independent verifier: the case above grades
// `tapWire` only, and this is the OTHER half-made wire, the one a pointer drag
// holds (`flows.wire`, from `flowsBeginWire`), which a Ctrl+Z can reach while
// the button is still down. Observed:
//   1 of 28 cases red:
//   x undo drops a wire being dragged from a block that is gone, and keeps one from a block that is not: a dragged wire from a block the undo removed stayed in hand
//     expected null
//     got      {"from":"n66_loyw3v28","fromPort":"done","kind":"flow","to":{"x":5,"y":5}}
// MUTANT "undo drops every dragged wire" (the same line made `wire: null,`).
// Observed: the same case red on its second half, "an undo dropped a dragged
// wire whose block is still there".
await test("undo drops a wire being dragged from a block that is gone, and keeps one from a block that is not", async () => {
  const s = slice();
  await openF1(s);
  const a1 = s.a.flowsAddNode("notify", { x: 1, y: 1 });
  s.a.flowsBeginWire({ from: a1, fromPort: "done", kind: "flow", to: { x: 5, y: 5 } });
  eq(s.flows.wire?.from, a1, "precondition: a wire is being dragged from the new block");
  s.a.flowsUndo();
  eq(s.flows.wire, null, "a dragged wire from a block the undo removed stayed in hand");

  s.a.flowsRedo();
  s.a.flowsBeginWire({ from: "t1", fromPort: "target", kind: "flow", to: { x: 5, y: 5 } });
  const held = s.flows.wire;
  s.a.flowsUndo();
  eq(s.flows.wire === held, true, "an undo dropped a dragged wire whose block is still there");
});

// MUTANT "undo does not compile" (the `get().flowsCompile()` of flowsUndo and
// flowsRedo removed). Observed:
//   1 of 27 cases red:
//   x undo and redo ask for a compile of the graph they restored: an undo did not ask for a compile (the chips and PLAN would describe the graph before...
//     expected 1
//     got      0
await test("undo and redo ask for a compile of the graph they restored", async () => {
  const s = slice();
  await openF1(s);
  s.a.flowsAddNode("notify", { x: 1, y: 1 });
  await flush();
  const base = compiles;
  s.a.flowsUndo();
  await flush();
  eq(compiles - base, 1, "an undo did not ask for a compile (the chips and PLAN would describe the graph before it)");
  eq(s.flows.compiled?.from === s.flows.graph, true,
    "the compile in hand is not for the graph the undo restored");
  s.a.flowsRedo();
  await flush();
  eq(compiles - base, 2, "a redo did not ask for a compile");
  eq(s.flows.compiled?.from === s.flows.graph, true,
    "the compile in hand is not for the graph the redo restored");
});

// ===================================================== 6. OPEN, CLOSE, SAVE

// MUTANT "history not cleared on open" (the `history` of flowsOpen's record
// write removed). Observed:
//   1 of 27 cases red:
//   x opening a flow clears the history; so does reopening the same one: opening another flow left the last flow's history to undo into
//     expected "0 past, 0 future"
//     got      "0 past, 1 future"
await test("opening a flow clears the history; so does reopening the same one", async () => {
  const s = slice();
  await openF1(s);
  s.a.flowsAddNode("notify", { x: 1, y: 1 });
  s.a.flowsUndo();
  eq(`${past(s)} past, ${future(s)} future`, "0 past, 1 future", "precondition");
  await s.a.flowsOpen("f2");
  await flush();
  eq(`${past(s)} past, ${future(s)} future`, "0 past, 0 future",
    "opening another flow left the last flow's history to undo into");
  s.a.flowsAddNode("notify", { x: 2, y: 2 });
  await s.a.flowsOpen("f2");
  await flush();
  eq(`${past(s)} past, ${future(s)} future`, "0 past, 0 future",
    "reopening the same flow kept its history (a re-read flow is a new starting point)");
});

// MUTANT "a refused open clears the history" (the history cleared before the
// refusal check). Observed:
//   1 of 27 cases red:
//   x an open that is refused leaves the open flow's history alone: a refused open took the open flow's history with it
//     expected 1
//     got      0
await test("an open that is refused leaves the open flow's history alone", async () => {
  const s = slice();
  await openF1(s);
  s.a.flowsAddNode("notify", { x: 1, y: 1 });
  saveMode = "fail";
  await s.a.flowsOpen("f2");
  eq(s.flows.record?.id, "f1", "precondition: the open was refused over the unsaved edit");
  eq(past(s), 1, "a refused open took the open flow's history with it");
});

// MUTANT "history not cleared on close" (the `history` of flowsCloseEditor's
// write removed). Observed:
//   1 of 27 cases red:
//   x closing the editor clears the history: the history outlived the flow it belonged to
//     expected "0 past, 0 future"
//     got      "1 past, 0 future"
await test("closing the editor clears the history", async () => {
  const s = slice();
  await openF1(s);
  s.a.flowsAddNode("notify", { x: 1, y: 1 });
  await s.a.flowsCloseEditor();
  eq(s.flows.record, null, "precondition: the editor closed");
  eq(`${past(s)} past, ${future(s)} future`, "0 past, 0 future",
    "the history outlived the flow it belonged to");
});

// MUTANT "a save clears the history" (flowsSave's answer write given
// `history: FLOW_HISTORY_EMPTY`). Observed:
//   2 of 27 cases red:
//   x a save does not clear the history: undo reaches back across an autosave: the save cleared the history
//     expected 1
//     got      0
//   x an undo after a save marks the flow edited, and the autosave sends the undone graph: an undo left the flow marked saved, so the autosave never se...
//     expected true
//     got      false
await test("a save does not clear the history: undo reaches back across an autosave", async () => {
  const s = slice({ autosave: true });
  await openF1(s);
  s.a.flowsSetParam("t1", "name", "M 31");
  await advance(2000);
  eq(sent.length, 1, "precondition: the autosave sent the edit");
  eq(s.flows.dirty, false, "precondition: the save landed");
  eq(past(s), 1, "the save cleared the history");
  s.a.flowsUndo();
  eq(nameParam(s), NODE_DEFS.target.params.name as string, "undo after a save did not take the edit back");
});

// MUTANT "undo leaves the flow clean" (the `dirty: true` of the undo write
// removed). Observed:
//   1 of 27 cases red:
//   x an undo after a save marks the flow edited, and the autosave sends the undone graph: an undo left the flow marked saved, so the autosave never se...
//     expected true
//     got      false
await test("an undo after a save marks the flow edited, and the autosave sends the undone graph", async () => {
  const s = slice({ autosave: true });
  await openF1(s);
  s.a.flowsSetParam("t1", "name", "M 31");
  await advance(2000);
  eq(sent.length, 1, "precondition: the first autosave");
  eq(s.flows.dirty, false, "precondition: saved");
  s.a.flowsUndo();
  eq(s.flows.dirty, true, "an undo left the flow marked saved, so the autosave never sends it");
  await advance(1999);
  eq(sent.length, 1, "the undone graph was sent before the autosave's window ended");
  await advance(1);
  eq(sent.length, 2, "the autosave did not send the undone graph");
  eq(String(sent[1].flow.graph.nodes.find((n: any) => n.id === "t1").params.name),
    NODE_DEFS.target.params.name as string, "the second PUT did not carry the undone graph");
  await flush();
  eq(s.flows.dirty, false, "the undone graph's save did not settle the flow");
});

// MUTANT "history not carried through the counts switch" (flowsSave's
// `acceptCounts` written into the graph only, not into the history).
// Observed:
//   1 of 27 cases red:
//   x a save's counts switch is not undone by an undo: an undo took the server's counts switch back, and the next save would say it again
//     expected "Accepted subs"
//     got      "Attempts"
await test("a save's counts switch is not undone by an undo", async () => {
  const s = slice();
  // A flow saved before the ruling: its target counts every sub taken.
  opened = (id) => {
    const g = OPENED();
    (g.nodes[1].params as any).counts = "Attempts";
    return rec(id, g);
  };
  answerExtra = { migrated: [{ key: "counts", note: "switched" }] };
  await openF1(s);
  s.a.flowsSetParam("t1", "name", "M 31");
  await s.a.flowsSave();
  await flush();
  eq(String((s.flows.graph.nodes[1].params as any).counts), "Accepted subs",
    "precondition: the save's switch reached the canvas");
  s.a.flowsUndo();
  eq(String((s.flows.graph.nodes[1].params as any).counts), "Accepted subs",
    "an undo took the server's counts switch back, and the next save would say it again");
  eq(s.flows.logs.filter((l) => l.msg === COUNTS_SWITCHED_LINE).length, 1,
    "precondition: the switch was said once");
});

await test("an undo while a PUT is out is saved after it, not beside it", async () => {
  const s = slice({ autosave: true });
  await openF1(s);
  saveMode = "hold";
  s.a.flowsSetParam("t1", "name", "M 31");
  await advance(2000);
  eq(held.length, 1, "precondition: the PUT is out");
  s.a.flowsUndo();
  await advance(2000);
  eq(held.length, 1, "the undone graph went out beside the PUT in flight");
  held[0].resolve();
  await flush();
  eq(s.flows.dirty, true, "the PUT's answer marked the flow saved over an undo made inside it");
  await advance(2000);
  eq(held.length, 2, "the undone graph was never sent after the PUT settled");
  held[1].resolve();
  await flush();
  eq(s.flows.dirty, false, "the second PUT did not settle the flow");
});

// ===================================================== 7. A READ-ONLY EXAMPLE

await test("an example flow can be edited and undone on screen, and is still never saved", async () => {
  const s = slice({ autosave: true });
  opened = (id) => ({ ...rec(id), readonly: true });
  await openF1(s);
  s.a.flowsAddNode("notify", { x: 1, y: 1 });
  s.a.flowsUndo();
  eq(s.flows.graph.nodes.length, 3, "undo did not work on an example");
  await advance(30_000);
  eq(sent.length, 0, "an example was sent to the server");
});

console.log(`\nw16FlowsUndo: ${passed} passed, ${failed} failed`);
if (failures.length) { failures.forEach((f) => console.log(f)); process.exit(1); }
export default { passed, failed, total: passed + failed };
