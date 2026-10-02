// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w1FlowStagesWaitForOpen.test.tsx - the phone stage list acts only on the
// flow its route opened, not on whatever the store still holds (#553).
//
//   Run directly:  npx tsx src/next/hubs/session/flows/canvas/__tests__/w1FlowStagesWaitForOpen.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT. `FlowStagesPhoneSheet`'s effect was `void flowsOpen(want)`, and
// `flowsOpen` swallows its own failure, leaving the record that was open
// before in place. Nothing compared `record.id` to `want`, so after a failed
// read of flow B (a 404, a dropped request) with flow A still open, the sheet
// titled for B drew A's stage list and RUN was live - a tap on B's row
// starting A on the rig. The fix: every read of the open record now goes
// through `mine` (`want !== "" && record?.id === want`), through
// `openFlowById`; while it is false the sheet draws a waiting card and locks
// RUN and SAVE.
//
// The screen is mounted for real (the real store, `flowsOpen`, `flowsSave`,
// `useFlowRunControls`); only the network is a fake.

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

// The phone layout: this sheet is the phone's own door into a flow.
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
const { useStore } = await import("../../../../../../store");
const {
  FlowStagesPhoneSheet, FLOW_STAGES_NOT_OPENED_REASON,
} = await import("../FlowStagesPhoneSheet");
const { FLOW_OPEN_FAILED } = await import("../../openFlow");

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
const all = (sel: string): any[] => [...container.querySelectorAll(sel)];
const click = (el: any): void => {
  act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
};

const ADMIN_CAPS = [
  "view.status", "view.preview", "control.mount", "control.capture", "view.site_derived",
];

/** Flow A open and clean in the store; every seed clears the run phase and
 *  selection so one case cannot leave another holding a stale lock. */
function seed(): void {
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
      } as never,
    } as never);
  });
}

async function mount(openId: string): Promise<void> {
  await act(async () => { root.render(createElement("div")); });
  await act(async () => {
    root.render(createElement(FlowStagesPhoneSheet as any, { params: { open: openId }, depth: 0 }));
  });
  await settle();
}

// ------------------------------------------------------------------ the cases

await test(
  "a failed read of flow B under B's route draws no stage of flow A, and locks RUN and SAVE",
  async () => {
    bFails = { status: 404, detail: B_REASON };
    try {
      seed();
      await mount(B_ID);

      assert(tid("session-flow-stages") != null, "the sheet did not render");
      assert(asked.includes(`GET /api/flows/${B_ID}`), "premise: flow B's read was attempted");
      eq(useStore.getState().flows.record?.id, A_ID,
        "premise: the failed read left flow A open, as flowsOpen does");

      assert(tid("flow-stages-waiting") != null,
        "no waiting card: the sheet drew something in its place");
      eq(all('[data-testid="flow-stage-row"]').length, 0,
        "a stage of flow A was drawn under flow B's route");
      assert(!/M31 LRGB/.test(container.textContent),
        "flow A's name reached a screen titled for flow B");
      assert(new RegExp(B_REASON).test(container.textContent),
        `the waiting card did not carry the server's own reason: ${container.textContent}`);
      assert(new RegExp(FLOW_OPEN_FAILED, "i").test(container.textContent),
        `the waiting card's title did not say the shared FLOW_OPEN_FAILED sentence: ${container.textContent}`);

      const run = tid("flow-stages-run");
      eq(run.getAttribute("aria-disabled"), "true", "RUN must be locked while waiting");
      eq(run.getAttribute("title"), FLOW_STAGES_NOT_OPENED_REASON, "and say why");
      const save = tid("flow-stages-save");
      eq(save.getAttribute("aria-disabled"), "true", "SAVE must be locked too - its graph is flow A's");

      const runsBefore = asked.filter((a) => a.startsWith("POST ") && a.endsWith("/run")).length;
      click(run);
      await settle();
      eq(asked.filter((a) => a.startsWith("POST ") && a.endsWith("/run")).length, runsBefore,
        "a press on the locked RUN must not start flow A");
    } finally {
      bFails = null;
    }
  },
);

await test(
  "control: a read that lands opens flow B for real, with its own stages and a live RUN",
  async () => {
    bFails = null;
    seed();
    await mount(B_ID);

    eq(useStore.getState().flows.record?.id, B_ID, "flow B must actually open when its read lands");
    assert(tid("flow-stages-waiting") == null, "no waiting card should remain once B has landed");
    eq(all('[data-testid="flow-stage-row"]').length, 1, "flow B's one stage must be listed");
    assert(/M33 Ha/.test(container.textContent), "the sheet must title itself for flow B");
  },
);

await act(async () => { root.unmount(); });

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`w1FlowStagesWaitForOpen.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) {
  (globalThis as unknown as { process?: { exitCode?: number } }).process!.exitCode = 1;
}
export default { passed, failed, total };
export { passed, failed, total };
