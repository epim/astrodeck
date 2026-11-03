// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w1KeysAndUndoNext.test.tsx - the #/next flow surface's keys and the phone
// stage sheet's undo (#1124; members #769, #771, #829).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/next/hubs/session/flows/canvas/__tests__/w1KeysAndUndoNext.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECTS. (1) The #/next canvas surface bound `f` (fit) and
// Delete/Backspace with no modifier check, so Ctrl+F and Cmd+F fitted the view
// instead of opening the browser's find, and Ctrl+Backspace deleted the selected
// stage (#769). (2) The phone stage sheet saves an edit two seconds later and
// had no UNDO or REDO (#771): an edit made through its rows or its stage editor
// could not be taken back from that surface.
//
// WHAT IS MOUNTED. The production surface and the production sheet against the
// real store; only the network is a fake. A key is dispatched at the document
// body and the test reads `defaultPrevented`, the one thing the browser
// consults to decide whether it opens its own find.
//
// MUTANT "the modifier check is dropped" (ui/src/lib/keys.ts: the three
// ctrl/meta/alt comparisons in `matches` removed, run from a byte backup
// of the file, restored and sha256-compared after). Observed, 2 of 5 cases red:
//   x Ctrl+F, Cmd+F and Alt+F on the surface are the browser's, and a bare f still fits: Ctrl+F was swallowed, so the browser's find never opens
//     expected false
//     got      true
//   x Ctrl+Backspace and Ctrl+Delete leave the selected stage, and a bare Delete removes it: Ctrl+Backspace was consumed
//     expected false
//     got      true

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
win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
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

// ------------------------------------------------------------------ fixture
const FLOW_ID = "flow-keys";
const GRAPH = () => ({
  nodes: [
    { id: "n1", type: "dusk", x: 0, y: 0, params: {} },
    { id: "n2", type: "target", x: 300, y: 0, params: {} },
  ],
  edges: [],
});
const RECORD = {
  id: FLOW_ID, name: "Key test flow", folder: "My flows", tagline: "", readonly: false,
  created_ts: 1, updated_ts: 2, last_run: null, last_result: "", graph: GRAPH(),
};

// ------------------------------------------------------------- the fake rig
g.fetch = async (url: string, init?: { method?: string; body?: string }) => {
  const method = (init?.method ?? "GET").toUpperCase();
  const body = init?.body ? JSON.parse(init.body) : undefined;
  let data: unknown = { ok: true };
  if (method === "PUT" && url === `/api/flows/${FLOW_ID}`) {
    data = { ...RECORD, ...(body?.flow ?? {}), id: FLOW_ID };
  } else if (url === `/api/flows/${FLOW_ID}`) {
    data = RECORD;
  } else if (url === "/api/flows/compile") {
    data = { plan: {}, structural: [], issues: [], unmapped: [] };
  } else if (/\/progress$/.test(url)) {
    data = { session: null, blocks: [] };
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
const { FlowCanvasSurface } = await import("../FlowCanvasSurface");
const { FlowStagesPhoneSheet } = await import("../FlowStagesPhoneSheet");
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
const settle = async (n = 6): Promise<void> => {
  for (let i = 0; i < n; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};
const tid = (t: string): any => container.querySelector(`[data-testid="${t}"]`);
const click = (el: any): void => {
  act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
};
async function key(target: any, init: Record<string, unknown>): Promise<boolean> {
  const ev = new win.KeyboardEvent("keydown", { bubbles: true, cancelable: true, ...init });
  act(() => { target.dispatchEvent(ev); });
  await settle(4);
  return ev.defaultPrevented;
}
const store = () => useStore.getState();
const nodeCount = (): number => store().flows.graph.nodes.length;
const toasts = (): string[] => store().toasts.map((t: any) => String(t.title));

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
        libraryLoaded: true, libraryError: null,
        pan: { x: 0, y: 0 }, zoom: 1,
        run: { ...s.flows.run, phase: "idle", etaS: null, startedAt: null },
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
const surface = () => mount(createElement(FlowCanvasSurface as any, { tier: "tablet" }));
const sheet = () => mount(
  createElement(FlowStagesPhoneSheet as any, { params: { open: FLOW_ID }, depth: 0 }));
const select = (id: string): void => {
  act(() => { store().flowsSelect({ kind: "node", id } as never); });
};

// ============================================ 1. THE SURFACE KEYS (#769)

await testAsync("Ctrl+F, Cmd+F and Alt+F on the surface are the browser's, and a bare f still fits", async () => {
  seed();
  await surface();
  eq(await key(win.document.body, { key: "f", ctrlKey: true }), false,
    "Ctrl+F was swallowed, so the browser's find never opens");
  eq(await key(win.document.body, { key: "F", metaKey: true }), false,
    "Cmd+F was swallowed, so the browser's find never opens");
  eq(await key(win.document.body, { key: "f", altKey: true }), false, "Alt+F was swallowed");
  eq(await key(win.document.body, { key: "f" }), true, "a bare f no longer fits the view");
  eq(await key(win.document.body, { key: "F", shiftKey: true }), true, "Shift+F no longer fits the view");
});

await testAsync("Ctrl+Backspace and Ctrl+Delete leave the selected stage, and a bare Delete removes it", async () => {
  seed();
  await surface();
  select("n2");
  eq(await key(win.document.body, { key: "Backspace", ctrlKey: true }), false, "Ctrl+Backspace was consumed");
  eq(await key(win.document.body, { key: "Delete", ctrlKey: true }), false, "Ctrl+Delete was consumed");
  eq(await key(win.document.body, { key: "Delete", metaKey: true }), false, "Cmd+Delete was consumed");
  eq(nodeCount(), 2, "a modified Delete removed the selected stage");
  eq(await key(win.document.body, { key: "Delete" }), true, "a bare Delete was not consumed");
  eq(nodeCount(), 1, "a bare Delete no longer removes the selected stage");
});

await testAsync("Ctrl+Z still undoes on the surface", async () => {
  seed();
  await surface();
  await act(async () => { store().flowsAddNode("notify", { x: 5, y: 5 }); });
  await settle();
  eq(nodeCount(), 3, "precondition: the add took");
  eq(await key(win.document.body, { key: "z", ctrlKey: true }), true, "Ctrl+Z was not consumed");
  eq(nodeCount(), 2, "Ctrl+Z did not undo");
});

// ============================================ 2. THE PHONE STAGE SHEET (#771)

await testAsync("the phone stage sheet carries UNDO and REDO, dimmed with their reasons over a flow with no history", async () => {
  seed();
  await sheet();
  const undo = tid("flow-stages-undo");
  const redo = tid("flow-stages-redo");
  eq(undo !== null && redo !== null, true, "the phone stage sheet has no UNDO and REDO");
  eq(String(undo.textContent), "UNDO", "the UNDO control is not labelled UNDO");
  eq(String(redo.textContent), "REDO", "the REDO control is not labelled REDO");
  eq(undo.getAttribute("aria-disabled"), "true", "a flow with no history has a live UNDO");
  eq(undo.getAttribute("title"), UNDO_NOTHING_REASON, "the dimmed UNDO does not say why");
  eq(redo.getAttribute("aria-disabled"), "true", "a flow with no redo has a live REDO");
  eq(redo.getAttribute("title"), REDO_NOTHING_REASON, "the dimmed REDO does not say why");
  click(undo);
  eq(toasts().includes(UNDO_NOTHING_REASON), true, "a blocked UNDO press stated nothing");
});

await testAsync("a phone edit then UNDO restores the previous flow, and REDO puts the edit back", async () => {
  seed();
  await sheet();
  await act(async () => { store().flowsAddNode("notify", { x: 5, y: 5 }); });
  await settle();
  eq(nodeCount(), 3, "precondition: the add took");
  eq(tid("flow-stages-undo").getAttribute("aria-disabled"), null, "UNDO is dimmed over an edit to take back");
  click(tid("flow-stages-undo"));
  await settle();
  eq(nodeCount(), 2, "the UNDO press did not restore the previous flow");
  eq(tid("flow-stages-redo").getAttribute("aria-disabled"), null, "REDO is dimmed over an undone edit");
  click(tid("flow-stages-redo"));
  await settle();
  eq(nodeCount(), 3, "the REDO press did not put the edit back");
  eq(toasts().length, 0, "a live press said something");
});

await act(async () => { root.unmount(); });

// ------------------------------------------------------------------- report
const total = passed + failed;
console.log(`w1KeysAndUndoNext.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) (globalThis as any).process.exitCode = 1;
export default { passed, failed, total };
export { passed, failed, total };
