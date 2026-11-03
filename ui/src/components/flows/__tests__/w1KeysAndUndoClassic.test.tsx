// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w1KeysAndUndoClassic.test.tsx - the classic flow editor's keys and the phone
// FLOW tab's undo (#1124; members #769, #771, #829).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/__tests__/w1KeysAndUndoClassic.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECTS. (1) The canvas bound `f` (fit) and Delete/Backspace with no
// modifier check, so Ctrl+F and Cmd+F fitted the view instead of opening the
// browser's find, and Ctrl+Backspace deleted the selected stage (#769). (2) The
// phone FLOW tab autosaves an edit two seconds later and had no UNDO or REDO
// (#771); the only pair on a phone was on the CANVAS tab.
//
// WHAT IS MOUNTED. The production canvas and the production phone graph against
// the real store, with the slice's `flowsApi` methods replaced and nothing
// else. A key is dispatched at the window's document body and the test reads
// `defaultPrevented`, the one thing the browser consults to decide whether it
// opens its own find.
//
// MUTANT "the modifier check is dropped" (ui/src/lib/keys.ts: the three
// ctrl/meta/alt comparisons in `matches` removed, run from a byte backup
// of the file, restored and sha256-compared after). Observed, 2 of 5 cases red:
//   x Ctrl+F, Cmd+F and Alt+F on the canvas are the browser's, and a bare f still fits: Ctrl+F was swallowed, so the browser's find never opens
//     expected false
//     got      true
//   x Ctrl+Backspace and Ctrl+Delete leave the selected stage, and a bare Delete removes it: Ctrl+Backspace was consumed
//     expected false
//     got      true

/* eslint-disable @typescript-eslint/no-explicit-any */

import { installAutoRaf } from "../../../testing/rafPolyfill";

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

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "PointerEvent", "FocusEvent", "localStorage", "sessionStorage",
  "getComputedStyle", "matchMedia", "WebSocket", "ResizeObserver", "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
installAutoRaf(g);
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------------ imports
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../store");
const { flowsApi } = await import("../../../lib/flowsApi");
const { default: FlowCanvas } = await import("../FlowCanvas");
const { default: FlowPhoneGraph } = await import("../FlowPhoneGraph");
const { REDO_NOTHING_REASON, UNDO_NOTHING_REASON } = await import("../flowsTypes");
const { NODE_DEFS } = await import("../nodeDefs");

// ------------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function testAsync(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) {
    throw new Error(`${msg}\n  expected ${JSON.stringify(want)}\n  got      ${JSON.stringify(got)}`);
  }
}

// ------------------------------------------------------------------ stubs
(flowsApi as any).save = async (id: string, flow: any) => ({ ...flow, id, updated_ts: 2 });
(flowsApi as any).compileDraft = async () => ({ plan: {}, structural: [], issues: [], unmapped: [] });
(flowsApi as any).progress = async () => { throw new Error("Not Found"); };
(flowsApi as any).list = async () => [];
(flowsApi as any).folders = async () => [];

// ------------------------------------------------------------------ fixture
const OPERATOR = {
  role: "operator", email: "op@rig",
  caps: ["view.status", "view.preview", "control.capture", "control.mount", "view.site_derived"],
};
const GRAPH = () => ({
  nodes: [
    { id: "t1", type: "target", x: 40, y: 40, params: { ...NODE_DEFS.target.params } },
    { id: "c1", type: "capture", x: 400, y: 40, params: { ...NODE_DEFS.capture.params } },
  ],
  edges: [],
});
function seed(phoneTab: "flow" | "canvas"): void {
  const flows = useStore.getState().flows;
  act(() => { useStore.setState({
    principal: OPERATOR,
    authGate: "open",
    equipConnected: true,
    wsPhase: "up",
    confirm: null,
    toasts: [],
    status: { connected: { camera: { connected: true, name: "sim" } } },
    sequence: { state: "idle" },
    flows: {
      ...flows,
      record: {
        id: "f1", name: "Key test flow", folder: "My flows", tagline: "",
        graph: GRAPH(), created_ts: 0, updated_ts: 0,
        last_run: null, last_result: "" as const, readonly: false,
      },
      graph: GRAPH(),
      dirty: false, saving: false, sel: null, editNode: null, wire: null, tapWire: null,
      history: { past: [], future: [], lastCommit: null },
      sessionIds: [], logs: [], libraryError: null, compiled: null, progress: null,
      pan: { x: 0, y: 0 }, zoom: 1,
      run: { ...flows.run, phase: "idle", startedAt: null },
      ui: { ...flows.ui, screen: "editor", phoneTab },
    },
  } as never); });
}

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
const tickReal = () => new Promise<void>((r) => setTimeout(r, 0));
const settle = async (): Promise<void> => {
  for (let i = 0; i < 3; i++) await act(async () => { await tickReal(); });
};
async function mount(node: any): Promise<void> {
  await act(async () => { root.render(createElement("div")); });
  await act(async () => { root.render(node); });
  await settle();
}
const label = (id: string): any => container.querySelector(`[data-testid="${id}"]`);
const button = (id: string): any => label(id)?.closest("button") ?? null;
const press = (el: any): void => {
  act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
};
const store = () => useStore.getState();
const nodeCount = (): number => store().flows.graph.nodes.length;
const toasts = (): string[] => store().toasts.map((t: any) => String(t.title));

/** A keydown at `target`, returning whether the page's handler consumed it. */
async function key(target: any, init: Record<string, unknown>): Promise<boolean> {
  const ev = new win.KeyboardEvent("keydown", { bubbles: true, cancelable: true, ...init });
  act(() => { target.dispatchEvent(ev); });
  await settle();
  return ev.defaultPrevented;
}
const select = (id: string): void => {
  act(() => { store().flowsSelect({ kind: "node", id }); });
};

// ============================================ 1. THE CANVAS KEYS (#769)

await testAsync("Ctrl+F, Cmd+F and Alt+F on the canvas are the browser's, and a bare f still fits", async () => {
  seed("canvas");
  await mount(createElement(FlowCanvas as any, { tier: "desktop" }));
  eq(await key(win.document.body, { key: "f", ctrlKey: true }), false,
    "Ctrl+F was swallowed, so the browser's find never opens");
  eq(await key(win.document.body, { key: "F", metaKey: true }), false,
    "Cmd+F was swallowed, so the browser's find never opens");
  eq(await key(win.document.body, { key: "f", altKey: true }), false,
    "Alt+F was swallowed");
  eq(await key(win.document.body, { key: "f" }), true, "a bare f no longer fits the view");
  eq(await key(win.document.body, { key: "F", shiftKey: true }), true, "Shift+F no longer fits the view");
});

await testAsync("Ctrl+Backspace and Ctrl+Delete leave the selected stage, and a bare Delete removes it", async () => {
  seed("canvas");
  await mount(createElement(FlowCanvas as any, { tier: "desktop" }));
  select("c1");
  eq(await key(win.document.body, { key: "Backspace", ctrlKey: true }), false,
    "Ctrl+Backspace was consumed");
  eq(await key(win.document.body, { key: "Delete", ctrlKey: true }), false, "Ctrl+Delete was consumed");
  eq(await key(win.document.body, { key: "Delete", metaKey: true }), false, "Cmd+Delete was consumed");
  eq(nodeCount(), 2, "a modified Delete removed the selected stage");
  eq(await key(win.document.body, { key: "Delete" }), true, "a bare Delete was not consumed");
  eq(nodeCount(), 1, "a bare Delete no longer removes the selected stage");
});

await testAsync("Ctrl+Z still undoes on the canvas", async () => {
  seed("canvas");
  await mount(createElement(FlowCanvas as any, { tier: "desktop" }));
  await act(async () => { store().flowsAddNode("notify", { x: 5, y: 5 }); });
  await settle();
  eq(nodeCount(), 3, "precondition: the add took");
  eq(await key(win.document.body, { key: "z", ctrlKey: true }), true, "Ctrl+Z was not consumed");
  eq(nodeCount(), 2, "Ctrl+Z did not undo");
});

// ============================================ 2. THE PHONE FLOW TAB (#771)

await testAsync("the phone FLOW tab carries UNDO and REDO, dimmed with their reasons over a flow with no history", async () => {
  seed("flow");
  await mount(createElement(FlowPhoneGraph as any));
  const undo = button("flow-undo");
  const redo = button("flow-redo");
  eq(undo !== null && redo !== null, true, "the phone FLOW tab has no UNDO and REDO");
  eq(String(undo.textContent), "UNDO", "the UNDO control is not labelled UNDO");
  eq(String(redo.textContent), "REDO", "the REDO control is not labelled REDO");
  eq(undo.getAttribute("aria-disabled"), "true", "a flow with no history has a live UNDO");
  eq(undo.getAttribute("title"), UNDO_NOTHING_REASON, "the dimmed UNDO does not say why");
  eq(redo.getAttribute("aria-disabled"), "true", "a flow with no redo has a live REDO");
  eq(redo.getAttribute("title"), REDO_NOTHING_REASON, "the dimmed REDO does not say why");
  press(undo);
  eq(toasts().includes(UNDO_NOTHING_REASON), true, "a blocked UNDO press stated nothing");
});

await testAsync("a phone edit then UNDO restores the previous flow, and REDO puts the edit back", async () => {
  seed("flow");
  await mount(createElement(FlowPhoneGraph as any));
  await act(async () => { store().flowsAddNode("notify", { x: 5, y: 5 }); });
  await settle();
  eq(nodeCount(), 3, "precondition: the add took");
  eq(button("flow-undo").getAttribute("aria-disabled"), null, "UNDO is dimmed over an edit to take back");
  press(button("flow-undo"));
  await settle();
  eq(nodeCount(), 2, "the UNDO press did not restore the previous flow");
  eq(button("flow-redo").getAttribute("aria-disabled"), null, "REDO is dimmed over an undone edit");
  press(button("flow-redo"));
  await settle();
  eq(nodeCount(), 3, "the REDO press did not put the edit back");
  eq(toasts().length, 0, "a live press said something");
});

await act(async () => { root.unmount(); });

// ------------------------------------------------------------------- report
const total = passed + failed;
console.log(`w1KeysAndUndoClassic.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) (globalThis as any).process.exitCode = 1;
export default { passed, failed, total };
export { passed, failed, total };
