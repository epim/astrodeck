// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w11_5NodeSheetUndo.test.tsx - the phone's nested stage-editor sheet (the
// `flowNode` sheet) carries UNDO and REDO over the flow's one history
// (#1153, WP 11.5).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/next/hubs/session/flows/inspector/__tests__/w11_5NodeSheetUndo.test.tsx   (from ui/)
//
// WHAT IS MOUNTED. The production sheet against the real store, the sheet
// loaded through the registry as the router loads it. An edit is typed into a
// real field row and committed at blur, as a thumb does. The coalescing rule
// is the store's (`commit`, one entry per burst on one field), the same rule
// the desktop inspector's field rows ride; this file pins that the sheet's
// rows ride it too, not a second history.

/* eslint-disable @typescript-eslint/no-explicit-any */

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
win.Element.prototype.setPointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.releasePointerCapture = function () { /* jsdom has none */ };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "FocusEvent", "localStorage", "sessionStorage", "getComputedStyle", "matchMedia",
  "WebSocket", "requestAnimationFrame", "cancelAnimationFrame", "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;
g.fetch = async () => ({
  ok: false, status: 404, statusText: "Not Found",
  headers: { get: () => "application/json" }, json: async () => ({}), text: async () => "",
});

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../../store");
const { NODE_DEFS } = await import("../../../../../../components/flows/nodeDefs");
const { UNDO_NOTHING_REASON, REDO_NOTHING_REASON, FLOW_HISTORY_EMPTY } =
  await import("../../../../../../components/flows/flowsTypes");
const { resetRouterCacheForTests } = await import("../../../../../router");
const { flowInspectorSheets, FlowInspectorColumn } = await import("../index");

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
const settle = async () => {
  for (let i = 0; i < 4; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};
const tid = (t: string): any => container.querySelector(`[data-testid="${t}"]`);
const click = (el: any) => {
  act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
};
const typeInto = (el: any, value: string) => {
  act(() => {
    Object.getOwnPropertyDescriptor(win.HTMLInputElement.prototype, "value")!.set!.call(el, value);
    el.dispatchEvent(new win.Event("input", { bubbles: true }));
  });
};
const blur = (el: any) => {
  act(() => { el.dispatchEvent(new win.Event("focusout", { bubbles: true })); });
};

const CAP = { id: "n-cap", type: "capture" as const, x: 10, y: 20,
  params: { ...NODE_DEFS.capture.params } };
const ORIGINAL_EXPOSURE = CAP.params.exposure;

function seed(): void {
  act(() => {
    const s = useStore.getState();
    useStore.setState({
      authGate: "open",
      wsPhase: "up",
      principal: { role: "admin", email: null, caps: ["control.capture"] } as never,
      status: {} as never,
      flows: {
        ...s.flows,
        record: {
          id: "f1", name: "M31 LRGB", folder: "My flows", tagline: "",
          graph: { nodes: [], edges: [] }, created_ts: 0, updated_ts: 0,
          last_run: null, last_result: "", readonly: false,
        },
        graph: { nodes: [{ ...CAP, params: { ...CAP.params } }], edges: [] },
        sel: null,
        editNode: null,
        compiled: null,
        history: FLOW_HISTORY_EMPTY,
      } as never,
    } as never);
  });
}
const exposure = (): unknown =>
  useStore.getState().flows.graph.nodes.find((n) => n.id === "n-cap")!.params.exposure;

async function openSheet(): Promise<void> {
  seed();
  win.location.hash = "#/session/flows/flowNode";
  resetRouterCacheForTests();
  await act(async () => { useStore.getState().flowsSetEditNode("n-cap"); });
  const Sheet = (await flowInspectorSheets.flowNode.load()).default;
  await act(async () => { root.render(createElement("div")); });
  await act(async () => { root.render(createElement(Sheet as any, { params: {}, depth: 1 })); });
  await settle();
}

async function editExposureTo(v: string): Promise<void> {
  const input = tid("flow-field-exposure").querySelector("input");
  typeInto(input, v);
  blur(input);
  await settle();
}

await testAsync("the stage sheet carries UNDO and REDO, dimmed with their reasons before any edit", async () => {
  await openSheet();
  const undo = tid("flow-node-undo");
  const redo = tid("flow-node-redo");
  eq(undo !== null && redo !== null, true, "the nested stage sheet has no UNDO and REDO");
  eq(String(undo.textContent), "UNDO", "the UNDO control is not labelled UNDO");
  eq(String(redo.textContent), "REDO", "the REDO control is not labelled REDO");
  eq(undo.getAttribute("aria-disabled"), "true", "a flow with no history has a live UNDO");
  eq(undo.getAttribute("title"), UNDO_NOTHING_REASON, "the dimmed UNDO does not say why");
  eq(redo.getAttribute("title"), REDO_NOTHING_REASON, "the dimmed REDO does not say why");
});

await testAsync("a value typed in the sheet is one undo step, and UNDO then REDO restore it", async () => {
  await openSheet();
  await editExposureTo("123");
  eq(exposure(), 123, "precondition: the typed value was stored");
  eq(tid("flow-node-undo").getAttribute("aria-disabled"), null,
    "UNDO is dimmed over an edit made in this sheet");
  click(tid("flow-node-undo"));
  await settle();
  eq(exposure(), ORIGINAL_EXPOSURE, "UNDO in the sheet did not restore the previous value");
  eq(tid("flow-node-redo").getAttribute("aria-disabled"), null,
    "REDO is dimmed over an edit that was undone");
  click(tid("flow-node-redo"));
  await settle();
  eq(exposure(), 123, "REDO in the sheet did not put the edit back");
});

await testAsync("a burst of writes to one field is one undo step, as on the desktop", async () => {
  await openSheet();
  await act(async () => {
    const s = useStore.getState();
    s.flowsSetParam("n-cap", "exposure", "11");
    s.flowsSetParam("n-cap", "exposure", "112");
    s.flowsSetParam("n-cap", "exposure", "1123");
  });
  await settle();
  eq(exposure(), 1123, "precondition: the burst landed");
  click(tid("flow-node-undo"));
  await settle();
  eq(exposure(), ORIGINAL_EXPOSURE, "one UNDO did not take back the whole burst on one field");
  eq(tid("flow-node-undo").getAttribute("aria-disabled"), "true",
    "the burst made more than one history entry");
});

await testAsync("the desktop column does not grow the sheet's row", async () => {
  seed();
  await act(async () => { root.render(createElement("div")); });
  await act(async () => {
    root.render(createElement(FlowInspectorColumn as any, { nodeId: "n-cap" }));
  });
  await settle();
  eq(tid("flow-node-undo"), null, "the 284 px column carries the sheet's UNDO row");
});

await act(async () => { root.unmount(); });

const total = passed + failed;
for (const f of failures) console.error(f);
console.log(`w11_5NodeSheetUndo: ${passed}/${total} passed`);
if (failed) (globalThis as any).process.exitCode = 1;
export default { passed, failed, total };
export { passed, failed, total };
