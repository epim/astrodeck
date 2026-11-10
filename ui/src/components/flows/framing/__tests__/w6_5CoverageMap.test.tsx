// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w6_5CoverageMap.test.tsx - the Target modal's coverage map in RUN MODE
// (#177 package 2, WP 6.5; spec 2026-09-23 flows mosaic, 2.6 run mode).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/framing/__tests__/w6_5CoverageMap.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// The server half answers `GET /api/flows/{id}/coverage` (server
// sequence/coverage.py, whose wire test_w16_coverage_route.py pins): per mosaic
// panel, how many of its banked lights were laid over the tile, how many fall
// short, and the worst overlap. This file grades what the modal does with it,
// mounted on the real store and the harness runMode.test.tsx uses:
//
//   1. A panel with frames that fall short is TINTED on the sky and its
//      PANELS row says how many and how bad. A clean panel is plain. A panel
//      whose frames carry no plate solve says it is not checked yet, never
//      covered, and a panel with no frames says nothing.
//   2. With stamping off the route's note is said ONCE, under the PANELS
//      heading, not per panel.
//   3. The route is asked only while the sheet is open in run mode: not in
//      edit mode, not after it closes, and again when the run's progress is
//      re-read (the readouts' own cadence, the slice's live re-read).
//   4. A 403 or a 404 is "no coverage to show", silently. An answer for
//      another session, or for another block's group, is not this block's.
//
// MUTANT "tint ignores flagged" (coverageModel.ts `panelCoverage`: the short
// verdict is `false` instead of `p.flagged > 0`, so every stamped panel is
// drawn as covered). Observed, 6/11 passed (5 red):
//   x a flagged panel is tinted on the sky and says how many frames fall short and how bad
//     (the clean, unchecked and empty panels say what they should): 2-1 has no tint: expected "short", got null
//
// disk-reads: server/tests/fixtures/flow_progress_continue.json
// disk-reads: server/tests/fixtures/sequence_state_mosaic.json

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
// Nothing in this file may reach a network: every route is replaced below.
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
const { ApiError } = await import("../../../../api");
const { NODE_DEFS } = await import("../../nodeDefs");
const { framingApi, framingTiming } = await import("../framingApi");
const Sheet = (await import("../TargetFramingSheet")).default;
type FlowNodeRec = import("../../flowsTypes").FlowNodeRec;
type FlowEdgeRec = import("../../flowsTypes").FlowEdgeRec;
type SequenceState = import("../../../../types").SequenceState;
type CoverageAnswer = import("../../../../types").CoverageAnswer;
type CoveragePanel = import("../../../../types").CoveragePanel;
type FlowProgress = import("../../../../lib/flowsApi").FlowProgress;

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
const FIXTURES = "../../../../../../server/tests/fixtures/";
function readFixture(name: string): any {
  try {
    return JSON.parse(readFileSync(new URL(FIXTURES + name, import.meta.url), "utf8") as string);
  } catch (e) {
    throw new Error(`cannot read ${FIXTURES}${name}, a recorded answer this file is graded against: `
      + `${(e as Error).message}`);
  }
}
const PROGRESS = readFixture("flow_progress_continue.json").response as FlowProgress;
const STATES = readFixture("sequence_state_mosaic.json").states as Record<string, SequenceState>;
const SHOOTING = STATES.shooting;
if (!PROGRESS?.session || !SHOOTING?.group) {
  throw new Error("the recorded fixtures do not hold the progress answer and the shooting state");
}
const BLOCK = PROGRESS.blocks[0];
const LIVE_GROUP_ID = SHOOTING.group!.id;
const SESSION_ID = PROGRESS.session!.id;
/** The progress answer with its block given the live group's id (runMode.test's JOINED). */
const JOINED: FlowProgress = { ...PROGRESS, blocks: [{ ...BLOCK, group_id: LIVE_GROUP_ID }] };

const OPERATOR = ["view.status", "view.preview", "view.site_derived", "control.capture", "control.mount"];
const VIEWER = ["view.status", "view.preview"];

function target(): FlowNodeRec {
  return {
    id: BLOCK.node_id, type: "target", x: 0, y: 0,
    params: {
      ...NODE_DEFS.target.params, name: BLOCK.name, ra: "00h 42m 44s", dec: "+41 16 09",
      rows: BLOCK.grid!.rows, cols: BLOCK.grid!.cols, overlap: 25, fovX: 2.0, fovY: 1.33, rotation: 0,
      angle: "Rotate to PA", counts: "Accepted subs", frameAnchor: "",
    },
  };
}
const CYCLE: FlowNodeRec = { id: "cy", type: "cycle", x: 260, y: 0, params: { ...NODE_DEFS.cycle.params } };
const E = (id: string, from: string, fromPort: string, to: string, toPort: string): FlowEdgeRec =>
  ({ id, from, fromPort, to, toPort });
const LANE = [E("a", BLOCK.node_id, "target", "cy", "run"), E("loop", "cy", "pass", BLOCK.node_id, "next")];
const COMPILED = { plan: {}, structural: [], issues: [], unmapped: [] };
const FLOW_ID = PROGRESS.flow_id;

/** The wire of a coverage panel (server test_w16_coverage_route.py ALLOWED),
 *  0-based row and col. */
function panel(row: number, col: number, over: Partial<CoveragePanel> = {}): CoveragePanel {
  return {
    target_id: `m31-${row + 1}-${col + 1}`, name: `M31 ${row + 1}-${col + 1}`, row, col,
    frames: 0, stamped: 0, unstamped: 0, flagged: 0,
    worst_overlap_frac: null, worst_offset_arcmin: null, worst_angle_off_deg: null, ...over,
  };
}
const NOTE = "coverage cannot be checked: frames are not being plate-solved";
/** One flagged panel (2-1), one clean (1-1), one unstamped (1-2), one with no
 *  frames (1-3); 2-2 and 2-3 are not in the answer at all. */
function answer(over: Partial<CoverageAnswer> = {}): CoverageAnswer {
  return {
    session: { id: SESSION_ID, status: "active" }, stamping: true, note: null,
    groups: [{
      group_id: LIVE_GROUP_ID, name: "M31", threshold: 0.875,
      panels: [
        panel(0, 0, { frames: 12, stamped: 12, worst_overlap_frac: 0.93, worst_offset_arcmin: 2.1 }),
        panel(0, 1, { frames: 8, unstamped: 8 }),
        panel(0, 2),
        panel(1, 0, { frames: 43, stamped: 40, unstamped: 3, flagged: 3,
          worst_overlap_frac: 0.4189, worst_offset_arcmin: 12.34, worst_angle_off_deg: 1.2 }),
      ],
    }],
    ...over,
  };
}
const FLAGGED_LINE = "3 of 40 checked frames cover under 87.5% of panel 2-1; "
  + "the worst is 41%, and the furthest is 12.3 arcmin off centre";

// ------------------------------------------------------------------ the network
(framingApi as any).mosaic = () => new Promise(() => {});
(framingApi as any).compileDraft = async () => COMPILED;
(flowsApi as any).compileDraft = async () => COMPILED;
(flowsApi as any).progress = async () => { throw new Error("progress is set in the store here"); };
framingTiming.settleMs = 0;

let coverageAsks: string[] = [];
let coverageReply: () => Promise<CoverageAnswer> = async () => answer();
(framingApi as any).coverage = (flowId: string) => { coverageAsks.push(flowId); return coverageReply(); };

// ------------------------------------------------------------------ the store
useStore.setState({ flowsFetchTonight: async () => {} } as any);

function record(graph: any): any {
  return { id: FLOW_ID, name: "M31 mosaic", folder: "", tagline: "", graph,
    created_ts: 0, updated_ts: 0, last_run: null, last_result: "", readonly: false };
}
function setup(o: { caps?: string[]; progress?: FlowProgress | null } = {}): void {
  const graph = { nodes: [target(), CYCLE], edges: LANE };
  act(() => {
    useStore.setState({
      principal: { role: o.caps === VIEWER ? "viewer" : "operator", email: null, caps: o.caps ?? OPERATOR },
      wsConnected: false,
      status: { sky_angle: null } as any,
      config: null,
      site: null,
      sequence: SHOOTING,
      flows: {
        ...FLOWS_INIT, record: record(graph), graph, compiled: COMPILED,
        progress: o.progress === undefined ? JOINED : o.progress,
      },
    } as any);
  });
  coverageAsks = [];
  coverageReply = async () => answer();
}

const container = win.document.getElementById("root");
const root = createRoot(container);
function mount(viewOnly = true): void {
  act(() => { root.render(null); });
  act(() => {
    root.render(createElement(Sheet, { nodeId: BLOCK.node_id, onClose: () => {}, viewOnly }));
  });
}
function unmount(): void { act(() => { root.render(null); }); }
async function flush(ms = 5): Promise<void> {
  await act(async () => { await new Promise((r) => setTimeout(r, ms)); });
}
const doc = win.document;
const skyLabel = (r: number, c: number) =>
  doc.querySelector(`[data-role="panel-label"][data-row="${r}"][data-col="${c}"]`) as any;
/** Each panel's tint verdict off the sky, by label. */
function tints(): Record<string, string | null> {
  const out: Record<string, string | null> = {};
  for (const el of Array.from(doc.querySelectorAll('[data-role="panel"]')) as any[]) {
    const k = `${+el.getAttribute("data-row") + 1}-${+el.getAttribute("data-col") + 1}`;
    const tinted = el.querySelector('[data-mark="tint"]') !== null;
    const verdict = el.getAttribute("data-coverage");
    assert(tinted === (verdict !== null), `${k}: the tint mark and data-coverage disagree`);
    out[k] = verdict;
  }
  return Object.fromEntries(Object.entries(out).sort(([a], [b]) => a.localeCompare(b)));
}
/** PANELS' coverage lines, by label. */
function coverageRows(): Record<string, string> {
  const out: Record<string, string> = {};
  for (const el of Array.from(doc.querySelectorAll('[data-testid="framing-panel-coverage"]')) as any[]) {
    out[el.getAttribute("data-panel-coverage")] = el.textContent;
  }
  return Object.fromEntries(Object.entries(out).sort(([a], [b]) => a.localeCompare(b)));
}
const notes = () => Array.from(doc.querySelectorAll('[data-testid="framing-coverage-note"]')) as any[];
const NONE_SHORT = { "1-1": null, "1-2": null, "1-3": null, "2-1": null, "2-2": null, "2-3": null };

// ---------------------------------------------------------- the premises
await test("premise: the sky draws all six panels, so the tints below are graded on something", async () => {
  setup();
  mount();
  await flush();
  eq(Object.keys(tints()).sort(), Object.keys(NONE_SHORT).sort(), "the panels the sky draws");
});

// ====================================================================
// 1. a flagged panel is tinted and worded; the rest say only what is true

await test("a flagged panel is tinted on the sky and says how many frames fall short and how bad (the clean, unchecked and empty panels say what they should)", async () => {
  setup();
  mount();
  await flush();
  eq(coverageAsks, [FLOW_ID], "the coverage route is asked once, for the open flow");
  const t = tints();
  eq(t["2-1"], "short", "2-1 has no tint: expected \"short\", got " + JSON.stringify(t["2-1"]));
  eq(t, { ...NONE_SHORT, "2-1": "short" }, "the sky's tints");
  eq(coverageRows(), {
    "1-2": "not checked yet: 8 frames banked, none plate-solved",
    "2-1": FLAGGED_LINE,
  }, "PANELS' coverage lines (1-1 is clean and 1-3 has no frames: both plain)");
  // A screen reader is told what the shape alone says to the eye.
  assert(String(skyLabel(1, 0)?.textContent).includes("covers too little of its panel"),
    `2-1's sky label does not say it falls short: ${skyLabel(1, 0)?.textContent}`);
  assert(!String(skyLabel(0, 0)?.textContent).includes("covers too little"), "1-1's label says it falls short");
  assert(!notes().length, "a note is shown while stamping is on");
  // Never the word covered for a panel nothing was checked on.
  assert(!/covered/i.test(String(coverageRows()["1-2"])), "an unchecked panel is called covered");
});

await test("a single flagged frame is worded in the singular", async () => {
  setup();
  coverageReply = async () => answer({
    groups: [{ group_id: LIVE_GROUP_ID, name: "M31", threshold: 0.875, panels: [
      panel(1, 0, { frames: 1, stamped: 1, flagged: 1, worst_overlap_frac: 0.5, worst_offset_arcmin: 5 })] }],
  });
  mount();
  await flush();
  eq(coverageRows()["2-1"],
    "1 of 1 checked frame covers under 87.5% of panel 2-1; the worst is 50%, and the furthest is 5 arcmin off centre",
    "2-1's line");
});

// ====================================================================
// 2. stamping off: the route's note, once

await test("with stamping off the route's note is said once, not per panel, and a panel checked earlier still says what fell short", async () => {
  setup();
  coverageReply = async () => answer({ stamping: false, note: NOTE });
  mount();
  await flush();
  eq(notes().map((n: any) => n.textContent), [NOTE], "the note, once");
  // 1-2's frames carry no solve and the note says why: no second sentence.
  eq(coverageRows(), { "2-1": FLAGGED_LINE }, "PANELS' coverage lines with stamping off");
  eq(tints()["2-1"], "short", "2-1's tint with stamping off");
});

await test("a note for a mosaic is not shown on a block the answer has no group for", async () => {
  setup();
  coverageReply = async () => answer({ stamping: false, note: NOTE, groups: [] });
  mount();
  await flush();
  assert(!notes().length, "the note of another block's mosaic is shown");
  eq(coverageRows(), {}, "coverage lines for a block with no group");
});

// ====================================================================
// 3. when the route is asked

await test("no coverage request while the sheet is in edit mode, and none after it closes", async () => {
  setup();
  mount(false);
  await flush();
  eq(coverageAsks, [], "asked in edit mode");
  eq(coverageRows(), {}, "edit mode draws coverage lines");
  eq(tints(), NONE_SHORT, "edit mode tints the sky");
  // Open in run mode, close, and let the run's progress be re-read.
  mount(true);
  await flush();
  eq(coverageAsks.length, 1, "asked once on opening in run mode");
  unmount();
  act(() => { useStore.setState({ flows: { ...useStore.getState().flows, progress: { ...JOINED } } } as any); });
  await flush();
  eq(coverageAsks.length, 1, "asked again after the sheet closed");
});

await test("the route is asked again when the run's progress is re-read, not on a timer of its own", async () => {
  setup();
  mount();
  await flush();
  eq(coverageAsks.length, 1, "asked on opening");
  await flush(60);
  eq(coverageAsks.length, 1, "asked again with nothing re-read");
  act(() => { useStore.setState({ flows: { ...useStore.getState().flows, progress: { ...JOINED } } } as any); });
  await flush();
  eq(coverageAsks.length, 2, "asked again after the progress re-read");
});

// ====================================================================
// 4. nothing to show

await test("a 403 and a 404 are no coverage to show, silently", async () => {
  for (const status of [403, 404]) {
    setup();
    coverageReply = async () => { throw new ApiError("no", status); };
    mount();
    await flush();
    eq(coverageAsks.length, 1, `asked once for the ${status}`);
    eq(coverageRows(), {}, `a ${status} draws coverage lines`);
    eq(tints(), NONE_SHORT, `a ${status} tints the sky`);
    assert(!notes().length, `a ${status} shows a note`);
    assert(!doc.querySelector('[role="alert"]'), `a ${status} raised an alert`);
  }
});

await test("a 403 after an answer clears it: what is on screen is not left from a session the viewer may no longer read", async () => {
  setup();
  mount();
  await flush();
  eq(tints()["2-1"], "short", "premise: the answer drew 2-1");
  coverageReply = async () => { throw new ApiError("no", 403); };
  act(() => { useStore.setState({ flows: { ...useStore.getState().flows, progress: { ...JOINED } } } as any); });
  await flush();
  eq(tints(), NONE_SHORT, "the tint after a 403");
  eq(coverageRows(), {}, "the lines after a 403");
});

await test("an answer for another session, or no session, is not this block's", async () => {
  setup();
  coverageReply = async () => answer({ session: { id: "another-session", status: "active" } });
  mount();
  await flush();
  eq(tints(), NONE_SHORT, "another session's tint");
  eq(coverageRows(), {}, "another session's lines");
  unmount();
  coverageReply = async () => answer({ session: null, groups: [] });
  mount();
  await flush();
  eq(tints(), NONE_SHORT, "no session's tint");
});

await test("a viewer reads the same map", async () => {
  setup({ caps: VIEWER });
  mount();
  await flush();
  eq(tints()["2-1"], "short", "the viewer's tint");
  eq(coverageRows()["2-1"], FLAGGED_LINE, "the viewer's line");
});

// ------------------------------------------------------------------ report
act(() => { root.unmount(); });
const total = passed + failed;
console.log(`w6_5CoverageMap.test: ${passed}/${total} passed`);
for (const f of failures) console.log(f);
if (failed) (globalThis as any).process.exitCode = 1;
export const result = { passed, failed, total };
export default result;
