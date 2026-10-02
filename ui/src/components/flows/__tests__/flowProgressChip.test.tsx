// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// flowProgressChip.test.tsx - the TARGET card's progress chip ("212/315 subs")
// on BOTH canvases, and the slice reads that feed it (#189 S1 item 9, spec 1.2;
// task S1-18).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/__tests__/flowProgressChip.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT IS WORTH GUARDING HERE
//
//   1. THE CHIP IS A NUMBER AN OPERATOR ACTS ON. "212/315 subs" decides
//      whether a target gets another night. So the formatter is graded on
//      exactly when it may speak: a session to count from, a SAVED graph
//      (an unsaved recipe edit is a different step id, #77), a TARGET block
//      the answer names - and on what it counts: the block's own banked subs,
//      never the orphaned frames the flow no longer has a step for.
//   2. ONE FORMATTER, TWO CARDS. The classic FlowNodeCard and the #/next
//      FlowNode both draw it, on TARGET nodes only, and both subscribe to the
//      chip STRING, so a progress answer that leaves a card's count alone does
//      not re-render that card (the discipline both card files exist for).
//   3. THE SLICE READS AT THE RIGHT MOMENTS AND KEEPS ONLY THE RIGHT ANSWER.
//      A saved flow opening, a save, a run starting. The newest read wins, a
//      read for a flow no longer open is dropped, and no state ever pairs one
//      flow's record with another flow's answer. The reads WHILE the open
//      flow's run shoots (#214) are graded in flowProgressLive.test.ts, and a
//      save that must not clear `dirty` (#215) in flowsSaveRace.test.ts.
//   4. A MOSAIC READS IN PANELS (#189 S3, task S3-U2). A TARGET block with a
//      grid reads "4/6 panels done" on both cards: the panels that owe
//      nothing, over the panels the run shoots, never the skipped ones; and
//      the single target and the pool of the same recorded answers read
//      exactly as S1 drew them.
//
// Every guarded case names the mutant it kills and quotes the failure that
// mutant produced when it was run from a byte-for-byte backup of the file it
// changed (the file restored and hash-compared after each run). The "n/24"
// tallies were recorded before the route case (1b) was added; it grades only
// flowsApi.progress's request, passes under every one of those mutants, and
// so moves each tally to n+1/25 without changing a quoted failure.
//
// Convention: shell-and-tests.md section 4 - jsdom by hand, createRoot + act,
// native events, printed tally plus the `{ passed, failed, total }` export.

/* eslint-disable @typescript-eslint/no-explicit-any */

import { installAutoRaf } from "../../../testing/rafPolyfill";

// ---------------------------------------------------------------- jsdom first
// Before any import that reaches lib/base.ts, which reads
// `window.location.pathname` at module scope.
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/", pretendToBeVisual: true },
);
const win = dom.window as any;
win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} addEventListener() {} send() {} };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "localStorage", "sessionStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
installAutoRaf(g);
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------------- imports
const { createElement, Fragment, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../store");
const { flowsApi } = await import("../../../lib/flowsApi");
const { progressChip } = await import("../flowProgress");
const { createFlowsActions, FLOWS_INIT } = await import("../flowsSlice");
const { NODE_DEFS } = await import("../nodeDefs");
const ClassicCard = (await import("../FlowNodeCard")).default;
const { FlowNodeCard: NextCard } =
  await import("../../../next/hubs/session/flows/canvas/FlowNode");
type FlowProgress = import("../../../lib/flowsApi").FlowProgress;
type FlowProgressBlock = import("../../../lib/flowsApi").FlowProgressBlock;
type FlowsHost = import("../flowsSlice").FlowsHost;
type FlowsState = import("../flowsSlice").FlowsState;
type FlowNodeRec = import("../flowsTypes").FlowNodeRec;

// ------------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: unknown, msg: string): asserts cond {
  if (!cond) throw new Error(msg);
}
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) {
    throw new Error(`${msg}\n  expected ${JSON.stringify(want)}\n  got      ${JSON.stringify(got)}`);
  }
}
/** Lets a read the action did not await settle: a stubbed promise resolves in
 *  a microtask, and the slice's `await` resumes in the next. */
const flush = () => new Promise<void>((r) => setTimeout(r, 0));
/** The answer a state holds, in a few words, so a failure names it instead of
 *  printing the whole payload. */
const held = (f: FlowsState): string | null =>
  f.progress ? `${f.progress.flow_id}, session ${f.progress.session?.id ?? "none"}` : null;

// ------------------------------------------------------------------ fixtures
function block(node_id: string, kind: "target" | "pool", banked: number,
               total: number): FlowProgressBlock {
  return {
    node_id, name: node_id, kind, banked, owed: total - banked, total,
    panels: [{
      target_id: `tid-${node_id}`, name: node_id,
      row: kind === "target" ? 0 : null, col: kind === "target" ? 0 : null,
      banked, owed: total - banked, total, steps: [],
    }],
  };
}

/** A flow that has run: TARGET t1 has 212 of 315 banked, POOL p1 30 of 90.
 *  `orphaned` is non-zero on purpose, so a formatter that added it back would
 *  show it (item 1). */
function answer(over: Partial<FlowProgress> = {}): FlowProgress {
  return {
    flow_id: "f1",
    session: { id: "s1", status: "dormant", nights: 2, count_mode: "attempts" },
    blocks: [block("t1", "target", 212, 315), block("p1", "pool", 30, 90)],
    orphaned: { frames: 40, steps: 1 },
    ...over,
  };
}

// ======================================================= 1. THE FORMATTER

await test("a TARGET block with a session reads '{banked}/{total} subs'", () => {
  eq(progressChip(answer(), "t1", false), "212/315 subs", "the chip text");
  eq(progressChip(answer({ blocks: [block("t1", "target", 0, 315)] }), "t1", false),
    "0/315 subs", "a session that has banked nothing on this target still counts it");
});

// MUTANT "show while dirty" (the `if (dirty) return null;` line in
// progressChip deleted). Observed, 20/24 (this case, the save case's
// precondition, and both cards' edit case below):
//   x an unsaved graph draws no chip: its step ids are not the answer's: the
//     answer is the SAVED flow's; an unsaved recipe edit is a new step id with
//     nothing banked, so this count describes a flow the canvas no longer shows
//     expected null
//     got      "212/315 subs"
await test("an unsaved graph draws no chip: its step ids are not the answer's", () => {
  eq(progressChip(answer(), "t1", true), null,
    "the answer is the SAVED flow's; an unsaved recipe edit is a new step id "
    + "with nothing banked, so this count describes a flow the canvas no longer shows");
});

// MUTANT "count orphaned frames as banked" (progressChip formats
// `banked + progress.orphaned.frames`). Observed, 13/24 (every case whose
// answer carries the fixture's 40 orphaned frames, both cards included):
//   x orphaned frames are not banked: they fill no quota: 40 frames sit on a
//     step the flow no longer has; counting them would show a quota met by
//     subs the run will shoot again
//     expected "212/315 subs"
//     got      "252/315 subs"
await test("orphaned frames are not banked: they fill no quota", () => {
  eq(progressChip(answer({ orphaned: { frames: 0, steps: 0 } }), "t1", false), "212/315 subs",
    "precondition: the count with nothing orphaned");
  eq(progressChip(answer({ orphaned: { frames: 40, steps: 1 } }), "t1", false), "212/315 subs",
    "40 frames sit on a step the flow no longer has; counting them would show a "
    + "quota met by subs the run will shoot again");
});

// MUTANT "chip on every node type" (the `block.kind !== "target"` gate in
// progressChip deleted; it is the ONLY type gate, so the cards inherit it).
// Observed, 21/24 (this case and both cards' TARGET-only case below):
//   x a POOL block gets no chip: its members are candidates, not one target's
//     quota: the POOL node's chip
//     expected null
//     got      "30/90 subs"
await test("a POOL block gets no chip: its members are candidates, not one target's quota", () => {
  eq(progressChip(answer(), "p1", false), null, "the POOL node's chip");
});

// CONTROL: a flow that never ran. MUTANT "no session guard" (the
// `if (!progress?.session) return null;` line made `if (!progress)`).
// Observed, 21/24 (this case and both cards' control below):
//   x CONTROL: no session, no chip - '0/315' would read as a campaign that
//     shot nothing: a flow that has never run has nothing to count yet
//     expected null
//     got      "0/315 subs"
await test("CONTROL: no session, no chip - '0/315' would read as a campaign that shot nothing", () => {
  eq(progressChip(answer({ session: null, blocks: [block("t1", "target", 0, 315)] }), "t1", false),
    null, "a flow that has never run has nothing to count yet");
});

// MUTANT "match the first TARGET block" (the block found by
// `b.kind === "target"` instead of by `b.node_id === nodeId`). Observed,
// 18/24:
//   x each TARGET reads its own block, and a node the answer does not name
//     reads nothing: t2 was handed another block's count
//     expected "5/60 subs"
//     got      "212/315 subs"
// MUTANT "borrow a TARGET's count for a node not in the answer" (the node's
// own block, else the first TARGET block). Observed, 19/24 (this case, and
// the CAPTURE card in both cards' TARGET-only and re-render cases):
//   x each TARGET reads its own block, and a node the answer does not name
//     reads nothing: a node with no block (a capture, a cycle) must not borrow
//     a TARGET's count
//     expected null
//     got      "212/315 subs"
await test("each TARGET reads its own block, and a node the answer does not name reads nothing", () => {
  const two = answer({
    blocks: [block("t1", "target", 212, 315), block("t2", "target", 5, 60)],
  });
  eq(progressChip(two, "t1", false), "212/315 subs", "t1's own count");
  eq(progressChip(two, "t2", false), "5/60 subs", "t2 was handed another block's count");
  eq(progressChip(two, "c9", false), null,
    "a node with no block (a capture, a cycle) must not borrow a TARGET's count");
});

// MUTANT "no shape guard" (the `!Array.isArray(progress.blocks)` check
// deleted). Observed, 23/24 - a throw, which inside a card is a render crash:
//   x a malformed answer draws no chip and never throws: Cannot read
//     properties of null (reading 'find')
// MUTANT "no number guard" (the `banked === null || total === null` check
// deleted). Observed, 23/24:
//   x a malformed answer draws no chip and never throws: a count that is not a
//     number reached the chip
//     expected null
//     got      "null/2 subs"
await test("a malformed answer draws no chip and never throws", () => {
  eq(progressChip(null, "t1", false), null, "no answer yet");
  eq(progressChip({} as any, "t1", false), null, "an empty object");
  eq(progressChip({ ok: true } as any, "t1", false), null,
    "a stub or proxy's generic 200 must not turn into a chip");
  eq(progressChip({ ...answer(), blocks: null } as any, "t1", false), null,
    "blocks that is not a list");
  const bad = answer({ blocks: [{ ...block("t1", "target", 1, 2), banked: undefined } as any] });
  eq(progressChip(bad, "t1", false), null, "a count that is not a number reached the chip");
});

// ================================================ 1b. THE ROUTE IT READS
//
// THE SEAM. Every slice case below replaces `flowsApi.progress` with a stub, so
// without this case nothing in the suite sends the real request: the path could
// name any route and every chip test would still pass, while a real server
// answered 404 on every open and the silent catch drew no chip, ever. So this
// one case runs BEFORE the stub is installed, against a fake `fetch`, and grades
// the one thing the stub hides: the request goes to S1-14's route.
//
// MUTANT "progress reads another route" (the path in flowsApi.progress made
// `${one(id)}/tonight`). Observed, 24/25:
//   x flowsApi.progress asks GET /api/flows/{id}/progress and hands back its
//     answer: the chip's read went to another route; S1-14's is
//     /api/flows/{id}/progress
//     expected "/api/flows/f1/progress"
//     got      "/api/flows/f1/tonight"
// MUTANT "raw id in the path" (`${FLOWS_BASE}/${id}/progress` instead of
// `${one(id)}/progress`). Observed, 24/25:
//   x flowsApi.progress asks GET /api/flows/{id}/progress and hands back its
//     answer: an id with a space or a slash must stay one path segment, or it
//     reads another flow's progress or none
//     expected "/api/flows/a%20b%2Fc/progress"
//     got      "/api/flows/a b/c/progress"
await test("flowsApi.progress asks GET /api/flows/{id}/progress and hands back its answer", async () => {
  const asked: { url: string; method: string }[] = [];
  const prev = g.fetch;
  g.fetch = async (url: unknown, init?: { method?: string }) => {
    asked.push({ url: String(url), method: String(init?.method ?? "GET") });
    return { ok: true, status: 200, statusText: "OK", json: async () => answer() };
  };
  try {
    const got = await flowsApi.progress("f1");
    eq(asked.length, 1, "precondition: one request");
    eq(asked[0].method, "GET", "the progress read is a GET");
    eq(asked[0].url, "/api/flows/f1/progress",
      "the chip's read went to another route; S1-14's is /api/flows/{id}/progress");
    // Read off the answer itself, not through progressChip, so a formatter
    // mutant does not fail this case too and blur which half is broken.
    eq(got?.blocks?.[0]?.banked, 212, "the route's answer was not handed back as sent");
    await flowsApi.progress("a b/c");
    eq(asked[1]?.url, "/api/flows/a%20b%2Fc/progress",
      "an id with a space or a slash must stay one path segment, or it reads another "
      + "flow's progress or none");
  } finally {
    g.fetch = prev;
  }
});

// ================================================ 2. THE SLICE'S READS

function rec(id: string) {
  return {
    id, name: `Flow ${id}`, folder: "My flows", tagline: "",
    graph: { nodes: [{ id: "t1", type: "target", x: 0, y: 0, params: { ...NODE_DEFS.target.params } }], edges: [] },
    created_ts: 1, updated_ts: 1, last_run: null, last_result: "", readonly: false,
  };
}

/** Every progress read the slice made, still pending until a test settles it. */
interface Read { id: string; resolve: (v: FlowProgress) => void; reject: (e: unknown) => void }
let reads: Read[] = [];
let runs = 0;
(flowsApi as any).progress = (id: string) =>
  new Promise<FlowProgress>((resolve, reject) => { reads.push({ id, resolve, reject }); });
(flowsApi as any).get = async (id: string) => rec(id);
(flowsApi as any).compileDraft = async () => ({ plan: {}, structural: [], issues: [], unmapped: [] });
(flowsApi as any).save = async (id: string, flow: any) => ({ ...flow, id });
(flowsApi as any).run = async (id: string) => {
  runs++;
  return { started: true, flow_id: id, frames: 12, unmapped: [] };
};

/** The slice alone behind a miniature store, with EVERY state it passed
 *  through recorded, so a test can ask whether some intermediate state paired
 *  the wrong things. */
function slice() {
  let state: FlowsHost;
  const seen: FlowsState[] = [];
  const set = (fn: (s: FlowsHost) => Partial<FlowsHost>) => {
    state = { ...state, ...fn(state) } as FlowsHost;
    seen.push(state.flows);
  };
  const get = () => state;
  const actions = createFlowsActions(set, get);
  state = { ...actions, flows: { ...FLOWS_INIT } } as FlowsHost;
  reads = [];
  return { get flows(): FlowsState { return state.flows; }, a: actions, seen };
}

/** Open `id` and land `ans` as its first read's answer. */
async function openWith(s: ReturnType<typeof slice>, id: string, ans: FlowProgress) {
  await s.a.flowsOpen(id);
  const r = reads.at(-1);
  assert(r && r.id === id, `precondition: opening ${id} read its progress`);
  r.resolve(ans);
  await flush();
}

// MUTANT "no fetch on open" (the `void fetchProgress();` in
// flowsOpen deleted). Observed, 16/24 (every slice case below fails its
// "opening read its progress" precondition too):
//   x opening a saved flow reads its progress, and the answer lands: the open
//     never asked for the flow's progress
//     expected 1
//     got      0
await test("opening a saved flow reads its progress, and the answer lands", async () => {
  const s = slice();
  await s.a.flowsOpen("f1");
  eq(reads.length, 1, "the open never asked for the flow's progress");
  eq(reads[0].id, "f1", "the read asked about another flow");
  reads[0].resolve(answer());
  await flush();
  eq(s.flows.progress?.flow_id, "f1", "the answer did not land");
  eq(progressChip(s.flows.progress, "t1", s.flows.dirty), "212/315 subs",
    "an opened flow's TARGET card has no chip");
});

// MUTANT "no fetch after save" (the `void fetchProgress();` in
// flowsSave deleted). Observed, 23/24:
//   x a save re-reads progress, and the pre-save answer is not drawn
//     meanwhile: the save did not re-read progress
//     expected 2
//     got      1
// MUTANT "keep the previous answer while refetching" (the
// `set(... progress: null)` at the start of fetchProgress deleted).
// Observed, 23/24:
//   x a save re-reads progress, and the pre-save answer is not drawn
//     meanwhile: while the post-save read is in flight the PRE-save answer is
//     still in hand; a save can move step ids, so that count is about a flow
//     that no longer exists
//     expected null
//     got      "f1, session s1"
await test("a save re-reads progress, and the pre-save answer is not drawn meanwhile", async () => {
  const s = slice();
  await openWith(s, "f1", answer());
  s.a.flowsSetParam("t1", "name", "M 31");
  eq(s.flows.dirty, true, "precondition: the edit made the graph dirty");
  eq(progressChip(s.flows.progress, "t1", s.flows.dirty), null,
    "precondition: a dirty graph draws no chip");
  await s.a.flowsSave();
  eq(s.flows.dirty, false, "precondition: the save landed");
  eq(reads.length, 2, "the save did not re-read progress");
  eq(held(s.flows), null,
    "while the post-save read is in flight the PRE-save answer is still in hand; a "
    + "save can move step ids, so that count is about a flow that no longer exists");
  reads[1].resolve(answer({ blocks: [block("t1", "target", 0, 315)] }));
  await flush();
  eq(progressChip(s.flows.progress, "t1", s.flows.dirty), "0/315 subs",
    "the post-save answer did not land");
});

// MUTANT "no fetch after run starts" (the `void fetchProgress();`
// in flowsRun's success path deleted). Observed, 22/24 (this case and the
// newest-read case's precondition):
//   x a run that starts re-reads progress: it may have started a new session:
//     a run started and the progress was never re-read
//     expected 2
//     got      1
await test("a run that starts re-reads progress: it may have started a new session", async () => {
  const s = slice();
  await openWith(s, "f1", answer());
  const before = runs;
  eq(await s.a.flowsRun(), null, "precondition: the run started");
  eq(runs - before, 1, "precondition: one run was posted");
  eq(reads.length, 2, "a run started and the progress was never re-read");
  reads[1].resolve(answer({ session: { id: "s2", status: "active", nights: 1, count_mode: "attempts" } }));
  await flush();
  eq(s.flows.progress?.session?.id, "s2", "the post-run answer did not land");
});

// MUTANT "no ticket check" (the `ticket !== progressTicket` test in
// fetchProgress deleted). Observed, 23/24:
//   x the NEWEST read wins, not the last to arrive: the open's slower read
//     landed after the run's and put the pre-run session back on the card
//     expected "after-run"
//     got      "before-run"
await test("the NEWEST read wins, not the last to arrive", async () => {
  const s = slice();
  await s.a.flowsOpen("f1");
  await s.a.flowsRun();
  eq(reads.length, 2, "precondition: the open and the run each read");
  reads[1].resolve(answer({ session: { id: "after-run", status: "active", nights: 3, count_mode: "attempts" } }));
  await flush();
  reads[0].resolve(answer({ session: { id: "before-run", status: "dormant", nights: 2, count_mode: "attempts" } }));
  await flush();
  eq(s.flows.progress?.session?.id, "after-run",
    "the open's slower read landed after the run's and put the pre-run session back on the card");
});

// MUTANT "no record check" (the `get().flows.record?.id !== id` test in
// fetchProgress deleted). Observed, 23/24:
//   x a read that lands after its flow was closed is dropped: a closed flow's
//     answer landed in the store; the next flow opened would start out
//     holding it
//     expected null
//     got      "f1, session s1"
await test("a read that lands after its flow was closed is dropped", async () => {
  const s = slice();
  await s.a.flowsOpen("f1");
  eq(reads.length, 1, "precondition: the open read");
  await s.a.flowsCloseEditor();
  eq(s.flows.record, null, "precondition: the editor closed");
  reads[0].resolve(answer());
  await flush();
  eq(held(s.flows), null,
    "a closed flow's answer landed in the store; the next flow opened would start "
    + "out holding it");
});

// MUTANT "open keeps the previous answer" (`progress: null` removed from
// flowsOpen's patch, leaving the clear to the read that starts a moment
// later). Observed, 23/24:
//   x no state ever pairs one flow's record with another flow's answer: f2
//     was open while f1's answer was still in the store; node ids are not
//     unique across flows (the wizard mints n1, n2, ... in every flow), so
//     f2's cards would draw f1's counts
//     expected 0
//     got      1
await test("no state ever pairs one flow's record with another flow's answer", async () => {
  const s = slice();
  await openWith(s, "f1", answer());
  eq(s.flows.progress?.flow_id, "f1", "precondition: f1's answer is in hand");
  const from = s.seen.length;
  await s.a.flowsOpen("f2");
  const mixed = s.seen.slice(from).filter((f) => f.record?.id === "f2" && f.progress?.flow_id === "f1");
  eq(mixed.length, 0,
    "f2 was open while f1's answer was still in the store; node ids are not unique "
    + "across flows (the wizard mints n1, n2, ... in every flow), so f2's cards "
    + "would draw f1's counts");
});

// MUTANT "close keeps the answer" (`progress: null` removed from
// flowsCloseEditor's patch). Observed, 23/24:
//   x closing the editor lets go of the answer with the record: the store
//     holds a progress answer for no open flow
//     expected null
//     got      "f1, session s1"
await test("closing the editor lets go of the answer with the record", async () => {
  const s = slice();
  await openWith(s, "f1", answer());
  await s.a.flowsCloseEditor();
  eq(held(s.flows), null, "the store holds a progress answer for no open flow");
});

// CONTROL: nothing is drawn and nothing is said. MUTANT "log a failed read"
// (the catch in fetchProgress appends a warn line). Observed, 23/24:
//   x CONTROL: a failed read leaves no chip and says nothing on the log: a
//     server older than S1 answers 404 on every open; the log is not the place
//     for that
//     expected 0
//     got      1
await test("CONTROL: a failed read leaves no chip and says nothing on the log", async () => {
  const s = slice();
  await s.a.flowsOpen("f1");
  const logs = s.flows.logs.length;
  reads[0].reject(Object.assign(new Error("Not Found"), { status: 404 }));
  await flush();
  eq(held(s.flows), null, "a failed read left an answer in hand");
  eq(s.flows.logs.length, logs,
    "a server older than S1 answers 404 on every open; the log is not the place for that");
  eq(s.flows.libraryError, null, "a failed progress read failed the open");
});

// ================================================== 3. BOTH CARDS DRAW IT

const T1: FlowNodeRec = { id: "t1", type: "target", x: 0, y: 0, params: { ...NODE_DEFS.target.params } };
const P1: FlowNodeRec = { id: "p1", type: "pool", x: 240, y: 0, params: { ...NODE_DEFS.pool.params } };
const C1: FlowNodeRec = { id: "c1", type: "capture", x: 480, y: 0, params: { ...NODE_DEFS.capture.params } };
const NODES = [T1, P1, C1];

/** Renders per card, counted where both cards render: the footer's summary,
 *  which each card calls once per render and nothing else on the canvas calls
 *  (flowNodeDom.test.tsx counts the same way). */
const renders: Record<string, number> = { target: 0, pool: 0, capture: 0 };
for (const t of ["target", "pool", "capture"] as const) {
  const orig = NODE_DEFS[t].sum;
  NODE_DEFS[t].sum = (p) => { renders[t]++; return orig(p); };
}

const container = win.document.getElementById("root");
const root = createRoot(container);

type Which = "classic" | "next";
const CARD = { classic: ClassicCard, next: NextCard } as const;
/** Where each card puts the chip. */
const CHIP_SEL: Record<Which, string> = {
  classic: "[data-flow-progress]",
  next: "[data-testid='flow-node-progress']",
};

/** A parent with NO store subscription, so anything that re-renders after a
 *  store write re-rendered because the card itself asked to. */
function Deck({ which, phone = false, nodes = NODES }: {
  which: Which; phone?: boolean; nodes?: FlowNodeRec[];
}) {
  return createElement(Fragment, null,
    nodes.map((n) => createElement(CARD[which] as any, { key: n.id, node: n, phone })));
}

function mount(which: Which, flows: Partial<FlowsState>, phone = false,
               nodes: FlowNodeRec[] = NODES): void {
  act(() => root.render(null));
  act(() => {
    useStore.setState({
      flows: { ...FLOWS_INIT, graph: { nodes, edges: [] }, ...flows },
    } as any);
  });
  for (const k of Object.keys(renders)) renders[k] = 0;
  act(() => root.render(createElement(Deck, { which, phone, nodes })));
}

function setFlows(p: Partial<FlowsState>): void {
  act(() => {
    useStore.setState({ flows: { ...useStore.getState().flows, ...p } } as any);
  });
}

const cardOf = (type: string): any => container.querySelector(`[data-node-type="${type}"]`);
const chipOn = (which: Which, type: string): string | null =>
  cardOf(type)?.querySelector(CHIP_SEL[which])?.textContent ?? null;

for (const which of ["classic", "next"] as const) {
  // MUTANT "classic card drops the chip" (its chip element in FlowNodeCard.tsx
  // replaced by null). Observed, 21/24 (every [classic] case that expects a
  // chip):
  //   x [classic] the TARGET card draws the chip; the POOL and CAPTURE cards
  //     do not: the classic TARGET card's chip
  //     expected "212/315 subs"
  //     got      null
  // MUTANT "next card drops the chip" (its Pill in canvas/FlowNode.tsx
  // replaced by null): the same three cases for [next], 21/24:
  //   x [next] the TARGET card draws the chip; the POOL and CAPTURE cards do
  //     not: the next TARGET card's chip
  //     expected "212/315 subs"
  //     got      null
  // MUTANT "chip on every node type", on the cards:
  //   x [classic] the TARGET card draws the chip; the POOL and CAPTURE cards
  //     do not: the classic POOL card drew a chip
  //     expected null
  //     got      "30/90 subs"
  //   (and the same line for [next])
  await test(`[${which}] the TARGET card draws the chip; the POOL and CAPTURE cards do not`, () => {
    mount(which, { progress: answer(), dirty: false });
    assert(cardOf("target"), `precondition: the ${which} TARGET card rendered`);
    eq(chipOn(which, "target"), "212/315 subs", `the ${which} TARGET card's chip`);
    eq(chipOn(which, "pool"), null, `the ${which} POOL card drew a chip`);
    eq(chipOn(which, "capture"), null, `the ${which} CAPTURE card drew a chip`);
  });

  // MUTANT "show while dirty", on the card. Observed:
  //   x [classic] an edit takes the chip away and a save brings it back: the
  //     classic card kept its chip over an unsaved edit
  //     expected null
  //     got      "212/315 subs"
  //   (and the same line for [next])
  await test(`[${which}] an edit takes the chip away and a save brings it back`, () => {
    mount(which, { progress: answer(), dirty: false });
    eq(chipOn(which, "target"), "212/315 subs", "precondition: the chip is drawn");
    setFlows({ dirty: true });
    eq(chipOn(which, "target"), null, `the ${which} card kept its chip over an unsaved edit`);
    setFlows({ dirty: false });
    eq(chipOn(which, "target"), "212/315 subs", "the chip did not come back when the graph was clean");
  });

  // CONTROL: no session. MUTANT "no session guard", on the card. Observed:
  //   x [classic] CONTROL: a flow that never ran draws no chip on any card:
  //     the classic canvas drew a chip for a flow with no session
  //     expected 0
  //     got      1
  //   (and the same line for [next])
  await test(`[${which}] CONTROL: a flow that never ran draws no chip on any card`, () => {
    mount(which, { progress: answer({ session: null }), dirty: false });
    assert(cardOf("target"), `precondition: the ${which} TARGET card rendered`);
    eq(container.querySelectorAll(CHIP_SEL[which]).length, 0,
      `the ${which} canvas drew a chip for a flow with no session`);
  });

  // MUTANT "classic card subscribes to the whole answer" / "next card
  // subscribes to the whole answer" (the card selects `s.flows.progress` and
  // formats it during render). Observed, 23/24 each, the classic one:
  //   x [classic] an answer that leaves a card's count alone does not
  //     re-render it: the classic CAPTURE card re-rendered for a progress
  //     answer it has no part in; on a twenty-stage graph every refetch would
  //     repaint the canvas
  //     expected 1
  //     got      2
  //   and the next one, the same line for [next].
  await test(`[${which}] an answer that leaves a card's count alone does not re-render it`, () => {
    mount(which, { progress: answer(), dirty: false });
    eq(renders.target, 1, "precondition: one render each");
    eq(renders.capture, 1, "precondition: one render each");
    // A refetch that answers the same counts: a new object, the same chips.
    setFlows({ progress: answer() });
    eq(renders.capture, 1,
      `the ${which} CAPTURE card re-rendered for a progress answer it has no part in; `
      + "on a twenty-stage graph every refetch would repaint the canvas");
    eq(renders.target, 1, `the ${which} TARGET card re-rendered for an unchanged count`);
    // The count moves: that card, and only that card, re-renders.
    setFlows({ progress: answer({ blocks: [block("t1", "target", 213, 315), block("p1", "pool", 30, 90)] }) });
    eq(chipOn(which, "target"), "213/315 subs", "the new count did not reach the card");
    eq(renders.target, 2, "the TARGET card did not re-render for its own new count");
    eq(renders.capture, 1, "the CAPTURE card re-rendered for another card's count");
  });
}

// MUTANT "classic footer drawn on phone for a chip" (the footer's guard
// `{!phone && (` made `{(!phone || chip) && (`). Observed, 23/24:
//   x [classic] on the phone tier the chip is hidden with the footer: the
//     150 px card drew the chip; the phone tier has no footer line, and the
//     auto-graph's layout budgets room for none
//     expected null
//     got      "212/315 subs"
await test("[classic] on the phone tier the chip is hidden with the footer", () => {
  mount("classic", { progress: answer(), dirty: false }, true);
  assert(cardOf("target"), "precondition: the phone TARGET card rendered");
  eq(chipOn("classic", "target"), null,
    "the 150 px card drew the chip; the phone tier has no footer line, and the "
    + "auto-graph's layout budgets room for none");
});

// ============================================== 4. A MOSAIC READS IN PANELS
//
// #189 S3 (spec 1.2): a TARGET block with a grid reads "N/M panels done", N
// the panels whose `owed` is 0 and M the panels the plan SHOOTS, which the
// route's `panels` already is: a skipped panel is listed in the block's
// `skipped` and never in `panels`. Both canvases, from the one formatter.
// This section's mutants were run in a private copy of ui/
// (scratchpad/s3-u2-readouts-m5q8/mut/), never in the shared tree, each file
// restored from a byte backup and hash-checked after each run.
//
// RECORDED, NOT HAND-WRITTEN. The two answers below are the progress route's
// own (`flows/progress.py::flow_progress`, as S3-T left it), recorded in
// scratchpad/s3-u2-readouts-m5q8/record.py exactly the way the route builds
// one: a graph of DUSK -> TARGET M16 (a grid, rotating, loop wire from its
// CAPTURE) -> CAPTURE Ha 300 s x 2 -> TARGET M31 (single) -> CAPTURE ->
// POOL M13, M92 -> CAPTURE, compiled with `compile_plan`, expanded by
// `to_sequence_plan(flow_id=...)`, and a dormant accepted-mode Session of
// that plan holding frames on chosen panels. Nothing site-derived is in
// them (the route is CAP_VIEW_STATUS). Re-record, never hand-edit: a
// hand-made fixture tests the answer its author imagined.
//
//  - PROGRESS_2X3: a 2x3, every panel shot; 1-1, 1-3, 2-2 and 2-1 banked
//    their 2 subs, 1-2 one, 2-3 none. M31 holds 3 of 5, M13 2 of 5.
//  - PROGRESS_2X4_SKIPS: a 2x4 with 1-4 and 2-4 skipped; 1-1 and 2-3 done,
//    1-2 one sub, and the skipped 1-4 still holding the 2 subs it banked
//    before it was skipped. M31 holds all 5.

const PROGRESS_2X3: FlowProgress = {
  "flow_id": "flow-s3u2-readouts",
  "session": {"id": "s-1", "status": "dormant", "nights": 2, "count_mode": "accepted"},
  "blocks": [
    {
      "node_id": "t",
      "name": "M16",
      "kind": "target",
      "banked": 9,
      "owed": 3,
      "total": 12,
      "panels": [
        {
          "target_id": "bbce1f80f6ba544ea71271dd6136e60a",
          "name": "M16 1-1",
          "row": 0,
          "col": 0,
          "banked": 2,
          "owed": 0,
          "total": 2,
          "steps": [
            {
              "step_id": "4872c877290157328cd49689b7db28e9",
              "filter": "Ha",
              "frame_type": "Light",
              "exposure_s": 300.0,
              "count": 2,
              "banked": 2,
              "owed": 0
            }
          ]
        },
        {
          "target_id": "3ff4f91287eb526891b049976a413b16",
          "name": "M16 1-2",
          "row": 0,
          "col": 1,
          "banked": 1,
          "owed": 1,
          "total": 2,
          "steps": [
            {
              "step_id": "cbefba6aee085b2d9eb5d97157fd6f1d",
              "filter": "Ha",
              "frame_type": "Light",
              "exposure_s": 300.0,
              "count": 2,
              "banked": 1,
              "owed": 1
            }
          ]
        },
        {
          "target_id": "bb226e5c15f854cdb88cec2a7a3c97df",
          "name": "M16 1-3",
          "row": 0,
          "col": 2,
          "banked": 2,
          "owed": 0,
          "total": 2,
          "steps": [
            {
              "step_id": "72ffcf511845515686a539c462745abf",
              "filter": "Ha",
              "frame_type": "Light",
              "exposure_s": 300.0,
              "count": 2,
              "banked": 2,
              "owed": 0
            }
          ]
        },
        {
          "target_id": "c77bc7c532ce505d8069ac65dceabdbc",
          "name": "M16 2-3",
          "row": 1,
          "col": 2,
          "banked": 0,
          "owed": 2,
          "total": 2,
          "steps": [
            {
              "step_id": "3af255a3c2a1555bb4616a072e4865ec",
              "filter": "Ha",
              "frame_type": "Light",
              "exposure_s": 300.0,
              "count": 2,
              "banked": 0,
              "owed": 2
            }
          ]
        },
        {
          "target_id": "615361c3abf15cb4b9169e335551e401",
          "name": "M16 2-2",
          "row": 1,
          "col": 1,
          "banked": 2,
          "owed": 0,
          "total": 2,
          "steps": [
            {
              "step_id": "a4666806dff35e62932a2f1d26b57e0e",
              "filter": "Ha",
              "frame_type": "Light",
              "exposure_s": 300.0,
              "count": 2,
              "banked": 2,
              "owed": 0
            }
          ]
        },
        {
          "target_id": "e695cab515675ce2ae546d7320968a67",
          "name": "M16 2-1",
          "row": 1,
          "col": 0,
          "banked": 2,
          "owed": 0,
          "total": 2,
          "steps": [
            {
              "step_id": "ca222273b62b50f8a92eea7bd8d4f62b",
              "filter": "Ha",
              "frame_type": "Light",
              "exposure_s": 300.0,
              "count": 2,
              "banked": 2,
              "owed": 0
            }
          ]
        }
      ],
      "grid": {"rows": 2, "cols": 3},
      "skipped": []
    },
    {
      "node_id": "u",
      "name": "M31",
      "kind": "target",
      "banked": 3,
      "owed": 2,
      "total": 5,
      "panels": [
        {
          "target_id": "21a3decb9ab05c73bdfadaff9d209d98",
          "name": "M31",
          "row": 0,
          "col": 0,
          "banked": 3,
          "owed": 2,
          "total": 5,
          "steps": [
            {
              "step_id": "b4d77a16cbf950f6b0737ca946d6f217",
              "filter": "L",
              "frame_type": "Light",
              "exposure_s": 60.0,
              "count": 5,
              "banked": 3,
              "owed": 2
            }
          ]
        }
      ]
    },
    {
      "node_id": "p",
      "name": "M13, M92",
      "kind": "pool",
      "banked": 2,
      "owed": 8,
      "total": 10,
      "panels": [
        {
          "target_id": "f926755fa56d5b83b7dbe7fe4b7e097b",
          "name": "M13",
          "row": null,
          "col": null,
          "banked": 2,
          "owed": 3,
          "total": 5,
          "steps": [
            {
              "step_id": "08144402b8095934bcc77006f3c5e0b9",
              "filter": "L",
              "frame_type": "Light",
              "exposure_s": 60.0,
              "count": 5,
              "banked": 2,
              "owed": 3
            }
          ]
        },
        {
          "target_id": "bb5cd3fa03405b6ea12b4a906cead261",
          "name": "M92",
          "row": null,
          "col": null,
          "banked": 0,
          "owed": 5,
          "total": 5,
          "steps": [
            {
              "step_id": "98e06c9ca1af5556a49190c3ef853c85",
              "filter": "L",
              "frame_type": "Light",
              "exposure_s": 60.0,
              "count": 5,
              "banked": 0,
              "owed": 5
            }
          ]
        }
      ]
    }
  ],
  "orphaned": {"frames": 0, "steps": 0}
};

const PROGRESS_2X4_SKIPS: FlowProgress = {
  "flow_id": "flow-s3u2-readouts",
  "session": {"id": "s-1", "status": "dormant", "nights": 2, "count_mode": "accepted"},
  "blocks": [
    {
      "node_id": "t",
      "name": "M16",
      "kind": "target",
      "banked": 5,
      "owed": 7,
      "total": 12,
      "panels": [
        {
          "target_id": "63335b38d798546f86e29cd551aae2a8",
          "name": "M16 1-1",
          "row": 0,
          "col": 0,
          "banked": 2,
          "owed": 0,
          "total": 2,
          "steps": [
            {
              "step_id": "dd48f0e69b2659d7aae305f155652ed9",
              "filter": "Ha",
              "frame_type": "Light",
              "exposure_s": 300.0,
              "count": 2,
              "banked": 2,
              "owed": 0
            }
          ]
        },
        {
          "target_id": "5fcac8705d2b5148a9d7df30c2d30565",
          "name": "M16 1-2",
          "row": 0,
          "col": 1,
          "banked": 1,
          "owed": 1,
          "total": 2,
          "steps": [
            {
              "step_id": "f1579218339652129e6699885d6ab7dc",
              "filter": "Ha",
              "frame_type": "Light",
              "exposure_s": 300.0,
              "count": 2,
              "banked": 1,
              "owed": 1
            }
          ]
        },
        {
          "target_id": "dea547f516b8510e9443cdea05392312",
          "name": "M16 1-3",
          "row": 0,
          "col": 2,
          "banked": 0,
          "owed": 2,
          "total": 2,
          "steps": [
            {
              "step_id": "42b338f0b23b5dc3a655d4faf2107a76",
              "filter": "Ha",
              "frame_type": "Light",
              "exposure_s": 300.0,
              "count": 2,
              "banked": 0,
              "owed": 2
            }
          ]
        },
        {
          "target_id": "857edf34d56d5ab18d451975329669bb",
          "name": "M16 2-3",
          "row": 1,
          "col": 2,
          "banked": 2,
          "owed": 0,
          "total": 2,
          "steps": [
            {
              "step_id": "52551cf4781a5d93a7632065cf6193e0",
              "filter": "Ha",
              "frame_type": "Light",
              "exposure_s": 300.0,
              "count": 2,
              "banked": 2,
              "owed": 0
            }
          ]
        },
        {
          "target_id": "ba07a8d19bd154d5af710664bc406212",
          "name": "M16 2-2",
          "row": 1,
          "col": 1,
          "banked": 0,
          "owed": 2,
          "total": 2,
          "steps": [
            {
              "step_id": "79844b200ba053068124ba6288b79bbe",
              "filter": "Ha",
              "frame_type": "Light",
              "exposure_s": 300.0,
              "count": 2,
              "banked": 0,
              "owed": 2
            }
          ]
        },
        {
          "target_id": "c9fbb82f3ff15ec392e0f1b44623c271",
          "name": "M16 2-1",
          "row": 1,
          "col": 0,
          "banked": 0,
          "owed": 2,
          "total": 2,
          "steps": [
            {
              "step_id": "1ac650e22cc455dfbeb735609ad42a40",
              "filter": "Ha",
              "frame_type": "Light",
              "exposure_s": 300.0,
              "count": 2,
              "banked": 0,
              "owed": 2
            }
          ]
        }
      ],
      "grid": {"rows": 2, "cols": 4},
      "skipped": [
        {
          "target_id": "38affd0962c65588a2aa3befca464226",
          "name": "M16 1-4",
          "row": 0,
          "col": 3,
          "banked": 2
        },
        {
          "target_id": "95b26da0ce4151f3b2a207ca73a08fec",
          "name": "M16 2-4",
          "row": 1,
          "col": 3,
          "banked": 0
        }
      ]
    },
    {
      "node_id": "u",
      "name": "M31",
      "kind": "target",
      "banked": 5,
      "owed": 0,
      "total": 5,
      "panels": [
        {
          "target_id": "21a3decb9ab05c73bdfadaff9d209d98",
          "name": "M31",
          "row": 0,
          "col": 0,
          "banked": 5,
          "owed": 0,
          "total": 5,
          "steps": [
            {
              "step_id": "b4d77a16cbf950f6b0737ca946d6f217",
              "filter": "L",
              "frame_type": "Light",
              "exposure_s": 60.0,
              "count": 5,
              "banked": 5,
              "owed": 0
            }
          ]
        }
      ]
    },
    {
      "node_id": "p",
      "name": "M13, M92",
      "kind": "pool",
      "banked": 0,
      "owed": 10,
      "total": 10,
      "panels": [
        {
          "target_id": "f926755fa56d5b83b7dbe7fe4b7e097b",
          "name": "M13",
          "row": null,
          "col": null,
          "banked": 0,
          "owed": 5,
          "total": 5,
          "steps": [
            {
              "step_id": "08144402b8095934bcc77006f3c5e0b9",
              "filter": "L",
              "frame_type": "Light",
              "exposure_s": 60.0,
              "count": 5,
              "banked": 0,
              "owed": 5
            }
          ]
        },
        {
          "target_id": "bb5cd3fa03405b6ea12b4a906cead261",
          "name": "M92",
          "row": null,
          "col": null,
          "banked": 0,
          "owed": 5,
          "total": 5,
          "steps": [
            {
              "step_id": "98e06c9ca1af5556a49190c3ef853c85",
              "filter": "L",
              "frame_type": "Light",
              "exposure_s": 60.0,
              "count": 5,
              "banked": 0,
              "owed": 5
            }
          ]
        }
      ]
    }
  ],
  "orphaned": {"frames": 0, "steps": 0}
};

const mosaicOf = (p: FlowProgress): FlowProgressBlock => {
  const b = p.blocks.find((x) => x.node_id === "t");
  if (!b) throw new Error("precondition: the recorded answer has the mosaic block t");
  return b;
};

// MUTANT "every panel done when any is" (panelsChip, once it has counted,
// calls every panel done if any one owes nothing: `if (done > 0) done =
// panels.length;`). Observed, 28/32 (this case, the skipped case below and
// both cards' mosaic case):
//   x a mosaic block reads 'N/M panels done': the panels that owe nothing:
//     the mosaic's chip counts the panels whose owed is 0, over the panels it
//     shoots
//     expected "4/6 panels done"
//     got      "6/6 panels done"
// MUTANT "chip by subs for a mosaic" (the `if (isMosaic(block))` branch in
// progressChip deleted, so a grid reads as S1's sub total). Observed,
// 27/32 (this case, the skipped and unreadable cases below, and both cards'
// mosaic case):
//   x a mosaic block reads 'N/M panels done': the panels that owe nothing:
//     the mosaic's chip counts the panels whose owed is 0, over the panels it
//     shoots
//     expected "4/6 panels done"
//     got      "9/12 subs"
await test("a mosaic block reads 'N/M panels done': the panels that owe nothing", () => {
  const b = mosaicOf(PROGRESS_2X3);
  eq(b.panels.length, 6, "premise: the recorded 2x3 shoots six panels");
  eq(b.panels.filter((p) => p.owed === 0).length, 4, "premise: four of them owe nothing");
  eq(progressChip(PROGRESS_2X3, "t", false), "4/6 panels done",
    "the mosaic's chip counts the panels whose owed is 0, over the panels it shoots");
});

// MUTANT "over the whole grid" (M read as grid.rows * grid.cols, the grid as
// drawn, skipped panels included). Observed, 31/32:
//   x a skipped panel is in neither number, however much it banked: M is the
//     six panels the run will shoot; counted over the grid, two panels nobody
//     will shoot would keep the mosaic from ever reading done
//     expected "2/6 panels done"
//     got      "2/8 panels done"
await test("a skipped panel is in neither number, however much it banked", () => {
  const b = mosaicOf(PROGRESS_2X4_SKIPS);
  eq(b.grid?.rows ?? 0, 2, "premise: a 2x4 grid");
  eq(b.grid?.cols ?? 0, 4, "premise: a 2x4 grid");
  eq(b.skipped?.length ?? 0, 2, "premise: two panels skipped");
  eq(b.skipped?.[0]?.banked ?? 0, 2, "premise: the skipped 1-4 still holds 2 subs");
  eq(progressChip(PROGRESS_2X4_SKIPS, "t", false), "2/6 panels done",
    "M is the six panels the run will shoot; counted over the grid, two panels nobody "
    + "will shoot would keep the mosaic from ever reading done");
});

// CONTROL: the single target and the pool of the SAME recorded answers read
// exactly as S1 drew them. MUTANT "every TARGET is a mosaic" (isMosaic
// answers true without a grid). Observed, 17/32 (this control, and every S1
// case above that draws a TARGET's sub count, each reading "0/1 panels
// done"):
//   x CONTROL: the single target and the pool in the same answers read as S1
//     drew them: M31's chip, S1's formula
//     expected "3/5 subs"
//     got      "0/1 panels done"
await test("CONTROL: the single target and the pool in the same answers read as S1 drew them", () => {
  eq(progressChip(PROGRESS_2X3, "u", false), "3/5 subs", "M31's chip, S1's formula");
  eq(progressChip(PROGRESS_2X4_SKIPS, "u", false), "5/5 subs", "M31 complete, still in subs");
  eq(progressChip(PROGRESS_2X3, "p", false), null, "a POOL block still gets no chip");
  eq(progressChip(PROGRESS_2X4_SKIPS, "p", false), null, "a POOL block still gets no chip");
});

// A 1x1 GRID IS A SINGLE TARGET. The route adds `grid` only to a block of
// more than one panel (server `compile._is_mosaic`, rows x cols > 1), so this
// is the answer a server could send only by mistake, and isMosaic's
// docstring promises it still reads in subs: one panel "0/1 panels done" or
// "1/1 panels done" would hide the sub count S1 draws for exactly that block.
// The recorded M31 block with a 1x1 grid added, nothing else changed.
// MUTANT "a 1x1 grid is a mosaic" (isMosaic's `rows * cols > 1` read as
// `>= 1`), found surviving by the S3-U2 verifier and run in its private copy
// (scratchpad/s3-u2-verify-k7r2/). Observed, 32/33:
//   x a 1x1 grid reads as the single target it is, in subs: a 1x1 grid is
//     one panel: its chip is S1's sub count, not a fraction of panels
//     expected "3/5 subs"
//     got      "0/1 panels done"
await test("a 1x1 grid reads as the single target it is, in subs", () => {
  const m31 = PROGRESS_2X3.blocks.find((x) => x.node_id === "u");
  if (!m31) throw new Error("precondition: the recorded answer has the single target u");
  const oneByOne: FlowProgress = {
    ...PROGRESS_2X3, blocks: [{ ...m31, grid: { rows: 1, cols: 1 } }],
  };
  eq(progressChip(oneByOne, "u", false), "3/5 subs",
    "a 1x1 grid is one panel: its chip is S1's sub count, not a fraction of panels");
});

await test("CONTROL: a mosaic keeps S1's gates - no chip while dirty or with no session", () => {
  eq(progressChip(PROGRESS_2X3, "t", true), null, "an unsaved graph describes another flow");
  eq(progressChip({ ...PROGRESS_2X3, session: null }, "t", false), null,
    "a flow that never ran has nothing to count");
});

// MUTANT "unreadable owed counts as not done" (panelsChip's `if (owed ===
// null) return null;` deleted, so a panel it cannot read shrinks N without
// a word). Observed, 31/32:
//   x a mosaic answer that cannot be counted draws no chip and never throws:
//     a panel whose owed cannot be read cannot be counted done or not done
//     expected null
//     got      "4/6 panels done"
// MUTANT "0/0 for a mosaic with no panels" (the `panels.length === 0` test
// deleted). Observed, 31/32:
//   x a mosaic answer that cannot be counted draws no chip and never throws:
//     every panel skipped: '0/0 panels done' is a fraction of nothing
//     expected null
//     got      "0/0 panels done"
await test("a mosaic answer that cannot be counted draws no chip and never throws", () => {
  const b = mosaicOf(PROGRESS_2X3);
  const withPanels = (panels: unknown): FlowProgress => ({
    ...PROGRESS_2X3, blocks: [{ ...b, panels } as any],
  });
  eq(progressChip(withPanels(null), "t", false), null, "panels that is not a list");
  eq(progressChip(withPanels(b.panels.map((p, i) => (i === 1 ? { ...p, owed: undefined } : p))),
    "t", false), null,
    "a panel whose owed cannot be read cannot be counted done or not done");
  eq(progressChip(withPanels([]), "t", false), null,
    "every panel skipped: '0/0 panels done' is a fraction of nothing");
});

// ----------------------------------------------- both cards draw the mosaic
// The recorded answer's own node ids, so the cards find their blocks the way
// they do on a real canvas.
const MOSAIC_NODES: FlowNodeRec[] = [
  { id: "t", type: "target", x: 0, y: 0,
    params: { ...NODE_DEFS.target.params, name: "M16", rows: 2, cols: 3 } },
  { id: "u", type: "target", x: 240, y: 0,
    params: { ...NODE_DEFS.target.params, name: "M31" } },
  { id: "p", type: "pool", x: 480, y: 0, params: { ...NODE_DEFS.pool.params } },
];
const chipOnNode = (which: Which, id: string): string | null =>
  container.querySelector(`[data-node-id="${id}"]`)?.querySelector(CHIP_SEL[which])
    ?.textContent ?? null;

for (const which of ["classic", "next"] as const) {
  // MUTANT "every panel done when any is", on the cards. Observed:
  //   x [classic] the mosaic card reads its panels, the single target its
  //     subs: the classic mosaic card's chip
  //     expected "4/6 panels done"
  //     got      "6/6 panels done"
  //   x [next] the mosaic card reads its panels, the single target its subs:
  //     the next mosaic card's chip
  //     expected "4/6 panels done"
  //     got      "6/6 panels done"
  await test(`[${which}] the mosaic card reads its panels, the single target its subs`, () => {
    mount(which, { progress: PROGRESS_2X3, dirty: false }, false, MOSAIC_NODES);
    assert(container.querySelector(`[data-node-id="t"]`),
      `precondition: the ${which} mosaic card rendered`);
    eq(chipOnNode(which, "t"), "4/6 panels done", `the ${which} mosaic card's chip`);
    eq(chipOnNode(which, "u"), "3/5 subs", `the ${which} single target's chip moved`);
    eq(chipOnNode(which, "p"), null, `the ${which} POOL card drew a chip`);
  });
}

// ------------------------------------------------------------------- report
act(() => { root.unmount(); });
const total = passed + failed;
console.log(`flowProgressChip.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) (globalThis as any).process.exitCode = 1;
export default { passed, failed, total };
export { passed, failed, total };
