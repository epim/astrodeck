// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w16DormantRetryOnTheSheet.test.tsx - RETRY SET-ASIDE PANELS on a DORMANT
// session, through the real Target modal (#727, backlog WP-141; wave 16
// integration).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/framing/__tests__/w16DormantRetryOnTheSheet.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE GAP. WP-141 made the progress route list a dormant session's standing
// set-aside panels on the block (`set_aside`) and made PanelsSection draw them
// and a RETRY button for them. The button needs a handler, and the sheet
// handed one only in run mode for the LIVE group, calling
// `flowsApi.retrySetAside(runGroupId)` with no session id, so a stored
// session's set-aside panels were drawn with no way to take them back from
// the modal that draws them. The sheet now hands `onRetry` for a dormant
// session whose progress block holds a `set_aside` entry and a group id, and
// the call names the SESSION as well as the group (the dormant route's
// shape: `retrySetAside(group, sessionId)`).
//
// WHAT IS GUARDED
//
//   1. A dormant session with a set-aside panel: the sheet draws RETRY, and
//      the press sends the block's group id WITH the session's id, once, and
//      says what was cleared.
//   2. CONTROLS, each the sheet drawing no RETRY: nothing set aside on the
//      block; a session that is not dormant; a block with no group id.
//   3. A viewer (no `control.mount`) gets the button locked, with the reason.
//
// NAMED MUTANTS (each run from a byte backup of TargetFramingSheet.tsx in the
// worktree, restored and sha256-compared; the first failure is quoted):
//   D1 "dormant retry not offered" (`dormantRetrySession` made null always).
//     Observed, 3/5 passed:
//     x a dormant session's set-aside panel gets RETRY, and the press names
//       the group AND the session: a stored session's set-aside panel has no
//       RETRY on the real sheet: PanelsSection was handed no handler
//   D2 "no session id sent" (`retrySetAside(retryGroupId, dormantRetrySession)`
//     made `retrySetAside(retryGroupId)`). Observed, 4/5 passed:
//     x ...: the retry names the block's group and the dormant session:
//       expected [["m31-mosaic-group","<session id>"]], got
//       [["m31-mosaic-group",null]]
//   D3 "offered for any session" (the `status === "dormant"` test made
//     `session !== undefined`). Observed, 4/5 passed:
//     x CONTROL: a session that is not dormant gets no RETRY from the stored
//       path: a complete session: expected null, got "RETRY SET-ASIDE PANELS"
// (A mutant that offers it with nothing set aside is EQUIVALENT: PANELS draws
// the button only while a row is set aside, so the sheet carries no test of
// its own for it and the CONTROL case below grades PANELS' one.)

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
const { RETRY_NEEDS_ACCESS, retryWords } = sheetModule;
const { RETRY_SET_ASIDE } = await import("../sections/PanelsSection");
type FlowNodeRec = import("../../flowsTypes").FlowNodeRec;
type FlowEdgeRec = import("../../flowsTypes").FlowEdgeRec;
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
const PROGRESS = JSON.parse(
  readFileSync(new URL(FIXTURES + "flow_progress_continue.json", import.meta.url), "utf8") as string,
).response as FlowProgress;
if (PROGRESS?.session?.status !== "dormant") throw new Error("premise: the recorded session is dormant");
const BLOCK = PROGRESS.blocks[0];
const GROUP_ID = "m31-mosaic-group";
const ASIDE = [{ target_id: "t-1-2", name: "M31 1-2", row: 0, col: 1, for_now: false }];

/** The recorded dormant answer with its block's group id and `set_aside`. */
function progress(over: { aside?: any[] | null; group?: string | null; status?: string } = {}): FlowProgress {
  const block: any = { ...BLOCK, group_id: over.group === undefined ? GROUP_ID : over.group };
  const aside = over.aside === undefined ? ASIDE : over.aside;
  if (aside !== null) block.set_aside = aside;
  return {
    ...PROGRESS, blocks: [block],
    session: { ...PROGRESS.session!, status: over.status ?? "dormant" } as any,
  };
}

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
let retryCalls: [string | null, string | undefined][] = [];
const ANSWER = { cleared: ["1-2"], live: false };
(flowsApi as any).retrySetAside = async (group: string | null, sessionId?: string) => {
  retryCalls.push([group, sessionId]);
  return ANSWER;
};

// ------------------------------------------------------------------ the store
const IDLE_SEQUENCE = useStore.getState().sequence;
useStore.setState({ flowsFetchTonight: async () => {} } as any);

function setup(prog: FlowProgress, caps: string[] = OPERATOR): void {
  const graph = { nodes: [target(), CYCLE], edges: LANE };
  act(() => {
    useStore.setState({
      principal: { role: caps === VIEWER ? "viewer" : "operator", email: null, caps },
      wsConnected: false,
      status: { sky_angle: null } as any,
      config: null,
      site: null,
      sequence: IDLE_SEQUENCE,
      flows: {
        ...FLOWS_INIT,
        record: { id: PROGRESS.flow_id, name: "M31 mosaic", folder: "", tagline: "", graph,
          created_ts: 0, updated_ts: 0, last_run: null, last_result: "", readonly: false },
        graph, compiled: COMPILED, progress: prog,
      },
    } as any);
  });
  retryCalls = [];
}

const doc = win.document;
const container = doc.getElementById("root");
const root = createRoot(container);
/** NOT run mode: `viewOnly` false, an idle sequence, a stored session. */
function mountSheet(): void {
  act(() => { root.render(null); });
  act(() => {
    root.render(createElement(Sheet as any, { nodeId: BLOCK.node_id, onClose: () => {}, viewOnly: false }));
  });
}
function unmount(): void { act(() => { root.render(null); }); }
async function flush(ms = 20): Promise<void> {
  await act(async () => { await new Promise((r) => setTimeout(r, ms)); });
}
const q = (id: string) => doc.querySelector(`[data-testid="${id}"]`) as any;
const retryButton = () => q("framing-retry")?.querySelector("button") as any;
function click(el: any): void {
  assert(el, "no element to click");
  act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
}

// ======================================================================= cases

await test("a dormant session's set-aside panel gets RETRY, and the press names the group AND the session", async () => {
  setup(progress());
  mountSheet();
  await flush();
  assert(q("framing-grid"), "premise: the sheet drew its sections");
  const b = retryButton();
  assert(b, "a stored session's set-aside panel has no RETRY on the real sheet: PanelsSection was handed no handler");
  eq(b.textContent, RETRY_SET_ASIDE, "the button's words");
  eq(b.getAttribute("aria-disabled"), null, "an operator's button is live");
  click(b);
  await flush();
  eq(retryCalls, [[GROUP_ID, PROGRESS.session!.id]], "the retry names the block's group and the dormant session");
  eq(q("framing-retry-note")?.textContent, retryWords(ANSWER), "the sheet says what was cleared");
});

await test("CONTROL: a dormant session with nothing set aside on the block gets no RETRY", async () => {
  setup(progress({ aside: null }));
  mountSheet();
  await flush();
  eq(retryButton()?.textContent ?? null, null, "no set_aside key");
  setup(progress({ aside: [] }));
  mountSheet();
  await flush();
  eq(retryButton()?.textContent ?? null, null, "an empty set_aside");
});

await test("CONTROL: a session that is not dormant gets no RETRY from the stored path", async () => {
  setup(progress({ status: "complete" }));
  mountSheet();
  await flush();
  eq(retryButton()?.textContent ?? null, null, "a complete session");
  setup(progress({ status: "active" }));
  mountSheet();
  await flush();
  eq(retryButton()?.textContent ?? null, null, "an active session that is not live here (no run mode)");
});

await test("CONTROL: a block with no group id has nothing to name, so no RETRY", async () => {
  setup(progress({ group: null }));
  mountSheet();
  await flush();
  eq(retryButton()?.textContent ?? null, null, "a null group id");
});

await test("a viewer sees the dormant RETRY locked, with the reason, and the press sends nothing", async () => {
  setup(progress(), VIEWER);
  mountSheet();
  await flush();
  const b = retryButton();
  assert(b, "a viewer sees no RETRY at all");
  eq(b.getAttribute("aria-disabled"), "true", "a viewer's button is locked");
  eq(q("framing-retry-why")?.textContent, RETRY_NEEDS_ACCESS, "its reason is on screen");
  click(b);
  await flush();
  eq(retryCalls, [], "a locked press sent a retry");
});

// ------------------------------------------------------------------- tally
unmount();
const total = passed + failed;
console.log(`w16DormantRetryOnTheSheet.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) (globalThis as any).process.exitCode = 1;
export const result = { passed, failed, total };
export default result;
