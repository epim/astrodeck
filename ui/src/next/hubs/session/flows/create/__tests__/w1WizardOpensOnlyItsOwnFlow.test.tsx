// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w1WizardOpensOnlyItsOwnFlow.test.tsx - GENERATE FLOW and START BLANK
// navigate only once the flow they just made has actually opened (#553).
//
//   Run directly:  npx tsx src/next/hubs/session/flows/create/__tests__/w1WizardOpensOnlyItsOwnFlow.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT. `generate` and `startBlank` both did `await flowsOpen(rec.id);
// nav.go(newFlowRoute(rec.id, phone));` - `flowsOpen` swallows its own
// failure and leaves whatever was open before in place, and since #450 it
// also REFUSES to replace a flow whose unsaved edits its save did not keep.
// Either way the wizard navigated onto the new route regardless, landing on
// `FlowsCanvasHost` or `FlowStagesPhoneSheet` while they drew the OTHER
// flow's graph (both #553). The fix: navigate only when `openFlowById`
// answers true; on false, stay on the wizard sheet and toast, naming where
// the new flow (already saved) can be found - the same door
// `sky/sheets/quick.tsx` GENERATE already uses.

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

win.matchMedia = (q: string) => {
  const m = /min-width:\s*(\d+)px/.exec(q);
  return {
    matches: m ? 1024 >= Number(m[1]) : false,
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
  "localStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "requestAnimationFrame", "cancelAnimationFrame", "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------- the fake rig
const A_ID = "flow-a";
const GENERATED_ID = "gen-1";
const GENERATED_READ_REASON = "no flow named gen-1";

const A_RECORD = {
  id: A_ID, name: "Edited flow", folder: "My flows", tagline: "", readonly: false,
  created_ts: 1, updated_ts: 2, last_run: null, last_result: "",
  graph: { nodes: [{ id: "t1", type: "target", x: 40, y: 40, params: { name: "M31" } }], edges: [] },
};
const GENERATED = {
  id: GENERATED_ID, name: "Deep-sky: M16", folder: "My flows", tagline: "generated",
  readonly: false, graph: { nodes: [], edges: [] },
  last_run: null, last_result: "", updated_ts: 1_757_000_000,
};

/** When true, flow A's PUT (the save-first rule on an open of another id, #450)
 *  refuses with a 500, which is what makes the wizard's own open refuse too. */
let refuseSaveOfA = false;
/** When true, the read of the flow the wizard just generated answers 404 - a
 *  plain dropped-request failure, no unsaved edit involved. */
let failGeneratedRead = false;

const asked: { url: string; method: string }[] = [];
g.fetch = async (url: string, init?: { method?: string; body?: string }) => {
  const method = init?.method ?? "GET";
  asked.push({ url, method });
  const ok = (data: unknown) => ({
    ok: true, status: 200, statusText: "OK",
    headers: { get: () => "application/json" },
    json: async () => data,
  });
  const fail = (status: number, data: unknown) => ({
    ok: false, status, statusText: String(status),
    headers: { get: () => "application/json" },
    json: async () => data,
  });
  if (url === "/api/flows/wizard" && method === "POST") return ok(GENERATED);
  if (url === "/api/flows" && method === "POST") return ok({ ...GENERATED, id: "blank-1" });
  if (url === `/api/flows/${A_ID}` && method === "PUT") {
    return refuseSaveOfA ? fail(500, { detail: "the flow store is not writable" }) : ok({ ...A_RECORD, updated_ts: 99 });
  }
  if (/^\/api\/flows\/[^/]+$/.test(url) && method === "GET") {
    return failGeneratedRead ? fail(404, { detail: GENERATED_READ_REASON }) : ok(GENERATED);
  }
  if (url === "/api/flows/compile") return ok({ plan: {}, structural: [], issues: [], unmapped: [] });
  return fail(404, { detail: "no" });
};

// ------------------------------------------------------------------ imports
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../../store");
const { resetRouterCacheForTests } = await import("../../../../../router");
const { FLOWS_INIT, FLOW_OPEN_OVER_UNSAVED } = await import("../../../../../../components/flows/flowsSlice");
const { FLOW_OPEN_FAILED } = await import("../../openFlow");
const { FlowNewSheet, WIZARD_SAVED_NOT_OPENED } = await import("../wizard");

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
const settle = async (): Promise<void> => {
  for (let i = 0; i < 6; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};
const tid = (t: string): any => container.querySelector(`[data-testid="${t}"]`);
const click = (el: any): void => {
  act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
};
const toastsTitled = (title: string) =>
  ((useStore.getState() as any).toasts as any[]).filter((t) => t.title === title);

/** Flow A open in the store, DIRTY when `dirty`, as a hub switch leaves it:
 *  the wizard's own `flowsOpen(rec.id)` must save it first (#450), and that
 *  save's refusal is what the "unsaved edit" failure case turns on. Clean, it
 *  saves nothing and goes straight to the read the "plain 404" case fails. */
function seed(dirty: boolean): void {
  win.location.hash = "#/session/flows/flowNew";
  resetRouterCacheForTests();
  act(() => {
    useStore.setState({
      principal: { role: "admin", email: null, caps: ["view.status", "control.capture"] } as never,
      authGate: "open",
      toasts: [],
      flows: {
        ...FLOWS_INIT,
        libraryLoaded: true, libraryError: null,
        record: A_RECORD as never,
        graph: dirty
          ? { ...A_RECORD.graph, nodes: A_RECORD.graph.nodes.map((n) => ({ ...n, x: 120 })) }
          : A_RECORD.graph,
        dirty,
      } as never,
    } as never);
  });
}

async function mount(): Promise<void> {
  asked.length = 0;
  await act(async () => { root.render(createElement("div")); });
  await act(async () => { root.render(createElement(FlowNewSheet)); });
  await settle();
}

// ------------------------------------------------------------------ the cases

await test(
  "GENERATE stays on the wizard when flow A's edit will not save",
  async () => {
    refuseSaveOfA = true;
    try {
      seed(true);
      await mount();
      const hash = win.location.hash;

      click(tid("flow-new-generate"));
      await settle();

      assert(asked.some((a) => a.url === "/api/flows/wizard" && a.method === "POST"),
        "premise: the wizard's POST was made");
      assert(asked.some((a) => a.url === `/api/flows/${A_ID}` && a.method === "PUT"),
        "premise: the save-first rule tried to save flow A before opening the new one");
      eq(win.location.hash, hash,
        "the wizard navigated onto the new flow's route although the open refused");
      eq(useStore.getState().flows.record?.id, A_ID,
        "the store must still hold flow A - the refusal left it in place, as it says it does");
      assert(tid("session-flow-new") != null, "the wizard sheet must still be on screen");

      // The store's own refusal toast carries the same title as the wizard's,
      // and the store's x2 rule for a toast still on screen coalesces the
      // wizard's own onto it - so the reason on screen is the STORE's
      // (FLOW_OPEN_OVER_UNSAVED), read once, exactly as the equivalent case in
      // nowRunOpensThePressedFlow.test.tsx documents for the same coalescing.
      const said = toastsTitled(FLOW_OPEN_FAILED);
      assert(said.some((t) => String(t.detail).includes(FLOW_OPEN_OVER_UNSAVED)),
        `nothing said the new flow did not open: ${JSON.stringify(said)}`);
    } finally {
      refuseSaveOfA = false;
    }
  },
);

await test(
  "GENERATE stays on the wizard and names where the new flow is when its plain read fails",
  async () => {
    failGeneratedRead = true;
    try {
      seed(false);
      await mount();
      const hash = win.location.hash;

      click(tid("flow-new-generate"));
      await settle();

      assert(asked.some((a) => a.url === "/api/flows/wizard" && a.method === "POST"),
        "premise: the wizard's POST was made, so the flow really was saved server-side");
      assert(!asked.some((a) => a.url === `/api/flows/${A_ID}` && a.method === "PUT"),
        "premise: flow A was clean, so no save-first PUT should have been sent");
      eq(win.location.hash, hash,
        "the wizard navigated onto the new flow's route although its read failed");
      eq(useStore.getState().flows.record?.id, A_ID,
        "the store must still hold flow A - a failed read leaves the previous record in place");
      assert(tid("session-flow-new") != null, "the wizard sheet must still be on screen");

      // Nothing else wrote to libraryError here, so this toast is the
      // wizard's own, carrying both the server's reason and where the new,
      // already-saved flow can still be reached.
      const said = toastsTitled(FLOW_OPEN_FAILED);
      assert(said.some((t) => String(t.detail).includes(GENERATED_READ_REASON)
        && String(t.detail).includes(WIZARD_SAVED_NOT_OPENED)),
        `the toast did not carry the server's reason and where to find the flow: ${JSON.stringify(said)}`);
    } finally {
      failGeneratedRead = false;
    }
  },
);

await test(
  "START BLANK stays on the wizard and names where the new flow is when its plain read fails",
  async () => {
    failGeneratedRead = true;
    try {
      seed(false);
      await mount();
      const hash = win.location.hash;

      click(tid("flow-new-blank"));
      await settle();

      assert(asked.some((a) => a.url === "/api/flows" && a.method === "POST"),
        "premise: START BLANK's POST was made, so the flow really was saved server-side");
      eq(win.location.hash, hash,
        "the wizard navigated onto the new flow's route although its read failed");
      eq(useStore.getState().flows.record?.id, A_ID,
        "the store must still hold flow A - a failed read leaves the previous record in place");
      assert(tid("session-flow-new") != null, "the wizard sheet must still be on screen");

      const said = toastsTitled(FLOW_OPEN_FAILED);
      assert(said.some((t) => String(t.detail).includes(GENERATED_READ_REASON)
        && String(t.detail).includes(WIZARD_SAVED_NOT_OPENED)),
        `the toast did not carry the server's reason and where to find the flow: ${JSON.stringify(said)}`);
    } finally {
      failGeneratedRead = false;
    }
  },
);

await test(
  "control: GENERATE opens and navigates once the read lands",
  async () => {
    refuseSaveOfA = false;
    failGeneratedRead = false;
    seed(false);
    await mount();

    click(tid("flow-new-generate"));
    await settle();

    eq(useStore.getState().flows.record?.id, GENERATED_ID, "the generated flow must actually open");
    eq(win.location.hash, `#/session/flows?open=${GENERATED_ID}`,
      "the wizard must navigate once the open has landed");
    eq(toastsTitled(FLOW_OPEN_FAILED), [], "a toast said the new flow did not open");
  },
);

await act(async () => { root.unmount(); });

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`w1WizardOpensOnlyItsOwnFlow.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) {
  (globalThis as unknown as { process?: { exitCode?: number } }).process!.exitCode = 1;
}
export default { passed, failed, total };
export { passed, failed, total };
