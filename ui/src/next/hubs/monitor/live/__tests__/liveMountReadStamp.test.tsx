// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// liveMountReadStamp.test.tsx - MONITOR - LIVE's own mount read never
// overwrites a newer live event (#476, the LiveScreen half; S7 orchestrator
// ruling 3; spec 5.10).
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/next/hubs/monitor/live/__tests__/liveMountReadStamp.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// The screen reads GET /api/monitor/snapshot once on mount and seeds the
// store's `status` and `sequence` from the answer. The socket keeps delivering
// while that read is in flight, and the server builds its answer when the
// request reaches it, so an event handled after the request went out is newer
// than the answer. Applied on arrival, the answer wrote the older state over
// it. For `sequence` that is the worst case: a terminal state is published
// once and never again, so a `complete` overwritten by the answer's `running`
// brought the run's PAUSE and STOP back for a run that had ended, until the
// next reconnect or relay gap. ws.ts's `rehydrateFromSnapshot` and the classic
// Monitor's first read already obey ws.ts's `snapshotStamp`; this screen's
// read now takes the same stamp before its fetch and applies each part only
// while it is still fresh.
//
// The REAL ws.ts counts the live events: they arrive through its socket's
// onmessage, from a fake WebSocket whose handlers the test calls. The socket's
// onopen is never called, so ws.ts reads no snapshot of its own and the only
// read is the screen's. Each snapshot read is HELD until the test releases it,
// so "an event between send and answer" is an ordering the test forces, not
// one it hopes to observe.
//
// Every assertion runs after the screen has been found, so an absence cannot
// pass over a blank page.
//
// NAMED MUTANTS. Each was run in a private scratch copy of ui/ (scratchpad
// H4-UMON-mut, never the shared tree) from a byte backup restored and
// hash-compared after each run; the observed failure is quoted at the test it
// turns red.
//   S1 "mount read applies unconditionally (sequence)"  LiveScreen: the
//                                     answer's sequence is applied without the stamp.
//   S2 "mount read applies unconditionally (status)"    LiveScreen: likewise status.
//   S3 "stamp taken at the answer"    LiveScreen: snapshotStamp() is called after
//                                     the fetch resolves instead of before it.
//   S4 "one stamp for every type"     LiveScreen: the answer's sequence and status
//                                     are both applied only while neither type
//                                     has had an event since the send.

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
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
win.scrollTo = () => {};

class FakeWebSocket {
  static all: FakeWebSocket[] = [];
  onopen: (() => void | Promise<void>) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor() { FakeWebSocket.all.push(this); }
  close(): void { /* the test drives onclose itself, and never does here */ }
  addEventListener(): void {}
  send(): void {}
}
win.WebSocket = FakeWebSocket;

// The rig. Every snapshot read waits in `heldReads` until the test releases it
// with the answer it names; every other cold read fails, which is a supported
// path for every caller on this screen.
const heldReads: Array<(body: Record<string, unknown>) => void> = [];
let snapshotReads = 0;
const ok = (data: any) => ({ ok: true, status: 200, statusText: "OK", json: async () => data });
win.fetch = (url: any) => {
  const u = String(url);
  if (u.includes("/api/monitor/snapshot")) {
    snapshotReads++;
    return new Promise((resolve) => { heldReads.push((body) => resolve(ok(body))); });
  }
  if (u.includes("/api/logs")) return Promise.resolve(ok([]));
  return Promise.reject(new Error("offline in this test"));
};

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "KeyboardEvent", "localStorage", "getComputedStyle",
  "matchMedia", "WebSocket", "ResizeObserver", "requestAnimationFrame",
  // ws.ts reads the BARE `location` when it connects.
  "cancelAnimationFrame", "fetch", "location",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { connectWs } = await import("../../../../../ws");
const { LiveScreen } = await import("../LiveScreen");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
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
async function set(state: Record<string, unknown>): Promise<void> {
  act(() => { useStore.setState(state as never); });
  await settle();
}

// ------------------------------------------------------------------ the rig's words
const OPERATOR = {
  role: "operator", email: "op@rig",
  caps: ["view.status", "view.preview", "control.capture", "control.guide", "control.mount"],
};
const WHEEL = ["L", "R", "G", "B", "Ha"];
/** A status poll with the wheel at `slot`: the filter RUN PROGRESS prints
 *  beside the target is the one piece of the status this screen shows as a
 *  word, so it is what the status cases grade on the page. */
function statusAt(slot: string): any {
  return { mode: "alpaca", busy: [], filterwheel: { position: WHEEL.indexOf(slot), names: WHEEL } };
}
function seqOf(state: string, target: string, framesDone: number): any {
  return {
    state, plan_name: target, target, target_index: 0,
    detail: `${target}: ${state} [${framesDone}/105]`,
    progress: {
      frames_done: framesDone, frames_total: 105, percent: Math.round(framesDone / 1.05),
      elapsed_s: 3000, rejected: 0, current_exposure_s: 60,
    },
  };
}

// A socket for live events. ws.ts counts what arrives on it, which is what the
// stamp compares against.
connectWs();
const liveSock = FakeWebSocket.all[FakeWebSocket.all.length - 1];
const liveEvent = (obj: unknown) => act(() => { liveSock.onmessage?.({ data: JSON.stringify(obj) }); });

await set({
  principal: OPERATOR,
  authGate: "open",
  equipConnected: true,
  wsPhase: "up",
  wsConnected: true,
  preview: null,
  snapshotPreviewId: null,
});

const container = win.document.getElementById("root") as any;
const byId = (id: string): any => container.querySelector(`[data-testid="${id}"]`);
/** The one mono line under MONITOR, "<phase> · <target>" (monState). */
function stateLine(target: string): string {
  const leaves = [...container.querySelectorAll("span, div")]
    .filter((el: any) => el.children.length === 0)
    .map((el: any) => (el.textContent || "") as string);
  return leaves.find((t) => t.endsWith(` · ${target}`)) ?? "";
}
const progressTarget = (): string => (byId("monitor-progress-target")?.textContent || "") as string;

/** Mount a fresh screen, run `fn`, and unmount it even when `fn` throws: a
 *  screen left mounted by a failed case would hold a read of its own into the
 *  next case and fail it for a reason that is not its own. */
async function withFreshScreen(fn: () => Promise<void>): Promise<void> {
  const root = createRoot(container);
  act(() => { root.render(createElement(LiveScreen)); });
  try {
    await settle();
    assert(byId("monitor-live") != null, "precondition: MONITOR - LIVE did not render");
    await fn();
  } finally {
    act(() => { root.unmount(); });
    heldReads.length = 0;
  }
}
/** Release the one read the mount sent, with this answer, and let it apply. */
async function answer(body: Record<string, unknown>): Promise<void> {
  eq(heldReads.length, 1, "precondition: the mount's one read is still in flight");
  act(() => { heldReads.shift()?.({ guide_recent: [], busy: [], preview_id: null, ...body }); });
  await settle();
}

// ------------------------------------------------------------------ sequence
await test("a sequence event between send and answer beats the older mount read, and the screen stays complete", async () => {
  // The run completes while the screen's read is in flight. `complete` is
  // published once, so a `running` written over it would never be undone.
  // S1 "mount read applies unconditionally (sequence)", observed (3 passed, 1 failed):
  //   x a sequence event between send and answer beats the older mount read,
  //   and the screen stays complete: the older mount read's running
  //   overwrote a newer complete (expected complete, got running)
  // S3 "stamp taken at the answer", observed (2 passed, 2 failed; the status
  // case below fails with it):
  //   x a sequence event between send and answer beats the older mount read,
  //   and the screen stays complete: the older mount read's running
  //   overwrote a newer complete (expected complete, got running)
  // S4 "one stamp for every type", observed on this case's control (2 passed,
  // 2 failed; the status case's control fails with it):
  //   x a sequence event between send and answer beats the older mount read,
  //   and the screen stays complete: control: no status event was in between,
  //   so the answer's status must still apply (expected 1, got 0)
  await set({ sequence: seqOf("running", "M31", 60), status: statusAt("L") });
  const before = snapshotReads;
  await withFreshScreen(async () => {
    eq(snapshotReads, before + 1, "precondition: the mount sent its read");
    assert(byId("run-pause") != null, "precondition: a live run offers PAUSE");
    liveEvent({ type: "sequence", ts: 1, data: seqOf("complete", "M31", 105) });
    await settle();
    eq(useStore.getState().sequence.state, "complete", "precondition: the live event landed");
    await answer({ sequence: seqOf("running", "M31", 104), status: statusAt("R") });
    eq(useStore.getState().sequence.state, "complete", "the older mount read's running overwrote a newer complete");
    assert(stateLine("M31").includes("complete"), `the screen reads the run as live again: "${stateLine("M31")}"`);
    eq(byId("run-pause"), null, "PAUSE is offered for a run that has ended");
    eq(byId("run-abort"), null, "STOP is offered for a run that has ended");
    eq(useStore.getState().status?.filterwheel?.position, WHEEL.indexOf("R"),
      "control: no status event was in between, so the answer's status must still apply");
  });
});

// ------------------------------------------------------------------ status
await test("a status event between send and answer beats the older mount read", async () => {
  // The 2 s poll moves on while the read is in flight: the wheel has turned
  // to Ha, and the answer still says L.
  // S2 "mount read applies unconditionally (status)", observed (3 passed, 1 failed):
  //   x a status event between send and answer beats the older mount read:
  //   the older mount read's status overwrote a newer status event
  //   (expected 4, got 0)
  // S3 "stamp taken at the answer" fails here with the same line.
  // S4 "one stamp for every type", observed on this case's control:
  //   x a status event between send and answer beats the older mount read:
  //   control: no sequence event was in between, so the answer's sequence
  //   must still apply (expected 61, got 60)
  await set({ sequence: seqOf("running", "M31", 60), status: statusAt("L") });
  await withFreshScreen(async () => {
    assert(progressTarget().startsWith("M31"), `precondition: RUN PROGRESS names the target: "${progressTarget()}"`);
    liveEvent({ type: "status", ts: 2, data: statusAt("Ha") });
    await settle();
    eq(progressTarget(), "M31 · Ha", "precondition: the live status landed on the page");
    await answer({ sequence: seqOf("running", "M31", 61), status: statusAt("L") });
    eq(useStore.getState().status?.filterwheel?.position, WHEEL.indexOf("Ha"),
      "the older mount read's status overwrote a newer status event");
    eq(progressTarget(), "M31 · Ha", "RUN PROGRESS went back to the older filter");
    eq(useStore.getState().sequence.progress?.frames_done, 61,
      "control: no sequence event was in between, so the answer's sequence must still apply");
  });
});

// ------------------------------------------------------------------ control
await test("control: with no event in between, the mount read applies its sequence and status", async () => {
  // Both types HAVE had live events before this send (the cases above), so the
  // stamp's counts are not zero here: a rule that refused any answer once an
  // event of its type had ever been seen goes red on this case.
  await set({ sequence: seqOf("running", "M31", 62), status: statusAt("L") });
  await withFreshScreen(async () => {
    await answer({ sequence: seqOf("running", "NGC 891", 3), status: statusAt("G") });
    eq(useStore.getState().sequence.target, "NGC 891", "the mount read did not apply its sequence");
    eq(useStore.getState().status?.filterwheel?.position, WHEEL.indexOf("G"), "the mount read did not apply its status");
    eq(progressTarget(), "NGC 891 · G", "the page does not show what the mount read said");
  });
});

await test("control: a terminal answer with no event in between ends the run on the page", async () => {
  // The read exists for this: a run that ended while nothing was listening.
  await set({ sequence: seqOf("running", "M31", 63), status: statusAt("L") });
  await withFreshScreen(async () => {
    assert(byId("run-pause") != null, "precondition: a live run offers PAUSE");
    await answer({ sequence: seqOf("aborted", "M31", 63), status: statusAt("L") });
    eq(useStore.getState().sequence.state, "aborted", "the mount read did not apply a terminal sequence");
    eq(byId("run-pause"), null, "PAUSE is still offered after the read said the run ended");
  });
});

// ------------------------------------------------------------------ report
for (const f of failures) console.log(f);
console.log(`liveMountReadStamp: ${passed} passed, ${failed} failed`);
export default { passed, failed, total: passed + failed };
// connectWs() above leaves ws.ts's socket and auth reads on jsdom's window;
// exit on the file's own result rather than wait on them.
(globalThis as any).process?.exit(failed ? 1 : 0);
