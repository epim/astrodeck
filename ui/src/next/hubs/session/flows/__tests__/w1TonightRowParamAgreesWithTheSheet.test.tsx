// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w1TonightRowParamAgreesWithTheSheet.test.tsx - FlowStagesPhoneSheet's
// TONIGHT row and FlowTonightSheet agree on the deep-link's param name
// (#553, backlog WP-08).
//
//   Run directly:  npx tsx src/next/hubs/session/flows/__tests__/w1TonightRowParamAgreesWithTheSheet.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT. FlowStagesPhoneSheet's TONIGHT row pressed
// `nav.sheet("flowTonight", { open: want })` - the same `?open=` every other
// row out of that sheet carries (FlowStagesPhoneSheet's own `params.open`
// read, FlowsCanvasHost, FlowsScreen, FlowFrameSheet) - but FlowTonightSheet
// read `params.id`. The two never agreed on a key, so the route the TONIGHT
// row actually pushed never named a flow to FlowTonightSheet at all: `mine`
// read `wantId === ""` (true, since `params.id` was always undefined) and
// drew whatever was already open - right by accident whenever the canvas's
// own flow was the one wanted, silently wrong the moment it was not (a
// stale flow still open from an earlier visit, say), and never caught
// because nothing failed loudly.
//
// THIS FILE PROVES THE AGREEMENT END TO END, not each side in isolation:
// it presses the real TONIGHT row, reads the real route the press left
// behind, and feeds those exact params into FlowTonightSheet - the shape
// production actually carries between the two components, not a shape this
// test invents for either one.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------------ css stub
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

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/#/session/flows", pretendToBeVisual: true },
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
  "localStorage", "sessionStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "requestAnimationFrame", "cancelAnimationFrame", "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------- the fake rig
const A_ID = "flow-a";

const A_RECORD = {
  id: A_ID, name: "M31 LRGB", folder: "My flows", tagline: "", readonly: false,
  created_ts: 1, updated_ts: 2, last_run: null, last_result: "",
  graph: { nodes: [{ id: "n1", type: "dusk", x: 0, y: 0, params: {} }], edges: [] },
};
const A_TONIGHT = {
  ok: true, reason: "",
  night: { dusk_unix: 1000, dawn_unix: 2000, dark_start_unix: 1200, dark_end_unix: 1800 },
  flats: { start_unix: 1000, end_unix: 1100 },
  moon: { illumination: 0.2, rise_unix: null, set_unix: null },
  brief: "Flow A's own brief sentence.",
  targets: [], story: [],
  campaign: { is_campaign: false, has_pool: false, has_ledger: false, quota: null, note: "", members: [] },
};

const asked: string[] = [];

g.fetch = async (url: any, init?: any) => {
  const u = String(url);
  const method = (init?.method ?? "GET").toUpperCase();
  asked.push(`${method} ${u}`);
  const reply = (status: number, data: unknown) => ({
    ok: status >= 200 && status < 300, status, statusText: String(status),
    headers: { get: () => "application/json" },
    json: async () => data,
    text: async () => JSON.stringify(data),
  });
  if (u === `/api/flows/${A_ID}` && method === "GET") return reply(200, A_RECORD);
  if (u === `/api/flows/${A_ID}/tonight` && method === "GET") return reply(200, A_TONIGHT);
  if (u === "/api/flows/compile") return reply(200, { plan: {}, structural: [], issues: [], unmapped: [] });
  if (/\/progress$/.test(u)) return reply(200, { session: null, blocks: [] });
  return reply(200, { ok: true });
};

// ------------------------------------------------------------------ imports
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { currentRoute } = await import("../../../../router");
const { FlowStagesPhoneSheet } = await import("../canvas/FlowStagesPhoneSheet");
const { FlowTonightSheet } = await import("../tonight/TonightSheet");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: unknown, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (JSON.stringify(got) !== JSON.stringify(want)) {
    throw new Error(`${msg}\n  expected ${JSON.stringify(want)}\n  got      ${JSON.stringify(got)}`);
  }
}

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
const settle = async (n = 8): Promise<void> => {
  for (let i = 0; i < n; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};
const tid = (t: string): any => container.querySelector(`[data-testid="${t}"]`);
const click = (el: any): void => {
  act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
};

const ADMIN_CAPS = [
  "view.status", "view.preview", "control.mount", "control.capture", "view.site_derived",
];

/** `record` (or none at all) open and clean in the store. */
function seed(record: typeof A_RECORD | null): void {
  act(() => {
    const s = useStore.getState();
    useStore.setState({
      principal: { role: "admin", email: null, caps: ADMIN_CAPS } as never,
      authGate: "open",
      sequence: { state: "idle" } as never,
      status: { connected: { camera: { connected: true } } } as never,
      equipConnected: true,
      wsPhase: "up",
      toasts: [],
      flows: {
        ...s.flows,
        record: record as never,
        graph: record ? JSON.parse(JSON.stringify(record.graph)) : { nodes: [], edges: [] },
        dirty: false,
        sel: null, editNode: null, wire: null, tapWire: null,
        statuses: {}, logs: [], compiled: null,
        libraryLoaded: true, libraryError: null,
        progress: null, sessionIds: [],
        run: { ...s.flows.run, phase: "idle", etaS: null },
        tonight: null, tonightLoading: false, tonightError: null,
        calHealth: null, compiling: false,
        ui: { ...s.flows.ui, tonightTab: "timeline" },
      } as never,
    } as never);
  });
}

// ------------------------------------------------------------------ the case

await test(
  "pressing TONIGHT opens a route FlowTonightSheet reads as flow A, not whatever is open when it mounts",
  async () => {
    seed(A_RECORD);
    await act(async () => { root.render(createElement("div")); });
    await act(async () => {
      root.render(createElement(FlowStagesPhoneSheet as any, { params: { open: A_ID }, depth: 0 }));
    });
    await settle();

    const row = tid("flow-stages-tonight");
    assert(row != null, "the TONIGHT row did not render");
    click(row);
    await settle();

    const route = currentRoute();
    eq(route.sheets[route.sheets.length - 1], "flowTonight", "TONIGHT must push the flowTonight sheet");
    eq(route.params, { open: A_ID },
      "the route TONIGHT pushed must carry ?open=<flow id> - not ?id=, which FlowTonightSheet never read");

    // FlowStagesPhoneSheet UNMOUNTS first (its own effects must not fire
    // against the clean slate below), then FlowTonightSheet mounts with
    // NOTHING open yet - a reload, or the canvas's own TONIGHT row (which
    // names no flow in its route), the two ways this sheet is reached
    // beside the one under test. A correct read of `route.params` must
    // still open flow A, the route's own flow, from this clean slate.
    await act(async () => { root.render(createElement("div")); });
    asked.length = 0;
    seed(null);
    await act(async () => {
      root.render(createElement(FlowTonightSheet as any, { params: route.params, depth: 0 }));
    });
    await settle();

    assert(asked[0] === `GET /api/flows/${A_ID}`,
      `the route's own flow (A) was never asked for - the route's flow id never reached the open: ${JSON.stringify(asked)}`);
    assert(tid("tonight-opening") == null,
      "flow A's own open never landed: the waiting card is still up");
    assert(tid("tonight-no-flow") == null,
      "the sheet drew \"no flow is open\" although the route named flow A - the route's id never reached " +
      "the open, so `mine` was read true with nothing open at all");
    assert(asked.includes(`GET /api/flows/${A_ID}/tonight`),
      "flow A's own /tonight was never asked once its open landed");
    assert(/M31 LRGB/.test(container.textContent),
      `the sheet did not end up titled for flow A, the route's own flow: ${container.textContent}`);
  },
);

await act(async () => { root.unmount(); });

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`w1TonightRowParamAgreesWithTheSheet.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) {
  (globalThis as unknown as { process?: { exitCode?: number } }).process!.exitCode = 1;
}
export default { passed, failed, total };
export { passed, failed, total };
