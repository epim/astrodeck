// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// flowInspectorFrame.test.tsx - the classic inspector's half of the Target
// modal's door, and the flow overview's whenWaiting setting (#189 S4; spec
// 2026-09-23 flows mosaic, 2.1 and 1.6).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/__tests__/flowInspectorFrame.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT IS GRADED HERE, with a spy for the door so nothing but the inspector is
// under test (classicFrameHost.test.tsx mounts the real editor and the real
// lazy sheet behind the same row):
//
//   1. A TARGET's per-type slot carries FRAME ON SKY, and the row opens the
//      door for the node the column SHOWS: the selection in the desktop
//      column, `nodeId` (the tablet sheet's `editNode`) when one is passed.
//   2. The plain field rows all stay; the row is not a FieldDef control.
//   3. CONTROL: every other type's inspector renders exactly what it renders
//      with no door at all, and with no door a TARGET gets no row (a button
//      with no modal behind it would do nothing).
//   4. A read-only Example's row reads VIEW ON SKY and says why, in the
//      sheet's own words, copied and held equal here.
//   5. The overview (nothing selected) edits the flow's whenWaiting through
//      flowsSetSetting, keeping every other settings key, under the same
//      label as the modal's RUN section.
//
// Every mutant below was run in a private scratch copy of ui/ (scratchpad
// s4-uhostc-mut), never in the shared tree (#254), and the failure it
// produced is quoted verbatim. After the limit reset every one was run again
// on the finished S4 tree (scratchpad s4-uhostc-r2-mut, 2026-09-27) and failed
// exactly as quoted; those marked "(r2)" were first recorded then.

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
win.fetch = async (url: string) => { throw new Error(`no network in this fixture: ${url}`); };
win.WebSocket = class { close() {} addEventListener() {} send() {} };
const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLSelectElement", "Element", "Node",
  "Event", "CustomEvent", "MouseEvent", "localStorage", "getComputedStyle", "matchMedia",
  "fetch", "WebSocket",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
installAutoRaf(g);
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------------- imports
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../store");
const { FLOWS_INIT } = await import("../flowsSlice");
const { NODE_DEFS, createParams } = await import("../nodeDefs");
const { FLOW_SETTINGS } = await import("../flowsTypes");
const inspectorModule = await import("../FlowInspector");
const FlowInspector = inspectorModule.default;
const {
  FramingDoorContext, FRAME_ON_SKY, VIEW_ON_SKY, EXAMPLE_FRAMING_VIEW_ONLY,
  WHEN_WAITING_OVERVIEW_LABEL, WHEN_WAITING_NO_MOSAIC,
} = inspectorModule;
// The sheet and its RUN section are imported HERE ONLY, to hold the copied
// sentences equal; the inspector itself must never import them (see
// classicFrameHost.test.tsx, which walks the editor's static imports).
const { EXAMPLE_VIEW_ONLY } = await import("../framing/TargetFramingSheet");
const { WHEN_WAITING_LABEL } = await import("../framing/sections/RunSection");
type FlowNodeRec = import("../flowsTypes").FlowNodeRec;
type FlowNodeType = import("../flowsTypes").FlowNodeType;

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
  finally { act(() => { root.render(null); }); }
}
function assert(cond: unknown, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (JSON.stringify(got) !== JSON.stringify(want)) {
    throw new Error(`${msg}: expected ${JSON.stringify(want)}, got ${JSON.stringify(got)}`);
  }
}

const container = win.document.getElementById("root");
const root = createRoot(container);
const doc = win.document;
async function flush(): Promise<void> {
  for (let i = 0; i < 3; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
}
function click(el: any): void {
  assert(el, "no element to click");
  act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
}
function choose(sel: any, value: string): void {
  assert(sel, "no select to choose from");
  const setter = Object.getOwnPropertyDescriptor(win.HTMLSelectElement.prototype, "value")!.set!;
  act(() => { setter.call(sel, value); sel.dispatchEvent(new win.Event("change", { bubbles: true })); });
}

// ------------------------------------------------------------------ fixtures
// The calibration matrix asks the library on mount; answered at once, so a
// calib inspector renders the same on every mount.
useStore.setState({ flowsFetchCalHealth: async () => {} } as any);

const target = (id: string, name: string, over: Record<string, string | number> = {}): FlowNodeRec =>
  ({ id, type: "target", x: 0, y: 0, params: { ...createParams("target"), name, ...over } });

/** Two TARGETs, the first of them FIRST in the graph, so a door opened on
 *  "the first TARGET" and one opened on the node shown differ. */
const ALPHA = target("ta", "Alpha", { rows: 2, cols: 3 });
const BETA = target("tb", "Beta");

function seed(o: {
  nodes: FlowNodeRec[]; sel?: string | null; readonly?: boolean;
  settings?: Record<string, string | number | boolean | null>;
}): void {
  const graph = { nodes: o.nodes, edges: [], ...(o.settings ? { settings: o.settings } : {}) };
  act(() => {
    useStore.setState({
      flows: {
        ...FLOWS_INIT,
        record: { id: "f1", name: "Two targets", folder: "", tagline: "", graph,
          created_ts: 0, updated_ts: 0, last_run: null, last_result: "", readonly: o.readonly ?? false },
        graph,
        sel: o.sel ? { kind: "node", id: o.sel } : null,
      },
    } as any);
  });
}

/** The spy door: every id the inspector asked it to open. */
let opened: string[] = [];
const SPY = { open: (id: string) => { opened.push(id); } };

async function render(props: Record<string, unknown> = {}, door: typeof SPY | null = SPY): Promise<void> {
  act(() => { root.render(null); });
  opened = [];
  const inspector = createElement(FlowInspector as any, props);
  act(() => {
    root.render(door ? createElement(FramingDoorContext.Provider, { value: door }, inspector) : inspector);
  });
  await flush();
}
const frameRow = () => doc.querySelector("[data-flows-frame]") as any;

// ======================================================================
// 1. the row opens the door for the node the column shows

// MUTANT "opens for the selected node's first sibling" (FlowInspector.tsx,
// FrameOnSkyRow's `onClick={() => door.open(id)}` ->
// `door.open(useStore.getState().flows.graph.nodes.find((n) => n.type ===
// "target")?.id ?? id)`, the first TARGET in the flow). Observed, with the
// Example case below red too:
//   x the row opens the door for the node the column shows: the desktop column, Beta selected: expected ["tb"], got ["ta"]
//   x a read-only Example's row reads VIEW ON SKY and says why: an Example's row still opens the door, to view: expected ["tb"], got ["ta"]
// MUTANT "the row opens the selection" (r2; FlowInspector.tsx, FrameOnSkyRow's
// `onClick={() => door.open(id)}` -> the selected node's id whenever a node is
// selected, the shortcut the edit sheet's `nodeId` exists to refuse). The
// desktop half cannot see it, since there the column shows the selection.
// Observed:
//   x the row opens the door for the node the column shows: the edit sheet on Beta while Alpha is selected: expected ["tb"], got ["ta"]
await test("the row opens the door for the node the column shows", async () => {
  seed({ nodes: [ALPHA, BETA], sel: "tb" });
  await render({ variant: "column" });
  assert(frameRow(), "a selected TARGET's inspector has no FRAME ON SKY row");
  eq(frameRow().textContent, FRAME_ON_SKY, "the row's label");
  click(frameRow());
  eq(opened, ["tb"], "the desktop column, Beta selected");
  // The tablet sheet shows `editNode`, which need not be the selection: the
  // row there opens the node named at the top of the sheet.
  seed({ nodes: [ALPHA, BETA], sel: "ta" });
  await render({ variant: "sheet", nodeId: "tb" });
  click(frameRow());
  eq(opened, ["tb"], "the edit sheet on Beta while Alpha is selected");
});

// ======================================================================
// 2. the plain field rows stay

// MUTANT "the row replaces the fields" (FlowInspector.tsx, the field map
// becomes `(node.type === "target" && door ? [] : def.fields).map(...)`, the
// modal as the only way in). Observed:
//   x the plain field rows all stay beside the row: the TARGET's field labels under the row: expected ["Name","RA","Dec","Position angle","Camera angle","Rows","Columns","Overlap","Field width at bin 1","Field height at bin 1","Field from","Skip panels","Passes per visit","Minimum visit","Panel order","Centre within","Centring tries","If a panel will not centre or reach its angle"], got []
await test("the plain field rows all stay beside the row", async () => {
  seed({ nodes: [ALPHA, BETA], sel: "ta" });
  await render({ variant: "column" });
  assert(frameRow(), "precondition: no row");
  // Every label a FlowFieldRow prints, in order: the `.label` span that is
  // its <label>'s first child.
  const labels = Array.from(container.querySelectorAll("label > span.label") as any[])
    .map((s: any) => s.textContent);
  eq(labels, NODE_DEFS.target.fields.map((f) => f.label), "the TARGET's field labels under the row");
  // And the row is no FieldDef: it is a button of the per-type slot, so the
  // vocabulary still holds only the three controls (nodeDefs.test.ts).
  assert(NODE_DEFS.target.fields.every((f) => ["select", "text", "cycleplan"].includes(f.control)),
    "a TARGET field carries a fourth control");
  eq(frameRow().tagName, "BUTTON", "the row's element");
});

// ======================================================================
// 3. CONTROLS: no other type changes, and no door means no row

// MUTANT "the row on every type" (FlowInspector.tsx, the slot's
// `node.type === "target" && door &&` -> `door &&`). Observed:
//   x CONTROL: every other type's inspector is what it is with no door: dusk: the inspector with a door differs from the one without
// MUTANT "non-TARGET inspectors render nothing" (FlowInspector.tsx,
// InspectorNode's `if (!node) return null;` -> `if (!node || node.type !==
// "target") return null;`). Without the precondition below the control
// compared an empty column with an empty column and passed 8/8; with it
// (verifier pass, scratchpad s4-uhostc-verify-mut). Observed:
//   x CONTROL: every other type's inspector is what it is with no door: precondition: dusk's inspector rendered nothing to compare
await test("CONTROL: every other type's inspector is what it is with no door", async () => {
  const types = (Object.keys(NODE_DEFS) as FlowNodeType[]).filter((t) => t !== "target");
  assert(types.length >= 10, `precondition: only ${types.length} other types`);
  for (const t of types) {
    const node: FlowNodeRec = { id: "x", type: t, x: 0, y: 0, params: createParams(t) };
    seed({ nodes: [ALPHA, node], sel: "x" });
    await render({ variant: "column" }, null);
    const bare = container.innerHTML;
    assert(NODE_DEFS[t].desc && container.textContent.includes(NODE_DEFS[t].desc),
      `precondition: ${t}'s inspector rendered nothing to compare`);
    await render({ variant: "column" }, SPY);
    assert(container.innerHTML === bare, `${t}: the inspector with a door differs from the one without`);
    assert(frameRow() === null, `${t}: carries a FRAME ON SKY row`);
  }
});

// MUTANT "a row without a door" (FlowInspector.tsx, the slot's
// `node.type === "target" && door &&` -> `node.type === "target" &&`, with
// `door={door!}`). Observed:
//   x CONTROL: with no editor to host the modal, a TARGET gets no row: a TARGET with no door carries a row that opens nothing
await test("CONTROL: with no editor to host the modal, a TARGET gets no row", async () => {
  seed({ nodes: [ALPHA, BETA], sel: "tb" });
  await render({ variant: "column" }, null);
  assert(frameRow() === null, "a TARGET with no door carries a row that opens nothing");
  assert(container.querySelector("label > span.label"), "precondition: the TARGET's fields did not render");
});

// ======================================================================
// 4. a read-only Example's row says view, and why

// MUTANT "readonly ignored" (FlowInspector.tsx, FrameOnSkyRow's `example`
// selector -> `useStore(() => false)`). Observed (classicFrameHost.test.tsx's
// Example case goes red under it too, with the real sheet behind the row):
//   x a read-only Example's row reads VIEW ON SKY and says why: the Example's row label: expected "VIEW ON SKY", got "FRAME ON SKY"
await test("a read-only Example's row reads VIEW ON SKY and says why", async () => {
  seed({ nodes: [ALPHA, BETA], sel: "tb", readonly: true });
  await render({ variant: "column" });
  eq(frameRow()?.textContent, VIEW_ON_SKY, "the Example's row label");
  const reason = Array.from(container.querySelectorAll("p") as any[])
    .find((p: any) => p.textContent === EXAMPLE_FRAMING_VIEW_ONLY);
  assert(reason, "the Example's row does not say why it opens to view");
  click(frameRow());
  eq(opened, ["tb"], "an Example's row still opens the door, to view");
  // Control: the operator's own flow reads FRAME and gives no reason.
  seed({ nodes: [ALPHA, BETA], sel: "tb" });
  await render({ variant: "column" });
  eq(frameRow()?.textContent, FRAME_ON_SKY, "an editable flow's row label");
  assert(!container.textContent.includes(EXAMPLE_FRAMING_VIEW_ONLY), "an editable flow says it opens to view");
});

// MUTANT "copied sentences drift" (FlowInspector.tsx, `frame your own` ->
// `frame yours` in EXAMPLE_FRAMING_VIEW_ONLY, and `in this flow)` -> `in the
// flow)` in WHEN_WAITING_OVERVIEW_LABEL, each run alone). Observed, in turn:
//   x the copied sentences say what the sheet says: the row's reason vs the sheet's: expected "Example flow: its framing opens to view, not to edit. Duplicate the flow to frame your own.", got "Example flow: its framing opens to view, not to edit. Duplicate the flow to frame yours."
//   x the copied sentences say what the sheet says: the overview's label vs RUN's: expected "While a mosaic waits (for every mosaic in this flow)", got "While a mosaic waits (for every mosaic in the flow)"
await test("the copied sentences say what the sheet says", () => {
  eq(EXAMPLE_FRAMING_VIEW_ONLY, EXAMPLE_VIEW_ONLY, "the row's reason vs the sheet's");
  eq(WHEN_WAITING_OVERVIEW_LABEL, WHEN_WAITING_LABEL, "the overview's label vs RUN's");
});

// ======================================================================
// 5. the overview's whenWaiting

const settingSelect = () => doc.querySelector('select[data-flows-setting="whenWaiting"]') as any;

// MUTANT "setting via flowsSetParam" (FlowInspector.tsx, the select's
// onChange -> `useStore.getState().flowsSetParam(useStore.getState().flows
// .graph.nodes[0].id, "whenWaiting", e.target.value)`, the per-node write).
// Observed:
//   x the overview's select writes whenWaiting through flowsSetSetting and keeps the other keys: the flow's settings after the choice: expected {"whenWaiting":"Wait for the mosaic","laterBuild":"kept"}, got {"whenWaiting":"Shoot later targets, then come back","laterBuild":"kept"}
// MUTANT "overview clobbers other settings" (the verifier's, recorded in r2;
// FlowInspector.tsx, the select's onChange writes `settings: { whenWaiting: v }`
// straight into the store, the value right and every other key gone).
// Observed:
//   x the overview's select writes whenWaiting through flowsSetSetting and keeps the other keys: the flow's settings after the choice: expected {"whenWaiting":"Wait for the mosaic","laterBuild":"kept"}, got {"whenWaiting":"Wait for the mosaic"}
await test("the overview's select writes whenWaiting through flowsSetSetting and keeps the other keys", async () => {
  const shoot = FLOW_SETTINGS.whenWaiting.default;
  const wait = "Wait for the mosaic";
  // `laterBuild` is a setting this build does not know, as a flow saved by a
  // newer build carries: a write that dropped it would change what that
  // build runs.
  seed({ nodes: [ALPHA, BETA], sel: null, settings: { whenWaiting: shoot, laterBuild: "kept" } });
  await render({ variant: "column" });
  const sel = settingSelect();
  assert(sel, "the overview has no whenWaiting select");
  eq(Array.from(sel.options as any[]).map((o: any) => o.value), [...FLOW_SETTINGS.whenWaiting.options],
    "the select's choices");
  eq(sel.value, shoot, "the select shows the stored value");
  const label = sel.closest("label")?.querySelector("span.label")?.textContent;
  eq(label, WHEN_WAITING_OVERVIEW_LABEL, "the select's label");
  const paramsBefore = JSON.stringify(useStore.getState().flows.graph.nodes);
  choose(sel, wait);
  const after = useStore.getState().flows;
  eq(after.graph.settings, { whenWaiting: wait, laterBuild: "kept" }, "the flow's settings after the choice");
  eq(JSON.stringify(after.graph.nodes), paramsBefore, "a node's params after a flow-level choice");
  eq(after.dirty, true, "the flow is dirty after the choice");
  eq(settingSelect().value, wait, "the select after the choice");
});

// MUTANT "the no-mosaic line inverted" (FlowInspector.tsx, `{!hasMosaic &&`
// -> `{hasMosaic &&`). Observed:
//   x the overview says when no block reads the setting: a flow with a 3x2 says it has no mosaic
// MUTANT "any TARGET is a mosaic" (the r2 verifier's, scratchpad
// s4-uhostc-r2-verify-mut; FlowInspector.tsx, `hasMosaic` reads
// `nodes.some((n) => n.type === "target")` in place of `isMultiPanel`).
// Observed:
//   x the overview says when no block reads the setting: a flow of single targets does not say the setting reads nothing
await test("the overview says when no block reads the setting", async () => {
  seed({ nodes: [ALPHA, BETA], sel: null });
  await render({ variant: "column" });
  assert(!container.textContent.includes(WHEN_WAITING_NO_MOSAIC), "a flow with a 3x2 says it has no mosaic");
  // A stored value the build does not offer reads as the default, as the
  // engine runs it (flowSetting). This line holds what the operator sees, not
  // that flowSetting is called: a controlled select whose value matches no
  // option shows its first option, and the default is listed first, so the
  // r2 verifier's mutant "select reads the raw stored value" (the selector
  // made `String(settings?.whenWaiting ?? default)`) passes it, 8/8.
  seed({ nodes: [BETA], sel: null, settings: { whenWaiting: "Idle all night" } });
  await render({ variant: "column" });
  assert(container.textContent.includes(WHEN_WAITING_NO_MOSAIC), "a flow of single targets does not say the setting reads nothing");
  eq(settingSelect()?.value, FLOW_SETTINGS.whenWaiting.default, "an unknown stored value's select");
  // Control: a selected node shows the node, not the flow's setting.
  seed({ nodes: [ALPHA, BETA], sel: "ta" });
  await render({ variant: "column" });
  assert(settingSelect() === null, "a selected node's inspector carries the flow's setting");
});

// ------------------------------------------------------------------- report
act(() => { root.unmount(); });
const total = passed + failed;
console.log(`flowInspectorFrame.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
