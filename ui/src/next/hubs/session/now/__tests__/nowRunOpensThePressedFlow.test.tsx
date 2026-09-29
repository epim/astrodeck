// nowRunOpensThePressedFlow.test.tsx - RUN on a Now row starts the flow that
// was pressed, or nothing (#499, the S7 integration).
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx <this file>
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT. `NowEmpty`'s RUN opened the pressed flow and then called the
// run toggle whatever the open did: `flowsOpen(id).then(() => act())`. The
// toggle posts against the record that is OPEN, and `flowsOpen` leaves the
// previous record in place when it fails. Since #450 it also REFUSES when the
// flow open in the editor holds edits its save did not keep. So with flow A
// edited and A's PUT failing (one dropped request on a phone), RUN on flow
// B's row posted `/api/flows/flow-a/run`: the wrong night, started by a press
// on a row that named another flow. S7-USLICE's verifier reproduced it on the
// real store: `PUT flow-a -> 500 | toast That flow did not open | POST
// /api/flows/flow-a/run`. The row now opens through `openFlowById` and posts
// nothing unless the flow it names is the one that landed, as FlowsScreen's
// RUN has since R5.
//
// The screen is mounted for real (NowScreen, the store, the real
// `createFlowsActions` open and save, the shared run controls); only the
// network is a fake, and every request it saw is asserted on.
//
// MUTANT "act on any open" (NowEmpty's `if (!landed) { ...; return; }`
// removed, so the toggle runs whatever the open did), observed in the
// integration's private copy (scratchpad S7-INTEG-mut), 1/3:
//
//   x RUN on flow B while flow A's edit does not save starts nothing and
//   says so: a press on flow B's row posted a run:
//     expected []
//     got      ["POST /api/flows/flow-a/run"]
//   x a 200 that carries another flow's record starts nothing: a press on
//   flow B's row posted a run:
//     expected []
//     got      ["POST /api/flows/flow-a/run"]
//
// The control (A's PUT answers, B opens and B runs, once) stays green under
// it, and is red under mutant "the row never runs" (`actRef.current()`
// removed), 2/3:
//
//   x control: flow A's edit saves, flow B opens, and flow B runs once: the
//   press did not start flow B:
//     expected ["POST /api/flows/flow-b/run"]
//     got      []

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/", pretendToBeVisual: true },
);
const win = dom.window as any;

// The phone layout, as nowDom.test.tsx: the one the Now screen is written for.
win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class {
  static OPEN = 1;
  readyState = 0;
  close() {} send() {} addEventListener() {} removeEventListener() {}
};
win.Element.prototype.setPointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.releasePointerCapture = function () { /* jsdom has none */ };
win.scrollTo = () => {};

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "localStorage", "sessionStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "requestAnimationFrame", "cancelAnimationFrame", "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------- the fake rig
const A = "flow-a";
const B = "flow-b";
function record(id: string, over: Record<string, unknown> = {}): any {
  return {
    id, name: id === A ? "M31 LRGB" : "M33 Ha", folder: "My flows",
    tagline: "", readonly: false, created_ts: 1, updated_ts: 2, last_run: null, last_result: "",
    graph: {
      nodes: [
        { id: "t1", type: "target", x: 40, y: 40, params: { name: id === A ? "M31" : "M33" } },
        { id: "c1", type: "capture", x: 300, y: 40, params: { filter: "L", exposure: 60, count: 10 } },
      ],
      edges: [{ id: "e1", from: "t1", fromPort: "target", to: "c1", toPort: "run" }],
    },
    ...over,
  };
}
function card(id: string, lastRun: number): any {
  return {
    id, name: record(id).name, folder: "My flows", tagline: "", readonly: false,
    stages: 2, wires: 1, last_run: lastRun, last_result: "ok", updated_ts: 2,
  };
}

/** Every request, in order, as "METHOD url". */
let asked: string[] = [];
/** When set, a PUT of that flow is refused with a 500. */
let refusePut: string | null = null;
/** When set, a GET of flow B answers with this record instead of B's. */
let bAnswers: any = null;

g.fetch = async (url: any, init?: any) => {
  const u = String(url);
  const method = (init?.method ?? "GET").toUpperCase();
  asked.push(`${method} ${u}`);
  const reply = (status: number, data: unknown) => ({
    ok: status >= 200 && status < 300, status, statusText: String(status),
    headers: { get: () => "application/json" },
    json: async () => data,
    text: async () => JSON.stringify(data),
  });
  if (/\/api\/flows\/[^/]+\/run$/.test(u)) return reply(200, { frames: 10, unmapped: [] });
  if (/\/api\/flows\/[^/]+\/progress$/.test(u)) return reply(200, { session: null, blocks: [] });
  if (/\/api\/flows\/[^/]+\/tonight$/.test(u)) return reply(200, { ok: false, reason: "No observatory site is set." });
  if (u === "/api/flows/compile") return reply(200, { plan: {}, structural: [], issues: [], unmapped: [] });
  const one = /^\/api\/flows\/([^/?]+)$/.exec(u);
  if (one) {
    const id = one[1];
    if (method === "PUT") {
      if (refusePut === id) return reply(500, { detail: "the flow store is not writable" });
      const body = init?.body ? JSON.parse(init.body) : {};
      return reply(200, { ...record(id), ...(body.flow ?? body), id });
    }
    if (id === B && bAnswers) return reply(200, bAnswers);
    if (id === A || id === B) return reply(200, record(id));
  }
  if (u.includes("/api/flows")) return reply(200, []);
  if (u.includes("/api/plans")) return reply(200, []);
  if (u.includes("/api/reports")) return reply(200, []);
  if (u.includes("/api/sequence/recoverable")) return reply(200, { recoverable: false });
  return reply(200, { ok: true });
};

// ------------------------------------------ imports, AFTER the globals are set
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { FLOWS_INIT, FLOW_OPEN_OVER_UNSAVED } = await import("../../../../../components/flows/flowsSlice");
const { FLOW_OPEN_FAILED, FLOW_OPEN_MISMATCH } = await import("../../flows/openFlow");
const { NowScreen } = await import("../NowScreen");

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

const OPERATOR = {
  role: "operator", email: "op@rig",
  caps: ["view.status", "view.preview", "view.site_derived", "control.capture", "control.mount", "control.guide"],
};

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
const byId = (id: string) => container.querySelector(`[data-testid="${id}"]`) as any;
async function press(id: string): Promise<void> {
  const el = byId(id);
  assert(el != null, `no ${id} to press: the fixture is wrong, not the component`);
  await act(async () => {
    el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
  });
  await settle();
}
const runs = () => asked.filter((a) => a.startsWith("POST ") && a.endsWith("/run"));

/** The Now screen idle, both flows on its list, flow A open in the store:
 *  EDITED (a moved node, `dirty`) when `edited`, as a hub switch leaves it. */
async function mount(edited: boolean): Promise<void> {
  await act(async () => { root.render(null); });
  asked = [];
  const a = record(A);
  const graph = edited
    ? { ...a.graph, nodes: a.graph.nodes.map((n: any) => (n.id === "t1" ? { ...n, x: 120 } : n)) }
    : a.graph;
  useStore.setState({
    principal: OPERATOR,
    authGate: "open",
    equipConnected: true,
    wsPhase: "up",
    wsConnected: true,
    telemetryStale: false,
    toasts: [],
    confirm: null,
    resumeArm: null,
    site: null,
    masters: [],
    safety: { connected: true, streak: 0, reading: { is_safe: true, reason: "", source: "sim", stale: false, ts: Date.now() / 1000 } },
    status: {
      connected: { camera: { connected: true, name: "sim" }, telescope: { connected: true, name: "sim mount" } },
      looping: false, busy_lanes: [],
    },
    sequence: { state: "idle" },
    flows: {
      ...FLOWS_INIT,
      ui: { ...FLOWS_INIT.ui },
      cards: [card(A, 100), card(B, 50)],
      libraryLoaded: true, libraryError: null,
      record: a, graph, dirty: edited,
    },
  } as never);
  await act(async () => { root.render(createElement(NowScreen)); });
  await settle();
  assert(byId("now-empty") != null, "no now-empty: the fixture is wrong, not the component");
  assert(byId(`run-flow-${B}`) != null, "flow B's row is missing: every assertion below would be vacuous");
}
const toastsTitled = (title: string) =>
  ((useStore.getState() as any).toasts as any[]).filter((t) => t.title === title);

// ------------------------------------------------------------------ the cases

await test("RUN on flow B while flow A's edit does not save starts nothing and says so", async () => {
  refusePut = A;
  bAnswers = null;
  try {
    await mount(true);
    await press(`run-flow-${B}`);
    assert(asked.includes(`PUT /api/flows/${A}`),
      `premise: flow A's edit was never saved first; requests: ${asked.join(", ")}`);
    const f = (useStore.getState() as any).flows;
    eq([f.record?.id, f.dirty], [A, true], "premise: the refusal left flow A open with its edit");
    eq(runs(), [], "a press on flow B's row posted a run:");
    // The store says the refusal itself (flowsSlice FLOW_NOT_OPENED, the same
    // title), and the row's toast of the same title coalesces onto it (the
    // store's x2 rule for a toast still on screen), so the operator reads
    // the refusal's reason once.
    const said = toastsTitled(FLOW_OPEN_FAILED);
    assert(said.some((t) => String(t.detail).includes(FLOW_OPEN_OVER_UNSAVED)),
      `nothing said why flow B did not open: ${JSON.stringify(said)}`);
  } finally {
    refusePut = null;
  }
});

await test("a 200 that carries another flow's record starts nothing", async () => {
  // Flow A clean, so no save is asked; B's read answers A's record. The
  // record open is then A, and a toggle would post A's run.
  bAnswers = record(A);
  try {
    await mount(false);
    await press(`run-flow-${B}`);
    assert(asked.includes(`GET /api/flows/${B}`),
      `premise: flow B was never read; requests: ${asked.join(", ")}`);
    eq(runs(), [], "a press on flow B's row posted a run:");
    // The store writes nothing for a read that answered, so this toast is
    // the row's own.
    const said = toastsTitled(FLOW_OPEN_FAILED);
    assert(said.some((t) => String(t.detail).includes(FLOW_OPEN_MISMATCH)
      && String(t.detail).includes("Nothing was started.")),
      `the row did not say the server answered with another flow: ${JSON.stringify(said)}`);
  } finally {
    bAnswers = null;
  }
});

await test("control: flow A's edit saves, flow B opens, and flow B runs once", async () => {
  await mount(true);
  await press(`run-flow-${B}`);
  eq(runs(), [`POST /api/flows/${B}/run`], "the press did not start flow B:");
  const put = asked.indexOf(`PUT /api/flows/${A}`);
  const read = asked.indexOf(`GET /api/flows/${B}`);
  const run = asked.indexOf(`POST /api/flows/${B}/run`);
  assert(put >= 0 && put < read && read < run,
    `not saved, then read, then run: ${asked.join(", ")}`);
  eq(toastsTitled(FLOW_OPEN_FAILED), [], "a toast said the flow did not open:");
});

await act(async () => { root.unmount(); });

const total = passed + failed;
console.log(`nowRunOpensThePressedFlow.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
