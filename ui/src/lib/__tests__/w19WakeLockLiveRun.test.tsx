// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w19WakeLockLiveRun.test.tsx - the monitor screen wake lock is held for EVERY
// live run state, not only running and paused (#922, WP-161, wave 19).
//
//   Run directly:  npx tsx src/lib/__tests__/w19WakeLockLiveRun.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT. `useMonitorWakeLock` derived its "a sequence is running" signal as
// `state === "running" || state === "paused"`. A cloud hold ("holding") is the
// long-lived state on a cloudy night and an abort's wind-down ("aborting") is
// minutes long; in both the run is live on the rig, but the lock was released,
// so the monitor tablet could sleep over a run that was still probing the sky.
//
// WHAT IS WORTH ASSERTING. The lock itself, not the hook's return value: a fake
// `navigator.wakeLock` records every request and whether each sentinel is still
// held. Per state in the union a probe is mounted and the held count is read;
// then the transition that matters, a run going into a hold and out of it, must
// never drop the lock in between.
//
// MUTANT "running or paused only" (`useMonitorWakeLock`'s `runIsLive(s.sequence)`
// made `s.sequence.state === "running" || s.sequence.state === "paused"`, the
// unfixed text). Run from a byte backup, restored byte-identically (md5sum
// compared): see the report for the failing lines.

/* eslint-disable @typescript-eslint/no-explicit-any */

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
const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "localStorage", "requestAnimationFrame", "cancelAnimationFrame",
  "getComputedStyle", "matchMedia", "WebSocket",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ----------------------------------------------------------- a fake wake lock
interface Sentinel {
  released: boolean;
  release(): Promise<void>;
  addEventListener(type: string, cb: () => void): void;
}
const sentinels: Sentinel[] = [];
Object.defineProperty(win.navigator, "wakeLock", {
  configurable: true,
  value: {
    async request(_type: "screen"): Promise<Sentinel> {
      const s: Sentinel = {
        released: false,
        async release() { s.released = true; },
        addEventListener() { /* the OS release event is not modelled */ },
      };
      sentinels.push(s);
      return s;
    },
  },
});
const held = (): number => sentinels.filter((s) => !s.released).length;

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../store");
const { useMonitorWakeLock } = await import("../useWakeLock");

let passed = 0;
let failed = 0;
const failures: string[] = [];
async function testAsync(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
const settle = async () => {
  for (let i = 0; i < 6; i++) await act(async () => { await Promise.resolve(); });
};

function Probe(): any {
  const { active } = useMonitorWakeLock();
  return createElement("span", { "data-active": String(active) });
}

const container = win.document.getElementById("root") as any;
let rootRef: ReturnType<typeof createRoot> | null = null;
async function mountWith(sequence: Record<string, unknown>): Promise<void> {
  if (rootRef) { act(() => { rootRef!.unmount(); }); rootRef = null; }
  sentinels.length = 0;
  act(() => { useStore.setState({ sequence, monitorAwake: false } as never); });
  rootRef = createRoot(container);
  await act(async () => { rootRef!.render(createElement(Probe)); });
  await settle();
}
const probeActive = (): string | null =>
  container.querySelector("[data-active]")?.getAttribute("data-active") ?? null;

/** The whole `SequenceState["state"]` union, with whether the rig's
 *  `engine.running` is true in it. */
const STATES: Array<[string, boolean]> = [
  ["running", true], ["paused", true], ["holding", true], ["aborting", true],
  ["idle", false], ["complete", false], ["aborted", false], ["error", false],
  ["nina_native", false],
];

for (const [state, live] of STATES) {
  await testAsync(`run ${state}: the screen wake lock is ${live ? "held" : "not held"}`, async () => {
    await mountWith({ state });
    assert(probeActive() !== null, "the probe did not render (nothing below grades a blank page)");
    assert(probeActive() === String(live),
      `useMonitorWakeLock reports active=${probeActive()} while the run is ${state}`);
    assert(held() === (live ? 1 : 0),
      live
        ? `the monitor tablet is free to sleep: ${held()} wake lock(s) held while the run is ${state}`
        : `${held()} wake lock(s) held while the run is ${state}: a battery drain with no run`);
  });
}

await testAsync("a run going into a cloud hold and out of it never drops the lock", async () => {
  await mountWith({ state: "running" });
  assert(held() === 1, `precondition: the lock is held while running (${held()})`);
  const requestedBefore = sentinels.length;
  act(() => { useStore.setState({ sequence: { state: "holding" } } as never); });
  await settle();
  assert(held() === 1, `the lock was released when the run went into a cloud hold (${held()} held)`);
  act(() => { useStore.setState({ sequence: { state: "running" } } as never); });
  await settle();
  assert(held() === 1, `the lock was lost when the hold ended (${held()} held)`);
  assert(sentinels.length === requestedBefore,
    `the lock was dropped and re-requested across the hold (${sentinels.length - requestedBefore} new request(s))`);
});

await testAsync("the lock is let go when the run really ends", async () => {
  await mountWith({ state: "aborting" });
  assert(held() === 1, `precondition: held through the abort's wind-down (${held()})`);
  act(() => { useStore.setState({ sequence: { state: "aborted" } } as never); });
  await settle();
  assert(held() === 0, `the lock outlived the run (${held()} held after aborted)`);
});

if (rootRef) act(() => { rootRef!.unmount(); });

console.log(`w19WakeLockLiveRun: ${passed}/${passed + failed} passed`);
for (const f of failures) console.log(f);
export const result = { passed, failed, total: passed + failed };
if (failed) process.exitCode = 1;
