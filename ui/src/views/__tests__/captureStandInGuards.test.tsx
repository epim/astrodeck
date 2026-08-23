// captureStandInGuards.test.tsx — the parts of the saved-frame stand-in that
// captureLastFrameDom.test.tsx does not reach.
//
//   Run directly:  npx tsx src/views/__tests__/captureStandInGuards.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// FOUND BY SABOTAGE. Every case below was written because a single-line change
// to the feature left the whole UI suite green:
//
//   * `enabled: !shown` -> `enabled: true` in LivePreview. A browser that
//     already has a live frame walked the whole library for a picture the stage
//     would then refuse to paint. Invisible on screen, so no DOM assertion saw
//     it; the request count was only ever checked AFTER a fetch had been
//     allowed to happen.
//   * the `mtime` cache-buster dropped from the render URL — the response
//     carries max-age, so a path re-captured to keeps showing the old bytes.
//   * the `astro` class dropped from the stand-in <img>. The night filter is
//     the reason the class is there: on a dark-adapted screen this is the one
//     element on the stage that would emit unfiltered white.
//   * `role="status"` dropped from the chip. The stand-in arrives after the
//     stage has already been read, so a screen reader is told nothing changed.
//   * LOOK_BACK cut to 1. The listing window is what lets the picker see past
//     rows that are not this run's; nothing made a row it had to see past.
//   * the `!preview` guard dropped inside PreviewStage, which is what stops a
//     stand-in painting OVER a live frame. LivePreview never passes both at
//     once, so the stage's own guard has no witness through CaptureView — it
//     needs the component mounted directly.
//
// EXPECT ACT WARNINGS ON STDERR, for the same reason captureLastFrameDom does:
// `settle()` advances a real macrotask and CaptureView's own mount loaders
// finish outside any act() window. The tally at the bottom is the signal.

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
win.Element.prototype.setPointerCapture = function () {};
win.Element.prototype.releasePointerCapture = function () {};
win.Element.prototype.hasPointerCapture = function () { return false; };
win.HTMLElement.prototype.scrollIntoView = function () {};

const STAGE_CSS_W = 720;
win.devicePixelRatio = 2;
Object.defineProperty(win.HTMLElement.prototype, "clientWidth", {
  configurable: true, get() { return STAGE_CSS_W; },
});

// ----------------------------------------------------------- the network seam
// The stub HONOURS `limit`, which is what makes the look-back window testable:
// a server that always returns the whole fixture cannot tell a window of 12
// from a window of 1.
const gets: string[] = [];
let framesAll: any[] = [];

win.fetch = async (input: any, init: any = {}) => {
  const path = String(input);
  if ((init.method ?? "GET") === "GET") gets.push(path);
  if (path.startsWith("/api/gallery/frames")) {
    const limit = Number(/[?&]limit=(\d+)/.exec(path)?.[1] ?? 0) || framesAll.length;
    const frames = framesAll.slice(0, limit);
    const body = {
      frames, total: framesAll.length, bytes: 0, offset: 0,
      limit, truncated: frames.length < framesAll.length, scan_ms: 1,
    };
    return {
      ok: true, status: 200,
      headers: { get: () => "application/json" },
      json: async () => body,
      text: async () => JSON.stringify(body),
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
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../store");
const { PreviewStage } = await import("../../components/preview/PreviewStage");
const CaptureView = (await import("../CaptureView")).default;
type PreviewInfo = import("../../types").PreviewInfo;

// ------------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

const NOW_MS = Date.now();
const NOW_S = Math.floor(NOW_MS / 1000);
const TARGET = "NGC 6946";
const FRAME_PATH = "NGC 6946/Light_NGC6946_L_180s_0042.fits";
const FRAME_MTIME = NOW_S - 35;

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
    mtime: FRAME_MTIME,
    ...over,
  };
}

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

function seed(opts: { sequence?: any; previews?: PreviewInfo[]; liveId?: number | null } = {}): void {
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
    // THE LINK IS UP. jsdom has no WebSocket, so the store's default wsPhase is
    // "connecting" and `useLinkDown` (wsPhase !== "up") reads TRUE — which is
    // not the state under test: a `sequence` object can only have reached the
    // browser over a live socket in the first place, and the stand-in fetch is
    // gated on the link because a frozen sequence makes an hours-old frame
    // measure as seconds old. Left at the default, every mount here is a
    // link-down mount and nothing fetches.
    wsPhase: "up",
    wsConnected: true,
    previews: opts.previews ?? [],
    livePreviewId: opts.liveId ?? null,
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

let root: ReturnType<typeof createRoot> | null = null;
let container: any = null;
function mount(el: any): void {
  if (root) act(() => { root!.unmount(); });
  container = win.document.createElement("div");
  win.document.body.appendChild(container);
  root = createRoot(container);
  act(() => { root!.render(el); });
}
const text = (): string => String(container?.textContent ?? "");
const standIn = (): any => container.querySelector('img[src*="/api/gallery/view"]');
const framesCalls = (): string[] => gets.filter((p) => p.startsWith("/api/gallery/frames"));
async function settle(): Promise<void> {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

// ============================================ the fetch a full ring must skip
await test("PRECONDITION: an empty ring mid-run does walk the library", async () => {
  gets.length = 0;
  framesAll = [galleryFrame()];
  seed({ sequence: running() });
  mount(createElement(CaptureView));
  await settle();
  assert(/Exposure/.test(text()), "CaptureView never rendered");
  assert(framesCalls().length === 1,
    `expected one gallery request, got ${framesCalls().length}`);
  assert(standIn() != null, "no stand-in — the rest of this file has no baseline");
});

await test("a browser that ALREADY has a live frame never walks the library", async () => {
  gets.length = 0;
  framesAll = [galleryFrame()];
  // Same sequence, same listing, same everything as the precondition above —
  // the ONE difference is that this browser session already received a preview
  // over the WebSocket. Navigating away from Capture and back mid-run is the
  // ordinary way to arrive here, and the ring survives it.
  seed({ sequence: running(), previews: [livePreview(5)], liveId: 5 });
  mount(createElement(CaptureView));
  await settle();
  assert(container.querySelector('img[src*="/api/preview/5"]') != null,
    "the live frame is not on the stage (precondition)");
  assert(framesCalls().length === 0,
    `a browser with a live frame walked the library ${framesCalls().length} time(s) ` +
    "for a picture the stage will refuse to paint — on a cold cache that is " +
    "every FITS header in the library, over a relay, during a run");
  assert(standIn() == null, "a saved frame was painted over a live one");
});

// ===================================================== what the URL must carry
await test("the render URL carries the frame's mtime as a cache-buster", async () => {
  gets.length = 0;
  framesAll = [galleryFrame()];
  seed({ sequence: running() });
  mount(createElement(CaptureView));
  await settle();
  const src = String(standIn()?.getAttribute("src") ?? "");
  // PRECONDITION: there is a URL to read.
  assert(src.length > 0, "no stand-in to inspect (precondition)");
  const v = Number(/[?&]v=(\d+)/.exec(src)?.[1] ?? 0);
  assert(v === Math.round(FRAME_MTIME),
    `the URL is ${src} — /api/gallery/view answers with max-age, so without the ` +
    "frame's mtime in the query a path that gets re-captured to keeps serving " +
    "the OLD bytes out of the browser cache for an hour");
});

// ============================================================= what it must be
await test("the stand-in wears the night filter", () => {
  const img = standIn();
  // PRECONDITION: it is on screen.
  assert(img != null, "no stand-in (precondition)");
  const cls = String(img.getAttribute("class") ?? "");
  assert(/\bastro\b/.test(cls),
    `the stand-in's classes are "${cls}" — without \`astro\` this is the one ` +
    "element on a night-mode stage emitting unfiltered white at someone who " +
    "has spent twenty minutes dark-adapting");
});

await test("the chip is announced, not just drawn", () => {
  // The stand-in appears AFTER the stage has already been read once, so a
  // screen reader hears nothing at all unless the chip is a live region.
  const chip = Array.from(container.querySelectorAll('[role="status"]'))
    .map((n: any) => String(n.textContent ?? ""))
    .find((t: string) => /not live/i.test(t));
  assert(chip != null,
    "the 'not live' chip is not in a live region — it is painted for sighted " +
    `operators only. role=status nodes present: ${
      Array.from(container.querySelectorAll('[role="status"]')).length}`);
});

// ================================================== the look-back window
await test("the listing window sees past rows that are not this run's", async () => {
  gets.length = 0;
  // A real end-of-run tail: the flats and darks that were written after the
  // last light frame, plus a second target's leftovers, all NEWER than the
  // frame we want. The server sorts newest first, so the qualifying frame is
  // the eighth row — inside a window of twelve, invisible to a window of one.
  const filler = Array.from({ length: 7 }, (_, i) => galleryFrame({
    path: `M 31/Light_M31_${i}.fits`, name: `Light_M31_${i}.fits`,
    folder: "M 31", target: "M 31", ts: NOW_S - 10 - i,
  }));
  framesAll = [...filler, galleryFrame()];
  seed({ sequence: running() });
  mount(createElement(CaptureView));
  await settle();
  // PRECONDITION: the view asked, and the stub answered with a truncated page.
  assert(framesCalls().length === 1, "the view did not ask (precondition)");
  const limit = Number(/[?&]limit=(\d+)/.exec(framesCalls()[0])?.[1] ?? 0);
  assert(limit > 0, `the request set no limit: ${framesCalls()[0]}`);
  assert(standIn() != null,
    `the run's own frame sits at row 8 and the request asked for ${limit} row(s), ` +
    "so the picker never saw it. The window has to clear the tail of " +
    "calibration and other-target frames that a real library carries above it");
});

// ============================ the stage's own guard, with the component alone
// LivePreview never hands PreviewStage both a live frame and a stand-in — that
// is the point of `enabled: !shown`. Which means the stage's own refusal to
// paint one over the other has NO witness through CaptureView: delete it and
// the whole suite stays green. The stage is shared chrome (FocusView mounts it
// too), so its prop contract is tested here directly.
const STAND_IN = {
  src: "/api/gallery/view?path=saved.fits&w=1600&v=1",
  label: "Last saved frame, 23:14 — not live",
  alt: "Last saved frame of NGC 6946, 23:14 — already captured, not the live view",
};

function stage(preview: PreviewInfo | null): any {
  const s = useStore.getState() as any;
  return createElement(PreviewStage, {
    preview,
    lastCaptured: STAND_IN,
    viewport: s.viewport,
    setViewport: () => {},
    stretch: s.stretch,
    overlays: s.overlays,
    hfrGood: 3, hfrWarn: 5,
    night: false, linkDown: false,
    pinned: false, newSincePinned: 0,
    onReturnToLive: () => {},
  });
}

await test("PRECONDITION: with no frame of its own the stage paints the stand-in", () => {
  seed({ sequence: running() });
  mount(stage(null));
  assert(standIn() != null,
    "the stage refused a stand-in it was handed with nothing else to show — " +
    "the two tests below would then pass for the wrong reason");
});

await test("a stand-in never paints over a live frame", () => {
  mount(stage(livePreview(9)));
  // PRECONDITION: the stage rendered at all. Asserted on the stage element
  // rather than on the live <img>, because the failure under test REPLACES
  // that <img> — keying the precondition to it would report "nothing
  // rendered" for a tree that rendered the wrong thing.
  assert(container.querySelector(".preview-stage") != null,
    "the stage did not render (precondition)");
  assert(standIn() == null,
    "the stage painted a saved frame while holding a live one — precedence " +
    "inverted, and the operator is judging focus from an old picture");
  assert(container.querySelector('img[src*="/api/preview/9"]') != null,
    "the live frame is not on the stage either");
  assert(!/not live/i.test(text()),
    "the stand-in's chip is over a live frame");
});

await test("a live frame that will not decode says so instead of substituting", () => {
  mount(stage(livePreview(10)));
  const img = container.querySelector('img[src*="/api/preview/10"]');
  // PRECONDITION: there is a live frame to break. (If the stand-in already
  // won, the test above says so; this one would be untestable.)
  assert(img != null,
    "the live frame never reached the stage, so there is nothing to break — " +
    "see 'a stand-in never paints over a live frame'");
  act(() => { img.dispatchEvent(new win.Event("error", { bubbles: false })); });
  assert(/Capture unavailable/i.test(text()),
    `a frame that cannot be displayed is a fault the operator has to see. ` +
    `Text: ${text().slice(0, 200)}`);
  assert(standIn() == null,
    "a broken live frame was papered over with an older saved one — the stage " +
    "now shows a picture while the camera's own output is failing");
});

// -------------------------------------------------------------------- report
if (root) act(() => { root!.unmount(); });
const total = passed + failed;
console.log(`captureStandInGuards: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
