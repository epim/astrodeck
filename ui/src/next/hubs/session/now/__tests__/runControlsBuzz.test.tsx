// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// runControlsBuzz.test.tsx - the run-ended buzz fires on ENTERING error or
// aborted while mounted, never on mounting in it (#467). MOUNTED.
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/next/hubs/session/now/__tests__/runControlsBuzz.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// Found by the S6 real-page probe (2026-09-28, desktop, local simulator):
// after a walk had aborted its run, every page load logged
//
//     Blocked call to navigator.vibrate because user hasn't tapped on the
//     frame or any embedded frame yet
//
// before any click. RunControls buzzed when the state was error or aborted
// and its ref was false, and the ref started false on every mount.
// SessionColumn mounts RunControls on every desktop #/next page and the Now
// screen on a phone, so each open, reload or remount "entered" a state the
// run had ended in hours before, and a phone buzzed the alarm pattern for it.
// The classic Monitor, which RunControls copied, had the same ref.
//
// Two ways to mount "in" the state, and both are graded here:
//   * the store already knows it (an in-app navigation): the ref is seeded
//     from the state at mount;
//   * the page load: the store holds its cold EMPTY_SEQUENCE, which reads
//     "idle", and the snapshot's `aborted` then arrives. That is the store
//     learning a state, not the rig entering one, and it is exactly the
//     probe's page load. The first KNOWN state seeds the ref.
//
// The vibrate spy is the whole observable: `navigator.vibrate` is replaced by
// a recorder, and each case counts the calls it caused.
//
// NAMED MUTANTS. Each was run in a private scratch copy of ui/ (never the
// shared tree); the observed failure is quoted at the test it turns red.
//   B1 "ref starts false"              RunControls: useRef<boolean | null>(false)
//   B2 "cold store read as a state"    RunControls: the EMPTY_SEQUENCE check
//                                      removed (the cold idle seeds the ref)
//   B3 "ref starts false" (classic)    MonitorView: useRef<boolean | null>(false)
//   B4 "cold store read as a state" (classic)
//                                      MonitorView: the EMPTY_SEQUENCE check removed

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/#/session/now", pretendToBeVisual: true },
);
const win = dom.window as any;
win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} addEventListener() {} send() {} };
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
win.scrollTo = () => {};

// The spy. jsdom has no vibrate; the components call `navigator.vibrate?.()`.
const buzzes: unknown[] = [];
Object.defineProperty(win.navigator, "vibrate", {
  value: (pattern: unknown) => { buzzes.push(pattern); return true; },
  configurable: true, writable: true,
});

// The classic Monitor reads the snapshot on mount; `snapshotBody` is its
// answer. Every other read fails, which each caller here treats as offline.
let snapshotBody: Record<string, unknown> = { preview_id: null };
win.fetch = async (url: any) => {
  if (String(url).includes("/api/monitor/snapshot")) {
    return { ok: true, status: 200, statusText: "OK", json: async () => snapshotBody };
  }
  throw new Error("offline in this test");
};

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "PointerEvent", "localStorage", "sessionStorage", "getComputedStyle",
  "matchMedia", "WebSocket", "ResizeObserver",
  "requestAnimationFrame", "cancelAnimationFrame", "fetch",
  // `api.ts` and `lib/base.ts` read the BARE `location` at module scope.
  "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------ imports, AFTER the globals are set
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { EMPTY_SEQUENCE } = await import("../../../../../lib/authGate");
const { RunControls } = await import("../RunControls");
const MonitorView = (await import("../../../../../views/MonitorView")).default;

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => Promise<void> | void): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg} (expected ${String(want)}, got ${String(got)})`);
}
async function settle(): Promise<void> {
  await act(async () => { for (let i = 0; i < 5; i++) await new Promise((r) => setTimeout(r, 0)); });
}
async function setSeq(seq: Record<string, unknown>): Promise<void> {
  act(() => { useStore.setState({ sequence: seq } as never); });
  await settle();
}
/** Hand the store a sequence the way the socket and the snapshot do. */
async function sequenceEvent(data: Record<string, unknown>): Promise<void> {
  act(() => { useStore.getState().handleEvent({ type: "sequence", data, ts: Date.now() / 1000 }); });
  await settle();
}
/** Mount `component` in its own box, run `fn`, and unmount it even when `fn`
 *  throws, so a failed case never leaves a mounted buzzer behind for the next. */
async function mounted(component: any, marker: string, fn: () => Promise<void>): Promise<void> {
  const box = win.document.createElement("div");
  win.document.body.appendChild(box);
  const root = createRoot(box);
  act(() => { root.render(createElement(component)); });
  await settle();
  try {
    // Asserted first, so "no buzz" can never pass over a blank page.
    assert(box.querySelector(marker) != null, `precondition: ${marker} did not render`);
    await fn();
  } finally {
    act(() => { root.unmount(); });
    box.remove();
  }
}
const running = (framesDone: number) => ({
  state: "running", plan_name: "M31", target: "M31", detail: "M31: L 60s [3/40]",
  progress: { frames_done: framesDone, frames_total: 40, percent: 8, elapsed_s: 300, rejected: 0,
    current_exposure_s: 60 },
});
const aborted = { state: "aborted", plan_name: "M31", target: "M31", detail: "M31: stopped by user" };
const errored = { state: "error", plan_name: "M31", target: "M31", detail: "M31: camera timed out" };

useStore.setState({
  principal: { role: "admin", email: null, caps: ["view.status", "view.preview", "control.mount"] },
  authGate: "open",
  wsConnected: true,
} as never);

const RC = '[data-testid="now-run-controls"]';

// --------------------------------------------------------- RunControls
await test("page load: the snapshot's aborted landing on the cold store does not buzz", async () => {
  // The probe's own page load. The store has heard nothing yet: its sequence
  // is the cold EMPTY_SEQUENCE, which reads "idle".
  // B2 "cold store read as a state", observed (7 passed, 1 failed):
  //   x page load: the snapshot's aborted landing on the cold store does not
  //   buzz: the page load's snapshot buzzed for a run that had already ended
  //   (expected 0, got 1)
  assert(useStore.getState().sequence === EMPTY_SEQUENCE, "precondition: the store starts cold");
  const before = buzzes.length;
  await mounted(RunControls, RC, async () => {
    await sequenceEvent(aborted);
    eq(useStore.getState().sequence.state, "aborted", "precondition: the snapshot's state landed");
    eq(buzzes.length - before, 0, "the page load's snapshot buzzed for a run that had already ended");
    // Control, on the same mount: a new run that aborts while watched does.
    await sequenceEvent(running(1));
    await sequenceEvent(aborted);
    eq(buzzes.length - before, 1, "control: a run aborting while mounted must buzz once");
  });
});

await test("mounting while aborted calls no vibrate (the store already knew)", async () => {
  // B1 "ref starts false", observed (5 passed, 3 failed; the other two are the
  // error mount below and the later-run control, whose mount buzzed first):
  //   x mounting while aborted calls no vibrate (the store already knew):
  //   mounting over an aborted run buzzed (expected 0, got 1)
  await setSeq(aborted);
  const before = buzzes.length;
  await mounted(RunControls, RC, async () => {
    eq(buzzes.length - before, 0, "mounting over an aborted run buzzed");
  });
});

await test("mounting while in error calls no vibrate", async () => {
  await setSeq(errored);
  const before = buzzes.length;
  await mounted(RunControls, RC, async () => {
    eq(buzzes.length - before, 0, "mounting over a failed run buzzed");
  });
});

await test("running then aborted calls it once, and the ticks after it do not", async () => {
  await setSeq(running(3));
  const before = buzzes.length;
  await mounted(RunControls, RC, async () => {
    eq(buzzes.length - before, 0, "precondition: a running run does not buzz");
    await setSeq(aborted);
    eq(buzzes.length - before, 1, "running -> aborted did not buzz exactly once");
    assert(JSON.stringify(buzzes[buzzes.length - 1]) === "[60,40,60]", "the buzz is not the run-ended pattern");
    await setSeq({ ...aborted, detail: "M31: stopped by user (report written)" });
    await setSeq(errored);
    eq(buzzes.length - before, 1, "a tick inside the ended state buzzed again");
  });
});

await test("control: a later run that fails is a new entry and buzzes again", async () => {
  await setSeq(aborted);
  const before = buzzes.length;
  await mounted(RunControls, RC, async () => {
    await setSeq(running(1));
    await setSeq(errored);
    eq(buzzes.length - before, 1, "a new run failing while mounted did not buzz");
  });
});

// ------------------------------------------------ the classic Monitor
// The same rule in MonitorView.tsx, which RunControls copied. Its own mount
// read seeds the store from the snapshot, so the page load is driven through
// that read rather than by hand.
// Any panel title: the Monitor's panels are sections with one.
const MON = "section .panel-title";

await test("classic page load: the Monitor's own snapshot seed on the cold store does not buzz", async () => {
  // B4 "cold store read as a state" (classic), observed (7 passed, 1 failed):
  //   x classic page load: the Monitor's own snapshot seed on the cold store
  //   does not buzz: the classic page load buzzed for a run that had already
  //   ended (expected 0, got 1)
  act(() => { useStore.setState({ sequence: EMPTY_SEQUENCE } as never); });
  snapshotBody = { preview_id: null, sequence: aborted };
  const before = buzzes.length;
  await mounted(MonitorView, MON, async () => {
    eq(useStore.getState().sequence.state, "aborted", "precondition: the Monitor's mount read seeded the state");
    eq(buzzes.length - before, 0, "the classic page load buzzed for a run that had already ended");
    await setSeq(running(1));
    await setSeq(aborted);
    eq(buzzes.length - before, 1, "control: a run aborting while the classic Monitor is open must buzz once");
  });
  snapshotBody = { preview_id: null };
});

await test("classic: mounting while aborted calls no vibrate", async () => {
  // B3 "ref starts false" (classic), observed (7 passed, 1 failed):
  //   x classic: mounting while aborted calls no vibrate: mounting the classic
  //   Monitor over an aborted run buzzed (expected 0, got 1)
  await setSeq(aborted);
  const before = buzzes.length;
  await mounted(MonitorView, MON, async () => {
    eq(buzzes.length - before, 0, "mounting the classic Monitor over an aborted run buzzed");
  });
});

await test("classic: running then aborted calls it once", async () => {
  await setSeq(running(3));
  const before = buzzes.length;
  await mounted(MonitorView, MON, async () => {
    await setSeq(aborted);
    eq(buzzes.length - before, 1, "running -> aborted did not buzz exactly once on the classic Monitor");
  });
});

// ------------------------------------------------------------------ report
for (const f of failures) console.log(f);
console.log(`runControlsBuzz: ${passed} passed, ${failed} failed`);
export default { passed, failed, total: passed + failed };
// The classic Monitor's dome poll and coarse tick are cleared on unmount, but
// jsdom's window can still hold a pending timer; exit on the file's result.
(globalThis as any).process?.exit(failed ? 1 : 0);
