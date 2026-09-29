// wsReconnectPreview.test.ts - the socket layer keeps the LAST FRAME honest
// across a reconnect and across a relay drop (#399).
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/__tests__/wsReconnectPreview.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// 2026-09-27, NGC 7331 over the relay: the phone read NO FRAME YET at 50/105
// frames while the rig served frame 535. Two holes, both in this layer:
//   1. a reconnect re-read /api/monitor/snapshot for status and sequence and
//      threw its `preview_id` away, so a `preview` event missed while the
//      socket was down was missed until the next exposure; and
//   2. the relay's overflow policy dropped events without telling anyone. Its
//      docstring promised the browser would "see a gap and re-snapshot", but
//      the seq it meant never reached the browser and nothing here looked.
// The relay now sends `{"type":"relay_gap"}` ahead of the next event after a
// drop (relay/relay/proxy.py), and ws.ts answers it with a snapshot read.
//
// This drives the REAL ws.ts against the REAL store: a fake WebSocket whose
// handlers the test calls, a stubbed api.get that counts snapshot reads, and a
// hand-cranked window.setTimeout so the throttle's trailing read fires when
// the test says so and not a millisecond of wall clock before.
//
// NAMED MUTANTS. Each was run in a private scratch copy of ui/ (never the
// shared tree); the observed failure is quoted at the test it turns red.
//   W1 "reconnect ignores preview_id"  ws.ts: rehydrateFromSnapshot never calls
//                                      setSnapshotPreviewId.
//   W2 "ws ignores relay_gap"          ws.ts: onmessage has no relay_gap branch,
//                                      so the frame goes to handleEvent.
//   W3 "gap reads unthrottled"         ws.ts: resnapshotAfterGap always reads now.
//   W4 "throttle swallows the gap"     ws.ts: inside the window a gap is dropped
//                                      instead of booking one trailing read.
//   W5 "drop keeps the trailing read"  ws.ts: onclose no longer cancels it.
//   W6 "credential reconnect keeps the trailing read"
//                                      ws.ts: reconnectWs no longer cancels it.

// ------------------------------------------------------------ browser stubs
class MemStorage {
  private m = new Map<string, string>();
  getItem(k: string): string | null { return this.m.has(k) ? (this.m.get(k) as string) : null; }
  setItem(k: string, v: string): void { this.m.set(k, String(v)); }
  removeItem(k: string): void { this.m.delete(k); }
  clear(): void { this.m.clear(); }
}

// Hand-cranked timers. ws.ts books its reconnect, its 5 s prove timer and the
// gap throttle's trailing read through window.setTimeout; every one lands here
// and fires only from `fire()`.
interface Booked { id: number; fn: () => void; ms: number }
let booked: Booked[] = [];
let nextTimerId = 1;
const fakeSetTimeout = (fn: () => void, ms?: number): number => {
  const id = nextTimerId++;
  booked.push({ id, fn, ms: ms ?? 0 });
  return id;
};
const fakeClearTimeout = (id?: number): void => {
  booked = booked.filter((b) => b.id !== id);
};
/** Fire (and forget) every booked timer whose delay matches. */
function fire(pred: (b: Booked) => boolean): number {
  const due = booked.filter(pred);
  booked = booked.filter((b) => !pred(b));
  for (const b of due) b.fn();
  return due.length;
}

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
g.window = {
  location: FAKE_LOCATION,
  setTimeout: fakeSetTimeout,
  clearTimeout: fakeClearTimeout,
  // The stale ticker is a 1 s interval; nothing here needs it to tick.
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

// Wall clock, stepped by hand so the throttle window is exact.
let nowMs = 1_800_000_000_000;
Date.now = () => nowMs;

const { useStore } = await import("../store");
const { api } = await import("../api");
const { connectWs, reconnectWs, GAP_RESNAPSHOT_MS } = await import("../ws");

// The rig's side: `rigNewest` is what the snapshot names as its newest frame.
let rigNewest: number | null = 535;
let snapshotReads = 0;
(api as unknown as { get: typeof api.get }).get = (async (path: string) => {
  if (path === "/api/monitor/snapshot") {
    snapshotReads++;
    return { preview_id: rigNewest, guide_recent: [] } as never;
  }
  if (path === "/api/logs") return [] as never;
  throw new Error("offline in this test"); // config/principal/methods/update: fail-quiet
}) as typeof api.get;

// Every event handleEvent is handed, so "the gap frame is not an event" can be
// stated positively rather than inferred from nothing blowing up.
const handled: string[] = [];
const realHandle = useStore.getState().handleEvent;
useStore.setState({
  handleEvent: ((ev: { type: string }) => {
    handled.push(ev.type);
    realHandle(ev as never);
  }) as never,
});

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
function assert(c: boolean, msg: string): void { if (!c) throw new Error(msg); }
/** Let awaited fetches and their continuations run. */
async function settle(): Promise<void> {
  for (let i = 0; i < 10; i++) await Promise.resolve();
  await new Promise((r) => setImmediateLike(r));
}
// Node's own macrotask, reached through globalThis so `tsc -b` (browser lib, no
// Node types) still compiles this file. window.setTimeout is the fake above.
const setImmediateLike = (globalThis as unknown as {
  setImmediate: (fn: (v?: unknown) => void) => void;
}).setImmediate;
const sock = () => FakeWebSocket.all[FakeWebSocket.all.length - 1];
/** Drop the current socket and fire the retry its onclose booked (the only
 *  timer onclose books), so the next socket is the reconnect. */
function dropAndRetry(): void {
  const mark = nextTimerId;
  const n = FakeWebSocket.all.length;
  sock().onclose?.();
  eq(fire((b) => b.id >= mark), 1, "precondition: onclose booked exactly one retry");
  eq(FakeWebSocket.all.length, n + 1, "precondition: the retry opened a new socket");
}
const frame = (obj: unknown) => sock().onmessage?.({ data: JSON.stringify(obj) });

// ---------------------------------------------------------------- connect
await test("connect: the snapshot's preview_id lands in the store", async () => {
  // W1 "reconnect ignores preview_id", observed (4 passed, 4 failed):
  //   x connect: the snapshot's preview_id lands in the store: the rig's newest
  //   frame was not recorded (expected 535, got null)
  eq(useStore.getState().snapshotPreviewId, null, "precondition: a cold store names no frame");
  connectWs();
  await sock().onopen?.();
  await settle();
  eq(snapshotReads, 1, "precondition: onopen read the snapshot once");
  eq(useStore.getState().snapshotPreviewId, 535, "the rig's newest frame was not recorded");
});

await test("reconnect: a frame missed while the socket was down is restored", async () => {
  // The live event for 536..540 went nowhere: the socket was down. Only the
  // snapshot read on the way back up can know about them.
  // W1 "reconnect ignores preview_id", observed:
  //   x reconnect: a frame missed while the socket was down is restored: the
  //   reconnect left the tile on the frame before the drop (expected 540, got null)
  rigNewest = 540;
  dropAndRetry();
  await sock().onopen?.();
  await settle();
  eq(useStore.getState().snapshotPreviewId, 540, "the reconnect left the tile on the frame before the drop");
});

await test("control: a snapshot with no frame records none (a restarted server counts from 1)", async () => {
  rigNewest = null;
  dropAndRetry();
  await sock().onopen?.();
  await settle();
  eq(useStore.getState().snapshotPreviewId, null, "an id from before the restart survived the server's answer");
  rigNewest = 541;
});

// -------------------------------------------------------------- relay gap
await test("relay_gap: the snapshot is re-read at once, and the notice is not an event", async () => {
  // W2 "ws ignores relay_gap", observed (4 passed, 4 failed):
  //   x relay_gap: the snapshot is re-read at once, and the notice is not an
  //   event: a relay_gap frame did not start a snapshot read (expected 4, got 3)
  const before = snapshotReads;
  handled.length = 0;
  frame({ type: "relay_gap" });
  // Synchronously, before any await: the read starts ahead of whatever the
  // relay delivers next, which is the order the relay sends them in.
  eq(snapshotReads, before + 1, "a relay_gap frame did not start a snapshot read");
  await settle();
  eq(useStore.getState().snapshotPreviewId, 541, "the re-read did not record the rig's newest frame");
  assert(!handled.includes("relay_gap"), `the transport notice reached handleEvent: ${handled.join(",")}`);
});

await test("control: an ordinary event still reaches handleEvent and reads nothing", async () => {
  const before = snapshotReads;
  handled.length = 0;
  frame({ type: "log", data: { level: "info", message: "R 60s captured", source: "sequence" }, ts: 1 });
  await settle();
  eq(snapshotReads, before, "an ordinary event read the snapshot");
  eq(handled.join(","), "log", "the event did not reach handleEvent");
});

await test("relay_gap inside the window books ONE trailing read instead of one per notice", async () => {
  // W3 "gap reads unthrottled", observed (6 passed, 2 failed):
  //   x relay_gap inside the window books ONE trailing read instead of one per
  //   notice: a gap inside the throttle window read the snapshot again at once
  //   (expected 4, got 7)
  // W4 "throttle swallows the gap", observed (6 passed, 2 failed):
  //   x relay_gap inside the window books ONE trailing read instead of one per
  //   notice: three notices inside the window must book exactly one trailing
  //   read (expected 1, got 0)
  nowMs += 1000; // 1 s after the read above: inside the window
  const before = snapshotReads;
  rigNewest = 542;
  frame({ type: "relay_gap" });
  frame({ type: "relay_gap" });
  frame({ type: "relay_gap" });
  await settle();
  eq(snapshotReads, before, "a gap inside the throttle window read the snapshot again at once");
  const trailing = booked.filter((b) => b.ms === GAP_RESNAPSHOT_MS - 1000);
  eq(trailing.length, 1, "three notices inside the window must book exactly one trailing read");
  nowMs += GAP_RESNAPSHOT_MS - 1000;
  fire((b) => b.ms === GAP_RESNAPSHOT_MS - 1000);
  await settle();
  eq(snapshotReads, before + 1, "the trailing read never happened");
  eq(useStore.getState().snapshotPreviewId, 542, "the trailing read did not record the newest frame");
});

await test("control: a gap after the window reads at once again", async () => {
  nowMs += GAP_RESNAPSHOT_MS + 1;
  const before = snapshotReads;
  frame({ type: "relay_gap" });
  eq(snapshotReads, before + 1, "a gap outside the window was throttled");
  await settle();
});

await test("a socket drop cancels a booked trailing read (the reconnect reads anyway)", async () => {
  // W5 "drop keeps the trailing read", observed (7 passed, 1 failed):
  //   x a socket drop cancels a booked trailing read (the reconnect reads
  //   anyway): the trailing read outlived its socket
  nowMs += 1000;
  frame({ type: "relay_gap" });
  assert(booked.some((b) => b.ms === GAP_RESNAPSHOT_MS - 1000), "precondition: a trailing read is booked");
  sock().onclose?.();
  assert(!booked.some((b) => b.ms === GAP_RESNAPSHOT_MS - 1000), "the trailing read outlived its socket");
});

await test("a credential reconnect cancels a booked trailing read too", async () => {
  // reconnectWs (sign-in, sign-out, a sign-in method flipped) detaches the old
  // socket's handlers before closing it, so onclose never runs and cannot do
  // the cancelling: reconnectWs has to. A read left booked would fire into the
  // next session, after a sign-out, against a server that now answers 401.
  // W6 "credential reconnect keeps the trailing read" (cancelGapResnapshot
  // removed from reconnectWs), observed (8 passed, 1 failed):
  //   x a credential reconnect cancels a booked trailing read too: the trailing
  //   read outlived the credential reconnect
  reconnectWs(); // a socket up again; this also clears the retry booked above
  await sock().onopen?.();
  await settle();
  // Still 1 s after the last immediate read (the drop above did not move the
  // clock), so this gap books a trailing read rather than reading at once.
  const before = snapshotReads;
  frame({ type: "relay_gap" });
  eq(snapshotReads, before, "precondition: the gap was inside the throttle window");
  assert(booked.some((b) => b.ms === GAP_RESNAPSHOT_MS - 1000), "precondition: a trailing read is booked");
  const n = FakeWebSocket.all.length;
  reconnectWs();
  eq(FakeWebSocket.all.length, n + 1, "precondition: reconnectWs opened a new socket");
  assert(!booked.some((b) => b.ms === GAP_RESNAPSHOT_MS - 1000), "the trailing read outlived the credential reconnect");
});

// ------------------------------------------------------------------ report
for (const f of failures) console.log(f);
console.log(`wsReconnectPreview: ${passed} passed, ${failed} failed`);
export const result = { passed, failed, total: passed + failed };
// connectWs() leaves booked timers and a reconnect pending; exit rather than
// let a stray real timer keep node alive.
(globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(failed ? 1 : 0);
