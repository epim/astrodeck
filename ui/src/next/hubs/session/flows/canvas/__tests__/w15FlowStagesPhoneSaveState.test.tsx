// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w15FlowStagesPhoneSaveState.test.tsx - the phone stage list says SAVING while
// a PUT is out and does not send a second one beside it (#688, part 2 of 3;
// WP-99, finished at the wave 15 integration).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/next/hubs/session/flows/canvas/__tests__/w15FlowStagesPhoneSaveState.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT. WP-99 gave the #/next toolbar a SAVING word and a SAVE that
// refuses while a PUT is out (`flows.saving`), because the autosave puts a PUT
// out two seconds after the last edit and a SAVE pressed in that gap sent the
// same graph beside the one in flight (two PUTs can answer out of order).
// FlowStagesPhoneSheet.tsx, the phone's own door into a flow, was outside that
// WP's one-rule exception and did not take `saving`: on the phone the pill
// still said UNSAVED EDITS under a PUT already carrying them, and SAVE stayed
// pressable. `saveStateWord`, `saveStateTone` and `saveLockReason` already
// accepted `saving`; the sheet now passes it.
//
// The sheet is mounted for real (the real store, `flowsOpen`, `flowsSave`);
// only the network is a fake, and the PUT is held open with a deferred fetch,
// so SAVING is a state the test stands in, not a moment it races.
//
// Each case names the mutant it kills and quotes what that mutant produced,
// run from a byte-for-byte backup of FlowStagesPhoneSheet.tsx (restored and
// sha256-compared after each run, the mutant's marker grepped absent):
//
// MUTANT "the phone pill ignores saving" (`saveStateWord(dirty, readonly,
// saving)` made `saveStateWord(dirty, readonly)`). Observed, 1/2:
//   x a PUT out says SAVING on the phone, SAVE sends no second one beside it, and the answer says SAVED: a PUT is out and the phone pill still says the edits are unsaved
//     expected SAVING
//     got      UNSAVED EDITS
// MUTANT "the phone SAVE ignores saving" (`saveLockReason(dirty, readonly,
// saving)` made `saveLockReason(dirty, readonly)`). Observed, 1/2:
//   x a PUT out says SAVING on the phone, SAVE sends no second one beside it, and the answer says SAVED: the phone SAVE is pressable while a PUT is out
//     expected true
//     got      null

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
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
win.Element.prototype.setPointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.releasePointerCapture = function () { /* jsdom has none */ };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "localStorage", "sessionStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "ResizeObserver", "requestAnimationFrame", "cancelAnimationFrame", "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------------ fixture
const FLOW_ID = "flow-a";
const RECORD = {
  id: FLOW_ID, name: "M31 LRGB", folder: "My flows", tagline: "", readonly: false,
  created_ts: 1, updated_ts: 2, last_run: null, last_result: "",
  graph: { nodes: [{ id: "n1", type: "dusk", x: 0, y: 0, params: {} }], edges: [] },
};

// ------------------------------------------------------------- the fake rig
// `PUT /api/flows/{id}` is held until a test answers it.
interface HeldPut { body: any; answer: () => void }
let heldPuts: HeldPut[] = [];
const asked: { url: string; method: string }[] = [];
g.fetch = (url: any, init?: { method?: string; body?: string }) => {
  const u = String(url);
  const method = (init?.method ?? "GET").toUpperCase();
  const body = init?.body ? JSON.parse(init.body) : undefined;
  asked.push({ url: u, method });
  const reply = (data: unknown) => ({
    ok: true, status: 200, statusText: "OK",
    headers: { get: () => "application/json" },
    json: async () => data,
    text: async () => JSON.stringify(data),
  });
  if (method === "PUT" && u === `/api/flows/${FLOW_ID}`) {
    return new Promise((resolve) => {
      heldPuts.push({
        body,
        answer: () => resolve(reply({ ...RECORD, ...(body?.flow ?? {}), id: FLOW_ID })),
      });
    });
  }
  if (u === `/api/flows/${FLOW_ID}`) return Promise.resolve(reply(RECORD));
  if (u === "/api/flows/compile") {
    return Promise.resolve(reply({ plan: {}, structural: [], issues: [], unmapped: [] }));
  }
  if (/\/progress$/.test(u)) return Promise.resolve(reply({ session: null, blocks: [] }));
  return Promise.resolve(reply({ ok: true }));
};

// ------------------------------------------------------------------ imports
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../../store");
const { FlowStagesPhoneSheet } = await import("../FlowStagesPhoneSheet");
const {
  SAVE_CLEAN_REASON, SAVE_SAVING_REASON, SAVE_STATE_CLEAN, SAVE_STATE_DIRTY, SAVE_STATE_SAVING,
} = await import("../canvasModel");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function testAsync(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
  finally {
    // A PUT left held would make every later flush of the shared slice wait.
    await act(async () => { for (const p of heldPuts) p.answer(); heldPuts = []; });
  }
}
function assert(cond: unknown, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg}\n  expected ${String(want)}\n  got      ${String(got)}`);
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
const puts = (): number => asked.filter((a) => a.method === "PUT").length;

const ADMIN_CAPS = [
  "view.status", "view.preview", "control.mount", "control.capture", "view.site_derived",
];

/** The flow open and clean, as the sheet's own read leaves it. */
function seed(): void {
  heldPuts = [];
  act(() => {
    const s = useStore.getState();
    useStore.setState({
      principal: { role: "admin", email: null, caps: ADMIN_CAPS } as never,
      authGate: "open",
      status: { connected: { camera: { connected: true } } } as never,
      equipConnected: true,
      wsPhase: "up",
      toasts: [],
      sequence: { state: "idle" } as never,
      flows: {
        ...s.flows,
        record: RECORD as never,
        graph: JSON.parse(JSON.stringify(RECORD.graph)),
        dirty: false, saving: false,
        sel: null, editNode: null, wire: null, tapWire: null,
        statuses: {}, logs: [], compiled: null,
        libraryLoaded: true, libraryError: null,
        progress: null, sessionIds: [],
        run: { ...s.flows.run, phase: "idle", etaS: null, startedAt: null },
      } as never,
    } as never);
  });
}
async function mount(): Promise<void> {
  await act(async () => { root.render(createElement("div")); });
  await act(async () => {
    root.render(createElement(FlowStagesPhoneSheet as any, { params: { open: FLOW_ID }, depth: 0 }));
  });
  await settle();
}
/** An edit that is not yet saved, written after the mount so the sheet's own
 *  open cannot clear it. The graph object is unchanged, so no autosave timer
 *  is armed: the only PUT in this file is the one SAVE sends. */
function edit(): void {
  act(() => {
    const s = useStore.getState();
    useStore.setState({ flows: { ...s.flows, dirty: true } } as never);
  });
}

// ============================================ SAVING, AND ONE PUT AT A TIME

await testAsync("a PUT out says SAVING on the phone, SAVE sends no second one beside it, and the answer says SAVED", async () => {
  seed();
  await mount();
  edit();
  await settle();
  assert(tid("flow-stages-save-state") != null, "premise: the phone sheet drew its save-state pill");
  eq(tid("flow-stages-save-state").textContent, SAVE_STATE_DIRTY, "precondition: the edit is unsaved");
  eq(tid("flow-stages-save").getAttribute("aria-disabled"), null, "precondition: SAVE is live over an edit");

  const before = puts();
  click(tid("flow-stages-save"));
  await settle();
  eq(puts() - before, 1, "precondition: the press sent its PUT");
  eq(tid("flow-stages-save-state").textContent, SAVE_STATE_SAVING,
    "a PUT is out and the phone pill still says the edits are unsaved");
  eq(tid("flow-stages-save").getAttribute("aria-disabled"), "true",
    "the phone SAVE is pressable while a PUT is out");
  eq(tid("flow-stages-save").getAttribute("title"), SAVE_SAVING_REASON, "and it does not say why");
  click(tid("flow-stages-save"));
  await settle();
  eq(puts() - before, 1, "a second press sent the same graph beside the one in flight");

  await act(async () => { heldPuts[0].answer(); });
  await settle();
  eq(tid("flow-stages-save-state").textContent, SAVE_STATE_CLEAN,
    "the PUT was answered and the phone pill is not SAVED");
  eq(tid("flow-stages-save").getAttribute("title"), SAVE_CLEAN_REASON,
    "SAVE has nothing left to say but that");
});

await testAsync("control: with no PUT out an edit says UNSAVED EDITS and SAVE is live", async () => {
  seed();
  const before = puts();
  await mount();
  edit();
  await settle();
  eq(useStore.getState().flows.saving, false, "premise: nothing is out");
  eq(tid("flow-stages-save-state").textContent, SAVE_STATE_DIRTY, "an unsent edit is unsaved");
  eq(tid("flow-stages-save").getAttribute("aria-disabled"), null, "and SAVE is pressable");
  eq(puts() - before, 0, "no PUT was sent by drawing the pill");
});

await act(async () => { root.unmount(); });

// ------------------------------------------------------------------- report
const total = passed + failed;
console.log(`w15FlowStagesPhoneSaveState.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) (globalThis as any).process.exitCode = 1;
export default { passed, failed, total };
export { passed, failed, total };
