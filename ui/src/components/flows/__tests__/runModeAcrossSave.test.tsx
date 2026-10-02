// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// runModeAcrossSave.test.tsx - run mode, the RUN button and the readouts hold
// through the progress re-read a save starts (#449; spec 2.6 run mode, 5.9,
// 5.10).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/__tests__/runModeAcrossSave.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT. Both Target modal doors, the RUN button and the run readouts
// decided whether the open flow's run was live from `flows.progress`, and the
// slice clears that answer at the start of every progress read that is not a
// live refresh: the read an open, a save and a RUN each start. So a save made
// while the flow's session ran turned the open modal editable for the length
// of that read (DONE offered over a night in progress, the panels' run states
// gone), RUN read RUN, the readouts dropped to IDLE, and a read that failed
// (422 for a saved graph that no longer compiles, 404 from a server older than
// S1) left all of them so for the rest of the run. #449's probe, on the real
// slice with the progress read held open:
//
//   run mode before the save: true
//   run mode while the save's progress read is in flight: false | flows.progress: null
//   run mode once the read answered: true
//
// Since #449 every one of them asks flowRunState `flowRunLive` of the sessions
// the slice knows as the open flow's (`flows.sessionIds`, through
// `knownSessions`): added on every progress answer and on RUN's, kept across a
// save and a failed read, replaced when another flow opens, dropped on close.
//
// WHAT IS GRADED, with the real store, the real slice (only `flowsApi` is
// stubbed), the real doors and the real monitor:
//
//   1. THE ROUND TRIP, AT BOTH DOORS. The classic editor's FRAME ON SKY
//      (FlowEditor `FramingHostSheet`) and the #/next `flowFrame` sheet
//      (FlowFrameSheet.tsx), each beside the classic phone MONITOR (the RUN
//      button, flowRunControls `useFlowRunControls`; the STATE readout,
//      `useFlowRunReadouts`, runCopy `runReadouts`). The run live, a save with
//      its progress read HELD OPEN: the modal stays read-only, RUN stays STOP
//      and STATE stays RUNNING before, during and after, and a recorder on the
//      two hooks sees no render with either off.
//   2. A READ THAT FAILS (422) during the run keeps all three.
//   3. THE LIST IS THE OPEN FLOW'S: RUN's own session is run mode before any
//      answer names it; another flow opened while this one's run is live
//      opens editable; a close empties the list; the same flow opened again
//      keeps run mode through that open's clearing read.
//   4. THE CONTROL that makes 1 and 2 able to fail: the run ending turns the
//      same modal editable, RUN back to CONTINUE and STATE to the store's
//      idle value, all read the same way.
//
// Every mutant below was run in a private scratch copy of ui/ (scratchpad
// S7-USLICE-mut, #254, #475), never in the shared tree, and the failure it
// produced is quoted verbatim.

/* eslint-disable @typescript-eslint/no-explicit-any */

// @ts-ignore  node built-ins; tsx supplies them at runtime
import { readFileSync } from "node:fs";

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
// Every route the store or the sheet asks is replaced below; one that slips
// past fails loudly.
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
const { createElement, act, Fragment } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../store");
const { FLOWS_INIT } = await import("../flowsSlice");
const { flowsApi } = await import("../../../lib/flowsApi");
const { NODE_DEFS } = await import("../nodeDefs");
const { framingApi, framingTiming } = await import("../framing/framingApi");
const { loadTargetFramingSheet } = await import("../framing");
const { RUNNING_VIEW_ONLY } = await import("../framing/TargetFramingSheet");
const FlowEditor = (await import("../FlowEditor")).default;
const FlowPhoneMonitor = (await import("../FlowPhoneMonitor")).default;
const { useFlowRunControls, useFlowRunReadouts } = await import("../flowRunControls");
const { FlowFrameSheet } = await import("../../../next/hubs/session/flows/framing/FlowFrameSheet");
type FlowNodeRec = import("../flowsTypes").FlowNodeRec;

// The modal's chunk, loaded once, so both doors' React.lazy resolve on their
// first render rather than racing tsx's first compile of the module.
await loadTargetFramingSheet();

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
    throw new Error(`${msg}\n  expected ${JSON.stringify(want)}\n  got      ${JSON.stringify(got)}`);
  }
}

// ------------------------------------------------------------------ fixtures
// The progress route's recorded answer for a flow whose session is dormant,
// and the rig's recorded state of a run writing that very session, READ, NOT
// COPIED (server/tests/test_s5_recorded_state.py rebuilds both byte for byte).
function readFixture(name: string): any {
  const rel = `../../../../../server/tests/fixtures/${name}`;
  try {
    return JSON.parse(readFileSync(new URL(rel, import.meta.url), "utf8") as string);
  } catch (e) {
    throw new Error(`cannot read ${rel}, a recorded answer this file is graded against: `
      + `${(e as Error).message}`);
  }
}
const PROGRESS = readFixture("flow_progress_continue.json").response;
const RUNNING = readFixture("sequence_state_mosaic.json").states.shooting;
const SESSION: string = PROGRESS.session.id;
const FLOW: string = PROGRESS.flow_id;
const BLOCK: string = PROGRESS.blocks[0].node_id;
const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v));

/** The recorded flow's block as a graph: the progress answer's TARGET (a 3x2
 *  "M31"), framed, and a FILTER CYCLE looping back to "next panel". */
function graph(): any {
  const target: FlowNodeRec = {
    id: BLOCK, type: "target", x: 0, y: 0,
    params: { ...NODE_DEFS.target.params, name: "M31", ra: "00h 42m 44s", dec: "+41 16 09",
      rows: 2, cols: 3, overlap: 25, fovX: 2.0, fovY: 1.33, rotation: 0,
      angle: "Rotate to PA", counts: "Accepted subs" },
  };
  return {
    nodes: [target, { id: "cy", type: "cycle", x: 260, y: 0, params: { ...NODE_DEFS.cycle.params } }],
    edges: [
      { id: "lane", from: BLOCK, fromPort: "target", to: "cy", toPort: "run" },
      { id: "loop", from: "cy", fromPort: "pass", to: BLOCK, toPort: "next" },
    ],
  };
}
const OTHER = "another-flow";

// ------------------------------------------------------------------ the network
/** How the next progress read answers: the recorded answer, held open until
 *  the test settles it, or refused with a status. */
let progressMode: "answer" | "hold" | { status: number } = "answer";
let progressReads: string[] = [];
let held: { resolve: (v: any) => void; reject: (e: any) => void } | null = null;
(flowsApi as any).progress = (id: string) => {
  progressReads.push(id);
  const mode = progressMode;
  if (mode === "hold") return new Promise((resolve, reject) => { held = { resolve, reject }; });
  if (typeof mode === "object") {
    return Promise.reject(Object.assign(new Error(`${mode.status} from the progress route`), { status: mode.status }));
  }
  // Only the recorded flow has an answer; another flow never ran.
  return Promise.resolve(id === FLOW ? clone(PROGRESS) : { ...clone(PROGRESS), flow_id: id, session: null });
};
let puts: string[] = [];
(flowsApi as any).save = async (id: string, flow: any) => { puts.push(id); return clone({ ...flow, id }); };
(flowsApi as any).get = async (id: string) => ({
  id, name: id === FLOW ? "M31 mosaic" : "Another flow", folder: "My flows", tagline: "",
  graph: graph(), created_ts: 0, updated_ts: 0, last_run: null, last_result: "", readonly: false,
});
(flowsApi as any).compileDraft = async () => ({ plan: {}, structural: [], issues: [], unmapped: [] });
/** RUN's answer names a session no progress answer has named yet (START
 *  OVER, or a first night). */
const FRESH = "fresh-session-from-run";
(flowsApi as any).run = async () => ({ started: true, flow_id: FLOW, frames: 120, unmapped: [],
  session: { id: FRESH, continued: false, new: 6 } });
(framingApi as any).mosaic = () => new Promise(() => {});
(framingApi as any).compileDraft = async () => null;
framingTiming.settleMs = 0;
useStore.setState({ flowsFetchCalHealth: async () => {}, flowsFetchTonight: async () => {} } as any);

// ------------------------------------------------------------------ the store
/** An operator with a camera, the rig idle, nothing open: then the REAL
 *  `flowsOpen`, so the list is whatever the slice itself noted. */
async function openRecorded(): Promise<void> {
  act(() => {
    useStore.setState({
      principal: { role: "operator", email: null, caps: ["view.status", "control.mount", "control.capture"] },
      authGate: "open",
      wsConnected: false,
      wsPhase: "up",
      status: { connected: { camera: { connected: true } }, sky_angle: null } as any,
      sequence: { state: "idle" } as any,
      config: null,
      site: null,
      toasts: [],
      confirm: null,
      flows: { ...FLOWS_INIT, ui: { ...FLOWS_INIT.ui } },
    } as any);
  });
  progressMode = "answer";
  progressReads = [];
  puts = [];
  held = null;
  await act(async () => { await useStore.getState().flowsOpen(FLOW); });
  await flush();
  eq(useStore.getState().flows.record?.id, FLOW, "precondition: the recorded flow did not open");
  eq(useStore.getState().flows.progress?.session?.id, SESSION, "precondition: the recorded answer is not in hand");
}
function runLive(live = true): void {
  act(() => { useStore.setState({ sequence: live ? clone(RUNNING) : { ...clone(RUNNING), state: "complete" } } as any); });
}

// ------------------------------------------------------------------ mounting
const container = win.document.getElementById("root");
const root = createRoot(container);
const doc = win.document;
async function flush(): Promise<void> {
  for (let i = 0; i < 6; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
}
function unmount(): void { act(() => { root.render(null); }); }
function click(el: any): void {
  assert(el, "no element to click");
  act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
}

/** Every render of the RUN button's hook and the readouts' hook, as
 *  "running/fed", so a round trip is graded at every render and not only at
 *  the moments the test looks. */
let renders: string[] = [];
function Recorder(): null {
  const { running } = useFlowRunControls();
  const { fed } = useFlowRunReadouts();
  renders.push(`${running}/${fed}`);
  return null;
}

type Door = "classic" | "next";
/** One door, beside the classic phone MONITOR and the recorder. */
async function mountDoor(door: Door): Promise<void> {
  unmount();
  if (door === "classic") act(() => { useStore.getState().flowsSelect({ kind: "node", id: BLOCK }); });
  const doorEl = door === "classic"
    ? createElement(FlowEditor as any, { tier: "desktop" })
    : createElement(FlowFrameSheet as any, { params: { open: FLOW, node: BLOCK }, depth: 0 });
  act(() => {
    root.render(createElement(Fragment, null,
      createElement("div", { key: "door", "data-box": "door" }, doorEl),
      createElement("div", { key: "monitor", "data-box": "monitor" }, createElement(FlowPhoneMonitor as any)),
      createElement(Recorder, { key: "rec" })));
  });
  await flush();
  if (door === "classic") {
    click(doc.querySelector("[data-flows-frame]"));
    await flush();
  }
  assert(sheet(), `the ${door} door mounted no Target modal`);
}
const sheet = () => doc.querySelector('[data-testid="target-framing-sheet"]') as any;
const monitor = () => doc.querySelector('[data-box="monitor"]') as any;

/** What the operator sees, as #449's probe wrote it: whether the modal is in
 *  run mode (read-only with the running reason, no DONE, the controls off),
 *  what RUN says and the STATE readout, and whether an answer is in hand. */
function probe(label: string): string {
  const s = sheet();
  const why = (doc.querySelector('[data-testid="framing-view-why"]') as any)?.textContent ?? null;
  const runMode = s?.getAttribute("data-view") === "true" && why === RUNNING_VIEW_ONLY
    && doc.querySelector('[data-testid="framing-done"]') === null
    && (doc.querySelector(".tfs-fieldset") as any)?.disabled === true;
  const button = Array.from(monitor().querySelectorAll("button") as any[])
    .find((b: any) => /\b(RUN|STOP|CONTINUE)\b/.test(b.textContent));
  const verb = /\b(RUN|STOP|CONTINUE)\b/.exec(button?.textContent ?? "")?.[1] ?? "(no RUN button)";
  const state = (monitor().querySelector('[data-testid="flow-monitor-state"] .font-mono') as any)?.textContent ?? "(no STATE)";
  const progress = useStore.getState().flows.progress === null ? "null" : "answer";
  return `run mode ${label}: ${runMode} | RUN: ${verb} | STATE: ${state} | flows.progress: ${progress}`;
}
const LIVE = (label: string, progress: "null" | "answer") =>
  `run mode ${label}: true | RUN: STOP | STATE: RUNNING | flows.progress: ${progress}`;

// ======================================================================
// 1. the round trip, at both doors

// MUTANT "read off flows.progress" (flowRunState.ts `knownSessions` answering
// the progress answer's session, `const id = (flows as any)?.progress?.session
// ?.id; return typeof id === "string" && id !== "" ? [id] : NONE;`, which is
// every reader as #449 found them). #449's probe line, word for word, at both
// doors; RUN reads RUN and STATE the store's IDLE with it. Observed
// ("runModeAcrossSave.test: 2/7 passed"):
//   x classic door: run mode, RUN and the readouts hold through a save's progress read: while the save's progress read is in flight
//   expected "run mode while the save's progress read is in flight: true | RUN: STOP | STATE: RUNNING | flows.progress: null"
//   got      "run mode while the save's progress read is in flight: false | RUN: RUN | STATE: IDLE | flows.progress: null"
//   x next door: run mode, RUN and the readouts hold through a save's progress read: while the save's progress read is in flight
//   (the same two lines)
//   x a progress read that fails with 422 during the run keeps run mode, RUN and the readouts: after the save's read failed with 422
//   expected "run mode after the save's read failed with 422: true | RUN: STOP | STATE: RUNNING | flows.progress: null"
//   got      "run mode after the save's read failed with 422: false | RUN: RUN | STATE: IDLE | flows.progress: null"
//   x RUN's own session is run mode before any answer names it, and through its clearing read: while RUN's progress read is in flight
//   expected "run mode while RUN's progress read is in flight: true | RUN: STOP | STATE: RUNNING | flows.progress: null"
//   got      "run mode while RUN's progress read is in flight: false | RUN: STOP | STATE: RUNNING | flows.progress: null"
//   x the same flow opened again keeps run mode through its clearing read: while the reopen's progress read is in flight
//   expected "run mode while the reopen's progress read is in flight: true | RUN: STOP | STATE: RUNNING | flows.progress: null"
//   got      "run mode while the reopen's progress read is in flight: false | RUN: RUN | STATE: IDLE | flows.progress: null"
//
// And each reader alone, the other three left on the list:
//
// MUTANT "the classic door reads flows.progress" (FlowEditor.tsx
// `FramingHostSheet`: `flowRunLive(s.flows.progress ? [s.flows.progress.session?.id
// ?? ""] : [], s.sequence)`). Observed ("runModeAcrossSave.test: 5/7 passed"):
//   x classic door: run mode, RUN and the readouts hold through a save's progress read: while the save's progress read is in flight
//   expected "run mode while the save's progress read is in flight: true | RUN: STOP | STATE: RUNNING | flows.progress: null"
//   got      "run mode while the save's progress read is in flight: false | RUN: STOP | STATE: RUNNING | flows.progress: null"
//   x a progress read that fails with 422 during the run keeps run mode, RUN and the readouts: after the save's read failed with 422
//   expected "run mode after the save's read failed with 422: true | RUN: STOP | STATE: RUNNING | flows.progress: null"
//   got      "run mode after the save's read failed with 422: false | RUN: STOP | STATE: RUNNING | flows.progress: null"
//
// MUTANT "the #/next door reads flows.progress" (FlowFrameSheet.tsx, the same
// selector). Observed ("runModeAcrossSave.test: 4/7 passed"):
//   x next door: run mode, RUN and the readouts hold through a save's progress read: while the save's progress read is in flight
//   expected "run mode while the save's progress read is in flight: true | RUN: STOP | STATE: RUNNING | flows.progress: null"
//   got      "run mode while the save's progress read is in flight: false | RUN: STOP | STATE: RUNNING | flows.progress: null"
//   x RUN's own session is run mode before any answer names it, and through its clearing read: while RUN's progress read is in flight
//   expected "run mode while RUN's progress read is in flight: true | RUN: STOP | STATE: RUNNING | flows.progress: null"
//   got      "run mode while RUN's progress read is in flight: false | RUN: STOP | STATE: RUNNING | flows.progress: null"
//   x the same flow opened again keeps run mode through its clearing read: while the reopen's progress read is in flight
//   expected "run mode while the reopen's progress read is in flight: true | RUN: STOP | STATE: RUNNING | flows.progress: null"
//   got      "run mode while the reopen's progress read is in flight: false | RUN: STOP | STATE: RUNNING | flows.progress: null"
//
// MUTANT "RUN reads flows.progress" (flowRunControls.tsx `ours`, the same
// selector). Observed ("runModeAcrossSave.test: 3/7 passed"):
//   x classic door: run mode, RUN and the readouts hold through a save's progress read: while the save's progress read is in flight
//   expected "run mode while the save's progress read is in flight: true | RUN: STOP | STATE: RUNNING | flows.progress: null"
//   got      "run mode while the save's progress read is in flight: true | RUN: RUN | STATE: RUNNING | flows.progress: null"
//   x next door: run mode, RUN and the readouts hold through a save's progress read: while the save's progress read is in flight
//   (the same two lines)
//   x a progress read that fails with 422 during the run keeps run mode, RUN and the readouts: after the save's read failed with 422
//   expected "run mode after the save's read failed with 422: true | RUN: STOP | STATE: RUNNING | flows.progress: null"
//   got      "run mode after the save's read failed with 422: true | RUN: RUN | STATE: RUNNING | flows.progress: null"
//   x the same flow opened again keeps run mode through its clearing read: while the reopen's progress read is in flight
//   expected "run mode while the reopen's progress read is in flight: true | RUN: STOP | STATE: RUNNING | flows.progress: null"
//   got      "run mode while the reopen's progress read is in flight: true | RUN: RUN | STATE: RUNNING | flows.progress: null"
//
// MUTANT "the readouts read flows.progress" (flowRunControls.tsx
// `useFlowRunReadouts` hands `runReadouts` `s.flows.progress`, which its
// transitional answer form still takes). Observed ("runModeAcrossSave.test:
// 3/7 passed"):
//   x classic door: run mode, RUN and the readouts hold through a save's progress read: while the save's progress read is in flight
//   expected "run mode while the save's progress read is in flight: true | RUN: STOP | STATE: RUNNING | flows.progress: null"
//   got      "run mode while the save's progress read is in flight: true | RUN: STOP | STATE: IDLE | flows.progress: null"
//   x next door: run mode, RUN and the readouts hold through a save's progress read: while the save's progress read is in flight
//   (the same two lines)
//   x a progress read that fails with 422 during the run keeps run mode, RUN and the readouts: after the save's read failed with 422
//   expected "run mode after the save's read failed with 422: true | RUN: STOP | STATE: RUNNING | flows.progress: null"
//   got      "run mode after the save's read failed with 422: true | RUN: STOP | STATE: IDLE | flows.progress: null"
//   x the same flow opened again keeps run mode through its clearing read: while the reopen's progress read is in flight
//   expected "run mode while the reopen's progress read is in flight: true | RUN: STOP | STATE: RUNNING | flows.progress: null"
//   got      "run mode while the reopen's progress read is in flight: true | RUN: STOP | STATE: IDLE | flows.progress: null"
for (const door of ["classic", "next"] as const) {
  await test(`${door} door: run mode, RUN and the readouts hold through a save's progress read`, async () => {
    await openRecorded();
    runLive();
    await mountDoor(door);
    eq(probe("before the save"), LIVE("before the save", "answer"), "before the save");

    // An edit, and SAVE with its progress read held open.
    act(() => { useStore.getState().flowsSetName("M31 mosaic, renamed mid-run"); });
    progressMode = "hold";
    renders = [];
    const reads = progressReads.length;
    await act(async () => { await useStore.getState().flowsSave(); });
    await flush();
    eq(puts, [FLOW], "precondition: the save sent its PUT");
    eq(progressReads.length, reads + 1, "precondition: the save started its progress read");
    assert(held !== null, "precondition: the save's progress read is not held open");
    eq(probe("while the save's progress read is in flight"),
      LIVE("while the save's progress read is in flight", "null"),
      "while the save's progress read is in flight");

    await act(async () => { held!.resolve(clone(PROGRESS)); await Promise.resolve(); });
    await flush();
    eq(probe("once the read answered"), LIVE("once the read answered", "answer"), "once the read answered");
    assert(renders.length > 0, "precondition: the recorder saw no render across the round trip");
    eq(renders.filter((r) => r !== "true/true"), [],
      "renders of the RUN button's and the readouts' hooks across the round trip that were not running/fed");
  });
}

// ======================================================================
// 2. a read that fails keeps run mode

// MUTANT "a failed read forgets the sessions" (flowsSlice.ts fetchProgress's
// catch writes `sessionIds: NO_SESSIONS`). Observed ("runModeAcrossSave.test:
// 6/7 passed"):
//   x a progress read that fails with 422 during the run keeps run mode, RUN and the readouts: after the save's read failed with 422
//   expected "run mode after the save's read failed with 422: true | RUN: STOP | STATE: RUNNING | flows.progress: null"
//   got      "run mode after the save's read failed with 422: false | RUN: RUN | STATE: IDLE | flows.progress: null"
await test("a progress read that fails with 422 during the run keeps run mode, RUN and the readouts", async () => {
  await openRecorded();
  runLive();
  await mountDoor("classic");
  eq(probe("before the save"), LIVE("before the save", "answer"), "before the save");
  act(() => { useStore.getState().flowsSetName("M31 mosaic, renamed mid-run"); });
  progressMode = { status: 422 };
  await act(async () => { await useStore.getState().flowsSave(); });
  await flush();
  eq(puts, [FLOW], "precondition: the save sent its PUT");
  eq(probe("after the save's read failed with 422"), LIVE("after the save's read failed with 422", "null"),
    "after the save's read failed with 422");
});

// ======================================================================
// 3. the list is the open flow's

// MUTANT "answers not noted" (flowsSlice.ts fetchProgress writes `progress`
// alone, never `sessionIds`). Every case above is red on its first probe;
// this one is its own. Observed ("runModeAcrossSave.test: 0/7 passed"), the
// first line and this case's:
//   x classic door: run mode, RUN and the readouts hold through a save's progress read: before the save
//   expected "run mode before the save: true | RUN: STOP | STATE: RUNNING | flows.progress: answer"
//   got      "run mode before the save: false | RUN: CONTINUE | STATE: IDLE | flows.progress: answer"
//   x RUN's own session is run mode before any answer names it, and through its clearing read: the sessions known after RUN answered
//   expected ["319caf668a27569db2239f9e8fa83396","fresh-session-from-run"]
//   got      ["fresh-session-from-run"]
//
// MUTANT "RUN's session not noted" (flowsSlice.ts flowsRun writes no
// `sessionIds`). Observed ("runModeAcrossSave.test: 6/7 passed"):
//   x RUN's own session is run mode before any answer names it, and through its clearing read: while RUN's progress read is in flight
//   expected "run mode while RUN's progress read is in flight: true | RUN: STOP | STATE: RUNNING | flows.progress: null"
//   got      "run mode while RUN's progress read is in flight: false | RUN: STOP | STATE: RUNNING | flows.progress: null"
await test("RUN's own session is run mode before any answer names it, and through its clearing read", async () => {
  await openRecorded();
  progressMode = "hold";
  await act(async () => { await useStore.getState().flowsRun(); });
  await flush();
  // The engine publishes the run RUN started, into the fresh session, while
  // the read RUN started is still out.
  act(() => {
    useStore.setState({ sequence: { ...clone(RUNNING), session: { ...RUNNING.session, id: FRESH } } } as any);
  });
  await mountDoor("next");
  // Only the modal can tell here: `flowsRun` has written a live run phase,
  // which RUN and the STATE readout show whoever's run it is.
  eq(probe("while RUN's progress read is in flight"), LIVE("while RUN's progress read is in flight", "null"),
    "while RUN's progress read is in flight");
  eq(useStore.getState().flows.sessionIds, [SESSION, FRESH], "the sessions known after RUN answered");
});

// MUTANT "another flow keeps the list" (flowsSlice.ts flowsOpen writes
// `sessionIds: s.flows.sessionIds` whatever the record). Observed
// ("runModeAcrossSave.test: 6/7 passed"):
//   x another flow opened while this one runs is not in run mode, and a close empties the list: the other flow's modal under the first flow's run
//   expected null
//   got      "true"
//
// MUTANT "a close keeps the list" (flowsSlice.ts flowsCloseEditor's write
// loses `sessionIds: NO_SESSIONS`). `knownSessions` already answers none
// with no record, so only the list itself shows it. Observed
// ("runModeAcrossSave.test: 6/7 passed"):
//   x another flow opened while this one runs is not in run mode, and a close empties the list: the sessions known once the flow was closed
//   expected []
//   got      ["319caf668a27569db2239f9e8fa83396"]
await test("another flow opened while this one runs is not in run mode, and a close empties the list", async () => {
  await openRecorded();
  runLive();
  eq(useStore.getState().flows.sessionIds, [SESSION], "precondition: the recorded session is known");
  // No edit is open, so the open saves nothing and replaces the record.
  await act(async () => { await useStore.getState().flowsOpen(OTHER); });
  await flush();
  eq(useStore.getState().flows.record?.id, OTHER, "precondition: the other flow is open");
  unmount();
  act(() => {
    root.render(createElement("div", { "data-box": "door" },
      createElement(FlowFrameSheet as any, { params: { open: OTHER, node: BLOCK }, depth: 0 })));
  });
  await flush();
  assert(sheet(), "the other flow's door mounted no Target modal");
  eq(sheet().getAttribute("data-view"), null, "the other flow's modal under the first flow's run");
  assert(doc.querySelector('[data-testid="framing-done"]'), "the other flow's modal offers no DONE under the first flow's run");
  eq(useStore.getState().flows.sessionIds, [], "the sessions known once another flow opened");
  unmount();
  // The recorded flow again, its session known, then closed.
  await openRecorded();
  eq(useStore.getState().flows.sessionIds, [SESSION], "precondition: the recorded session is known again");
  await act(async () => { await useStore.getState().flowsCloseEditor(); });
  eq(useStore.getState().flows.sessionIds, [], "the sessions known once the flow was closed");
});

// The same flow opened again (RUN on its own Now row does it) is a clearing
// read like a save's: its sessions stay known through it.
//
// MUTANT "a reopen forgets its own sessions" (flowsSlice.ts flowsOpen writes
// `sessionIds: NO_SESSIONS` whatever the record). Observed
// ("runModeAcrossSave.test: 6/7 passed"):
//   x the same flow opened again keeps run mode through its clearing read: while the reopen's progress read is in flight
//   expected "run mode while the reopen's progress read is in flight: true | RUN: STOP | STATE: RUNNING | flows.progress: null"
//   got      "run mode while the reopen's progress read is in flight: false | RUN: RUN | STATE: IDLE | flows.progress: null"
//
// MUTANT "the readouts take a list for an answer" (runCopy.ts `sessionsOf`
// loses its list arm, so the list the store's readers pass reads as an
// answer naming no session). Observed ("runModeAcrossSave.test: 2/7 passed"),
// the first of five, every one on STATE alone:
//   x classic door: run mode, RUN and the readouts hold through a save's progress read: before the save
//   expected "run mode before the save: true | RUN: STOP | STATE: RUNNING | flows.progress: answer"
//   got      "run mode before the save: true | RUN: STOP | STATE: IDLE | flows.progress: answer"
await test("the same flow opened again keeps run mode through its clearing read", async () => {
  await openRecorded();
  runLive();
  await mountDoor("next");
  eq(probe("before the reopen"), LIVE("before the reopen", "answer"), "before the reopen");
  progressMode = "hold";
  await act(async () => { await useStore.getState().flowsOpen(FLOW); });
  await flush();
  assert(held !== null, "precondition: the reopen's progress read is not held open");
  eq(probe("while the reopen's progress read is in flight"),
    LIVE("while the reopen's progress read is in flight", "null"),
    "while the reopen's progress read is in flight");
});

// ======================================================================
// 4. the control: the same probe sees an editable modal

await test("control: the run ending turns the same modal editable, RUN to CONTINUE and STATE idle", async () => {
  await openRecorded();
  runLive();
  await mountDoor("classic");
  eq(probe("while the run is live"), LIVE("while the run is live", "answer"), "precondition");
  runLive(false);
  await flush();
  eq(probe("once the run ended"), "run mode once the run ended: false | RUN: CONTINUE | STATE: IDLE | flows.progress: answer",
    "once the run ended");
  // And the other door, the same.
  await mountDoor("next");
  eq(probe("at the #/next door once the run ended"),
    "run mode at the #/next door once the run ended: false | RUN: CONTINUE | STATE: IDLE | flows.progress: answer",
    "at the #/next door once the run ended");
});

// ------------------------------------------------------------------- report
act(() => { root.unmount(); });
const total = passed + failed;
console.log(`runModeAcrossSave.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) (globalThis as any).process.exitCode = 1;
export const result = { passed, failed, total };
export default result;
