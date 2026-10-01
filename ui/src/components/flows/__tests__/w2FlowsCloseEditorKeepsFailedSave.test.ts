// w2FlowsCloseEditorKeepsFailedSave.test.ts - flowsCloseEditor keeps the graph
// when its own save fails (#500, part of WP-16 (b); backlog ruling,
// owner-approved 2026-09-30: "flowsCloseEditor keeps the graph when its save
// fails").
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/__tests__/w2FlowsCloseEditorKeepsFailedSave.test.ts   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT. "every way out saves first" (openFlow.ts) was kept by
// `flowsOpen`'s own refusal (#450): a save that does not keep its edits
// refuses the open and leaves the record, the graph and `dirty` exactly as
// they were. `flowsCloseEditor` never got that half: it `await`s its save and
// then clears `record`, `graph` and `dirty` UNCONDITIONALLY, whatever that
// save did. A refused PUT (5xx, 422, a dropped connection) left `dirty` true
// (flowsSave's catch writes `libraryError` and otherwise leaves the state
// alone) and the close threw the edit away anyway - worse than #450's open,
// because the close then reloads the library, and a successful
// `flowsLoadLibrary` clears `libraryError` too, so the save's own failure text
// is gone as well: the operator finds out only when they reopen the flow or
// when a night runs the old graph.
//
// WHAT MUST STILL WORK: an edited read-only Example, whose save the server
// always declines (`flowsSave` returns at once on `record.readonly`, so
// `dirty` never clears on its own) must still be closeable - refusing it would
// trap the operator in an Example forever, the same carve-out `flowsOpen`
// makes for it (#450).

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------- globals first
(globalThis as any).window = { location: { pathname: "/", origin: "http://local" } };
(globalThis as any).localStorage = { getItem: () => null, setItem() {}, removeItem() {} };

const {
  createFlowsActions, FLOWS_INIT, FLOW_NOT_OPENED, FLOW_OPEN_OVER_UNSAVED,
} = await import("../flowsSlice");
const { flowsApi } = await import("../../../lib/flowsApi");
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
function rec(id: string, over: Record<string, unknown> = {}) {
  return {
    id, name: `Flow ${id}`, folder: "My flows", tagline: "",
    graph: { nodes: [{ id: "t1", type: "target", x: 0, y: 0, params: { name: "M31" } }], edges: [] },
    created_ts: 1, updated_ts: 1, last_run: null, last_result: "", readonly: false,
    ...over,
  };
}

/** Set per test: when truthy, every PUT rejects with this message. */
let putFails: string | null = null;
let listCalled = 0;
(flowsApi as any).get = async (id: string) =>
  (id === "example" ? rec(id, { readonly: true }) : rec(id));
(flowsApi as any).save = async (_id: string, flow: any) => {
  if (putFails) throw new Error(putFails);
  return { ...flow, id: _id, updated_ts: (flow.updated_ts ?? 1) + 1 };
};
(flowsApi as any).compileDraft = async () => ({ plan: {}, structural: [], issues: [], unmapped: [] });
(flowsApi as any).progress = async () => { throw new Error("Not Found"); };
(flowsApi as any).list = async () => { listCalled++; return []; };
(flowsApi as any).folders = async () => [];

/** The slice alone, behind a miniature store. */
function slice() {
  let state: FlowsHost;
  const toasts: { level: string; title: string; detail?: string }[] = [];
  const set = (fn: (s: FlowsHost) => Partial<FlowsHost>) => {
    state = { ...state, ...fn(state) } as FlowsHost;
  };
  const get = () => state;
  const actions = createFlowsActions(set, get);
  state = {
    ...actions, flows: { ...FLOWS_INIT },
    enqueueToast: (t: any) => { toasts.push(t); },
  } as FlowsHost;
  putFails = null;
  listCalled = 0;
  return { get flows(): FlowsState { return state.flows; }, a: actions, toasts };
}

// =============================================== 1. THE DEFECT, FIXED
//
// MUTANT "close clears unconditionally" (flowsSlice.ts flowsCloseEditor: the
// `if (now.dirty && !now.record?.readonly) { ...; return; }` guard removed, so
// the unconditional clear below always runs). Observed, byte-for-byte
// restored and sha256-compared afterward:
//   x closing the editor over a save that fails keeps the record, the graph
//     and the edit, and says why: the close cleared the edit though its own
//     save failed
//     expected "f1"
//     got      null
await test("closing the editor over a save that fails keeps the record, the graph and the edit, and says why", async () => {
  const s = slice();
  await s.a.flowsOpen("f1");
  s.a.flowsSetParam("t1", "name", "M 33");
  eq(s.flows.dirty, true, "precondition: the edit is unsaved");
  putFails = "the flow store is not writable";
  await s.a.flowsCloseEditor();
  eq(s.flows.record?.id ?? null, "f1",
    "the close cleared the edit though its own save failed");
  eq(s.flows.dirty, true, "the edit must still read as unsaved");
  eq(s.flows.graph.nodes.find((n) => n.id === "t1")?.params.name, "M 33",
    "the edited graph must still be on screen");
  eq(s.flows.ui.screen, "editor", "the screen must stay on the editor, not fall back to the library");
  eq(s.flows.libraryError, FLOW_OPEN_OVER_UNSAVED, "the refusal's reason, where callers read it");
  eq(JSON.stringify(s.toasts.map((t) => [t.level, t.title, t.detail]).slice(-1)[0]),
    JSON.stringify(["error", FLOW_NOT_OPENED, FLOW_OPEN_OVER_UNSAVED]),
    "a toast saying the close did not land, and why");
  eq(listCalled, 0, "a refused close must not reload the library over the kept edit");
});

// ===================================================================== 2. CONTROL
//
// A save that SUCCEEDS still closes exactly as before: the control that makes
// case 1 meaningful rather than "flowsCloseEditor now always refuses".
await test("CONTROL: closing the editor over a save that succeeds still clears and reloads", async () => {
  const s = slice();
  await s.a.flowsOpen("f1");
  s.a.flowsSetParam("t1", "name", "M 33");
  await s.a.flowsCloseEditor();
  eq(s.flows.record, null, "a clean close must still clear the record");
  eq(s.flows.dirty, false, "a clean close must still clear dirty");
  eq(s.flows.ui.screen, "library", "a clean close must still return to the library");
  eq(listCalled, 1, "a clean close must still reload the library");
});

// =========================================== 3. THE CARVE-OUT: AN EXAMPLE
//
// `flowsSave` declines a readonly record at once, so `dirty` never clears on
// its own; without the same carve-out `flowsOpen` makes (#450), the fix above
// would trap an edited Example in the editor forever.
// MUTANT "close refuses an Example too" (flowsSlice.ts flowsCloseEditor: the
// `!now.record?.readonly` carve-out dropped, so `if (now.dirty)` alone
// guards the refusal). Observed, byte-for-byte restored and sha256-compared
// afterward:
//   x an edited read-only Example still closes, though its save never clears
//     dirty: an edited Example must not trap the operator in the editor
//     expected null
//     got      {"id":"example","name":"Flow example", ... ,"readonly":true}
await test("an edited read-only Example still closes, though its save never clears dirty", async () => {
  const s = slice();
  await s.a.flowsOpen("example");
  eq(s.flows.record?.readonly, true, "precondition: the open record is read-only");
  // An edit on the canvas: `flowsSave` returns at once for a readonly record
  // (it sends nothing), so `dirty` never clears on its own - the shape every
  // edited Example is in for as long as it stays open.
  s.a.flowsSetParam("t1", "name", "M 33");
  eq(s.flows.dirty, true, "precondition: the Example reads dirty, with nothing to save it");
  await s.a.flowsCloseEditor();
  eq(s.flows.record, null, "an edited Example must not trap the operator in the editor");
  eq(s.flows.ui.screen, "library", "an edited Example must still return to the library");
});

// ------------------------------------------------------------------- report
const total = passed + failed;
console.log(`w2FlowsCloseEditorKeepsFailedSave.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) (globalThis as any).process.exitCode = 1;
export default { passed, failed, total };
export { passed, failed, total };
