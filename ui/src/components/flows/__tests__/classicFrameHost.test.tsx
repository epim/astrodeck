// classicFrameHost.test.tsx - the classic editor HOSTS the Target modal
// (#189 S4 items 1 and 3; spec 2026-09-23 flows mosaic, 2.1; spec Revision 2
// ruling 2 and S4 orchestrator ruling 8 for the counts line).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/__tests__/classicFrameHost.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// The real FlowEditor, the real store and slice, and the real lazy sheet
// behind them (only the network is replaced). What is graded:
//
//   1. THE DOOR. FRAME ON SKY opens the lazy TargetFramingSheet, in Overlay
//      'full', on the node the column shows; in the tablet edit sheet the
//      modal REPLACES the sheet rather than stacking on it.
//   2. THE DROP. A TARGET picked from the palette (the desktop rail, or the
//      tablet sheet whose drop point is the canvas's) opens its modal at once
//      on the id flowsAddNode returned. Any other stage opens nothing.
//   3. VIEW MODE. A read-only Example's row says VIEW ON SKY and why, and the
//      modal it opens is read-only.
//   4. THE NOTICE. countsNotice's line stands above the editor while it is
//      non-null, through any amount of log traffic, a selection and a run,
//      and goes when the counts switch.
//   5. THE CHUNK. No static import from the app's entry, or from the classic
//      flows view, reaches the sheet: it loads when a block is first framed.
//   6. A sheet that cannot load leaves the editor, and its unsaved graph, up.
//   7. A key pressed inside the modal does not reach the canvas behind it:
//      Delete or Backspace there took the block being framed.
//   8. A close the sheet sends from an earlier opening closes nothing: only
//      the modal on screen can close itself (#382).
//   9. The same key guard holds under the tablet's edit sheet and palette
//      sheet, the other two modal Overlays over the canvas (#381).
//  10. RUN MODE IS THE DOOR'S (#189 S5; spec 2.6). While the open flow's
//      session runs (flowRunState `flowRunLive`, over the sessions the slice
//      knows as the flow's since #449), FRAME ON SKY opens the modal with
//      `viewOnly`: read-only, with RUNNING_VIEW_ONLY. A dormant session, or
//      the rig running another flow's session, opens it editable, and the run
//      ending under an open modal makes it editable again. That it holds
//      through a save's re-read is runModeAcrossSave.test.tsx's.
//
// Every mutant below was run in a private scratch copy of ui/ (scratchpad
// s4-uhostc-mut, and s4-uhostc-verify-mut for case 7), never in the shared
// tree (#254), and the failure it produced is quoted verbatim. After the limit
// reset every one was run again on the finished S4 tree (scratchpad
// s4-uhostc-r2-mut, 2026-09-27) and failed exactly as quoted; those marked
// "(r2)" were first recorded then.

/* eslint-disable @typescript-eslint/no-explicit-any */

// @ts-ignore  node built-ins; tsx supplies them at runtime
import { existsSync, readFileSync, statSync } from "node:fs";
// @ts-ignore  node built-ins; tsx supplies them at runtime
import { dirname, join, relative, resolve } from "node:path";
// @ts-ignore  node built-ins; tsx supplies them at runtime
import { fileURLToPath } from "node:url";

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
// Nothing in this file may reach a network: every route the editor or the
// sheet asks is replaced below, and a request that slips past fails loudly.
win.fetch = async (url: string) => { throw new Error(`no network in this fixture: ${url}`); };
win.WebSocket = class { close() {} addEventListener() {} send() {} };
const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement", "HTMLSelectElement",
  "HTMLTextAreaElement", "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "FocusEvent", "PointerEvent", "localStorage", "sessionStorage", "getComputedStyle", "matchMedia",
  "ResizeObserver", "requestAnimationFrame", "cancelAnimationFrame", "Image", "URL", "fetch", "Blob",
  "WebSocket", "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  if (v !== undefined) Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------------- imports
const { createElement, act, lazy } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../store");
const { FLOWS_INIT } = await import("../flowsSlice");
const { flowsApi } = await import("../../../lib/flowsApi");
const { createParams } = await import("../nodeDefs");
const { framingApi, framingTiming } = await import("../framing/framingApi");
const { loadTargetFramingSheet } = await import("../framing");
const { EXAMPLE_VIEW_ONLY, RUNNING_VIEW_ONLY } = await import("../framing/TargetFramingSheet");
const { COUNTS_NOTE, COUNTS_DORMANT_ADDENDUM, acceptCounts } = await import("../countsNotice");
const { FRAME_ON_SKY, VIEW_ON_SKY, EXAMPLE_FRAMING_VIEW_ONLY } = await import("../FlowInspector");
const editorModule = await import("../FlowEditor");
const FlowEditor = editorModule.default;
const { __setFramingSheetForTest, FRAMING_FAILED } = editorModule;
type FlowNodeRec = import("../flowsTypes").FlowNodeRec;
type FlowGraphRec = import("../flowsTypes").FlowGraphRec;

// The sheet's module, loaded once here, so the editor's React.lazy resolves
// on its first render instead of racing tsx's first compile of the module.
await loadTargetFramingSheet();
const RealSheet = (await import("../framing")).TargetFramingSheetLazy;

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

// ------------------------------------------------------------------ network
// The sheet asks the panels route and the draft compile once its draft
// settles; offline (below) it asks neither, but both are answered anyway.
(framingApi as any).mosaic = () => new Promise(() => {});
(framingApi as any).compileDraft = async () => null;
(flowsApi as any).compileDraft = async () => ({ plan: {}, structural: [], issues: [], unmapped: [] });
framingTiming.settleMs = 0;
useStore.setState({
  flowsFetchCalHealth: async () => {},
  flowsFetchTonight: async () => {},
} as any);

// ------------------------------------------------------------------ fixtures
const target = (id: string, name: string, over: Record<string, string | number> = {}): FlowNodeRec =>
  ({ id, type: "target", x: id === "ta" ? 40 : 300, y: 60, params: { ...createParams("target"), name, ...over } });
/** Two named TARGETs, Alpha FIRST in the graph, so a modal opened on "the
 *  first TARGET" and one opened on the node meant show different titles. */
const ALPHA = target("ta", "Alpha", { ra: "00h 42m 44s", dec: "+41 16 09", rows: 2, cols: 3,
  fovX: 2, fovY: 1.33, angle: "Rotate to PA", rotation: 0 });
const BETA = target("tb", "Beta", { ra: "05h 35m 17s", dec: "-05 23 28" });

/** The rig's state before any run: what the store starts with. Every seed
 *  puts it back, so a case that ran a flow cannot leave the next one
 *  running. */
const IDLE_SEQUENCE = useStore.getState().sequence;

// The run-mode case's answers (section 10), READ, NOT COPIED: the progress
// route's recorded answer for a flow with a dormant session, and the rig's
// recorded state of a run writing that very session
// (server/tests/test_s5_recorded_state.py rebuilds both byte for byte).
function readFixture(name: string): any {
  const rel = `../../../../../server/tests/fixtures/${name}`;
  try {
    return JSON.parse(readFileSync(new URL(rel, import.meta.url), "utf8") as string);
  } catch (e) {
    throw new Error(`cannot read ${rel}, a recorded answer the run-mode case is graded against: `
      + `${(e as Error).message}`);
  }
}
const RUN_PROGRESS = readFixture("flow_progress_continue.json").response;
const RUN_STATE = readFixture("sequence_state_mosaic.json").states.shooting;

function seed(o: {
  nodes?: FlowNodeRec[]; sel?: string | null; editNode?: string | null; readonly?: boolean;
  countsNote?: string | null; paletteOpen?: boolean; progress?: any; sequence?: any;
} = {}): void {
  const graph: FlowGraphRec = { nodes: o.nodes ?? [ALPHA, BETA], edges: [] };
  act(() => {
    useStore.setState({
      principal: { role: "operator", email: null, caps: ["view.status", "control.capture"] },
      wsConnected: false,
      status: { sky_angle: null } as any,
      sequence: o.sequence ?? IDLE_SEQUENCE,
      config: null,
      site: null,
      flows: {
        ...FLOWS_INIT,
        record: { id: "f1", name: "Two targets", folder: "", tagline: "", graph,
          created_ts: 0, updated_ts: 0, last_run: null, last_result: "", readonly: o.readonly ?? false },
        graph,
        sel: o.sel ? { kind: "node", id: o.sel } : null,
        editNode: o.editNode ?? null,
        countsNote: o.countsNote ?? null,
        progress: o.progress ?? null,
        // As the slice holds it once an answer has landed: the answer's
        // session noted in the same write (flowsSlice `fetchProgress`), which
        // is what the door reads since #449.
        sessionIds: o.progress?.session?.id ? [o.progress.session.id] : [],
        ui: { ...FLOWS_INIT.ui, screen: "editor", paletteOpen: o.paletteOpen ?? false },
      },
    } as any);
  });
}

const container = win.document.getElementById("root");
const root = createRoot(container);
const doc = win.document;
async function flush(): Promise<void> {
  for (let i = 0; i < 6; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
}
async function mount(tier: "desktop" | "tablet" | "phone"): Promise<void> {
  act(() => { root.render(null); });
  act(() => { root.render(createElement(FlowEditor as any, { tier })); });
  await flush();
}
function unmount(): void {
  act(() => { root.render(null); });
}
function click(el: any): void {
  assert(el, "no element to click");
  act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
}
function buttonByText(text: string): any {
  return Array.from(doc.querySelectorAll("button") as any[]).find((b: any) => b.textContent.trim() === text);
}
const sheet = () => doc.querySelector('[data-testid="target-framing-sheet"]') as any;
const sheetTitle = () => (doc.querySelector(".tfs-title") as any)?.textContent ?? null;
const frameRow = () => doc.querySelector("[data-flows-frame]") as any;
const countsLine = () => doc.querySelector("[data-flows-counts]") as any;

// ======================================================================
// 1. the door

// MUTANT "opens for the selected node's first sibling" (FlowInspector.tsx,
// FrameOnSkyRow's `onClick={() => door.open(id)}` ->
// `door.open(useStore.getState().flows.graph.nodes.find((n) => n.type ===
// "target")?.id ?? id)`). Observed, with the two cases below it red too:
//   x FRAME ON SKY opens the lazy sheet, in Overlay 'full', on the node the column shows: the sheet's title: expected "FRAME Beta", got "FRAME Alpha"
//   x a modal stays with its flow: another flow's block of the same id is not framed: precondition: the sheet's title: expected "FRAME Beta", got "FRAME Alpha"
//   x in the tablet edit sheet, the row's modal replaces the sheet: the sheet's title (the edit sheet's node, not the selection): expected "FRAME Beta", got "FRAME Alpha"
// and, since the verifier pass added case 7:
//   x Delete or Backspace pressed inside the modal leaves the flow's stages alone: the modal's block after the keys: expected "FRAME Beta", got "FRAME Alpha"
await test("FRAME ON SKY opens the lazy sheet, in Overlay 'full', on the node the column shows", async () => {
  seed({ sel: "tb" });
  await mount("desktop");
  assert(sheet() === null, "a sheet is open before anything was pressed");
  eq(frameRow()?.textContent, FRAME_ON_SKY, "the column's row");
  click(frameRow());
  await flush();
  assert(sheet(), "pressing FRAME ON SKY mounted no sheet");
  eq(sheetTitle(), "FRAME Beta", "the sheet's title");
  const surface = sheet().closest(".overlay-surface");
  assert(surface?.classList.contains("overlay-full"),
    `the sheet is not in Overlay 'full': its surface is "${surface?.className}"`);
  // CANCEL closes it and writes nothing.
  const before = JSON.stringify(useStore.getState().flows.graph);
  click(buttonByText("CANCEL"));
  await flush();
  assert(sheet() === null, "CANCEL left the sheet open");
  eq(JSON.stringify(useStore.getState().flows.graph), before, "the graph after CANCEL");
});

// MUTANT "the modal follows a namesake" (FlowEditor.tsx, `const framing =
// frame !== null && frame.recordId === recordId ? frame.nodeId : null;` ->
// `const framing = frame?.nodeId ?? null;`). Observed:
//   x a modal stays with its flow: another flow's block of the same id is not framed: another flow's "tb" is framed: the sheet reads "FRAME Beta"
// Worse than a wrong title: the sheet keys its body by node id, so it kept
// Beta's draft over the other flow's "tb", and its DONE would have written
// Beta's framing onto Gamma.
await test("a modal stays with its flow: another flow's block of the same id is not framed", async () => {
  seed({ sel: "tb" });
  await mount("desktop");
  click(frameRow());
  await flush();
  eq(sheetTitle(), "FRAME Beta", "precondition: the sheet's title");
  // Another flow opens under the modal (ids are per flow: its "tb" is a
  // different block).
  const other: FlowGraphRec = { nodes: [target("tb", "Gamma")], edges: [] };
  act(() => {
    useStore.setState((s: any) => ({
      flows: { ...s.flows, graph: other, record: { ...s.flows.record, id: "f2", name: "Other", graph: other } },
    }));
  });
  await flush();
  assert(sheet() === null, `another flow's "tb" is framed: the sheet reads "${sheetTitle()}"`);
});

// MUTANT "the edit sheet stays" (FlowEditor.tsx, the door's `open` loses
// `setEditNode(null);`, so the modal stacks over the edit sheet). Observed:
//   x in the tablet edit sheet, the row's modal replaces the sheet: the edit sheet is still open under the modal
// MUTANT "the row opens the selection" (r2; FlowInspector.tsx, FrameOnSkyRow
// opens the selected node's id whenever a node is selected). Only the tablet
// door can tell, because only the edit sheet shows a node that need not be
// the selection. Observed:
//   x in the tablet edit sheet, the row's modal replaces the sheet: the sheet's title (the edit sheet's node, not the selection): expected "FRAME Beta", got "FRAME Alpha"
await test("in the tablet edit sheet, the row's modal replaces the sheet", async () => {
  seed({ sel: "ta", editNode: "tb" });
  await mount("tablet");
  assert(doc.querySelector("[data-flows-editsheet]"), "precondition: the edit sheet is not open");
  click(frameRow());
  await flush();
  assert(sheet(), "the edit sheet's row mounted no sheet");
  eq(sheetTitle(), "FRAME Beta", "the sheet's title (the edit sheet's node, not the selection)");
  assert(doc.querySelector("[data-flows-editsheet]") === null, "the edit sheet is still open under the modal");
  eq(useStore.getState().flows.editNode, null, "editNode after the row");
});

// ======================================================================
// 2. the drop

function newIdAfter(press: () => void): string {
  const before = new Set(useStore.getState().flows.graph.nodes.map((n) => n.id));
  press();
  const added = useStore.getState().flows.graph.nodes.filter((n) => !before.has(n.id));
  eq(added.length, 1, "nodes the pick added");
  return added[0].id;
}

// MUTANT "drop does not open" (FlowEditor.tsx, onPick's
// `if (type === "target") door.open(id);` deleted). Observed, with the
// tablet case below red too:
//   x a TARGET picked from the rail opens its modal at once on the id the add returned: the drop mounted no sheet
//   x a TARGET picked from the tablet palette sheet replaces the sheet with its modal: the sheet's pick mounted no framing sheet
// MUTANT "drop opens the first TARGET, not the returned id" (the verifier's,
// recorded in r2; onPick opens `nodes.find((n) => n.type === "target")`, a
// TARGET, just not the one the add made). Observed, with the tablet case red
// too:
//   x a TARGET picked from the rail opens its modal at once on the id the add returned: the dropped block's sheet title: expected "FRAME TARGET", got "FRAME Alpha"
//   x a TARGET picked from the tablet palette sheet replaces the sheet with its modal: the dropped block's sheet title: expected "FRAME TARGET", got "FRAME Alpha"
// MUTANT "drop opens for every type" (the verifier's, recorded in r2; onPick's
// `if (type === "target")` removed). Observed:
//   x a TARGET picked from the rail opens its modal at once on the id the add returned: dropping a CAPTURE opened the framing sheet
await test("a TARGET picked from the rail opens its modal at once on the id the add returned", async () => {
  seed({ sel: null });
  await mount("desktop");
  // The id the pick returns, recorded by wrapping the real action.
  const real = useStore.getState().flowsAddNode;
  const returned: string[] = [];
  act(() => {
    useStore.setState({ flowsAddNode: (t: any, at: any) => { const id = real(t, at); returned.push(id); return id; } } as any);
  });
  try {
    const id = newIdAfter(() => click(doc.querySelector('[data-palette-type="target"]')));
    eq(returned, [id], "the ids flowsAddNode returned");
    await flush();
    assert(sheet(), "the drop mounted no sheet");
    // A created TARGET has no name (createParams), and it is the only
    // nameless block here, so this title names the dropped node.
    eq(sheetTitle(), "FRAME TARGET", "the dropped block's sheet title");
    const dropped = useStore.getState().flows.graph.nodes.find((n) => n.id === id)!;
    eq(dropped.params.name, "", "the dropped block's name");
    click(buttonByText("CANCEL"));
    await flush();
    // Held here, or a CANCEL that closed nothing would read below as the
    // CAPTURE opening a modal.
    assert(sheet() === null, "precondition: CANCEL left the dropped block's sheet open");
    // CONTROL: any other stage is placed by its drop and opens nothing.
    newIdAfter(() => click(doc.querySelector('[data-palette-type="capture"]')));
    await flush();
    assert(sheet() === null, "dropping a CAPTURE opened the framing sheet");
  } finally {
    act(() => { useStore.setState({ flowsAddNode: real } as any); });
  }
});

// The tablet door: the canvas's + ADD STAGE opens the palette SHEET, whose
// pick is the editor's `onPick` too (the drop point the canvas's). Red under
// "drop does not open", quoted above.
await test("a TARGET picked from the tablet palette sheet replaces the sheet with its modal", async () => {
  seed({ sel: null, paletteOpen: true });
  await mount("tablet");
  const press = doc.querySelector('[data-palette-type="target"]');
  assert(press, "precondition: the palette sheet is not open");
  newIdAfter(() => click(press));
  await flush();
  assert(sheet(), "the sheet's pick mounted no framing sheet");
  eq(sheetTitle(), "FRAME TARGET", "the dropped block's sheet title");
  eq(useStore.getState().flows.ui.paletteOpen, false, "the palette sheet after the pick");
});

// ======================================================================
// 3. a read-only Example opens to view

// MUTANT "readonly ignored" (FlowInspector.tsx, FrameOnSkyRow's `example`
// selector -> `useStore(() => false)`). Observed:
//   x a read-only Example's row says why, and its modal opens read-only: the Example's row label: expected "VIEW ON SKY", got "FRAME ON SKY"
// MUTANT "the sheet ignores readonly" (r2; framing/TargetFramingSheet.tsx, the
// sheet's own `example` selector -> `useStore(() => false)`, mutated in the
// scratch copy only). The sheet is not this task's file, but the view mode an
// Example opens in is the sheet's decision, not the row's (the classic host
// passes no `viewOnly`), so this case grades it through the real door rather
// than trusting the row's label. Observed:
//   x a read-only Example's row says why, and its modal opens read-only: the sheet's view mode: expected "true", got null
await test("a read-only Example's row says why, and its modal opens read-only", async () => {
  seed({ sel: "ta", readonly: true });
  await mount("desktop");
  eq(frameRow()?.textContent, VIEW_ON_SKY, "the Example's row label");
  assert(container.textContent.includes(EXAMPLE_FRAMING_VIEW_ONLY), "the Example's row does not say why");
  const before = JSON.stringify(useStore.getState().flows.graph);
  click(frameRow());
  await flush();
  assert(sheet(), "an Example's row mounted no sheet");
  eq(sheet().getAttribute("data-view"), "true", "the sheet's view mode");
  eq((doc.querySelector('[data-testid="framing-view-why"]') as any)?.textContent, EXAMPLE_VIEW_ONLY,
    "the sheet's reason");
  assert(doc.querySelector('[data-testid="framing-done"]') === null, "an Example's sheet offers DONE");
  eq((doc.querySelector(".tfs-fieldset") as any)?.disabled, true, "an Example's controls are live");
  click(buttonByText("CLOSE"));
  await flush();
  assert(sheet() === null, "CLOSE left the Example's sheet open");
  eq(JSON.stringify(useStore.getState().flows.graph), before, "an Example's graph after viewing");
});

// ======================================================================
// 4. the counts line

// MUTANT "notice as a log line" (FlowEditor.tsx, FlowCountsLine appends the
// line to the flow log once, `useEffect(() => { if (line) log(line, "warn");
// }, [line, log])`, and renders null). Observed:
//   x the counts line stands while the flow counts every sub, and goes when the counts switch: a flow counting every sub shows no counts line
// Recorded in r2, each run alone on FlowEditor.tsx's FlowCountsLine (the first
// two are the verifier's):
// MUTANT "counts line hides once a node is selected" (it renders only while
// `flows.sel` is null, as if it were part of the overview). Observed:
//   x the counts line stands while the flow counts every sub, and goes when the counts switch: the counts line after 300 log lines and a selection: expected "This flow counts every sub taken, rejected ones included. New flows count accepted subs only, and saving this flow switches it.", got undefined
// MUTANT "counts line on desktop only" (`{!phone && <FlowCountsLine />}`).
// Observed:
//   x the counts line stands while the flow counts every sub, and goes when the counts switch: the phone editor's counts line: expected "This flow counts every sub taken, rejected ones included. New flows count accepted subs only, and saving this flow switches it.", got undefined
// MUTANT "notice frozen at mount" (the selector becomes `useState(() =>
// countsNotice(...))`, read once when the editor mounts). Observed:
//   x the counts line stands while the flow counts every sub, and goes when the counts switch: the line over a dormant session: expected "This flow counts every sub taken, rejected ones included. New flows count accepted subs only, and saving this flow switches it. Its armed session keeps its count until you CONTINUE.", got "This flow counts every sub taken, rejected ones included. New flows count accepted subs only, and saving this flow switches it."
// MUTANT "notice from the server's note alone" (the line only while
// `flows.countsNote` is set, so the open's answer decides and not the graph on
// screen). Observed:
//   x the counts line stands while the flow counts every sub, and goes when the counts switch: a flow counting every sub shows no counts line
// MUTANT "notice drops the dormant addendum" (`countsNotice(s.flows.graph,
// null)`). Observed:
//   x the counts line stands while the flow counts every sub, and goes when the counts switch: the line over a dormant session: expected "This flow counts every sub taken, rejected ones included. New flows count accepted subs only, and saving this flow switches it. Its armed session keeps its count until you CONTINUE.", got "This flow counts every sub taken, rejected ones included. New flows count accepted subs only, and saving this flow switches it."
// MUTANT "counts line hidden during a run" (the r2 verifier's, scratchpad
// s4-uhostc-r2-verify-mut; FlowEditor.tsx, `<FlowCountsLine />` ->
// `{phase === "idle" && <FlowCountsLine />}`). It passed this case until the
// run step below was added. Observed:
//   x the counts line stands while the flow counts every sub, and goes when the counts switch: the counts line while the flow runs: expected "This flow counts every sub taken, rejected ones included. New flows count accepted subs only, and saving this flow switches it.", got undefined
await test("the counts line stands while the flow counts every sub, and goes when the counts switch", async () => {
  const old = target("tb", "Beta", { counts: "Every sub taken" });
  seed({ nodes: [ALPHA, old], sel: null });
  await mount("desktop");
  assert(countsLine(), "a flow counting every sub shows no counts line");
  eq(countsLine().textContent, COUNTS_NOTE, "the counts line");
  // It is not the log: any amount of log traffic leaves it where it is.
  act(() => {
    for (let i = 0; i < 300; i++) useStore.getState().flowsAppendLog(`frame ${i} saved`, "info");
  });
  act(() => { useStore.getState().flowsSelect({ kind: "node", id: "ta" }); });
  await flush();
  eq(countsLine()?.textContent, COUNTS_NOTE, "the counts line after 300 log lines and a selection");
  // Nor does a run: the line is a fact about the flow, not about an idle
  // editor, and a run is exactly when the rejected subs are being counted.
  act(() => {
    useStore.setState((s: any) => ({ flows: { ...s.flows, run: { ...s.flows.run, phase: "running" } } }));
  });
  await flush();
  eq(countsLine()?.textContent, COUNTS_NOTE, "the counts line while the flow runs");
  // A dormant session adds the server's second sentence.
  act(() => {
    useStore.setState((s: any) => ({ flows: { ...s.flows, countsNote: `${COUNTS_NOTE} ${COUNTS_DORMANT_ADDENDUM}` } }));
  });
  eq(countsLine()?.textContent, `${COUNTS_NOTE} ${COUNTS_DORMANT_ADDENDUM}`, "the line over a dormant session");
  // The save's switch (acceptCounts, as flowsSave writes it) takes it down.
  act(() => {
    useStore.setState((s: any) => ({ flows: { ...s.flows, graph: acceptCounts(s.flows.graph) } }));
  });
  assert(countsLine() === null, "the counts line outlived the switch");
  // Every tier carries it: the phone editor too.
  seed({ nodes: [ALPHA, old], sel: null });
  await mount("phone");
  eq(countsLine()?.textContent, COUNTS_NOTE, "the phone editor's counts line");
  // CONTROL: a flow that counts accepted subs has no line at all.
  seed({ nodes: [ALPHA, BETA], sel: null });
  await mount("desktop");
  assert(countsLine() === null, "a flow counting accepted subs shows a counts line");
});

// ======================================================================
// 5. the chunk

const SRC = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
const rel = (p: string) => relative(SRC, p).split("\\").join("/");

/** The relative modules `file` imports STATICALLY, resolved to files. A type
 *  import (`import type`, `export type`) is erased and a dynamic `import()`
 *  is a separate chunk, so neither is an edge; an `import { type X }` is
 *  taken as an edge, which can only make this walk find more, never less. */
function staticImports(file: string): string[] {
  const src = readFileSync(file, "utf8") as string;
  const specs = [
    ...Array.from(src.matchAll(/^[ \t]*(?:import|export)\s+(?!type\b)[^;]*?\bfrom\s*["']([^"']+)["']/gm)),
    ...Array.from(src.matchAll(/^[ \t]*import\s*["']([^"']+)["']/gm)),
  ].map((m) => m[1]).filter((s) => s.startsWith("."));
  const out: string[] = [];
  for (const s of specs) {
    const base = resolve(dirname(file), s);
    // `./framing` is a directory AND the name of its index, so a candidate
    // counts only when it is a file.
    const hit = [base, `${base}.ts`, `${base}.tsx`, join(base, "index.ts"), join(base, "index.tsx")]
      .find((p) => existsSync(p) && statSync(p).isFile());
    if (!hit) throw new Error(`${rel(file)} imports "${s}", which resolves to no file`);
    out.push(hit);
  }
  return out;
}
function closure(root: string): Set<string> {
  const seen = new Set<string>();
  const todo = [resolve(SRC, root)];
  while (todo.length) {
    const f = todo.pop()!;
    if (seen.has(f)) continue;
    seen.add(f);
    if (/\.(ts|tsx)$/.test(f)) todo.push(...staticImports(f));
  }
  return new Set(Array.from(seen).map(rel));
}

// MUTANT "static sheet import" (FlowEditor.tsx, `import {
// TargetFramingSheetLazy, type TargetFramingSheetProps } from "./framing";`
// -> `import TargetFramingSheetLazy from "./framing/TargetFramingSheet";`
// plus `import type { TargetFramingSheetProps } from "./framing";`).
// Observed:
//   x the sheet is in neither the entry chunk nor the classic flows chunk: the classic flows view statically imports 11 framing modules: components/flows/framing/TargetFramingSheet.tsx, components/flows/framing/framing.css, components/flows/framing/sections/CentringSection.tsx, ...
// MUTANT "label imported from RunSection" (FlowInspector.tsx takes the
// overview's label from the modal, `import { WHEN_WAITING_LABEL } from
// "./framing/sections/RunSection";` and `export const
// WHEN_WAITING_OVERVIEW_LABEL = WHEN_WAITING_LABEL;`, the copy's obvious
// shortcut). Observed:
//   x the sheet is in neither the entry chunk nor the classic flows chunk: the classic flows view statically imports 3 framing modules: components/flows/framing/sections/RunSection.tsx, components/flows/framing/sections/GridSection.tsx, components/flows/framing/framingModel.ts, ...
await test("the sheet is in neither the entry chunk nor the classic flows chunk", () => {
  const flows = closure("components/flows/FlowsView.tsx");
  // The walk is real: it reaches the editor, the inspector and the store.
  for (const f of ["components/flows/FlowEditor.tsx", "components/flows/FlowInspector.tsx", "store.ts"]) {
    assert(flows.has(f), `the walk from FlowsView did not reach ${f}; the walker is broken, not the door`);
  }
  const framing = Array.from(flows).filter((f) =>
    f.startsWith("components/flows/framing/") && f !== "components/flows/framing/index.ts");
  assert(framing.length === 0,
    `the classic flows view statically imports ${framing.length} framing modules: ${framing.slice(0, 3).join(", ")}, ...`);
  // And the editor does reach the sheet, through the one module that loads
  // it dynamically: a door that imported nothing would pass the line above.
  assert(flows.has("components/flows/framing/index.ts"),
    "the classic flows view no longer imports the sheet's lazy door, framing/index.ts");
  const entry = closure("main.tsx");
  assert(entry.size > 50 && entry.has("store.ts"), `the walk from main.tsx reached only ${entry.size} files`);
  const inEntry = Array.from(entry).filter((f) => f.startsWith("components/flows/framing/"));
  eq(inEntry, [], "framing modules in the entry chunk");
});

// ======================================================================
// 6. a sheet that cannot load

// MUTANT "no boundary" (FlowEditor.tsx, the sheet's `<FramingBoundary
// onClose={closeFrame}>` wrapper removed, leaving the Suspense). Observed
// (the chunk's rejection climbed out of the editor and out of act() itself,
// which is what it does to the view in the app):
//   x a sheet that cannot load says so and leaves the editor up: chunk TargetFramingSheet-abc.js 404
await test("a sheet that cannot load says so and leaves the editor up", async () => {
  const quiet = console.error;
  console.error = () => {};
  __setFramingSheetForTest(lazy(() => Promise.reject(new Error("chunk TargetFramingSheet-abc.js 404"))) as any);
  try {
    seed({ sel: "tb" });
    await mount("desktop");
    click(frameRow());
    await flush();
    assert(doc.querySelector("[data-flows-inspector]"), "the editor is gone after the sheet failed");
    const failedBox = doc.querySelector("[data-flows-frame-failed]") as any;
    assert(failedBox, "a sheet that failed to load said nothing");
    assert(failedBox.textContent.includes(FRAMING_FAILED), "the failure does not say what to do");
    assert(failedBox.textContent.includes("chunk TargetFramingSheet-abc.js 404"), "the failure hides its cause");
    click(buttonByText("CLOSE"));
    await flush();
    assert(doc.querySelector("[data-flows-frame-failed]") === null, "CLOSE left the failure open");
  } finally {
    __setFramingSheetForTest(RealSheet);
    console.error = quiet;
  }
});

// ======================================================================
// 7. a key pressed in the modal does not reach the canvas behind it (#381)

// FlowCanvas's Delete/Backspace listener is on the WINDOW, so it stays live
// under the modal, and the modal's sky (a focusable role="application") and
// its buttons are not the text fields its typing guard skips. The row opens
// the SELECTION, so the stage a Backspace on the sky took was the very block
// being framed, gone behind a full-screen modal where nobody saw it go; the
// sheet then read "That stage is no longer in this flow."
//
// MUTANT "the canvas keys act under a modal" (FlowCanvas.tsx, the key
// handler's `if (document.querySelector('[aria-modal="true"]')) return;`
// deleted, which is the code as this task first shipped it). Observed:
//   x Delete or Backspace pressed inside the modal leaves the flow's stages alone: the flow's stages after Backspace on the sky in the modal: expected ["ta","tb"], got ["ta"]
// MUTANT "the canvas keys never act" (the same line made `if (document
// .querySelector('[aria-modal="true"]') || e.key) return;`, a guard that
// swallows every key and would pass the half above). Observed:
//   x Delete or Backspace pressed inside the modal leaves the flow's stages alone: Delete with no modal open: expected ["ta"], got ["ta","tb"]
await test("Delete or Backspace pressed inside the modal leaves the flow's stages alone", async () => {
  seed({ sel: "tb" });
  await mount("desktop");
  click(frameRow());
  await flush();
  const sky = doc.querySelector('[data-testid="target-framing-sheet"] [role="application"]') as any;
  assert(sky, "precondition: the sheet has no sky to press a key on");
  const ids = () => useStore.getState().flows.graph.nodes.map((n) => n.id);
  const press = (el: any, key: string) => {
    act(() => { el.dispatchEvent(new win.KeyboardEvent("keydown", { key, bubbles: true, cancelable: true })); });
  };
  for (const [where, el] of [["the sky", sky], ["CANCEL", buttonByText("CANCEL")]] as const) {
    for (const key of ["Backspace", "Delete"]) {
      press(el, key);
      eq(ids(), ["ta", "tb"], `the flow's stages after ${key} on ${where} in the modal`);
    }
  }
  await flush();
  eq(sheetTitle(), "FRAME Beta", "the modal's block after the keys");
  // CONTROL: with the modal closed the same key on the canvas still deletes
  // the selection, so what holds it back above is the modal, not the key.
  click(buttonByText("CANCEL"));
  await flush();
  assert(sheet() === null, "precondition: CANCEL left the sheet open");
  press(doc.body, "Delete");
  eq(ids(), ["ta"], "Delete with no modal open");
});

// ======================================================================
// 8. a close from an earlier opening closes nothing (#382)

// The shared sheet once called `onClose` a second time, when DONE's compile
// answered after CANCEL (#382). It now refuses CANCEL while DONE writes, and
// #/next's adapter pops only while its own sheet is on top; this is the
// classic host's half of that guard. The host used to hand every opening the
// same `setFrame(null)`, so a late close from the modal on Alpha closed the
// one the operator had since opened on Beta, and one from an earlier opening
// of Beta closed Beta's second. A stand-in sheet keeps each opening's close,
// so the late call is made directly rather than timed against a compile.
//
// MUTANT "the host trusts a stale close" (r2; FlowEditor.tsx, `closeFrame`
// back to `useCallback(() => setFrame(null), [])`, the code before this
// case, on which it was first seen red). Observed:
//   x a close from an earlier opening closes nothing: Alpha's late close closed the modal on Beta: expected "tb", got null
// MUTANT "the close is keyed by node" (r2; FlowEditor.tsx, `closeFrame`
// closes while `cur?.nodeId === frame?.nodeId`, which the Alpha half cannot
// tell apart). Observed:
//   x a close from an earlier opening closes nothing: the first opening's late close closed Beta's second opening: expected "tb", got null
// MUTANT "the close closes nothing" (r2; FlowEditor.tsx, `closeFrame` ->
// `setFrame((cur) => cur)`, a guard that passes the halves above by never
// closing). Observed ("classicFrameHost.test: 5/11 passed"; before the drop
// case held its CANCEL, this mutant read there as "dropping a CAPTURE opened
// the framing sheet"):
//   x FRAME ON SKY opens the lazy sheet, in Overlay 'full', on the node the column shows: CANCEL left the sheet open
//   x a TARGET picked from the rail opens its modal at once on the id the add returned: precondition: CANCEL left the dropped block's sheet open
//   x a read-only Example's row says why, and its modal opens read-only: CLOSE left the Example's sheet open
//   x a sheet that cannot load says so and leaves the editor up: CLOSE left the failure open
//   x Delete or Backspace pressed inside the modal leaves the flow's stages alone: precondition: CANCEL left the sheet open
//   x a close from an earlier opening closes nothing: the open modal's own close left it open
await test("a close from an earlier opening closes nothing", async () => {
  const closes: Record<string, () => void> = {};
  const Standin = (p: { nodeId: string; onClose: () => void }) => {
    closes[p.nodeId] = p.onClose;
    return createElement("div", { "data-standin-sheet": p.nodeId });
  };
  const shown = () => (doc.querySelector("[data-standin-sheet]") as any)?.getAttribute("data-standin-sheet") ?? null;
  const call = async (f: () => void) => { act(() => { f(); }); await flush(); };
  const openOn = async (id: string) => {
    act(() => { useStore.getState().flowsSelect({ kind: "node", id }); });
    await flush();
    click(frameRow());
    await flush();
  };
  __setFramingSheetForTest(Standin as any);
  try {
    seed({ sel: "ta" });
    await mount("desktop");
    await openOn("ta");
    eq(shown(), "ta", "precondition: the stand-in is not framing Alpha");
    const alphaClose = closes.ta;
    // CONTROL: the open modal's own close closes it.
    await call(alphaClose);
    assert(shown() === null, "the open modal's own close left it open");
    await openOn("tb");
    eq(shown(), "tb", "precondition: the stand-in is not framing Beta");
    const betaFirst = closes.tb;
    await call(alphaClose);
    eq(shown(), "tb", "Alpha's late close closed the modal on Beta");
    // The same block opened twice: a close from the first opening is as late.
    await call(betaFirst);
    assert(shown() === null, "precondition: Beta's own close left it open");
    await openOn("tb");
    assert(closes.tb !== betaFirst, "precondition: Beta's second opening kept the first opening's close");
    await call(betaFirst);
    eq(shown(), "tb", "the first opening's late close closed Beta's second opening");
    await call(closes.tb);
    assert(shown() === null, "Beta's second opening's own close left it open");
  } finally {
    __setFramingSheetForTest(RealSheet);
  }
});

// ======================================================================
// 9. the same guard holds under the tablet's own sheets (#381)

// #381 names them: the edit sheet and the palette sheet are modal Overlays
// over the same canvas, and before S4 a Delete or Backspace on one of their
// buttons deleted the canvas's selection out of sight. The edit sheet need
// not show the selection (here it shows Beta while Alpha is selected), so the
// stage that went was not even the one on screen. Case 7 presses keys in the
// Target modal only, so a guard narrowed to that modal passed it.
//
// Added by the r2 verifier (scratchpad s4-uhostc-r2-verify-mut), each mutant
// run alone in that private copy.
//
// MUTANT "the guard knows only the Target modal" (FlowCanvas.tsx, the key
// handler's `[aria-modal="true"]` -> `[data-testid="target-framing-sheet"]`).
// Case 7 passes under it, and so did the whole file (11/11) before this case.
// Observed:
//   x Delete or Backspace under the tablet's edit sheet or palette sheet leaves the flow's stages alone: the flow's stages after Backspace on a button in the edit sheet: expected ["ta","tb"], got ["tb"]
// MUTANT "the guard spares the palette sheet" (the same selector made
// `[aria-modal="true"]:not([aria-label="Add stage"])`, so the edit-sheet half
// above passes and only the palette half can see it). Observed:
//   x Delete or Backspace under the tablet's edit sheet or palette sheet leaves the flow's stages alone: the flow's stages after Backspace on a button in the palette sheet: expected ["ta","tb"], got ["tb"]
// Case 7's two mutants are red here too: "the canvas keys act under a modal"
// with the edit-sheet line above, and "the canvas keys never act" with
//   x Delete or Backspace under the tablet's edit sheet or palette sheet leaves the flow's stages alone: Delete on the tablet with no sheet open: expected ["tb"], got ["ta","tb"]
await test("Delete or Backspace under the tablet's edit sheet or palette sheet leaves the flow's stages alone", async () => {
  const ids = () => useStore.getState().flows.graph.nodes.map((n) => n.id);
  const press = (el: any, key: string) => {
    act(() => { el.dispatchEvent(new win.KeyboardEvent("keydown", { key, bubbles: true, cancelable: true })); });
  };
  // The edit sheet on Beta, Alpha selected behind it.
  seed({ sel: "ta", editNode: "tb" });
  await mount("tablet");
  const editSheet = doc.querySelector("[data-flows-editsheet]") as any;
  assert(editSheet, "precondition: the edit sheet is not open");
  const inEdit = editSheet.querySelector("button");
  assert(inEdit, "precondition: the edit sheet has no button to press a key on");
  for (const key of ["Backspace", "Delete"]) {
    press(inEdit, key);
    eq(ids(), ["ta", "tb"], `the flow's stages after ${key} on a button in the edit sheet`);
  }
  // The palette sheet, Alpha selected behind it.
  seed({ sel: "ta", paletteOpen: true });
  await mount("tablet");
  const inPalette = doc.querySelector('[data-palette-type="target"]') as any;
  assert(inPalette, "precondition: the palette sheet is not open");
  for (const key of ["Backspace", "Delete"]) {
    press(inPalette, key);
    eq(ids(), ["ta", "tb"], `the flow's stages after ${key} on a button in the palette sheet`);
  }
  // CONTROL: the same tablet with no sheet open still deletes the selection,
  // so what held the key back above is the sheet, not the tier.
  seed({ sel: "ta" });
  await mount("tablet");
  assert(doc.querySelector('[aria-modal="true"]') === null, "precondition: a modal is open on the bare tablet");
  press(doc.body, "Delete");
  eq(ids(), ["tb"], "Delete on the tablet with no sheet open");
});

// ======================================================================
// 10. run mode is the door's (#189 S5; spec 2.6)

// MUTANT "the doors never pass viewOnly" (FlowEditor.tsx `FramingHostSheet`
// renders `<FramingSheet nodeId={nodeId} onClose={onClose} />`, the host as
// S4 left it). Run in scratchpad S5-RUNMODE-mut, 2026-09-28. Observed, 12/13:
//   x while the open flow's session runs, FRAME ON SKY opens the modal read-only in run mode; a dormant session opens it editable: the sheet's view mode while the flow runs: expected "true", got null
// MUTANT "run mode decided once at open" (FlowEditor.tsx `FramingHostSheet`
// reads the answer once, `const [running] = useState(() =>
// flowRunLive(useStore.getState().flows.progress,
// useStore.getState().sequence))`, as S5 wrote the reader). Observed, 12/13:
//   x while the open flow's session runs, FRAME ON SKY opens the modal read-only in run mode; a dormant session opens it editable: the sheet's reason once the run has ended: expected null, got "This flow's session is running: its framing opens to view, not to edit."
await test("while the open flow's session runs, FRAME ON SKY opens the modal read-only in run mode; a dormant session opens it editable", async () => {
  const sheetReason = () => (doc.querySelector('[data-testid="framing-view-why"]') as any)?.textContent ?? null;
  const done = () => doc.querySelector('[data-testid="framing-done"]');
  // Premise: the two recorded answers are one session, and the run is live.
  eq(RUN_STATE.session.id, RUN_PROGRESS.session.id, "premise: the run writes the progress answer's session");
  eq(RUN_STATE.state, "running", "premise: the recorded run's state");

  // CONTROL: the session dormant, the rig idle. The modal edits.
  seed({ sel: "ta", progress: RUN_PROGRESS });
  await mount("desktop");
  click(frameRow());
  await flush();
  assert(sheet(), "FRAME ON SKY mounted no sheet over a dormant session");
  eq(sheet().getAttribute("data-view"), null, "the sheet's view mode over a dormant session");
  eq(sheetReason(), null, "the sheet's reason over a dormant session");
  assert(done(), "the sheet over a dormant session offers no DONE");
  unmount();

  // CONTROL: the rig running ANOTHER flow's session (the recorded run, its
  // session id changed). The modal edits: a run is this flow's by the session
  // id the progress route counts, never because the rig is busy. MUTANT "the
  // door asks whether ANY run is live" (`FramingHostSheet` reads
  // `s.sequence?.state === "running"` in place of `flowRunLive`), green before
  // this control existed; observed (verifier's scratch copy
  // S5-RUNMODE-verify-mut, 2026-09-28), 12/13:
  //   x while the open flow's session runs, FRAME ON SKY opens the modal read-only in run mode; a dormant session opens it editable: the sheet's view mode under another flow's run: expected null, got "true"
  const otherRun = { ...RUN_STATE, session: { ...RUN_STATE.session, id: "another-flows-session" } };
  seed({ sel: "ta", progress: RUN_PROGRESS, sequence: otherRun });
  await mount("desktop");
  click(frameRow());
  await flush();
  assert(sheet(), "FRAME ON SKY mounted no sheet under another flow's run");
  eq(sheet().getAttribute("data-view"), null, "the sheet's view mode under another flow's run");
  eq(sheetReason(), null, "the sheet's reason under another flow's run");
  assert(done(), "the sheet under another flow's run offers no DONE");
  unmount();

  // The flow's session running: the same door opens run mode.
  seed({ sel: "ta", progress: RUN_PROGRESS, sequence: RUN_STATE });
  await mount("desktop");
  eq(frameRow()?.textContent, FRAME_ON_SKY, "the column's row while the flow runs");
  const before = JSON.stringify(useStore.getState().flows.graph);
  click(frameRow());
  await flush();
  assert(sheet(), "FRAME ON SKY mounted no sheet while the flow runs");
  eq(sheet().getAttribute("data-view"), "true", "the sheet's view mode while the flow runs");
  eq(sheetReason(), RUNNING_VIEW_ONLY, "the sheet's reason while the flow runs");
  assert(done() === null, "the sheet offers DONE while the flow runs");
  eq((doc.querySelector(".tfs-fieldset") as any)?.disabled, true, "the sheet's controls while the flow runs");

  // The run ends under the open modal: the door's answer follows it, and the
  // modal edits again without being reopened.
  act(() => { useStore.setState({ sequence: { ...RUN_STATE, state: "complete" } } as any); });
  await flush();
  eq(sheetReason(), null, "the sheet's reason once the run has ended");
  assert(done(), "the sheet offers no DONE once the run has ended");
  click(buttonByText("CANCEL"));
  await flush();
  eq(JSON.stringify(useStore.getState().flows.graph), before, "the graph after run mode and a CANCEL");
});

// ------------------------------------------------------------------- report
act(() => { root.unmount(); });
const total = passed + failed;
console.log(`classicFrameHost.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
export default result;
