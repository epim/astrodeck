// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w16PanelCsvOnTheSheet.test.tsx - EXPORT PANELS and IMPORT PANELS reach the
// real Target modal (#178, backlog WP-130; wave 16 integration).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/framing/__tests__/w16PanelCsvOnTheSheet.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE GAP. WP-130 built the panel file (the server's export and import, the
// model's `panelCsvRequest`/`panelCsvExportLock`/`mergeImportedPanels`, and
// GridSection's two buttons) and graded the buttons through a HOST written in
// the test (w16PanelCsv.test.tsx). GridSection takes the feature as an
// optional `panelCsv` prop and its only caller, TargetFramingSheet, did not
// pass it, so no operator saw either button: built, tested, unreachable (the
// route strings sit in GridSection's constants, which is why
// test_routes_have_callers could not see the gap). This file mounts the REAL
// sheet, so the wiring itself is what is graded.
//
// NAMED MUTANTS (each run from a byte backup of TargetFramingSheet.tsx in the
// worktree, restored and sha256-compared; the first failure is quoted):
//   S1 "panelCsv not passed" (the whole `panelCsv={{ ... }}` prop removed from
//     the <GridSection>). Observed, 0/4 passed:
//     x the real sheet draws EXPORT PANELS and IMPORT PANELS inside GRID, with
//       a file input: no EXPORT PANELS on the real sheet: GridSection was not
//       handed `panelCsv`
//   S2 "request not the draft's" (`request: panelCsvRequest(draft)` made
//     `request: null`). Observed, 3/4 passed:
//     x EXPORT PANELS on the sheet posts the draft's layout and saves what the
//       server wrote: EXPORT PANELS posted once: expected 1, got 0
//   S3 "import not merged" (`setDraft((d) => mergeImportedPanels(...))` made
//     `void imported`). Observed, 3/4 passed:
//     x IMPORT PANELS on the sheet merges the file into the draft the sheet
//       shows: the imported grid did not reach the sheet's draft: COLS reads
//       "COLS-fewer cols3+more cols"
//   S4 "lock not passed" (`exportLock: panelCsvExportLock(draft)` made
//     `exportLock: null`). Observed, 3/4 passed:
//     x a draft with no coordinates locks EXPORT PANELS with the reason, and
//       posts nothing: EXPORT PANELS looks live with nothing to export:
//       expected "true", got null

/* eslint-disable @typescript-eslint/no-explicit-any */

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
win.Element.prototype.setPointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.releasePointerCapture = function () { /* jsdom has none */ };
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
win.HTMLCanvasElement.prototype.getContext = () => null;
win.URL.createObjectURL = () => "blob:stub";
win.URL.revokeObjectURL = () => {};
win.fetch = async (url: string) => { throw new Error(`no network in this fixture: ${url}`); };
win.WebSocket = class { close() {} addEventListener() {} send() {} };
const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement", "HTMLSelectElement",
  "HTMLTextAreaElement", "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "FocusEvent", "localStorage", "sessionStorage", "getComputedStyle", "matchMedia", "ResizeObserver",
  "requestAnimationFrame", "cancelAnimationFrame", "Image", "URL", "fetch", "Blob", "File",
  "FileReader", "WebSocket", "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------------- imports
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../store");
const { FLOWS_INIT } = await import("../../flowsSlice");
const { flowsApi } = await import("../../../../lib/flowsApi");
const { NODE_DEFS } = await import("../../nodeDefs");
const { framingApi, framingTiming } = await import("../framingApi");
const { PANEL_CSV_FILENAME, NO_COORDINATES_TO_EXPORT } = await import("../framingModel");
const { panelCsvApi } = await import("../sections/GridSection");
const Sheet = (await import("../TargetFramingSheet")).default;
type FlowNodeRec = import("../../flowsTypes").FlowNodeRec;

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
  finally { unmount(); }
}
function assert(cond: unknown, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (JSON.stringify(got) !== JSON.stringify(want)) {
    throw new Error(`${msg}: expected ${JSON.stringify(want)}, got ${JSON.stringify(got)}`);
  }
}

// ------------------------------------------------------------------ fixtures
const NODE_ID = "n2";
/** A 3 x 2 of 2.0 x 1.33 deg at 25%, rotation 30; M31's public centre. */
function target(over: Record<string, string | number> = {}): FlowNodeRec {
  return {
    id: NODE_ID, type: "target", x: 0, y: 0,
    params: {
      ...NODE_DEFS.target.params, name: "M31", ra: "00h 42m 44s", dec: "+41 16 09",
      rows: 2, cols: 3, overlap: 25, fovX: 2.0, fovY: 1.33, rotation: 30,
      angle: "Rotate to PA", counts: "Accepted subs", frameAnchor: "", skip: "1-2", ...over,
    },
  };
}
const COMPILED = { plan: {}, structural: [], issues: [], unmapped: [] };
/** The server's reading of a file of a 3 x 4, in the node's own shape. */
const IMPORTED = {
  draft: {
    ra: "00h 42m 44.30000s", dec: "+41° 16' 09.0000\"", rows: 3, cols: 4, overlap: 20.0,
    fovX: 2.0, fovY: 1.33, fovFrom: "imported from a panel file", rotation: 330.0, skip: "",
  },
  warnings: ["the file has no unit for width and height"],
  convention: "THE SERVER'S SENTENCE about the angle.",
};

// ------------------------------------------------------------------ the network
(framingApi as any).mosaic = () => new Promise(() => {});
(framingApi as any).compileDraft = async () => COMPILED;
(flowsApi as any).compileDraft = async () => COMPILED;
(flowsApi as any).progress = async () => { throw new Error("not asked in this file"); };
framingTiming.settleMs = 0;
let exportCalls: any[] = [];
let saved: Array<[string, string]> = [];
let importCalls: string[] = [];
(panelCsvApi as any).exportCsv = async (req: any) => {
  exportCalls.push(req);
  return { text: "Pane,RA\n1,2\n", convention: "THE SERVER'S SENTENCE about the angle." };
};
(panelCsvApi as any).save = (name: string, text: string) => { saved.push([name, text]); };
(panelCsvApi as any).importCsv = async (text: string) => { importCalls.push(text); return IMPORTED; };

// ------------------------------------------------------------------ the store
useStore.setState({ flowsFetchTonight: async () => {} } as any);
const IDLE_SEQUENCE = useStore.getState().sequence;

function setup(node: FlowNodeRec): void {
  const graph = { nodes: [node], edges: [] };
  act(() => {
    useStore.setState({
      principal: { role: "operator", email: null,
        caps: ["view.status", "view.preview", "view.site_derived", "control.capture", "control.mount"] },
      wsConnected: false,
      status: { sky_angle: null } as any,
      config: null,
      site: null,
      sequence: IDLE_SEQUENCE,
      flows: {
        ...FLOWS_INIT,
        record: { id: "f1", name: "M31 mosaic", folder: "", tagline: "", graph,
          created_ts: 0, updated_ts: 0, last_run: null, last_result: "", readonly: false },
        graph, compiled: COMPILED, progress: null,
      },
    } as any);
  });
  exportCalls = []; saved = []; importCalls = [];
}

const doc = win.document;
const container = doc.getElementById("root");
const root = createRoot(container);
function mountSheet(): void {
  act(() => { root.render(null); });
  act(() => {
    root.render(createElement(Sheet as any, { nodeId: NODE_ID, onClose: () => {}, viewOnly: false }));
  });
}
function unmount(): void { act(() => { root.render(null); }); }
const q = (id: string) => doc.querySelector(`[data-testid="${id}"]`) as any;
function buttonByText(text: string): any {
  return Array.from(doc.querySelectorAll("button") as any[]).find((b: any) => b.textContent.trim() === text);
}
function click(el: any): void {
  assert(el, "no element to click");
  act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
}
async function flush(ms = 20): Promise<void> {
  await act(async () => { await new Promise((r) => setTimeout(r, ms)); });
}

// ======================================================================= cases

await test("the real sheet draws EXPORT PANELS and IMPORT PANELS inside GRID, with a file input", async () => {
  setup(target());
  mountSheet();
  await flush();
  const grid = q("framing-grid");
  assert(grid, "premise: the sheet drew its GRID section");
  const exp = buttonByText("EXPORT PANELS");
  const imp = buttonByText("IMPORT PANELS");
  assert(exp, "no EXPORT PANELS on the real sheet: GridSection was not handed `panelCsv`");
  assert(imp, "no IMPORT PANELS on the real sheet: GridSection was not handed `panelCsv`");
  assert(grid.contains(exp) && grid.contains(imp), "the two buttons are not in the GRID section");
  assert(q("framing-panel-csv-file"), "no file input for IMPORT PANELS");
});

await test("EXPORT PANELS on the sheet posts the draft's layout and saves what the server wrote", async () => {
  setup(target());
  mountSheet();
  await flush();
  click(buttonByText("EXPORT PANELS"));
  await flush();
  eq(exportCalls.length, 1, "EXPORT PANELS posted once");
  const req = exportCalls[0];
  eq([req.rows, req.cols, req.overlap, req.rotation_deg, req.fov_x_deg, req.fov_y_deg, req.skip],
    [2, 3, 0.25, 30, 2, 1.33, "1-2"], "the request is the draft on screen");
  eq(saved, [[PANEL_CSV_FILENAME, "Pane,RA\n1,2\n"]], "the file saved is the server's text");
  eq(q("framing-panel-csv-convention")?.textContent, "THE SERVER'S SENTENCE about the angle.",
    "the server's convention sentence is shown");
});

await test("IMPORT PANELS on the sheet merges the file into the draft the sheet shows", async () => {
  setup(target());
  mountSheet();
  await flush();
  assert(/3/.test(q("framing-cols").textContent), "premise: the grid starts at 3 columns");
  const input = q("framing-panel-csv-file");
  const file = new win.File(["Pane,RA\n"], "plan.csv", { type: "text/csv" });
  Object.defineProperty(input, "files", { value: [file], configurable: true });
  act(() => { input.dispatchEvent(new win.Event("change", { bubbles: true })); });
  await flush(50);
  eq(importCalls, ["Pane,RA\n"], "the file's text was posted");
  assert(/4/.test(q("framing-cols").textContent) && !/3/.test(q("framing-cols").textContent),
    `the imported grid did not reach the sheet's draft: COLS reads "${q("framing-cols").textContent}"`);
  assert(/3/.test(q("framing-rows").textContent),
    `ROWS reads "${q("framing-rows").textContent}" where the file said 3`);
  eq(q("framing-panel-csv-warning")?.textContent, "the file has no unit for width and height",
    "the server's warning is shown");
});

await test("a draft with no coordinates locks EXPORT PANELS with the reason, and posts nothing", async () => {
  setup(target({ ra: "", dec: "" }));
  mountSheet();
  await flush();
  const exp = buttonByText("EXPORT PANELS");
  assert(exp, "no EXPORT PANELS to press");
  eq(exp.getAttribute("aria-disabled"), "true", "EXPORT PANELS looks live with nothing to export");
  click(exp);
  await flush();
  eq(exportCalls.length, 0, "a locked EXPORT PANELS posted");
  assert(String(doc.body.textContent).includes(NO_COORDINATES_TO_EXPORT),
    "the press did not say why it is locked");
});

// ------------------------------------------------------------------- tally
unmount();
const total = passed + failed;
console.log(`w16PanelCsvOnTheSheet.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) (globalThis as any).process.exitCode = 1;
export const result = { passed, failed, total };
export default result;
