// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w16FlowsUndoDom.test.tsx - the classic editor's UNDO and REDO controls and
// keys (#688, part 3 of 3; WP-117). The store's history is graded in
// w16FlowsUndo.test.ts; this file is the half a store test cannot see: that
// the controls are on screen where the layout has room for them, that they are
// honest about having nothing to do, and that Ctrl/Cmd+Z reaches the store from
// the canvas and does NOT while the operator is typing.
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/__tests__/w16FlowsUndoDom.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHERE THE CONTROLS ARE. The header carries them at DESKTOP only: the tablet
// row is already at the edge of 700 px with its save pill, and the phone row
// would lose its flow title. Below desktop the canvas carries them over its
// top-right corner, above `+ ADD STAGE`, at the tiers that show that button
// (tablet, and the phone CANVAS tab). The first group of cases pins that
// split, so a later change to either side cannot leave a tier with none.
//
// WHAT IS MOUNTED. The production header and canvas against the real store,
// with the slice's `flowsApi` methods replaced and nothing else; edits go
// through the store's own actions, so the wiring that hands the slice the
// store's subscription is exercised too.
//
// Every guarded case names the mutant it kills and quotes what that mutant
// produced when it was run from a byte-for-byte backup of the file named
// (restored and sha256-compared after each run, the mutant's marker grepped
// absent).

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
const { default: FlowHeader } = await import("../FlowHeader");
const { default: FlowCanvas } = await import("../FlowCanvas");
const {
  REDO_NOTHING_REASON, UNDO_NOTHING_REASON, historyKeyAction,
} = await import("../flowsTypes");
const { NODE_DEFS } = await import("../nodeDefs");

// ------------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function testAsync(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) {
    throw new Error(`${msg}\n  expected ${JSON.stringify(want)}\n  got      ${JSON.stringify(got)}`);
  }
}

// ------------------------------------------------------------------ stubs
// Nothing here reaches the network: a save answers at once with what it was
// sent, a compile answers an empty verdict.
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
function seed(): void {
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
        id: "f1", name: "NGC7331 Preferential Filtering", folder: "My flows", tagline: "",
        graph: GRAPH(), created_ts: 0, updated_ts: 0,
        last_run: null, last_result: "" as const, readonly: false,
      },
      graph: GRAPH(),
      dirty: false, saving: false, sel: null, editNode: null, wire: null, tapWire: null,
      history: { past: [], future: [], lastCommit: null },
      sessionIds: [], logs: [], libraryError: null, compiled: null, progress: null,
      pan: { x: 0, y: 0 }, zoom: 1,
      run: { ...flows.run, phase: "idle", startedAt: null },
      ui: { ...flows.ui, screen: "editor", phoneTab: "canvas" },
    },
  } as never); });
}

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
const tickReal = () => new Promise<void>((r) => setTimeout(r, 0));
const settle = async (): Promise<void> => {
  for (let i = 0; i < 3; i++) await act(async () => { await tickReal(); });
};
type Tier = "phone" | "tablet" | "desktop";
async function mountHeader(tier: Tier): Promise<void> {
  await act(async () => { root.render(createElement("div")); });
  await act(async () => { root.render(createElement(FlowHeader as any, { tier })); });
  await settle();
}
async function mountCanvas(tier: Tier): Promise<void> {
  await act(async () => { root.render(createElement("div")); });
  await act(async () => { root.render(createElement(FlowCanvas as any, { tier })); });
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

/** A keydown at `target`, returning whether the page's handler consumed it.
 *  Settles after, so the frame the canvas asks for in answer to the edit runs
 *  inside an `act` scope rather than between two of them. */
async function key(target: any, init: Record<string, unknown>): Promise<boolean> {
  const ev = new win.KeyboardEvent("keydown", { bubbles: true, cancelable: true, ...init });
  act(() => { target.dispatchEvent(ev); });
  await settle();
  return ev.defaultPrevented;
}
const CTRL_Z = { key: "z", ctrlKey: true };

// ============================================ 1. WHERE THE CONTROLS ARE

// MUTANT "no header controls" (FlowHeader.tsx: `const showHistory = editor &&
// tier === "desktop";` made `false`). Observed:
//   3 of 12 cases red:
//   x the desktop header carries UNDO and REDO, and says why they are dimmed: the desktop header has no UNDO and REDO
//     expected true
//     got      false
//   x a press on a dimmed UNDO says why, in a toast, and changes nothing: Cannot read properties of null (reading 'dispatchEvent')
await testAsync("the desktop header carries UNDO and REDO, and says why they are dimmed", async () => {
  seed();
  await mountHeader("desktop");
  const undo = button("flow-undo");
  const redo = button("flow-redo");
  eq(undo !== null && redo !== null, true, "the desktop header has no UNDO and REDO");
  eq(String(undo.textContent), "UNDO", "the UNDO control is not labelled UNDO");
  eq(String(redo.textContent), "REDO", "the REDO control is not labelled REDO");
  // Honest-disabled: dimmed and aria-disabled, never `disabled`, and the reason
  // is its own title.
  eq(undo.getAttribute("aria-disabled"), "true", "a flow with no history has a live UNDO");
  eq(undo.getAttribute("title"), UNDO_NOTHING_REASON, "the dimmed UNDO does not say why");
  eq(undo.hasAttribute("disabled"), false, "UNDO is natively disabled, which takes its reason with it");
  eq(redo.getAttribute("aria-disabled"), "true", "a flow with no redo has a live REDO");
  eq(redo.getAttribute("title"), REDO_NOTHING_REASON, "the dimmed REDO does not say why");
  eq(UNDO_NOTHING_REASON.startsWith("Nothing to undo"), true, "the reason is not the ruling's sentence");
  eq(REDO_NOTHING_REASON.startsWith("Nothing to redo"), true, "the reason is not the ruling's sentence");
});

// MUTANT "a dimmed press does nothing" (FlowHeader.tsx: UNDO's `onExplain`
// replaced by `() => {}`). Observed:
//   1 of 12 cases red:
//   x a press on a dimmed UNDO says why, in a toast, and changes nothing: a blocked UNDO press stated nothing
//     expected "[\"Nothing to undo. Edits made since this flow was opened can be undone, the last 50 of them.\"]"
//     got      "[]"
await testAsync("a press on a dimmed UNDO says why, in a toast, and changes nothing", async () => {
  seed();
  await mountHeader("desktop");
  press(button("flow-undo"));
  eq(JSON.stringify(toasts()), JSON.stringify([UNDO_NOTHING_REASON]),
    "a blocked UNDO press stated nothing");
  eq(store().flows.dirty, false, "a blocked UNDO press edited the flow");
  press(button("flow-redo"));
  eq(toasts().includes(REDO_NOTHING_REASON), true, "a blocked REDO press stated nothing");
});

// MUTANT "UNDO wired to redo" (FlowHeader.tsx: UNDO's `onClick={undo}` made
// `onClick={redo}`). Observed:
//   1 of 12 cases red:
//   x after an edit UNDO is live and takes it back; then REDO is live and puts it back: the UNDO press did not take the edit back
//     expected 2
//     got      3
// MUTANT "the reasons ignore the history" (FlowHeader.tsx: `undoReason(s.flows)`
// made `UNDO_NOTHING_REASON`). Observed:
//   3 of 12 cases red:
//   x the desktop header carries UNDO and REDO, and says why they are dimmed: the dimmed UNDO does not say why
//     expected "Nothing to undo. Edits made since this flow was opened can be undone, the last 50 of them."
//     got      "Nothing to undo"
//   x a press on a dimmed UNDO says why, in a toast, and changes nothing: a blocked UNDO press stated nothing
//     expected "[\"Nothing to undo. Edits made since this flow was opened can be undone, the last 50 of them.\"]"
//     got      "[\"Nothing to undo\"]"
await testAsync("after an edit UNDO is live and takes it back; then REDO is live and puts it back", async () => {
  seed();
  await mountHeader("desktop");
  await act(async () => { store().flowsAddNode("notify", { x: 5, y: 5 }); });
  await settle();
  eq(nodeCount(), 3, "precondition: the add took");
  eq(button("flow-undo").getAttribute("aria-disabled"), null, "UNDO is dimmed over a flow with an edit to take back");
  eq(button("flow-redo").getAttribute("aria-disabled"), "true", "REDO is live with nothing undone");

  press(button("flow-undo"));
  await settle();
  eq(nodeCount(), 2, "the UNDO press did not take the edit back");
  eq(button("flow-undo").getAttribute("aria-disabled"), "true", "UNDO stays live with nothing left to take back");
  eq(button("flow-redo").getAttribute("aria-disabled"), null, "REDO is dimmed over an undone edit");
  eq(toasts().length, 0, "a live press said something");

  press(button("flow-redo"));
  await settle();
  eq(nodeCount(), 3, "the REDO press did not put the edit back");
});

// MUTANT "the header carries them at every tier" (`tier === "desktop"` dropped
// from `showHistory`). Observed:
//   1 of 12 cases red:
//   x the tablet and phone headers do not (the canvas carries them there): the tablet header grew an UNDO it has no width for
//     expected true
//     got      false
await testAsync("the tablet and phone headers do not (the canvas carries them there)", async () => {
  seed();
  await mountHeader("tablet");
  eq(button("flow-undo") === null, true, "the tablet header grew an UNDO it has no width for");
  await mountHeader("phone");
  eq(button("flow-undo") === null, true, "the phone header grew an UNDO it has no width for");
});

// MUTANT "the library carries them" (`editor &&` dropped from `showHistory`).
// Observed:
//   1 of 12 cases red:
//   x the library header (no flow open) has no UNDO: the library header, with no flow to edit, has an UNDO
//     expected true
//     got      false
await testAsync("the library header (no flow open) has no UNDO", async () => {
  seed();
  act(() => { useStore.setState({ flows: { ...store().flows, ui: { ...store().flows.ui, screen: "library" } } } as never); });
  await mountHeader("desktop");
  eq(button("flow-undo") === null, true, "the library header, with no flow to edit, has an UNDO");
});

// MUTANT "no canvas float" (FlowCanvas.tsx: `{showAddFloat && <FlowHistoryFloat />}`
// made `{false && <FlowHistoryFloat />}`). Observed:
//   2 of 12 cases red:
//   x the canvas carries UNDO and REDO at tablet and on the phone's canvas tab, and not at desktop: the tablet canvas has no UNDO and REDO
//     expected true
//     got      false
//   x the canvas float's UNDO and REDO act, and a dimmed press says why: Cannot read properties of null (reading 'dispatchEvent')
await testAsync("the canvas carries UNDO and REDO at tablet and on the phone's canvas tab, and not at desktop", async () => {
  seed();
  await mountCanvas("tablet");
  eq(button("flow-undo") !== null && button("flow-redo") !== null, true,
    "the tablet canvas has no UNDO and REDO");
  eq(button("flow-undo").getAttribute("aria-disabled"), "true", "the tablet's UNDO is live with nothing to undo");
  eq(button("flow-undo").getAttribute("title"), UNDO_NOTHING_REASON, "the tablet's dimmed UNDO does not say why");

  await mountCanvas("phone");
  eq(button("flow-undo") !== null, true, "the phone's canvas tab has no UNDO");

  act(() => { useStore.setState({ flows: { ...store().flows, ui: { ...store().flows.ui, phoneTab: "flow" } } } as never); });
  await settle();
  eq(button("flow-undo") === null, true, "the phone's FLOW tab has a canvas float it has no add button beside");

  await mountCanvas("desktop");
  eq(button("flow-undo") === null, true, "the desktop canvas carries a second pair: the header already does");
});

// MUTANT "the float's UNDO is inert" (FlowCanvas.tsx: the float's UNDO
// `onClick={undo}` made `onClick={redo}`). Observed:
//   1 of 12 cases red:
//   x the canvas float's UNDO and REDO act, and a dimmed press says why: the canvas's UNDO did not take the edit back
//     expected 2
//     got      3
await testAsync("the canvas float's UNDO and REDO act, and a dimmed press says why", async () => {
  seed();
  act(() => { useStore.setState({ flows: { ...store().flows, ui: { ...store().flows.ui, phoneTab: "canvas" } } } as never); });
  await mountCanvas("tablet");
  press(button("flow-undo"));
  eq(toasts().includes(UNDO_NOTHING_REASON), true, "a blocked press on the canvas's UNDO stated nothing");
  await act(async () => { store().flowsAddNode("notify", { x: 5, y: 5 }); });
  await settle();
  press(button("flow-undo"));
  await settle();
  eq(nodeCount(), 2, "the canvas's UNDO did not take the edit back");
  press(button("flow-redo"));
  await settle();
  eq(nodeCount(), 3, "the canvas's REDO did not put the edit back");
});

// ============================================ 2. THE KEYS

test("the chords: Ctrl or Cmd+Z undo, +Shift redo, Ctrl+Y redo, and nothing else", () => {
  const k = (key: string, o: Record<string, boolean> = {}) =>
    historyKeyAction({ key, ctrlKey: false, metaKey: false, shiftKey: false, altKey: false, ...o });
  eq(k("z", { ctrlKey: true }), "undo", "Ctrl+Z");
  eq(k("z", { metaKey: true }), "undo", "Cmd+Z");
  eq(k("Z", { ctrlKey: true, shiftKey: true }), "redo", "Ctrl+Shift+Z (the key reads upper case)");
  eq(k("Z", { metaKey: true, shiftKey: true }), "redo", "Cmd+Shift+Z");
  eq(k("y", { ctrlKey: true }), "redo", "Ctrl+Y");
  eq(k("y", { metaKey: true }), null, "Cmd+Y is the browser's History on a Mac");
  eq(k("y", { ctrlKey: true, shiftKey: true }), null, "Ctrl+Shift+Y is not redo");
  eq(k("z"), null, "a bare z");
  eq(k("z", { shiftKey: true }), null, "Shift+Z");
  eq(k("z", { ctrlKey: true, altKey: true }), null, "Ctrl+Alt+Z is somebody else's chord");
  eq(k("x", { ctrlKey: true }), null, "Ctrl+X");
});

// MUTANT "the canvas ignores the keys" (FlowCanvas.tsx: the `if (step) {` block
// of the keydown handler made `if (false) {`). Observed:
//   2 of 12 cases red:
//   x Ctrl+Z on the canvas undoes, Ctrl+Shift+Z and Ctrl+Y redo, Cmd works too, and the key is consumed: Ctrl+Z was not consumed, so the browser would ...
//     expected true
//     got      false
//   x Ctrl+Z under a modal does not rewrite a graph the operator cannot see: Ctrl+Z did not work again once the modal closed
//     expected 2
//     got      3
// MUTANT "the key is not consumed" (the block's `e.preventDefault();`
// removed). Observed:
//   1 of 12 cases red:
//   x Ctrl+Z on the canvas undoes, Ctrl+Shift+Z and Ctrl+Y redo, Cmd works too, and the key is consumed: Ctrl+Z was not consumed, so the browser would ...
//     expected true
//     got      false
await testAsync("Ctrl+Z on the canvas undoes, Ctrl+Shift+Z and Ctrl+Y redo, Cmd works too, and the key is consumed", async () => {
  seed();
  await mountCanvas("desktop");
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

// MUTANT "typing guard removed" (FlowCanvas.tsx: the `if (tag === "input" ||
// tag === "select" || tag === "textarea") return;` line deleted). Observed:
//   1 of 12 cases red:
//   x Ctrl+Z inside an input, a textarea or a select stays the browser's: Ctrl+Z in a input was consumed: the browser's text undo would be lost
//     expected false
//     got      true
await testAsync("Ctrl+Z inside an input, a textarea or a select stays the browser's", async () => {
  seed();
  await mountCanvas("desktop");
  await act(async () => { store().flowsAddNode("notify", { x: 5, y: 5 }); });
  await settle();
  for (const tag of ["input", "textarea", "select"]) {
    const el = win.document.createElement(tag);
    win.document.body.appendChild(el);
    eq(await key(el, CTRL_Z), false, `Ctrl+Z in a ${tag} was consumed: the browser's text undo would be lost`);
    eq(nodeCount(), 3, `Ctrl+Z in a ${tag} undid a canvas edit`);
    eq(await key(el, { key: "Z", ctrlKey: true, shiftKey: true }), false, `Ctrl+Shift+Z in a ${tag} was consumed`);
    el.remove();
  }
  eq(store().flows.history.past.length, 1, "typing left the history changed");
});

// MUTANT "modal guard removed" (FlowCanvas.tsx: the `if (document.querySelector(
// '[aria-modal="true"]')) return;` line deleted). Observed:
//   1 of 12 cases red:
//   x Ctrl+Z under a modal does not rewrite a graph the operator cannot see: Ctrl+Z under a modal was consumed
//     expected false
//     got      true
await testAsync("Ctrl+Z under a modal does not rewrite a graph the operator cannot see", async () => {
  seed();
  await mountCanvas("desktop");
  await act(async () => { store().flowsAddNode("notify", { x: 5, y: 5 }); });
  await settle();
  const modal = win.document.createElement("div");
  modal.setAttribute("aria-modal", "true");
  win.document.body.appendChild(modal);
  eq(await key(win.document.body, CTRL_Z), false, "Ctrl+Z under a modal was consumed");
  eq(nodeCount(), 3, "Ctrl+Z under a modal undid an edit behind it");
  modal.remove();
  await key(win.document.body, CTRL_Z);
  eq(nodeCount(), 2, "Ctrl+Z did not work again once the modal closed");
});

// MUTANT "the listener outlives the canvas" (FlowCanvas.tsx: the keydown
// effect's `removeEventListener` removed). Observed:
//   1 of 12 cases red:
//   x a canvas that has unmounted no longer answers Ctrl+Z: an unmounted canvas's handler undid an edit
//     expected 3
//     got      2
await testAsync("a canvas that has unmounted no longer answers Ctrl+Z", async () => {
  seed();
  await mountCanvas("desktop");
  await act(async () => { store().flowsAddNode("notify", { x: 5, y: 5 }); });
  await settle();
  await act(async () => { root.render(createElement("div")); });
  await settle();
  await key(win.document.body, CTRL_Z);
  eq(nodeCount(), 3, "an unmounted canvas's handler undid an edit");
});

await act(async () => { root.unmount(); });

console.log(`\nw16FlowsUndoDom: ${passed} passed, ${failed} failed`);
if (failures.length) { failures.forEach((f) => console.log(f)); process.exit(1); }
export default { passed, failed, total: passed + failed };
