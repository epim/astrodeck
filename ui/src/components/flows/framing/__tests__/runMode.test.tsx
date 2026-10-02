// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// runMode.test.tsx - the Target modal in RUN MODE, mounted on the real store
// and graded on what the server really answers (#189 S5; spec 2026-09-23
// flows mosaic, 2.6 run mode, 2.3 the panel states by shape, 5.10 published
// state, 6.9 a viewer during a meridian wait).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/framing/__tests__/runMode.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE ANSWERS ARE TWO RECORDED FILES, READ, NOT COPIED:
//
//   server/tests/fixtures/flow_progress_continue.json
//     GET /api/flows/example-m31-mosaic/progress: a 3x2 called "M31", node
//     n2, with a dormant session of two runs.
//   server/tests/fixtures/sequence_state_mosaic.json
//     GET /api/sequence/state on the clocked simulator for a rotating 2x2,
//     also called "M31", under the progress file's session id: 1-1 being
//     shot while 2-2 is set aside, and a meridian wait as an operator and as
//     a viewer is served it (the viewer's without `panel` and `pass`).
//
// server/tests/test_s5_recorded_state.py rebuilds both byte for byte, and
// flowRunState.test.ts grades the readers on them. The two were recorded
// from two different plans, so they share a session id and a name but NOT a
// group id: as recorded, the pair is exactly "another block called M31 is
// running" (case 2), and joined (`JOINED`, the progress block given the live
// group's id, as it would carry were its plan the one running) it is "this
// block is running" (every other case). Nothing else in either file is edited
// except in case 3, which moves one panel's count as its remaining subs land,
// and in case 1's second half, which moves the group on to a panel off the
// diagonal (1-3), where a row read as a column would name another panel.
//
// WHAT IS GUARDED
//
//   1. The live group is drawn: the panel being shot as a thick stroke with
//      corner ticks, a set-aside panel dotted with "!", and its reason in
//      the engine's words in PANELS, each at its own row and column.
//   2. The group is the block's only by the progress block's group_id, never
//      by name: the recorded pair draws no run at all.
//   3. A panel is hatched as its subs land, through the slice's live
//      progress re-read (#214), with the sheet left open.
//   4. A viewer during a meridian wait (panel and pass absent) sees no panel
//      being shot and no timing; the set-aside panel and its reason stay.
//   5. Run mode writes nothing: no DONE, every control disabled, the sky
//      read-only, no store action reached.
//   6. An Example that is running says why in run mode's words and draws
//      the run.
//
// Every mutant below was run in a private scratch copy of ui/ (scratchpad
// S5-RUNMODE-mut, each file mutated from a byte backup and restored and
// hash-compared after the run), never in the shared tree (#254), and the
// failure it produced is quoted verbatim (2026-09-28), wrapped at spaces. The
// engine's reason carries an em dash, which the console printed as a
// replacement character; it is elided here as "...", so this file stays ASCII.
// Counts written N/7 were taken before the off-diagonal case (in section 1)
// was added. The independent verification re-ran every one of those mutants
// against all eight cases (scratchpad S5-RUNMODE-verify-mut, 2026-09-28):
// each quoted line recurred verbatim, and "group ignored", "set-aside reason
// dropped", "viewOnly ignored" and the reader's name match are red at the
// off-diagonal case too.
//
// SINCE S7 (#509) a set-aside line whose engine reason already names its
// panel is not prefixed with the label: 2-2's line is the set-aside words
// and the reason alone, where S5 printed "2-2: " before them too. The three
// cases below that pin 2-2's line were moved for it on purpose (S7-URUNHOLD),
// and the two mutants that touch the line were run again against the moved
// pins in scratchpad S7-URUNHOLD-mut (2026-09-28), quoted where they stand
// (as "set aside tonight", the wording the recorded night carried then).
// framingSections.test.tsx grades the rule itself, and the words for a run
// that is paused, holding or stopping (#451) on the recorded states.
//
// SINCE #573 (#534 follow-up; backlog ruling D-07, owner-approved
// 2026-09-30) the recorded 2-2 is a CENTRING set-aside that has not yet
// expired, so it is worded "set aside for now: ...; tried once more
// tonight" (`ASIDE_LINE`, below), not "set aside tonight": the mutants
// quoted above, from before this change, still read "set aside tonight" in
// their "got" and "expected" lines, which is what the engine served then.
// w7SetAsideForNow.test.ts grades the two wordings themselves, against
// `forNow` alone, with its own mutant.

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
// Nothing in this file may reach a network: every route is replaced below,
// and a request that slips past them fails loudly here.
win.fetch = async (url: string) => { throw new Error(`no network in this fixture: ${url}`); };
win.WebSocket = class { close() {} addEventListener() {} send() {} };
const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement", "HTMLSelectElement",
  "HTMLTextAreaElement", "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "FocusEvent", "localStorage", "sessionStorage", "getComputedStyle", "matchMedia", "ResizeObserver",
  "requestAnimationFrame", "cancelAnimationFrame", "Image", "URL", "fetch", "Blob", "WebSocket",
  "location", "history",
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
const sheetModule = await import("../TargetFramingSheet");
const Sheet = sheetModule.default;
const { RUNNING_VIEW_ONLY, EXAMPLE_VIEW_ONLY } = sheetModule;
const {
  SHOOTING_NOW, SET_ASIDE_TONIGHT, SET_ASIDE_FOR_NOW, CURRENT_PAUSED, CURRENT_HOLDING_FOR_CLOUD,
  CURRENT_STOPPING,
} = await import("../sections/PanelsSection");
type FlowNodeRec = import("../../flowsTypes").FlowNodeRec;
type FlowEdgeRec = import("../../flowsTypes").FlowEdgeRec;
type SequenceState = import("../../../../types").SequenceState;
type FlowProgress = import("../../../../lib/flowsApi").FlowProgress;

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
/** Each test UNMOUNTS in `finally`: a failed assertion must not leave its
 *  sheet mounted, where the next mount would reuse its state. */
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
// A missing or unreadable file FAILS the whole file, never skips it: a
// skipped fixture reads as a green run mode.
const FIXTURES = "../../../../../../server/tests/fixtures/";
function readFixture(name: string): any {
  try {
    return JSON.parse(readFileSync(new URL(FIXTURES + name, import.meta.url), "utf8") as string);
  } catch (e) {
    throw new Error(`cannot read ${FIXTURES}${name}, a recorded answer run mode is graded against: `
      + `${(e as Error).message}`);
  }
}
const PROGRESS = readFixture("flow_progress_continue.json").response as FlowProgress;
const STATES = readFixture("sequence_state_mosaic.json").states as Record<string, SequenceState>;
const SHOOTING = STATES.shooting;
const WAIT_OPERATOR = STATES.meridian_wait_operator;
const WAIT_VIEWER = STATES.meridian_wait_viewer;
if (!PROGRESS?.session || !SHOOTING?.group || !WAIT_OPERATOR?.group || !WAIT_VIEWER?.group) {
  throw new Error("the recorded fixtures do not hold the progress answer and the three states");
}
const BLOCK = PROGRESS.blocks[0];
const LIVE_GROUP_ID = SHOOTING.group!.id;
const REASON = SHOOTING.group!.set_aside[0].reason;
// The recorded 2-2 is a CENTRING set-aside not yet expired (#573, #534
// follow-up): `for_now` is true, so PANELS words it "set aside for now",
// with the "tried once more tonight" tail, not "set aside tonight".
assert(SHOOTING.group!.set_aside[0].for_now === true,
  "premise: the recorded set-aside is still for now");
const ASIDE_LINE = `${SET_ASIDE_FOR_NOW}: ${REASON}; tried once more tonight`;
assert(!ASIDE_LINE.startsWith(SET_ASIDE_TONIGHT),
  "premise: the recorded for-now set-aside does not read as set aside tonight");
/** The recorded progress answer, its block given the live group's id: the
 *  answer the progress route gives while this block's own plan is the one
 *  the rig runs (see the header). */
const JOINED: FlowProgress = { ...PROGRESS, blocks: [{ ...BLOCK, group_id: LIVE_GROUP_ID }] };

const OPERATOR = ["view.status", "view.preview", "view.site_derived", "control.capture", "control.mount"];
const VIEWER = ["view.status", "view.preview"];

/** The block the progress answer counts: n2, "M31", 3 columns by 2 rows of a
 *  2.00 x 1.33 deg camera, then a FILTER CYCLE looping back to it. */
function target(over: Record<string, string | number> = {}): FlowNodeRec {
  return {
    id: BLOCK.node_id, type: "target", x: 0, y: 0,
    params: {
      ...NODE_DEFS.target.params, name: BLOCK.name, ra: "00h 42m 44s", dec: "+41 16 09",
      rows: BLOCK.grid!.rows, cols: BLOCK.grid!.cols, overlap: 25, fovX: 2.0, fovY: 1.33, rotation: 0,
      angle: "Rotate to PA", counts: "Accepted subs", frameAnchor: "", ...over,
    },
  };
}
const CYCLE: FlowNodeRec = { id: "cy", type: "cycle", x: 260, y: 0, params: { ...NODE_DEFS.cycle.params } };
const E = (id: string, from: string, fromPort: string, to: string, toPort: string): FlowEdgeRec =>
  ({ id, from, fromPort, to, toPort });
const LANE = [E("a", BLOCK.node_id, "target", "cy", "run"), E("loop", "cy", "pass", BLOCK.node_id, "next")];
const COMPILED = { plan: {}, structural: [], issues: [], unmapped: [] };
const FLOW_ID = PROGRESS.flow_id;

// ------------------------------------------------------------------ the network
// Offline (`wsConnected: false`) the sheet asks no panel route; the answers
// are here so a request that is made anyway waits rather than fails.
(framingApi as any).mosaic = () => new Promise(() => {});
(framingApi as any).compileDraft = async () => COMPILED;
let storeCompiles = 0;
(flowsApi as any).compileDraft = async () => { storeCompiles++; return COMPILED; };
/** The progress route's answers, in the order the slice asks for them. */
let progressAnswers: FlowProgress[] = [];
let progressReads = 0;
(flowsApi as any).progress = async () => {
  progressReads++;
  const next = progressAnswers.shift();
  if (!next) throw new Error("the progress route was asked more often than this test answers");
  return next;
};
framingTiming.settleMs = 0;

// ------------------------------------------------------------------ the store
const real = useStore.getState();
const IDLE_SEQUENCE = real.sequence;
let applyCalls: any[][] = [];
let settingCalls: any[][] = [];
useStore.setState({
  flowsApplyFraming: (...a: any[]) => { applyCalls.push(a); return (real.flowsApplyFraming as any)(...a); },
  flowsSetSetting: (...a: any[]) => { settingCalls.push(a); return (real.flowsSetSetting as any)(...a); },
  flowsFetchTonight: async () => {},
} as any);

function record(graph: any, readonly = false): any {
  return { id: FLOW_ID, name: "M31 mosaic", folder: "", tagline: "", graph,
    created_ts: 0, updated_ts: 0, last_run: null, last_result: "", readonly };
}

function setup(o: {
  caps?: string[]; progress?: FlowProgress | null; sequence?: SequenceState;
  params?: Record<string, string | number>; readonly?: boolean; skyAngle?: any;
} = {}): void {
  const graph = { nodes: [target(o.params), CYCLE], edges: LANE };
  act(() => {
    useStore.setState({
      principal: { role: o.caps === VIEWER ? "viewer" : "operator", email: null, caps: o.caps ?? OPERATOR },
      wsConnected: false,
      status: { sky_angle: o.skyAngle ?? null } as any,
      config: null,
      site: null,
      sequence: o.sequence ?? IDLE_SEQUENCE,
      flows: {
        ...FLOWS_INIT,
        record: record(graph, o.readonly ?? false),
        graph,
        compiled: COMPILED,
        progress: o.progress === undefined ? JOINED : o.progress,
      },
    } as any);
  });
  applyCalls = []; settingCalls = []; storeCompiles = 0; progressReads = 0; progressAnswers = [];
}

const container = win.document.getElementById("root");
const root = createRoot(container);
let closes = 0;
/** A fresh sheet, in run mode unless `viewOnly` is false. */
function mount(viewOnly = true): void {
  act(() => { root.render(null); });
  act(() => {
    root.render(createElement(Sheet, { nodeId: BLOCK.node_id, onClose: () => { closes++; }, viewOnly }));
  });
}
function unmount(): void {
  act(() => { root.render(null); });
  closes = 0;
}
async function flush(ms = 5): Promise<void> {
  await act(async () => { await new Promise((r) => setTimeout(r, ms)); });
}
const doc = win.document;
const q = (id: string) => doc.querySelector(`[data-testid="${id}"]`) as any;
/** The sky's shape for a panel (0-based server row and col). */
const shape = (r: number, c: number) =>
  doc.querySelector(`[data-role="panel"][data-row="${r}"][data-col="${c}"]`) as any;
const outline = (r: number, c: number) => shape(r, c)?.querySelector('[data-mark="outline"]') as any;
const skyLabel = (r: number, c: number) =>
  doc.querySelector(`[data-role="panel-label"][data-row="${r}"][data-col="${c}"]`) as any;
/** Every panel's drawn state, by label in label order, off the sky's shapes
 *  (which are drawn in the route's snake order). */
function skyStates(): Record<string, string> {
  const pairs: [string, string][] = [];
  for (const el of Array.from(doc.querySelectorAll('[data-role="panel"]')) as any[]) {
    pairs.push([`${+el.getAttribute("data-row") + 1}-${+el.getAttribute("data-col") + 1}`, el.getAttribute("data-state")]);
  }
  return Object.fromEntries(pairs.sort(([a], [b]) => a.localeCompare(b)));
}
/** PANELS' run lines, by label in label order (PANELS lists run order). */
function runRows(): Record<string, string> {
  const pairs: [string, string][] = [];
  for (const el of Array.from(doc.querySelectorAll('[data-testid="framing-panel-run"]')) as any[]) {
    pairs.push([el.getAttribute("data-panel-run"), el.textContent]);
  }
  return Object.fromEntries(pairs.sort(([a], [b]) => a.localeCompare(b)));
}
function click(el: any): void {
  assert(el, "no element to click");
  act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
}
function ptr(type: string, x: number, y: number): any {
  const ev = new win.Event(type, { bubbles: true, cancelable: true });
  Object.assign(ev, { pointerId: 1, pointerType: "mouse", button: 0, buttons: 1, isPrimary: true, clientX: x, clientY: y });
  return ev;
}
function drag(el: any, x0: number, y0: number, x1: number, y1: number): void {
  act(() => { el.dispatchEvent(ptr("pointerdown", x0, y0)); });
  act(() => { el.dispatchEvent(ptr("pointermove", (x0 + x1) / 2, (y0 + y1) / 2)); });
  act(() => { el.dispatchEvent(ptr("pointermove", x1, y1)); });
  act(() => { el.dispatchEvent(ptr("pointerup", x1, y1)); });
}
function key(el: any, k: string): void {
  act(() => { el.dispatchEvent(new win.KeyboardEvent("keydown", { key: k, bubbles: true, cancelable: true })); });
}

// ---------------------------------------------------------- the premises
await test("premise: the two files are one session and two blocks of one name, and the sky draws every panel", async () => {
  eq(SHOOTING.session?.id, PROGRESS.session!.id, "the night does not run under the progress file's session");
  eq(BLOCK.name, SHOOTING.group!.name, "the two blocks were meant to share a name");
  assert(BLOCK.group_id !== LIVE_GROUP_ID, `the progress block names its own group: ${BLOCK.group_id}`);
  eq(SHOOTING.group!.panel, "1-1", "the recorded panel being shot");
  eq(SHOOTING.group!.set_aside.map((a) => a.panel), ["2-2"], "the recorded set-aside panels");
  assert(REASON.startsWith("centring failed on 2-2"), `the engine's reason: ${REASON}`);
  assert(!("panel" in WAIT_VIEWER.group!) && !("pass" in WAIT_VIEWER.group!),
    "the viewer is served the panel or the pass across the wait");
  // The harness's sky draws a shape and a label for all six panels, or the
  // states below would be graded on nothing.
  setup({ sequence: SHOOTING });
  mount();
  eq(Object.keys(skyStates()).sort(), ["1-1", "1-2", "1-3", "2-1", "2-2", "2-3"], "the panels the sky draws");
  for (const [r, c] of [[0, 0], [1, 1], [0, 1]]) assert(skyLabel(r, c), `no sky label for ${r + 1}-${c + 1}`);
});

// ====================================================================
// 1. the live group, drawn

// MUTANT "group ignored" (TargetFramingSheet.tsx: the run selector hands
// `runPanelsOf` null instead of `groupForBlock(s.sequence, runGroupId)`, so
// run mode draws only what the progress route says). Observed, 2/7 (every
// case that draws the run is red; this one's line):
//   x run mode draws the live group: 1-1 shot with thick stroke and corner ticks, 2-2 dotted with
//     '!' and its reason in PANELS: the sky's panel states: expected {"1-1":"shooting","1-2":
//     "pending","1-3":"pending","2-1":"pending","2-2":"set_aside","2-3":"pending"}, got {"1-1":
//     "done","1-2":"pending","1-3":"pending","2-1":"pending","2-2":"pending","2-3":"pending"}
// MUTANT "set-aside reason dropped" (PanelsSection.tsx `runLine` answers
// SET_ASIDE_TONIGHT alone, the engine's reason left out). Observed again
// against the #509 pins (S7-URUNHOLD-mut), 5/8 (the off-diagonal and the
// viewer's cases below are red with the same line for 2-2):
//   x run mode draws the live group: 1-1 shot with thick stroke and corner ticks, 2-2 dotted with
//     '!' and its reason in PANELS: PANELS' run lines: expected {"1-1":"1-1: shooting now","2-2":
//     "set aside tonight: centring failed on 2-2 on 3 consecutive visits: plate solve failed ...
//     used raw GoTo"}, got {"1-1":"1-1: shooting now","2-2":"set aside tonight"}
// MUTANT "label prefixed again" (PanelsSection.tsx `runRowText` prefixing the
// label whatever the reason says, as S5 printed every line; #509). Observed,
// 5/8 (the same two cases red with the same line for 2-2):
//   x run mode draws the live group: 1-1 shot with thick stroke and corner ticks, 2-2 dotted with
//     '!' and its reason in PANELS: PANELS' run lines: expected {"1-1":"1-1: shooting now","2-2":
//     "set aside tonight: centring failed on 2-2 on 3 consecutive visits: plate solve failed ...
//     used raw GoTo"}, got {"1-1":"1-1: shooting now","2-2":"2-2: set aside tonight: centring failed
//     on 2-2 on 3 consecutive visits: plate solve failed ... used raw GoTo"}
await test("run mode draws the live group: 1-1 shot with thick stroke and corner ticks, 2-2 dotted with '!' and its reason in PANELS", async () => {
  setup({ sequence: SHOOTING });
  mount();
  eq(q("target-framing-sheet")?.getAttribute("data-view"), "true", "the sheet's view mode");
  eq(q("framing-view-why")?.textContent, RUNNING_VIEW_ONLY, "the sheet's reason");
  // The sky: every panel as the live group and the progress route say.
  // 1-1 is complete on the progress answer and the one the group is on:
  // shooting wins, the count lagging the run (framingModel panelDrawState).
  eq(skyStates(), {
    "1-1": "shooting", "1-2": "pending", "1-3": "pending",
    "2-1": "pending", "2-2": "set_aside", "2-3": "pending",
  }, "the sky's panel states");
  // Shooting: a thick stroke and the outward corner ticks.
  eq(outline(0, 0)?.getAttribute("stroke-width"), "4", "1-1's stroke");
  assert(shape(0, 0)?.querySelector('[data-mark="ticks"]'), "1-1 has no corner ticks");
  eq(skyLabel(0, 0)?.getAttribute("data-state"), "shooting", "1-1's label");
  // Set aside: a dotted stroke, and "!" in the label.
  eq(outline(1, 1)?.getAttribute("stroke-dasharray"), "1.5 6", "2-2's stroke");
  eq(outline(1, 1)?.getAttribute("stroke-linecap"), "round", "2-2's dots");
  eq(skyLabel(1, 1)?.querySelector('[data-role="panel-flag"]')?.textContent, "!", "2-2's flag");
  // PANELS: the run's line under the two panels it names, and no other.
  eq(runRows(), {
    "1-1": `1-1: ${SHOOTING_NOW}`,
    "2-2": ASIDE_LINE,
  }, "PANELS' run lines");
  // A pending panel keeps its progress bar from the route, unchanged.
  eq(doc.querySelector('[data-panel="1-2"] [role="progressbar"]')?.getAttribute("aria-valuenow"),
    String(BLOCK.panels.find((p) => p.name.endsWith(" 1-2"))!.banked), "1-2's banked subs");
});

// The recorded panels, 1-1 and 2-2, sit on the diagonal, where a label read
// column first ("c-r") names the same panel, so the case above cannot tell a
// row from a column. This one moves the recorded group on to 1-3, row 1 and
// column 3 of the progress block's 2 rows by 3 columns, which has no 3-1.
//
// MUTANT "row and column swapped in PanelsSection" (`panelRows` looks a row's
// run state up as `${c0 + 1}-${r0 + 1}`). Observed (verifier's scratch copy
// S5-RUNMODE-verify-mut, 2026-09-28), 7/8, and 8/8 before this case existed:
//   x the panel being shot is drawn at its own row and column, off the diagonal: the sky's panel
//     states with the group on 1-3: expected {"1-1":"done","1-2":"pending","1-3":"shooting","2-1":
//     "pending","2-2":"set_aside","2-3":"pending"}, got {"1-1":"done","1-2":"pending","1-3":
//     "pending","2-1":"pending","2-2":"set_aside","2-3":"pending"}
// MUTANT "row and column swapped in runPanelsOf" (framingModel.ts builds each
// label as `${c}-${r}`). Observed, 7/8 (the same line; framingModel.test's
// off-diagonal case is red under it too):
//   x the panel being shot is drawn at its own row and column, off the diagonal: the sky's panel
//     states with the group on 1-3: expected {"1-1":"done","1-2":"pending","1-3":"shooting","2-1":
//     "pending","2-2":"set_aside","2-3":"pending"}, got {"1-1":"done","1-2":"pending","1-3":
//     "pending","2-1":"pending","2-2":"set_aside","2-3":"pending"}
await test("the panel being shot is drawn at its own row and column, off the diagonal", async () => {
  const next = { ...SHOOTING, group: { ...SHOOTING.group!, panel: "1-3" } };
  setup({ sequence: next });
  mount();
  // 1-1, complete on the progress answer and no longer the group's panel,
  // is hatched now.
  eq(skyStates(), {
    "1-1": "done", "1-2": "pending", "1-3": "shooting",
    "2-1": "pending", "2-2": "set_aside", "2-3": "pending",
  }, "the sky's panel states with the group on 1-3");
  eq(runRows(), {
    "1-3": `1-3: ${SHOOTING_NOW}`,
    "2-2": ASIDE_LINE,
  }, "PANELS' run lines with the group on 1-3");
});

// ====================================================================
// 2. by id, never by name

// MUTANT "group matched by name" (TargetFramingSheet.tsx: the run selector
// takes the live group when its NAME is the draft's name, `s.sequence.group
// && s.sequence.group.name === String(draft.name) ? s.sequence.group : null`,
// in place of `groupForBlock(s.sequence, runGroupId)`). Observed, 5/7 (and
// case 6's control: a name match lights up an Example outside run mode too):
//   x the recorded pair, two blocks called M31, draws no run: a group is the block's only by the
//     progress block's group_id: the sky's panel states under another block's run: expected
//     {"1-1":"done","1-2":"pending","1-3":"pending","2-1":"pending","2-2":"pending","2-3":
//     "pending"}, got {"1-1":"shooting","1-2":"pending","1-3":"pending","2-1":"pending","2-2":
//     "set_aside","2-3":"pending"}
// The same defect inside the reader (flowRunState.ts `groupForBlock`
// comparing `group.name === groupId`, that file's own mutant) is red here
// too, from the other side: the joined answer's id no longer finds the group.
// Observed, 2/7:
//   x run mode draws the live group: 1-1 shot with thick stroke and corner ticks, 2-2 dotted with
//     '!' and its reason in PANELS: the sky's panel states: expected {"1-1":"shooting","1-2":
//     "pending","1-3":"pending","2-1":"pending","2-2":"set_aside","2-3":"pending"}, got {"1-1":
//     "done","1-2":"pending","1-3":"pending","2-1":"pending","2-2":"pending","2-3":"pending"}
await test("the recorded pair, two blocks called M31, draws no run: a group is the block's only by the progress block's group_id", async () => {
  setup({ sequence: SHOOTING, progress: PROGRESS });
  mount();
  eq(q("framing-view-why")?.textContent, RUNNING_VIEW_ONLY, "the sheet's reason");
  // 1-1 is complete on this block's own progress, so it is hatched; nothing
  // the other M31's run is doing is drawn here.
  eq(skyStates(), {
    "1-1": "done", "1-2": "pending", "1-3": "pending",
    "2-1": "pending", "2-2": "pending", "2-3": "pending",
  }, "the sky's panel states under another block's run");
  eq(runRows(), {}, "PANELS' run lines under another block's run");
  // CONTROL: the same sheet with its block's group live draws the run, so
  // what held it back above is the id.
  setup({ sequence: SHOOTING, progress: JOINED });
  mount();
  eq(skyStates()["1-1"], "shooting", "1-1 once the live group is this block's");
});

// ====================================================================
// 3. done, as the subs land

// The recorded answer, with 1-2's remaining subs landed: every step owed
// nothing. The next answer the live re-read gets once 1-2 completes.
function withPanelComplete(p: FlowProgress, label: string): FlowProgress {
  const blocks = p.blocks.map((b) => ({
    ...b,
    panels: b.panels.map((pp) => (pp.name.endsWith(` ${label}`)
      ? { ...pp, banked: pp.total, owed: 0, steps: pp.steps.map((s) => ({ ...s, banked: s.count, owed: 0 })) }
      : pp)),
  }));
  return { ...p, blocks };
}
/** A recorded state with its frame counter moved on, as the next publish. */
function framesAt(s: SequenceState, n: number): SequenceState {
  return { ...s, progress: { ...s.progress!, frames_done: n } };
}

// MUTANT "progress read once" (TargetFramingSheet.tsx reads the progress
// answer as the sheet opened, `const [progress] = useState(() =>
// useStore.getState().flows.progress)`, instead of subscribing). Observed, 6/7:
//   x a panel is hatched as its subs land, through the slice's live progress re-read, with the
//     sheet left open: 1-2 once its last subs landed: expected "done", got "pending"
await test("a panel is hatched as its subs land, through the slice's live progress re-read, with the sheet left open", async () => {
  setup({ sequence: IDLE_SEQUENCE, progress: null });
  // The flow opens as the editor opens it: the record, then the progress
  // route's first answer, which names the session the run will write.
  const graph = useStore.getState().flows.graph;
  (flowsApi as any).get = async () => record(graph);
  progressAnswers = [JOINED, withPanelComplete(JOINED, "1-2")];
  await act(async () => { await useStore.getState().flowsOpen(FLOW_ID); });
  await flush();
  eq(useStore.getState().flows.progress?.session?.id, PROGRESS.session!.id, "precondition: the first answer landed");
  // The run publishes: its first state, then a frame landing on it.
  act(() => { useStore.setState({ sequence: framesAt(SHOOTING, 6) } as any); });
  mount();
  eq(skyStates()["1-2"], "pending", "1-2 before its last subs land");
  eq(doc.querySelector('[data-panel="1-2"] [role="progressbar"]')?.getAttribute("aria-valuenow"),
    String(BLOCK.panels.find((p) => p.name.endsWith(" 1-2"))!.banked), "1-2's banked subs before");
  act(() => { useStore.setState({ sequence: framesAt(SHOOTING, 7) } as any); });
  await flush();
  eq(progressReads, 2, "progress reads: the open's, and the live re-read the frame started");
  eq(skyStates()["1-2"], "done", "1-2 once its last subs landed");
  assert(/^url\(#/.test(outline(0, 1)?.getAttribute("fill") ?? ""), "1-2 is not hatched");
  eq(doc.querySelector('[data-panel="1-2"] [role="progressbar"]')?.getAttribute("aria-valuenow"), "80",
    "1-2's banked subs after");
  // The live group is still drawn beside it.
  eq(skyStates()["1-1"], "shooting", "1-1 after the re-read");
});

// ====================================================================
// 4. a viewer during a meridian wait

/** Every duration the recorded state carries, as seconds and as the minutes
 *  the app's chips round them to: none may appear on the sheet. The meridian
 *  countdown above all (spec 5.10, 6.9): its end is the crossing, and the
 *  crossing of a known RA is the site's longitude. */
function timingTokens(s: SequenceState): string[] {
  const secs = [
    (s as any).live?.meridian_eta_s, s.progress?.eta_s, s.progress?.remaining_capture_s,
    s.progress?.events_cost_s, s.progress?.elapsed_s,
  ].filter((v): v is number => typeof v === "number" && v > 0);
  return secs.flatMap((v) => [String(v), `${Math.round(v / 60)} min`]);
}

// MUTANT "shooting read off the target name" (TargetFramingSheet.tsx: the run
// selector also marks as shooting the panel the state's `target` names, "M31
// 2-1" -> "2-1", when the group names none: the naive join of the monitor's
// target line). Observed, 6/7:
//   x a viewer during a meridian wait sees no panel being shot and no timing; the set-aside panel
//     and its reason stay: shapes or labels drawn as shooting for the viewer: expected 0, got 2
// MUTANT "the meridian chip in the strip" (TargetFramingSheet.tsx: the readout
// strip gains a line from the state's `live.meridian_eta_s`, "meridian flip
// in N min", as the Monitor's schedule chip words it). Observed, 6/7:
//   x a viewer during a meridian wait sees no panel being shot and no timing; the set-aside panel
//     and its reason stay: timing on the viewer's sheet: expected [], got ["14 min"]
await test("a viewer during a meridian wait sees no panel being shot and no timing; the set-aside panel and its reason stay", async () => {
  setup({ caps: VIEWER, sequence: WAIT_VIEWER });
  mount();
  eq(q("framing-view-why")?.textContent, RUNNING_VIEW_ONLY, "the viewer's reason");
  eq(doc.querySelectorAll('[data-state="shooting"]').length, 0, "shapes or labels drawn as shooting for the viewer");
  eq(runRows(), { "2-2": ASIDE_LINE }, "the viewer's PANELS run lines");
  eq(skyStates()["2-2"], "set_aside", "2-2 for the viewer");
  const text = String(q("target-framing-sheet")?.textContent ?? "");
  // #166 item 1 (W2 integration): the server now withholds `live.meridian_eta_s`
  // from a viewer's OWN recorded state too (api/redact.py's `_DERIVED_NODES`),
  // so WAIT_VIEWER no longer carries "813"/"14 min" at all -- there is nothing
  // left for this sheet to leak a second way. The premise and the check both
  // move to WAIT_OPERATOR's tokens (the same countdown, still present there),
  // graded against the VIEWER's rendered text: the sheet must not show it
  // even if a future change to this fixture or route ever put it back on the
  // wire, which is the regression this case exists to catch.
  const tokens = timingTokens(WAIT_OPERATOR);
  assert(tokens.includes("813") && tokens.includes("14 min"),
    `premise: the operator's recorded state carries the meridian countdown: ${JSON.stringify(tokens)}`);
  assert(!timingTokens(WAIT_VIEWER).includes("813"),
    "premise: #166 item 1 means the viewer's own recorded state no longer " +
    "carries the countdown at all");
  const shown = tokens.filter((t) => new RegExp(`(^|[^0-9])${t}($|[^0-9])`).test(text));
  eq(shown, [], "timing on the viewer's sheet");
  // The operator's wait names its panel and pass, and is not shooting it
  // either: the panel a waiting group names is the one it last shot.
  setup({ sequence: WAIT_OPERATOR });
  mount();
  eq(doc.querySelectorAll('[data-state="shooting"]').length, 0, "shapes or labels drawn as shooting for the operator");
  // CONTROL: the same viewer, the run shooting, sees the panel being shot,
  // so the absence above is the wait's.
  setup({ caps: VIEWER, sequence: SHOOTING });
  mount();
  eq(skyStates()["1-1"], "shooting", "1-1 for the viewer while it is shot");
});

// ====================================================================
// 5. run mode writes nothing

// MUTANT "viewOnly ignored" (TargetFramingSheet.tsx, the shell's `runMode`
// is `false` whatever `viewOnly` says, as the sheet stood before S5 passed
// it on). Observed, 1/7 (both doors' run-mode cases are red under it too):
//   x run mode writes nothing: no DONE, every control disabled, the sky read-only, no store action
//     reached: run mode offers DONE
// MUTANT "sky live in run mode" (TargetFramingSheet.tsx hands FramingSky
// `readOnly={busy}` instead of `readOnly={frozen}`). Observed, 6/7:
//   x run mode writes nothing: no DONE, every control disabled, the sky read-only, no store action
//     reached: run mode's sky offers MOVE SKY or MOVE GRID
// Each of the sky's three gestures, reaching the draft on its own (FramingSky
// wiring one handler whatever `readOnly` says), is red at its own line. The
// strip's middle dot is written <U+00B7>.
// MUTANT "run-mode taps skip" (`onPanelTap={p.onPanelTap}`). Observed, 6/7:
//   x run mode writes nothing: no DONE, every control disabled, the sky read-only, no store action
//     reached: 1-2 after a tap on run mode's sky: expected "pending", got "skipped"
// MUTANT "run-mode drag moves the centre" (`onCenterChange={moveGrid ?
// p.onViewCentre : p.onFrameCentre}`). Observed, 6/7:
//   x run mode writes nothing: no DONE, every control disabled, the sky read-only, no store action
//     reached: the readout strip after a drag of run mode's sky: expected "5.0 x 2.3 deg <U+00B7> 6
//     panels <U+00B7> 00h42m44s +41 16'no angle", got "5.0 x 2.3 deg <U+00B7> 6 panels <U+00B7>
//     00h49m54s +41 55'no angle"
// MUTANT "run-mode keys turn the angle" (`onRotate={p.onRotate}`). Observed, 6/7:
//   x run mode writes nothing: no DONE, every control disabled, the sky read-only, no store action
//     reached: the readout strip after [ and ] on run mode's sky: expected "5.0 x 2.3 deg <U+00B7>
//     6 panels <U+00B7> 00h42m44s +41 16'no angle", got "5.0 x 2.3 deg <U+00B7> 6 panels <U+00B7>
//     00h42m44s +41 16'rotate to 0.0 deg"
await test("run mode writes nothing: no DONE, every control disabled, the sky read-only, no store action reached", async () => {
  // A measured angle and a grid at ANY ANGLE, so the strip WOULD offer the
  // measurement: the one control outside the fieldset a draft can write.
  const skyAngle = { pa_deg: 37.2, pier_side: "west", solved_at: Date.now() / 1000 - 60, source: "plate solve" };
  const params = { angle: "Any angle", rotation: -1 };
  // CONTROL first: the same sheet, not in run mode, edits by tap and offers.
  setup({ sequence: SHOOTING, params, skyAngle });
  mount(false);
  assert(q("framing-done"), "precondition: the editable sheet has no DONE");
  assert(q("framing-strip-offer"), "precondition: the editable sheet does not offer the measured angle");
  click(skyLabel(0, 1));
  eq(skyLabel(0, 1)?.getAttribute("data-state"), "skipped", "precondition: a tap on the editable sky skips");
  {
    // The strip carries the draft's centre and its angle, so it is what a
    // drag or a turn that reached the draft changes.
    const app = doc.querySelector('[data-testid="framing-sky"] [role="application"]') as any;
    const stripBefore = q("framing-strip")?.textContent;
    drag(app, 180, 180, 240, 210);
    const dragged = q("framing-strip")?.textContent;
    assert(dragged !== stripBefore, "precondition: a drag of the editable sky did not move the mosaic centre");
    key(app, "]");
    assert(q("framing-strip")?.textContent !== dragged, "precondition: ] on the editable sky did not turn the angle");
  }
  // Run mode.
  setup({ sequence: SHOOTING, params, skyAngle });
  const before = JSON.stringify(useStore.getState().flows.graph);
  mount();
  assert(q("framing-done") === null, "run mode offers DONE");
  assert(q("framing-strip-offer") === null, "run mode offers the measured angle");
  const fs = doc.querySelector(".tfs-fieldset") as any;
  eq(fs?.disabled, true, "run mode's fieldset");
  const controls = Array.from(doc.querySelectorAll(
    ".tfs-scroller button, .tfs-scroller input, .tfs-scroller select, .tfs-scroller textarea") as any[]);
  assert(controls.length > 10, `premise: the scroller holds its controls: ${controls.length}`);
  const live = controls.filter((el) => !el.matches(":disabled"))
    .map((el) => el.getAttribute("aria-label") ?? el.id ?? el.textContent);
  eq(live, [], "controls run mode leaves live");
  // The sky: no MOVE toggle, and no gesture that moves, turns or skips
  // anything.
  assert(!Array.from(doc.querySelectorAll("button") as any[]).some((b: any) => /MOVE (SKY|GRID)/.test(b.textContent)),
    "run mode's sky offers MOVE SKY or MOVE GRID");
  click(skyLabel(0, 1));
  await flush();
  eq(skyLabel(0, 1)?.getAttribute("data-state"), "pending", "1-2 after a tap on run mode's sky");
  eq(skyLabel(0, 1)?.tagName, "SPAN", "run mode's panel label element (a button is a tap target)");
  const strip = () => q("framing-strip")?.textContent;
  const stripBefore = strip();
  const app = doc.querySelector('[data-testid="framing-sky"] [role="application"]') as any;
  assert(app, "run mode mounted no SkyCanvas");
  drag(app, 180, 180, 240, 210);
  await flush();
  eq(strip(), stripBefore, "the readout strip after a drag of run mode's sky");
  for (const k of ["[", "]"]) key(app, k);
  await flush();
  eq(strip(), stripBefore, "the readout strip after [ and ] on run mode's sky");
  eq(applyCalls.length, 0, "flowsApplyFraming calls");
  eq(settingCalls.length, 0, "flowsSetSetting calls");
  eq(storeCompiles, 0, "compiles");
  eq(useStore.getState().flows.dirty, false, "the flow's dirty flag");
  eq(JSON.stringify(useStore.getState().flows.graph), before, "the graph after run mode");
  // CLOSE is the one way out, and closes.
  click(Array.from(doc.querySelectorAll(".tfs-head button") as any[]).find((b: any) => b.textContent === "CLOSE"));
  eq(closes, 1, "closes after CLOSE");
});

// ====================================================================
// 6. a running Example

// MUTANT "Example reason first" (TargetFramingSheet.tsx, the shell's reason
// back in S4's order, `example ? EXAMPLE_VIEW_ONLY : runMode ?
// RUNNING_VIEW_ONLY : null`). Observed, 6/7:
//   x an Example that is running says so in run mode's words, and draws the run: a running
//     Example's reason: expected "This flow's session is running: its framing opens to view, not
//     to edit.", got "Example flow: its framing opens to view, not to edit. Duplicate the flow to
//     frame your own."
await test("an Example that is running says so in run mode's words, and draws the run", async () => {
  setup({ sequence: SHOOTING, readonly: true });
  mount();
  eq(q("framing-view-why")?.textContent, RUNNING_VIEW_ONLY, "a running Example's reason");
  eq(skyStates()["1-1"], "shooting", "a running Example's 1-1");
  // CONTROL: the same Example, not running, keeps its own reason and draws
  // no run, even with the rig's state in the store.
  setup({ sequence: SHOOTING, readonly: true });
  mount(false);
  eq(q("framing-view-why")?.textContent, EXAMPLE_VIEW_ONLY, "an Example's reason outside run mode");
  eq(runRows(), {}, "an Example's PANELS run lines outside run mode");
  eq(skyStates()["1-1"], "done", "an Example's 1-1 outside run mode");
});

// ====================================================================
// 7. a run paused, holding for cloud or stopping (#451)

// The recorded held night's states (test_s5_recorded_state.py, S7): its group
// on 1-1, 2-2 set aside, and the run paused, holding for cloud, and stopping
// from an Abort pressed in that hold (which still carries the hold's key,
// #513). Joined to this block as case 1 joins the shooting state.
//
// Each mutant below was run in scratchpad S7-URUNHOLD-mut (2026-09-28) with
// the integration this case needs applied there (runPanelsOf given the run's
// state, the current panel drawn with the ticks, PanelLayer told its words).
// MUTANT "state check dropped" (flowRunState.ts `panelStateOf` answering
// `shooting` for the group's panel whatever the run's state). Observed, 8/9:
//   x the panel a paused, held or stopping run is on keeps its corner ticks and is never said to be
//     shot: PANELS' run lines while paused: expected {"1-1":"1-1: current panel, run paused","2-2":
//     "set aside tonight: centring failed on 2-2 on 3 consecutive visits: plate solve failed ...
//     used raw GoTo"}, got {"1-1":"1-1: shooting now","2-2":"set aside tonight: centring failed on
//     2-2 on 3 consecutive visits: plate solve failed ... used raw GoTo"}
// MUTANT "state not passed" (TargetFramingSheet.tsx asking `runPanelsOf`
// without `s.sequence`). Observed, 8/9, the same line. Since the S7
// integration made `run` required, that mutant is a type error (`tsc -p
// tsconfig.json --noEmit`: "TargetFramingSheet.tsx(364,20): error TS2554:
// Expected 5 arguments, but got 4."), and at run time, where tsx checks no
// type, a missing state is a run nobody knows, which shoots nothing: 2/9 in
// the integration's private copy (scratchpad S7-INTEG-r2-mut), every panel
// the run is on drawn done, this case's line being
//   x the panel a paused, held or stopping run is on keeps its corner ticks and is never said to be
//     shot: 1-1's shape while paused: expected "shooting", got "done"
// MUTANT "ticks lost" (framingModel.ts `panelDrawState` drawing only
// `shooting` as shooting). Observed, 8/9:
//   x the panel a paused, held or stopping run is on keeps its corner ticks and is never said to be
//     shot: 1-1's shape while paused: expected "shooting", got "done"
// MUTANT "sky words ignored" (PanelLayer.tsx's label telling a screen reader
// the state's own words, never `words`). Observed, 8/9:
//   x the panel a paused, held or stopping run is on keeps its corner ticks and is never said to be
//     shot: the sheet says "shooting now" while the run is paused
await test("the panel a paused, held or stopping run is on keeps its corner ticks and is never said to be shot", async () => {
  const cases: [string, string][] = [
    ["paused", CURRENT_PAUSED], ["holding", CURRENT_HOLDING_FOR_CLOUD], ["aborting", CURRENT_STOPPING],
  ];
  for (const [kind, words] of cases) {
    const s = STATES[kind];
    assert(s?.group?.panel === "1-1" && s.state === kind, `premise: the recorded ${kind} state is on 1-1`);
    setup({ sequence: s });
    mount();
    eq(q("framing-view-why")?.textContent, RUNNING_VIEW_ONLY, `the sheet's reason while ${kind}`);
    // Drawn as the panel the visit is on: the thick stroke and the ticks.
    eq(skyStates()["1-1"], "shooting", `1-1's shape while ${kind}`);
    assert(shape(0, 0)?.querySelector('[data-mark="ticks"]'), `1-1 has no corner ticks while ${kind}`);
    eq(skyStates()["2-2"], "set_aside", `2-2 while ${kind}`);
    // Worded as the run is: PANELS and the sky's screen-reader label.
    eq(runRows(), { "1-1": `1-1: ${words}`, "2-2": ASIDE_LINE },
      `PANELS' run lines while ${kind}`);
    const said = String(q("target-framing-sheet")?.textContent ?? "");
    assert(!said.includes(SHOOTING_NOW), `the sheet says "${SHOOTING_NOW}" while the run is ${kind}`);
    assert(String(skyLabel(0, 0)?.textContent ?? "").includes(words),
      `1-1's sky label does not say "${words}" while ${kind}: ${skyLabel(0, 0)?.textContent}`);
  }
  // CONTROL: the same group, the run shooting, is shot, in words too.
  setup({ sequence: { ...STATES.holding, state: "running", hold: undefined } as SequenceState });
  mount();
  eq(runRows()["1-1"], `1-1: ${SHOOTING_NOW}`, "1-1 once the run is running again");
  assert(String(skyLabel(0, 0)?.textContent ?? "").includes(SHOOTING_NOW), "1-1's sky label while it is shot");
});

// ------------------------------------------------------------------ report
act(() => { root.unmount(); });
const total = passed + failed;
console.log(`runMode.test: ${passed}/${total} passed`);
for (const f of failures) console.log(f);
if (failed) (globalThis as any).process.exitCode = 1;
export const result = { passed, failed, total };
export default result;
