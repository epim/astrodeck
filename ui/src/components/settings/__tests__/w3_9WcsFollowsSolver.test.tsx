// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w3_9WcsFollowsSolver.test.tsx - the CLASSIC "Record where each photo points"
// panel says the option follows the plate solver until it is changed by hand,
// and offers the way back (#826, owner ruling D-I; consolidation WP 3.9).
// Mounted: the real WcsStampPanel.
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/settings/__tests__/w3_9WcsFollowsSolver.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT IS GUARDED
//   1. The panel says, in the shared sentence, that stamping follows the plate
//      solver until it is changed by hand, and no longer says it is off by
//      default (it is on whenever a solver is installed).
//   2. Before anything was changed by hand there is no way back to offer.
//   3. Once changed by hand, FOLLOW THE PLATE SOLVER AGAIN posts
//      `follow_solver: true` with the advanced block, once.
//   4. A viewer sees the control locked and its press writes nothing.
//
// NAMED MUTANTS (from a byte backup of WcsStampPanel.tsx, restored and
// sha256-compared):
//   "no sentence" (the paragraph removed): x the panel says it follows the
//     plate solver: no sentence
//   "control always shown" (`byHand &&` dropped): x before anything was changed
//     by hand there is no way back to offer: the panel offers it
//   "no follow flag" (the press posts without `follow_solver`): x FOLLOW THE
//     PLATE SOLVER AGAIN posts follow_solver: the press posted no follow_solver

/* eslint-disable @typescript-eslint/no-explicit-any */

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
const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement", "HTMLButtonElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "localStorage", "sessionStorage", "requestAnimationFrame", "cancelAnimationFrame",
  "getComputedStyle", "matchMedia", "WebSocket",
]) {
  const v = win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const posts: { url: string; body: any }[] = [];
g.fetch = async (url: any, init: any) => {
  const u = String(url);
  const method = (init?.method ?? "GET").toUpperCase();
  if (method === "POST") posts.push({ url: u, body: init?.body ? JSON.parse(init.body) : null });
  return {
    ok: true, status: 200, statusText: "OK",
    headers: { get: () => "application/json" },
    json: async () => ({}),
    text: async () => "{}",
  };
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../store");
const { default: WcsStampPanel } = await import("../WcsStampPanel");
const { WCS_FOLLOWS_SOLVER, WCS_FOLLOW_AGAIN } = await import("../../../lib/wcsStamp");

let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg} (expected ${String(want)}, got ${String(got)})`);
}
function assert(cond: unknown, msg: string): void { if (!cond) throw new Error(msg); }

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
const tick = () => new Promise((r) => setTimeout(r, 0));
const settle = async () => { for (let i = 0; i < 6; i++) await act(async () => { await tick(); }); };
const q = (id: string): any => container.querySelector(`[data-testid="${id}"]`);

const WCS_STAMP = { solver: "auto", downsample: 2, min_stars: 30, queue_max: 4 };
async function seed(role: "admin" | "viewer", config: Record<string, unknown>): Promise<void> {
  posts.length = 0;
  await act(async () => {
    useStore.setState({
      principal: role === "admin"
        ? { role: "admin", email: "admin@example.test", caps: ["config.site_optics"] }
        : { role: "viewer", email: "viewer@example.test", caps: ["view.status"] },
      authGate: "open",
      config: { wcs_stamp: WCS_STAMP, ...config } as never,
    } as never);
  });
  await act(async () => { root.render(null); });
  await act(async () => { root.render(createElement(WcsStampPanel)); });
  await settle();
}
const press = async (el: any): Promise<void> => {
  await act(async () => {
    el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
    for (let i = 0; i < 6; i++) await tick();
  });
};
const wcsPosts = () => posts.filter((p) => p.url.includes("/api/config/wcs"));

await test("the panel says it follows the plate solver, and no longer that it is off by default", async () => {
  await seed("admin", { solve_saved_lights: true });
  const note = q("wcs-follows-solver");
  assert(note, "the panel says nothing about following the plate solver: no sentence");
  eq(note.textContent.trim(), WCS_FOLLOWS_SOLVER, "the sentence is not the shared one");
  assert(!String(container.textContent).includes("off by default"),
    "the panel still says the option is off by default");
});

await test("before anything was changed by hand there is no way back to offer", async () => {
  await seed("admin", { solve_saved_lights: true, solve_saved_lights_by_hand: false });
  assert(!q("wcs-follow-again"), "the panel offers it");
  await seed("admin", { solve_saved_lights: true });
  assert(!q("wcs-follow-again"), "the panel offers it to a config that predates the flag");
});

await test("once changed by hand, FOLLOW THE PLATE SOLVER AGAIN posts follow_solver with the advanced block, once", async () => {
  await seed("admin", { solve_saved_lights: false, solve_saved_lights_by_hand: true });
  const again = q("wcs-follow-again");
  assert(again, "a by-hand option has no way back to following");
  eq(again.textContent.trim(), WCS_FOLLOW_AGAIN, "the control's label");
  await press(again);
  const sent = wcsPosts();
  eq(sent.length, 1, `the press did not post exactly once: ${JSON.stringify(sent)}`);
  assert(sent[0].body.follow_solver === true, "the press posted no follow_solver");
  eq(sent[0].body.wcs_stamp.min_stars, 30, "the advanced block was not carried with it");
});

await test("a viewer sees the control locked and its press writes nothing", async () => {
  await seed("viewer", { solve_saved_lights: false, solve_saved_lights_by_hand: true });
  const again = q("wcs-follow-again");
  assert(again, "a viewer is not shown the control, locked");
  eq(again.getAttribute("aria-disabled"), "true", "the control is live for a viewer");
  await press(again);
  eq(wcsPosts().length, 0, "a viewer's press wrote");
});

await act(async () => { root.unmount(); });
const total = passed + failed;
console.log(`w3_9WcsFollowsSolver.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) (globalThis as any).process.exitCode = 1;
export const result = { passed, failed, total };
export default result;
