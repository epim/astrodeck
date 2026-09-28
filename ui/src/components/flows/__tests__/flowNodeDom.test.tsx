// flowNodeDom.test.tsx — the node card and its port rows, MOUNTED.
//
//   Run directly:  npx tsx src/components/flows/__tests__/flowNodeDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// Two things are pinned here and they are different kinds of promise.
//
// The first is the harness contract. `scripts/flows_visual_check.py` reaches the
// canvas ONLY through `data-node-type` (click / hover), `data-flows-edit` (the
// sheet's only opener — line 438 fails with "the edit sheet has no opener the
// harness can find") and `data-port` / `data-port-dir` (drag-drop and arm-wire).
// A card that renders beautifully with a marker spelled wrong photographs as a
// pass on a page the harness never actually drove.
//
// The second is the RE-RENDER DISCIPLINE, which is invisible in behaviour. A
// card that subscribed to the whole `flows` object would render identically and
// re-render the entire graph on every status tick, and no functional assertion
// anywhere would notice. The only way to see it is to count renders, so the
// test counts them — through `def.sum()`, which the footer calls once per card
// render and which nothing else on this surface calls.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/", pretendToBeVisual: true },
);
const win = dom.window as any;

// ui.tsx -> Overlay -> useMediaQuery calls matchMedia during render; without it
// the first `Led` import takes the whole file down before an assertion runs.
win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "localStorage", "getComputedStyle", "matchMedia",
  "WebSocket",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.requestAnimationFrame = (cb: (t: number) => void) => setTimeout(() => cb(0), 0);
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------------- imports
import React from "react";
import { createRoot } from "react-dom/client";
import { act } from "react";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
const assert = {
  ok(cond: unknown, msg?: string) { if (!cond) throw new Error(msg ?? "not ok"); },
  equal(a: unknown, b: unknown, msg?: string) {
    if (a !== b) throw new Error(msg ?? `${String(a)} !== ${String(b)}`);
  },
  match(text: string, re: RegExp, msg?: string) {
    if (!re.test(text)) throw new Error(msg ?? `no match for ${re} in: ${text.slice(0, 200)}`);
  },
};

const { useStore } = await import("../../../store");
const { NODE_DEFS } = await import("../nodeDefs");
const { FLOWS_INIT } = await import("../flowsSlice");
const FlowNodeCard = (await import("../FlowNodeCard")).default;
type FlowNodeRec = import("../flowsTypes").FlowNodeRec;

// ------------------------------------------------------------------- fixture
// TARGET has two inputs + one output; CAPTURE has one input and THREE outputs,
// of both kinds — a node that mixes lanes on one side, so it is what proves
// the lane colour is read per port and not per node.
//
// RE-PINNED IN THE INTEGRATION OF MOSAIC S3 (#189, spec 1.2): TARGET gained
// the optional event input `next` (after `arm`), and CAPTURE LOOP the event
// output `pass` (after `frame`). Until then these tests reached a port by its
// index, so `target`'s output moved from ports[1] to ports[2] under them. The
// wiring tests now find a port by its `data-port` (`portOf`), and the two
// ORDER tests pin the new lists in full. Each RED under its port dropped from
// nodeDefs.ts in a private scratch copy, observed:
//   "capture pass port missing":
//     x inputs render before outputs, never interleaved: n2|run|in,
//       n2|complete|out,n2|frame|out
//   "target next port missing":
//     x every port announces its node, its id and its direction: target has 2
//       in + 1 out, got 2
//     x an armed output takes the ring: no [data-port="n1|next|in"] on the
//       target card
const N1: FlowNodeRec = {
  id: "n1", type: "target", x: 10, y: 20, params: { ...NODE_DEFS.target.params },
};
const N2: FlowNodeRec = {
  id: "n2", type: "capture", x: 300, y: 20, params: { ...NODE_DEFS.capture.params },
};
const EDGE = { id: "e1", from: "n1", fromPort: "target", to: "n2", toPort: "run" };

/** Renders per card, counted where the card itself renders: the footer's
 *  summary. Nothing else in the app calls `def.sum`. */
const renders: Record<string, number> = { target: 0, capture: 0 };
for (const t of ["target", "capture"] as const) {
  const orig = NODE_DEFS[t].sum;
  NODE_DEFS[t].sum = (p) => { renders[t]++; return orig(p); };
}

const root = createRoot(win.document.getElementById("root"));
const container = win.document.getElementById("root");

function setFlows(patch: Record<string, unknown>): void {
  act(() => {
    useStore.setState({
      flows: { ...useStore.getState().flows, ...patch },
    } as any);
  });
}

/** A parent with NO store subscription of its own — which is the point. The
 *  canvas subscribes to the node ARRAY (§B.3) and that array does not change
 *  when a status ticks, so anything that re-renders after a status write can
 *  only have re-rendered because a card asked to. */
function Deck(props: Record<string, unknown>) {
  return React.createElement(
    React.Fragment, null,
    [N1, N2].map((n) => React.createElement(FlowNodeCard, { key: n.id, node: n, ...props })),
  );
}

function render(props: Record<string, unknown> = {}): void {
  // Unmount first. `memo` is doing its job, so re-rendering the same element
  // with the same props would legitimately render NOTHING, and the render
  // counters below would read every test after the first as a bail-out.
  act(() => root.render(null));
  act(() => {
    useStore.setState({
      flows: { ...FLOWS_INIT, graph: { nodes: [N1, N2], edges: [EDGE] } },
    } as any);
  });
  renders.target = 0;
  renders.capture = 0;
  act(() => root.render(React.createElement(Deck, props)));
}

const card = (type: string): any =>
  container.querySelector(`[data-node-type="${type}"]`);

/** A port by its `data-port` string, e.g. "n1|target|out": found by what it
 *  is, so a port added to the card does not move the one a test means. */
const portOf = (type: string, key: string): any => {
  const p = card(type).querySelector(`[data-port="${key}"]`);
  assert.ok(p, `no [data-port="${key}"] on the ${type} card`);
  return p;
};

/** jsdom has no PointerEvent; React reads the fields off the native event, so a
 *  plain bubbling Event carrying them is what a finger looks like. */
function pointerDown(node: any): void {
  act(() => {
    const ev = new win.Event("pointerdown", { bubbles: true, cancelable: true }) as any;
    ev.pointerId = 1;
    ev.pointerType = "mouse";
    ev.isPrimary = true;
    ev.clientX = 0;
    ev.clientY = 0;
    node.dispatchEvent(ev);
  });
}
function click(node: any): void {
  act(() => {
    node.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
  });
}

// ───────────────────────────────────────────────────────── harness markers

test("the card carries the two markers the harness steers the canvas with", () => {
  render();
  const c = card("target");
  assert.ok(c, "no [data-node-type='target'] — select-node-type finds no node "
    + "and the capture is of a canvas the harness never touched");
  assert.equal(c.getAttribute("data-node-id"), "n1",
    "markersDom needs data-node-id to tell two cards of the same type apart");
});

test("the ✎ carries data-flows-edit — it is the edit sheet's only opener", () => {
  render();
  const pencil = card("target").querySelector("[data-flows-edit]");
  assert.ok(pencil, "flows_visual_check.py:438 clicks [data-flows-edit] inside the "
    + "node; without it state 13 cannot be captured at all");
  assert.equal(pencil.getAttribute("aria-label"), "Edit parameters",
    "an icon-only button with no accessible name is unreachable by name");
});

test("every port announces its node, its id and its direction", () => {
  render();
  const ports = [...card("target").querySelectorAll("[data-port]")] as any[];
  assert.equal(ports.length, 3, `target has 2 in + 1 out, got ${ports.length}`);
  assert.equal(ports[0].getAttribute("data-port"), "n1|arm|in",
    "the drop resolver splits this string on '|' — a different shape silently "
    + "wires nothing");
  assert.equal(ports[0].getAttribute("data-port-dir"), "in");
  assert.equal(ports[1].getAttribute("data-port"), "n1|next|in",
    "S3's optional `next` input renders after `arm`");
  assert.equal(ports[1].getAttribute("data-port-dir"), "in");
  assert.equal(ports[2].getAttribute("data-port"), "n1|target|out");
  assert.equal(ports[2].getAttribute("data-port-dir"), "out",
    "arm-wire selects [data-port][data-port-dir='out']");
});

test("inputs render before outputs, never interleaved", () => {
  render();
  // CAPTURE: in `run`, then outs `complete` (flow), `frame` and `pass` (both
  // event; `pass` since S3). The wire anchors in geometry.portPos() place an
  // output at row `ins.length + idx`, so an interleaved list would detach
  // every wire on the card from its dot.
  const dirs = [...card("capture").querySelectorAll("[data-port]")]
    .map((p: any) => p.getAttribute("data-port"));
  assert.equal(dirs.join(","),
    "n2|run|in,n2|complete|out,n2|frame|out,n2|pass|out", dirs.join(","));
});

test("an AUTOFOCUS card renders `focused` and then its `pass` event port (S4, #331)", () => {
  // DELIBERATE PIN, NEW IN S4: AUTOFOCUS gained the event output `pass`, last,
  // so the loop wire can leave it when it ends a panel lane. The dot a drop
  // resolver or a tap lands on is `n3|pass|out`, on the row below `focused`
  // (geometry.portPos: row ins.length + 1), in the event lane's colour.
  //
  // MUTANT "AUTOFOCUS has no pass" (in a private scratch copy, the port
  // dropped from nodeDefs.ts). Observed:
  //   x an AUTOFOCUS card renders `focused` and then its `pass` event port
  //     (S4, #331): n3|run|in,n3|focused|out !==
  //     n3|run|in,n3|focused|out,n3|pass|out
  //   and under MUTANT "pass first" (AUTOFOCUS's outs reordered to pass,
  //   focused):
  //   x an AUTOFOCUS card renders `focused` and then its `pass` event port
  //     (S4, #331): n3|run|in,n3|pass|out,n3|focused|out !==
  //     n3|run|in,n3|focused|out,n3|pass|out
  //   (flowNodeDom.test: 20/21 passed, each)
  const n3: FlowNodeRec = {
    id: "n3", type: "autofocus", x: 600, y: 20, params: { ...NODE_DEFS.autofocus.params },
  };
  act(() => root.render(null));
  act(() => {
    useStore.setState({
      flows: { ...FLOWS_INIT, graph: { nodes: [n3], edges: [] } },
    } as any);
  });
  act(() => root.render(React.createElement(FlowNodeCard, { node: n3 })));
  const ports = [...card("autofocus").querySelectorAll("[data-port]")] as any[];
  assert.equal(ports.map((p) => p.getAttribute("data-port")).join(","),
    "n3|run|in,n3|focused|out,n3|pass|out");
  const dot = (p: any) => p.firstElementChild.getAttribute("style");
  assert.match(dot(ports[1]), /border:\s*1\.5px solid var\(--accent\)/, "`focused` is a flow port");
  assert.match(dot(ports[2]), /border:\s*1\.5px solid var\(--warn\)/, "`pass` is an event port");
});

// ──────────────────────────────────────────────────── the re-render discipline

test("a status tick re-renders ONE card, not the graph", () => {
  render();
  assert.equal(renders.target, 1, "setup: one render each");
  assert.equal(renders.capture, 1, "setup: one render each");

  setFlows({ statuses: { n1: "busy" } });

  assert.equal(renders.target, 2, "the node whose status changed must re-render");
  assert.equal(renders.capture, 1,
    "a flow.node frame for n1 re-rendered n2 as well — the card is subscribing "
    + "to something wider than its own status, and on a 19-node graph every tick "
    + "now repaints the whole canvas");
});

test("the status reaches the LED as a silhouette and a word, not a colour", () => {
  render();
  setFlows({ statuses: { n1: "bad" } });
  const led = card("target").querySelector(".led");
  assert.match(led.className, /led-bad/,
    "night mode collapses --good/--warn/--bad toward one red family; the square "
    + "silhouette is what still distinguishes them");
  assert.equal(led.getAttribute("aria-label"), "bad",
    "the state must be readable without seeing the dot at all");
  assert.match(card("capture").querySelector(".led").className, /led-off/,
    "an untouched node must stay idle");
});

// ─────────────────────────────────────────────────────────── border + status

test("selection outranks status on the border", () => {
  render();
  setFlows({ statuses: { n1: "busy" }, sel: { kind: "node", id: "n1" } });
  const style = card("target").getAttribute("style");
  assert.match(style, /border-color:\s*var\(--accent\)/,
    "a selected busy node must read as selected");
  assert.match(style, /box-shadow:[^;]*35%/,
    "the selected halo is the 35% mix, not the busy 25% one");
});

test("idle draws from the tokens, never a literal", () => {
  render();
  const style = card("target").getAttribute("style");
  assert.match(style, /border-color:\s*var\(--line\)/,
    "the prototype's rgba(120,140,200,0.22) has no token; --line is what night "
    + "mode can reach");
  assert.match(style, /width:\s*188px/, "desktop card is 188px");
});

// ─────────────────────────────────────────────────────────────── port state

test("a wired port is filled, an unwired one is hollow", () => {
  render();
  const dot = (p: any) => p.firstElementChild.getAttribute("style");
  assert.match(dot(portOf("target", "n1|arm|in")), /background:\s*var\(--bg\)/,
    "`arm` has no wire — a filled dot would claim a connection that is not there");
  assert.match(dot(portOf("target", "n1|target|out")), /background:\s*var\(--accent\)/,
    "`target` carries e1; fill is a SHAPE change, so it survives the night palette");
});

test("the lane colour is per PORT, not per node", () => {
  render();
  const ports = [...card("capture").querySelectorAll("[data-port]")] as any[];
  const dot = (p: any) => p.firstElementChild.getAttribute("style");
  assert.match(dot(ports[1]), /border:\s*1\.5px solid var\(--accent\)/,
    "`complete` is a flow port");
  assert.match(dot(ports[2]), /border:\s*1\.5px solid var\(--warn\)/,
    "`frame` is an event port on the SAME side of the SAME card — a per-node "
    + "colour would draw the run cursor and the per-frame event identically");
});

// ───────────────────────────────────────────────────────────────── the ✎

test("the ✎ opens the sheet and never drags the card", () => {
  let dragged = 0;
  render({ onStartDrag: () => { dragged++; } });
  const pencil = card("target").querySelector("[data-flows-edit]");

  pointerDown(pencil);
  assert.equal(dragged, 0,
    "the pointerdown reached the header: pressing ✎ would drag the node out from "
    + "under the finger and the click would land somewhere else");

  click(pencil);
  assert.equal(useStore.getState().flows.editNode, "n1", "the sheet did not open");
  assert.equal(useStore.getState().flows.sel?.id, "n1",
    "the sheet renders the SELECTED node, so opening without selecting shows "
    + "another node's parameters");
});

test("the header IS the drag handle on a canvas", () => {
  let dragged: string | null = null;
  render({ onStartDrag: (id: string) => { dragged = id; } });
  pointerDown(card("target").firstElementChild);
  assert.equal(dragged, "n1");
});

// ────────────────────────────────────────────────────────── tiers and wiring

test("phone narrows the card and drops the footer, in BOTH tabs", () => {
  render({ phone: true });
  assert.match(card("target").getAttribute("style"), /width:\s*150px/);
  assert.equal(renders.target, 0,
    "def.sum() ran, so the footer rendered — at 150px it is the summary that "
    + "overflows first, which is why the design drops it");
});

test("a canvas wires by DRAG, from outputs only", () => {
  const grabs: string[] = [];
  render({ onStartWire: (_n: string, p: string) => grabs.push(p) });
  pointerDown(portOf("target", "n1|arm|in"));
  assert.equal(grabs.length, 0,
    "there is no reverse drag (§D.4 rule 2) — an input that grabbed would leave "
    + "the operator holding a wire with no source");
  pointerDown(portOf("target", "n1|target|out"));
  assert.equal(grabs.join(), "target");
});

test("the auto-graph wires by TAP, from either end", () => {
  const taps: string[] = [];
  render({
    phone: true, auto: true,
    onTapPort: (n: string, p: string, d: string) => taps.push(`${n}|${p}|${d}`),
  });
  const arm = portOf("target", "n1|arm|in");
  click(portOf("target", "n1|target|out"));
  click(arm);
  assert.equal(taps.join(" "), "n1|target|out n1|arm|in",
    "an output arms and an input completes; a tap surface that only listened to "
    + "outputs could never finish a wire");
  assert.match(arm.getAttribute("style"), /width:\s*26px/,
    "the FLOW tab's port hits grow to 26px (README §5)");
});

test("an armed output takes the ring", () => {
  render({ phone: true, auto: true });
  setFlows({ tapWire: { from: "n1", fromPort: "target" } });
  const dot = (p: any) => p.firstElementChild.getAttribute("style");
  const armed = portOf("target", "n1|target|out");
  assert.match(dot(armed), /box-shadow:\s*0 0 0 3px/,
    "nothing marks which port is armed, so the hint bar is the only clue and "
    + "the graph itself says nothing");
  assert.match(dot(armed), /background:\s*var\(--accent\)/,
    "armed fills the dot the same way wired does");
  for (const key of ["n1|arm|in", "n1|next|in"]) {
    assert.ok(!/box-shadow:\s*0 0 0 3px/.test(dot(portOf("target", key))),
      `the ring is on the armed OUTPUT only, not on ${key}`);
  }
});

// ------------------------------------- NOT HONOURED BY A RUN, on the canvas
// The list existed in two places you have to already be looking at: the
// inspector's NOT HONOURED BY A RUN panel, and a confirm dialog that appears
// AFTER you press RUN. The canvas — the surface the operator reads — said
// nothing, so a node whose settings the compiler drops looked exactly like one
// it honours.

const LOSS = [
  { key: "nodes.target", level: "warn",
    detail: "the TARGET node's settings do not reach the run" },
  { key: "nodes.capture", level: "note",
    detail: "the scheduler advances the pool instead" },
];

test("a node whose settings the compile drops is marked ON THE CANVAS", () => {
  render({});
  setFlows({ compiled: { unmapped: LOSS } });
  const mark = card("target").querySelector("[data-node-loss]") as any;
  assert.ok(mark, "the node the compiler ignores looks identical to one it runs");
  assert.equal(mark.getAttribute("data-node-loss"), "warn");
  assert.equal(mark.textContent, "!", "colour is never the only channel");
  assert.match(mark.getAttribute("title") ?? "", /do not reach the run/,
    "a bare glyph makes the operator go hunting; to_plan already wrote the "
    + "sentence that names the setting");
  assert.match(mark.getAttribute("aria-label") ?? "", /not honoured/,
    "the mark has to survive a screen reader and a colourblind eye");
});

test("a NOTE is not a loss — it says the thing happens another way", () => {
  render({});
  setFlows({ compiled: { unmapped: LOSS } });
  assert.ok(!card("capture").querySelector("[data-node-loss]"),
    "marking the notes too would train the mark to mean nothing: a note says "
    + "the cloud hold releases itself, not that it was dropped");
});

test("the loss ring is below selection and busy, and above idle", () => {
  render({});
  setFlows({ compiled: { unmapped: LOSS }, statuses: {}, sel: null });
  assert.match(card("target").getAttribute("style") ?? "", /--warn/,
    "at rest the ring is what makes it visible without opening anything");
  setFlows({ sel: { kind: "node", id: "n1" } });
  assert.match(card("target").getAttribute("style") ?? "", /--accent/,
    "selection is what the operator is doing NOW and outranks a standing fact");
  setFlows({ sel: null, statuses: { n1: "busy" } });
  assert.match(card("target").getAttribute("style") ?? "", /--accent/,
    "so does busy — that is the rig moving");
  setFlows({ statuses: {} });
});

test("a clean compile leaves every card unmarked", () => {
  render({});
  setFlows({ compiled: { unmapped: [] } });
  assert.ok(!card("target").querySelector("[data-node-loss]"));
  setFlows({ compiled: null });
  assert.ok(!card("target").querySelector("[data-node-loss]"),
    "before the first compile nothing is known, and a mark would be a claim");
});

// ------------------------------------------------------------------- report
act(() => { root.unmount(); });
const total = passed + failed;
console.log(`flowNodeDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
