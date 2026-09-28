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
// NAMED MUTANTS. Each was run in a private scratch copy of ui/ (never the
// shared tree); the observed failure is quoted at the test it turns red.
//   M1 "mount-only read"   MonitorView.tsx: the snapshot effect's deps are []
//   M2 "live id first"     MonitorView.tsx: previewId={preview?.id ?? coldPreviewId}
//   M3 "older live numbers" MonitorView.tsx: hfr={preview?.hfr} unconditionally
//   M4 "newest is the last read" lastFrameId.ts: newestPreviewId returns b ?? a

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

// The snapshot is the one route under test; every other cold read fails, which
// is a supported path. `snapshotId` is what the rig reports as its newest frame.
let snapshotId: number | null = 535;
let snapshotReads = 0;
win.fetch = (url: string) => {
  if (String(url).includes("/api/monitor/snapshot")) {
    snapshotReads++;
    return Promise.resolve({
      ok: true,
      json: () => Promise.resolve({ preview_id: snapshotId }),
    });
  }
  return Promise.reject(new Error("offline in this test"));
};

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "KeyboardEvent", "localStorage", "getComputedStyle",
  "matchMedia", "WebSocket", "requestAnimationFrame", "cancelAnimationFrame",
  "fetch",
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
  // control below fails with it: "the newer live frame is not shown: 541"):
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
  // M1 "mount-only read", observed (5 passed, 3 failed):
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
  // M2 "live id first", observed (7 passed, 1 failed; M1 fails here too):
  //   x an older live event does not pull the tile back, nor lend it its numbers: an older live preview replaced the newest frame: 539
  // M3 "older live numbers", observed:
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

// ------------------------------------------------------------------ report
for (const f of failures) console.log(f);
console.log(`monitorLastFrameDom: ${passed} passed, ${failed} failed`);
export default { passed, failed, total: passed + failed };
if (failed) process.exitCode = 1;
