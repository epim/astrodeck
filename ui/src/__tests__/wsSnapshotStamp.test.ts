// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// wsSnapshotStamp.test.ts - a snapshot re-read never overwrites a newer live
// event (#476, S7 orchestrator ruling 3).
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/__tests__/wsSnapshotStamp.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// ws.ts reads GET /api/monitor/snapshot on every connect and, since #399,
// after every relay_gap notice, which is mid-stream and on the slowest links.
// The socket keeps delivering events while that read is in flight, and the
// server builds its answer when the request reaches it. So an event handled
// after the request went out can be newer than the answer, and applying the
// answer on arrival wrote the older state over it. For `sequence` that is the
// worst case: a terminal state (complete, aborted, error) is published once
// and never again, so a `complete` overwritten by the answer's `running` read
// as running until the next reconnect or gap.
//
// The ruling: rehydrateFromSnapshot takes a per-type stamp when the request is
// sent, and does not apply the answer's `sequence` or `status` (nor `polar`,
// nor the focus clear, which have the same shape) if an event of that type was
// handled after that moment. `preview_id` keeps its replace semantics.
//
// This drives the REAL ws.ts against the REAL store: a fake WebSocket whose
// handlers the test calls, and a stubbed api.get whose snapshot answers are
// HELD until the test releases them, so "an event between send and answer" is
// an ordering the test forces rather than one it hopes to observe.
//
// NAMED MUTANTS. Each was run in a private scratch copy of ui/ (never the
// shared tree); the observed failure is quoted at the test it turns red.
//   U1 "snapshot applied unconditionally (sequence)"  ws.ts: the answer's
//                                     sequence is applied without the stamp.
//   U2 "snapshot applied unconditionally (status)"    ws.ts: likewise status.
//   U3 "one stamp for every type"     ws.ts: fresh() compares the total of
//                                     events of all types, so any event blocks.
//   U4 "stamp taken at the answer"    ws.ts: snapshotStamp() is called after
//                                     the await instead of before the request.
//   U5 "preview_id stamped too"       ws.ts: setSnapshotPreviewId only when no
//                                     preview event was handled since the send.
//   U6 "snapshot applied unconditionally (polar)"     ws.ts: likewise polar.
//   U7 "focus cleared unconditionally" ws.ts: the busy-driven focus clear
//                                     ignores the stamp.

// ------------------------------------------------------------ browser stubs
class MemStorage {
  private m = new Map<string, string>();
  getItem(k: string): string | null { return this.m.has(k) ? (this.m.get(k) as string) : null; }
  setItem(k: string, v: string): void { this.m.set(k, String(v)); }
  removeItem(k: string): void { this.m.delete(k); }
  clear(): void { this.m.clear(); }
}

// Timers go nowhere: nothing here needs the reconnect, prove or trailing-read
// timers to fire, and a real one would keep node alive.
const g = globalThis as unknown as {
  localStorage?: Storage; document?: unknown; window?: unknown; location?: unknown;
  WebSocket?: unknown;
};
if (typeof g.localStorage === "undefined") g.localStorage = new MemStorage() as unknown as Storage;
if (typeof g.document === "undefined") {
  const classList = { toggle() {}, add() {}, remove() {}, contains() { return false; } };
  const style = { setProperty() {}, getPropertyValue() { return ""; } };
  g.document = { documentElement: { classList, style } };
}
const FAKE_LOCATION = { pathname: "/", host: "localhost", protocol: "http:" };
let nextTimerId = 1;
g.window = {
  location: FAKE_LOCATION,
  setTimeout: () => nextTimerId++,
  clearTimeout: () => {},
  setInterval: () => 0,
  clearInterval: () => {},
  addEventListener() {},
  removeEventListener() {},
};
g.location = FAKE_LOCATION;

class FakeWebSocket {
  static all: FakeWebSocket[] = [];
  url: string;
  onopen: (() => void | Promise<void>) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(url: string) {
    this.url = url;
    FakeWebSocket.all.push(this);
  }
  close(): void { /* the test drives onclose itself */ }
}
g.WebSocket = FakeWebSocket;

// Wall clock, stepped by hand so each relay_gap is outside the throttle window
// and reads at once.
let nowMs = 1_800_000_000_000;
Date.now = () => nowMs;

const { useStore } = await import("../store");
const { api } = await import("../api");
const { connectWs, GAP_RESNAPSHOT_MS } = await import("../ws");

// The rig's side. Each snapshot read is HELD: `sent` counts requests that went
// out, and `answer(body)` releases the oldest one with the body given.
const held: Array<(body: unknown) => void> = [];
let sent = 0;
(api as unknown as { get: typeof api.get }).get = (async (path: string) => {
  if (path === "/api/monitor/snapshot") {
    sent++;
    return new Promise<never>((resolve) => { held.push(resolve as (body: unknown) => void); });
  }
  if (path === "/api/logs") return [] as never;
  throw new Error("offline in this test"); // config/principal/methods/update: fail-quiet
}) as typeof api.get;

// ---------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg} (expected ${String(want)}, got ${String(got)})`);
}
/** Let awaited fetches and their continuations run. */
async function settle(): Promise<void> {
  for (let i = 0; i < 10; i++) await Promise.resolve();
  await new Promise((r) => setImmediateLike(r));
}
// Node's own macrotask, reached through globalThis so `tsc -b` (browser lib, no
// Node types) still compiles this file.
const setImmediateLike = (globalThis as unknown as {
  setImmediate: (fn: (v?: unknown) => void) => void;
}).setImmediate;
const sock = () => FakeWebSocket.all[FakeWebSocket.all.length - 1];
const frame = (obj: unknown) => sock().onmessage?.({ data: JSON.stringify(obj) });
/** Release the oldest held snapshot read with this body, and let it apply. */
async function answer(body: Record<string, unknown>): Promise<void> {
  const resolve = held.shift();
  if (!resolve) throw new Error("precondition: no snapshot read is waiting for an answer");
  resolve({ guide_recent: [], busy: [], ...body });
  await settle();
}
/** Start a gap-driven read outside the throttle window: it is sent at once. */
function gapRead(): void {
  nowMs += GAP_RESNAPSHOT_MS + 1;
  const before = sent;
  frame({ type: "relay_gap" });
  eq(sent, before + 1, "precondition: the relay_gap sent a snapshot read");
}
const status = (marker: string) => ({ mode: "alpaca", busy: [], marker });
const seqOf = (state: string, detail: string) => ({ state, detail });
const marker = () => (useStore.getState().status as unknown as { marker?: string } | null)?.marker;

// ---------------------------------------------------------------- connect
await test("connect: a status event between send and answer beats the older snapshot", async () => {
  // U2 "snapshot applied unconditionally (status)", observed (10 passed, 1 failed):
  //   x connect: a status event between send and answer beats the older
  //   snapshot: the older snapshot's status overwrote a newer status event
  //   (expected live, after the send, got snapshot, older)
  // U4 "stamp taken at the answer" fails here too, with the same line.
  connectWs();
  const opened = sock().onopen?.();
  await settle();
  eq(sent, 1, "precondition: onopen sent the snapshot read");
  frame({ type: "status", data: status("live, after the send"), ts: 2 });
  await answer({
    status: status("snapshot, older"),
    sequence: seqOf("running", "M31: L 60s [3/40]"),
    preview_id: 535,
  });
  await opened;
  eq(marker(), "live, after the send", "the older snapshot's status overwrote a newer status event");
});

await test("control: a status event in between does not stop the snapshot's sequence (the stamp is per type)", () => {
  // U3 "one stamp for every type", observed (9 passed, 2 failed; the other is
  // the sequence case's status control below):
  //   x control: a status event in between does not stop the snapshot's
  //   sequence (the stamp is per type): a status event stopped the snapshot's
  //   sequence (expected running, got idle)
  eq(useStore.getState().sequence.state, "running", "a status event stopped the snapshot's sequence");
  eq(useStore.getState().sequence.detail, "M31: L 60s [3/40]", "the snapshot's sequence did not land");
  eq(useStore.getState().snapshotPreviewId, 535, "the snapshot's preview_id did not land");
});

await test("control: with no status event in between, the snapshot's status applies", async () => {
  // A status event WAS handled before this send (the case above), so the
  // stamp is not zero here: a rule that refused any snapshot once a status
  // event had ever been seen goes red on this case.
  gapRead();
  await answer({ status: status("snapshot, fresh"), sequence: seqOf("running", "M31: L 60s [4/40]") });
  eq(marker(), "snapshot, fresh", "a snapshot with no status event in between was not applied");
});

// ------------------------------------------------------------- sequence
await test("relay_gap: a sequence event between send and answer beats the older snapshot", async () => {
  // The run finishes while the read is in flight. `complete` is published
  // once; nothing would ever correct a `running` written over it.
  // U1 "snapshot applied unconditionally (sequence)", observed (10 passed, 1 failed):
  //   x relay_gap: a sequence event between send and answer beats the older
  //   snapshot: the older snapshot's running overwrote a newer complete
  //   (expected complete, got running)
  // U4 "stamp taken at the answer", observed (7 passed, 4 failed: every
  // "between send and answer" case, status, sequence, polar and focus, since a
  // stamp taken at the answer already counts the event that overtook it):
  //   x relay_gap: a sequence event between send and answer beats the older
  //   snapshot: the older snapshot's running overwrote a newer complete
  //   (expected complete, got running)
  // U3 "one stamp for every type", observed on this case's control:
  //   x relay_gap: a sequence event between send and answer beats the older
  //   snapshot: control: no status event was in between, so the snapshot's
  //   status must still apply (expected snapshot, beside the old sequence,
  //   got snapshot, fresh)
  gapRead();
  frame({ type: "sequence", data: seqOf("complete", "M31: done"), ts: 3 });
  await answer({ status: status("snapshot, beside the old sequence"), sequence: seqOf("running", "M31: L 60s [39/40]") });
  eq(useStore.getState().sequence.state, "complete", "the older snapshot's running overwrote a newer complete");
  eq(marker(), "snapshot, beside the old sequence",
    "control: no status event was in between, so the snapshot's status must still apply");
});

await test("control: with no sequence event in between, the snapshot's sequence applies", async () => {
  gapRead();
  await answer({ status: status("snapshot, next"), sequence: seqOf("aborted", "M31: stopped by user") });
  eq(useStore.getState().sequence.state, "aborted", "a snapshot with no sequence event in between was not applied");
});

await test("a sequence event BEFORE the send does not stop the snapshot", async () => {
  // The stamp is taken when the request goes out. An event handled before it
  // is older than the answer, which applies.
  frame({ type: "sequence", data: seqOf("running", "M33: L 60s [1/40]"), ts: 4 });
  gapRead();
  await answer({ sequence: seqOf("running", "M33: L 60s [2/40]") });
  eq(useStore.getState().sequence.detail, "M33: L 60s [2/40]", "an event from before the send blocked the answer");
});

// ------------------------------------------------------------- preview_id
await test("preview_id keeps its replace semantics, even past a live preview", async () => {
  // The tile takes the newer of the live preview and this field
  // (lib/lastFrameId.ts), so the field only has to be the server's answer.
  // A restarted server counts from 1 again, so the answer replaces.
  // U5 "preview_id stamped too", observed (10 passed, 1 failed):
  //   x preview_id keeps its replace semantics, even past a live preview: the
  //   snapshot's preview_id did not replace the field (expected 2, got null)
  // W7 "ws.ts max-merges" (liveLastFrameReconnect.test.tsx names it), observed
  // here (10 passed, 1 failed):
  //   x preview_id keeps its replace semantics, even past a live preview: the
  //   snapshot's preview_id did not replace the field (expected 2, got 535)
  gapRead();
  frame({ type: "preview", data: { id: 3 }, ts: 5 });
  await answer({ preview_id: 2 });
  eq(useStore.getState().snapshotPreviewId, 2, "the snapshot's preview_id did not replace the field");
  eq(useStore.getState().preview?.id, 3, "precondition: the live preview landed");
});

// ------------------------------------------------------ polar and focus
await test("polar: an aligner event between send and answer beats the older snapshot", async () => {
  // U6 "snapshot applied unconditionally (polar)", observed (10 passed, 1 failed):
  //   x polar: an aligner event between send and answer beats the older
  //   snapshot: the older snapshot's polar overwrote a newer aligner event
  //   (expected error, got measuring)
  gapRead();
  frame({ type: "polar", data: { state: "error", message: "too close to the pole" }, ts: 6 });
  await answer({ polar: { state: "measuring", message: "" } });
  eq(useStore.getState().polar.state, "error", "the older snapshot's polar overwrote a newer aligner event");
});

await test("control: with no polar event in between, the snapshot's polar applies", async () => {
  gapRead();
  await answer({ polar: { state: "idle", message: "" } });
  eq(useStore.getState().polar.state, "idle", "a snapshot with no polar event in between was not applied");
});

await test("focus: a sweep that started after the send is not cleared by the older busy list", async () => {
  // U7 "focus cleared unconditionally", observed (10 passed, 1 failed):
  //   x focus: a sweep that started after the send is not cleared by the older
  //   busy list: a sweep newer than the snapshot was cleared by it (expected
  //   running, got undefined)
  gapRead();
  frame({ type: "focus", data: { state: "running", step: 1 }, ts: 7 });
  await answer({ busy: [] });
  eq(useStore.getState().focus?.state, "running", "a sweep newer than the snapshot was cleared by it");
});

await test("control: with no focus event in between, a running focus the server does not corroborate is cleared", async () => {
  gapRead();
  await answer({ busy: [] });
  eq(useStore.getState().focus, null, "a stale running focus survived a snapshot that says no autofocus is running");
});

// ------------------------------------------------------------------ report
for (const f of failures) console.log(f);
console.log(`wsSnapshotStamp: ${passed} passed, ${failed} failed`);
export const result = { passed, failed, total: passed + failed };
// connectWs() leaves a reconnect path and held promises; exit rather than let
// anything keep node alive.
(globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(failed ? 1 : 0);
