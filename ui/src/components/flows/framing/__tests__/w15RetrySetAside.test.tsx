// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w15RetrySetAside.test.tsx - the PANELS section's RETRY SET-ASIDE PANELS
// button, alone and in the Target modal's run mode (#600; backlog ruling
// D-07, owner-approved 2026-09-30; WP-104).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/framing/__tests__/w15RetrySetAside.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE RULE. A panel set aside for the night stayed out of it until tomorrow
// even once the operator had fixed the cause. While a run is live and some
// row of PANELS says it is set aside, a button under the rows asks the run to
// take them back: `flowsApi.retrySetAside(group)`, with the group id the
// progress block names (the live `state.group.id`). It is drawn for no panel
// that is not set aside, locked (and said to be, in a line of its own, since
// the sheet's "explained" banner is not drawn read-only) for a principal
// without `control.mount`, and it WORKS IN RUN MODE: the sheet is read-only
// exactly while a run is live, and a button inside a disabled fieldset is
// natively disabled whatever its own props say, so PANELS carries its own
// fieldset for the controls a frozen draft disables and the button stands
// outside it.
//
// THE RUNS: PanelsSection mounted alone on `panelRows`, and the real sheet in
// run mode on the recorded progress answer and sequence state (the fixtures
// runMode.test.tsx grades), with only the network replaced. Every mutant was
// run from a byte backup of the file named, restored and sha256-compared
// after each, the mutant's marker grepped absent (#254); the failure it
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
const { RETRY_NEEDS_ACCESS, RETRY_IN_FLIGHT, retryWords } = sheetModule;
const { PanelsSection, panelRows, RETRY_SET_ASIDE } = await import("../sections/PanelsSection");
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
assert(SHOOTING.group!.set_aside.length === 1,
  "premise: the recorded shooting state has one set-aside panel");
const BLOCK = PROGRESS.blocks[0];
const LIVE_GROUP_ID = SHOOTING.group!.id;
const JOINED: FlowProgress = { ...PROGRESS, blocks: [{ ...BLOCK, group_id: LIVE_GROUP_ID }] };
/** The same state with nothing set aside: the run is live and every panel is
 *  in play. */
const NOTHING_ASIDE: SequenceState = {
  ...SHOOTING, group: { ...SHOOTING.group!, set_aside: [] },
} as SequenceState;

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

// ------------------------------------------------------------------ the network
(framingApi as any).mosaic = () => new Promise(() => {});
(framingApi as any).compileDraft = async () => COMPILED;
(flowsApi as any).compileDraft = async () => COMPILED;
(flowsApi as any).progress = async () => { throw new Error("not asked in this file"); };
framingTiming.settleMs = 0;
/** Every RETRY the sheet sent: `[group, sessionId]`. */
let retryCalls: [string | null, string | undefined][] = [];
/** While set, the retry waits on it, so a test can look at the sheet while
 *  the request is out. */
let retryGate: Promise<void> | null = null;
let retryFails: string | null = null;
(flowsApi as any).retrySetAside = async (group: string | null, sessionId?: string) => {
  retryCalls.push([group, sessionId]);
  if (retryGate) await retryGate;
  if (retryFails) throw new Error(retryFails);
  return { queued: ["2-2"], live: true };
};

// ------------------------------------------------------------------ the store
const real = useStore.getState();
const IDLE_SEQUENCE = real.sequence;
useStore.setState({ flowsFetchTonight: async () => {} } as any);

function setup(o: { caps?: string[]; sequence?: SequenceState } = {}): void {
  const graph = { nodes: [target(), CYCLE], edges: LANE };
  act(() => {
    useStore.setState({
      principal: { role: o.caps === VIEWER ? "viewer" : "operator", email: null, caps: o.caps ?? OPERATOR },
      wsConnected: false,
      status: { sky_angle: null } as any,
      config: null,
      site: null,
      sequence: o.sequence ?? IDLE_SEQUENCE,
      flows: {
        ...FLOWS_INIT,
        record: { id: PROGRESS.flow_id, name: "M31 mosaic", folder: "", tagline: "", graph,
          created_ts: 0, updated_ts: 0, last_run: null, last_result: "", readonly: false },
        graph, compiled: COMPILED, progress: JOINED,
      },
    } as any);
  });
  retryCalls = []; retryGate = null; retryFails = null;
}

const container = win.document.getElementById("root");
const root = createRoot(container);
function mountSheet(viewOnly = true): void {
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
const q = (id: string) => doc.querySelector(`[data-testid="${id}"]`) as any;
const retryButton = () => q("framing-retry")?.querySelector("button") as any;
function click(el: any): void {
  assert(el, "no element to click");
  act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
}

// ------------------------------------------------- PANELS alone, on panelRows

/** 2x2 PANELS rows in the given run states ("2-2" set aside, say). */
function rowsWith(run: Record<string, any>) {
  return panelRows({ rows: 2, cols: 2, skip: [], progress: null, answerPanels: null,
    order: "Least complete first", run });
}
const ASIDE_2_2 = { "2-2": { kind: "set_aside", reason: "rejected every frame", forNow: false } };
let explained: string[] = [];
let retries = 0;
function mountSection(over: Record<string, unknown> = {}): void {
  act(() => { root.render(null); });
  explained = []; retries = 0;
  act(() => {
    root.render(createElement(PanelsSection, {
      rows: rowsWith(ASIDE_2_2), order: "Least complete first", showAltitude: false,
      nightCard: null, onOrder: () => {}, onToggle: () => {},
      onRetry: () => { retries++; }, explain: (why: string) => { explained.push(why); },
      ...over,
    } as any));
  });
}

// MUTANT "button always shown" (PanelsSection.tsx `retryShown` made
// `p.onRetry !== undefined`, no longer asking whether a row is set aside).
// Observed:
//   x the retry button is drawn only while some panel is set aside: a live
//     run with every panel in play: no button when no row is set aside:
//     expected null, got "RETRY SET-ASIDE PANELS"
await test("the retry button is drawn only while some panel is set aside: a live run with every panel in play", () => {
  // No row set aside: nothing to bring back, so no button, though a handler
  // is given. A panel the run is shooting is no set-aside panel either.
  mountSection({ rows: rowsWith({ "1-1": { kind: "shooting" } }) });
  eq(retryButton()?.textContent ?? null, null, "no button when no row is set aside");
  mountSection({ rows: rowsWith({}) });
  eq(retryButton()?.textContent ?? null, null, "no button when no row says anything");
  // A set-aside row: the button, in its words.
  mountSection();
  eq(retryButton()?.textContent, RETRY_SET_ASIDE, "the button when 2-2 is set aside");
  eq(RETRY_SET_ASIDE, "RETRY SET-ASIDE PANELS", "the label");
  // A set-aside FOR NOW counts as set aside: its expiry is 45 minutes away.
  mountSection({ rows: rowsWith({ "2-2": { kind: "set_aside", reason: "centring failed", forNow: true } }) });
  eq(retryButton()?.textContent, RETRY_SET_ASIDE, "the button when 2-2 is set aside for now");
  // No handler (the draft, not a run): no button, the rows alone.
  mountSection({ onRetry: undefined });
  eq(retryButton()?.textContent ?? null, null, "no button outside a run, where nothing hands a handler");
});

await test("a live button calls the handler once per press, says nothing, and a locked one says why and calls nothing", () => {
  mountSection();
  click(retryButton());
  eq([retries, explained], [1, []], "one press, a live button");
  eq(retryButton().getAttribute("aria-disabled"), null, "a live button is not aria-disabled");
  assert(q("framing-retry-why") === null, "a live button draws no reason line");
  mountSection({ retryLocked: "needs operator access" });
  const b = retryButton();
  eq(b.getAttribute("aria-disabled"), "true", "a locked button says so to a screen reader");
  eq(b.getAttribute("title"), "needs operator access", "its reason is its tooltip");
  eq(q("framing-retry-why")?.textContent, "needs operator access", "its reason on screen, since a touch screen has no hover");
  click(b);
  eq([retries, explained], [0, ["needs operator access"]], "a locked press explains and calls nothing");
  mountSection({ retryNote: "Retry queued for 2-2" });
  eq(q("framing-retry-note")?.textContent, "Retry queued for 2-2", "what the last press did");
});

// MUTANT "button inside the frozen fieldset" (PanelsSection.tsx's `</fieldset>`
// moved below the retry bar, so the button stands in the fieldset a read-only
// sheet disables). Observed:
//   x a frozen PANELS freezes its ORDER and SHOOT controls and leaves RETRY
//     pressable: the retry button is natively disabled: expected false, got true
await test("a frozen PANELS freezes its ORDER and SHOOT controls and leaves RETRY pressable", () => {
  mountSection({ frozen: true });
  const select = doc.querySelector("#tfs-order") as any;
  const toggles = Array.from(doc.querySelectorAll(".tfs-c-on button") as any[]);
  assert(toggles.length === 4, `premise: four SHOOT toggles: ${toggles.length}`);
  eq([select.matches(":disabled"), toggles.map((t: any) => t.matches(":disabled"))],
    [true, [true, true, true, true]], "the draft's controls while frozen");
  eq(retryButton().matches(":disabled"), false, "the retry button is natively disabled");
  click(retryButton());
  eq(retries, 1, "a press on a frozen PANELS' retry button");
  // CONTROL: unfrozen (an editable sheet, whose rows say nothing of a run)
  // leaves the draft's controls live, as every caller before #600 had them.
  mountSection({ frozen: false });
  eq((doc.querySelector("#tfs-order") as any).matches(":disabled"), false, "ORDER unfrozen");
});

// -------------------------------------------------------- the sheet, run mode

// MUTANT "sends no group id" (TargetFramingSheet.tsx's
// `flowsApi.retrySetAside(runGroupId)` made `retrySetAside(null)`, every
// group). Observed:
//   x in run mode an operator's press sends the live group's id, once, and
//     the sheet says what was queued: the retry's calls: expected
//     [["m31-mosaic",null]], got [[null,null]]
await test("in run mode an operator's press sends the live group's id, once, and the sheet says what was queued", async () => {
  setup({ sequence: SHOOTING });
  mountSheet();
  const b = retryButton();
  assert(b, "run mode with a set-aside panel draws no retry button");
  eq(b.matches(":disabled"), false, "the retry button is natively disabled in run mode");
  // The rest of the sheet is frozen, as run mode freezes it: the retry
  // button is the ONE control of the scroller that run mode leaves live (the
  // run-mode case of runMode.test.tsx, which asserts no control is live,
  // names a recorded state with a panel set aside, so it holds exactly this).
  eq((doc.querySelector("#tfs-order") as any).matches(":disabled"), true, "ORDER in run mode");
  const controls = Array.from(doc.querySelectorAll(
    ".tfs-scroller button, .tfs-scroller input, .tfs-scroller select, .tfs-scroller textarea") as any[]);
  assert(controls.length > 10, `premise: the scroller holds its controls: ${controls.length}`);
  eq(controls.filter((el) => !el.matches(":disabled")).map((el) => el.textContent),
    [RETRY_SET_ASIDE], "the controls run mode leaves live");
  let release: () => void = () => {};
  retryGate = new Promise<void>((r) => { release = r; });
  click(b);
  await flush();
  eq(retryCalls.map(([grp, sid]) => [grp, sid ?? null]), [[LIVE_GROUP_ID, null]], "the retry's calls");
  // While the request is out the button is locked and says why, so a second
  // press is not a second request.
  eq(retryButton().getAttribute("aria-disabled"), "true", "the button while the request is out");
  eq(q("framing-retry-why")?.textContent, RETRY_IN_FLIGHT, "its reason while the request is out");
  click(retryButton());
  eq(retryCalls.length, 1, "a press while the request is out");
  release();
  await flush();
  eq(q("framing-retry-note")?.textContent, retryWords({ queued: ["2-2"] }), "what the press did");
  assert(String(q("framing-retry-note")?.textContent).includes("2-2"), "the note names the panel");
  eq(retryButton().getAttribute("aria-disabled"), null, "the button once the answer is in");
});

await test("a refused retry is said in the server's words, and the button is live for another try", async () => {
  setup({ sequence: SHOOTING });
  retryFails = "nothing is set aside";
  mountSheet();
  click(retryButton());
  await flush();
  eq(q("framing-retry-note")?.textContent, "The retry was refused: nothing is set aside", "the refusal");
  eq(retryButton().getAttribute("aria-disabled"), null, "the button after a refusal");
});

// MUTANT "viewer not locked" (TargetFramingSheet.tsx's `retryLocked` always
// null). Observed:
//   x a viewer sees the button locked, with the reason on screen, and a press
//     sends nothing: a viewer's button is not locked: expected "true", got null
await test("a viewer sees the button locked, with the reason on screen, and a press sends nothing", async () => {
  setup({ sequence: SHOOTING, caps: VIEWER });
  mountSheet();
  const b = retryButton();
  assert(b, "a viewer is shown no retry button (it is there, locked, so the reason can be read)");
  eq(b.getAttribute("aria-disabled"), "true", "a viewer's button is not locked");
  eq(q("framing-retry-why")?.textContent, RETRY_NEEDS_ACCESS, "the reason on screen");
  assert(RETRY_NEEDS_ACCESS.includes("operator"), `the reason names who may: ${RETRY_NEEDS_ACCESS}`);
  click(b);
  await flush();
  eq(retryCalls, [], "a viewer's press reached the route");
});

await test("run mode with no panel set aside draws no retry button, and an editable sheet draws none", () => {
  setup({ sequence: NOTHING_ASIDE });
  mountSheet();
  assert(q("framing-panels"), "premise: PANELS is drawn");
  eq(retryButton()?.textContent ?? null, null, "a live run with every panel in play");
  // The same state in an editable sheet (no run mode): rows carry no run
  // state, so no panel reads set aside and nothing hands a handler.
  setup({ sequence: SHOOTING });
  mountSheet(false);
  eq(retryButton()?.textContent ?? null, null, "an editable sheet");
});

await test("retryWords names what the server queued or cleared, and says so when it was nothing", () => {
  eq(retryWords({ queued: ["2-2"] }).startsWith("Retry queued for 2-2: the run takes it up"), true, "one panel");
  assert(retryWords({ queued: ["1-2", "2-2"] }).includes("1-2, 2-2"), "two panels");
  eq(retryWords({ cleared: ["2-2"] }), "Cleared 2-2 for tonight.", "a stored session");
  eq(retryWords(null), "Nothing was set aside.", "no answer");
});

// ------------------------------------------------------------------ report
act(() => { root.unmount(); });
const total = passed + failed;
console.log(`w15RetrySetAside.test: ${passed}/${total} passed`);
for (const f of failures) console.log(f);
if (failed) (globalThis as any).process.exitCode = 1;
export const result = { passed, failed, total };
export default result;
