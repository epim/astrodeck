// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w18QuickActionsLiveRun.test.tsx - RIG - DEVICES quick actions, MOUNTED: COOL and
// UNPARK stay locked for every live run state, not only running and paused
// (#821, WP-158, wave 18).
//
//   Run directly:  npx tsx src/next/hubs/rig/devices/__tests__/w18QuickActionsLiveRun.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT. `flowOwns` in `QuickActions.tsx` was `state === "running" || state
// === "paused"`. A cloud hold ("holding") and an abort's wind-down ("aborting")
// are `engine.running` on the rig, so the run still owns the camera (hold darks,
// cloud probes) and the mount, but the COOL / WARM button and UNPARK came back
// armed and a press reached the rig to be refused, or worse, accepted.
//
// WHAT IS WORTH ASSERTING. Per state in the union: COOL carries the camera
// sentence (the PAUSED variant for "paused" only) and UNPARK carries the mount
// sentence exactly when the run is live; a run that is not live locks neither
// (the control row, which a predicate that is simply always true fails); a press
// on a locked control explains and sends nothing.
//
// MUTANT "running or paused only" (`const flowOwns = seqState === "running" ||
// seqState === "paused";`, the unfixed text). Run from a byte backup, restored
// byte-identically (md5sum compared): see the report for the failing lines.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/#/rig/devices", pretendToBeVisual: true },
);
const win = dom.window as any;
win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} addEventListener() {} send() {} };
const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "KeyboardEvent", "PointerEvent", "localStorage",
  "getComputedStyle", "matchMedia", "WebSocket", "requestAnimationFrame",
  "cancelAnimationFrame",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const asked: Array<{ method: string; url: string }> = [];
g.fetch = async (url: string, init?: { method?: string }) => {
  asked.push({ method: init?.method ?? "GET", url: String(url) });
  return { ok: true, status: 200, statusText: "OK", json: async () => ({}) };
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { QuickActions } = await import("../QuickActions");
const { FLOW_OWNS_CAMERA, FLOW_OWNS_CAMERA_PAUSED } = await import("../../sheets/camera");
const { FLOW_OWNS_MOUNT } = await import("../../sheets/mount");

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
async function testAsync(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg} (expected ${String(want)}, got ${String(got)})`);
}
const settle = async () => {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
};

// ------------------------------------------------------------------ fixtures
const ADMIN = {
  role: "admin", email: "admin@rig",
  caps: ["view.status", "view.preview", "control.capture", "control.mount",
    "control.reconnect", "config.safety", "config.backend"],
};
const ROLES = ["camera", "telescope"];

function seed(sequence: Record<string, unknown>, parked: boolean): void {
  act(() => {
    useStore.setState({
      principal: ADMIN,
      wsPhase: "up",
      equipConnected: true,
      toasts: [],
      sequence,
      config: { cooling: { warm_ramp: true, warm_rate_c_per_min: 2, warm_ambient_c: null } },
      status: {
        connected: {
          camera: { name: "ASI2600MM Pro", kind: "camera", connected: true },
          telescope: { name: "AM5N", kind: "telescope", connected: true },
        },
        looping: false,
        busy_lanes: [],
        backend_links: ROLES.map((role) => ({
          role, ok: true, error: null, attempted: true, connected: true,
        })),
        camera: {
          temperature: 8.4, can_cool: true,
          cooler: { on: false, power: 0, target_c: -10, at_target: false, can_report_power: true },
        },
        mount: { tracking: true, parked, slewing: false },
      },
    } as never);
  });
}

const container = win.document.getElementById("root") as any;
let rootRef: ReturnType<typeof createRoot> | null = null;
function mount(): void {
  if (rootRef) act(() => { rootRef!.unmount(); });
  rootRef = createRoot(container);
  act(() => { rootRef!.render(createElement(QuickActions as any)); });
}
function click(node: any): void {
  act(() => { node.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
}
const q = (sel: string) => container.querySelector(sel) as any;
const locked = (node: any): boolean => node?.getAttribute("aria-disabled") === "true";
const titleOf = (node: any): string => (node?.getAttribute("title") ?? "") as string;
const toastTitles = (): string[] =>
  ((useStore.getState() as any).toasts as Array<{ title?: string }>).map((t) => String(t.title));

/** The whole `SequenceState["state"]` union, with whether the rig's
 *  `engine.running` is true in it. */
const STATES: Array<[string, boolean]> = [
  ["running", true], ["paused", true], ["holding", true], ["aborting", true],
  ["idle", false], ["complete", false], ["aborted", false], ["error", false],
  ["nina_native", false],
];

// ----------------------------------------------------------- the precondition
seed({ state: "idle" }, false);
mount();
await settle();
test("the quick actions mounted, with COOL armed and PARK armed on an idle rig", () => {
  assert(q('[data-testid="quick-actions"]') != null, "no marker - the quick actions did not render");
  assert(q('[data-testid="quick-cool"]') != null, "no COOL button");
  assert(q('[data-testid="quick-park"]') != null, "no PARK button");
  assert(!locked(q('[data-testid="quick-cool"]')),
    `COOL is locked on an idle rig (${titleOf(q('[data-testid="quick-cool"]'))})`);
  assert(!locked(q('[data-testid="quick-park"]')), "PARK is locked on an idle rig");
});

// ------------------------------------------------------------------- COOL
for (const [state, live] of STATES) {
  test(`run ${state}: COOL is ${live ? "locked with the run's camera sentence" : "not locked by a run"}`, () => {
    seed({ state }, false);
    mount();
    const cool = q('[data-testid="quick-cool"]');
    assert(cool != null, "no COOL button");
    if (live) {
      assert(locked(cool), `COOL is ARMED while the run is ${state}: the run owns the camera`);
      eq(titleOf(cool), state === "paused" ? FLOW_OWNS_CAMERA_PAUSED : FLOW_OWNS_CAMERA,
        `COOL's reason while the run is ${state}`);
    } else {
      assert(!locked(cool), `COOL is locked by a run that is ${state}: ${titleOf(cool)}`);
    }
  });
}

// ----------------------------------------------------------------- UNPARK
for (const [state, live] of STATES) {
  test(`run ${state}: UNPARK is ${live ? "locked with the run's mount sentence" : "not locked by a run"}`, () => {
    seed({ state }, true);
    mount();
    const park = q('[data-testid="quick-park"]');
    assert(park != null, "no PARK / UNPARK button");
    assert(/UNPARK/.test(park.textContent), `the button is not UNPARK on a parked mount (${park.textContent})`);
    if (live) {
      assert(locked(park), `UNPARK is ARMED while the run is ${state}: the run owns the mount`);
      eq(titleOf(park), FLOW_OWNS_MOUNT, `UNPARK's reason while the run is ${state}`);
    } else {
      assert(!locked(park), `UNPARK is locked by a run that is ${state}: ${titleOf(park)}`);
    }
  });
}

// ------------------------------------------------- a press explains, sends nothing
for (const state of ["holding", "aborting"]) {
  await testAsync(`run ${state}: a press on COOL and on UNPARK states the reason and reaches nothing`, async () => {
    seed({ state }, true);
    mount();
    for (const [testid, sentence] of [
      ["quick-cool", FLOW_OWNS_CAMERA],
      ["quick-park", FLOW_OWNS_MOUNT],
    ] as const) {
      act(() => { useStore.setState({ toasts: [] } as never); });
      asked.length = 0;
      click(q(`[data-testid="${testid}"]`));
      await settle();
      // The toast clips a long sentence with an ellipsis, so match its head.
      assert(toastTitles().some((t) => t.startsWith(sentence.slice(0, 60))),
        `${testid} refused in silence: toasts were ${JSON.stringify(toastTitles())}`);
      eq(asked.filter((a) => a.method !== "GET").length, 0,
        `${testid} reached the rig anyway: ${JSON.stringify(asked)}`);
    }
  });
}

// ------------------------------------------------ the lock lifts with the run
await testAsync("a hold ending re-arms COOL and UNPARK without a remount", async () => {
  seed({ state: "holding" }, true);
  mount();
  assert(locked(q('[data-testid="quick-cool"]')), "premise: COOL is locked through the hold");
  act(() => { useStore.setState({ sequence: { state: "running" } } as never); });
  assert(locked(q('[data-testid="quick-cool"]')), "COOL unlocked when the hold gave way to a run");
  act(() => { useStore.setState({ sequence: { state: "complete" } } as never); });
  await settle();
  assert(!locked(q('[data-testid="quick-cool"]')), "COOL is still locked after the run ended");
  assert(!locked(q('[data-testid="quick-park"]')), "UNPARK is still locked after the run ended");
});

if (rootRef) act(() => { rootRef!.unmount(); });

console.log(`w18QuickActionsLiveRun: ${passed}/${passed + failed} passed`);
for (const f of failures) console.log(f);
export const result = { passed, failed, total: passed + failed };
if (failed) process.exitCode = 1;
