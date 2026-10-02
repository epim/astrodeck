// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// stagesLoopRail.test.tsx - the phone stage list's panel loop and its counts
// line, MOUNTED (#189 S4 item 6; spec 2026-09-23 flows mosaic, 1.4 "How it is
// drawn" and "When the wire is added"; Revision 2 ruling 2; S4 orchestrator
// ruling 8).
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/next/hubs/session/flows/canvas/__tests__/stagesLoopRail.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT IS WORTH GUARDING HERE
//
// The phone has no canvas, so the loop wire the tablet draws as a back-arc is
// invisible there unless the stage list draws it. Spec 1.4 says how: a dashed
// amber rail from the TARGET row to the row of the lane's TAIL, the rows inside
// indented, labelled EVERY PASS: NEXT PANEL; with the wire absent, a LOOP PANELS
// button in its place. So:
//
//   1. THE RAIL ENDS AT THE TAIL (panelLane `laneTail`), the stage the loop
//      wire leaves, not at the first stage the block owns. A rail that stopped
//      at the first stage would tell the operator that only that stage is shot
//      per panel.
//   2. THE RAIL HOLDS THE LANE AND NOTHING ELSE. `stageOrder` is a breadth-
//      first order, so a stage fed from the same parent as the TARGET (a DOME
//      here) sits between the TARGET and its lane; the rail must not swallow
//      it, because that stage is not shot per panel.
//   3. LOOP PANELS SHOWS ONLY WHILE THE WIRE IS ABSENT, and one press is ONE
//      `flowsApplyFraming(id, {}, true)`: one graph write, one compile, and the
//      wire it adds leaves the TAIL. A wire from an earlier stage of the lane
//      is M12, a danger the doctor refuses to run.
//   4. THE COUNTS LINE (ruling 2, ruling 8) is shown while `countsNotice` says
//      so, persistently, and comes down once a save switched the counts,
//      without the flow being reopened.
//   5. CONTROLS: a single target, a mosaic that owns no stage, a mosaic whose
//      lane branches (no single tail, M12), and a flow with no mosaic render
//      the stage list as before: every row a direct child of the list, in
//      `stageOrder`, with no rail, no button and no line.
//
// Every mutant below was run in a private scratch copy of ui/ (#254), and the
// failure it produced is quoted verbatim.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------------ css stub
// The sheet imports `canvas.css`; Node has no loader for it.
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
// A phone: the sheet asks the breakpoint, and the plan editor row locks on it.
win.matchMedia = (q: string) => {
  const m = /min-width:\s*(\d+)px/.exec(q);
  return {
    matches: m ? 390 >= Number(m[1]) : false,
    addEventListener() {}, removeEventListener() {},
    addListener() {}, removeListener() {},
  };
};
win.WebSocket = class { close() {} addEventListener() {} send() {} };
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

// ------------------------------------------------------------- the fake rig
const asked: { url: string; method: string; body: any }[] = [];
/** What the next `PUT /api/flows/{id}` answers with in `migrated`: the
 *  server's counts switch (`["counts"]`), or nothing. */
let switchOnSave = false;
g.fetch = async (url: string, init?: { method?: string; body?: string }) => {
  const method = init?.method ?? "GET";
  const body = init?.body ? JSON.parse(init.body) : undefined;
  asked.push({ url, method, body });
  // `PUT /api/flows/{id}` is `flowsApi.save`; the slice keeps what comes back
  // as the record, so the answer has to be one.
  const one = /^\/api\/flows\/([^/]+)$/.exec(url);
  let data: any = { ok: true };
  if (one && method === "PUT") {
    data = { ...RECORD, ...(body?.flow ?? {}), id: one[1] };
    if (switchOnSave) data.migrated = ["counts"];
  } else if (url === "/api/flows/compile") {
    data = { plan: {}, structural: [], issues: [], unmapped: [] };
  }
  return {
    ok: true, status: 200, statusText: "OK",
    headers: { get: () => "application/json" },
    json: async () => data,
    text: async () => JSON.stringify(data),
  };
};

// ------------------------------------------------------------------ imports
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../../store");
const { NODE_DEFS } = await import("../../../../../../components/flows/nodeDefs");
const { COUNTS_NOTE, COUNTS_DORMANT_ADDENDUM } =
  await import("../../../../../../components/flows/countsNotice");
const { FlowStagesPhoneSheet, LOOP_RAIL_LABEL, stageOrder } = await import("../FlowStagesPhoneSheet");
const { LOOP_PANELS_LABEL } = await import("../FlowNode");
type FlowNodeRec = import("../../../../../../components/flows/flowsTypes").FlowNodeRec;
type FlowEdgeRec = import("../../../../../../components/flows/flowsTypes").FlowEdgeRec;

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => Promise<void> | void): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: unknown, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) {
    throw new Error(`${msg}\n  expected ${JSON.stringify(want)}\n  got      ${JSON.stringify(got)}`);
  }
}

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
const settle = async (): Promise<void> => {
  for (let i = 0; i < 4; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};
const tid = (t: string): any => container.querySelector(`[data-testid="${t}"]`);
const click = (el: any): void => {
  act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
};
/** A stage's row, found by its own port buttons (`data-port="<id>|..."`): the
 *  row card carries no node id of its own. */
const rowOf = (id: string): any =>
  container.querySelector(`[data-port^="${id}|"]`)?.closest('[data-testid="flow-stage-row"]') ?? null;
/** The node id each listed row belongs to, in the order the list draws them. */
const rowIds = (): string[] => [...container.querySelectorAll('[data-testid="flow-stage-row"]')]
  .map((r: any) => String(r.querySelector("[data-port]")?.getAttribute("data-port") ?? "").split("|")[0]);

// ------------------------------------------------------------------ fixture
// DUSK -> TARGET M31 (3 columns by 2 rows) -> CAPTURE LOOP -> FILTER CYCLE, the
// cycle's "pass done" wired back into the TARGET's "next panel". DUSK's window
// also opens a DOME, so the breadth-first `stageOrder` reads DUSK, TARGET,
// DOME, CAPTURE, CYCLE: the DOME sits between the TARGET and its lane, and it
// is not part of that lane (a DOME ends a lane). The CAPTURE comes first in the
// lane and HAS a "pass done" output, so a wire from it is a real, wrong wire
// (M12), not one refused for a missing port.
const P = (type: string, extra: Record<string, unknown> = {}): any =>
  ({ ...(NODE_DEFS as any)[type].params, ...extra });
const node = (id: string, type: string, x: number, y: number, extra: Record<string, unknown> = {}): FlowNodeRec =>
  ({ id, type, x, y, params: P(type, extra) } as FlowNodeRec);
const E = (id: string, from: string, fromPort: string, to: string, toPort: string): FlowEdgeRec =>
  ({ id, from, fromPort, to, toPort });

const ACCEPTED = { counts: "Accepted subs" };
const D = node("d", "dusk", 0, 0);
const T = node("t", "target", 200, 0, { name: "M31", rows: 2, cols: 3, ...ACCEPTED });
const DM = node("dm", "dome", 200, 300);
const C1 = node("c1", "capture", 400, 0);
const CY = node("cy", "cycle", 600, 0);
const LANE_EDGES: FlowEdgeRec[] = [
  E("e1", "d", "window", "t", "arm"),
  E("e2", "d", "window", "dm", "run"),
  E("e3", "t", "target", "c1", "run"),
  E("e4", "c1", "complete", "cy", "run"),
];
const LOOP = E("loop", "cy", "pass", "t", "next");
const MOSAIC_NODES = [D, T, DM, C1, CY];

const RECORD = {
  id: "flow-rail", name: "M31 mosaic", folder: "My flows",
  tagline: "", graph: { nodes: MOSAIC_NODES, edges: [...LANE_EDGES, LOOP] },
  created_ts: 1, updated_ts: 2, last_run: null, last_result: "", readonly: false,
};

const ADMIN_CAPS = ["view.status", "view.preview", "control.mount", "control.capture", "view.site_derived"];

/** Every argument list `flowsApplyFraming` was called with. */
let framed: unknown[][] = [];
const realApply = useStore.getState().flowsApplyFraming;

function seed(nodes: FlowNodeRec[], edges: FlowEdgeRec[], flows: Record<string, unknown> = {}): void {
  act(() => {
    const s = useStore.getState();
    useStore.setState({
      principal: { role: "admin", email: null, caps: ADMIN_CAPS } as never,
      authGate: "open",
      status: { connected: { camera: { connected: true } } } as never,
      equipConnected: true,
      wsPhase: "up",
      toasts: [],
      // A spy that forwards: the real action does the write, so the graph the
      // test reads afterwards is the one the slice made.
      flowsApplyFraming: ((...args: any[]) => {
        framed.push(args);
        return (realApply as any)(...args);
      }) as never,
      flows: {
        ...s.flows,
        record: RECORD as never,
        graph: JSON.parse(JSON.stringify({ nodes, edges })),
        dirty: false,
        sel: null, editNode: null, wire: null, tapWire: null,
        statuses: {}, logs: [], compiled: null, progress: null, countsNote: null,
        run: { ...s.flows.run, phase: "idle", etaS: null },
        ...flows,
      } as never,
    } as never);
  });
  framed = [];
}

async function mount(): Promise<void> {
  await act(async () => { root.render(createElement("div")); });
  await act(async () => {
    root.render(createElement(FlowStagesPhoneSheet as any, { params: { open: RECORD.id }, depth: 0 }));
  });
  await settle();
}

/** The rows as the list drew them before S4: every row a DIRECT child of the
 *  list, in `stageOrder`, and nothing of the loop's or the counts line's. */
function assertPlainList(nodes: FlowNodeRec[], edges: FlowEdgeRec[], what: string): void {
  const list = container.querySelector(".nx-flow-stages");
  assert(list != null, `${what}: the stage list did not render`);
  const want = stageOrder(nodes, edges).map((n) => n.id);
  eq(rowIds().join(","), want.join(","), `${what}: the rows, in the list's order`);
  const direct = [...list.children].filter((c: any) => c.getAttribute("data-testid") === "flow-stage-row");
  eq(direct.length, want.length, `${what}: every row a direct child of the list`);
  eq(list.children.length, want.length, `${what}: and nothing else in the list`);
  assert(tid("flow-stage-rail") == null, `${what}: a rail was drawn`);
  assert(container.querySelector('[data-testid^="flow-stage-loop-"]') == null,
    `${what}: a LOOP PANELS button was offered`);
}

// ================================================================ the rail

// MUTANT "rail drawn to the first owned stage" (the rail's lane is
// `panelLane(...).slice(0, 1)`, its end that lane's last). Observed,
// stagesLoopRail.test 8/10:
//   x a looped mosaic's rail runs from the TARGET row to the TAIL's row, the
//     lane indented inside it: the rail ends at the lane's TAIL, the stage
//     the loop wire leaves
//     expected "cy"
//     got      "c1"
//   x the rail holds the lane and nothing else: a DOME between them in the
//     list stays outside: the lane reads as one block under its TARGET, and
//     the rows it moved past keep their order
//     expected "d,t,c1,cy,dm"
//     got      "d,t,c1,dm,cy"
// MUTANT "rail label dropped" (the label renders ""). Observed,
// stagesLoopRail.test 9/10:
//   x a looped mosaic's rail runs from the TARGET row to the TAIL's row, the
//     lane indented inside it: the rail's label
//     expected "EVERY PASS: NEXT PANEL"
//     got      ""
// MUTANT "rail drawn solid" (`2px solid var(--warn)`). Observed,
// stagesLoopRail.test 9/10:
//   x a looped mosaic's rail runs from the TARGET row to the TAIL's row, the
//     lane indented inside it: the rail is a dashed amber line, got style
//     "display: flex; flex-direction: column; gap: 8px; margin-left: 14px;
//     padding-left: 12px; border-left: 2px solid var(--warn);"
// MUTANT "rail rows not indented" (the rail body loses its margin-left and
// padding-left). Observed, stagesLoopRail.test 9/10:
//   x a looped mosaic's rail runs from the TARGET row to the TAIL's row, the
//     lane indented inside it: the rows inside the rail are indented, got
//     style "display: flex; flex-direction: column; gap: 8px; border-left:
//     2px dashed var(--warn);"
await test("a looped mosaic's rail runs from the TARGET row to the TAIL's row, the lane indented inside it", async () => {
  seed(MOSAIC_NODES, [...LANE_EDGES, LOOP]);
  await mount();

  assert(tid("session-flow-stages") != null, "precondition: the phone sheet did not render");
  eq([...rowIds()].sort().join(","), ["c1", "cy", "d", "dm", "t"].join(","),
    "every stage is listed exactly once, rail or no rail");

  const rail = tid("flow-stage-rail");
  assert(rail != null, "a mosaic with its loop wire drew no rail: the loop is invisible on the phone");
  eq(rail.getAttribute("data-rail-from"), "t", "the rail starts at the TARGET");
  eq(rail.getAttribute("data-rail-to"), "cy", "the rail ends at the lane's TAIL, the stage the loop wire leaves");

  const body = tid("flow-stage-rail-body");
  assert(body != null && rail.contains(body), "the rail has no indented body");
  const style = String(body.getAttribute("style"));
  assert(/border-left:\s*2px dashed var\(--warn\)/.test(style),
    `the rail is a dashed amber line, got style "${style}"`);
  assert(/margin-left:\s*\d+px/.test(style) && /padding-left:\s*\d+px/.test(style),
    `the rows inside the rail are indented, got style "${style}"`);

  assert(rail.contains(rowOf("t")) && !body.contains(rowOf("t")),
    "the TARGET row is where the rail starts, and it is not itself indented");
  assert(body.contains(rowOf("c1")), "the lane's first stage is not inside the rail");
  assert(body.contains(rowOf("cy")), "the TAIL's row is not inside the rail, so the rail stops short of it");
  const inside = [...body.querySelectorAll('[data-testid="flow-stage-row"]')];
  assert(inside[inside.length - 1] === rowOf("cy"), "the rail ends at the TAIL's row, not below it");

  eq(String(tid("flow-stage-rail-label")?.textContent), "EVERY PASS: NEXT PANEL", "the rail's label");
  eq(LOOP_RAIL_LABEL, "EVERY PASS: NEXT PANEL", "the exported label is the spec's");
});

// MUTANT "rail spans the stage order" (the rail takes every row between the
// TARGET and the tail in `stageOrder`). Observed, stagesLoopRail.test 9/10:
//   x the rail holds the lane and nothing else: a DOME between them in the
//     list stays outside: the DOME opened by the same DUSK was drawn inside
//     the rail, as if it were shot per panel
await test("the rail holds the lane and nothing else: a DOME between them in the list stays outside", async () => {
  seed(MOSAIC_NODES, [...LANE_EDGES, LOOP]);
  await mount();
  const rail = tid("flow-stage-rail");
  assert(rail != null, "precondition: no rail");
  assert(!rail.contains(rowOf("dm")),
    "the DOME opened by the same DUSK was drawn inside the rail, as if it were shot per panel");
  eq(rowIds().join(","), "d,t,c1,cy,dm",
    "the lane reads as one block under its TARGET, and the rows it moved past keep their order");
});

// MUTANT "LOOP PANELS shown while the wire exists" (the phone's version: the
// button is offered wherever `loopSource` names a tail, rail or no rail).
// Observed, stagesLoopRail.test 8/10:
//   x with the loop wire in place there is no LOOP PANELS button: LOOP PANELS
//     is offered on a mosaic that already loops: a press would do nothing, or
//     add a second wire
//   x one press is one flowsApplyFraming(id, {}, true): one write, one
//     compile, a wire from the TAIL: LOOP PANELS stayed after it did its work
// The same mutant in the #/next card's shared `offersLoopPanels` leaves this
// file green (10/10): a looped block takes the rail branch first, so the
// phone never asks the offer for it. cardLoopPanels.test.tsx holds that one.
await test("with the loop wire in place there is no LOOP PANELS button", async () => {
  seed(MOSAIC_NODES, [...LANE_EDGES, LOOP]);
  await mount();
  assert(tid("flow-stage-rail") != null, "precondition: no rail");
  assert(tid("flow-stage-loop-t") == null,
    "LOOP PANELS is offered on a mosaic that already loops: a press would do nothing, or add a second wire");
});

// ================================================== the wire absent: LOOP PANELS

await test("with the wire absent a LOOP PANELS button takes the rail's place, under the TARGET row", async () => {
  seed(MOSAIC_NODES, LANE_EDGES);
  await mount();
  assert(tid("flow-stage-rail") == null, "a rail was drawn over a mosaic that has no loop wire");
  const btn = tid("flow-stage-loop-t");
  assert(btn != null, "a mosaic with no loop wire offers no LOOP PANELS on the phone");
  eq(String(btn.textContent).trim(), LOOP_PANELS_LABEL, "the button's label");
  eq(LOOP_PANELS_LABEL, "LOOP PANELS", "the exported label is the spec's");
  assert(/FILTER CYCLE/.test(String(btn.getAttribute("aria-label"))),
    `the button's name says which stage the wire will leave, got "${btn.getAttribute("aria-label")}"`);
  // Placed where the rail would start: straight after the TARGET's row.
  const list = container.querySelector(".nx-flow-stages");
  const kids = [...list.children];
  const at = kids.indexOf(rowOf("t"));
  assert(at >= 0, "precondition: the TARGET row is not a direct child of the list");
  assert(kids[at + 1]?.contains(btn), "LOOP PANELS is not directly under its TARGET's row");
  // The lane's rows are drawn as before, since nothing loops yet.
  eq(rowIds().join(","), stageOrder(MOSAIC_NODES, LANE_EDGES).map((n) => n.id).join(","),
    "without a rail the rows keep the list's order");
});

// MUTANT "button adds the wire from the first owned stage" (the press wires
// `panelLane(g, id)[0]`'s "pass done" to the TARGET through `flowsConnect`,
// M12 by panelLane). Observed, stagesLoopRail.test 9/10:
//   x one press is one flowsApplyFraming(id, {}, true): one write, one
//     compile, a wire from the TAIL: the press adds one wire, from the lane's
//     TAIL; from any earlier stage it is M12
//     expected "cy.pass -> t.next"
//     got      "c1.pass -> t.next"
// MUTANT "press sends no loop" (`applyFraming(id, {}, undefined)`, a DONE
// that says nothing about the loop). Observed, stagesLoopRail.test 9/10:
//   x one press is one flowsApplyFraming(id, {}, true): one write, one
//     compile, a wire from the TAIL: the press adds one wire, from the lane's
//     TAIL; from any earlier stage it is M12
//     expected "cy.pass -> t.next"
//     got      ""
await test("one press is one flowsApplyFraming(id, {}, true): one write, one compile, a wire from the TAIL", async () => {
  seed(MOSAIC_NODES, LANE_EDGES);
  await mount();
  const btn = tid("flow-stage-loop-t");
  assert(btn != null, "precondition: no LOOP PANELS button");

  let writes = 0;
  const unsub = useStore.subscribe((s: any, prev: any) => {
    if (s.flows.graph !== prev.flows.graph) writes++;
  });
  const compilesBefore = asked.filter((a) => a.url === "/api/flows/compile").length;
  click(btn);
  await settle();
  unsub();

  // The wire first, so a press that wired the wrong stage says which.
  const edges = useStore.getState().flows.graph.edges as FlowEdgeRec[];
  const added = edges.filter((e) => !LANE_EDGES.some((x) => x.id === e.id));
  eq(added.map((e) => `${e.from}.${e.fromPort} -> ${e.to}.${e.toPort}`).join(" | "), "cy.pass -> t.next",
    "the press adds one wire, from the lane's TAIL; from any earlier stage it is M12");
  eq(JSON.stringify(framed), JSON.stringify([["t", {}, true]]),
    "the press is exactly one flowsApplyFraming(id, {}, true)");
  eq(writes, 1, "the press wrote the graph more than once (or not at all)");
  eq(asked.filter((a) => a.url === "/api/flows/compile").length - compilesBefore, 1,
    "the press did not start exactly one compile");
  eq(useStore.getState().flows.dirty, true, "an added wire the SAVE would skip");

  assert(tid("flow-stage-rail") != null, "the rail did not appear once the wire was added");
  assert(tid("flow-stage-loop-t") == null, "LOOP PANELS stayed after it did its work");
});

// ================================================================ controls

// MUTANT "phone offer on a single target" (the offer is any unrailed
// TARGET's `laneTail`, with no multi-panel or pass-port check). Observed,
// stagesLoopRail.test 8/10:
//   x CONTROL: a single target with a lane gets neither a rail nor LOOP
//     PANELS: a 1x1 block with a pass wire: and nothing else in the list
//     expected 5
//     got      6
//   x CONTROL: a flow with no mosaic renders the list as before: a flow with
//     no mosaic: and nothing else in the list
//     expected 3
//     got      4
// The same guard dropped from the #/next card's shared `offersLoopPanels`
// (the block read as 2 rows) leaves this file green, 10/10: the sheet also
// asks `loopSource`, which answers null for one panel. cardLoopPanels.test
// holds that one.
await test("CONTROL: a single target with a lane gets neither a rail nor LOOP PANELS", async () => {
  const S = node("t", "target", 200, 0, { name: "NGC 7331", ...ACCEPTED });
  const nodes = [D, S, DM, C1, CY];
  seed(nodes, [...LANE_EDGES, LOOP]);
  await mount();
  assertPlainList(nodes, [...LANE_EDGES, LOOP], "a 1x1 block with a pass wire");
  seed(nodes, LANE_EDGES);
  await mount();
  assertPlainList(nodes, LANE_EDGES, "a 1x1 block without one");
});

// MUTANT "phone offer on any multi-panel target" (the offer is any unrailed
// TARGET with rows x cols above 1, its own node named as the tail).
// Observed, stagesLoopRail.test 8/10:
//   x with the wire absent a LOOP PANELS button takes the rail's place,
//     under the TARGET row: the button's name says which stage the wire will
//     leave, got "LOOP PANELS: wire TARGET 'pass done' to this TARGET's 'next
//     panel', so every pass moves to the next panel"
//   x CONTROL: a mosaic that owns no stage, or whose lane branches, gets
//     neither a rail nor LOOP PANELS: a mosaic with no stage: and nothing
//     else in the list
//     expected 3
//     got      4
await test("CONTROL: a mosaic that owns no stage, or whose lane branches, gets neither a rail nor LOOP PANELS", async () => {
  // No lane at all: nothing to loop.
  const bare = [D, T, DM];
  const bareEdges = LANE_EDGES.slice(0, 2);
  seed(bare, bareEdges);
  await mount();
  assertPlainList(bare, bareEdges, "a mosaic with no stage");
  // TARGET -> CAPTURE and TARGET -> FILTER CYCLE: a branched lane has no
  // single tail (M12), so no wire is its loop wire and a press could add none.
  // Drawn with a pass wire too: from a branch, it is not a loop to put a rail on.
  const brNodes = [D, T, DM, C1, CY];
  const brEdges = [...LANE_EDGES.slice(0, 3), E("e5", "t", "target", "cy", "run")];
  seed(brNodes, brEdges);
  await mount();
  assertPlainList(brNodes, brEdges, "a mosaic whose lane branches");
  seed(brNodes, [...brEdges, LOOP]);
  await mount();
  assertPlainList(brNodes, [...brEdges, LOOP], "a branched mosaic with a pass wire");
});

await test("CONTROL: a flow with no mosaic renders the list as before", async () => {
  const S = node("t", "target", 200, 0, { name: "M16", ...ACCEPTED });
  const SL = node("sl", "slew", 400, 0);
  const nodes = [D, S, SL];
  const edges = [E("e1", "d", "window", "t", "arm"), E("e2", "t", "target", "sl", "run")];
  seed(nodes, edges);
  await mount();
  assertPlainList(nodes, edges, "a flow with no mosaic");
  assert(tid("flow-stages-counts") == null, "a flow that counts accepted subs has no counts line");
});

// ======================================================== the counts line

// MUTANT "line hidden once the flow is edited" (`countsLine && !dirty`).
// Observed, stagesLoopRail.test 8/10:
//   x the counts line shows while a TARGET counts every sub taken, and
//     survives an unrelated edit: the line is persistent: an unrelated edit
//     took it down
//   x a save that switched the counts takes the line down, without a reopen:
//     precondition: no counts line
// MUTANT "addendum dropped (note not passed)" (`countsNotice(graph, null)`).
// Observed, stagesLoopRail.test 9/10:
//   x the counts line shows while a TARGET counts every sub taken, and
//     survives an unrelated edit: a flow with a dormant session says its
//     session keeps its count
//     expected "This flow counts every sub taken, rejected ones included. New
//       flows count accepted subs only, and saving this flow switches it. Its
//       armed session keeps its count until you CONTINUE."
//     got      "This flow counts every sub taken, rejected ones included. New
//       flows count accepted subs only, and saving this flow switches it."
// MUTANT "counts line never shown" (`false && countsLine`). Observed,
// stagesLoopRail.test 8/10:
//   x the counts line shows while a TARGET counts every sub taken, and
//     survives an unrelated edit: a flow that counts attempts shows no counts
//     line on the phone
//   x a save that switched the counts takes the line down, without a reopen:
//     a save that did not switch the counts took the line down
await test("the counts line shows while a TARGET counts every sub taken, and survives an unrelated edit", async () => {
  const OLD = node("t", "target", 200, 0, { name: "M16" });
  delete (OLD.params as any).counts;
  const nodes = [D, OLD];
  const edges = [E("e1", "d", "window", "t", "arm")];
  seed(nodes, edges);
  await mount();
  const line = tid("flow-stages-counts");
  assert(line != null, "a flow that counts attempts shows no counts line on the phone");
  eq(String(line.textContent).trim(), COUNTS_NOTE, "the line is ruling 2's sentence");
  act(() => { useStore.getState().flowsAddNode("slew" as never, { x: 1, y: 1 }); });
  await settle();
  assert(tid("flow-stages-counts") != null, "the line is persistent: an unrelated edit took it down");

  seed(nodes, edges, { countsNote: `${COUNTS_NOTE} ${COUNTS_DORMANT_ADDENDUM}` });
  await mount();
  eq(String(tid("flow-stages-counts")?.textContent).trim(), `${COUNTS_NOTE} ${COUNTS_DORMANT_ADDENDUM}`,
    "a flow with a dormant session says its session keeps its count");
});

// MUTANT "notice read only at open" (the sheet computes the line once, as it
// mounts: `useState(() => countsNotice(...))`). Observed,
// stagesLoopRail.test 9/10:
//   x a save that switched the counts takes the line down, without a reopen:
//     the line still tells the operator that saving switches a flow the save
//     just switched
await test("a save that switched the counts takes the line down, without a reopen", async () => {
  const OLD = node("t", "target", 200, 0, { name: "M16" });
  delete (OLD.params as any).counts;
  const nodes = [D, OLD];
  const edges = [E("e1", "d", "window", "t", "arm")];

  // CONTROL first: an answer that switched nothing leaves the line up, so the
  // line reads the graph, not "a save happened".
  seed(nodes, edges, { dirty: true });
  await mount();
  switchOnSave = false;
  click(tid("flow-stages-save"));
  await settle();
  assert(asked.some((a) => a.method === "PUT"), "precondition: SAVE sent no PUT");
  assert(tid("flow-stages-counts") != null, "a save that did not switch the counts took the line down");

  seed(nodes, edges, { dirty: true });
  await mount();
  assert(tid("flow-stages-counts") != null, "precondition: no counts line");
  switchOnSave = true;
  click(tid("flow-stages-save"));
  await settle();
  switchOnSave = false;
  eq(useStore.getState().flows.graph.nodes.find((n: any) => n.id === "t")?.params?.counts, "Accepted subs",
    "precondition: the slice did not write the switch into the graph");
  assert(tid("flow-stages-counts") == null,
    "the line still tells the operator that saving switches a flow the save just switched");
});

await act(async () => { root.render(createElement("div")); });

// ------------------------------------------------------------------- report
const total = passed + failed;
for (const f of failures) console.log(f);
console.log(`stagesLoopRail.test: ${passed}/${total} passed`);
export const result = { passed, failed, total };
