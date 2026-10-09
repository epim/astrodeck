// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// p6TonightReaskNeverWrites.test.tsx -- the Tonight sheet's re-ask (websocket
// up, tab visible) is a pure read, runs only after a transient failure, and
// never runs over an unsaved edit (#859).
//
//   Run directly:  npx tsx src/next/hubs/session/flows/__tests__/p6TonightReaskNeverWrites.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// RETRY flushes: a PUT of an edited flow, then a compile POST when the compile
// in hand is not for the graph on screen (#688). Nothing may post by itself,
// and a pure read over an unsaved edit would answer for the flow as last saved.
// Harness from tonightDom.test.tsx; every request is counted by method and
// path. The mount's own read runs first and is not counted: each case resets
// the count, writes the failure state, then fires one visibilitychange.
//
// Named mutants (each turns the case beside it red):
//   TS1  the re-ask calls fetchTonight() (the flush default: the compile POST fires).
//   TS2  drop the `!(f.dirty && !f.record?.readonly)` clause (a GET fires).
//   TS3  drop the `f.tonightErrorTransient` clause (a GET fires).
//   UX3  the `tonight-retrying` line is not rendered (`loading && tonightRetry`
//        -> `false && tonightRetry`): while the read waits to ask again the
//        sheet shows the plain loading line.

/* eslint-disable @typescript-eslint/no-explicit-any */
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

const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/", pretendToBeVisual: true },
);
const win = dom.window as any;
win.matchMedia = () => ({
  matches: false,
  addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} addEventListener() {} send() {} };
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

const TONIGHT = { ok: true, reason: "", night: null, targets: [], story: [], brief: "" };
const FLOW_RECORD = {
  id: "quick-m31", name: "QUICK M31", folder: "My flows",
  tagline: "", readonly: false, graph: { nodes: [], edges: [] },
  last_run: null, last_result: "", updated_ts: 1,
};

let asked: { url: string; method: string }[] = [];
/** True: every tonight GET times out the way a browser's AbortSignal does. */
let tonightDown = false;
g.fetch = async (url: string, init?: { method?: string }) => {
  const method = init?.method ?? "GET";
  asked.push({ url, method });
  if (tonightDown && /\/tonight$/.test(url)) {
    throw new DOMException("The operation timed out.", "TimeoutError");
  }
  const ok = (data: unknown) => ({
    ok: true, status: 200, statusText: "OK",
    headers: { get: () => "application/json" },
    json: async () => data,
  });
  if (/\/tonight$/.test(url)) return ok(TONIGHT);
  return {
    ok: false, status: 404, statusText: "Not Found",
    headers: { get: () => "application/json" },
    json: async () => ({ detail: "no" }),
  };
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { compiledIsCurrent } = await import("../../../../../components/flows/flowsSlice");
const { setRetrySleepForTests } = await import("../../../../../lib/retryLoad");
const { FlowTonightSheet } = await import("../tonight/TonightSheet");

let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function eq(got: unknown, want: unknown, msg: string): void {
  if (got !== want) throw new Error(`${msg}\n  expected ${String(want)}\n  got      ${String(got)}`);
}
const count = (method: string, re?: RegExp) =>
  asked.filter((a) => a.method === method && (!re || re.test(a.url))).length;

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
const settle = async () => {
  for (let i = 0; i < 6; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};

const ADMIN = ["view.status", "view.preview", "view.site_derived", "control.capture", "control.mount"];

async function mountClean(): Promise<void> {
  act(() => {
    const s = useStore.getState();
    useStore.setState({
      principal: { role: "operator", email: null, caps: ADMIN } as never,
      authGate: "open",
      sequence: { state: "idle" } as never,
      status: {} as never,
      equipConnected: true,
      wsPhase: "up",
      resumeArm: null as never,
      flows: {
        ...s.flows,
        record: FLOW_RECORD as never, graph: FLOW_RECORD.graph as never, dirty: false,
        tonight: null, tonightLoading: false, tonightError: null,
        tonightRetry: null, tonightErrorTransient: false,
        calHealth: null, compiled: null, compiling: false,
        ui: { ...s.flows.ui, tonightTab: "timeline" },
      } as never,
    } as never);
  });
  await act(async () => { root.render(createElement("div")); });
  await act(async () => { root.render(createElement(FlowTonightSheet as any, { params: {}, depth: 0 })); });
  await settle();
}

/** The state a failed read leaves, then one tab-visible event, counting only
 *  what the event asked. */
async function failThenReturn(fields: Record<string, unknown>): Promise<void> {
  act(() => {
    const s = useStore.getState();
    useStore.setState({
      flows: {
        ...s.flows, tonight: null, tonightLoading: false, tonightRetry: null,
        tonightError: "request timed out — server not responding", ...fields,
      } as never,
    } as never);
  });
  asked = [];
  await act(async () => { document.dispatchEvent(new win.Event("visibilitychange")); });
  await settle();
}

await test("TS1 after a transient failure the re-ask is ONE pure GET: no compile POST, no PUT", async () => {
  await mountClean();
  await failThenReturn({ tonightErrorTransient: true });
  eq(compiledIsCurrent(useStore.getState().flows), false,
    "precondition: the compile in hand is not for this graph, so a flush would POST one");
  eq(count("GET", /\/tonight$/), 1, "the re-ask reads tonight once");
  eq(count("POST"), 0, "the re-ask must never post a compile");
  eq(count("PUT"), 0, "the re-ask must never save");
});

await test("TS3 a refusal is the home's answer and is not re-asked", async () => {
  await mountClean();
  await failThenReturn({ tonightErrorTransient: false, tonightError: "no site is set" });
  eq(asked.length, 0, `a refusal was re-asked: ${JSON.stringify(asked)}`);
});

await test("TS2 with an unsaved edit nothing is asked at all", async () => {
  await mountClean();
  await failThenReturn({ tonightErrorTransient: true, dirty: true });
  eq(asked.length, 0, `an unsaved edit was read over: ${JSON.stringify(asked)}`);
  act(() => {
    const s = useStore.getState();
    useStore.setState({ flows: { ...s.flows, dirty: false, record: null } as never } as never);
  });
});

await test("UX3 while the read waits to ask again the sheet says so", async () => {
  let release!: () => void;
  const gate = new Promise<void>((r) => { release = r; });
  setRetrySleepForTests(() => gate);
  tonightDown = true;
  try {
    await mountClean();
    const line = container.querySelector('[data-testid="tonight-retrying"]');
    if (line == null) throw new Error("no retrying line while the read waits to ask again");
    if (!/No answer from the rig yet\. Asking again by itself \(try 2 of 4\)\./.test(line.textContent)) {
      throw new Error(`the retrying line reads "${line.textContent}"`);
    }
    tonightDown = false;
    release();
    await settle();
    eq(container.querySelector('[data-testid="tonight-retrying"]'), null, "the retrying line outlived the answer");
  } finally {
    tonightDown = false;
    setRetrySleepForTests(null);
  }
});

await act(async () => { root.unmount(); });

console.log(`p6TonightReaskNeverWrites.test: ${passed} passed, ${failed} failed`);
if (failed) {
  failures.forEach((f) => console.error(f));
  process.exitCode = 1;
}
