// captureLastFrameDom.test.tsx — CaptureView MOUNTED mid-run: does the stage
// show the last frame the rig actually saved, instead of the logo?
//
//   Run directly:  npx tsx src/views/__tests__/captureLastFrameDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT. The stage paints from `previews`, an in-memory ring filled only
// by WebSocket preview events received during THIS browser session. Open the
// Capture tab five minutes into a run and the ring is empty, so the stage
// showed the AstroDeck logo — for up to a full sub (180 s on a normal night)
// while the rig was imaging and had 40 frames on disk.
//
// WHY MOUNTED. Every question here is about ORDER — a fetch that lands before,
// or after, the first live frame; a stand-in that must never come back once the
// real thing has arrived — and no single render tree can show that. The three
// failure modes this pins down, in the order they would actually bite:
//
//   * the stand-in is a LIE — a frame from another target, or from last week,
//     painted under a running sequence;
//   * the stand-in FIGHTS the live frame — a slow gallery fetch lands after the
//     first real preview and replaces it, which is a worse regression than the
//     bug being fixed;
//   * the stand-in is a THUMBNAIL — 256 px upscaled into a stage where focus is
//     judged. The owner ruled on this: "for the gallery - the thumbnail size I
//     specified is ok. I did not make any declaration for capture previews.
//     those need to be high fidelity."
//
// EVERY test states its PRECONDITION as its own assertion first. "The stand-in
// is absent" is true of a screen that never rendered and of a view that never
// asked, so each is preceded by proof that the state it denies was reached.
//
// EXPECT ACT WARNINGS ON STDERR. `settle()` advances a real macrotask, which is
// what lets a fetch chain finish — and it also lets CaptureView's own mount
// loaders (GuideFramePreview's among them) settle between tests, outside any
// act() window. They predate this file and say nothing about the feature; the
// tally at the bottom is the signal.
//
// WHAT THIS FILE CANNOT SEE. PreviewStage paints a stand-in only when it has no
// frame of its own, so it covers for every ordering guard inside the hook:
// strip them all out and this file stays green (measured). That contract is
// pinned in components/preview/__tests__/lastSessionFrameHook.test.tsx, which
// probes the hook's return value directly. Both are needed.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
// Installed BEFORE the store, the api client or the view are imported: BASE
// reads window.location at module scope and several components read matchMedia.
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
win.Element.prototype.setPointerCapture = function () {};
win.Element.prototype.releasePointerCapture = function () {};
win.Element.prototype.hasPointerCapture = function () { return false; };
win.HTMLElement.prototype.scrollIntoView = function () {};

// LAYOUT, FAKED — and it is load-bearing here. jsdom lays nothing out, so every
// element's clientWidth is 0, and the requested render width is precisely what
// this file exists to check. A fixed width plus a HiDPI ratio is the case that
// matters: 720 CSS px on a 2x screen is 1440 REAL pixels, so anything that
// asked for the CSS number (or worse, the gallery tile's 256) would be visibly
// soft on the screen most likely to be looking at it.
const STAGE_CSS_W = 720;
win.devicePixelRatio = 2;
Object.defineProperty(win.HTMLElement.prototype, "clientWidth", {
  configurable: true, get() { return STAGE_CSS_W; },
});

// ------------------------------------------------------------- the network seam
// Every api.* call funnels through fetch. `gets` answers "did the view ask, and
// how many times"; `framesGate` lets one test hold the gallery response open so
// a live preview can win the race against it.
const gets: string[] = [];
let framesPage: any = { frames: [], total: 0, bytes: 0, offset: 0, limit: 12, truncated: false, scan_ms: 1 };
let framesStatus = 200;
let framesGate: Promise<void> | null = null;
let openFramesGate: (() => void) | null = null;

function armFramesGate(): void {
  framesGate = new Promise<void>((resolve) => { openFramesGate = resolve; });
}

win.fetch = async (input: any, init: any = {}) => {
  const path = String(input);
  const method = init.method ?? "GET";
  if (method === "GET") gets.push(path);
  if (path.startsWith("/api/gallery/frames")) {
    if (framesGate) await framesGate;
    return {
      ok: framesStatus === 200, status: framesStatus,
      headers: { get: () => "application/json" },
      json: async () => framesPage,
      text: async () => JSON.stringify(framesPage),
    };
  }
  return {
    ok: true, status: 200,
    headers: { get: () => "application/json" },
    json: async () => ({}),
    text: async () => "{}",
  };
};

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "Image", "localStorage", "requestAnimationFrame", "cancelAnimationFrame",
  "getComputedStyle", "matchMedia", "WebSocket", "fetch", "devicePixelRatio",
  "ResizeObserver", "IntersectionObserver",
]) {
  const v = k === "window" ? win
    : k === "ResizeObserver" || k === "IntersectionObserver"
      ? class { observe() {} unobserve() {} disconnect() {} }
      : win[k];
  // Node >=21 defines `navigator` as a getter-only global, so a plain
  // assignment throws; defineProperty works for every key.
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../store");
const { VIEW_WIDTH_STEPS } = await import("../../lib/frameView");
const CaptureView = (await import("../CaptureView")).default;
type PreviewInfo = import("../../types").PreviewInfo;

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
// THE SILENT-FAILURE CONTRACT, ENFORCED BY NAME. The hook swallows a failed
// gallery request on purpose (a decorative fetch must not put an error on the
// screen someone is running a night from) and "a failed gallery request leaves
// the Capture tab working" below is what holds it to that. But the fetch lives
// in a `void async` IIFE: strip its try/catch and the rejection escapes, Node
// kills the child, this file prints no tally at all, and the runner can only
// report it as unscorable. The suite does go red — via the runner's
// cannot-be-scored rule rather than via any assertion here, which is not the
// same thing. This trap turns that into a named failure.
process.on("unhandledRejection", (e) => {
  failed++;
  failures.push(`x UNHANDLED REJECTION escaped a test: ${String(e)}`);
});

// Tonight, on the RIG's clock. The fixtures below are all expressed against it,
// and the sequence publishes it as `server_now_ms` — which is what makes the
// staleness test compare two readings of the SAME clock.
const NOW_MS = Date.now();
const NOW_S = Math.floor(NOW_MS / 1000);
const TARGET = "NGC 6946";
const FRAME_PATH = "NGC 6946/Light_NGC6946_L_180s_0042.fits";

function galleryFrame(over: Record<string, any> = {}): any {
  return {
    path: FRAME_PATH,
    name: "Light_NGC6946_L_180s_0042.fits",
    folder: TARGET,
    night: "2026-08-16",
    ts: NOW_S - 40,
    local_date: "2026-08-17",
    local_clock: "23:14",
    target: TARGET,
    filter: "L",
    frame_type: "Light",
    exposure_s: 180,
    bytes: 52_000_000,
    mtime: NOW_S - 35,
    ...over,
  };
}

function page(frames: any[]): any {
  return { frames, total: frames.length, bytes: 0, offset: 0, limit: 12, truncated: false, scan_ms: 1 };
}

/** A connected imaging camera, plus whatever the sequencer is doing. */
function seed(opts: { sequence?: any } = {}): void {
  useStore.setState({
    status: {
      connected: { camera: { connected: true, name: "sim cam" } },
      looping: false,
      mode: "sim",
      busy_lanes: [],
      camera: {
        temperature: -19.4, can_cool: true, has_dew_heater: false,
        width: 1000, height: 800, max_gain: 300, max_bin: 2,
      },
    },
    principal: {
      role: "operator", email: null,
      caps: ["view.status", "view.preview", "control.capture"],
    },
    // A BROWSER THAT JUST OPENED THE TAB: the ring is empty and no frame is
    // live. This is the precondition of the whole feature, and resetting it
    // between mounts is what stops one test's live frame passing the next.
    // THE LINK IS UP. jsdom has no WebSocket, so the store's default wsPhase is
    // "connecting" and `useLinkDown` (wsPhase !== "up") reads TRUE — which is
    // not the state under test: a `sequence` object can only have reached the
    // browser over a live socket in the first place, and the stand-in fetch is
    // gated on the link because a frozen sequence makes an hours-old frame
    // measure as seconds old. Left at the default, every mount here is a
    // link-down mount and nothing fetches.
    wsPhase: "up",
    wsConnected: true,
    previews: [],
    livePreviewId: null,
    selectedPreviewId: null,
    sequence: opts.sequence ?? { state: "idle" },
  } as never);
}

const running = (over: Record<string, any> = {}): any => ({
  state: "running",
  target: TARGET,
  plan_name: "Tonight",
  progress: {
    frames_done: 42, frames_total: 120, percent: 35, elapsed_s: 7800,
    rejected: 0, server_now_ms: NOW_MS,
  },
  ...over,
});

/** A frame off the WebSocket. `is_stretched` puts the stage on its <img> path —
 *  the linear path wants a canvas 2D context jsdom does not have, and this test
 *  is about which SOURCE wins, not about the LUT. */
const livePreview = (id: number): PreviewInfo => ({
  id,
  stats: { min: 0, max: 65535, mean: 900, median: 850, std: 120 },
  histogram: [1, 2, 3], histogram_domain: "display",
  exposure_s: 180, gain: 120, binning: 1,
  data_width: 6252, data_height: 4176,
  display_width: 1400, display_height: 935,
  mime: "image/jpeg", source: "camera" as any,
  is_stretched: true, data_is_linear: false, has_lossless: false,
  full_well: null,
  auto_levels: { black: 0, mid: 0.5, white: 1 },
  ts: NOW_S,
} as unknown as PreviewInfo);

// Each scenario gets its OWN container and root: re-rooting one node logs a
// React warning and, worse, can leave the previous tree's effects alive.
let root: ReturnType<typeof createRoot> | null = null;
let container: any = null;
function mount(): void {
  if (root) act(() => { root!.unmount(); });
  container = win.document.createElement("div");
  win.document.body.appendChild(container);
  root = createRoot(container);
  act(() => { root!.render(createElement(CaptureView)); });
}
const text = (): string => String(container?.textContent ?? "");
/** The stand-in: an <img> pointed at the high-fidelity gallery render. */
const standIn = (): any => container.querySelector('img[src*="/api/gallery/view"]');
const framesCalls = (): string[] => gets.filter((p) => p.startsWith("/api/gallery/frames"));
/** Flush the fetch + its .json() + the setState they land in. A macrotask,
 *  not two microtasks: the api client wraps the response in two awaits and the
 *  timeout signal in a setTimeout. */
async function settle(): Promise<void> {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}
function statusFrame(seq: any): void {
  act(() => { useStore.setState({ sequence: seq } as never); });
}

// ============================================================ the happy path
await test("mid-run, the stage shows the last frame the rig saved", async () => {
  gets.length = 0;
  framesPage = page([galleryFrame()]);
  seed({ sequence: running() });
  mount();
  // PRECONDITION 1: the view really rendered. Every absence assertion in this
  // file is vacuous against a blank page.
  assert(/Exposure/.test(text()), "no Exposure panel — CaptureView never rendered");
  // PRECONDITION 2: this browser genuinely has nothing to paint — which is what
  // used to put the logo on screen for a full sub.
  assert((useStore.getState() as any).previews.length === 0,
    "the ring is not empty, so the empty-stage case is not under test");
  assert(standIn() == null, "a saved frame was on screen before the fetch resolved");

  await settle();
  assert(standIn() != null,
    "the stage is still empty under a running sequence with 42 frames on disk — " +
    `the gallery was asked ${framesCalls().length} times. Text: ${text().slice(0, 200)}`);
});

await test("it is the high-fidelity render, not the gallery thumbnail", () => {
  const src = String(standIn()?.getAttribute("src") ?? "");
  // PRECONDITION: there is a src to judge.
  assert(src.length > 0, "no stand-in to inspect (precondition)");
  assert(!/\/api\/gallery\/thumb/.test(src),
    `the stage is showing the scanning-grid thumbnail: ${src}`);
  const w = Number(/[?&]w=(\d+)/.exec(src)?.[1] ?? 0);
  assert((VIEW_WIDTH_STEPS as readonly number[]).includes(w),
    `w=${w} is not a rung the server renders and caches (${VIEW_WIDTH_STEPS.join("/")})`);
  assert(w >= STAGE_CSS_W * win.devicePixelRatio,
    `asked for ${w} px to fill ${STAGE_CSS_W * win.devicePixelRatio} device px — ` +
    "the picture is being upscaled on exactly the screens most likely to be " +
    "judging focus from it");
  assert(src.includes(encodeURIComponent(FRAME_PATH)),
    `the stand-in is not the frame the listing named: ${src}`);
});

await test("the stage says the picture is not live, and when it was taken", () => {
  // PRECONDITION: the stand-in is on screen, so this is about what it SAYS.
  assert(standIn() != null, "no stand-in on screen (precondition)");
  assert(/not live/i.test(text()),
    `the stage presents a saved frame as the live view. Text: ${text().slice(0, 300)}`);
  assert(text().includes("23:14"),
    "the label does not say when the frame was taken, so a 40-second-old " +
    "stand-in reads the same as a 40-minute-old one");
});

await test("it is one fetch, not a poll", async () => {
  // PRECONDITION: it fetched at all.
  assert(framesCalls().length === 1,
    `the first mount made ${framesCalls().length} gallery requests, expected 1`);
  for (let i = 0; i < 4; i++) statusFrame(running({ progress: { frames_done: 43 + i, frames_total: 120, percent: 36, elapsed_s: 8000 + i, rejected: 0, server_now_ms: NOW_MS } }));
  await settle();
  assert(framesCalls().length === 1,
    `four status frames turned into ${framesCalls().length} library walks — each ` +
    "one reads every FITS header on a cold cache");
});

// ================================================= the live frame always wins
await test("the first live preview takes the stage back", async () => {
  // PRECONDITION: the stand-in is what is on screen right now.
  assert(standIn() != null, "no stand-in to displace (precondition)");
  act(() => {
    useStore.setState({ previews: [livePreview(7)], livePreviewId: 7 } as never);
  });
  await settle();
  assert(standIn() == null,
    "a saved frame is still on the stage after a live one arrived — the " +
    "stand-in is fighting the thing it was standing in for");
  assert(container.querySelector('img[src*="/api/preview/7"]') != null,
    "the live frame did not reach the stage either");
});

await test("and it never reverts", async () => {
  // PRECONDITION: we are in the post-live state.
  assert(standIn() == null, "the stand-in is already back (precondition)");
  statusFrame(running({ progress: { frames_done: 44, frames_total: 120, percent: 37, elapsed_s: 8200, rejected: 0, server_now_ms: NOW_MS } }));
  await settle();
  assert(standIn() == null,
    "a status frame brought the saved frame back over a live preview");
  assert(framesCalls().length === 1,
    `the gallery was walked again after the live frame arrived (${framesCalls().length} calls)`);
});

// ==================================================== the refusals: idle rig
await test("an idle rig gets the empty state and no gallery request at all", async () => {
  gets.length = 0;
  framesPage = page([galleryFrame()]);            // a perfectly good frame on disk
  seed({ sequence: { state: "idle" } });
  mount();
  await settle();
  // PRECONDITION: the very same listing DOES produce a stand-in under a run —
  // proven by the happy path above — so this is the rig state doing the work.
  assert(/Exposure/.test(text()), "CaptureView never rendered (precondition)");
  assert(standIn() == null,
    "an idle rig is showing last night's frame where the live view goes");
  assert(framesCalls().length === 0,
    `an idle rig walked the library ${framesCalls().length} times for a picture ` +
    "it must not show");
  assert(/No capture yet/i.test(text()),
    `the honest empty state is gone too. Text: ${text().slice(0, 200)}`);
});

// ============================================== the refusals: a stale frame
await test("a week-old frame is refused and the logo stands", async () => {
  gets.length = 0;
  framesPage = page([galleryFrame({ ts: NOW_S - 7 * 86400, night: "2026-08-09", local_clock: "01:12" })]);
  seed({ sequence: running() });
  mount();
  await settle();
  // PRECONDITION: the view DID ask — this is the picker refusing, not the fetch
  // never happening, and those two would look identical on screen.
  assert(framesCalls().length === 1,
    `expected one gallery request under a running sequence, got ${framesCalls().length}`);
  assert(standIn() == null,
    "a frame from last week is on the stage under a running sequence — worse " +
    "than the logo, which at least promises nothing");
  assert(/No capture yet/i.test(text()), "the empty state did not come back");
});

await test("another target's frame is refused", async () => {
  gets.length = 0;
  framesPage = page([galleryFrame({ target: "M 31", folder: "M 31", path: "M 31/x.fits" })]);
  seed({ sequence: running() });
  mount();
  await settle();
  assert(framesCalls().length === 1, "the view did not ask (precondition)");
  assert(standIn() == null, "a frame of another object was shown for this run");
});

// ================================================== the refusals: a bad reply
await test("a failed gallery request leaves the Capture tab working", async () => {
  gets.length = 0;
  framesStatus = 500;
  framesPage = { detail: "boom" };
  seed({ sequence: running() });
  mount();
  await settle();
  framesStatus = 200;
  assert(framesCalls().length === 1, "the view did not ask (precondition)");
  assert(standIn() == null, "a 500 produced an image anyway");
  assert(/Exposure/.test(text()) && /No capture yet/i.test(text()),
    `a failed stand-in broke the tab. Text: ${text().slice(0, 300)}`);
});

// ================================ the refusals: a render the server cannot make
await test("a stand-in that will not decode falls back to the logo", async () => {
  gets.length = 0;
  framesPage = page([galleryFrame()]);
  seed({ sequence: running() });
  mount();
  await settle();
  const img = standIn();
  // PRECONDITION: the stand-in got as far as the DOM. The listing succeeded and
  // the frame qualified; what fails below is the RENDER of it.
  assert(img != null, "no stand-in to break (precondition)");
  // The frame moved, or its header defeated the renderer: /api/gallery/view
  // answers 404/422 and the <img> never decodes. jsdom loads no images, so the
  // event is dispatched rather than provoked — the handler under test is the
  // component's, either way.
  act(() => { img.dispatchEvent(new win.Event("error", { bubbles: false })); });
  assert(standIn() == null,
    "the stage is still pointed at bytes that will not decode — the operator " +
    "gets the browser's broken-image glyph where the live view goes");
  assert(/No capture yet/i.test(text()),
    `the honest empty state did not come back. Text: ${text().slice(0, 200)}`);
  assert(/Exposure/.test(text()), "a broken stand-in took the Capture tab with it");
});

// ========================================================== the race, on purpose
await test("a gallery reply that lands AFTER the live frame is dropped", async () => {
  gets.length = 0;
  framesPage = page([galleryFrame()]);
  armFramesGate();
  seed({ sequence: running() });
  mount();
  await settle();
  // PRECONDITION: the request is genuinely in flight and unanswered.
  assert(framesCalls().length === 1, "the view did not ask (precondition)");
  assert(standIn() == null, "the gate did not hold the reply");

  // The first real preview arrives while the library walk is still running —
  // the ordinary case on a cold cache, where the walk is seconds and the sub
  // was already nearly done.
  act(() => {
    useStore.setState({ previews: [livePreview(11)], livePreviewId: 11 } as never);
  });
  await settle();
  assert(container.querySelector('img[src*="/api/preview/11"]') != null,
    "the live frame never reached the stage (precondition)");

  openFramesGate!();
  framesGate = null;
  await settle();
  assert(standIn() == null,
    "the late gallery reply replaced a LIVE frame with a saved one — the " +
    "regression this ordering exists to prevent");
  assert(container.querySelector('img[src*="/api/preview/11"]') != null,
    "the live frame was displaced by the late reply");
});

// ------------------------------------------------------------------ report
if (root) act(() => { root!.unmount(); });
const total = passed + failed;
console.log(`captureLastFrameDom: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
