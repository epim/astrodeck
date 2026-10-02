// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// flowFrameSheet.test.tsx - the #/next `flowFrame` sheet, its doors and its
// registry entry (#189 S4 items 3 and 5; spec 2026-09-23 flows mosaic, 2.1).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/next/hubs/session/flows/framing/__tests__/flowFrameSheet.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT IS WORTH GUARDING HERE
//
//   1. THE REGISTRY STAYS LAZY. `framing/reg.ts` is reached from the entry
//      chunk through `session/sheets/index.ts`, and the sheet it names brings
//      the Target modal and its survey canvas. Graded at RUN TIME, not only on
//      the source text: a resolve hook records every module the process
//      resolves, so importing the registry must resolve neither the sheet
//      module nor the modal, and loading the sheet must still not resolve the
//      modal until it renders (lazy twice over). The positive controls show
//      the hook does see both modules once they are really asked for.
//   2. THE PHONE DOOR, DEPTH 2. From the stage list (`flowStages`) a row opens
//      `flowNode`; FRAME ON SKY there opens `flowFrame` IN PLACE OF `flowNode`
//      (the router's MAX_SHEETS), and the route keeps `?open=`. Mounted
//      through the real `SheetHost` and the real composed registry, so the
//      name, the lazy chunks and the modal's own marker are all on the path.
//      A read-only Example keeps the door, and the modal opens to view.
//   3. THE DROP. A TARGET tapped in the palette sheet opens the modal on the
//      node the tap just made, in the palette's place; any other stage closes
//      the palette as before.
//   4. THE CLOSE. The modal's CANCEL pops this sheet, and a close that
//      arrives once the sheet is no longer on top pops nothing. Since #382
//      the shared modal sends no such close itself, so this is the adapter's
//      defence in depth.
//   5. NO FORK. The sheet mounts the shared modal through its lazy door.
//   6. THE FLOW THE ROUTE NAMES (#384). Node ids repeat across flows, so
//      while `?open=` names a flow the store does not hold, the sheet waits
//      and mounts no modal, says which of the two waits it is, and offers
//      BACK; it frames that flow once it loads, and a flow that changes under
//      an open modal takes the modal down. Until this case the rule was held
//      only by the real-page probe (tools/ui_probe/routes_s4_frame.json),
//      which is run by hand against a simulator server.
//   7. A CLOSE WHILE DONE WRITES, AT THE SHEETHOST SEAM. `SheetHost` binds
//      Escape to `nav.back()` itself. The modal refuses CANCEL and Escape
//      while DONE's write waits for its compile (#382), and in #/next that
//      refusal holds only because the modal's Overlay stops the key before
//      it reaches the window. Graded through the real SheetHost with the
//      compile held open, which neither the modal's own test (no SheetHost)
//      nor the probe (no held compile) can do.
//   8. RUN MODE IS THE DOOR'S (#189 S5; spec 2.6). While the open flow's
//      session runs (flowRunState `flowRunLive`, over the sessions the slice
//      knows as the flow's since #449), the sheet mounts the modal with
//      `viewOnly`: read-only, with RUNNING_VIEW_ONLY; a dormant session, or
//      the rig running another flow's session, opens it editable. That it
//      holds through a save's re-read is runModeAcrossSave.test.tsx's.
//
// Every mutant below was run in a private scratch copy of ui/, never in the
// shared tree (#254), and the failure it produced is quoted verbatim. All of
// them were re-run on 2026-09-27 in scratchpad s4-uhostn-r2-mut, on the tree
// as S4's second wave left it (the #382 and #384 fixes in), and the quotes
// below are that run's. Their tallies ("N/16") were counted before this
// task's verifier added section 2's read-only Example case, which makes 17;
// that case's own quote is 17-based.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------ resolve recorder first
// Installed before ANY import below, so the first import of the registry is
// the first time the process resolves it and everything it drags in.
const resolved: string[] = [];
{
  const { registerHooks } = await import("node:module");
  registerHooks({
    resolve(specifier: string, context: any, nextResolve: any) {
      const r = nextResolve(specifier, context);
      resolved.push(String(r.url));
      return r;
    },
    load(url: string, context: any, nextLoad: any) {
      if (url.endsWith(".css")) {
        return { format: "module", shortCircuit: true, source: "export default {};" };
      }
      return nextLoad(url, context);
    },
  } as any);
}

// ---------------------------------------------------------------- jsdom next
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/", pretendToBeVisual: true },
);
const win = dom.window as any;
let viewportW = 390;
win.matchMedia = (q: string) => {
  const m = /min-width:\s*(\d+)px/.exec(q);
  return {
    matches: m ? viewportW >= Number(m[1]) : false,
    addEventListener() {}, removeEventListener() {},
    addListener() {}, removeListener() {},
  };
};
win.Element.prototype.setPointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.releasePointerCapture = function () { /* jsdom has none */ };
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
win.HTMLCanvasElement.prototype.getContext = () => null;
win.URL.createObjectURL = () => "blob:stub";
win.URL.revokeObjectURL = () => {};
win.WebSocket = class { close() {} addEventListener() {} send() {} };
// No route in this file needs an answer; a request that is made is recorded
// and refused, so nothing waits on a network.
const asked: string[] = [];
win.fetch = async (url: string) => {
  asked.push(String(url));
  return {
    ok: false, status: 404, statusText: "Not Found",
    headers: { get: () => "application/json" },
    json: async () => ({ detail: "not in this fixture" }),
  };
};
const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement", "HTMLSelectElement",
  "HTMLTextAreaElement", "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "PointerEvent", "FocusEvent", "localStorage", "sessionStorage", "getComputedStyle", "matchMedia",
  "ResizeObserver", "requestAnimationFrame", "cancelAnimationFrame", "Image", "URL", "fetch",
  "Blob", "WebSocket", "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: unknown, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (JSON.stringify(got) !== JSON.stringify(want)) {
    throw new Error(`${msg}\n  expected ${JSON.stringify(want)}\n  got      ${JSON.stringify(got)}`);
  }
}

const isSheetModule = (u: string) => /\/flows\/framing\/FlowFrameSheet\.tsx$/.test(u);
const isModal = (u: string) => /\/components\/flows\/framing\/TargetFramingSheet\.tsx$/.test(u);
const isSharedDoor = (u: string) => /\/components\/flows\/framing\/index\.ts$/.test(u);
const short = (u: string) => u.replace(/^.*\/ui\/src\//, "");

// ============================================= 1. the registry stays lazy
//
// MUTANT "static import in reg.ts" (reg.ts gains
// `import { FlowFrameSheet } from "./FlowFrameSheet";` and its loader becomes
// `() => Promise.resolve({ default: FlowFrameSheet })`). Observed
// ("flowFrameSheet.test: 13/16 passed"):
//   x importing the registry resolves neither the sheet module nor the modal: importing framing/reg.ts resolved these - the entry chunk pays for the sheet to register one name:
//     expected ""
//     got      "next/hubs/session/flows/framing/FlowFrameSheet.tsx, components/flows/framing/index.ts"
//   x reg.ts imports nothing but types, and its loader is a dynamic import: reg.ts has a static import:
//     expected []
//     got      ["./FlowFrameSheet"]
//   x loading the sheet resolves the shared door, and still not the modal: loading resolved no FlowFrameSheet.tsx:
// (The session registry's own case stays green under this mutant: reg.ts has
// already pulled the sheet in by then, so nothing new resolves. Its mutant is
// the next one.)
//
// MUTANT "session registry imports the barrel" (session/sheets/index.ts takes
// `flowFrameSheets` from "../flows/framing", the area barrel, instead of
// "../flows/framing/reg"). Observed ("flowFrameSheet.test: 14/16 passed"):
//   x importing the session hub's registry resolves no framing module either: the session registry resolved these, and it is in the entry chunk:
//     expected ""
//     got      "next/hubs/session/flows/framing/FlowFrameSheet.tsx, components/flows/framing/index.ts"
//   x loading the sheet resolves the shared door, and still not the modal: the sheet did not reach the shared modal's door
//
// MUTANT "framing half not spread" (session/sheets/index.ts loses
// `...flowFrameSheets`; its home is flowsDom.test.tsx) is red here too.
// Observed ("flowFrameSheet.test: 7/16 passed"): the session registry's case
// on its precondition, and every case that reaches the sheet through
// SheetHost on a timeout, the modal's marker never arriving:
//   x importing the session hub's registry resolves no framing module either: precondition: the session registry has no flowFrame entry
//   x from flowStages -> flowNode, FRAME ON SKY opens flowFrame in flowNode's place and keeps ?open=: timed out waiting for the Target modal
//   x a TARGET tapped in the palette sheet opens flowFrame on the new node, in the palette's place: timed out waiting for the Target modal
//   x at tablet the palette sheet is replaced by the modal, not left under it: timed out waiting for the Target modal
//   x while ?open= names a flow the store does not hold, the sheet waits, and frames that flow once it loads: timed out waiting for the framing sheet
//   x with no flow open the wait says the link's flow is loading, and its BACK leaves the sheet: timed out waiting for the wait
//   x with no ?open= the sheet frames the open flow, as before: timed out waiting for the Target modal
//   x on a phone, while DONE writes, neither CANCEL nor Escape pops flowFrame, and DONE's own close pops it once: timed out waiting for the Target modal
//   x on a phone with nothing in flight, Escape closes the modal back to the stage list: timed out waiting for the Target modal
//
// MUTANT "modal imported statically" (FlowFrameSheet.tsx imports
// `TargetFramingSheet` from "components/flows/framing/TargetFramingSheet"
// instead of the lazy door). Observed ("flowFrameSheet.test: 14/16 passed"):
//   x loading the sheet resolves the shared door, and still not the modal: loading the sheet resolved the modal itself (components/flows/framing/TargetFramingSheet.tsx) before anything rendered it - the stage editor's door and the palette would pay for the survey canvas
//   x the sheet mounts the shared modal through its lazy door, with no fork: FlowFrameSheet.tsx does not import TargetFramingSheetLazy from the shared door: ["react","../../../../../components/flows/framing/TargetFramingSheet","../../../../../store","../../../../router","../../../../ui","../../../sheets"]

resolved.length = 0;
const { flowFrameSheets } = await import("../reg");
const regResolved = resolved.splice(0);
const { sheets: sessionSheets } = await import("../../../sheets/index");
const sessionResolved = resolved.splice(0);

await test("importing the registry resolves neither the sheet module nor the modal", () => {
  // Vacuity: the recorder is live and saw the registry itself being resolved.
  assert(regResolved.some((u) => /\/flows\/framing\/reg\.ts$/.test(u)),
    `the resolve hook did not see framing/reg.ts, so this test can see nothing: ${regResolved.map(short).join(", ")}`);
  const heavy = regResolved.filter((u) => isSheetModule(u) || isModal(u) || isSharedDoor(u));
  eq(heavy.map(short).join(", "), "",
    "importing framing/reg.ts resolved these - the entry chunk pays for the sheet to register one name:");
  // And nothing else either: the file imports one type, which is erased.
  eq(regResolved.map(short).join(", "), "next/hubs/session/flows/framing/reg.ts",
    "framing/reg.ts resolved more than itself:");
});

await test("importing the session hub's registry resolves no framing module either", () => {
  assert(sessionResolved.some((u) => /\/session\/sheets\/index\.ts$/.test(u)),
    "the resolve hook did not see session/sheets/index.ts");
  assert(sessionSheets.flowFrame != null, "precondition: the session registry has no flowFrame entry");
  const heavy = sessionResolved.filter((u) => isSheetModule(u) || isModal(u) || isSharedDoor(u));
  eq(heavy.map(short).join(", "), "",
    "the session registry resolved these, and it is in the entry chunk:");
});

await test("reg.ts imports nothing but types, and its loader is a dynamic import", async () => {
  const { readFileSync } = await import("node:fs");
  const src = readFileSync(new URL("../reg.ts", import.meta.url), "utf8")
    .replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
  const statics = Array.from(src.matchAll(/^import\s+(?!type\s)[^;]*?from\s+"([^"]+)";/gm)).map((m) => m[1]);
  eq(statics, [], "reg.ts has a static import:");
  assert(/import\("\.\/FlowFrameSheet"\)/.test(src), "reg.ts does not load the sheet with import()");
});

await test("loading the sheet resolves the shared door, and still not the modal", async () => {
  resolved.length = 0;
  const mod = await flowFrameSheets.flowFrame.load();
  const seen = resolved.splice(0);
  eq(typeof mod.default, "function", "the loader did not hand back a component");
  // The positive control: the hook sees the sheet module the moment it is
  // really asked for.
  assert(seen.some(isSheetModule), `loading resolved no FlowFrameSheet.tsx: ${seen.map(short).join(", ")}`);
  const modal = seen.filter(isModal);
  assert(modal.length === 0,
    `loading the sheet resolved the modal itself (${modal.map(short).join(", ")}) before anything `
    + "rendered it - the stage editor's door and the palette would pay for the survey canvas");
  assert(seen.some(isSharedDoor), "the sheet did not reach the shared modal's door");
});

// ------------------------------------------------------------------ imports
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../../store");
const { FLOWS_INIT } = await import("../../../../../../components/flows/flowsSlice");
const { NODE_DEFS } = await import("../../../../../../components/flows/nodeDefs");
const { resetRouterCacheForTests, useRoute } = await import("../../../../../router");
const { SheetHost } = await import("../../../../../shell/SheetHost");
const {
  FLOW_FRAME_SHEET, FlowFrameSheet, FRAME_FLOW_LOADING, FRAME_OTHER_FLOW,
} = await import("../FlowFrameSheet");
const { flowsApi } = await import("../../../../../../lib/flowsApi");

/** While set, the store's compile waits on it, so DONE's write stays in
 *  flight for as long as a test holds it (section 7). Null everywhere else,
 *  where the compile goes to the refusing fetch above and settles at once. */
let compileGate: Promise<void> | null = null;
{
  const realCompile = (flowsApi as any).compileDraft;
  (flowsApi as any).compileDraft = async (...a: any[]) => {
    if (compileGate) await compileGate;
    return realCompile(...a);
  };
}

// ------------------------------------------------------------------ fixture
const OPEN = "quick-m31";
const TARGET_ID = "t1";
const CAPTURE_ID = "c1";
const OPERATOR = ["view.status", "view.preview", "control.mount", "control.capture"];

function graph(target = "M31"): any {
  return {
    nodes: [
      { id: TARGET_ID, type: "target", x: 0, y: 0,
        params: { ...NODE_DEFS.target.params, name: target, counts: "Accepted subs" } },
      { id: CAPTURE_ID, type: "capture", x: 300, y: 0, params: { ...NODE_DEFS.capture.params } },
    ],
    edges: [],
  };
}

/** A flow already open in the store, as the stage list leaves it, so no sheet
 *  here has a reason to fetch it. Offline (`wsConnected: false`) and without
 *  the site capability, so the modal asks no route of its own.
 *
 *  `id` and `target` open another flow instead (section 6): by default the
 *  flow `?open=` names in every route here, framing M31. `id: null` is an
 *  unsaved flow, which has no record and so no id to name. `readonly` makes
 *  the flow a read-only Example (section 2's last case). */
function seed(o: {
  id?: string | null; target?: string; readonly?: boolean; progress?: any; sequence?: any;
} = {}): void {
  const id = o.id === undefined ? OPEN : o.id;
  const gr = graph(o.target);
  act(() => {
    useStore.setState({
      principal: { role: "operator", email: null, caps: OPERATOR } as never,
      authGate: "open",
      wsConnected: false,
      wsPhase: "up",
      status: { connected: { camera: { connected: true } } } as never,
      sequence: (o.sequence ?? { state: "idle" }) as never,
      resumeArm: null as never,
      flows: {
        ...FLOWS_INIT,
        record: id === null ? null : {
          id, name: `Quick ${o.target ?? "M31"}`, folder: "My flows", tagline: "", graph: gr,
          created_ts: 0, updated_ts: 0, last_run: null, last_result: "", readonly: o.readonly ?? false },
        graph: gr,
        progress: o.progress ?? null,
        // As the slice holds it once an answer has landed: the answer's
        // session noted in the same write (flowsSlice `fetchProgress`), which
        // is what the door reads since #449.
        sessionIds: o.progress?.session?.id ? [o.progress.session.id] : [],
        ui: { ...FLOWS_INIT.ui, screen: "editor" },
      } as never,
    } as never);
  });
}

const container = win.document.getElementById("root") as any;
let root = createRoot(container);
let phone = true;
function Host(): any {
  const r = useRoute();
  return createElement(SheetHost as any, { route: r, phone });
}

const doc = win.document;
const q = (id: string): any => doc.querySelector(`[data-testid="${id}"]`);
function click(el: any): void {
  assert(el, "no element to click");
  act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
}
async function flush(): Promise<void> {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}
/** Lazy chunks arrive on the module loader's clock, not React's: poll. */
async function until(what: string, cond: () => boolean): Promise<void> {
  for (let i = 0; i < 400; i++) {
    if (cond()) return;
    await act(async () => { await new Promise((r) => setTimeout(r, 5)); });
  }
  throw new Error(`timed out waiting for ${what}`);
}
async function mountAt(hash: string, asPhone = true): Promise<void> {
  phone = asPhone;
  viewportW = asPhone ? 390 : 1024;
  await act(async () => { root.unmount(); });
  win.location.hash = hash;
  resetRouterCacheForTests();
  root = createRoot(container);
  await act(async () => { root.render(createElement(Host)); });
  await flush();
}
async function unmountAll(): Promise<void> {
  await act(async () => { root.unmount(); });
  root = createRoot(container);
}
/** The stage list's row for one stage, by its card title. */
function stageRowButton(label: string): any {
  const rows = Array.from(doc.querySelectorAll('[data-testid="flow-stage-row"]')) as any[];
  const row = rows.find((r) => r.querySelector(".nx-row-title")?.textContent === label);
  return row?.querySelector("button.nx-row");
}
const modal = (): any => q("target-framing-sheet");
const cancelButton = (): any => Array.from(doc.querySelectorAll(".tfs-head button") as any[])
  .find((b: any) => /CANCEL|CLOSE/.test(b.textContent));
const hash = (): string => String(win.location.hash);
const doneButton = (): any => q("framing-done")?.closest("button");
/** The TARGET's name as the store holds it: what DONE's write left. */
const storedName = (): string =>
  String(useStore.getState().flows.graph.nodes.find((n: any) => n.id === TARGET_ID)?.params.name);
/** A React-controlled input takes a value through the native setter. */
function typeInto(el: any, value: string): void {
  assert(el, "no input to type into");
  const setter = Object.getOwnPropertyDescriptor(win.HTMLInputElement.prototype, "value")!.set!;
  act(() => { setter.call(el, value); el.dispatchEvent(new win.Event("input", { bubbles: true })); });
}
/** Escape as a browser sends it: at the focused element, bubbling, so it
 *  passes `document` (the modal's Overlay, capture phase) before the window
 *  (SheetHost, bubble phase). */
function pressEscape(): void {
  const at = (doc.activeElement ?? doc.body) as any;
  act(() => {
    at.dispatchEvent(new win.KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }));
  });
}
/** Every chance for a modal that should not mount to do so: its chunk is
 *  cached by the cases before, so it would mount within a tick or two. */
async function settleChunks(): Promise<void> {
  for (let i = 0; i < 20; i++) await flush();
}

// ========================================= 2. the phone door, at depth 2
//
// MUTANT "params dropped" (FlowFrameSheet.tsx `flowFrameParams` returns `{}`,
// so `nav.sheet` is handed no params). Observed ("flowFrameSheet.test: 13/16 passed"):
//   x from flowStages -> flowNode, FRAME ON SKY opens flowFrame in flowNode's place and keeps ?open=: the door did not land on flowFrame at depth 2 with the stage and the flow in the route
//     expected "#/session/flows/flowStages/flowFrame?node=t1&open=quick-m31"
//     got      "#/session/flows/flowStages/flowFrame"
//   x a TARGET tapped in the palette sheet opens flowFrame on the new node, in the palette's place: the dropped TARGET did not open its framing in the palette's place
//     expected "#/session/flows/flowStages/flowFrame?node=n1_mukutn1b&open=quick-m31"
//     got      "#/session/flows/flowStages/flowFrame"
//   x at tablet the palette sheet is replaced by the modal, not left under it: the palette's TARGET did not open the modal in its place
//     expected "#/session/flows/flowFrame?node=n3_mukutn2l&open=quick-m31"
//     got      "#/session/flows/flowFrame"
//
// MUTANT "open dropped at the door" (FlowNodeEditor.tsx's FRAME ON SKY calls
// `openFlowFrame(id, null)`). Observed ("flowFrameSheet.test: 15/16 passed"):
//   x from flowStages -> flowNode, FRAME ON SKY opens flowFrame in flowNode's place and keeps ?open=: the door did not land on flowFrame at depth 2 with the stage and the flow in the route
//     expected "#/session/flows/flowStages/flowFrame?node=t1&open=quick-m31"
//     got      "#/session/flows/flowStages/flowFrame?node=t1"
//
// MUTANT "door appends to stack" (the verifier's: `openFlowFrame` pushes the
// whole stack plus flowFrame through `nav.go(buildHash(...))` instead of
// `nav.sheet`, so the router's depth-2 replacement never runs and
// `buildHash` cuts the third sheet off). Observed
// ("flowFrameSheet.test: 15/16 passed"):
//   x from flowStages -> flowNode, FRAME ON SKY opens flowFrame in flowNode's place and keeps ?open=: timed out waiting for the Target modal

await test("from flowStages -> flowNode, FRAME ON SKY opens flowFrame in flowNode's place and keeps ?open=", async () => {
  seed();
  await mountAt(`#/session/flows/flowStages?open=${OPEN}`);
  await until("the stage list", () => q("session-flow-stages") != null);

  click(stageRowButton(NODE_DEFS.target.label));
  await until("the stage editor", () => q("session-flow-node") != null);
  eq(hash(), `#/session/flows/flowStages/flowNode?node=${TARGET_ID}&open=${OPEN}`,
    "precondition: the row did not open flowNode at depth 2 on the TARGET");

  const door = q("flow-node-frame");
  assert(door != null, "the TARGET's stage editor has no FRAME ON SKY door");
  click(door);
  await until("the Target modal", () => modal() != null);

  eq(hash(), `#/session/flows/flowStages/${FLOW_FRAME_SHEET}?node=${TARGET_ID}&open=${OPEN}`,
    "the door did not land on flowFrame at depth 2 with the stage and the flow in the route");
  assert(q("session-flow-node") == null,
    "flowNode is still mounted - flowFrame was stacked on it instead of taking its place");
  const stages = q("session-flow-stages");
  assert(stages != null, "the stage list under the modal was unmounted");
  eq(stages.closest(".nx-sheet-slot")?.getAttribute("data-under"), "true",
    "the stage list is not the sheet under flowFrame");
  assert(!String(modal().textContent).includes("no longer in this flow"),
    "the modal opened on no stage: it said the stage is gone");
  assert(String(modal().textContent).includes("FRAME M31"), "the modal is not framing the TARGET M31");

  // The modal's CANCEL is BACK: the stage list, with its flow still named.
  click(cancelButton());
  await until("the modal to close", () => modal() == null);
  eq(hash(), `#/session/flows/flowStages?node=${TARGET_ID}&open=${OPEN}`,
    "CANCEL did not return to the stage list with ?open= kept");
  eq(q("session-flow-stages")?.closest(".nx-sheet-slot")?.getAttribute("data-under"), "false",
    "the stage list is not back on top after CANCEL");
  await unmountAll();
});

// CONTROL. A stage that is not a TARGET has no door, and its row still opens
// flowNode at depth 2 with the flow in the route, as before this slice.
//
// MUTANT "door on every stage" (FlowNodeEditor.tsx renders the frame slot for
// every node type). Observed ("flowFrameSheet.test: 15/16 passed"):
//   x a stage that is not a TARGET opens flowNode as before and offers no door: a CAPTURE LOOP stage offers FRAME ON SKY, which frames a TARGET
await test("a stage that is not a TARGET opens flowNode as before and offers no door", async () => {
  seed();
  await mountAt(`#/session/flows/flowStages?open=${OPEN}`);
  await until("the stage list", () => q("session-flow-stages") != null);
  click(stageRowButton(NODE_DEFS.capture.label));
  await until("the stage editor", () => q("session-flow-node") != null);
  eq(hash(), `#/session/flows/flowStages/flowNode?node=${CAPTURE_ID}&open=${OPEN}`,
    "a non-TARGET row no longer opens flowNode at depth 2 with ?open=");
  assert(q("flow-node-delete") != null, "precondition: the stage editor did not render its body");
  assert(q("flow-node-frame") == null,
    `a ${NODE_DEFS.capture.label} stage offers FRAME ON SKY, which frames a TARGET`);
  await unmountAll();
});

// A READ-ONLY EXAMPLE KEEPS ITS DOOR (spec 2.1: an Example opens the modal in
// view mode and says why). The stage editor locks every field row on an
// Example, and `FlowNodeEditor` has the lock reason in hand, so a door locked
// or hidden along with the rows is one condition away; it would leave the
// operator no way to see an Example's framing at all.
//
// MUTANT "door locked on read-only" (the verifier's: FlowNodeEditor.tsx
// renders the frame slot only while `lockedReason` is null). Observed
// ("flowFrameSheet.test: 16/17 passed"; run in scratchpad
// s4-uhostn-r2-verify2-mut, 2026-09-27):
//   x on a read-only Example the TARGET keeps its door, and the modal opens to view: a read-only Example's TARGET offers no FRAME ON SKY, so its framing cannot even be viewed
// (flowsDom.test stays green under it: none of its doors is on an Example.)
await test("on a read-only Example the TARGET keeps its door, and the modal opens to view", async () => {
  seed({ readonly: true });
  await mountAt(`#/session/flows/flowStages?open=${OPEN}`);
  await until("the stage list", () => q("session-flow-stages") != null);
  click(stageRowButton(NODE_DEFS.target.label));
  await until("the stage editor", () => q("session-flow-node") != null);
  assert(q("flow-node-readonly-note") != null,
    "precondition: the stage editor does not say the Example is read-only, so this case is not on the locked path");
  const door = q("flow-node-frame");
  assert(door != null, "a read-only Example's TARGET offers no FRAME ON SKY, so its framing cannot even be viewed");
  click(door);
  await until("the Target modal", () => modal() != null);
  eq(hash(), `#/session/flows/flowStages/${FLOW_FRAME_SHEET}?node=${TARGET_ID}&open=${OPEN}`,
    "the Example's door did not land on flowFrame at depth 2 with the stage and the flow");
  // Loaded here, not at the top: a static import would resolve the modal
  // before section 1 grades that nothing does.
  const { EXAMPLE_VIEW_ONLY } = await import("../../../../../../components/flows/framing/TargetFramingSheet");
  assert(String(modal().textContent).includes(EXAMPLE_VIEW_ONLY),
    "the Example's modal did not open in view mode with its reason");
  eq(doneButton() ?? null, null, "the Example's modal offers DONE, which the save would refuse");
  await unmountAll();
});

// ============================================= 3. the drop opens the sheet
//
// MUTANT "drop does not open" (paletteDrop.ts `frameDroppedStage` answers
// false before opening anything). Observed ("flowFrameSheet.test: 14/16 passed"):
//   x a TARGET tapped in the palette sheet opens flowFrame on the new node, in the palette's place: the dropped TARGET did not open its framing in the palette's place
//     expected "#/session/flows/flowStages/flowFrame?node=n1_mukuucgl&open=quick-m31"
//     got      "#/session/flows/flowStages?open=quick-m31"
//   x at tablet the palette sheet is replaced by the modal, not left under it: the palette's TARGET did not open the modal in its place
//     expected "#/session/flows/flowFrame?node=n3_mukuuchv&open=quick-m31"
//     got      "#/session/flows"
// (The node ids are the slice's `n<seq>_<base36 time>`, as minted in that run.)

await test("a TARGET tapped in the palette sheet opens flowFrame on the new node, in the palette's place", async () => {
  seed();
  await mountAt(`#/session/flows/flowStages/flowPalette?open=${OPEN}`);
  await until("the palette sheet", () => q("session-flow-palette") != null);
  const before = useStore.getState().flows.graph.nodes.length;

  click(q("palette-type-target"));
  const nodes = useStore.getState().flows.graph.nodes;
  eq(nodes.length, before + 1, "precondition: the palette did not add a stage");
  const added = nodes[nodes.length - 1];
  eq(added.type, "target", "precondition: the stage added is not a TARGET");

  eq(hash(), `#/session/flows/flowStages/${FLOW_FRAME_SHEET}?node=${added.id}&open=${OPEN}`,
    "the dropped TARGET did not open its framing in the palette's place");
  await until("the Target modal", () => modal() != null);
  assert(!String(modal().textContent).includes("no longer in this flow"),
    "the modal opened on a stage that is not the one just dropped");
  assert(q("session-flow-palette") == null, "the palette is still mounted under the modal");

  click(cancelButton());
  await until("the modal to close", () => modal() == null);
  eq(hash(), `#/session/flows/flowStages?node=${added.id}&open=${OPEN}`,
    "BACK from the dropped TARGET's framing did not land on the stage list");
  await unmountAll();
});

// CONTROL. Any other stage: the palette closes, exactly as before.
await test("any other stage tapped in the palette sheet closes the palette as before", async () => {
  seed();
  await mountAt(`#/session/flows/flowStages/flowPalette?open=${OPEN}`);
  await until("the palette sheet", () => q("session-flow-palette") != null);
  click(q("palette-type-capture"));
  const nodes = useStore.getState().flows.graph.nodes;
  eq(nodes[nodes.length - 1].type, "capture", "precondition: the palette did not add the CAPTURE");
  eq(hash(), `#/session/flows/flowStages?open=${OPEN}`,
    "a CAPTURE tapped in the palette no longer just closes the palette");
  await flush();
  assert(modal() == null, "a CAPTURE opened the Target modal");
  await unmountAll();
});

// At tablet the palette is the only sheet, and the modal takes its place
// without a history entry: BACK from the modal is the canvas, not a palette
// whose one tap has been spent.
//
// MUTANT "palette left under the modal" (FlowFrameSheet.tsx `openFlowFrame`
// ignores `inPlaceOfTop` and always calls `nav.sheet`). Observed
// ("flowFrameSheet.test: 15/16 passed"):
//   x at tablet the palette sheet is replaced by the modal, not left under it: the palette's TARGET did not open the modal in its place
//     expected "#/session/flows/flowFrame?node=n3_mukuukv2&open=quick-m31"
//     got      "#/session/flows/flowPalette/flowFrame?node=n3_mukuukv2&open=quick-m31"
// (On a phone the router's own depth-2 replacement hides this mutant, which
// is why this case runs at tablet width.)
//
// MUTANT "replace pushes" (the verifier's: `openFlowFrame`'s replacement
// calls `nav.go` instead of `nav.replace`, so the hash is right and only
// the history can tell). Observed ("flowFrameSheet.test: 15/16 passed"):
//   x at tablet the palette sheet is replaced by the modal, not left under it: the replacement pushed a history entry, so Back returns to a spent palette
//     expected 10
//     got      11
await test("at tablet the palette sheet is replaced by the modal, not left under it", async () => {
  seed();
  await mountAt(`#/session/flows/flowPalette?open=${OPEN}`, false);
  await until("the palette sheet", () => q("session-flow-palette") != null);
  const depth = win.history.length;
  click(q("palette-type-target"));
  const nodes = useStore.getState().flows.graph.nodes;
  const added = nodes[nodes.length - 1];
  eq(hash(), `#/session/flows/${FLOW_FRAME_SHEET}?node=${added.id}&open=${OPEN}`,
    "the palette's TARGET did not open the modal in its place");
  eq(win.history.length, depth, "the replacement pushed a history entry, so Back returns to a spent palette");
  await until("the Target modal", () => modal() != null);
  await unmountAll();
});

// ======================================================== 4. the close
//
// The shared modal used to call `onClose` again when DONE's compile answered
// after CANCEL had already closed it (#382, found here), and this adapter's
// guard was what kept that second call from popping the sheet underneath.
// The modal now closes once at most (section 7 holds that at this seam), so
// the guard is defence in depth; this case still grades it directly, with
// the sheet mounted and its route already gone.
//
// MUTANT "unguarded close" (FlowFrameSheet.tsx `closeFrame` calls `nav.back()`
// whatever is on top). Observed ("flowFrameSheet.test: 15/16 passed"):
//   x a close that arrives once the sheet is no longer on top pops nothing: a late close called the browser's Back with no sheet open - it would leave the canvas
//     expected 0
//     got      1
await test("a close that arrives once the sheet is no longer on top pops nothing", async () => {
  seed();
  await act(async () => { root.unmount(); });
  win.location.hash = `#/session/flows?open=${OPEN}`;
  resetRouterCacheForTests();
  root = createRoot(container);
  // The sheet as DONE's late close finds it: mounted, its route already gone.
  await act(async () => {
    root.render(createElement(FlowFrameSheet as any, { params: { node: TARGET_ID, open: OPEN }, depth: 0 }));
  });
  await until("the Target modal", () => modal() != null);
  let backs = 0;
  const realBack = win.history.back;
  win.history.back = () => { backs++; };
  try {
    click(cancelButton());
    await flush();
  } finally {
    win.history.back = realBack;
  }
  eq(backs, 0, "a late close called the browser's Back with no sheet open - it would leave the canvas");
  eq(hash(), `#/session/flows?open=${OPEN}`, "a late close changed the route");
  await unmountAll();
});

// ========================================================== 5. no fork
//
// MUTANT "a #/next copy of the modal" (FlowFrameSheet.tsx defines its own
// `TargetFramingSheetLazy` component rendering a div with the modal's marker,
// and drops the shared import). Observed ("flowFrameSheet.test: 10/16 passed"):
//   x loading the sheet resolves the shared door, and still not the modal: the sheet did not reach the shared modal's door
//   x from flowStages -> flowNode, FRAME ON SKY opens flowFrame in flowNode's place and keeps ?open=: the modal is not framing the TARGET M31
//   x the sheet mounts the shared modal through its lazy door, with no fork: FlowFrameSheet.tsx does not import TargetFramingSheetLazy from the shared door: ["react","../../../../../store","../../../../router","../../../../ui","../../../sheets"]
//   x while ?open= names a flow the store does not hold, the sheet waits, and frames that flow once it loads: the modal is not framing the route's flow's M31: "FRAME t1CANCEL"
//   x with no ?open= the sheet frames the open flow, as before: the modal is not framing the unsaved flow's M31
//   x on a phone, while DONE writes, neither CANCEL nor Escape pops flowFrame, and DONE's own close pops it once: no input to type into
await test("the sheet mounts the shared modal through its lazy door, with no fork", async () => {
  const { readFileSync, readdirSync } = await import("node:fs");
  const dir = new URL("../", import.meta.url);
  const src = readFileSync(new URL("FlowFrameSheet.tsx", dir), "utf8");
  const imports = Array.from(src.matchAll(/^import\s+([^;]*?)\s+from\s+"([^"]+)";/gm));
  const specs = imports.map((m) => m[2]);
  const door = imports.find((m) => m[2] === "../../../../../components/flows/framing");
  assert(door != null && /\bTargetFramingSheetLazy\b/.test(door[1]),
    `FlowFrameSheet.tsx does not import TargetFramingSheetLazy from the shared door: ${JSON.stringify(specs)}`);
  // Nothing in this area draws framing of its own: no Overlay, no model, no
  // stylesheet of the modal's.
  for (const f of readdirSync(dir).filter((n: string) => /\.tsx?$/.test(n))) {
    // Comments name these modules on purpose (to say why they are not here),
    // so only the code is read.
    const text = String(readFileSync(new URL(f, dir), "utf8"))
      .replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
    for (const banned of ["components/Overlay", "framingModel", "framing.css", "SkyCanvas"]) {
      assert(!text.includes(banned), `${f} reaches ${banned}: the #/next area is forking the modal`);
    }
  }
});

// ================================ 6. the flow the route names (#384)
//
// The modal finds its node by id in whatever flow the store holds, and every
// copy of an Example has the same ids. The defect #384 records: with flow B
// open, a link to flow A's framing mounted the modal on B's stage at once;
// A then loaded under it, and the modal kept B's draft under the same key,
// so DONE would have written B's target onto A. Graded at tablet width,
// where SheetHost shows the top sheet alone, so nothing under the sheet
// loads a flow and the test decides when "that flow has loaded".
const ELSEWHERE = "copy-of-quick-m31";
const FRAME_AT = `#/session/flows/${FLOW_FRAME_SHEET}?node=${TARGET_ID}&open=${OPEN}`;
const waitCard = (): any => q("flow-frame-other-flow");

// MUTANT "modal mounted for another flow" (FlowFrameSheet.tsx never waits:
// its `open !== "" && open !== recordId` test becomes `false && ...`, the
// sheet as it stood before #384). Observed ("flowFrameSheet.test: 14/16 passed"):
//   x while ?open= names a flow the store does not hold, the sheet waits, and frames that flow once it loads: the modal mounted on the other flow's t1 under a route naming quick-m31: "CANCELFRAME M33DONEMOVE "
//   x with no flow open the wait says the link's flow is loading, and its BACK leaves the sheet: timed out waiting for the wait
//
// MUTANT "other-flow copy never chosen" (the wait's hint is always
// FRAME_FLOW_LOADING). Observed ("flowFrameSheet.test: 15/16 passed"):
//   x while ?open= names a flow the store does not hold, the sheet waits, and frames that flow once it loads: the wait does not say another flow is open: "FRAMEThis framing opens once the flow the link names has loaded.BACK"

await test("while ?open= names a flow the store does not hold, the sheet waits, and frames that flow once it loads", async () => {
  // Another flow is open, and its stage has the same id, framing M33.
  seed({ id: ELSEWHERE, target: "M33" });
  await mountAt(FRAME_AT, false);
  await until("the framing sheet", () => q("session-flow-frame") != null);
  await settleChunks();
  assert(modal() == null,
    `the modal mounted on the other flow's ${TARGET_ID} under a route naming ${OPEN}: `
    + `"${String(modal()?.textContent ?? "").slice(0, 24)}"`);
  assert(waitCard() != null, "the sheet shows no wait while another flow is open");
  assert(String(waitCard().textContent).includes(FRAME_OTHER_FLOW),
    `the wait does not say another flow is open: "${String(waitCard().textContent)}"`);

  // The flow the route names loads: the modal mounts on ITS stage.
  seed();
  await until("the Target modal", () => modal() != null);
  assert(String(modal().textContent).includes("FRAME M31"),
    `the modal is not framing the route's flow's M31: "${String(modal().textContent).slice(0, 24)}"`);
  eq(waitCard(), null, "the wait stayed up once the route's flow had loaded");

  // Another flow replaces it under the open modal: the modal goes, by the
  // same test, rather than carry one flow's draft onto the other's stage.
  seed({ id: ELSEWHERE, target: "M33" });
  await flush();
  assert(modal() == null,
    "a flow that changed under the open modal left the modal up, now over another flow's stage");
  assert(waitCard() != null, "the wait did not come back when the flow changed under the modal");
  await unmountAll();
});

// MUTANT "loading copy never chosen" (the wait's hint is always
// FRAME_OTHER_FLOW, the sheet as #384's first fix left it). Observed
// ("flowFrameSheet.test: 15/16 passed"):
//   x with no flow open the wait says the link's flow is loading, and its BACK leaves the sheet: the wait does not say the link's flow is loading: "FRAMEThis framing belongs to the flow the link names, and another flow is open. It opens once that flow has loaded.BACK"
//
// MUTANT "wait has no BACK" (the wait card loses its `action`). Observed
// ("flowFrameSheet.test: 15/16 passed"):
//   x with no flow open the wait says the link's flow is loading, and its BACK leaves the sheet: the wait offers no BACK
await test("with no flow open the wait says the link's flow is loading, and its BACK leaves the sheet", async () => {
  // A link loaded fresh (or to a flow since deleted): nothing is open yet.
  seed({ id: null });
  act(() => {
    useStore.setState({ flows: { ...useStore.getState().flows, record: null, graph: { nodes: [], edges: [] } } } as never);
  });
  await mountAt(FRAME_AT, false);
  await until("the wait", () => waitCard() != null);
  const text = String(waitCard().textContent);
  assert(text.includes(FRAME_FLOW_LOADING), `the wait does not say the link's flow is loading: "${text}"`);
  assert(!text.includes(FRAME_OTHER_FLOW), "the wait says another flow is open when none is");
  assert(modal() == null, "the modal mounted with no flow open");

  // On a phone the slot is bare, so the card's own BACK is the way out of a
  // link whose flow never loads.
  const back = q("flow-frame-other-flow-back");
  assert(back != null, "the wait offers no BACK");
  click(back);
  await flush();
  eq(hash(), "#/session/flows", "the wait's BACK did not leave the framing sheet");
  await unmountAll();
});

// CONTROL. With no `?open=` (an unsaved flow, which has no id to name) the
// sheet frames the open flow, as it did before #384.
//
// MUTANT "no ?open= waits too" (the test becomes `open !== recordId`, so an
// empty `?open=` waits on an unsaved flow's null id). Observed
// ("flowFrameSheet.test: 15/16 passed"):
//   x with no ?open= the sheet frames the open flow, as before: timed out waiting for the Target modal
await test("with no ?open= the sheet frames the open flow, as before", async () => {
  seed({ id: null });
  await mountAt(`#/session/flows/${FLOW_FRAME_SHEET}?node=${TARGET_ID}`, false);
  await until("the Target modal", () => modal() != null);
  assert(String(modal().textContent).includes("FRAME M31"), "the modal is not framing the unsaved flow's M31");
  eq(waitCard(), null, "an unsaved flow's framing waits for a flow the route never named");
  await unmountAll();
});

// ============ 7. a close while DONE writes, at the SheetHost seam (#382)
//
// DONE writes the framing and then waits for the compile before it closes.
// By then the write has happened, so CANCEL and Escape can no longer mean
// "write nothing", and the modal refuses both and says why. `SheetHost` binds
// Escape itself, to `nav.back()` on the window: the refusal holds in #/next
// only while the modal's Overlay takes the key first and stops it. So this
// runs through the real SheetHost on a phone, with the compile held open.
const PHONE_FRAME_AT = `#/session/flows/flowStages/${FLOW_FRAME_SHEET}?node=${TARGET_ID}&open=${OPEN}`;
const PHONE_STAGES_AT = `#/session/flows/flowStages?node=${TARGET_ID}&open=${OPEN}`;

// MUTANT "SheetHost takes Escape first" (SheetHost.tsx binds its Escape on
// the window in the CAPTURE phase, which runs before the Overlay's listener
// on `document`). Observed ("flowFrameSheet.test: 15/16 passed"):
//   x on a phone, while DONE writes, neither CANCEL nor Escape pops flowFrame, and DONE's own close pops it once: Escape while DONE writes popped flowFrame: SheetHost's own Escape got past the modal's refusal
//     expected "#/session/flows/flowStages/flowFrame?node=t1&open=quick-m31"
//     got      "#/session/flows/flowStages?node=t1&open=quick-m31"
//
// MUTANT "Overlay lets Escape through" (components/Overlay.tsx's Escape
// branch loses `e.stopPropagation()`). Observed
// ("flowFrameSheet.test: 14/16 passed"):
//   x on a phone, while DONE writes, neither CANCEL nor Escape pops flowFrame, and DONE's own close pops it once: Escape while DONE writes popped flowFrame: SheetHost's own Escape got past the modal's refusal
//     expected "#/session/flows/flowStages/flowFrame?node=t1&open=quick-m31"
//     got      "#/session/flows/flowStages?node=t1&open=quick-m31"
//   x on a phone with nothing in flight, Escape closes the modal back to the stage list: Escape did not pop exactly flowFrame
//     expected "#/session/flows/flowStages?node=t1&open=quick-m31"
//     got      "#/session/flows"
// (The control goes red too: the modal's close and SheetHost's each pop a
// sheet, so one Escape leaves the stage list as well.)
//
// MUTANT "CANCEL live while DONE writes" (TargetFramingSheet.tsx's
// `cancelReason` is never CHECKING). Observed ("flowFrameSheet.test: 15/16 passed"):
//   x on a phone, while DONE writes, neither CANCEL nor Escape pops flowFrame, and DONE's own close pops it once: CANCEL while DONE writes popped flowFrame
//     expected "#/session/flows/flowStages/flowFrame?node=t1&open=quick-m31"
//     got      "#/session/flows/flowStages?node=t1&open=quick-m31"

await test("on a phone, while DONE writes, neither CANCEL nor Escape pops flowFrame, and DONE's own close pops it once", async () => {
  seed();
  await mountAt(PHONE_FRAME_AT);
  await until("the Target modal", () => modal() != null);
  typeInto(doc.querySelector("#tfs-name"), "M31 wide");
  assert(doneButton() != null && !doneButton().disabled && doneButton().getAttribute("aria-disabled") !== "true",
    "precondition: DONE is not pressable offline with a name typed");

  let release: () => void = () => {};
  compileGate = new Promise<void>((r) => { release = r; });
  try {
    click(doneButton());
    await flush();
    eq(storedName(), "M31 wide", "precondition: DONE did not write the framing before its compile");

    click(cancelButton());
    await flush();
    eq(hash(), PHONE_FRAME_AT, "CANCEL while DONE writes popped flowFrame");

    pressEscape();
    await flush();
    eq(hash(), PHONE_FRAME_AT,
      "Escape while DONE writes popped flowFrame: SheetHost's own Escape got past the modal's refusal");
    assert(modal() != null, "the modal closed while DONE was still writing");
  } finally {
    release();
    compileGate = null;
  }
  await until("DONE's close", () => modal() == null);
  eq(hash(), PHONE_STAGES_AT, "DONE's close did not pop exactly flowFrame, back to the stage list with its flow");
  eq(q("session-flow-stages")?.closest(".nx-sheet-slot")?.getAttribute("data-under"), "false",
    "the stage list is not on top after DONE's close");
  eq(storedName(), "M31 wide", "the name DONE wrote");
  await unmountAll();
});

// CONTROL. With nothing in flight, Escape closes the modal and pops exactly
// flowFrame: the modal's close, not SheetHost's, and only one of them.
await test("on a phone with nothing in flight, Escape closes the modal back to the stage list", async () => {
  seed();
  await mountAt(PHONE_FRAME_AT);
  await until("the Target modal", () => modal() != null);
  pressEscape();
  await until("the modal to close", () => modal() == null);
  await flush();
  eq(hash(), PHONE_STAGES_AT, "Escape did not pop exactly flowFrame");
  eq(q("session-flow-stages")?.closest(".nx-sheet-slot")?.getAttribute("data-under"), "false",
    "the stage list is not on top after Escape");
  await unmountAll();
});

// ============================================ 8. run mode is the door's
//
// The progress route's recorded answer for a flow with a dormant session, and
// the rig's recorded state of a run writing that very session, READ, NOT
// COPIED (server/tests/test_s5_recorded_state.py rebuilds both byte for byte).
// They name the session, not this file's flow, and that is all the door reads.
async function readFixture(name: string): Promise<any> {
  const { readFileSync } = await import("node:fs");
  const rel = `../../../../../../../../server/tests/fixtures/${name}`;
  try {
    return JSON.parse(String(readFileSync(new URL(rel, import.meta.url), "utf8")));
  } catch (e) {
    throw new Error(`cannot read ${rel}, a recorded answer the run-mode case is graded against: `
      + `${(e as Error).message}`);
  }
}

// MUTANT "the doors never pass viewOnly" (FlowFrameSheet.tsx mounts
// `<TargetFramingSheetLazy nodeId={params.node ?? ""} onClose={closeFrame} />`,
// the adapter as S4 left it). Run in scratchpad S5-RUNMODE-mut, 2026-09-28.
// Observed ("flowFrameSheet.test: 17/18 passed"):
//   x while the open flow's session runs, the sheet opens the modal read-only in run mode; a dormant session opens it editable: the modal's view mode while the flow runs
//     expected "true"
//     got      null
await test("while the open flow's session runs, the sheet opens the modal read-only in run mode; a dormant session opens it editable", async () => {
  const progress = (await readFixture("flow_progress_continue.json")).response;
  const running = (await readFixture("sequence_state_mosaic.json")).states.shooting;
  eq(running.session.id, progress.session.id, "premise: the run writes the progress answer's session");
  const { RUNNING_VIEW_ONLY } = await import("../../../../../../components/flows/framing/TargetFramingSheet");
  const reason = (): string | null => q("framing-view-why")?.textContent ?? null;

  // CONTROL: the session dormant, the rig idle. The modal edits.
  seed({ progress });
  await mountAt(FRAME_AT, false);
  await until("the Target modal", () => modal() != null);
  eq(modal().getAttribute("data-view"), null, "the modal's view mode over a dormant session");
  eq(reason(), null, "the modal's reason over a dormant session");
  assert(doneButton() != null, "the modal over a dormant session offers no DONE");
  await unmountAll();

  // CONTROL: the rig running ANOTHER flow's session (the recorded run, its
  // session id changed). The modal edits: a run is this flow's by the session
  // id the progress route counts, never because the rig is busy. MUTANT "the
  // door asks whether ANY run is live" (FlowFrameSheet.tsx reads
  // `s.sequence?.state === "running"` in place of `flowRunLive`), green before
  // this control existed; observed (verifier's scratch copy
  // S5-RUNMODE-verify-mut, 2026-09-28, "flowFrameSheet.test: 17/18 passed"):
  //   x while the open flow's session runs, the sheet opens the modal read-only in run mode; a dormant session opens it editable: the modal's view mode under another flow's run
  //     expected null
  //     got      "true"
  seed({ progress, sequence: { ...running, session: { ...running.session, id: "another-flows-session" } } });
  await mountAt(FRAME_AT, false);
  await until("the Target modal", () => modal() != null);
  eq(modal().getAttribute("data-view"), null, "the modal's view mode under another flow's run");
  eq(reason(), null, "the modal's reason under another flow's run");
  assert(doneButton() != null, "the modal under another flow's run offers no DONE");
  await unmountAll();

  // The flow's session running: the same sheet opens run mode.
  seed({ progress, sequence: running });
  await mountAt(FRAME_AT, false);
  await until("the Target modal", () => modal() != null);
  eq(modal().getAttribute("data-view"), "true", "the modal's view mode while the flow runs");
  eq(reason(), RUNNING_VIEW_ONLY, "the modal's reason while the flow runs");
  eq(doneButton() ?? null, null, "the modal offers DONE while the flow runs");
  eq(doc.querySelector(".tfs-fieldset")?.disabled, true, "the modal's controls while the flow runs");
  await unmountAll();
});

await unmountAll();

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`flowFrameSheet.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) {
  (globalThis as unknown as { process?: { exitCode?: number } }).process!.exitCode = 1;
}
export default { passed, failed, total };
export { passed, failed, total };
