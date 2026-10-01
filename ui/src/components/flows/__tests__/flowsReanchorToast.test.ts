// flowsReanchorToast.test.ts - a save that restarts a block's counts says so
// in a toast as well as on the flow log (#189; spec 3.3, Revision 2 ruling 3;
// S4 orchestrator ruling 8).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/__tests__/flowsReanchorToast.test.ts   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT IS WORTH GUARDING HERE
//
// A save's answer lists every block whose framing moved too far for its counts
// to carry over (`reanchored`). Before ruling 8 that reached only the flow
// log, a strip the operator has to open, and the restart is the one thing a
// save does that costs nights: a nudged RA in the inspector starts a
// campaign's counts again, and unsaid the operator meets it as CONTINUE's
// dropped-steps question. So the save also enqueues ONE warn toast, through
// the store's one toast model (store.ts `enqueueToast`), naming each block
// once. The log line stays (flowsSaveAnswer.test.ts holds it).
//
// Two harnesses. A miniature store with a spy for `enqueueToast` counts the
// calls exactly. The app's own store proves the seam: the slice reaches the
// toast model through `get().enqueueToast`, an OPTIONAL member, so a rename
// on either side would make the call silently do nothing, and only the real
// store can show the toast landed in `toasts`.
//
// Every mutant below was run in a private scratch copy of ui/ (#254), and the
// failure it produced is quoted verbatim.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
// store.ts touches the document at import time (touch sizing), and lib/base.ts
// reads `window.location.pathname` at module scope. No request is made: every
// flowsApi call a save reaches is replaced below.
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(`<!doctype html><html><body></body></html>`,
                      { url: "http://local/", pretendToBeVisual: true });
const win = dom.window as any;
win.matchMedia = () => ({ matches: false, addEventListener() {},
                          removeEventListener() {}, addListener() {},
                          removeListener() {} });
win.WebSocket = class { close() {} addEventListener() {} send() {} };
const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "localStorage", "requestAnimationFrame", "cancelAnimationFrame",
  "getComputedStyle", "matchMedia", "WebSocket", "location",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}

const { useStore } = await import("../../../store");
const { createFlowsActions, FLOWS_INIT } = await import("../flowsSlice");
const { flowsApi } = await import("../../../lib/flowsApi");
type FlowsHost = import("../flowsSlice").FlowsHost;
type FlowsState = import("../flowsSlice").FlowsState;
type FlowToast = import("../flowsSlice").FlowToast;
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
    { id: "m31", type: "target", x: 0, y: 0, params: { name: "M31", rows: 3, cols: 2, counts: "Accepted subs" } },
    { id: "coords", type: "target", x: 0, y: 200, params: { name: "  ", ra: "05h 35m", counts: "Accepted subs" } },
    { id: "m33", type: "target", x: 0, y: 400, params: { name: "M33", counts: "Accepted subs" } },
    { id: "cy", type: "cycle", x: 300, y: 0, params: {} },
  ],
  edges: [{ id: "k1", from: "m31", fromPort: "target", to: "cy", toPort: "run" }],
};

function record(id = "f1") {
  return {
    id, name: `Flow ${id}`, folder: "My flows", tagline: "",
    graph: JSON.parse(JSON.stringify(GRAPH)),
    created_ts: 1, updated_ts: 1, last_run: null, last_result: "", readonly: false,
  };
}

/** What the next `PUT /api/flows/{id}` answers: the flow it was sent, plus
 *  `answer`. `hold` keeps the PUT open until released. */
let answer: Record<string, unknown> = {};
let hold: Promise<void> | null = null;
(flowsApi as any).save = async (_id: string, flow: any) => {
  if (hold) await hold;
  return JSON.parse(JSON.stringify({ ...flow, migrated: [], ...answer }));
};
(flowsApi as any).get = async (id: string) => record(id);
(flowsApi as any).compileDraft = async () => ({ plan: {}, structural: [], issues: [], unmapped: [] });
(flowsApi as any).progress = async () => { throw new Error("Not Found"); };
(flowsApi as any).list = async () => [];
(flowsApi as any).folders = async () => [];

/** A miniature store holding flow f1, open and dirty, with a spy standing in
 *  for the store's toast model. */
function harness() {
  let state: FlowsHost;
  const toasts: FlowToast[] = [];
  const set = (fn: (s: FlowsHost) => Partial<FlowsHost>) => {
    state = { ...state, ...fn(state) } as FlowsHost;
  };
  const get = () => state;
  const actions = createFlowsActions(set, get);
  const rec = record();
  state = {
    ...actions,
    enqueueToast: (t: FlowToast) => { toasts.push(t); },
    flows: { ...FLOWS_INIT, record: rec as never, graph: rec.graph, dirty: true },
  } as FlowsHost;
  return { get flows(): FlowsState { return state.flows; }, a: actions, toasts };
}

const TWO_BLOCKS = [
  { node_id: "m31", max_move_deg: 14.8 / 60, threshold_deg: 10 / 60 },
  { node_id: "coords", max_move_deg: 0.05, threshold_deg: 0 },
];

// ============================================================ the toast

// MUTANT "log only" (flowsSlice.ts flowsSave: the `enqueueToast` call
// deleted, the log lines kept). Observed (3/6 passed; this case, the
// once-each case and the app-store case failed):
//   x a save that re-anchored blocks enqueues ONE warn toast naming each, and still logs each line: a save that restarted two blocks' counts must raise exactly one toast
//   expected 1
//   got      0
//   x each block is named once, however many rows name it, and three read as a list: one block, two rows: one toast naming it once, in the singular
//   expected "TARGET \"M31\" starts counting from zero"
//   got      ""
//   x the app store's save lands the toast in store.ts's one toast queue: the save's toast never reached store.ts's toast queue
//   expected "warning: TARGET \"M31\" and a TARGET with no name start counting from zero"
//   got      ""
await test("a save that re-anchored blocks enqueues ONE warn toast naming each, and still logs each line", async () => {
  answer = { reanchored: TWO_BLOCKS };
  const h = harness();
  await h.a.flowsSave();
  eq(h.flows.dirty, false, "precondition: the save completed");
  eq(h.toasts.length, 1, "a save that restarted two blocks' counts must raise exactly one toast");
  eq(h.toasts[0].level, "warning", "the banked subs stop counting: a warning, not news");
  eq(h.toasts[0].title, 'TARGET "M31" and a TARGET with no name start counting from zero',
    "the toast must name every block the save re-anchored");
  eq(h.toasts[0].detail, "The subs they banked stay on disk. The flow log says what changed.",
    "the toast's second line");
  eq(h.flows.logs.filter((l) => l.tone === "warn").length, 2,
    "the toast is AS WELL AS the log line per block, not instead of it");
});

// MUTANT "a toast per row" (flowsSlice.ts flowsSave: `reanchorToast` called
// once per `reanchored` row with that row alone). Observed (3/6 passed;
// the first case and the app-store case failed with this one):
//   x each block is named once, however many rows name it, and three read as a list: one block, two rows: one toast naming it once, in the singular
//   expected "TARGET \"M31\" starts counting from zero"
//   got      "TARGET \"M31\" starts counting from zero | TARGET \"M31\" starts counting from zero"
await test("each block is named once, however many rows name it, and three read as a list", async () => {
  answer = { reanchored: [
    { node_id: "m31", max_move_deg: null, threshold_deg: null, reason: "grid" },
    { node_id: "m31", max_move_deg: null, threshold_deg: null, reason: "angle" },
  ] };
  const h = harness();
  await h.a.flowsSave();
  eq(h.toasts.map((t) => t.title).join(" | "), 'TARGET "M31" starts counting from zero',
    "one block, two rows: one toast naming it once, in the singular");
  eq(h.toasts[0]?.detail, "The subs it banked stay on disk. The flow log says what changed.",
    "the singular's second line");
  answer = { reanchored: [...TWO_BLOCKS, { node_id: "m33", max_move_deg: null, threshold_deg: null, reason: "identity" }] };
  const h3 = harness();
  await h3.a.flowsSave();
  eq(h3.toasts.map((t) => t.title).join(" | "),
    'TARGET "M31", a TARGET with no name and TARGET "M33" start counting from zero',
    "three blocks in one toast");
});

// ============================================================== controls

// CONTROL. An empty list is a save that restarted nothing; no key at all is
// an older server's answer; a counts switch alone is not a restart.
//
// MUTANT "a toast for every save answer" (reanchorToast: the empty-list
// check deleted, so the toast names nobody). Observed (4/6 passed; the
// app-store case's control arm failed with it):
//   x control: no toast when the answer re-anchored nothing: the answer {"reanchored":[]} raised a toast
//   expected 0
//   got      1
await test("control: no toast when the answer re-anchored nothing", async () => {
  for (const a of [{ reanchored: [] }, {}, { migrated: [{ key: "counts", note: "x" }] },
                   { reanchored: [null, "junk"] }]) {
    answer = a;
    const h = harness();
    await h.a.flowsSave();
    eq(h.flows.libraryError, null, `the answer ${JSON.stringify(a)} made the save fail`);
    eq(h.flows.dirty, false, "precondition: the save completed");
    eq(h.toasts.length, 0, `the answer ${JSON.stringify(a)} raised a toast`);
  }
});

// CONTROL. The answer arrives after another flow was opened: the blocks it
// names are not on screen, and CONTINUE's dropped-steps question still
// guards that flow's ledger when it next runs.
//
// MUTANT "toast before the stale check" (flowsSave: the toast raised
// straight after the PUT answers). Observed (5/6 passed):
//   x control: a save answered after another flow opened raises no toast: the answer for f1 raised a toast over f2
//   expected 0
//   got      1
await test("control: a save answered after another flow opened raises no toast", async () => {
  answer = { reanchored: TWO_BLOCKS };
  let release!: () => void;
  hold = new Promise<void>((r) => { release = r; });
  const h = harness();
  const saving = h.a.flowsSave();
  await h.a.flowsOpen("f2");
  eq(h.flows.record?.id, "f2", "precondition: the other flow opened");
  release();
  await saving;
  hold = null;
  eq(h.toasts.length, 0, "the answer for f1 raised a toast over f2");
});

await test("control: a miniature store with no toast model still saves and logs", async () => {
  // Every miniature store built before ruling 8 has no `enqueueToast`; the
  // save must not fail on it.
  answer = { reanchored: TWO_BLOCKS };
  let state: FlowsHost;
  const set = (fn: (s: FlowsHost) => Partial<FlowsHost>) => { state = { ...state, ...fn(state) } as FlowsHost; };
  const actions = createFlowsActions(set, () => state);
  const rec = record();
  state = { ...actions, flows: { ...FLOWS_INIT, record: rec as never, graph: rec.graph, dirty: true } } as FlowsHost;
  await actions.flowsSave();
  eq(state!.flows.libraryError, null, "a store without a toast model made the save fail");
  eq(state!.flows.logs.length, 2, "the log lines are still said");
});

// ============================================== the app store's toast model

// The seam. Under "log only" this fails too (see the first case). Under
// MUTANT "the store's toast action renamed" (store.ts: every `enqueueToast`
// renamed `queueToast`, so the store still works and has no member by the
// name the slice calls, which the optional chain turns into nothing), every
// miniature case above stays green, because each brings its own
// `enqueueToast`, and only this one fails. Observed (5/6 passed):
//   x the app store's save lands the toast in store.ts's one toast queue: the save's toast never reached store.ts's toast queue
//   expected "warning: TARGET \"M31\" and a TARGET with no name start counting from zero"
//   got      ""
await test("the app store's save lands the toast in store.ts's one toast queue", async () => {
  answer = { reanchored: TWO_BLOCKS };
  const rec = record();
  useStore.setState({ toasts: [], flows: { ...FLOWS_INIT, record: rec as never, graph: rec.graph, dirty: true } } as any);
  await useStore.getState().flowsSave();
  const st = useStore.getState();
  eq(st.flows.dirty, false, "precondition: the save completed");
  const mine = st.toasts.filter((t) => t.source === "flows");
  eq(mine.map((t) => `${t.level}: ${t.title}`).join(" | "),
    'warning: TARGET "M31" and a TARGET with no name start counting from zero',
    "the save's toast never reached store.ts's toast queue");
  answer = { reanchored: [] };
  useStore.setState({ toasts: [], flows: { ...st.flows, dirty: true } } as any);
  await useStore.getState().flowsSave();
  eq(useStore.getState().toasts.length, 0, "control: an empty list raised a toast in the app store");
});

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`flowsReanchorToast.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) (globalThis as any).process.exitCode = 1;
export const result = { passed, failed, total };
export default result;
