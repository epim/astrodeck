// wireDropLoop.test.ts - the #/next canvas refuses a flow loop at the drop, in
// the server's words (#149; spec 2026-09-23 section 1.4 item 5, S0 item 2).
//   Run:  node --import ./test-css-stub.mjs --import tsx src/next/hubs/session/flows/canvas/__tests__/wireDropLoop.test.ts   (from ui/)
//
// The rebuilt canvas keeps its OWN copy of the drop grammar (`canvasModel.ts`
// says why: the legacy one lives in a presentation module), so the rule has to
// be proved on this copy separately - a fix to the classic resolver changes
// nothing here. Same two halves as the classic test:
//
//   1. THE RESOLVER. `canvasModel.resolveWireDrop`, handed the loop check,
//      refuses the loop drop with the exact sentence and accepts a forward one.
//   2. THE SURFACE PASSES THE CHECK. The argument is optional, so a
//      FlowCanvasSurface that forgot it would compile and pass every resolver
//      case; the store's guard would keep the graph intact and the operator
//      would get no toast. The mounted case asserts the TOAST. And the check it
//      passes must read the graph AT THE DROP: the last case edits the lane
//      after mount, which a check frozen at the mounted graph gets wrong.
//
// Every case names the mutant it kills and quotes the failure that mutant
// produced when it was run from a byte-for-byte backup of the file it names.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/", pretendToBeVisual: true },
);
const win = dom.window as any;

// TABLET: the narrowest width the canvas surface exists at.
const VIEWPORT_W = 820;
win.matchMedia = (q: string) => {
  const m = /min-width:\s*(\d+)px/.exec(q);
  return {
    matches: m ? VIEWPORT_W >= Number(m[1]) : false,
    addEventListener() {}, removeEventListener() {},
    addListener() {}, removeListener() {},
  };
};
win.WebSocket = class { close() {} addEventListener() {} send() {} };
win.Element.prototype.setPointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.releasePointerCapture = function () { /* jsdom has none */ };

// jsdom implements no hit testing, and the drop is resolved against the element
// under the pointerup. The test says which element that is; the surface still
// has to ask for it, read its `data-port` and apply the grammar.
let hitTarget: any = null;
win.document.elementFromPoint = () => hitTarget;

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "localStorage", "sessionStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "requestAnimationFrame", "cancelAnimationFrame", "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
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
const { useStore } = await import("../../../../../../store");
const { FlowCanvasSurface } = await import("../FlowCanvasSurface");
const { portKind, resolveWireDrop } = await import("../canvasModel");
const { flowLoopRefusal } = await import("../../../../../../components/flows/flowLoop");
type FlowGraphRec = import("../../../../../../components/flows/flowsTypes").FlowGraphRec;
type PortDir = import("../../../../../../components/flows/geometry").PortDir;

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
// dusk -> target -> cycle, the spec's own loop, plus SESSION REPORT for the
// forward drop to land on.
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
  portKind(GRAPH.nodes, nodeId, portId, dir);
const loopOf = (w: { from: string; fromPort: string; to: string; toPort: string }) =>
  flowLoopRefusal(GRAPH.nodes, GRAPH.edges, w);

// ======================================================= 1. the resolver

test("the loop drop is refused with the server's sentence", () => {
  // MUTANT "delete the #/next resolver's loop check" (canvasModel.ts).
  // Observed (2 failed; this one and the mounted drop below):
  //   x the loop drop is refused with the server's sentence: a flow wire back into its own lane must not connect
  //     expected false
  //     got      true
  const res = resolveWireDrop({ from: "n3", fromPort: "complete" }, "n2|arm|in", kindOf, loopOf);
  eq(res.ok, false, "a flow wire back into its own lane must not connect");
  eq((res as { refusal: string | null }).refusal, SENTENCE,
    "the refusal must be the server's sentence, naming source then destination");
});

test("a lane mismatch drop is refused with flowLoop.ts's own sentence", () => {
  // SHARED WITH THE STORE AND THE CLASSIC CANVAS (#197, backlog WP-36): this
  // resolver's lane check used to carry its own copy of this sentence,
  // spelt out inline; it now calls `flowLoop.ts`'s `laneMismatchRefusal`
  // directly, the SAME function `flowsConnect` and the classic
  // `FlowCanvas.tsx` resolver call (w4SelfWireAndLaneChecks.test.ts), so a
  // wording change made once in `flowLoop.ts` reaches all three instead of
  // needing a second, easily-missed edit here.
  //
  // DUSK WINDOW's "night ends" output is an EVENT port; TARGET's "arm"
  // input is a FLOW one (the fixture's own e1 wires it from "window
  // opens", a flow port) - so this drop is the lane mismatch, inside the
  // same fixture graph the loop cases above already use.
  //
  // MUTANT "flowLoop.ts's sentence changes" (`laneMismatchRefusal`'s
  // template edited to read "...into..." instead of "...feed..."), run
  // in a private scratch copy of ui/ (byte-for-byte restored, sha256
  // checked): RED here too, proving the resolver's answer comes from that
  // function and not a private copy of its own. Observed:
  //   x a lane mismatch drop is refused with flowLoop.ts's own sentence: the refusal must be the sentence flowLoop.ts's laneMismatchRefusal owns
  //     expected "Event output can't feed a flow input"
  //     got      "Event output can't feed into a flow input"
  const res = resolveWireDrop({ from: "n1", fromPort: "nightend" }, "n2|arm|in", kindOf, loopOf);
  eq(res.ok, false, "an event output cannot feed a flow input");
  eq((res as { refusal: string | null }).refusal,
    "Event output can't feed a flow input",
    "the refusal must be the sentence flowLoop.ts's laneMismatchRefusal owns");
});

test("a forward drop is accepted", () => {
  // CONTROL. MUTANT "refuse every drop the check is asked about" (the check's
  // answer ignored, the drop refused whenever a check was passed; in
  // canvasModel.ts). Observed (2 failed; this one and the mounted forward drop):
  //   x a forward drop is accepted: FILTER CYCLE -> SESSION REPORT runs forward and must connect
  //     expected "{\"ok\":true,\"nodeId\":\"n4\",\"portId\":\"session\"}"
  //     got      "{\"ok\":false,\"refusal\":null}"
  const res = resolveWireDrop({ from: "n3", fromPort: "complete" }, "n4|session|in", kindOf, loopOf);
  eq(JSON.stringify(res), JSON.stringify({ ok: true, nodeId: "n4", portId: "session" }),
    "FILTER CYCLE -> SESSION REPORT runs forward and must connect");
});

test("called the old way, with no check, the resolver keeps the old grammar", () => {
  // The argument is optional so every existing call shape (canvasDom's among
  // them) still compiles. This three-argument call is the compile-time half.
  //
  // MUTANT "loopOf made required" (canvasModel.ts). Observed from
  // `tsc -p tsconfig.json --noEmit` - canvasDom's existing call breaks too:
  //   src/next/hubs/session/flows/canvas/__tests__/canvasDom.test.tsx(331,15): error TS2554: Expected 4 arguments, but got 3.
  //   src/next/hubs/session/flows/canvas/__tests__/wireDropLoop.test.ts(149,15): error TS2554: Expected 4 arguments, but got 3.
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
await act(async () => { root.render(createElement(FlowCanvasSurface as any, { tier: "tablet" })); });
await settle();

test("a loop dropped on the mounted surface is TOASTED with the sentence and wires nothing", () => {
  // MUTANT "FlowCanvasSurface does not pass the check to the resolver".
  // Observed - every resolver case above still passed, and only this one saw it:
  //   x a loop dropped on the mounted surface is TOASTED with the sentence and wires nothing: the drop must be refused out loud, with the server's sentence; toasts were []
  //     expected true
  //     got      false
  if (container.querySelector('[data-testid="flows-canvas"]') == null) {
    throw new Error("no flows-canvas marker - the surface did not render, so nothing below is evidence");
  }
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

test("a forward drop on the mounted surface connects, and says nothing", () => {
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
  // The surface builds `loopOf` once and reads the store inside it, so an edit
  // does not re-attach the window listeners. The obvious way to get that wrong
  // - close over this render's `nodes` and `edges` and leave the dependency
  // list empty - passes every case above, because each of them drops on the
  // graph the surface mounted with. So the lane is edited first, through the
  // store's own delete: TARGET -> FILTER CYCLE goes, TARGET no longer reaches
  // FILTER CYCLE, and FILTER CYCLE "complete" into TARGET "arm" closes no
  // circle. A check still looking at the mounted graph refuses it - a wrong
  // sentence, and a wire the operator cannot draw.
  //
  // MUTANT "loopOf reads the graph the surface mounted with"
  // (FlowCanvasSurface.tsx: `return flowLoopRefusal(nodes, edges, w)` over the
  // render's graph, deps left `[]`). Every case above still passed; observed:
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

console.log(`\nwireDropLoop (next): ${passed} passed, ${failed} failed`);
if (failures.length) { failures.forEach((f) => console.log(f)); process.exit(1); }
export default { passed, failed, total: passed + failed };
