// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w14RotatorCardPreflight.test.tsx - TEST ROTATOR on the legacy equipment card
// (`components/equipment/RotatorCard.tsx`, which still serves `#/classic`),
// MOUNTED (WP-88; #145, #594).
//
//   Run directly:  npx tsx src/components/__tests__/w14RotatorCardPreflight.test.tsx
//   Also run by `npm test` (run-tests.mjs).
//
// The next-UI panel has its own test (`next/hubs/rig/rotator/__tests__/
// w14RotatorPreflight.test.tsx`); this card carries its own copy of the same
// copy rather than importing across the legacy/next line, so each side pins its
// own sentences and a change to one that is not made to the other fails here.
//
//   NOT MEASURED IS NOT FAILED: `sky_sign` / `trusted` are null until measured
//   and absent from an older server; FAILED is for `trusted === false` only.
//   THE BUTTON SAYS WHAT IT DOES: about 22 degrees of travel, four plate solves.
//   A PRESS THAT WOULD CHANGE NOTHING IS REFUSED: once both halves are known and
//   the follow test PASSED the server measures nothing, so the button is
//   disabled with its reason. A FAILED follow test leaves it live: the press
//   runs that test again on its own (#697).
//
//   DELIBERATE PIN CHANGE (backlog WP-114, #697, wave 15 integration). Two cases
//   below pinned the behaviour #697 replaced ("a FAILED one, which stays failed
//   until the rig reconnects", and a disabled button over a failed rotator).
//   They say so where they stand; w15RotatorCardRetest.test.tsx covers the
//   retest itself.
//
// MUTANTS RUN (each from a byte backup of RotatorCard.tsx, restored
// byte-identically, sha256 compared, the mutant text grepped out), 8 cases:
//
//   U3 "null reads as failed" (`if (rot.trusted === false) {` made
//   `if (!rot.trusted) {`). 6/8 passed; red on "a rotator nobody has tested is
//   not a failed one":
//     x ...: the line says FAILED for a rotator whose test has not run (got
//     "FAILED: the camera did not follow a 20 degree step, so rotation is off.
//     Press Test rotator to measure it again")
//   and on "a measured sign, a passed test and a failed test are each said".
//
//   U4 "route typo" (`/api/rotator/preflight` posted as `/api/rotator/preflite`).
//   7/8 passed; red on "pressing it posts the preflight route and nothing else":
//     x ...: posted POST /api/rotator/preflite instead of POST /api/rotator/preflight

/* eslint-disable @typescript-eslint/no-explicit-any */

const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/#/classic/equipment", pretendToBeVisual: true },
);
const win = dom.window as any;

win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} addEventListener() {} send() {} };
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "FocusEvent", "PointerEvent",
  "Image", "localStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "requestAnimationFrame", "cancelAnimationFrame", "ResizeObserver",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

interface Asked { method: string; url: string; body: any }
const asked: Asked[] = [];
const ok = (json: any) => ({ ok: true, status: 200, statusText: "OK", json: async () => json });
g.fetch = async (url: string, init?: { method?: string; body?: string }) => {
  const method = init?.method ?? "GET";
  let body: any = null;
  try { body = init?.body ? JSON.parse(init.body) : null; } catch { body = init?.body; }
  asked.push({ method, url: String(url), body });
  return ok({});
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../store");
const RotatorCard = (await import("../equipment/RotatorCard")).default;

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
  if (got !== want) throw new Error(`${msg} (expected ${String(want)}, got ${String(got)})`);
}
const settle = async () => {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
};

const OPERATOR = {
  role: "operator", email: "op@rig",
  caps: ["view.status", "view.preview", "control.capture", "control.guide", "control.mount"],
};
const VIEWER = { role: "viewer", email: null, caps: ["view.status", "view.preview"] };

function rotator(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    name: "ZWO CAA", sky_deg: 42.5, mech_deg: 100.5, moving: false,
    synced: true, can_reverse: false, reverse: false,
    ...over,
  };
}

function seed(over: Record<string, unknown> = {}, rot: Record<string, unknown> = rotator()): void {
  act(() => {
    useStore.setState({
      principal: OPERATOR,
      wsPhase: "up",
      equipConnected: true,
      toasts: [],
      config: {
        active_profile_id: null,
        rotator: { range_type: "full", range_start_deg: 0, tolerance_deg: 1 },
      },
      status: { connected: {}, backend_links: [], busy_lanes: [], rotator: rot },
      ...over,
    } as never);
  });
}

const container = win.document.getElementById("root") as any;
let rootRef: ReturnType<typeof createRoot> | null = null;
function mount(): void {
  if (rootRef) act(() => { rootRef!.unmount(); });
  rootRef = createRoot(container);
  act(() => { rootRef!.render(createElement(RotatorCard as any)); });
}
function click(node: any): void {
  act(() => { node.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
}
const q = (sel: string) => container.querySelector(sel) as any;
const button = () => q("[data-rotator-preflight]");
const line = (): string => (q("[data-rotator-preflight-line]")?.textContent ?? "") as string;
const commands = () => asked.filter((a) => a.method !== "GET");

seed();
mount();

test("the card has the button, named Test rotator, and its line", () => {
  assert(button() != null, "no Test rotator button on the legacy card");
  eq((button().textContent || "").trim(), "Test rotator", "wrong button name");
  assert(q("[data-rotator-preflight-line]") != null, "no state line");
});

test("the card says what the button does: 22 degrees of travel and four solves", () => {
  const text = (container.textContent || "") as string;
  assert(/about 22 degrees/.test(text), "the card does not say how far it turns");
  assert(/four plate solves/.test(text), "the card does not say what it costs");
});

test("a rotator nobody has tested is not a failed one", () => {
  for (const over of [{}, { sky_sign: null, trusted: null }]) {
    seed({}, rotator(over));
    mount();
    assert(!/FAILED/.test(line()),
      `the line says FAILED for a rotator whose test has not run (got "${line()}")`);
    eq(line(), "sign not measured, follow test not run", "wrong line for an unmeasured rotator");
  }
});

test("a measured sign, a passed test and a failed test are each said", () => {
  seed({}, rotator({ sky_sign: -1, trusted: null }));
  mount();
  eq(line(), "sign -1 measured, follow test not run", "the measured sign is not stated");
  seed({}, rotator({ sky_sign: 1, trusted: true }));
  mount();
  eq(line(), "sign +1 measured, follow test passed: the camera turned with a 20 degree step",
    "a passed test is not stated");
  seed({}, rotator({ sky_sign: -1, trusted: false }));
  mount();
  // DELIBERATE PIN CHANGE (#697): this asserted /rotation is off until the rig
  // reconnects/. A failure is cleared by Test rotator now, not by a reconnect.
  assert(/^FAILED:/.test(line()) && /rotation is off/.test(line()),
    `a failed test is not stated (got "${line()}")`);
  assert(/Press Test rotator to measure it again/.test(line()),
    `a failed test does not say what clears it (got "${line()}")`);
  assert(!/reconnects/.test(line()),
    `a failed test still says a reconnect clears it (got "${line()}")`);
});

await testAsync("pressing it posts the preflight route and nothing else", async () => {
  seed();
  mount();
  asked.length = 0;
  click(button());
  await settle();
  const posts = commands();
  eq(posts.length, 1, "the press sent more or fewer than one command");
  assert(posts[0].method === "POST" && posts[0].url.endsWith("/api/rotator/preflight"),
    `posted ${posts[0].method} ${posts[0].url} instead of POST /api/rotator/preflight`);
});

await testAsync("once both halves are known the button is disabled and posts nothing", async () => {
  // DELIBERATE PIN CHANGE (#697): this seeded `trusted: false` and expected the
  // button disabled. A FAILED rotator leaves it live now (a press re-tests it),
  // so the disabled state is a rotator that PASSED.
  seed({}, rotator({ sky_sign: -1, trusted: true }));
  mount();
  assert(button().disabled === true, "Test rotator is live when there is nothing left to measure");
  assert(/already measured the rotator/.test(button().getAttribute("title") || ""),
    "no reason on the disabled button");
  asked.length = 0;
  click(button());
  await settle();
  eq(commands().length, 0, "a disabled press reached the rig");
});

test("half-known is still live: the sign is measured, the follow test is not", () => {
  seed({}, rotator({ sky_sign: -1, trusted: null }));
  mount();
  assert(button().disabled === false, "the button is disabled while the follow test has not run");
});

test("a viewer cannot press it", () => {
  seed({ principal: VIEWER });
  mount();
  assert(button().disabled === true, "a viewer can press Test rotator");
});

act(() => { rootRef!.unmount(); });

const total = passed + failed;
console.log(`w14RotatorCardPreflight.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
