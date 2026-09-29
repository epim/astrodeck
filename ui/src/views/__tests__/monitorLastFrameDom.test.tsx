// monitorLastFrameDom.test.tsx - the classic Monitor's LAST FRAME tile follows
// the rig's newest frame, not only the live event that happened to arrive (#399).
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/views/__tests__/monitorLastFrameDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// 2026-09-27, NGC 7331 over the relay: the phone showed NO FRAME YET at
// 50/105 frames with "last frame 41s ago" beside it. The rig had the frame
// (snapshot preview_id 535, the JPEG answered 200). The page had neither id:
// the Monitor read the snapshot ONCE, on mount, and a reconnect restored status
// and sequence but never the preview, so one missed `preview` event left the
// tile empty for as long as nothing new arrived.
//
// Every assertion runs after the Last frame panel has been found, so an
// absence cannot pass over a blank page.
//
// AND ACROSS A SERVER RESTART (#437). The tile used to keep the snapshot's id
// in its own state as a running MAXIMUM over every read. `hub.preview_seq`
// starts at 0 on every server start, so after a mid-run deploy the new
// process's snapshot said 2, the maximum kept 535, and the tile stayed on the
// pre-restart frame for the rest of the night. The tile now reads the store's
// `snapshotPreviewId`, which every read replaces, as MONITOR - LIVE does.
//
// AND THE FIRST READ'S SEED OBEYS THE SNAPSHOT STAMP (#476, S7 orchestrator
// ruling 3). The Monitor's first read seeds status and sequence; a live event
// handled while that read was in flight is newer than its answer.
//
// NAMED MUTANTS. Each was run in a private scratch copy of ui/ (never the
// shared tree); the observed failure is quoted at the test it turns red.
//   M1 "mount-only read"   MonitorView.tsx: the snapshot effect's deps are []
//   M2 "live id first"     MonitorView.tsx: previewId={preview?.id ?? snapshotPreviewId}
//   M3 "older live numbers" MonitorView.tsx: hfr={preview?.hfr} unconditionally
//   M4 "newest is the last read" lastFrameId.ts: newestPreviewId returns b ?? a
//   M5 "max-merge"         MonitorView.tsx: the pre-#437 code, the id kept in
//                          local state as newestPreviewId(prev, id) over reads
//   M5s "max-merge in the store" MonitorView.tsx: the read writes
//                          newestPreviewId(store's id, snapshot's id)
//   M6 "seed applied unconditionally" MonitorView.tsx: the first read's
//                          sequence ignores the snapshot stamp

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
// Recorded, so the seed case below can hand ws.ts a live event through the
// socket it opened.
class FakeWebSocket {
  static all: FakeWebSocket[] = [];
  onopen: (() => void | Promise<void>) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor() { FakeWebSocket.all.push(this); }
  close() {} addEventListener() {} send() {}
}
win.WebSocket = FakeWebSocket;

// The snapshot is the one route under test; every other cold read fails, which
// is a supported path. `snapshotId` is what the rig reports as its newest frame.
// `snapshotExtra` adds the rest of the answer (sequence, status). With `hold`
// set, each read waits in `heldReads` until the test releases it, so the
// ordering "an event between send and answer" is forced, not hoped for.
let snapshotId: number | null = 535;
let snapshotExtra: Record<string, unknown> = {};
let snapshotReads = 0;
let hold = false;
const heldReads: Array<() => void> = [];
win.fetch = (url: string) => {
  if (String(url).includes("/api/monitor/snapshot")) {
    snapshotReads++;
    const reply = () => ({
      ok: true,
      json: () => Promise.resolve({ preview_id: snapshotId, ...snapshotExtra }),
    });
    if (hold) return new Promise((resolve) => { heldReads.push(() => resolve(reply())); });
    return Promise.resolve(reply());
  }
  return Promise.reject(new Error("offline in this test"));
};

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "KeyboardEvent", "localStorage", "getComputedStyle",
  "matchMedia", "WebSocket", "requestAnimationFrame", "cancelAnimationFrame",
  // ws.ts reads the BARE `location` when the seed case below connects.
  "fetch", "location",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../store");
const MonitorView = (await import("../MonitorView")).default;
const { newestPreviewId, showingLivePreview } = await import("../../lib/lastFrameId");

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
  if (got !== want) throw new Error(`${msg}\n  expected ${String(want)}\n  got      ${String(got)}`);
}

async function settle(): Promise<void> {
  await act(async () => { for (let i = 0; i < 5; i++) await new Promise((r) => setTimeout(r, 0)); });
}
async function set(state: Record<string, unknown>): Promise<void> {
  act(() => { useStore.setState(state as never); });
  await settle();
}
function running(framesDone: number): any {
  return {
    state: "running", plan_name: "NGC 7331", target: "NGC 7331",
    detail: "NGC 7331: R 60s [8/15]",
    progress: { frames_done: framesDone, frames_total: 105, current_exposure_s: 60 },
  };
}

// --------------------------------------------------------- the pure helpers
await test("newestPreviewId takes the newer id, whichever source has it", () => {
  // M4 "newest is the last read", observed (6 passed, 2 failed; the page
  // control below fails with it: "the newer live frame is not shown: 541").
  // Re-run against the #437 code: 8 passed, 3 failed, the same two lines and
  // the restart case's "the new process's live frame 3 is not shown: 2":
  //   x newestPreviewId takes the newer id, whichever source has it: a newer live id lost to an older snapshot
  eq(newestPreviewId(540, 535), 540, "a newer live id lost to an older snapshot");
  eq(newestPreviewId(535, 540), 540, "a newer snapshot id lost to an older live event");
  eq(newestPreviewId(null, 535), 535, "the snapshot alone must name the frame");
  eq(newestPreviewId(535, null), 535, "the live event alone must name the frame");
  eq(newestPreviewId(undefined, undefined), null, "control: nothing named means no frame");
});

await test("showingLivePreview is true only when the tile shows the live event's own frame", () => {
  eq(showingLivePreview(540, 540), true, "the live frame itself");
  eq(showingLivePreview(541, 540), false, "a newer snapshot frame is not the live one");
  eq(showingLivePreview(null, 540), false, "control: no frame shown");
});

// ------------------------------------------------------------------ the page
await set({
  principal: { role: "admin", email: null, caps: ["view.status", "view.preview"] },
  wsConnected: true,
  sequence: running(50),
  preview: null,
});

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
act(() => { root.render(createElement(MonitorView)); });
await settle();

const lastFramePanel = (): any =>
  [...container.querySelectorAll("section")].find((s: any) =>
    (s.querySelector(".panel-title")?.textContent || "") === "Last frame");
const tileImgIds = (): number[] =>
  [...(lastFramePanel()?.querySelectorAll("img") || [])]
    .map((i: any) => /\/api\/preview\/(\d+)/.exec(i.getAttribute("src") || "")?.[1])
    .filter(Boolean).map(Number);
const tileText = () => (lastFramePanel()?.textContent || "") as string;

await test("precondition: the Last frame panel is mounted", () => {
  assert(lastFramePanel() != null, "no Last frame panel on the Monitor");
});

await test("with no live preview, the tile shows the snapshot's frame, not NO FRAME YET", () => {
  assert(!/no frame yet/i.test(tileText()), `the tile says NO FRAME YET: ${tileText().slice(0, 80)}`);
  assert(tileImgIds().includes(535), `the tile does not show frame 535: ${tileImgIds()}`);
});

await test("a reconnect reads the snapshot again and moves the tile to the rig's newest frame", async () => {
  // M1 "mount-only read", observed (5 passed, 3 failed; re-run against the
  // #437 code, 7 passed, 4 failed, the restart case with them):
  //   x a reconnect reads the snapshot again and moves the tile to the rig's newest frame: a reconnect did not read the snapshot
  const before = snapshotReads;
  snapshotId = 540;
  await set({ wsConnected: false });
  await set({ wsConnected: true });
  assert(snapshotReads > before, "a reconnect did not read the snapshot");
  assert(tileImgIds().includes(540), `the tile stayed behind the rig after a reconnect: ${tileImgIds()}`);
});

await test("each saved frame reads the snapshot again, so a lost preview event cannot leave the tile behind", async () => {
  // M1 "mount-only read", observed:
  //   x each saved frame reads the snapshot again, so a lost preview event cannot leave the tile behind: frames_done advanced but the tile did not: 535
  snapshotId = 541;
  await set({ sequence: running(51) });
  assert(tileImgIds().includes(541), `frames_done advanced but the tile did not: ${tileImgIds()}`);
});

await test("an older live event does not pull the tile back, nor lend it its numbers", async () => {
  // M2 "live id first", observed (7 passed, 1 failed; M1 fails here too;
  // re-run against the #437 code, 10 passed, 1 failed, the same line):
  //   x an older live event does not pull the tile back, nor lend it its numbers: an older live preview replaced the newest frame: 539
  // M3 "older live numbers", observed (re-run against the #437 code, 10
  // passed, 1 failed):
  //   x an older live event does not pull the tile back, nor lend it its numbers: frame 539's HFR is shown beside frame 541
  await set({ preview: { id: 539, hfr: 9.87, stars: 12 } });
  assert(tileImgIds().includes(541), `an older live preview replaced the newest frame: ${tileImgIds()}`);
  assert(!/9\.87/.test(tileText()), "frame 539's HFR is shown beside frame 541");
});

await test("control: a newer live event wins, and its numbers are shown", async () => {
  await set({ preview: { id: 542, hfr: 2.61, stars: 180 } });
  assert(tileImgIds().includes(542), `the newer live frame is not shown: ${tileImgIds()}`);
  assert(/2\.61/.test(tileText()), `the live frame's HFR is missing: ${tileText().slice(0, 120)}`);
});

act(() => { root.unmount(); });

// ------------------------------------------------- a server restart (#437)
// A fresh page, holding nothing from the cases above.
await set({ preview: null, snapshotPreviewId: null, sequence: running(10) });
snapshotId = 535;
const restarted = createRoot(container);
act(() => { restarted.render(createElement(MonitorView)); });
await settle();

await test("a server restart: the tile follows the new process's frames, not the old maximum", async () => {
  // M5 "max-merge", observed (10 passed, 1 failed):
  //   x a server restart: the tile follows the new process's frames, not the
  //   old maximum: the new process's live frame 3 is not shown: 535
  // M5s "max-merge in the store", observed (10 passed, 1 failed):
  //   x a server restart: the tile follows the new process's frames, not the
  //   old maximum: the new process's live frame 3 is not shown: 535
  // M1 and M4, re-run against the #437 code, fail here too (M4 shows 2).
  assert(lastFramePanel() != null, "precondition: the remounted Last frame panel is on the page");
  assert(tileImgIds().includes(535), `precondition: the tile shows the seeded frame 535: ${tileImgIds()}`);
  // A mid-run deploy restarts the server: the socket drops, `hub.preview_seq`
  // starts at 0 again, and by the reconnect the new process has saved frame 2.
  snapshotId = 2;
  await set({ wsConnected: false });
  await set({ wsConnected: true });
  await set({ preview: { id: 3, hfr: 2.44, stars: 160 } });
  assert(tileImgIds().includes(3), `the new process's live frame 3 is not shown: ${tileImgIds()}`);
  assert(!tileImgIds().includes(535), `the pre-restart frame 535 is still on the tile: ${tileImgIds()}`);
  assert(/2\.44/.test(tileText()), "frame 3's HFR is missing beside it");
  eq(useStore.getState().snapshotPreviewId, 2, "the reconnect's snapshot did not replace the store's frame id");
});

act(() => { restarted.unmount(); });

// ---------------------------------------- the first read's seed (#476)
// A socket for live events. Its onopen is never called, so ws.ts reads no
// snapshot of its own here; the only read is the Monitor's.
const { connectWs } = await import("../../ws");
connectWs();
const liveSock = FakeWebSocket.all[FakeWebSocket.all.length - 1];
const liveEvent = (obj: unknown) => act(() => { liveSock.onmessage?.({ data: JSON.stringify(obj) }); });
/** Mount a fresh Monitor in its own container, run `fn`, and unmount it even
 *  when `fn` throws: a page left mounted by a failed case would send reads of
 *  its own into the next case and fail it for a reason that is not its own. */
async function withFreshMonitor(fn: () => Promise<void>): Promise<void> {
  const box = win.document.createElement("div");
  win.document.body.appendChild(box);
  const page = createRoot(box);
  act(() => { page.render(createElement(MonitorView)); });
  try { await fn(); } finally {
    act(() => { page.unmount(); });
    box.remove();
    heldReads.length = 0;
  }
}

await test("the first read's seed does not overwrite a sequence event that overtook it", async () => {
  // The run completes while the Monitor's first read is in flight. `complete`
  // is published once, so a `running` seeded over it would never be undone.
  // frames_done stays 60 in both, so the effect is not re-run (which would
  // cancel the read and pass this case for the wrong reason).
  // M6 "seed applied unconditionally", observed (10 passed, 1 failed):
  //   x the first read's seed does not overwrite a sequence event that
  //   overtook it: the first read's older running overwrote a newer complete
  await set({ wsPhase: "up", wsConnected: true, sequence: running(60), preview: null });
  hold = true;
  snapshotExtra = { sequence: running(60) };
  const before = snapshotReads;
  await withFreshMonitor(async () => {
    await settle();
    eq(snapshotReads, before + 1, "precondition: the mount sent its read");
    liveEvent({ type: "sequence", ts: 1, data: {
      state: "complete", plan_name: "NGC 7331", target: "NGC 7331", detail: "NGC 7331: done",
      progress: { frames_done: 60, frames_total: 105, current_exposure_s: 60 },
    } });
    await settle();
    eq(useStore.getState().sequence.state, "complete", "precondition: the live event landed");
    eq(heldReads.length, 1, "precondition: the one read is still in flight");
    act(() => { heldReads.shift()?.(); });
    await settle();
    eq(useStore.getState().sequence.state, "complete", "the first read's older running overwrote a newer complete");
  });
});

await test("control: with no event in between, the first read seeds the sequence", async () => {
  await set({ sequence: running(61) });
  snapshotExtra = { sequence: { state: "aborted", detail: "NGC 7331: stopped by user",
    progress: { frames_done: 61, frames_total: 105 } } };
  await withFreshMonitor(async () => {
    await settle();
    eq(heldReads.length, 1, "precondition: the mount sent one read");
    act(() => { heldReads.shift()?.(); });
    await settle();
    eq(useStore.getState().sequence.state, "aborted", "the first read did not seed the snapshot's sequence");
  });
  hold = false;
});

// ------------------------------------------------------------------ report
for (const f of failures) console.log(f);
console.log(`monitorLastFrameDom: ${passed} passed, ${failed} failed`);
export default { passed, failed, total: passed + failed };
// connectWs() above leaves ws.ts's socket and auth reads on jsdom's window;
// exit on the file's own result rather than wait on them.
(globalThis as any).process?.exit(failed ? 1 : 0);
