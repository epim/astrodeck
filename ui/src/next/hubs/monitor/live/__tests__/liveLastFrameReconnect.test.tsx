// liveLastFrameReconnect.test.tsx - MONITOR - LIVE's LAST FRAME tile follows
// the rig across a dropped socket (#399).
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/next/hubs/monitor/live/__tests__/liveLastFrameReconnect.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// 2026-09-27, NGC 7331 over the relay: the phone read NO FRAME YET at 50/105
// frames while the rig served frame 535. This screen read the snapshot's
// `preview_id` once, on mount, into local state, and a reconnect never read it
// again. Now ws.ts reads the snapshot on every connect and reconnect into the
// store's `snapshotPreviewId`, and the tile shows the newer of that and the
// live preview.
//
// The REAL ws.ts drives it: a fake WebSocket whose handlers the test calls,
// and a fetch stub standing in for the rig. The drop is a real onclose and the
// reconnect is ws.ts's own retry timer, not a test calling connectWs again.
//
// Every assertion runs after the tile has been found, so an absence cannot
// pass over a blank page.
//
// NAMED MUTANTS. Each was run in a private scratch copy of ui/ (never the
// shared tree); the observed failure is quoted at the test it turns red.
//   W1 "reconnect ignores preview_id"  ws.ts: rehydrateFromSnapshot never calls
//                                      setSnapshotPreviewId.
//   L1 "live id first"                 LiveScreen: previewId={preview?.id ?? snapshotPreviewId}
//   L2 "older live numbers"            LiveScreen: hfr={preview?.hfr} unconditionally
//   L3 "mount read lowers the field"   LiveScreen: the mount read writes
//                                      snap.preview_id as it came, not the newer.
//   W7 "ws.ts max-merges"              ws.ts: rehydrateFromSnapshot writes
//                                      newestPreviewId(store's id, snapshot's id),
//                                      so a restarted server's lower id is lost (#437).

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
  url: string;
  onopen: (() => void | Promise<void>) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(url: string) { this.url = url; FakeWebSocket.all.push(this); }
  close(): void { /* the test drives onclose itself */ }
  addEventListener(): void {}
  send(): void {}
}
win.WebSocket = FakeWebSocket;

// The rig. `rigNewest` is the newest frame the snapshot names. Every other
// cold read fails, which is a supported path for every caller here.
let rigNewest: number | null = 535;
let snapshotReads = 0;
const ok = (data: any) => ({ ok: true, status: 200, statusText: "OK", json: async () => data });
win.fetch = async (url: any) => {
  const u = String(url);
  if (u.includes("/api/monitor/snapshot")) {
    snapshotReads++;
    return ok({ preview_id: rigNewest });
  }
  if (u.includes("/api/logs")) return ok([]);
  throw new Error("offline in this test");
};

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "location", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "KeyboardEvent", "localStorage", "getComputedStyle",
  "matchMedia", "WebSocket", "ResizeObserver", "requestAnimationFrame",
  "cancelAnimationFrame", "fetch",
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
const sock = () => FakeWebSocket.all[FakeWebSocket.all.length - 1];
async function open(): Promise<void> {
  await act(async () => { await sock().onopen?.(); });
  await settle();
}
/** Wait (real time: ws.ts's first retry is 1 s) for the retry onclose booked
 *  to open the next socket. */
async function awaitRetry(n: number): Promise<void> {
  const deadline = Date.now() + 5000;
  // Inside act: the screen's 1 s coarse tick fires while we wait.
  await act(async () => {
    while (FakeWebSocket.all.length <= n) {
      if (Date.now() > deadline) throw new Error("ws.ts never retried after the drop");
      await new Promise((r) => setTimeout(r, 50));
    }
  });
}

const OPERATOR = {
  role: "operator", email: "op@rig",
  caps: ["view.status", "view.preview", "control.capture", "control.guide", "control.mount"],
};
function running(framesDone: number): any {
  return {
    state: "running", plan_name: "NGC 7331", target: "NGC 7331", target_index: 0,
    detail: "NGC 7331: R 60s [8/15]",
    progress: {
      frames_done: framesDone, frames_total: 105, percent: 48, elapsed_s: 3000, rejected: 0,
      current_exposure_s: 60,
    },
  };
}

// ------------------------------------------------------------------ the page
await set({
  principal: OPERATOR,
  authGate: "open",
  equipConnected: true,
  sequence: running(50),
  preview: null,
  snapshotPreviewId: null,
});

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
act(() => { root.render(createElement(LiveScreen)); });
await settle();

const tile = (): any => container.querySelector('[data-testid="monitor-lastframe"]');
const tileImgIds = (): number[] =>
  [...(tile()?.querySelectorAll("img") || [])]
    .map((i: any) => /\/api\/preview\/(\d+)/.exec(i.getAttribute("src") || "")?.[1])
    .filter(Boolean).map(Number);
const tileText = () => (tile()?.textContent || "") as string;

await test("precondition: MONITOR - LIVE is mounted with its LAST FRAME tile", () => {
  assert(container.querySelector('[data-testid="monitor-live"]') != null, "LIVE did not render");
  assert(tile() != null, "no LAST FRAME tile");
});

await test("on connect, with no live preview, the tile shows the snapshot's frame", async () => {
  connectWs();
  await open();
  eq(useStore.getState().snapshotPreviewId, 535, "precondition: the connect read recorded the rig's frame");
  assert(!/no frame yet/i.test(tileText()), `the tile says NO FRAME YET: ${tileText().slice(0, 80)}`);
  assert(tileImgIds().includes(535), `the tile does not show frame 535: ${tileImgIds()}`);
});

await test("drop the socket, reconnect, and the tile shows the snapshot's newer frame", async () => {
  // The frames between went out as `preview` events while the socket was
  // down, so only the reconnect's snapshot read can know about them.
  // W1 "reconnect ignores preview_id", observed (4 passed, 2 failed; the next
  // case fails with it, 539 then beating a stale 535):
  //   x drop the socket, reconnect, and the tile shows the snapshot's newer
  //   frame: the tile stayed on the frame before the drop: 535
  const before = snapshotReads;
  const n = FakeWebSocket.all.length;
  rigNewest = 540;
  act(() => { sock().onclose?.(); });
  await awaitRetry(n);
  await open();
  assert(snapshotReads > before, "precondition: the reconnect read the snapshot");
  assert(tileImgIds().includes(540), `the tile stayed on the frame before the drop: ${tileImgIds()}`);
  assert(!tileImgIds().includes(535), `the tile still shows frame 535: ${tileImgIds()}`);
});

await test("an older live event does not pull the tile back, nor lend it its numbers", async () => {
  // A live event that was in flight when the socket dropped can land after
  // the reconnect's read.
  // L1 "live id first", observed (4 passed, 2 failed):
  //   x an older live event does not pull the tile back, nor lend it its
  //   numbers: an older live preview replaced the newest frame: 539
  // L2 "older live numbers", observed (5 passed, 1 failed):
  //   x an older live event does not pull the tile back, nor lend it its
  //   numbers: frame 539's HFR is shown beside frame 540
  await set({ preview: { id: 539, hfr: 9.87, stars: 12, exposure_s: 60, gain: 100, binning: 1,
    data_is_linear: false, stats: { median: 0, max: 0 } } });
  assert(tileImgIds().includes(540), `an older live preview replaced the newest frame: ${tileImgIds()}`);
  assert(!/9\.87/.test(tileText()), "frame 539's HFR is shown beside frame 540");
});

await test("control: a newer live event wins, and its numbers are shown", async () => {
  await set({ preview: { id: 542, hfr: 2.61, stars: 180, exposure_s: 60, gain: 100, binning: 1,
    data_is_linear: false, stats: { median: 0, max: 0 } } });
  assert(tileImgIds().includes(542), `the newer live frame is not shown: ${tileImgIds()}`);
  assert(/2\.61/.test(tileText()), `the live frame's HFR is missing: ${tileText().slice(0, 120)}`);
});

await test("a page opened later cannot pull the tile back with an older mount read", async () => {
  // ws.ts's reconnect read landed 550; this screen's own mount read races it
  // and answers 548. The mount read may only raise the field.
  // L3 "mount read lowers the field", observed (5 passed, 1 failed):
  //   x a page opened later cannot pull the tile back with an older mount
  //   read: an older mount read lowered the store's frame id (expected 550, got 548)
  act(() => { root.unmount(); });
  await set({ snapshotPreviewId: 550 });
  rigNewest = 548;
  const again = createRoot(container);
  act(() => { again.render(createElement(LiveScreen)); });
  await settle();
  assert(tile() != null, "precondition: the remounted tile is on the page");
  eq(useStore.getState().snapshotPreviewId, 550, "an older mount read lowered the store's frame id");
  assert(tileImgIds().includes(550), `the tile went back to an older frame: ${tileImgIds()}`);
  act(() => { again.unmount(); });
});

await test("a server restart: the tile follows the new process's frames through ws.ts's replace (#437)", async () => {
  // `hub.preview_seq` starts at 0 on every server start, and a restart always
  // drops the socket, so the reconnect read is the first to hear the new
  // count. It must replace: a maximum would keep 535 until the new process
  // had saved 535 frames, which on the classic Monitor was the rest of the
  // night. The live frame arrives as a real socket event.
  // W7 "ws.ts max-merges" (rehydrateFromSnapshot writes
  // newestPreviewId(store, snapshot)), observed (6 passed, 1 failed; it also
  // turns wsReconnectPreview.test.ts's restart control and wsSnapshotStamp's
  // preview case red):
  //   x a server restart: the tile follows the new process's frames through
  //   ws.ts's replace (#437): the new process's live frame 3 is not shown: 535
  // A fresh page: nothing held from the cases above, so the seed below is 535
  // under any merge rule and only the restart can tell them apart.
  await set({ preview: null, snapshotPreviewId: null });
  rigNewest = 535;
  let n = FakeWebSocket.all.length;
  act(() => { sock().onclose?.(); });
  await awaitRetry(n);
  await open();
  eq(useStore.getState().snapshotPreviewId, 535, "precondition: the reconnect seeded 535");
  const page = createRoot(container);
  act(() => { page.render(createElement(LiveScreen)); });
  await settle();
  try {
    assert(tile() != null, "precondition: the tile is on the page");
    assert(tileImgIds().includes(535), `precondition: the tile shows frame 535: ${tileImgIds()}`);
    // The restart: the socket drops, and the new process has saved frame 2.
    rigNewest = 2;
    n = FakeWebSocket.all.length;
    act(() => { sock().onclose?.(); });
    await awaitRetry(n);
    await open();
    act(() => {
      sock().onmessage?.({ data: JSON.stringify({ type: "preview", ts: 1, data: {
        id: 3, hfr: 2.44, stars: 160, exposure_s: 60, gain: 100, binning: 1,
        data_is_linear: false, stats: { median: 0, max: 0 } } }) });
    });
    await settle();
    assert(tileImgIds().includes(3), `the new process's live frame 3 is not shown: ${tileImgIds()}`);
    assert(!tileImgIds().includes(535), `the pre-restart frame 535 is still on the tile: ${tileImgIds()}`);
    eq(useStore.getState().snapshotPreviewId, 2, "the reconnect's snapshot did not replace the frame id");
  } finally {
    act(() => { page.unmount(); });
  }
});

// ------------------------------------------------------------------ report
for (const f of failures) console.log(f);
console.log(`liveLastFrameReconnect: ${passed} passed, ${failed} failed`);
export default { passed, failed, total: passed + failed };
// ws.ts left a stale ticker and a prove timer on jsdom's window.
(globalThis as any).process?.exit(failed ? 1 : 0);
