// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w16FlowCanvasUndo.test.tsx - the #/next canvas's UNDO and REDO: the toolbar
// controls, and Ctrl/Cmd+Z on the surface (#688, part 3 of 3; WP-117). The
// store's history is graded in components/flows/__tests__/w16FlowsUndo.test.ts
// and the classic editor's controls in w16FlowsUndoDom.test.tsx; this file is
// the #/next half.
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/next/hubs/session/flows/canvas/__tests__/w16FlowCanvasUndo.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT IS MOUNTED. The production toolbar and surface against the real store.
// The last case drives a REAL drag through the surface's own pointer handlers,
// so the coalescing the store promises is seen end to end: a drag is a
// `flowsMoveNode` per pointer move, and one Ctrl+Z puts the stage back.
//
// Every guarded case names the mutant it kills and quotes what that mutant
// produced when it was run from a byte-for-byte backup of the file named
// (restored and sha256-compared after each run, the mutant's marker grepped
// absent).

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------------ css stub
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
win.matchMedia = (q: string) => {
  const m = /min-width:\s*(\d+)px/.exec(q);
  return {
    matches: m ? 820 >= Number(m[1]) : false,
    addEventListener() {}, removeEventListener() {},
    addListener() {}, removeListener() {},
  };
};
win.WebSocket = class { close() {} addEventListener() {} send() {} };
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
win.Element.prototype.setPointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.releasePointerCapture = function () { /* jsdom has none */ };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "localStorage", "sessionStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "ResizeObserver", "requestAnimationFrame", "cancelAnimationFrame", "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------- the fake rig
// A save answers at once with what it was sent; everything else answers an
// empty success.
const RECORD = {
  id: "flow-m16", name: "M16 - full-service night", folder: "My flows",
  tagline: "", graph: { nodes: [], edges: [] }, created_ts: 1, updated_ts: 2,
  last_run: null, last_result: "", readonly: false,
};
g.fetch = async (url: string, init?: { method?: string; body?: string }) => {
  const method = init?.method ?? "GET";
  const body = init?.body ? JSON.parse(init.body) : undefined;
  const one = /^\/api\/flows\/([^/]+)$/.exec(url);
  const data = one && method === "PUT"
    ? { ...RECORD, ...(body?.flow ?? {}), id: one[1] }
    : { ok: true };
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
const { FlowCanvasSurface } = await import("../FlowCanvasSurface");
const { FlowCanvasToolbar } = await import("../FlowCanvasToolbar");
const { resetRouterCacheForTests } = await import("../../../../../router");
const { REDO_NOTHING_REASON, UNDO_NOTHING_REASON } =
  await import("../../../../../../components/flows/flowsTypes");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function testAsync(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
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
const click = (el: any): void => {
  act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
};
const ptr = (el: any, type: string, clientX: number, clientY: number): void => {
  act(() => {
    el.dispatchEvent(new win.MouseEvent(type, {
      bubbles: true, cancelable: true, clientX, clientY, button: 0, buttons: 1,
    }));
  });
};
async function key(target: any, init: Record<string, unknown>): Promise<boolean> {
  const ev = new win.KeyboardEvent("keydown", { bubbles: true, cancelable: true, ...init });
  act(() => { target.dispatchEvent(ev); });
  await settle();
  return ev.defaultPrevented;
}
const CTRL_Z = { key: "z", ctrlKey: true };
const store = () => useStore.getState();
const nodeCount = (): number => store().flows.graph.nodes.length;
const toasts = (): string[] => store().toasts.map((t: any) => String(t.title));

// ------------------------------------------------------------------ fixture
const GRAPH = () => ({
  nodes: [
    { id: "n1", type: "dusk", x: 0, y: 0, params: {} },
    { id: "n2", type: "target", x: 300, y: 0, params: {} },
  ],
  edges: [{ id: "e1", from: "n1", fromPort: "window", to: "n2", toPort: "arm" }],
});
const ADMIN_CAPS = [
  "view.status", "view.preview", "control.mount", "control.capture", "view.site_derived",
];

function seed(): void {
  act(() => {
    const s = useStore.getState();
    useStore.setState({
      principal: { role: "admin", email: null, caps: ADMIN_CAPS } as never,
      authGate: "open",
      status: { connected: { camera: { connected: true } } } as never,
      equipConnected: true,
      wsPhase: "up",
      toasts: [],
      sequence: { state: "idle" } as never,
      flows: {
        ...s.flows,
        record: { ...RECORD, graph: GRAPH() } as never,
        graph: GRAPH(),
        dirty: false, saving: false,
        sel: null, editNode: null, wire: null, tapWire: null,
        history: { past: [], future: [], lastCommit: null },
        statuses: {}, logs: [], compiled: null, progress: null, sessionIds: [],
        pan: { x: 0, y: 0 }, zoom: 1,
        run: { ...s.flows.run, phase: "idle", etaS: null, startedAt: null },
        ui: { ...s.flows.ui, notesOpen: false, logOpen: false },
      } as never,
    } as never);
  });
  win.location.hash = "#/session/flows";
  resetRouterCacheForTests();
}
async function mount(node: any): Promise<void> {
  await act(async () => { root.render(createElement("div")); });
  await act(async () => { root.render(node); });
  await settle();
}
const toolbar = () => mount(createElement(FlowCanvasToolbar as any));
const surface = () => mount(createElement(FlowCanvasSurface as any, { tier: "tablet" }));

// ============================================ 1. THE TOOLBAR

// MUTANT "no toolbar controls" (FlowCanvasToolbar.tsx: the UNDO `ActionButton`
// removed). Observed:
//   3 of 8 cases red:
//   x the toolbar carries UNDO and REDO, dimmed with their reasons over a flow with no history: the #/next toolbar has no UNDO and REDO
//     expected true
//     got      false
//   x a press on a dimmed UNDO says why, in a toast, and changes nothing: Cannot read properties of null (reading 'dispatchEvent')
await testAsync("the toolbar carries UNDO and REDO, dimmed with their reasons over a flow with no history", async () => {
  seed();
  await toolbar();
  const undo = tid("flow-undo");
  const redo = tid("flow-redo");
  eq(undo !== null && redo !== null, true, "the #/next toolbar has no UNDO and REDO");
  eq(String(undo.textContent), "UNDO", "the UNDO control is not labelled UNDO");
  eq(String(redo.textContent), "REDO", "the REDO control is not labelled REDO");
  eq(undo.getAttribute("aria-disabled"), "true", "a flow with no history has a live UNDO");
  eq(undo.getAttribute("title"), UNDO_NOTHING_REASON, "the dimmed UNDO does not say why");
  eq(undo.hasAttribute("disabled"), false, "UNDO is natively disabled, which takes its reason with it");
  eq(redo.getAttribute("aria-disabled"), "true", "a flow with no redo has a live REDO");
  eq(redo.getAttribute("title"), REDO_NOTHING_REASON, "the dimmed REDO does not say why");
});

// MUTANT "a dimmed press does nothing" (FlowCanvasToolbar.tsx: UNDO's
// `onExplain={explain}` removed). Observed:
//   1 of 8 cases red:
//   x a press on a dimmed UNDO says why, in a toast, and changes nothing: a blocked UNDO press stated nothing
//     expected ["Nothing to undo. Edits made since this flow was opened can be undone, the last 50 of them."]
//     got      []
await testAsync("a press on a dimmed UNDO says why, in a toast, and changes nothing", async () => {
  seed();
  await toolbar();
  click(tid("flow-undo"));
  eq(JSON.stringify(toasts()), JSON.stringify([UNDO_NOTHING_REASON]), "a blocked UNDO press stated nothing");
  eq(store().flows.dirty, false, "a blocked UNDO press edited the flow");
  click(tid("flow-redo"));
  eq(toasts().includes(REDO_NOTHING_REASON), true, "a blocked REDO press stated nothing");
});

// MUTANT "UNDO wired to redo" (FlowCanvasToolbar.tsx: UNDO's `onPress={undo}`
// made `onPress={redo}`). Observed:
//   1 of 8 cases red:
//   x after an edit UNDO is live and takes it back; then REDO is live and puts it back: the UNDO press did not take the edit back
//     expected 2
//     got      3
await testAsync("after an edit UNDO is live and takes it back; then REDO is live and puts it back", async () => {
  seed();
  await toolbar();
  await act(async () => { store().flowsAddNode("notify", { x: 5, y: 5 }); });
  await settle();
  eq(nodeCount(), 3, "precondition: the add took");
  eq(tid("flow-undo").getAttribute("aria-disabled"), null, "UNDO is dimmed over a flow with an edit to take back");
  eq(tid("flow-redo").getAttribute("aria-disabled"), "true", "REDO is live with nothing undone");
  click(tid("flow-undo"));
  await settle();
  eq(nodeCount(), 2, "the UNDO press did not take the edit back");
  eq(tid("flow-undo").getAttribute("aria-disabled"), "true", "UNDO is live with nothing left to take back");
  eq(tid("flow-redo").getAttribute("aria-disabled"), null, "REDO is dimmed over an undone edit");
  click(tid("flow-redo"));
  await settle();
  eq(nodeCount(), 3, "the REDO press did not put the edit back");
  eq(toasts().length, 0, "a live press said something");
});

// ============================================ 2. THE SURFACE'S KEYS

// MUTANT "the surface ignores the keys" (FlowCanvasSurface.tsx: the
// `if (step && !underSheet) {` block made `if (false) {`). Observed:
//   3 of 8 cases red:
//   x Ctrl+Z on the surface undoes, Ctrl+Shift+Z and Ctrl+Y redo, Cmd works too, and the key is consumed: Ctrl+Z was not consumed, so the browser would...
//     expected true
//     got      false
//   x Ctrl+Z under an open sheet does not rewrite a graph the operator cannot see: Ctrl+Z did not work again once the sheet closed
//     expected 2
//     got      3
// MUTANT "the key is not consumed" (the block's `e.preventDefault();`
// removed). Observed:
//   1 of 8 cases red:
//   x Ctrl+Z on the surface undoes, Ctrl+Shift+Z and Ctrl+Y redo, Cmd works too, and the key is consumed: Ctrl+Z was not consumed, so the browser would...
//     expected true
//     got      false
await testAsync("Ctrl+Z on the surface undoes, Ctrl+Shift+Z and Ctrl+Y redo, Cmd works too, and the key is consumed", async () => {
  seed();
  await surface();
  await act(async () => { store().flowsAddNode("notify", { x: 5, y: 5 }); });
  await settle();
  eq(nodeCount(), 3, "precondition: the add took");
  eq(await key(win.document.body, CTRL_Z), true, "Ctrl+Z was not consumed, so the browser would act on it too");
  eq(nodeCount(), 2, "Ctrl+Z did not undo");
  eq(await key(win.document.body, { key: "Z", ctrlKey: true, shiftKey: true }), true, "Ctrl+Shift+Z was not consumed");
  eq(nodeCount(), 3, "Ctrl+Shift+Z did not redo");
  await key(win.document.body, { key: "z", metaKey: true });
  eq(nodeCount(), 2, "Cmd+Z did not undo");
  await key(win.document.body, { key: "y", ctrlKey: true });
  eq(nodeCount(), 3, "Ctrl+Y did not redo");
});

// MUTANT "typing guard removed" (FlowCanvasSurface.tsx: the `if (tag ===
// "input" || tag === "select" || tag === "textarea") return;` line deleted).
// Observed:
//   1 of 8 cases red:
//   x Ctrl+Z inside an input, a textarea, a select or an editable region stays the browser's: Ctrl+Z in a input was consumed: the browser's text undo w...
//     expected false
//     got      true
// MUTANT "contenteditable guard removed" (the `if (el?.isContentEditable)
// return;` line deleted). Observed:
//   1 of 8 cases red:
//   x Ctrl+Z inside an input, a textarea, a select or an editable region stays the browser's: Ctrl+Z in an editable region was consumed
//     expected false
//     got      true
await testAsync("Ctrl+Z inside an input, a textarea, a select or an editable region stays the browser's", async () => {
  seed();
  await surface();
  await act(async () => { store().flowsAddNode("notify", { x: 5, y: 5 }); });
  await settle();
  for (const tag of ["input", "textarea", "select"]) {
    const el = win.document.createElement(tag);
    win.document.body.appendChild(el);
    eq(await key(el, CTRL_Z), false, `Ctrl+Z in a ${tag} was consumed: the browser's text undo would be lost`);
    eq(nodeCount(), 3, `Ctrl+Z in a ${tag} undid a canvas edit`);
    el.remove();
  }
  const editable = win.document.createElement("div");
  // jsdom does not compute `isContentEditable`, so the property is stood in.
  Object.defineProperty(editable, "isContentEditable", { value: true });
  win.document.body.appendChild(editable);
  eq(await key(editable, CTRL_Z), false, "Ctrl+Z in an editable region was consumed");
  eq(nodeCount(), 3, "Ctrl+Z in an editable region undid a canvas edit");
  editable.remove();
  eq(store().flows.history.past.length, 1, "typing left the history changed");
});

// MUTANT "sheet guard removed" (FlowCanvasSurface.tsx: `&& !underSheet`
// dropped from the history block's condition). Observed:
//   1 of 8 cases red:
//   x Ctrl+Z under an open sheet does not rewrite a graph the operator cannot see: Ctrl+Z under a sheet was consumed
//     expected false
//     got      true
await testAsync("Ctrl+Z under an open sheet does not rewrite a graph the operator cannot see", async () => {
  seed();
  await surface();
  await act(async () => { store().flowsAddNode("notify", { x: 5, y: 5 }); });
  await settle();
  win.location.hash = "#/session/flows/flowNode?open=flow-m16&node=n2";
  resetRouterCacheForTests();
  eq(await key(win.document.body, CTRL_Z), false, "Ctrl+Z under a sheet was consumed");
  eq(nodeCount(), 3, "Ctrl+Z under a sheet undid an edit behind it");
  win.location.hash = "#/session/flows?open=flow-m16";
  resetRouterCacheForTests();
  await key(win.document.body, CTRL_Z);
  eq(nodeCount(), 2, "Ctrl+Z did not work again once the sheet closed");
});

// MUTANT "the listener outlives the surface" (FlowCanvasSurface.tsx: the
// keydown effect's `removeEventListener` removed). Observed:
//   1 of 8 cases red:
//   x a surface that has unmounted no longer answers Ctrl+Z: an unmounted surface's handler undid an edit
//     expected 3
//     got      2
await testAsync("a surface that has unmounted no longer answers Ctrl+Z", async () => {
  seed();
  await surface();
  await act(async () => { store().flowsAddNode("notify", { x: 5, y: 5 }); });
  await settle();
  await act(async () => { root.render(createElement("div")); });
  await settle();
  await key(win.document.body, CTRL_Z);
  eq(nodeCount(), 3, "an unmounted surface's handler undid an edit");
});

// ============================================ 3. A REAL DRAG

// MUTANT "coalescing off" (flowsSlice.ts `commit`'s coalesce test made
// `false`). Observed:
//   1 of 8 cases red:
//   x a drag through the surface's own pointer handlers is ONE entry, and one Ctrl+Z puts the stage back: a 12-step drag made more than one history entry
//     expected 1
//     got      12
await testAsync("a drag through the surface's own pointer handlers is ONE entry, and one Ctrl+Z puts the stage back", async () => {
  seed();
  await surface();
  const head = container.querySelector('[data-node-id="n1"] .nx-flow-node-head');
  if (!head) throw new Error("the stage header is the drag handle and it is missing - the fixture is wrong");
  ptr(head, "pointerdown", 40, 20);
  for (let i = 1; i <= 12; i++) ptr(win, "pointermove", 40 + i * 10, 20 + i * 4);
  ptr(win, "pointerup", 160, 68);
  await settle();
  const moved = store().flows.graph.nodes.find((n) => n.id === "n1")!;
  eq(`${moved.x},${moved.y}`, "120,48", "precondition: the drag moved the stage");
  eq(store().flows.history.past.length, 1, "a 12-step drag made more than one history entry");
  await key(win.document.body, CTRL_Z);
  const back = store().flows.graph.nodes.find((n) => n.id === "n1")!;
  eq(`${back.x},${back.y}`, "0,0", "one Ctrl+Z did not put the dragged stage back where the drag began");
});

await act(async () => { root.unmount(); });

console.log(`\nw16FlowCanvasUndo: ${passed} passed, ${failed} failed`);
if (failures.length) { failures.forEach((f) => console.log(f)); process.exit(1); }
export default { passed, failed, total: passed + failed };
