// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// hubSwitchSavesFlow.test.tsx - an edited flow survives a hub switch: the next
// flowsOpen from another hub saves it before it reads the other flow (#450).
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/next/__tests__/hubSwitchSavesFlow.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT. The editor's rule "every way out saves first" (openFlow.ts) was
// kept by the exits that are components: the canvas host's BACK and its
// stranded-route effect, the Flows screen's effect, the stage sheet's. NextApp
// renders only the active hub (`const Hub = HUBS[route.hub]`), so pressing
// another hub's rail button unmounts the whole Session hub, none of those
// runs, and `flows.dirty` stays set with the edited graph in the store. The
// next `flowsOpen` of another flow from that hub (the Sky's flow card, its
// quick flow, Tonight, RUN on a Now row) replaced the record with no save and
// no word. Since #450 `flowsOpen` itself saves a dirty open record of another
// id first, and refuses, saying why, when that save does not keep its edits.
//
// WHAT IS GRADED
//
//   1. THE REAL HUB SWITCH. NextApp mounted at the tablet width on
//      `#/session/flows?open=<A>`, where the canvas host opens flow A; an
//      edit; the Sky hub's rail button; then the Sky's flow card for flow B
//      (`#/sky/flow?id=<B>`, whose deep-link effect opens B). The PUT of A,
//      carrying the edit, comes before the GET of B, and B opens.
//   2. THE REFUSAL, through the same switch: A's PUT fails, so B is never
//      read, A stays open with its edit, and the store says why, in a toast
//      and in `libraryError`.
//   3. THE CONTROLS, on the store: a clean record is replaced with no PUT, and
//      so is an edited read-only Example, which the server would refuse to
//      save and which must never trap the operator.
//
// Every mutant below was run in a private scratch copy of ui/ (scratchpad
// S7-USLICE-mut, #254, #475), never in the shared tree, and the failure it
// produced is quoted verbatim.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------------ css stub
// NextApp imports next.css and shell/shell.css, and the hubs their own; Node
// has no loader for CSS.
{
  const { registerHooks } = await import("node:module");
  registerHooks({
    load(url: string, context: any, nextLoad: any) {
      if (url.endsWith(".css")) {
        return { format: "module", shortCircuit: true, source: "export default {};" };
      }
      return nextLoad(url, context);
    },
  } as any);
}

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/", pretendToBeVisual: true },
);
const win = dom.window as any;
// The tablet width: the Flows canvas is the workspace there (a phone gets the
// stage list instead), and the hubs are switched from the rail.
const VIEWPORT_W = 900;
win.matchMedia = (q: string) => {
  const m = /min-width:\s*(\d+)px/.exec(q);
  return {
    matches: m ? VIEWPORT_W >= Number(m[1]) : false,
    addEventListener() {}, removeEventListener() {},
    addListener() {}, removeListener() {},
  };
};
win.WebSocket = class {
  static OPEN = 1;
  readyState = 0;
  close() {}
  send() {}
  addEventListener() {}
  removeEventListener() {}
};
win.Element.prototype.setPointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.releasePointerCapture = function () { /* jsdom has none */ };
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
win.HTMLCanvasElement.prototype.getContext = function () { return null; };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement", "HTMLSelectElement",
  "HTMLTextAreaElement", "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "PointerEvent", "FocusEvent", "localStorage", "sessionStorage", "getComputedStyle", "matchMedia",
  "WebSocket", "requestAnimationFrame", "cancelAnimationFrame", "ResizeObserver",
  "HTMLCanvasElement", "HTMLImageElement", "Image", "SVGElement", "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  if (v !== undefined) Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------- the fake rig
const A = "flow-a";
const B = "flow-b";
const EXAMPLE = "example-flow";
function record(id: string, over: Record<string, unknown> = {}): any {
  return {
    id, name: id === A ? "M31 LRGB" : id === B ? "M33 Ha" : "Example", folder: "My flows",
    tagline: "", readonly: false, created_ts: 1, updated_ts: 2, last_run: null, last_result: "",
    graph: {
      nodes: [
        { id: "t1", type: "target", x: 40, y: 40, params: { name: id === A ? "M31" : "M33", counts: "Accepted subs" } },
        { id: "c1", type: "capture", x: 300, y: 40, params: { filter: "L", exposure: 60, count: 10 } },
      ],
      edges: [{ id: "e1", from: "t1", fromPort: "target", to: "c1", toPort: "run" }],
    },
    ...over,
  };
}
const CARDS = [A, B].map((id) => ({
  id, name: record(id).name, folder: "My flows", tagline: "", readonly: false,
  stages: 2, wires: 1, last_run: null, last_result: "", updated_ts: 2,
}));

/** Every request, in order, as "METHOD url". */
let asked: string[] = [];
/** The body of every PUT, by flow id. */
let putBodies: Record<string, any> = {};
/** When set, a PUT of that flow is refused with a 500. */
let refusePut: string | null = null;

g.fetch = async (url: any, init?: any) => {
  const u = String(url);
  const method = init?.method ?? "GET";
  asked.push(`${method} ${u}`);
  const reply = (status: number, data: unknown) => ({
    ok: status >= 200 && status < 300, status, statusText: String(status),
    headers: { get: () => "application/json" },
    json: async () => data,
    text: async () => JSON.stringify(data),
  });
  if (u === "/api/flows") return reply(200, CARDS);
  if (u === "/api/flows/folders") return reply(200, [{ name: "My flows", count: 2, readonly: false }]);
  if (u === "/api/flows/compile") return reply(200, { plan: {}, structural: [], issues: [], unmapped: [] });
  const one = /^\/api\/flows\/([^/?]+)$/.exec(u);
  if (one) {
    const id = one[1];
    if (method === "PUT") {
      if (refusePut === id) return reply(500, { detail: "the flow store is not writable" });
      const body = init?.body ? JSON.parse(init.body) : {};
      putBodies[id] = body;
      return reply(200, { ...(body.flow ?? body), id });
    }
    if (id === A || id === B) return reply(200, record(id));
  }
  return reply(404, { detail: "not in this fixture" });
};

// ------------------------------------------------------------------ imports
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../store");
const { FLOWS_INIT, FLOW_NOT_OPENED, FLOW_OPEN_OVER_UNSAVED } = await import("../../components/flows/flowsSlice");
const { resetFlowEditorDoorForTests } = await import("../hubs/session/flows/openFlow");
const { resetRouterCacheForTests } = await import("../router");
const NextApp = (await import("../NextApp")).default;
// The hub bodies are code-split; warm them so a rail press resolves from the
// module registry inside act() rather than racing a file read.
const { HUB_LOADERS } = await import("../hubs");
await Promise.all(Object.values(HUB_LOADERS).map((load) => load()));

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: unknown, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (JSON.stringify(got) !== JSON.stringify(want)) {
    throw new Error(`${msg}\n  expected ${JSON.stringify(want)}\n  got      ${JSON.stringify(got)}`);
  }
}
const settle = async (n = 8) => {
  for (let i = 0; i < n; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};
async function until(what: string, cond: () => boolean): Promise<void> {
  for (let i = 0; i < 80; i++) {
    if (cond()) return;
    await settle(1);
  }
  throw new Error(`timed out waiting for ${what}; requests: ${asked.join(", ")}`);
}

const ALL_CAPS = [
  "view.status", "view.media", "view.weather", "view.site_precise", "view.site_derived",
  "control.capture", "control.mount", "control.guide", "control.power",
  "config.backend", "config.safety", "config.site_optics", "config.alerts",
  "admin.users", "system.update",
];
function seed(): void {
  useStore.setState({
    authMethods: { methods: [], first_run: false } as any,
    principal: { role: "admin", email: null, caps: ALL_CAPS } as any,
    authGate: "open",
    wsPhase: "up",
    wsConnected: true,
    telemetryStale: false,
    equipConnected: true,
    toasts: [],
    confirm: null,
    resumeArm: null,
    runBanner: null,
    sequence: { state: "idle" } as any,
    status: {
      connected: { camera: { connected: true, name: "sim camera" }, telescope: { connected: true, name: "sim mount" } },
      looping: false, mode: "sim",
    } as any,
    flows: { ...FLOWS_INIT, ui: { ...FLOWS_INIT.ui } },
  } as never);
}

const container = win.document.getElementById("root") as any;
let root: any = null;
async function unmountApp(): Promise<void> {
  if (root) await act(async () => { root.unmount(); });
  root = null;
}
async function mountAt(hash: string): Promise<void> {
  await unmountApp();
  resetFlowEditorDoorForTests();
  win.location.hash = hash;
  resetRouterCacheForTests();
  root = createRoot(container);
  await act(async () => { root.render(createElement(NextApp)); });
  await settle();
  act(() => { useStore.setState({ wsPhase: "up", wsConnected: true } as never); });
}
function click(el: any, what: string): void {
  assert(el != null, `no ${what} to press`);
  act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
}
const byId = (id: string) => container.querySelector(`[data-testid="${id}"]`) as any;

/** Flow A open on the canvas, edited, and the Sky hub pressed on the rail. */
async function editAThenSwitchToSky(): Promise<void> {
  // The last case's app comes down before the store is reseeded under it.
  await unmountApp();
  seed();
  asked = [];
  putBodies = {};
  await mountAt(`#/session/flows?open=${A}`);
  await until("flow A to open on the canvas", () => useStore.getState().flows.record?.id === A);
  assert(asked.includes(`GET /api/flows/${A}`), `precondition: the canvas host did not read flow A: ${asked.join(", ")}`);
  // THE EDIT: a node moved on the canvas.
  act(() => { useStore.getState().flowsMoveNode("t1", 120, 80); });
  eq(useStore.getState().flows.dirty, true, "precondition: the edit is unsaved");
  asked = [];
  click(byId("rail-sky"), "Sky rail button");
  await settle();
  eq(win.location.hash, "#/sky", "precondition: the rail did not switch to the Sky hub");
  // THE DEFECT'S PREMISE: the switch alone saved nothing, and the edit sits
  // in the store with `dirty` set, the Session hub and its exits unmounted.
  eq(asked.filter((a) => a.startsWith("PUT ")), [], "precondition: the hub switch itself sent a PUT");
  eq(useStore.getState().flows.dirty, true, "precondition: the edit is no longer unsaved after the switch");
  eq(useStore.getState().flows.record?.id, A, "precondition: flow A is no longer the open record");
}

/** The Sky's flow card for B, by its deep link: its effect opens B. */
async function openBFromSky(): Promise<void> {
  // jsdom queues `hashchange` as a task, so the route moves after this act()
  // returns unless the act waits for it.
  await act(async () => {
    win.location.hash = `#/sky/flow?id=${B}`;
    await new Promise((r) => setTimeout(r, 0));
  });
  await settle();
}

// ======================================================================
// 1. the real hub switch

// MUTANT "replace without saving" (flowsSlice.ts flowsOpen: the save-first
// block deleted, so the open reads B and replaces A at once, as before
// #450). Observed ("hubSwitchSavesFlow.test: 1/3 passed"; the Sky hub's own
// reads elided from the second request list as "..."):
//   x a hub switch, then the Sky's flow card for another flow: the edit is PUT before the other flow is read: flow A's edit was not saved before flow B was read: GET /api/flows/flow-b, GET /api/flows/flow-b/progress, POST /api/flows/compile
//   x when the save does not keep the edit, the other flow is not read, the edit stays, and the store says why: timed out waiting for flow A's PUT; requests: GET /api/sequence/resume-arm, ..., GET /api/flows/flow-b, GET /api/flows/flow-b/progress, POST /api/flows/compile, ...
// It is red in sendToWizardSheet.test.tsx's two open cases and in
// compileAfterSave.test.ts's case of an edit made after the SAVE went out,
// each quoted there.
await test("a hub switch, then the Sky's flow card for another flow: the edit is PUT before the other flow is read", async () => {
  await editAThenSwitchToSky();
  await openBFromSky();
  await until("flow B to open", () => useStore.getState().flows.record?.id === B || asked.includes(`GET /api/flows/${B}`));
  await settle();
  const put = asked.indexOf(`PUT /api/flows/${A}`);
  const get = asked.indexOf(`GET /api/flows/${B}`);
  assert(put >= 0 && get > put,
    `flow A's edit was not saved before flow B was read: ${asked.filter((a) => a.includes("/api/flows/")).join(", ")}`);
  const saved = putBodies[A]?.flow?.graph ?? putBodies[A]?.graph;
  eq(saved?.nodes?.find((n: any) => n.id === "t1")?.x, 120, "the graph the PUT carried (the moved node)");
  eq(useStore.getState().flows.record?.id, B, "flow B is not the open record");
  eq(useStore.getState().flows.dirty, false, "flow B opened dirty");
});

// ======================================================================
// 2. the refusal

// MUTANT "open despite a failed save" (flowsSlice.ts flowsOpen: the refusal's
// `return` removed, so a save that kept nothing still lets the read and the
// replace go ahead). Observed ("hubSwitchSavesFlow.test: 2/3 passed"):
//   x when the save does not keep the edit, the other flow is not read, the edit stays, and the store says why: flow B was read over an edit that did not save
//   expected []
//   got      ["GET /api/flows/flow-b"]
//
// MUTANT "a refusal leaves libraryError" (flowsSlice.ts flowsOpen: the
// refusal's `libraryError` write deleted, so `openFlowById`'s callers read
// the failed PUT's own text, or nothing). Observed ("hubSwitchSavesFlow.test:
// 2/3 passed"):
//   x when the save does not keep the edit, the other flow is not read, the edit stays, and the store says why: the refusal's reason where openFlowById's callers read it
//   expected "The flow open in the editor has edits that did not save, and opening this one would drop them. Save or close that flow first."
//   got      "the flow store is not writable"
//
// MUTANT "refuse without a word" (flowsSlice.ts flowsOpen: the refusal's
// toast deleted). Observed ("hubSwitchSavesFlow.test: 2/3 passed"):
//   x when the save does not keep the edit, the other flow is not read, the edit stays, and the store says why: the toast saying the other flow did not open, and why
//   expected [["error","The flow open in the editor has edits that did not save, and opening this one would drop them. Save or close that flow first."]]
//   got      []
await test("when the save does not keep the edit, the other flow is not read, the edit stays, and the store says why", async () => {
  await editAThenSwitchToSky();
  refusePut = A;
  try {
    await openBFromSky();
    await until("flow A's PUT", () => asked.includes(`PUT /api/flows/${A}`));
    await settle();
    eq(asked.filter((a) => a === `GET /api/flows/${B}`), [], "flow B was read over an edit that did not save");
    const f = useStore.getState().flows;
    eq(f.record?.id, A, "the open record after the refused open");
    eq(f.dirty, true, "the edit after the refused open");
    eq(f.graph.nodes.find((n: any) => n.id === "t1")?.x, 120, "the edited graph after the refused open");
    eq(f.libraryError, FLOW_OPEN_OVER_UNSAVED, "the refusal's reason where openFlowById's callers read it");
    const toasts = (useStore.getState() as any).toasts.filter((t: any) => t.title === FLOW_NOT_OPENED);
    eq(toasts.map((t: any) => [t.level, t.detail]), [["error", FLOW_OPEN_OVER_UNSAVED]],
      "the toast saying the other flow did not open, and why");
  } finally {
    refusePut = null;
  }
});

// ======================================================================
// 3. the controls, on the store

// MUTANT "refuse whatever the save did" (flowsSlice.ts flowsOpen: the
// refusal's `&& now.dirty` dropped, so a save that sent nothing, a clean
// record's, still refuses). Observed ("hubSwitchSavesFlow.test: 1/3 passed";
// the first case's request list elided as "..."):
//   x a hub switch, then the Sky's flow card for another flow: the edit is PUT before the other flow is read: timed out waiting for flow B to open; requests: GET /api/sequence/resume-arm, ..., PUT /api/flows/flow-a, GET /api/flows/flow-a/progress, POST /api/flows/compile, ...
//   x control: a clean record, and an edited read-only Example, are replaced with no PUT: a clean record was not replaced
//   expected "flow-b"
//   got      "flow-a"
//
// MUTANT "an Example is saved first" (flowsSlice.ts flowsOpen:
// `!leaving.readonly` dropped: `flowsSave` declines an Example, its `dirty`
// stands, and the open is refused for good). Observed ("hubSwitchSavesFlow
// .test: 2/3 passed"):
//   x control: a clean record, and an edited read-only Example, are replaced with no PUT: an edited Example trapped the open
//   expected "flow-a"
//   got      "example-flow"
await test("control: a clean record, and an edited read-only Example, are replaced with no PUT", async () => {
  await unmountApp();
  seed();
  asked = [];
  await act(async () => { await useStore.getState().flowsOpen(A); });
  eq(useStore.getState().flows.record?.id, A, "precondition: flow A opened");
  await act(async () => { await useStore.getState().flowsOpen(B); });
  eq(asked.filter((a) => a.startsWith("PUT ")), [], "a clean record was saved before another flow opened");
  eq(useStore.getState().flows.record?.id, B, "a clean record was not replaced");
  // An Example, edited on the canvas: the server refuses its save, so the
  // open must not wait on one.
  act(() => {
    useStore.setState((s: any) => ({
      flows: { ...s.flows, record: record(EXAMPLE, { readonly: true }), graph: record(EXAMPLE).graph, dirty: true },
    }));
  });
  asked = [];
  await act(async () => { await useStore.getState().flowsOpen(A); });
  eq(asked.filter((a) => a.startsWith("PUT ")), [], "an Example was PUT before another flow opened");
  eq(useStore.getState().flows.record?.id, A, "an edited Example trapped the open");
});

// ------------------------------------------------------------------- report
await unmountApp();
const total = passed + failed;
console.log(`hubSwitchSavesFlow.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) (globalThis as any).process.exitCode = 1;
export const result = { passed, failed, total };
export default result;
