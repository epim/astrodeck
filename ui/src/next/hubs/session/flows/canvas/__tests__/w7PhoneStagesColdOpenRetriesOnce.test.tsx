// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w7PhoneStagesColdOpenRetriesOnce.test.tsx - a cold `?open=<id>` link opens
// the flow on the phone stage sheet too, instead of 404ing forever (#658
// class, W7 follow-on to WP-80).
//
//   Run directly:  npx tsx src/next/hubs/session/flows/canvas/__tests__/w7PhoneStagesColdOpenRetriesOnce.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT (#658 class). `w7ColdOpenRetriesOnce.test.tsx` fixed this exact
// race for `FlowsCanvasHost.tsx` (the tablet/desktop canvas): a fresh tab
// landing on `?open=<id>` mounts the open effect the SAME render
// `FlowsScreen`'s own `flowsLoadLibrary()` starts the flows-list GET, and on
// a server whose single-flow read loses that race, firing straight to
// `setFailure` turns a timing loss into a permanent "That flow did not open".
// `FlowStagesPhoneSheet.tsx` - the phone's own door into a flow - had the
// same race and the pre-fix shape still: `void openFlowById(want).then(...)`,
// with no wait and no retry. This file holds the same two cases against it.
//
// THE FIX reuses `waitForLibrary`/`libraryHasLoaded` from `openFlow.ts`
// (moved there from `FlowsCanvasHost.tsx`, which both this sheet and the
// host already import, rather than from the host directly: the host pulls in
// the canvas barrel, which re-exports this very sheet, so an import the
// other way would be a cycle).
//
// THE NAMED MUTANT (`FlowStagesPhoneSheet.tsx`'s open effect, one line):
// change `if (!libraryHasLoaded()) {` to `if (false) {`, which disables the
// wait and the retry and restores the original "report the first failure"
// behaviour - the same mutant `w7ColdOpenRetriesOnce.test.tsx` uses against
// the host. Run from a byte backup, restored byte-identically after (sha256
// verified), mutant text grepped absent afterward. Under it both cases here
// fail:
//   x a cold load whose first read loses the race still opens the flow, once
//     the list lands: the sheet did not retry exactly once after the library
//     landed, got 1
//   x a cold load whose retry ALSO fails reports the precise reason, not a
//     permanent hang: expected exactly one retry (two reads total), got 1

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
const FLOW_ID = "cold-open-phone-flow";
const FLOW_RECORD = {
  id: FLOW_ID, name: "Cold Open Phone Target", folder: "My flows", tagline: "",
  readonly: false, created_ts: 1, updated_ts: 2, last_run: null, last_result: "",
  graph: { nodes: [{ id: "n1", type: "dusk", x: 0, y: 0, params: {} }], edges: [] },
};
const COLD_REASON = `no flow named ${FLOW_ID}`;

/** How many times `GET /api/flows/<FLOW_ID>` has been asked. The race this
 *  file reproduces answers the FIRST ask with a 404 (the cold-load loss) and
 *  every ask after it with the real record - as if whatever the list GET
 *  primes is now in place. */
let flowAsks = 0;
/** Set false to make every attempt fail, for the "retry also fails" case. */
let secondAttemptLands = true;
/** When set, `GET /api/flows` and `GET /api/flows/folders` do not resolve
 *  until this is released - the library side of the race. */
let libraryGate: Promise<void> = Promise.resolve();

const g_fetch = async (url: any, init?: any) => {
  const u = String(url);
  const method = (init?.method ?? "GET").toUpperCase();
  const reply = (status: number, data: unknown) => ({
    ok: status >= 200 && status < 300, status, statusText: String(status),
    headers: { get: () => "application/json" },
    json: async () => data,
    text: async () => JSON.stringify(data),
  });
  if (u === `/api/flows/${FLOW_ID}` && method === "GET") {
    flowAsks += 1;
    if (flowAsks === 1) return reply(404, { detail: COLD_REASON });
    if (!secondAttemptLands) return reply(404, { detail: COLD_REASON });
    return reply(200, FLOW_RECORD);
  }
  if (u === "/api/flows" && method === "GET") { await libraryGate; return reply(200, [FLOW_RECORD]); }
  if (u === "/api/flows/folders" && method === "GET") {
    await libraryGate;
    return reply(200, [{ name: "My flows", readonly: false }]);
  }
  if (u === "/api/flows/compile") return reply(200, { plan: {}, structural: [], issues: [], unmapped: [] });
  if (/\/progress$/.test(u)) return reply(200, { session: null, blocks: [] });
  return reply(200, { ok: true });
};
g.fetch = g_fetch;

// ------------------------------------------------------------------ imports
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../../store");
const { resetRouterCacheForTests } = await import("../../../../../router");
const { FLOW_OPEN_FAILED } = await import("../../openFlow");
const { FlowStagesPhoneSheet } = await import("../FlowStagesPhoneSheet");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: unknown, msg: string): void { if (!cond) throw new Error(msg); }

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
const settle = async (n = 10): Promise<void> => {
  for (let i = 0; i < n; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};
const tid = (t: string): any => container.querySelector(`[data-testid="${t}"]`);

const ADMIN_CAPS = [
  "view.status", "view.preview", "control.mount", "control.capture", "view.site_derived",
];

/** The store as `FLOWS_INIT` leaves it, plus the route a fresh tab would
 *  carry - a COLD load: no record open, the library not yet loaded. */
function seedCold(): void {
  win.location.hash = `#/session/flows?open=${FLOW_ID}`;
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
        record: null, graph: { nodes: [], edges: [] }, dirty: false,
        sel: null, editNode: null, wire: null, tapWire: null,
        statuses: {}, logs: [], compiled: null,
        cards: [], folders: [], libraryLoaded: false, libraryError: null,
        progress: null, sessionIds: [],
        run: { ...s.flows.run, phase: "idle", etaS: null },
      } as never,
    } as never);
  });
}

/** Detach whatever is mounted BEFORE the next test seeds the store, same
 *  reason `w7ColdOpenRetriesOnce.test.tsx` gives: a flow that opened in a
 *  previous case leaves the sheet subscribed to `flows.record`, and
 *  `seedCold()`'s own reset back to `record: null` would otherwise be read
 *  as a stray close, re-arming the open effect uncounted by this file. */
async function unmount(): Promise<void> {
  await act(async () => { root.render(createElement("div")); });
}

async function mount(): Promise<void> {
  await act(async () => {
    root.render(createElement(FlowStagesPhoneSheet as any, { params: { open: FLOW_ID }, depth: 0 }));
  });
}

// ------------------------------------------------------------------ the cases

await test(
  "a cold load whose first read loses the race still opens the flow, once the list lands",
  async () => {
    flowAsks = 0;
    secondAttemptLands = true;
    let releaseLibrary: () => void = () => {};
    libraryGate = new Promise((resolve) => { releaseLibrary = resolve; });

    await unmount();
    seedCold();
    await mount();
    // `FlowsScreen`'s own effect, started the same render as this sheet's -
    // not this file's to own, so it is simulated rather than imported.
    void useStore.getState().flowsLoadLibrary();
    await settle(2);

    // PREMISE: the first read really did lose the race, and the flow is not
    // open yet - otherwise this proves nothing about the retry.
    assert(flowAsks === 1, `premise: exactly one read before the library landed, got ${flowAsks}`);
    assert(useStore.getState().flows.record?.id !== FLOW_ID,
      "premise: the first, cold read must not have landed the flow");
    assert(tid("flow-stages-waiting") != null,
      "premise: the sheet is still waiting, not yet showing a permanent failure");

    // Release the list GETs; the retry should follow once `libraryLoaded` flips.
    releaseLibrary();
    await settle(6);

    assert(useStore.getState().flows.libraryLoaded, "premise: the library did land");
    assert(flowAsks === 2, `the sheet did not retry exactly once after the library landed, got ${flowAsks}`);
    assert(useStore.getState().flows.record?.id === FLOW_ID,
      "the flow never opened even though its retry read succeeded");
    assert(tid("flow-stages-waiting") == null,
      "the waiting card never cleared even though the retry landed");
    assert(tid("session-flow-stages") != null, "the stage list did not render once the flow opened");
  },
);

await test(
  "a cold load whose retry ALSO fails reports the precise reason, not a permanent hang",
  async () => {
    flowAsks = 0;
    secondAttemptLands = false;
    libraryGate = Promise.resolve();

    await unmount();
    seedCold();
    await mount();
    void useStore.getState().flowsLoadLibrary();
    await settle(10);

    assert(flowAsks === 2, `expected exactly one retry (two reads total), got ${flowAsks}`);
    assert(tid("flow-stages-waiting") != null, "a flow that never opens must still show a card");
    assert(new RegExp(COLD_REASON).test(container.textContent),
      `the failure card did not carry the server's own reason: ${container.textContent}`);
    assert(new RegExp(FLOW_OPEN_FAILED, "i").test(container.textContent),
      `the failure card's title did not say the shared FLOW_OPEN_FAILED sentence: ${container.textContent}`);
  },
);

await unmount();

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`w7PhoneStagesColdOpenRetriesOnce.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) {
  (globalThis as unknown as { process?: { exitCode?: number } }).process!.exitCode = 1;
}
export default { passed, failed, total };
export { passed, failed, total };
