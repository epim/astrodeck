// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w14RotatorPreflight.test.tsx - TEST ROTATOR on the ROTATOR sheet, MOUNTED
// (WP-88; #145, #594).
//
//   Run directly:  npx tsx src/next/hubs/rig/rotator/__tests__/w14RotatorPreflight.test.tsx
//   Also run by `npm test` (run-tests.mjs).
//
// The sign calibration and the nightly follow test were built and never called
// by anything, so on a real rig every automated rotation was refused as "sign
// not learned" until somebody ran them by hand. The server now runs them from
// the first rotating hop and from POST /api/rotator/preflight; this is the
// operator's button for it and the one line that says what the rig knows.
//
// WHAT IS WORTH ASSERTING:
//
//   NOT MEASURED IS NOT FAILED. `sky_sign` / `trusted` are null until measured
//   (and absent from an older server). A rotator nobody has tested is not one
//   that failed, and a line that said so would tell an operator to chase a
//   fault that does not exist. FAILED appears for `trusted === false` only.
//
//   THE BUTTON SAYS WHAT IT DOES. It turns the rotator about 22 degrees and
//   takes four plate solves, and the sheet says so beside it.
//
//   A PRESS THAT WOULD CHANGE NOTHING IS LOCKED WITH ITS REASON. The server
//   measures only what this connection has not measured, so once both halves
//   are known (including a FAILED one, which stays failed until the rig
//   reconnects) the button says why it has nothing to do instead of posting.
//
//   THE SAME GATES AS THE OTHER TWO SOLVING BUTTONS: the capability, a camera,
//   and the `rotate_to_pa` lane (the preflight spawns on it).
//
// MUTANTS RUN (each from a byte backup of RotatorPanel.tsx, restored
// byte-identically, sha256 compared, the mutant text grepped out), 14 cases:
//
//   U1 "null reads as failed" (`if (rot.trusted === false) {` made
//   `if (!rot.trusted) {`). 12/14 passed; red on "a rotator nobody has tested
//   is not a failed one":
//     x a rotator nobody has tested is not a failed one: the line says FAILED
//     for a rotator whose test has not run (got "FAILED: the camera did not
//     follow a 20 degree step, so rotation is off until the rig reconnects")
//   and on "a measured sign is named, with its sign".
//
//   U2 "locked once known removed" (`preflightReason` made `solveReason`).
//   12/14 passed; red on "once both halves are known the button is locked with
//   its reason":
//     x once both halves are known the button is locked with its reason: TEST
//     ROTATOR is live when there is nothing left to measure
//   and on "a locked TEST ROTATOR posts nothing".
//
//   U5 "route typo" (`/api/rotator/preflight` posted as `/api/rotator/preflite`).
//   13/14 passed; red on "pressing TEST ROTATOR posts the preflight route and
//   nothing else":
//     x ...: posted /api/rotator/preflite instead of /api/rotator/preflight

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

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/#/rig/devices/rotator", pretendToBeVisual: true },
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

// ------------------------------------------------------------- fetch recorder
interface Asked { method: string; url: string; body: any }
const asked: Asked[] = [];
const ok = (json: any) => ({ ok: true, status: 200, statusText: "OK", json: async () => json });

g.fetch = async (url: string, init?: { method?: string; body?: string }) => {
  const method = init?.method ?? "GET";
  let body: any = null;
  try { body = init?.body ? JSON.parse(init.body) : null; } catch { body = init?.body; }
  asked.push({ method, url: String(url), body });
  if (method === "POST" && String(url).includes("/api/rotator/preflight")) {
    return ok({ started: "rotate_to_pa" });
  }
  return ok({});
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { RotatorSheet } = await import("../../sheets/rotator");

// ------------------------------------------------------------------ harness
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
  caps: ["view.status", "view.preview", "view.weather", "view.site_derived",
    "control.capture", "control.guide", "control.mount"],
};
const VIEWER = { role: "viewer", email: null, caps: ["view.status", "view.preview"] };

function rotator(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    name: "ZWO CAA", sky_deg: 42.5, mech_deg: 100.5, moving: false,
    synced: true, can_reverse: true, reverse: false,
    ...over,
  };
}

function seed(over: Record<string, unknown> = {}, statusOver: Record<string, unknown> = {}): void {
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
      status: {
        connected: {
          rotator: { name: "ZWO CAA", connected: true },
          camera: { name: "ZWO ASI", connected: true },
        },
        backend_links: [
          { role: "rotator", connected: true, error: null },
          { role: "camera", connected: true, error: null },
        ],
        busy_lanes: [],
        rotator: rotator(),
        ...statusOver,
      },
      ...over,
    } as never);
  });
}

const container = win.document.getElementById("root") as any;
let rootRef: ReturnType<typeof createRoot> | null = null;
function mount(): void {
  if (rootRef) act(() => { rootRef!.unmount(); });
  rootRef = createRoot(container);
  act(() => { rootRef!.render(createElement(RotatorSheet as any, { params: {}, depth: 0 })); });
}
function click(node: any): void {
  act(() => { node.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
}
const q = (sel: string) => container.querySelector(sel) as any;
const id = (marker: string) => q(`[data-testid="${marker}"]`);
const locked = (node: any): boolean => node?.getAttribute("aria-disabled") === "true";
const commands = () => asked.filter((a) => a.method !== "GET");
const line = (): string => (id("rotator-preflight-line")?.textContent ?? "") as string;

// ================================================================== the line
seed();
mount();

test("the button and its line are on the sheet", () => {
  assert(id("rotator-preflight") != null, "no TEST ROTATOR button");
  assert(id("rotator-preflight-line") != null, "no state line");
  eq((id("rotator-preflight").textContent || "").trim(), "TEST ROTATOR",
    "the button is not named TEST ROTATOR");
});

test("the sheet says what the button does: 22 degrees of travel and four solves", () => {
  const note = (id("rotator-preflight-note")?.textContent ?? "") as string;
  assert(/about 22 degrees/.test(note), `the note does not say how far it turns: ${note}`);
  assert(/four plate solves/.test(note), `the note does not say what it costs: ${note}`);
});

test("a rotator nobody has tested is not a failed one", () => {
  // The server publishes null for both until they are measured, and an older
  // server publishes neither key at all.
  for (const over of [
    {}, { sky_sign: null, trusted: null },
  ]) {
    seed({}, { rotator: rotator(over) });
    mount();
    assert(!/FAILED/.test(line()),
      `the line says FAILED for a rotator whose test has not run (got "${line()}")`);
    eq(line(), "sign not measured, follow test not run", "wrong line for an unmeasured rotator");
    assert(id("rotator-preflight-line").className.includes("nx-rot-err") === false,
      "an unmeasured rotator is drawn as an error");
  }
});

test("a measured sign is named, with its sign", () => {
  seed({}, { rotator: rotator({ sky_sign: -1, trusted: null }) });
  mount();
  eq(line(), "sign -1 measured, follow test not run", "the measured sign is not stated");
  seed({}, { rotator: rotator({ sky_sign: 1, trusted: null }) });
  mount();
  eq(line(), "sign +1 measured, follow test not run", "a +1 sign is not stated as +1");
});

test("a passed follow test is said in words", () => {
  seed({}, { rotator: rotator({ sky_sign: -1, trusted: true }) });
  mount();
  eq(line(), "sign -1 measured, follow test passed: the camera turned with a 20 degree step",
    "a passed test is not stated");
});

test("a failed follow test says rotation is off until the rig reconnects", () => {
  seed({}, { rotator: rotator({ sky_sign: -1, trusted: false }) });
  mount();
  assert(/^FAILED:/.test(line()), `the line does not lead with FAILED (got "${line()}")`);
  assert(/rotation is off until the rig reconnects/.test(line()),
    `the line does not say what the failure costs and what clears it (got "${line()}")`);
  assert(id("rotator-preflight-line").className.includes("nx-rot-err"),
    "a failed test is not drawn as an error");
});

test("a failed test is a failure even when the sign was never measured", () => {
  seed({}, { rotator: rotator({ sky_sign: null, trusted: false }) });
  mount();
  assert(/^FAILED:/.test(line()), `an untrusted rotator reads as fine (got "${line()}")`);
});

// ================================================================= the press
await testAsync("pressing TEST ROTATOR posts the preflight route and nothing else", async () => {
  seed();
  mount();
  asked.length = 0;
  click(id("rotator-preflight"));
  await settle();
  const posts = commands();
  eq(posts.length, 1, "the press sent more or fewer than one command");
  eq(posts[0].method, "POST", "wrong method");
  assert(posts[0].url.endsWith("/api/rotator/preflight"),
    `posted ${posts[0].url} instead of /api/rotator/preflight`);
});

test("once both halves are known the button is locked with its reason", () => {
  seed({}, { rotator: rotator({ sky_sign: 1, trusted: true }) });
  mount();
  const b = id("rotator-preflight");
  assert(locked(b), "TEST ROTATOR is live when there is nothing left to measure");
  assert(/already measured the rotator/.test(b.getAttribute("title") || ""),
    `no reason on the locked button: ${JSON.stringify(b.getAttribute("title"))}`);
});

await testAsync("a locked TEST ROTATOR posts nothing", async () => {
  seed({}, { rotator: rotator({ sky_sign: -1, trusted: false }) });
  mount();
  assert(locked(id("rotator-preflight")),
    "precondition failed: a failed rotator left the button live");
  asked.length = 0;
  click(id("rotator-preflight"));
  await settle();
  eq(commands().length, 0, "a locked press reached the rig");
});

test("half-known is still live: the sign is measured, the follow test is not", () => {
  seed({}, { rotator: rotator({ sky_sign: -1, trusted: null }) });
  mount();
  assert(!locked(id("rotator-preflight")),
    "the button is locked while the follow test has not run");
});

// =================================================================== the gates
await testAsync("a viewer cannot press it, and is told why", async () => {
  seed({ principal: VIEWER });
  mount();
  const b = id("rotator-preflight");
  assert(locked(b), "a viewer can press TEST ROTATOR");
  assert((b.getAttribute("title") || "").length > 0, "no reason on the viewer's locked button");
  assert(b.getAttribute("disabled") == null, "a native disabled attribute took the reason away");
  asked.length = 0;
  click(b);
  await settle();
  eq(commands().length, 0, "a viewer's press reached the rig");
});

test("it is locked while the rotate_to_pa lane is running", () => {
  seed({}, { busy_lanes: ["rotate_to_pa"] });
  mount();
  assert(locked(id("rotator-preflight")),
    "TEST ROTATOR is live while a solve or rotation owns the lane it spawns on");
});

test("it needs a camera, as the other solving buttons do", () => {
  seed({}, {
    connected: { rotator: { name: "ZWO CAA", connected: true } },
    backend_links: [
      { role: "rotator", connected: true, error: null },
      { role: "camera", connected: false, error: null },
    ],
  });
  mount();
  assert(locked(id("rotator-preflight")),
    "TEST ROTATOR is live with no camera to solve through");
});

act(() => { rootRef!.unmount(); });

const total = passed + failed;
console.log(`w14RotatorPreflight.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
