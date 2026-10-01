// flowsSaveRace.test.ts - an edit made while a SAVE is in flight is never
// marked saved (#215).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/__tests__/flowsSaveRace.test.ts   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT. `flowsSave` read `{ record, graph, dirty }`, awaited the PUT, and
// then wrote `dirty: false` whatever had happened meanwhile. An edit inside the
// PUT's round trip (a param, a drag, a wire, a rename) set `dirty` and the
// returning save cleared it again: the canvas showed the edit and the store
// said it was on the server. Every way out of the editor goes through
// `flowsCloseEditor`, whose save then declined on `!dirty`, so the edit was
// dropped on close without a word. And the TARGET chip, which hides while
// `dirty` because its answer is the SAVED flow's, drew the saved flow's count
// beside an unsaved recipe edit.
//
// It is intermittent on a rig (the window is one PUT's round trip, longer over
// the relay) but deterministic in code, so these tests hold the PUT open with a
// deferred `flowsApi.save` rather than racing a clock.
//
// Every guarded case names the mutant it kills and quotes the failure that
// mutant produced when it was run from a byte-for-byte backup of
// flowsSlice.ts (restored and hash-compared after each run). Against the code
// before #215 itself (dirty cleared unconditionally, no record check) the
// file was observed at 1/6: the five cases above the control all failed.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------- globals first
// The slice imports lib/flowsApi -> lib/api -> lib/base, and base.ts reads
// `window.location.pathname` at module scope. Nothing here fakes behaviour the
// tests then assert on.
(globalThis as any).window = {
  location: { pathname: "/", origin: "http://local" },
};
(globalThis as any).localStorage = {
  getItem: () => null, setItem() {}, removeItem() {},
};

const { createFlowsActions, FLOWS_INIT } = await import("../flowsSlice");
const { flowsApi } = await import("../../../lib/flowsApi");
const { NODE_DEFS } = await import("../nodeDefs");
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
/** Lets an awaited stub settle: it resolves in a microtask, and the slice's
 *  `await` resumes in the next. */
const flush = () => new Promise<void>((r) => setTimeout(r, 0));

// ------------------------------------------------------------------- stubs
function rec(id: string) {
  return {
    id, name: `Flow ${id}`, folder: "My flows", tagline: "",
    graph: { nodes: [{ id: "t1", type: "target", x: 0, y: 0,
                       params: { ...NODE_DEFS.target.params } }], edges: [] },
    created_ts: 1, updated_ts: 1, last_run: null, last_result: "", readonly: false,
  };
}

/** Every PUT the slice sent, held open until a test answers it. */
interface Put { id: string; flow: any; resolve: (v: unknown) => void }
let puts: Put[] = [];
(flowsApi as any).save = (id: string, flow: any) =>
  new Promise((resolve) => { puts.push({ id, flow, resolve }); });
(flowsApi as any).get = async (id: string) => rec(id);
(flowsApi as any).compileDraft = async () => ({ plan: {}, structural: [], issues: [], unmapped: [] });
(flowsApi as any).progress = async () => { throw new Error("Not Found"); };
(flowsApi as any).list = async () => [];
(flowsApi as any).folders = async () => [];

/** The server's answer to a PUT: what it was sent, stored, with a new stamp.
 *  The stamp is how a test sees that `record: saved` was kept. */
function answerPut(p: Put, ts: number): void {
  p.resolve({ ...p.flow, id: p.id, updated_ts: ts });
}

/** The slice alone behind a miniature store: the set/get contract zustand
 *  hands it. */
function slice() {
  let state: FlowsHost;
  const set = (fn: (s: FlowsHost) => Partial<FlowsHost>) => {
    state = { ...state, ...fn(state) } as FlowsHost;
  };
  const get = () => state;
  const actions = createFlowsActions(set, get);
  state = { ...actions, flows: { ...FLOWS_INIT } } as FlowsHost;
  puts = [];
  return { get flows(): FlowsState { return state.flows; }, a: actions };
}

const onCanvas = (f: FlowsState): unknown =>
  f.graph.nodes.find((n) => n.id === "t1")?.params.name;
const sentIn = (p: Put | undefined): string =>
  String(p?.flow?.graph?.nodes?.find((n: any) => n.id === "t1")?.params?.name ?? "-");

// ================================================= 1. AN EDIT DURING THE PUT

// MUTANT "clear dirty unconditionally" (flowsSave's `dirty: edited ||
// renamed` made `dirty: false`, the write before #215). Observed, 3/6 (this
// case, the rename case and the close case):
//   x an edit made while the PUT is in flight stays unsaved, and the next SAVE
//     sends it: the save marked M 33 as saved though its PUT carried M 31; the
//     second SAVE then sent nothing, so closing the editor would drop the edit
//     expected "dirty true, 2 PUTs"
//     got      "dirty false, 1 PUTs"
await test("an edit made while the PUT is in flight stays unsaved, and the next SAVE sends it", async () => {
  const s = slice();
  await s.a.flowsOpen("f1");
  s.a.flowsSetParam("t1", "name", "M 31");
  const saving = s.a.flowsSave();
  eq(puts.length, 1, "precondition: the save sent one PUT");
  eq(sentIn(puts[0]), "M 31", "precondition: the PUT carries M 31");
  s.a.flowsSetParam("t1", "name", "M 33");
  eq(s.flows.dirty, true, "precondition: the edit during the PUT made the graph dirty");
  answerPut(puts[0], 2);
  await saving;
  eq(s.flows.record?.updated_ts, 2, "the server's record was not kept; the next PUT would carry a stale one");
  eq(onCanvas(s.flows), "M 33", "the canvas lost the edit made during the PUT");
  const dirtyAfter = s.flows.dirty;
  const again = s.a.flowsSave();
  eq(`dirty ${dirtyAfter}, ${puts.length} PUTs`, "dirty true, 2 PUTs",
    "the save marked M 33 as saved though its PUT carried M 31; the second SAVE then "
    + "sent nothing, so closing the editor would drop the edit");
  eq(sentIn(puts[1]), "M 33", "the second PUT did not carry the edit made during the first");
  answerPut(puts[1], 3);
  await again;
  eq(s.flows.dirty, false, "the second save, with nothing edited during it, left the flow unsaved");
});

// MUTANT "check graph only" (flowsSave's `dirty: edited || renamed` made
// `dirty: edited`). Observed, 5/6 ("clear dirty unconditionally" fails this
// case with the same line):
//   x a rename made while the PUT is in flight stays unsaved, and the next
//     SAVE sends it: the PUT carried 'Flow f1'; the rename made during it was
//     marked saved and the second SAVE sent nothing
//     expected "dirty true, 2 PUTs, the second named Andromeda"
//     got      "dirty false, 1 PUTs, the second named -"
// MUTANT "the server's name over the rename" (flowsSave's `record: renamed ?
// { ...saved, name: cur.name } : saved` made `record: saved`). Observed, 5/6:
//   x a rename made while the PUT is in flight stays unsaved, and the next
//     SAVE sends it: the returning save put the old name back on the open
//     record
//     expected "Andromeda"
//     got      "Flow f1"
await test("a rename made while the PUT is in flight stays unsaved, and the next SAVE sends it", async () => {
  const s = slice();
  await s.a.flowsOpen("f1");
  s.a.flowsSetParam("t1", "name", "M 31");
  const saving = s.a.flowsSave();
  eq(puts[0]?.flow?.name, "Flow f1", "precondition: the PUT carries the old name");
  s.a.flowsSetName("Andromeda");
  answerPut(puts[0], 2);
  await saving;
  eq(s.flows.record?.name, "Andromeda", "the returning save put the old name back on the open record");
  eq(s.flows.record?.updated_ts, 2, "the server's record was not kept");
  const dirtyAfter = s.flows.dirty;
  const again = s.a.flowsSave();
  eq(`dirty ${dirtyAfter}, ${puts.length} PUTs, the second named ${puts[1]?.flow?.name ?? "-"}`,
    "dirty true, 2 PUTs, the second named Andromeda",
    "the PUT carried 'Flow f1'; the rename made during it was marked saved and the second SAVE sent nothing");
  answerPut(puts[1], 3);
  await again;
  eq(s.flows.dirty, false, "the second save, with nothing renamed during it, left the flow unsaved");
});

// MUTANT "clear dirty unconditionally". Observed, 3/6:
//   x closing the editor after an edit made during a save sends that edit:
//     closing dropped the edit made during the save; the canvas showed M 33
//     and the server kept M 31
//     expected "2 PUTs, the last carrying M 33"
//     got      "1 PUTs, the last carrying -"
await test("closing the editor after an edit made during a save sends that edit", async () => {
  const s = slice();
  await s.a.flowsOpen("f1");
  s.a.flowsSetParam("t1", "name", "M 31");
  const saving = s.a.flowsSave();
  s.a.flowsSetParam("t1", "name", "M 33");
  answerPut(puts[0], 2);
  await saving;
  const closing = s.a.flowsCloseEditor();
  await flush();
  const summary = `${puts.length} PUTs, the last carrying ${sentIn(puts[1])}`;
  if (puts[1]) answerPut(puts[1], 3);
  await closing;
  eq(s.flows.record, null, "precondition: the editor closed");
  eq(summary, "2 PUTs, the last carrying M 33",
    "closing dropped the edit made during the save; the canvas showed M 33 and the server kept M 31");
});

// A STALE COMPLETION ONTO ANOTHER FLOW. The same class as #215, and the fix
// for #215 would have made it worse without this guard: once the save
// compares the graph it sent against the graph open NOW, a save of f1 that
// returns after f2 was opened sees f2's graph, calls it an unsaved edit, and
// leaves f1's record beside it - so the next close PUTs f2's graph into f1's
// file. Before #215 the same answer wrote f1's record over f2's with dirty
// false, a corruption waiting for f2's first edit.
//
// MUTANT "no record check on the save's answer" (flowsSave's
// `if (!cur || cur.id !== record.id) return;` made `if (!cur) return;`).
// Observed, 5/6:
//   x a save that returns after another flow opened writes nothing onto it:
//     f1's save answer landed on f2: f2's graph now sits under f1's record and
//     is marked unsaved, so closing the editor PUTs it into f1's file
//     expected "open f2, dirty false, PUTs on close 0"
//     got      "open f1, dirty true, PUTs on close 1"
// RE-PINNED (W2 integration, #500/#162 backlog WP-16 (b), owner-approved
// 2026-09-30): `flowsOpen` now awaits a carried save's own promise (f1's PUT
// above IS the one `flowsOpen("f2")` is about to wait on: nothing edited the
// graph again after `flowsSave()`), instead of sending a second PUT and
// letting a stale answer race the open unobserved. Answering `puts[0]` AFTER
// the open, as this test used to, now deadlocks `flowsOpen` on its own
// await, so it is answered FIRST. The save therefore settles on f1 (still
// open) before the switch to f2, so the no-corruption outcome below no
// longer needs `flowsSave`'s stale-id guard for THIS race (a save
// `flowsOpen` is not waiting on, a non-carried one, still does); the
// assertion is unchanged, because the operator-visible promise -- f1's
// answer never lands on f2 -- is the stronger guarantee now, not a weaker
// one.
await test("a save that returns after another flow opened writes nothing onto it", async () => {
  const s = slice();
  await s.a.flowsOpen("f1");
  s.a.flowsSetParam("t1", "name", "M 31");
  const saving = s.a.flowsSave();
  answerPut(puts[0], 2);
  await s.a.flowsOpen("f2");
  eq(s.flows.record?.id, "f2", "precondition: f2 opened once f1's carried save settled");
  await saving;
  const open = s.flows.record?.id;
  const dirty = s.flows.dirty;
  // Not awaited: under the mutant the close sends a PUT nobody answers.
  void s.a.flowsCloseEditor();
  await flush();
  eq(`open ${open}, dirty ${dirty}, PUTs on close ${puts.length - 1}`,
    "open f2, dirty false, PUTs on close 0",
    "f1's save answer landed on f2: f2's graph now sits under f1's record and is marked "
    + "unsaved, so closing the editor PUTs it into f1's file");
});

// A STALE COMPLETION ONTO A CLOSED EDITOR: the same guard's other arm. SAVE,
// then leave the editor before the PUT answers. The close sends its own PUT
// (the flow is still dirty until the first one answers), and when the answers
// arrive out of order the first save returns to a store with no flow open.
// Before #215 it wrote `record: saved` there, putting the closed flow back
// behind the library; the rename check #215 added reads `cur.name`, so without
// the null test it throws instead, and the catch turns a TypeError into the
// library's error banner.
//
// MUTANT "no null test on the save's answer" (flowsSave's
// `if (!cur || cur.id !== record.id) return;` made
// `if (cur && cur.id !== record.id) return;`). Observed, 5/6:
//   x a save that returns after the editor closed writes nothing: the first
//     save's answer landed after the close: it put the closed flow back in
//     the store, or showed a TypeError on the library
//     expected "record null, dirty false, error null"
//     got      "record null, dirty false, error Cannot read properties of null (reading 'name')"
// Against the pre-#215 write (`record: saved, dirty: false`, no guard) the
// same case got "record f1, dirty false, error null".
await test("a save that returns after the editor closed writes nothing", async () => {
  const s = slice();
  await s.a.flowsOpen("f1");
  s.a.flowsSetParam("t1", "name", "M 31");
  const saving = s.a.flowsSave();
  const closing = s.a.flowsCloseEditor();
  eq(puts.length, 2, "precondition: the close sent its own PUT while the first was in flight");
  answerPut(puts[1], 3);
  await closing;
  eq(s.flows.record, null, "precondition: the editor closed on the close's PUT");
  eq(s.flows.libraryError ?? null, null, "precondition: the close left no error");
  answerPut(puts[0], 2);
  await saving;
  eq(`record ${s.flows.record?.id ?? null}, dirty ${s.flows.dirty}, error ${s.flows.libraryError ?? null}`,
    "record null, dirty false, error null",
    "the first save's answer landed after the close: it put the closed flow back in the "
    + "store, or showed a TypeError on the library");
});

// CONTROL: nothing happened during the PUT, so the save is complete.
// MUTANT "never clear dirty" (flowsSave's `dirty: edited || renamed` made
// `dirty: true`). Observed, 3/6 (this case, and the closing "the second save
// ... left the flow unsaved" line of both in-flight cases):
//   x CONTROL: no edit during the PUT marks the flow saved, and one PUT is
//     all it takes: a save with nothing edited during it left the flow unsaved,
//     so every SAVE and every close would PUT the same graph again
//     expected "dirty false, 1 PUTs"
//     got      "dirty true, 2 PUTs"
await test("CONTROL: no edit during the PUT marks the flow saved, and one PUT is all it takes", async () => {
  const s = slice();
  await s.a.flowsOpen("f1");
  s.a.flowsSetParam("t1", "name", "M 31");
  const saving = s.a.flowsSave();
  answerPut(puts[0], 2);
  await saving;
  const dirtyAfter = s.flows.dirty;
  // Not awaited: under the mutant this sends a PUT nobody answers.
  void s.a.flowsSave();
  eq(`dirty ${dirtyAfter}, ${puts.length} PUTs`, "dirty false, 1 PUTs",
    "a save with nothing edited during it left the flow unsaved, so every SAVE and "
    + "every close would PUT the same graph again");
  eq(s.flows.record?.updated_ts, 2, "the server's record was not kept");
  eq(onCanvas(s.flows), "M 31", "the canvas changed under a save");
});

// ------------------------------------------------------------------- report
const total = passed + failed;
console.log(`flowsSaveRace.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) (globalThis as any).process.exitCode = 1;
export default { passed, failed, total };
export { passed, failed, total };
