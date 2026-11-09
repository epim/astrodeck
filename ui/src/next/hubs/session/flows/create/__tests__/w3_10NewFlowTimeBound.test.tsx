// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w3_10NewFlowTimeBound.test.tsx - NEW FLOW asks how many minutes to stay on
// the target (#1094, WP 3.10, owner ruling D-D), #/next sheet, MOUNTED.
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx <this file>
//
// The test reads the request body the sheet posts: a field that drew and
// posted nothing would get a valid 200 describing a flow with no bound. An
// untouched sheet posts the three keys it always did, and Best of several,
// which picks its member at dusk, is not asked.

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
  "PointerEvent", "FocusEvent",
  "localStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "requestAnimationFrame", "cancelAnimationFrame", "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const GENERATED = {
  id: "gen-1", name: "Deep-sky: M16", folder: "My flows", tagline: "generated",
  readonly: false, graph: { nodes: [], edges: [] },
  last_run: null, last_result: "", updated_ts: 1_757_000_000,
};

const asked: { url: string; method: string; body: any }[] = [];
g.fetch = async (url: string, init?: { method?: string; body?: string }) => {
  const method = init?.method ?? "GET";
  const body = init?.body ? JSON.parse(init.body) : undefined;
  asked.push({ url, method, body });
  const ok = (data: unknown) => ({
    ok: true, status: 200, statusText: "OK",
    headers: { get: () => "application/json" },
    json: async () => data,
  });
  if (url === "/api/flows/wizard" && method === "POST") return ok(GENERATED);
  if (url === "/api/flows" && method === "GET") return ok([]);
  if (url === "/api/flows/folders") return ok([{ name: "My flows", count: 1, readonly: false }]);
  if (url === "/api/flows/compile" && method === "POST") {
    return ok({ plan: {}, structural: [], issues: [], unmapped: [] });
  }
  if (/^\/api\/flows\/[^/]+$/.test(url) && method === "GET") return ok(GENERATED);
  return {
    ok: false, status: 404, statusText: "Not Found",
    headers: { get: () => "application/json" },
    json: async () => ({ detail: "no" }),
  };
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../../store");
const { resetRouterCacheForTests } = await import("../../../../../router");
const { FlowNewSheet } = await import("../wizard");

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
async function testAsync(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg}\n  expected ${String(want)}\n  got      ${String(got)}`);
}

const container = win.document.getElementById("root") as any;
let root = createRoot(container);
const settle = async (): Promise<void> => {
  for (let i = 0; i < 6; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};
const tid = (t: string): any => container.querySelector(`[data-testid="${t}"]`);
const click = (el: any): void => {
  act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
};
const typeInto = (el: any, value: string): void => {
  act(() => {
    const setter = Object.getOwnPropertyDescriptor(win.HTMLInputElement.prototype, "value")!.set!;
    setter.call(el, value);
    el.dispatchEvent(new win.Event("input", { bubbles: true }));
  });
};
/** One option of a Segmented group, by the value it carries. */
const option = (group: string, value: string): any =>
  tid(group)?.querySelector(`[data-value="${value}"]`);
const wizardPosts = (): { body: any }[] =>
  asked.filter((a) => a.method === "POST" && a.url === "/api/flows/wizard");

function route(): void {
  win.history.replaceState(null, "", "http://local/#/session/flows/flowNew");
  resetRouterCacheForTests();
}

async function freshSheet(): Promise<void> {
  asked.length = 0;
  route();
  act(() => {
    useStore.setState({
      principal: { role: "admin", email: null, caps: ["control.capture"] } as never,
      authGate: "open",
      toasts: [] as never,
      flows: { ...useStore.getState().flows, record: null } as never,
    } as never);
  });
  await act(async () => { root.unmount(); });
  root = createRoot(container);
  await act(async () => { root.render(createElement(FlowNewSheet)); });
  await settle();
}

// ------------------------------------------------------------- the sheet

await freshSheet();

test("the bound is asked of a deep-sky target, blank", () => {
  const field = tid("flow-wizard-max-time");
  assert(field != null, "no time-bound field on the sheet");
  eq(field.value, "", "the bound opens blank: no limit");
});

await testAsync("the typed minutes travel in the request body", async () => {
  typeInto(tid("flow-wizard-max-time"), "60");
  click(tid("flow-new-generate"));
  await settle();
  eq(wizardPosts().length, 1, "one wizard request");
  eq(wizardPosts()[0].body.max_time_min, 60,
    `the body carried ${JSON.stringify(wizardPosts()[0].body)}: the bound was dropped`);
});

await testAsync("an untouched sheet posts exactly the three keys it always did", async () => {
  await freshSheet();
  click(tid("flow-new-generate"));
  await settle();
  eq(Object.keys(wizardPosts()[0].body).sort().join(","), "kind,options,target",
    "an unanswered bound adds nothing to the body");
});

await testAsync("an unreadable bound is not posted", async () => {
  await freshSheet();
  typeInto(tid("flow-wizard-max-time"), "an hour");
  click(tid("flow-new-generate"));
  await settle();
  eq(wizardPosts().length, 0, "an unreadable bound was posted");
});

await testAsync("the bound says what happens when the target's time is up (#1207)", async () => {
  // Each clause is the engine's (`_enforce_time_bound`): counted from the
  // target's first attempt, setup included; the target is done for tonight
  // with its frames owed to a later night; the run moves on.
  await freshSheet();
  const said: string = tid("flow-wizard-max-time-field")?.textContent ?? "";
  assert(said !== "", "the bound says nothing about what the limit does");
  assert(said.includes("first attempt, so setup counts"), `no start of the count: ${said}`);
  assert(said.includes("done for tonight"), `not said to be done for tonight: ${said}`);
  assert(said.includes("wait for a later night"), `owed frames not said to wait: ${said}`);
  assert(said.includes("the run moves on to the next target"), `not said that the run moves on: ${said}`);
});

await testAsync("Best of several is not asked a bound", async () => {
  await freshSheet();
  click(option("flow-wizard-kind", "Best of several"));
  await settle();
  assert(tid("flow-wizard-max-time") == null, "a pool has no single target to bound");
});

const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\nw3_10NewFlowTimeBound.test (next): ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
  process.exitCode = 1;
}
export default { passed, failed, total };
export { passed, failed, total };
