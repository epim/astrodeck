// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// p6PlanLibraryRetryATimeout.test.tsx -- the #/next plan library asks a
// timed-out list read again by itself, and the refresh after a save does NOT
// (#859).
//
//   Run directly:  npx tsx src/next/hubs/session/plan/__tests__/p6PlanLibraryRetryATimeout.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// Harness cut down from planEditorDom.test.tsx, mounting PlanLibrary itself.
// The retry wait runs at once.
//
// Named mutants (each turns the case beside it red):
//   (a) refresh calls api.get directly (no retryTransient) -> the saved list
//       shows "Could not load saved plans" and RETRY.
//   (b) the post-write refresh retries (`opts?.retry === true` ->
//       `opts?.retry !== false`) -> four GETs after the save, and SAVING held
//       through them.
//   UX13 the `plan-saved-retrying` line is not rendered while the wait is held
//        (`listRetry && rows.length === 0` -> `false && ...` in the body).
//   PLS  the SAVED PLANS sub does not say "asking again" while it waits.
//   PLR  delete PlanLibrary's useRetryOnReturn -> a list read that settled as
//        failed is not asked again when the tab comes back.

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
  { url: "http://local/#/session/flows/planEditor", pretendToBeVisual: true },
);
const win = dom.window as any;
win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} addEventListener() {} send() {} };
const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "HTMLSelectElement", "Element", "Node", "Event", "CustomEvent", "MouseEvent",
  "KeyboardEvent", "localStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "requestAnimationFrame", "cancelAnimationFrame", "URL", "URLSearchParams",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const ROW = { id: "p1", name: "NGC 7331 Ha", targets: 1, frames: 60, seconds: 18000, updated_ts: 1 };
const json = (data: unknown) => ({ ok: true, status: 200, statusText: "OK", json: async () => data });

/** How the n-th GET /api/plans (0-based) answers; set per case. */
let listStep: (n: number) => Promise<unknown> = async () => json([]);
let asked: { method: string; url: string }[] = [];
g.fetch = async (url: string, init?: { method?: string }) => {
  const method = init?.method ?? "GET";
  asked.push({ method, url });
  if (url === "/api/plans" && method === "GET") {
    const n = asked.filter((a) => a.method === "GET" && a.url === "/api/plans").length - 1;
    return listStep(n);
  }
  if (url === "/api/plans" && method === "POST") return json({ ...ROW, id: "p2", name: "Test plan" });
  return { ok: false, status: 404, statusText: "Not Found", json: async () => ({ detail: "no" }) };
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { setRetrySleepForTests } = await import("../../../../../lib/retryLoad");
const { PlanLibrary } = await import("../PlanLibrary");

let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

const container = win.document.getElementById("root") as any;
let root = createRoot(container);
const settle = async () => {
  for (let i = 0; i < 8; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};
const tid = (t: string): any => container.querySelector(`[data-testid="${t}"]`);
const click = (el: any) => {
  act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
};
const PLAN = { name: "Test plan", targets: [] } as any;

setRetrySleepForTests(async () => {});
act(() => {
  useStore.setState({
    principal: { role: "admin", email: null, caps: ["view.status", "control.capture"] } as never,
    authGate: "open",
    loadedPlanId: null,
    editorDirty: false,
    toasts: [],
  } as never);
});

async function mount(): Promise<void> {
  await act(async () => { root.unmount(); });
  root = createRoot(container);
  await act(async () => {
    root.render(createElement(PlanLibrary as any, { plan: PLAN, setPlan: () => {}, lockedReason: null }));
  });
  await settle();
}
const timedOut = () => Promise.reject(new DOMException("The operation timed out.", "TimeoutError"));
const plansGets = () => asked.filter((a) => a.method === "GET" && a.url === "/api/plans").length;

await test("(a) a list read that times out once and then answers lists the rows", async () => {
  asked = [];
  listStep = (n) => (n === 0 ? timedOut() : Promise.resolve(json([ROW])));
  await mount();
  assert(plansGets() === 2, `precondition: the list was asked twice (asked ${plansGets()})`);
  const head = tid("plan-saved-list").querySelector("button[aria-expanded]");
  assert(head != null, "precondition: the SAVED PLANS disclosure has a head");
  click(head);
  await settle();
  assert(tid("plan-saved-retry") == null, "one timed-out read left RETRY on the saved list");
  assert(tid("plan-load-p1") != null, "the row of the answer is not listed");
});

await test("(b) the refresh after a save asks once and does not hold SAVING", async () => {
  asked = [];
  listStep = async () => json([]);
  await mount();
  listStep = () => Promise.reject(new TypeError("Failed to fetch"));
  const before = plansGets();
  click(tid("plan-save"));
  await settle();
  const posts = asked.filter((a) => a.method === "POST" && a.url === "/api/plans").length;
  assert(posts === 1, `precondition: the save posted once (posted ${posts})`);
  assert(plansGets() - before === 1,
    `the refresh after the save asked ${plansGets() - before} times; a save that landed must not wait on retries`);
  assert(tid("plan-save").getAttribute("aria-busy") == null, "SAVE is still busy after its refresh settled");
});

await test("UX13 + PLS while the list read waits to ask again the saved list says so", async () => {
  asked = [];
  let down = true;
  listStep = () => (down ? timedOut() : Promise.resolve(json([ROW])));
  let release!: () => void;
  const gate = new Promise<void>((r) => { release = r; });
  setRetrySleepForTests(() => gate);
  try {
    await mount();
    const list = tid("plan-saved-list");
    const head = list.querySelector("button[aria-expanded]");
    assert(/asking again/.test(head.textContent),
      `the SAVED PLANS head reads "${head.textContent}" while it waits`);
    click(head);
    await settle();
    const line = tid("plan-saved-retrying");
    assert(line != null, "no retrying line while the list read waits to ask again");
    assert(/No answer from the rig yet\. Asking again by itself \(try 2 of 4\)\./.test(line.textContent),
      `the retrying line reads "${line.textContent}"`);
    assert(tid("plan-saved-retry") == null, "RETRY shows while the app is already asking again");
    down = false;
    release();
    await settle();
    assert(tid("plan-saved-retrying") == null, "the retrying line outlived the answer");
    assert(tid("plan-load-p1") != null, "the row of the answer is not listed");
  } finally {
    setRetrySleepForTests(async () => {});
  }
});

await test("PLR a list read that settled as failed is asked once more when the tab comes back", async () => {
  asked = [];
  listStep = () => timedOut();
  await mount();
  assert(plansGets() === 4, `precondition: four tries (asked ${plansGets()})`);
  listStep = () => Promise.resolve(json([ROW]));
  asked = [];
  await act(async () => { document.dispatchEvent(new win.Event("visibilitychange")); });
  await settle();
  assert(plansGets() === 1, `the tab coming back asked the list ${plansGets()} times, not once`);
  const head = tid("plan-saved-list").querySelector("button[aria-expanded]");
  if (head.getAttribute("aria-expanded") !== "true") click(head);
  await settle();
  assert(tid("plan-load-p1") != null, "the row of the re-asked answer is not listed");
});

await act(async () => { root.unmount(); });
setRetrySleepForTests(null);

console.log(`p6PlanLibraryRetryATimeout.test: ${passed} passed, ${failed} failed`);
if (failed) {
  failures.forEach((f) => console.error(f));
  process.exitCode = 1;
}
