// wireDropLoop.test.ts - the CLASSIC canvas refuses a flow loop at the drop,
// in the server's words (#149; spec 2026-09-23 section 1.4 item 5, S0 item 2).
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/__tests__/wireDropLoop.test.ts   (from ui/)
//
// TWO HALVES, and the second is the one a pure test cannot see.
//
//   1. THE RESOLVER. `resolveWireDrop`, handed the loop check, refuses the loop
//      drop with the exact sentence and still accepts a forward drop. Called the
//      old way, with no check, it keeps the old grammar - the argument is
//      optional so existing call shapes compile.
//   2. THE SURFACE PASSES THE CHECK. Because the argument is optional, a
//      FlowCanvas that forgot to pass it would compile and every resolver case
//      above would still pass. The store's own guard would then keep the graph
//      intact, and the operator would get NO toast: the sentence would land in
//      the flow log and the drop would look like a miss. So the mounted case
//      asserts the TOAST, which only the resolver path produces. And the check
//      it passes must read the graph AT THE DROP: the last case edits the lane
//      after mount, which a check frozen at the mounted graph gets wrong.
//
// Every case names the mutant it kills and quotes the failure that mutant
// produced when it was run from a byte-for-byte backup of FlowCanvas.tsx.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
// FlowCanvas imports the store, and store.ts -> lib/base.ts reads
// `window.location.pathname` at module scope, so every import below is dynamic
// and comes after the window exists.
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

// jsdom implements no hit testing, and the drop is resolved against the element
// under the pointerup. The test says which element that is; the canvas still
// has to ask for it, read its `data-port` and apply the grammar.
let hitTarget: any = null;
win.document.elementFromPoint = () => hitTarget;

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "KeyboardEvent", "localStorage", "sessionStorage",
  "getComputedStyle", "matchMedia", "WebSocket", "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.requestAnimationFrame = (cb: (t: number) => void) => setTimeout(() => cb(0), 0);
g.cancelAnimationFrame = (id: number) => clearTimeout(id);
g.IS_REACT_ACT_ENVIRONMENT = true;
// Nothing on this surface should reach the network; answer anything that does
// with an empty success rather than letting it throw into a render.
g.fetch = async () => ({
  ok: true, status: 200, statusText: "OK",
  headers: { get: () => "application/json" },
  json: async () => ({}), text: async () => "{}",
});

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../store");
const FlowCanvasMod = await import("../FlowCanvas");
const FlowCanvas = FlowCanvasMod.default;
const { resolveWireDrop } = FlowCanvasMod;
const { flowLoopRefusal, portKindOf } = await import("../flowLoop");
type FlowGraphRec = import("../flowsTypes").FlowGraphRec;
type PortDir = import("../geometry").PortDir;

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) {
    throw new Error(`${msg}\n  expected ${JSON.stringify(want)}\n  got      ${JSON.stringify(got)}`);
  }
}

// ------------------------------------------------------------------ fixture
// dusk -> target -> cycle, the spec's own loop: FILTER CYCLE "complete" back
// into TARGET "arm" is the wire an operator draws when they want "and round
// again". SESSION REPORT gives the forward drop somewhere to land.
const GRAPH: FlowGraphRec = {
  nodes: [
    { id: "n1", type: "dusk", x: 0, y: 0, params: {} },
    { id: "n2", type: "target", x: 260, y: 0, params: {} },
    { id: "n3", type: "cycle", x: 520, y: 0, params: {} },
    { id: "n4", type: "report", x: 780, y: 0, params: {} },
  ],
  edges: [
    { id: "e1", from: "n1", fromPort: "window", to: "n2", toPort: "arm" },
    { id: "e2", from: "n2", fromPort: "target", to: "n3", toPort: "run" },
  ],
};
const SENTENCE = "this flow loops back on itself at FILTER CYCLE -> TARGET; a flow lane runs once";

const kindOf = (nodeId: string, portId: string, dir: PortDir) =>
  portKindOf(GRAPH.nodes, nodeId, portId, dir);
const loopOf = (w: { from: string; fromPort: string; to: string; toPort: string }) =>
  flowLoopRefusal(GRAPH.nodes, GRAPH.edges, w);

// ======================================================= 1. the resolver

test("the loop drop is refused with the server's sentence", () => {
  // MUTANT "delete the classic resolver's loop check". Observed (2 failed;
  // this one and the mounted drop below):
  //   x the loop drop is refused with the server's sentence: a flow wire back into its own lane must not connect
  //     expected false
  //     got      true
  const res = resolveWireDrop({ from: "n3", fromPort: "complete" }, "n2|arm|in", kindOf, loopOf);
  eq(res.ok, false, "a flow wire back into its own lane must not connect");
  eq((res as { refusal: string | null }).refusal, SENTENCE,
    "the refusal must be the server's sentence, naming source then destination");
});

test("a forward drop is accepted", () => {
  // CONTROL. MUTANT "refuse every drop the check is asked about" (the check's
  // answer ignored, the drop refused whenever a check was passed). Observed
  // (2 failed; this one and the mounted forward drop below):
  //   x a forward drop is accepted: FILTER CYCLE -> SESSION REPORT runs forward and must connect
  //     expected "{\"ok\":true,\"nodeId\":\"n4\",\"portId\":\"session\"}"
  //     got      "{\"ok\":false,\"refusal\":null}"
  const res = resolveWireDrop({ from: "n3", fromPort: "complete" }, "n4|session|in", kindOf, loopOf);
  eq(JSON.stringify(res), JSON.stringify({ ok: true, nodeId: "n4", portId: "session" }),
    "FILTER CYCLE -> SESSION REPORT runs forward and must connect");
});

test("called the old way, with no check, the resolver keeps the old grammar", () => {
  // The argument is optional so every existing call shape still compiles; the
  // store's guard is what stands behind a caller that passes none. This
  // three-argument call is the compile-time half of the pin.
  //
  // MUTANT "loopOf made required" (the `?` dropped). Observed from
  // `tsc -p tsconfig.json --noEmit`:
  //   src/components/flows/__tests__/wireDropLoop.test.ts(147,15): error TS2554: Expected 4 arguments, but got 3.
  const res = resolveWireDrop({ from: "n3", fromPort: "complete" }, "n2|arm|in", kindOf);
  eq(res.ok, true, "with no check handed in, the loop rule is not the resolver's to apply");
});

// ================================================ 2. the surface passes it

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
const settle = async (): Promise<void> => {
  for (let i = 0; i < 4; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};
const ptr = (el: any, type: string, clientX: number, clientY: number): void => {
  act(() => {
    el.dispatchEvent(new win.MouseEvent(type, {
      bubbles: true, cancelable: true, clientX, clientY, button: 0, buttons: 1,
    }));
  });
};

act(() => {
  const s = useStore.getState();
  useStore.setState({
    principal: { role: "admin", email: null, caps: ["view.status", "control.capture"] } as never,
    authGate: "open",
    toasts: [],
    flows: {
      ...s.flows,
      graph: JSON.parse(JSON.stringify(GRAPH)),
      dirty: false, sel: null, editNode: null, wire: null, tapWire: null,
      statuses: {}, logs: [], pan: { x: 0, y: 0 }, zoom: 1,
    },
  } as never);
});
await act(async () => { root.render(createElement(FlowCanvas as any, { tier: "desktop" })); });
await settle();

test("a loop dropped on the mounted canvas is TOASTED with the sentence and wires nothing", () => {
  // MUTANT "FlowCanvas does not pass the check to the resolver". Observed -
  // every resolver case above still passed, and only this one saw it:
  //   x a loop dropped on the mounted canvas is TOASTED with the sentence and wires nothing: the drop must be refused out loud, with the server's sentence; toasts were []
  //     expected true
  //     got      false
  const out = container.querySelector('[data-port="n3|complete|out"]');
  const inp = container.querySelector('[data-port="n2|arm|in"]');
  if (!out || !inp) throw new Error("the ports the drop needs are not on the cards - the fixture is wrong, not the rule");
  const graph = useStore.getState().flows.graph;

  ptr(out, "pointerdown", 700, 40);
  if (!useStore.getState().flows.wire) throw new Error("the press did not begin a wire");
  hitTarget = inp;
  ptr(win, "pointerup", 270, 40);
  hitTarget = null;

  const titles = useStore.getState().toasts.map((t: any) => String(t.title));
  eq(titles.includes(SENTENCE), true,
    `the drop must be refused out loud, with the server's sentence; toasts were ${JSON.stringify(titles)}`);
  eq(useStore.getState().flows.graph === graph, true,
    "a refused drop must leave the graph exactly as it was");
  eq(useStore.getState().flows.wire, null, "the pending wire must not outlive the drop");
});

test("a forward drop on the mounted canvas connects, and says nothing", () => {
  // CONTROL for the mounted path: the check is asked, answers null, and the
  // drop goes through to flowsConnect as before.
  const out = container.querySelector('[data-port="n3|complete|out"]');
  const inp = container.querySelector('[data-port="n4|session|in"]');
  if (!out || !inp) throw new Error("the ports the drop needs are not on the cards");
  act(() => { useStore.setState({ toasts: [] } as never); });

  ptr(out, "pointerdown", 700, 40);
  hitTarget = inp;
  ptr(win, "pointerup", 790, 40);
  hitTarget = null;

  const edges = useStore.getState().flows.graph.edges;
  eq(edges.some((e) => e.from === "n3" && e.fromPort === "complete" && e.to === "n4" && e.toPort === "session"),
    true, "the forward drop made no edge");
  eq(useStore.getState().toasts.length, 0, "a successful drop is silent");
});

test("the check reads the graph as it stands at the drop, not as it stood at mount", () => {
  // The canvas builds `loopOf` once and reads the store inside it, so an edit
  // does not re-attach the window listeners. The obvious way to get that wrong
  // - close over this render's `nodes` and `edges` and leave the dependency
  // list empty - passes every case above, because each of them drops on the
  // graph the canvas mounted with. So the lane is edited first, through the
  // store's own delete: TARGET -> FILTER CYCLE goes, TARGET no longer reaches
  // FILTER CYCLE, and FILTER CYCLE "complete" into TARGET "arm" closes no
  // circle. A check still looking at the mounted graph refuses it - a wrong
  // sentence, and a wire the operator cannot draw.
  //
  // MUTANT "loopOf reads the graph the canvas mounted with" (FlowCanvas.tsx:
  // `return flowLoopRefusal(nodes, edges, w)` over the render's graph, deps
  // left `[]`). Every case above still passed; observed:
  //   x the check reads the graph as it stands at the drop, not as it stood at mount: with the lane wire deleted the drop closes no circle, so nothing may be toasted
  //     expected "[]"
  //     got      "[\"this flow loops back on itself at FILTER CYCLE -> TARGET; a flow lane runs once\"]"
  act(() => {
    useStore.setState({ toasts: [] } as never);
    useStore.getState().flowsSelect({ kind: "edge", id: "e2" });
    useStore.getState().flowsDeleteSel();
  });
  if (useStore.getState().flows.graph.edges.some((e) => e.id === "e2")) {
    throw new Error("the TARGET -> FILTER CYCLE wire was not deleted - the fixture is wrong, not the rule");
  }
  const out = container.querySelector('[data-port="n3|complete|out"]');
  const inp = container.querySelector('[data-port="n2|arm|in"]');
  if (!out || !inp) throw new Error("the ports the drop needs are not on the cards");

  ptr(out, "pointerdown", 700, 40);
  hitTarget = inp;
  ptr(win, "pointerup", 270, 40);
  hitTarget = null;

  const titles = useStore.getState().toasts.map((t: any) => String(t.title));
  eq(JSON.stringify(titles), "[]",
    "with the lane wire deleted the drop closes no circle, so nothing may be toasted");
  eq(useStore.getState().flows.graph.edges.some((e) =>
    e.from === "n3" && e.fromPort === "complete" && e.to === "n2" && e.toPort === "arm"),
  true, "the drop made no edge");
});

await act(async () => { root.unmount(); });

console.log(`\nwireDropLoop (classic): ${passed} passed, ${failed} failed`);
if (failures.length) { failures.forEach((f) => console.log(f)); process.exit(1); }
export default { passed, failed, total: passed + failed };
