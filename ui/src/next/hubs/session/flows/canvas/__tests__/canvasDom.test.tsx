// canvasDom.test.tsx - SESSION / FLOWS, the rebuilt canvas surface, its toolbar
// and the phone stage sheet, MOUNTED (wave R7, T-R7-1).
//
//   Run directly:  npx tsx src/next/hubs/session/flows/canvas/__tests__/canvasDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc --noEmit`.
//
// WHAT IS WORTH GUARDING HERE
//
//   1. IT RENDERED, WITH A REAL GRAPH. A surface that silently produced nothing
//      would let every assertion below pass over a blank rectangle, so the first
//      test names the marker AND counts the stages and wires a seeded three-node
//      graph must produce.
//   2. A DRAG MOVES A STAGE THROUGH THE TRANSFORM, not by screen pixels. At
//      zoom 0.5 a 20 px drag is 40 world units; a rebuild that forgot the
//      transform would move the card half as far as the finger and nothing
//      would fail.
//   3. THE WIRE GRAMMAR SURVIVED. A flow output onto a flow input connects; an
//      EVENT output onto a flow input is refused with the sentence and leaves no
//      edge. That refusal is the whole reason ports have lanes.
//   4. A VIEWER SEES THE SAME TOOLBAR, LOCKED, with the capability named and no
//      request fired.
//   5. RUN IS ARMED AND STOP IS NOT. Starting the rig is a two-tap confirm;
//      stopping it is one tap, because an emergency motion stop that needed
//      confirming is the defect, not the safety.
//   6. THE DEAD CONTROL IS GONE. The legacy `i` button wrote `ui.notesOpen`,
//      which nothing in the repository reads (wave section 6.1 defect 2).
//   7. THE PHONE HAS AN EDITING PATH AT ALL, and it is not a canvas: the stage
//      list, the monitor readouts, the tap-to-wire footer.
//
// Convention: shell-and-tests.md section 4 - jsdom by hand, createRoot + act,
// native events, printed tally plus the `{ passed, failed, total }` export.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------------ css stub
// The area's roots import `canvas.css`, by contract (one shared `next.css` is
// owned by one task; every other area writes its own file). Node has no idea
// what a `.css` file is, so a synchronous load hook answers with an empty
// module - the same hook `shellDom.test.tsx` uses for `next.css`.
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

// One number drives the breakpoint, so the same file can render the tablet
// canvas and the phone sheet. Starts at TABLET - the narrowest width the canvas
// exists at.
let viewportW = 820;
win.matchMedia = (q: string) => {
  const m = /min-width:\s*(\d+)px/.exec(q);
  return {
    matches: m ? viewportW >= Number(m[1]) : false,
    addEventListener() {}, removeEventListener() {},
    addListener() {}, removeListener() {},
  };
};
win.WebSocket = class { close() {} addEventListener() {} send() {} };
win.Element.prototype.setPointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.releasePointerCapture = function () { /* jsdom has none */ };

// jsdom implements no hit testing, and the wire drop is resolved against the
// element under the pointerup. The test says which element that is; the
// component still has to ask for it, read its `data-port` and apply the
// grammar, which is the part under test.
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

// ------------------------------------------------------------- the fake rig
const asked: { url: string; method: string; body: any }[] = [];
g.fetch = async (url: string, init?: { method?: string; body?: string }) => {
  const method = init?.method ?? "GET";
  const body = init?.body ? JSON.parse(init.body) : undefined;
  asked.push({ url, method, body });
  // `PUT /api/flows/{id}` is `flowsApi.save`, and the slice puts what comes back
  // INTO `flows.record`. A stub answering `{ok: true}` there would leave the
  // editor holding a record with no id and no `readonly`, so every assertion
  // after a save would be about a flow that does not exist.
  const one = /^\/api\/flows\/([^/]+)$/.exec(url);
  const data = one && method === "PUT"
    ? { ...RECORD, ...(body?.flow ?? {}), id: one[1] }
    : { ok: true };
  return {
    ok: true, status: 200, statusText: "OK",
    headers: { get: () => "application/json" },
    json: async () => data,
    text: async () => "{}",
  };
};

// ------------------------------------------------------------------ imports
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../../store");
const { runBlockedReason } = await import("../../../../../../components/flows/flowRunControls");
const { FlowCanvasSurface } = await import("../FlowCanvasSurface");
const { FlowCanvasToolbar } = await import("../FlowCanvasToolbar");
const { FlowStagesPhoneSheet } = await import("../FlowStagesPhoneSheet");
const { flowCanvasSheets } = await import("../sheets");
const {
  ADD_STAGE_LABEL, CHECKS_DRAFT_PREFIX, IDLE_LOG_TEXT, RUN_UNSAVED_REASON,
  SAVE_CLEAN_REASON, SAVE_READONLY_REASON, SAVE_STATE_CLEAN, SAVE_STATE_DIRTY,
  SAVE_STATE_READONLY, resolveWireDrop,
  MARK_LOST, MARK_PARTIAL, MARK_RIG, RIG_VALUE_PREFIX,
} = await import("../canvasModel");
const { clearMountedFlowCanvas, flowCanvasDropPoint, setMountedFlowCanvas } =
  await import("../canvasMount");
const { resetRouterCacheForTests } = await import("../../../../../router");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
async function testAsync(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg}\n  expected ${String(want)}\n  got      ${String(got)}`);
}

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
const settle = async (): Promise<void> => {
  for (let i = 0; i < 4; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};
const tid = (t: string): any => container.querySelector(`[data-testid="${t}"]`);
const all = (sel: string): any[] => [...container.querySelectorAll(sel)];
const click = (el: any): void => {
  act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
};
/** A pointer event React can read. `buttons: 1` matters: the surface self-heals
 *  a latch when a non-touch move arrives with no button held, so a move sent
 *  with the default `buttons: 0` would end the very gesture under test. */
const ptr = (el: any, type: string, clientX: number, clientY: number): void => {
  act(() => {
    el.dispatchEvent(new win.MouseEvent(type, {
      bubbles: true, cancelable: true, clientX, clientY, button: 0, buttons: 1,
    }));
  });
};

// ------------------------------------------------------------------ fixture
// dusk -> target -> slew. `dusk` has one FLOW output (`window`) and one EVENT
// output (`nightend`), which is what makes the lane refusal testable without
// inventing a node type.
const GRAPH = {
  nodes: [
    { id: "n1", type: "dusk", x: 0, y: 0, params: {} },
    { id: "n2", type: "target", x: 300, y: 0, params: {} },
    { id: "n3", type: "slew", x: 600, y: 0, params: {} },
  ],
  edges: [
    { id: "e1", from: "n1", fromPort: "window", to: "n2", toPort: "arm" },
    { id: "e2", from: "n2", fromPort: "target", to: "n3", toPort: "run" },
  ],
};

const RECORD = {
  id: "flow-m16", name: "M16 - full-service night", folder: "My flows",
  tagline: "dusk to dawn", graph: GRAPH, created_ts: 1, updated_ts: 2,
  last_run: null, last_result: "", readonly: false,
};

const ADMIN_CAPS = [
  "view.status", "view.preview", "control.mount", "control.capture",
  "view.site_derived",
];

/** Every seed goes through here so no test inherits another's run phase, wire in
 *  flight or selection. `flowsRun` writes `run.phase` optimistically and nothing
 *  server-side ever clears it.
 *
 *  `dirty: false` is part of that, and it is not cosmetic: `flowsOpen` clears it,
 *  so a freshly opened flow IS clean, and RUN is now refused while it is not
 *  (`unsavedRunReason`). A fixture that inherited the drag test's `dirty` would
 *  have every later test pressing a locked RUN. */
function seed(role: string, caps: string[], flows: Record<string, unknown> = {}): void {
  act(() => {
    const s = useStore.getState();
    useStore.setState({
      principal: { role, email: null, caps } as never,
      authGate: "open",
      status: { connected: { camera: { connected: true } } } as never,
      equipConnected: true,
      wsPhase: "up",
      toasts: [],
      flows: {
        ...s.flows,
        record: RECORD as never,
        graph: JSON.parse(JSON.stringify(GRAPH)),
        dirty: false,
        sel: null, editNode: null, wire: null, tapWire: null,
        statuses: {}, logs: [], compiled: null,
        pan: { x: 20, y: 10 }, zoom: 0.5,
        run: { ...s.flows.run, phase: "idle", etaS: null },
        ui: { ...s.flows.ui, notesOpen: false, logOpen: false },
        ...flows,
      } as never,
    } as never);
  });
}

async function mount(node: any): Promise<void> {
  await act(async () => { root.render(createElement("div")); });
  await act(async () => { root.render(node); });
  await settle();
}

// =========================================================== 1. it rendered

seed("admin", ADMIN_CAPS);
await mount(createElement(FlowCanvasSurface as any, { tier: "tablet" }));

test("precondition: the canvas rendered the seeded graph, stages and wires", () => {
  assert(tid("flows-canvas") != null, "no flows-canvas marker - the fixture is wrong, not the surface");
  eq(all('[data-testid="flow-node"]').length, 3, "three seeded stages must draw three cards");
  eq(all('[data-testid="flow-wire"]').length, 2, "two seeded edges must draw two wires");
  assert(/DUSK WINDOW/.test(container.textContent), "the stage card carries the vocabulary's own label");
  assert(tid("flow-zoom") != null, "the zoom cluster is missing");
  assert(tid("flow-log") != null, "the log strip is missing");
  assert(container.textContent.includes(IDLE_LOG_TEXT),
    `an empty ring must say so with a hyphen, got "${String(container.textContent).slice(0, 200)}"`);
});

test("the log's idle sentence carries a hyphen, not an em-dash (defect 4)", () => {
  assert(!/Idle\s*—/.test(container.textContent), "the em-dash came back into IDLE_LOG_TEXT");
});

test("every wire anchor came from the shared geometry, so a wire ends on its dot", () => {
  // The first edge leaves `dusk.window`, which is output row 0 on a node with no
  // inputs: x = 0 + 188, y = 0 + 37 + 0*20 + 10. Asserting the emitted path is
  // what catches a rebuild that re-derived the anchor formula instead of
  // importing it.
  const d = String(all('[data-testid="flow-wire"]')[0].getAttribute("d"));
  assert(d.startsWith("M188 47 C"), `the first wire must start at the shared anchor, got "${d}"`);
});

// ================================================ 2. a drag moves a stage

test("dragging a stage writes flowsMoveNode in WORLD units, not screen pixels", () => {
  const head = container.querySelector('[data-node-id="n1"] .nx-flow-node-head');
  assert(head != null, "the stage header is the drag handle and it is missing");
  ptr(head, "pointerdown", 120, 60);
  ptr(win, "pointermove", 140, 80);

  const moved = useStore.getState().flows.graph.nodes.find((n: any) => n.id === "n1");
  // pan (20,10), zoom 0.5: a 20 px screen move is 40 world units.
  eq(moved?.x, 40, "the drag did not travel through the pan/zoom transform");
  eq(moved?.y, 40, "the drag did not travel through the pan/zoom transform");
  assert(useStore.getState().flows.sel?.id === "n1", "a drag selects the stage it grabbed");
  ptr(win, "pointerup", 140, 80);
});

// ============================================ 3. the wire grammar survived

test("a flow output dropped on a flow input connects", () => {
  seed("admin", ADMIN_CAPS);
  const out = container.querySelector('[data-port="n1|window|out"]');
  const inp = container.querySelector('[data-port="n3|run|in"]');
  assert(out != null && inp != null, "the ports the drop needs are not on the card");

  ptr(out, "pointerdown", 200, 60);
  assert(useStore.getState().flows.wire != null, "the press did not begin a wire");
  hitTarget = inp;
  ptr(win, "pointerup", 400, 60);

  const edges = useStore.getState().flows.graph.edges;
  assert(edges.some((e: any) => e.from === "n1" && e.fromPort === "window" && e.to === "n3" && e.toPort === "run"),
    "the compatible drop made no edge");
  assert(useStore.getState().flows.wire == null, "the pending wire outlived the drop");
});

test("an EVENT output dropped on a flow input is refused, with the sentence and no edge", () => {
  seed("admin", ADMIN_CAPS);
  const before = useStore.getState().flows.graph.edges.length;
  const out = container.querySelector('[data-port="n1|nightend|out"]');
  const inp = container.querySelector('[data-port="n3|run|in"]');
  assert(out != null && inp != null, "the ports the refusal needs are not on the card");

  ptr(out, "pointerdown", 200, 80);
  hitTarget = inp;
  ptr(win, "pointerup", 400, 60);
  hitTarget = null;

  const after = useStore.getState().flows.graph.edges;
  // The count alone is not enough: inputs are single-occupancy, so a wrongly
  // accepted drop would REPLACE the incumbent edge and leave the total the same.
  assert(!after.some((e: any) => e.from === "n1" && e.fromPort === "nightend"),
    "the event output was wired into a flow input");
  eq(after.length, before, "a refused drop must leave the graph exactly as it was");
  const toasts = useStore.getState().toasts;
  assert(toasts.some((t: any) => /Event output can't feed a flow input/.test(String(t.title))),
    `the refusal must say why, got ${JSON.stringify(toasts.map((t: any) => t.title))}`);
});

test("a drop on empty canvas clears the wire and says nothing", () => {
  const res = resolveWireDrop({ from: "n1", fromPort: "window" }, null, () => "flow");
  eq(res.ok, false, "a miss is not a connection");
  eq((res as any).refusal, null, "a miss must be SILENT - a toast there would fire on every stray release");
});

// ================================ 3b. the canvas's own editing affordances

await testAsync("the pencil is a live door: it selects the stage AND opens the node sheet", async () => {
  seed("admin", ADMIN_CAPS);
  win.location.hash = "#/session/flows";
  await settle();
  const pencil = container.querySelector('[data-node-id="n2"] [data-flows-edit]');
  assert(pencil != null, "the stage card has no edit control at all");
  click(pencil);
  await settle();

  eq(useStore.getState().flows.editNode, "n2", "the pencil must set the seam the node sheet reads");
  eq(useStore.getState().flows.sel?.id, "n2", "and select the stage, or the sheet shows another one");
  assert(/flowNode/.test(String(win.location.hash)),
    `the pencil must also put the sheet on the glass, got "${win.location.hash}"`);
  win.location.hash = "#/session/flows";
  await settle();
});

test("selecting a wire offers a remove control on the wire, and it removes THAT wire", () => {
  seed("admin", ADMIN_CAPS);
  assert(tid("flow-wire-delete") == null, "the remove control must appear only for a selected wire");
  click(all('[data-testid="flow-wire"]')[0]);
  const cut = tid("flow-wire-delete");
  assert(cut != null, "selecting a wire offered no way to remove it");
  // The midpoint of the two port anchors: (188,47) and (300,47).
  assert(/translate3d\(244px,47px,0\)/.test(String(cut.getAttribute("style"))),
    `the control has to sit ON the curve, got "${cut.getAttribute("style")}"`);

  click(cut);
  const left = useStore.getState().flows.graph.edges;
  eq(left.length, 1, "removing one wire must remove exactly one");
  assert(!left.some((e: any) => e.id === "e1"), "the wrong wire was removed");
});

test("the zoom cluster steps the zoom and leaves the pan alone", () => {
  seed("admin", ADMIN_CAPS);
  const pan0 = { ...useStore.getState().flows.pan };
  click(tid("flow-zoom-in"));
  const s = useStore.getState().flows;
  // 0.5 * 1.15. The buttons deliberately do NOT re-anchor, unlike the wheel.
  assert(Math.abs(s.zoom - 0.575) < 1e-9, `zoom in must step by 1.15, got ${s.zoom}`);
  eq(s.pan.x, pan0.x, "the buttons must not re-anchor the pan");
  eq(s.pan.y, pan0.y, "the buttons must not re-anchor the pan");
  // 57, not 58: 0.5 * 1.15 lands at 0.57499999999999996 in binary floating
  // point, and the readout rounds what the store actually holds.
  assert(/57%/.test(tid("flow-zoom").textContent),
    `the readout is the only place the zoom is shown, got "${tid("flow-zoom").textContent}"`);
});

test("the log strip opens onto the ring, newest first", () => {
  seed("admin", ADMIN_CAPS);
  act(() => {
    useStore.getState().flowsAppendLog("first line", "info");
    useStore.getState().flowsAppendLog("second line", "warn");
  });
  assert(tid("flow-log-body") == null, "the scrollback starts collapsed");
  click(tid("flow-log-toggle"));
  const body = tid("flow-log-body");
  assert(body != null, "the LOG bar did not open the scrollback");
  const text = String(body.textContent);
  assert(text.indexOf("second line") < text.indexOf("first line"),
    `the newest line has to be first, got "${text}"`);
  eq(tid("flow-log-toggle").getAttribute("aria-expanded"), "true", "the bar must carry its state");
  click(tid("flow-log-toggle"));
});

// ============================================ 4. the viewer sees it locked

await testAsync("a viewer sees RUN honest-disabled with the reason, and fires nothing", async () => {
  seed("viewer", ["view.status", "view.preview"]);
  await mount(createElement(FlowCanvasToolbar as any));

  const run = tid("flow-run");
  assert(run != null, "no RUN button on the toolbar");
  eq(run.getAttribute("aria-disabled"), "true", "RUN must be honest-disabled, never natively disabled");
  assert(run.getAttribute("disabled") == null,
    "the native disabled attribute strips the control from the accessibility tree, reason and all");
  const want = runBlockedReason(false, true, false);
  eq(run.getAttribute("title"), want, "the lock must name the capability the server enforces");

  const before = asked.length;
  click(run);
  await settle();
  eq(asked.length, before, "a locked RUN reached the network");
  assert(useStore.getState().toasts.some((t: any) => String(t.title) === want),
    "a locked press must SAY the reason, not swallow the tap");
});

test("a viewer's TONIGHT names the derived-site capability, not view.status", () => {
  const tonight = tid("flow-tonight");
  assert(tonight != null, "no TONIGHT button on the toolbar");
  eq(tonight.getAttribute("aria-disabled"), "true", "TONIGHT is gated on view.site_derived");
  assert(/Tonight is worked out from the observatory site, so it needs /
    .test(String(tonight.getAttribute("title"))),
  `the sentence is verbatim, got "${tonight.getAttribute("title")}"`);
});

test("the validation pill says NOT CHECKED before the checker has answered", () => {
  const pill = tid("flow-checks");
  assert(pill != null, "no validation pill");
  assert(/NOT CHECKED/.test(pill.textContent),
    `a null compile must not read as GRAPH VALID, got "${pill.textContent}"`);
});

// ================================== 5. RUN is armed, STOP is a single tap

await testAsync("RUN arms before it starts, and STOP never arms", async () => {
  seed("admin", ADMIN_CAPS);
  await mount(createElement(FlowCanvasToolbar as any));

  const run = tid("flow-run");
  eq(run.getAttribute("data-armed"), "false", "RUN must be a two-tap arm");
  const before = asked.filter((a) => a.method === "POST").length;
  click(run);
  await settle();
  eq(tid("flow-run").getAttribute("data-armed"), "true", "the first tap must arm, not start");
  eq(asked.filter((a) => a.method === "POST").length, before,
    "the first tap on RUN started the rig");
  assert(/CONFIRM RUN/.test(tid("flow-run").textContent), "the armed label has to say what a second tap does");

  // A live run, on a FRESH tree so no armed state carries over.
  seed("admin", ADMIN_CAPS, { run: { ...useStore.getState().flows.run, phase: "running" } });
  await mount(createElement(FlowCanvasToolbar as any));

  const stop = tid("flow-run");
  assert(/STOP/.test(stop.textContent), "a live run must offer STOP, not RUN");
  assert(stop.getAttribute("data-armed") == null,
    "STOP is armed - an emergency motion stop must never need a second tap");
  const posts = asked.filter((a) => a.method === "POST").length;
  click(stop);
  await settle();
  const sent = asked.filter((a) => a.method === "POST");
  eq(sent.length, posts + 1, "one tap on STOP must issue exactly one request");
  eq(sent[sent.length - 1].url, "/api/sequence/abort",
    "STOP is not a flows route - a flow run IS a sequence run");
});

// ============================================== 6. the dead control is gone

await testAsync("the design-notes button is gone and nothing writes ui.notesOpen", async () => {
  seed("admin", ADMIN_CAPS);
  // Record every ui patch the toolbar makes, forwarding to the real action so
  // behaviour is unchanged.
  const patches: Record<string, unknown>[] = [];
  const real = useStore.getState().flowsSetUi;
  act(() => {
    useStore.setState({ flowsSetUi: (p: any) => { patches.push(p); real(p); } } as never);
  });
  await mount(createElement(FlowCanvasToolbar as any));

  assert(!/Design notes/i.test(container.innerHTML),
    "the design-notes control came back - it wrote ui.notesOpen, which nothing reads");
  for (const b of all("button")) click(b);
  await settle();

  assert(!patches.some((p) => "notesOpen" in p),
    `something still writes ui.notesOpen: ${JSON.stringify(patches)}`);
  eq(useStore.getState().flows.ui.notesOpen, false, "ui.notesOpen was set by the rebuilt toolbar");
  act(() => { useStore.setState({ flowsSetUi: real } as never); });
});

// ================================================ 7. the phone stage sheet

await testAsync("on a phone the flowStages sheet replaces the canvas, editing and all", async () => {
  viewportW = 390;
  seed("admin", ADMIN_CAPS);
  await mount(createElement(FlowStagesPhoneSheet as any, { params: { open: RECORD.id }, depth: 0 }));

  assert(tid("session-flow-stages") != null, "the phone sheet did not render");
  assert(tid("flows-canvas") == null, "the phone must never render a pannable canvas");
  eq(all('[data-testid="flow-stage-row"]').length, 3, "every stage in the graph has to be listed");
  assert(tid("flow-stages-monitor") != null, "the run monitor readouts are missing");
  assert(/M16 - full-service night/.test(container.textContent), "the sheet is titled with the flow");
  assert(tid("flow-stages-run") != null, "RUN has to work from the phone - that is the whole promise");

  // Tap-to-wire: an OUTPUT arms, and the hint bar is the sticky footer.
  assert(tid("flow-tapwire") == null, "the hint bar must exist only while a wire is armed");
  const outPort = container.querySelector('.nx-flow-port-btn[data-dir="out"]');
  assert(outPort != null, "the stage rows carry no port buttons, so the phone cannot wire");
  click(outPort);
  await settle();
  const bar = tid("flow-tapwire");
  assert(bar != null, "tapping an output did not arm tap-to-wire");
  assert(/tap an input port/.test(bar.textContent),
    `the armed hint says what to do next, got "${bar.textContent}"`);
  assert(!/—/.test(bar.textContent), "the hint's em-dash must be a hyphen");

  click(tid("flow-tapwire-cancel"));
  await settle();
  assert(tid("flow-tapwire") == null, "CANCEL did not disarm the wire");
  viewportW = 820;
});

// ================= 8. the save path, and what RUN is allowed to start

await testAsync("SAVE stores the graph, and the toolbar says whether the rig has it", async () => {
  // THE P0 THIS CLOSES: the canvas had no save at all. `flowsSave` and
  // `flowsCloseEditor` existed and nothing under `next/**` called either, so
  // every edit made here was dropped by BACK, by the FLOWS chip and by a reload.
  viewportW = 820;
  seed("admin", ADMIN_CAPS);
  await mount(createElement(FlowCanvasToolbar as any));

  eq(tid("flow-save-state").textContent, SAVE_STATE_CLEAN,
    "a freshly opened flow has to say its graph is the one the rig holds");
  eq(tid("flow-save").getAttribute("aria-disabled"), "true",
    "SAVE with nothing changed must be honest-disabled, not live and silent");
  eq(tid("flow-save").getAttribute("title"), SAVE_CLEAN_REASON, "and say why");

  act(() => { useStore.getState().flowsAddNode("slew" as never, { x: 10, y: 10 }); });
  await settle();
  eq(tid("flow-save-state").textContent, SAVE_STATE_DIRTY,
    "an edited graph must SAY it is unsaved - a dirty flag nobody renders is not a cue");
  eq(tid("flow-save").getAttribute("aria-disabled"), null, "SAVE has to be live once there is an edit");

  const before = asked.filter((a) => a.method === "PUT").length;
  click(tid("flow-save"));
  await settle();

  const puts = asked.filter((a) => a.method === "PUT");
  eq(puts.length, before + 1, "SAVE issued no PUT - the button is a decoration");
  eq(puts[puts.length - 1].url, "/api/flows/flow-m16", "and it must store THIS flow");
  eq((puts[puts.length - 1].body?.flow?.graph?.nodes ?? []).length, 4,
    "the PUT carried a graph without the new stage in it");
  eq(useStore.getState().flows.dirty, false, "a stored flow is still marked dirty");
  eq(tid("flow-save-state").textContent, SAVE_STATE_CLEAN, "and the cue must go back to SAVED");
});

await testAsync("RUN is refused while the graph on screen is not the graph on the rig", async () => {
  seed("admin", ADMIN_CAPS, { dirty: true });
  await mount(createElement(FlowCanvasToolbar as any));

  const run = tid("flow-run");
  eq(run.getAttribute("aria-disabled"), "true",
    "RUN starts the SAVED flow, so with unsaved edits it would start a graph nobody is looking at");
  assert(run.getAttribute("disabled") == null, "never the native disabled attribute");
  eq(run.getAttribute("title"), RUN_UNSAVED_REASON, "and the refusal has to name the way out");

  const before = asked.filter((a) => a.method === "POST").length;
  click(run);
  await settle();
  eq(asked.filter((a) => a.method === "POST").length, before,
    "a locked RUN still reached the network");
  eq(tid("flow-run").getAttribute("data-armed"), "false", "and it must not even arm");
  assert(useStore.getState().toasts.some((t: any) => String(t.title) === RUN_UNSAVED_REASON),
    "a locked press must SAY the reason rather than swallow the tap");
});

await testAsync("the validation pill grades the DRAFT, and says so, while RUN is locked", async () => {
  // The other half of the same finding: `flowsCompile` posts the DRAFT graph,
  // `POST /api/flows/{id}/run` compiles the SAVED record. A bare GRAPH VALID over
  // an unsaved edit is a green claim about a flow nobody can start.
  seed("admin", ADMIN_CAPS, {
    dirty: true,
    compiled: { plan: {}, structural: [], issues: [], unmapped: [] },
  });
  await mount(createElement(FlowCanvasToolbar as any));

  const pill = tid("flow-checks");
  assert(String(pill.textContent).startsWith(CHECKS_DRAFT_PREFIX),
    `the pill has to say which graph it graded, got "${pill.textContent}"`);
  assert(pill.getAttribute("data-tone") !== "good",
    "a clean DRAFT must not read green - green here has always meant safe to press RUN");

  seed("admin", ADMIN_CAPS, {
    compiled: { plan: {}, structural: [], issues: [], unmapped: [] },
  });
  await mount(createElement(FlowCanvasToolbar as any));
  eq(tid("flow-checks").textContent, "GRAPH VALID",
    "with the graph stored the pill describes exactly what RUN would execute");
  eq(tid("flow-checks").getAttribute("data-tone"), "good", "and it is allowed to be green there");
  eq(tid("flow-run").getAttribute("aria-disabled"), null, "and RUN is live again");
});

await testAsync("an example flow says it cannot be saved rather than offering to", async () => {
  seed("admin", ADMIN_CAPS, {
    dirty: true,
    record: { ...RECORD, readonly: true } as never,
  });
  await mount(createElement(FlowCanvasToolbar as any));

  eq(tid("flow-save-state").textContent, SAVE_STATE_READONLY,
    "an example flow must not claim its edits are stored, nor that they are pending");
  eq(tid("flow-save").getAttribute("title"), SAVE_READONLY_REASON,
    "SAVE has to name the blocker, not the generic nothing-changed sentence");
  const before = asked.filter((a) => a.method === "PUT").length;
  click(tid("flow-save"));
  await settle();
  eq(asked.filter((a) => a.method === "PUT").length, before,
    "a read-only flow was PUT anyway - the server refuses it and the UI must too");
  // And RUN names THAT blocker, not "save it first" - there is no save to make.
  assert(/example flow cannot be saved/.test(String(tid("flow-run").getAttribute("title"))),
    `RUN sent the operator to a SAVE that refuses them, got "${tid("flow-run").getAttribute("title")}"`);
});

// ======================= 9. the phone can finish what it starts

await testAsync("the phone stage list can ADD a stage, at zero stages too", async () => {
  viewportW = 390;
  win.location.hash = "#/session/flows/flowStages?open=flow-m16";
  resetRouterCacheForTests();
  seed("admin", ADMIN_CAPS);
  await mount(createElement(FlowStagesPhoneSheet as any, { params: { open: RECORD.id }, depth: 0 }));

  const add = tid("flow-stages-add");
  assert(add != null,
    "the phone had no way to add a stage at all: the zero-stage state was one sentence "
    + "pointing at a canvas a phone will never draw");
  assert(String(add.textContent).includes(ADD_STAGE_LABEL),
    "and it has to carry the design's own label");

  click(add);
  await settle();
  eq(win.location.hash, "#/session/flows/flowStages/flowPalette?open=flow-m16",
    "ADD STAGE dropped the flow from the URL, so the palette's BACK lands on a stage list "
    + "whose flow nobody named");

  // The zero-stage state is the one that most needs the control.
  win.location.hash = "#/session/flows/flowStages?open=flow-m16";
  resetRouterCacheForTests();
  seed("admin", ADMIN_CAPS, { graph: { nodes: [], edges: [] } });
  await mount(createElement(FlowStagesPhoneSheet as any, { params: { open: RECORD.id }, depth: 0 }));
  assert(tid("flow-stages-empty") != null, "precondition: the empty state is not rendering");
  assert(tid("flow-stages-add") != null,
    "the empty flow is exactly the one with no way to add anything to it");
  assert(!/opens on a tablet or desktop/.test(String(tid("flow-stages-empty").textContent)),
    "the empty state still sends the operator to a screen their phone cannot draw");
});

await testAsync("a wire can be removed from the stage it leaves", async () => {
  viewportW = 390;
  win.location.hash = "#/session/flows/flowStages?open=flow-m16";
  resetRouterCacheForTests();
  seed("admin", ADMIN_CAPS);
  await mount(createElement(FlowStagesPhoneSheet as any, { params: { open: RECORD.id }, depth: 0 }));

  // The legacy phone editor selected the drawn edge and offered a cross ON it.
  // There is no drawn graph here, so the wire is a row under the stage it leaves.
  const row = tid("flow-wire-row-e1");
  assert(row != null,
    "the phone could make a wire and never unwire one: a graph it can break and cannot repair");
  assert(/window/.test(row.textContent) && /TARGET/.test(row.textContent),
    `the row has to name both ends, got "${row.textContent}"`);

  const cut = tid("flow-wire-remove-e1");
  assert(cut != null, "the wire row offers no way to remove the wire");
  assert(/Remove the wire from/.test(String(cut.getAttribute("aria-label"))),
    "four buttons all called REMOVE are four guesses - the name has to say which wire");

  click(cut);
  await settle();
  const left = useStore.getState().flows.graph.edges;
  eq(left.length, 1, "removing one wire must remove exactly one");
  assert(!left.some((e: any) => e.id === "e1"), "the wrong wire was removed");
  eq(useStore.getState().flows.dirty, true,
    "an edit that is not marked dirty is an edit SAVE will skip");
  assert(tid("flow-wire-row-e1") == null, "the removed wire is still listed");

  // The phone can EDIT now, so it owes the same two answers the toolbar does:
  // a SAVE, and a RUN that refuses to start the graph the rig still holds.
  assert(tid("flow-stages-save") != null,
    "a phone that can break a graph and cannot store the repair is worse than a read-only list");
  eq(tid("flow-stages-run").getAttribute("aria-disabled"), "true",
    "RUN would start the version on the rig, which still has the wire that was just cut");
  eq(tid("flow-stages-run").getAttribute("title"), RUN_UNSAVED_REASON, "and it has to say so");
  assert(String(container.textContent).includes(RUN_UNSAVED_REASON),
    "the reason has to be readable without a hover a touch screen cannot perform");
  assert(!/Read-only - This flow has unsaved/.test(String(container.textContent)),
    "an unsaved edit is not a read-only flow, and the two sentences must not be framed alike");
});

await testAsync("the phone's stage editor route keeps the flow in the URL", async () => {
  viewportW = 390;
  win.location.hash = "#/session/flows/flowStages?open=flow-m16";
  resetRouterCacheForTests();
  seed("admin", ADMIN_CAPS);
  await mount(createElement(FlowStagesPhoneSheet as any, { params: { open: RECORD.id }, depth: 0 }));

  const rows = all('[data-testid="flow-stage-row"]');
  assert(rows.length > 0, "precondition: no stage rows to press");
  click(rows[0].querySelector("button"));
  await settle();
  assert(/open=flow-m16/.test(String(win.location.hash)),
    `the stage editor dropped ?open=, so a reload or a share loses the flow: ${win.location.hash}`);
  assert(/node=/.test(String(win.location.hash)),
    "and it still has to name the stage being edited");
});

await testAsync("BACK out of the phone stage list saves before it reloads the library", async () => {
  viewportW = 390;
  win.location.hash = "#/session/flows/flowStages?open=flow-m16";
  resetRouterCacheForTests();
  seed("admin", ADMIN_CAPS);
  await mount(createElement(FlowStagesPhoneSheet as any, { params: { open: RECORD.id }, depth: 0 }));

  act(() => { useStore.getState().flowsAddNode("slew" as never, { x: 1, y: 1 }); });
  await settle();
  eq(useStore.getState().flows.dirty, true, "precondition: the edit was not recorded");

  const from = asked.length;
  click(container.querySelector(".nx-sheet-back"));
  await settle();
  const after = asked.slice(from);
  const put = after.findIndex((a) => a.method === "PUT" && a.url === "/api/flows/flow-m16");
  const list = after.findIndex((a) => a.method === "GET" && a.url === "/api/flows");
  assert(put >= 0,
    `BACK out of the phone editor threw the graph away: ${
      JSON.stringify(after.map((a) => `${a.method} ${a.url}`))}`);
  assert(list >= 0, "the library was never reloaded, so MY FLOWS still shows the pre-edit card");
  assert(put < list, "the reload overtook the save, so the list is drawn from the old flow");
  viewportW = 820;
});

// ============ 10. two window-level handlers that outlive their surface

await testAsync("Delete does not remove a stage while a sheet covers the canvas", async () => {
  viewportW = 820;
  seed("admin", ADMIN_CAPS);
  await mount(createElement(FlowCanvasSurface as any, { tier: "tablet" }));
  act(() => { useStore.getState().flowsSelect({ kind: "node", id: "n2" } as never); });

  const press = (): void => {
    act(() => {
      win.document.body.dispatchEvent(new win.KeyboardEvent("keydown", {
        key: "Delete", bubbles: true, cancelable: true,
      }));
    });
  };

  // The listener is on the WINDOW, so it stayed live under the stage editor -
  // and that sheet covers the canvas, so the stage that vanished was one the
  // operator could not see going.
  win.location.hash = "#/session/flows/flowNode?open=flow-m16&node=n2";
  resetRouterCacheForTests();
  press();
  eq(useStore.getState().flows.graph.nodes.length, 3,
    "a keystroke under an open sheet deleted a stage nobody could see");

  win.location.hash = "#/session/flows?open=flow-m16";
  resetRouterCacheForTests();
  press();
  eq(useStore.getState().flows.graph.nodes.length, 2,
    "Delete on the bare canvas has to still remove the selected stage");
  win.location.hash = "#/session/flows";
  resetRouterCacheForTests();
});

test("an outgoing canvas unmounting does not clear the incoming one's handle", () => {
  // React mounts the next tree before it unmounts the old one, so a cleanup that
  // cleared "whatever is registered" wiped the handle the NEW canvas had already
  // set. `flowCanvasDropPoint` then answered null with a canvas plainly on
  // screen, and the palette dropped every stage on the (120,120) fallback.
  const a = win.document.createElement("div");
  const b = win.document.createElement("div");
  a.getBoundingClientRect = () => ({ width: 800, height: 600, left: 0, top: 0 }) as any;
  b.getBoundingClientRect = () => ({ width: 800, height: 600, left: 0, top: 0 }) as any;

  setMountedFlowCanvas(a);
  setMountedFlowCanvas(b);   // the incoming canvas registers
  clearMountedFlowCanvas(a); // the outgoing one's cleanup runs afterwards
  assert(flowCanvasDropPoint("tablet") != null,
    "the late cleanup cleared the live canvas, so a dropped stage lands on the fallback point");

  clearMountedFlowCanvas(b);
  eq(flowCanvasDropPoint("tablet"), null,
    "with no canvas mounted the drop point has to be null, so the caller owns the fallback");
});

test("the area registers exactly the flowStages sheet", () => {
  eq(Object.keys(flowCanvasSheets).join(","), "flowStages",
    "the cutover composes this registry - an extra or missing name is a wrong screen");
});

// ============ 11. a note is not a defect, and the canvas must not paint one
//
// THE DEFECT, AS SEEN ON THE RIG (2026-09-11). A clean flow put an amber `!` on
// five stage cards, an amber outline on two of them and a PARTLY HONOURED badge
// on each - because every `nodes.<type>` entry the compiler emits was a `warn`,
// and the card knew only "loss or nothing". The entries are notes now: the
// settings are taken from the rig's own, which is a statement, not a finding.
//
// SABOTAGE CHECKS:
//   * let a note through `isLoss` (return true for "note") -> "ten notes leave
//     the canvas with no ! and no amber" goes red on the glyph count AND on the
//     outline attribute.
//   * give a note the warn tone in `markTone` -> the same test goes red on the
//     badge's `data-tone`.
//   * stop rendering the badge for a note -> it goes red on the badge count,
//     because a note that paints NOTHING is the other half of the same bug: the
//     card stops saying where its numbers come from.
//   * drop `rigValueFor` from the card -> "the card names the provider the rig
//     will really use" goes red.

const NOTE_GRAPH = {
  nodes: [
    { id: "n1", type: "dusk", x: 0, y: 0, params: {} },
    { id: "n2", type: "slew", x: 300, y: 0, params: {} },
    { id: "n3", type: "guide", x: 600, y: 0, params: {} },
  ],
  edges: [{ id: "e1", from: "n2", fromPort: "centered", to: "n3", toPort: "run" }],
};

const NOTES = [
  {
    key: "nodes.slew", level: "note",
    detail: "the SLEW node's settings do not reach the run",
    carried: ["presence: the run centres on the target"],
    ignored: ["tolerance 0.5 arcmin", "solver ASTAP"], source: "Settings > Standards",
  },
  {
    key: "nodes.guide", level: "note",
    detail: "the GUIDE node's settings do not reach the run",
    carried: ["presence: the night guides"],
    ignored: ["settle 1.5 s", "dither 3 px", "provider PHD2"], source: "Rig > Guider",
  },
];

/** The resolved per-capability answer `hub.poll_status` attaches to `status`.
 *  Both values DIFFER from the node's stored param, which is the whole point:
 *  the GUIDE node ships "PHD2" and this rig guides natively. */
const RESOLVED = {
  connected: { camera: { connected: true } },
  providers: {
    guide: { kind: "astrodeck", label: "AstroDeck native", reason: "guide camera assigned" },
    solve: { kind: "sim", label: "Simulator", reason: "no ASTAP on this box" },
  },
};

/** `seed` owns `status`, so this puts the resolved providers on top of it. */
function withStatus(status: unknown): void {
  act(() => { useStore.setState({ status } as never); });
}

await testAsync("note-level entries leave the canvas with no ! and no amber", async () => {
  viewportW = 1024;
  seed("admin", ADMIN_CAPS, { graph: NOTE_GRAPH, compiled: { plan: {}, structural: [], issues: [], unmapped: NOTES } });
  await mount(createElement(FlowCanvasSurface as any, { tier: "tablet" }));

  eq(all('[data-testid="flow-node"]').length, 3,
    "precondition: the seeded graph never drew, so every assertion below would pass over nothing");
  eq(all(".nx-flow-node-loss").length, 0,
    "a note still paints the amber ! - a clean flow reads as a failing build");
  eq(all('[data-loss="warn"]').length, 0, "a note still paints the amber card outline");
  eq(all('[data-loss="danger"]').length, 0, "a note is being drawn as a blocking loss");

  const marks = all('[data-testid="flow-node-mark"]');
  eq(marks.length, 2, "the two stages the compile spoke about carry no badge at all, so the "
    + "card stops saying where its numbers come from");
  for (const m of marks) {
    eq(String(m.textContent), MARK_RIG, "the badge is not the note word");
    eq(m.getAttribute("data-tone"), "dim", "the note badge is painted at finding weight");
  }
});

await testAsync("a real loss keeps the ! , the outline and its own word", async () => {
  viewportW = 1024;
  seed("admin", ADMIN_CAPS, {
    graph: NOTE_GRAPH,
    compiled: {
      plan: {}, structural: [], issues: [],
      unmapped: [
        { key: "nodes.slew", level: "warn", detail: "the SLEW node's tolerance does not reach the run" },
        NOTES[1],
      ],
    },
  });
  await mount(createElement(FlowCanvasSurface as any, { tier: "tablet" }));

  eq(all(".nx-flow-node-loss").length, 1, "the loss lost its at-a-glance mark");
  eq(all('[data-loss="warn"]').length, 1, "the loss lost its card outline");
  const marks = all('[data-testid="flow-node-mark"]');
  eq(marks.length, 2, "one of the two marked stages stopped saying anything");
  const words = marks.map((m: any) => String(m.textContent)).sort();
  eq(words.join("|"), [MARK_PARTIAL, MARK_RIG].sort().join("|"),
    "the loss and the note now read as the same thing on the canvas");
  const warnMark = marks.find((m: any) => String(m.textContent) === MARK_PARTIAL);
  eq(warnMark.getAttribute("data-tone"), "warn", "the loss stopped being amber");
});

await testAsync("only notes leaves the checks pill green and saying GRAPH VALID", async () => {
  viewportW = 1024;
  seed("admin", ADMIN_CAPS, {
    graph: NOTE_GRAPH,
    compiled: { plan: {}, structural: [], issues: [], unmapped: NOTES },
  });
  await mount(createElement(FlowCanvasToolbar as any));
  eq(tid("flow-checks").textContent, "GRAPH VALID",
    "a flow whose only findings are notes is a valid graph, and the pill has to say so");
  eq(tid("flow-checks").getAttribute("data-tone"), "good",
    "the verdict pill went off green over settings the rig owns");
});

await testAsync("the verdict pill counts real losses and still ignores notes", async () => {
  // THE DEFECT, SEEN ON THE PROBE (2026-09-11). The pill graded the doctor's
  // `issues` and nothing else, so the example campaign - whose compile says
  // "nothing will bind the dome or close it on an unsafe reading during this
  // run", a DANGER row two inches below on the same screen - read GRAPH VALID
  // in green. Green here has always meant "safe to press RUN".
  //
  // SABOTAGE CHECKS:
  //   * take `losses` back out of `checksWord`/`checksTone` -> the amber and
  //     coral halves of this test go red with GRAPH VALID / good.
  //   * count notes in `lossCount` -> the last two assertions go red.
  viewportW = 1024;
  seed("admin", ADMIN_CAPS, {
    graph: NOTE_GRAPH,
    compiled: {
      plan: {}, structural: [], issues: [],
      unmapped: [
        { key: "nodes.slew", level: "warn", detail: "the SLEW node's tolerance does not reach the run" },
        NOTES[1],
      ],
    },
  });
  await mount(createElement(FlowCanvasToolbar as any));
  eq(tid("flow-checks").textContent, `1 ${MARK_LOST}`,
    "the pill claims a graph is valid while the column beside it lists a loss");
  eq(tid("flow-checks").getAttribute("data-tone"), "warn", "and the verdict stayed green over it");

  // A structural refusal is a danger: a wire that does not resolve is not advice.
  seed("admin", ADMIN_CAPS, {
    graph: NOTE_GRAPH,
    compiled: {
      plan: {}, structural: ["TARGET has no wire into CAPTURE LOOP"], issues: [], unmapped: NOTES,
    },
  });
  await mount(createElement(FlowCanvasToolbar as any));
  eq(tid("flow-checks").textContent, `1 ${MARK_LOST}`, "a structural refusal is not counted");
  eq(tid("flow-checks").getAttribute("data-tone"), "bad",
    "a refusal that stops the run reads the same as one that does not");

  // And the notes alone leave it green - the same fixture, minus the loss.
  seed("admin", ADMIN_CAPS, {
    graph: NOTE_GRAPH,
    compiled: { plan: {}, structural: [], issues: [], unmapped: NOTES },
  });
  await mount(createElement(FlowCanvasToolbar as any));
  eq(tid("flow-checks").textContent, "GRAPH VALID", "two notes were counted as losses");
  eq(tid("flow-checks").getAttribute("data-tone"), "good", "two notes took the verdict off green");
});

await testAsync("the card names the provider the rig will really use", async () => {
  viewportW = 1024;
  seed("admin", ADMIN_CAPS, { graph: NOTE_GRAPH, compiled: { plan: {}, structural: [], issues: [], unmapped: NOTES } });
  withStatus(RESOLVED);
  await mount(createElement(FlowCanvasSurface as any, { tier: "tablet" }));

  const rig = all('[data-testid="flow-node-rig"]').map((el: any) => String(el.textContent));
  eq(rig.length, 2, "neither the GUIDE nor the SLEW card says what the rig will use");
  assert(rig.includes(`${RIG_VALUE_PREFIX}AstroDeck native`),
    `the GUIDE card still shows only its stored PHD2, got ${JSON.stringify(rig)}`);
  assert(rig.includes(`${RIG_VALUE_PREFIX}Simulator`),
    `the SLEW card still shows only its stored ASTAP, got ${JSON.stringify(rig)}`);

  // And nothing is invented when the rig has not answered.
  seed("admin", ADMIN_CAPS, { graph: NOTE_GRAPH, compiled: { plan: {}, structural: [], issues: [], unmapped: NOTES } });
  withStatus({ connected: { camera: { connected: true } } });
  await mount(createElement(FlowCanvasSurface as any, { tier: "tablet" }));
  eq(all('[data-testid="flow-node-rig"]').length, 0,
    "a rig that has published no provider resolution had a value put in its mouth");
});

await testAsync("the phone stage list uses the same words as the canvas", async () => {
  viewportW = 390;
  win.location.hash = "#/session/flows/flowStages?open=flow-m16";
  resetRouterCacheForTests();
  seed("admin", ADMIN_CAPS, { graph: NOTE_GRAPH, compiled: { plan: {}, structural: [], issues: [], unmapped: NOTES } });
  withStatus(RESOLVED);
  await mount(createElement(FlowStagesPhoneSheet as any, { params: { open: RECORD.id }, depth: 0 }));

  const marks = all('[data-testid="flow-stage-mark"]');
  eq(marks.length, 2, "the phone list says nothing about the two stages the compile spoke about");
  for (const m of marks) {
    eq(String(m.textContent), MARK_RIG,
      "the phone list and the canvas disagree about what this stage's badge says");
    eq(m.getAttribute("data-tone"), "dim", "the phone list paints a note at finding weight");
  }
  const rig = all('[data-testid="flow-stage-rig"]').map((el: any) => String(el.textContent));
  assert(rig.includes(`${RIG_VALUE_PREFIX}AstroDeck native`),
    `the phone row never names the rig's own guider, got ${JSON.stringify(rig)}`);
});

act(() => { root.unmount(); });

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`canvasDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
