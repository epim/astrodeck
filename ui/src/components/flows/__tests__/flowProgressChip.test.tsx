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
//      flow's record with another flow's answer.
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
g.requestAnimationFrame = (cb: (t: number) => void) => setTimeout(() => cb(0), 0);
g.cancelAnimationFrame = (h: any) => clearTimeout(h);
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
function Deck({ which, phone = false }: { which: Which; phone?: boolean }) {
  return createElement(Fragment, null,
    NODES.map((n) => createElement(CARD[which] as any, { key: n.id, node: n, phone })));
}

function mount(which: Which, flows: Partial<FlowsState>, phone = false): void {
  act(() => root.render(null));
  act(() => {
    useStore.setState({
      flows: { ...FLOWS_INIT, graph: { nodes: NODES, edges: [] }, ...flows },
    } as any);
  });
  for (const k of Object.keys(renders)) renders[k] = 0;
  act(() => root.render(createElement(Deck, { which, phone })));
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

// ------------------------------------------------------------------- report
act(() => { root.unmount(); });
const total = passed + failed;
console.log(`flowProgressChip.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) (globalThis as any).process.exitCode = 1;
export default { passed, failed, total };
export { passed, failed, total };
