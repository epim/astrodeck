// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w1FlowsCanvasHostWaitForOpen.test.tsx - the tablet/desktop canvas host acts
// only on the flow its route opened, not on whatever the store still holds
// (#553).
//
//   Run directly:  npx tsx src/next/hubs/session/flows/__tests__/w1FlowsCanvasHostWaitForOpen.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT. `FlowsCanvasHost`'s effect was `void flowsOpen(named)`, and
// `flowsOpen` swallows its own failure, leaving the record that was open
// before in place. Nothing compared `record.id` to `named` before mounting
// `FlowCanvasSurface` and `FlowCanvasToolbar` - both of which read the open
// record straight off the store, the toolbar's RUN included - so a failed
// read of flow B with flow A still open drew A's graph and A's live RUN
// under a route naming B. The fix: `mine` (`named !== null && record?.id ===
// named`), through `openFlowById`, gates every one of those three panels;
// while it is false the host shows a waiting card and mounts none of them.

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
  { url: "http://local/", pretendToBeVisual: true },
);
const win = dom.window as any;

// Tablet width: wide enough for the canvas, narrow enough that neither the
// docked palette rail nor the docked inspector column (desktop-only) adds
// requirements this file does not need to seed.
win.matchMedia = (q: string) => {
  const m = /min-width:\s*(\d+)px/.exec(q);
  return {
    matches: m ? 820 >= Number(m[1]) : false,
    addEventListener() {}, removeEventListener() {},
    addListener() {}, removeListener() {},
  };
};
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
const B_ID = "flow-b";
const B_REASON = "no flow named flow-b";

const A_RECORD = {
  id: A_ID, name: "M31 LRGB", folder: "My flows", tagline: "", readonly: false,
  created_ts: 1, updated_ts: 2, last_run: null, last_result: "",
  graph: { nodes: [{ id: "n1", type: "dusk", x: 0, y: 0, params: {} }], edges: [] },
};
const B_RECORD = {
  id: B_ID, name: "M33 Ha", folder: "My flows", tagline: "", readonly: false,
  created_ts: 3, updated_ts: 4, last_run: null, last_result: "",
  graph: { nodes: [{ id: "n1", type: "slew", x: 0, y: 0, params: {} }], edges: [] },
};

/** When set, `GET /api/flows/flow-b` answers this status/body instead of 200. */
let bFails: { status: number; detail: string } | null = null;
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
  if (u === `/api/flows/${B_ID}` && method === "GET") {
    return bFails ? reply(bFails.status, { detail: bFails.detail }) : reply(200, B_RECORD);
  }
  if (u === `/api/flows/${A_ID}` && method === "GET") return reply(200, A_RECORD);
  if (u === "/api/flows/compile") return reply(200, { plan: {}, structural: [], issues: [], unmapped: [] });
  if (/\/progress$/.test(u)) return reply(200, { session: null, blocks: [] });
  return reply(200, { ok: true });
};

// ------------------------------------------------------------------ imports
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { resetRouterCacheForTests } = await import("../../../../router");
const { FLOW_OPEN_FAILED } = await import("../openFlow");
const { FlowsCanvasHost } = await import("../FlowsCanvasHost");

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

const ADMIN_CAPS = [
  "view.status", "view.preview", "control.mount", "control.capture", "view.site_derived",
];

/** Flow A open and clean in the store, and the route on `#/session/flows?open=<openId>`
 *  - the host's own "stranded by nav" effect calls `leaveFlowEditor()` when the
 *  route names no flow at all, so the hash has to agree with what is mounted. */
function seed(openId: string): void {
  win.location.hash = `#/session/flows?open=${openId}`;
  resetRouterCacheForTests();
  act(() => {
    const s = useStore.getState();
    useStore.setState({
      principal: { role: "admin", email: null, caps: ADMIN_CAPS } as never,
      authGate: "open",
      status: { connected: { camera: { connected: true } } } as never,
      equipConnected: true,
      wsPhase: "up",
      toasts: [],
      flows: {
        ...s.flows,
        record: A_RECORD as never,
        graph: JSON.parse(JSON.stringify(A_RECORD.graph)),
        dirty: false,
        sel: null, editNode: null, wire: null, tapWire: null,
        statuses: {}, logs: [], compiled: null,
        libraryLoaded: true, libraryError: null,
        progress: null, sessionIds: [],
        run: { ...s.flows.run, phase: "idle", etaS: null },
        ui: { ...s.flows.ui, screen: "editor" },
      } as never,
    } as never);
  });
}

async function mount(openProp: string): Promise<void> {
  await act(async () => { root.render(createElement("div")); });
  await act(async () => { root.render(createElement(FlowsCanvasHost as any, { open: openProp })); });
  await settle();
}

// ------------------------------------------------------------------ the cases

await test(
  "a failed read of flow B under B's route mounts neither the canvas nor the toolbar of flow A",
  async () => {
    bFails = { status: 404, detail: B_REASON };
    try {
      seed(B_ID);
      await mount(B_ID);

      assert(tid("session-flows-canvas") != null, "the host did not render");
      assert(asked.includes(`GET /api/flows/${B_ID}`), "premise: flow B's read was attempted");
      eq(useStore.getState().flows.record?.id, A_ID,
        "premise: the failed read left flow A open, as flowsOpen does");

      assert(tid("flows-canvas-waiting") != null,
        "no waiting card: the host drew something in its place");
      assert(tid("flows-canvas") == null, "flow A's canvas was drawn under flow B's route");
      assert(tid("flow-toolbar") == null,
        "flow A's toolbar - and its live RUN - was mounted under flow B's route");
      assert(!/M31 LRGB/.test(container.textContent),
        "flow A's name reached a screen the route names flow B for");
      assert(new RegExp(B_REASON).test(container.textContent),
        `the waiting card did not carry the server's own reason: ${container.textContent}`);
      assert(new RegExp(FLOW_OPEN_FAILED, "i").test(container.textContent),
        `the waiting card's title did not say the shared FLOW_OPEN_FAILED sentence: ${container.textContent}`);
    } finally {
      bFails = null;
    }
  },
);

await test(
  "control: a read that lands opens flow B for real, with its own canvas and toolbar",
  async () => {
    bFails = null;
    seed(B_ID);
    await mount(B_ID);

    eq(useStore.getState().flows.record?.id, B_ID, "flow B must actually open when its read lands");
    assert(tid("flows-canvas-waiting") == null, "no waiting card should remain once B has landed");
    assert(tid("flows-canvas") != null, "flow B's canvas must render once it is open");
    assert(tid("flow-toolbar") != null, "flow B's toolbar must render once it is open");
    assert(/M33 Ha/.test(container.textContent), "the toolbar must name flow B");
  },
);

await act(async () => { root.unmount(); });

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`w1FlowsCanvasHostWaitForOpen.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) {
  (globalThis as unknown as { process?: { exitCode?: number } }).process!.exitCode = 1;
}
export default { passed, failed, total };
export { passed, failed, total };
