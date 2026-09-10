// videoDom.test.tsx - VIDEO · PLANETS, mounted, pressed, refused (D-RIG-1).
//
//   Run directly:  npx tsx src/next/hubs/rig/capture/__tests__/videoDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by tsc.
//
// THE SIX THINGS THIS FILE IS FOR, and what goes wrong without each:
//
//  1. A PRECONDITION MARKER. A DOM test asserting an absence over a blank page
//     passes forever. Every assertion below runs after `rig-capture-video` has
//     been found, and every refusal test runs after the ACCEPTED case has
//     posted - a positive control, so "nothing posted" can only mean "the
//     refusal suppressed it".
//  2. THE ROUTE IS THE MODE. `#/rig/capture?mode=video` must render VIDEO and
//     `#/rig/capture` must render STILL. The Sky hub's RECORD CTA for a planet
//     or the Moon navigates to the first of those (`SkyHub.tsx:695-699`), and a
//     screen holding its mode in a `useState` would swallow the hand-off with
//     nothing on screen to say it had been asked for.
//  3. THE POST BODY IS THE ALIGNED SUBFRAME. The picker rounds before the press
//     so what is on screen is what gets recorded; a body carrying the raw typed
//     width would record a different rectangle than the one displayed and the
//     only symptom would be a readout that changes by itself.
//  4. THE BAR MOVES ON THE BUS. Progress arrives on the `video` bus event, so
//     the one store case this task bought has to reach the bar. A frozen bar
//     during a two-minute recording is the "green while wrong" shape.
//  5. REFUSALS BY CODE, NEVER BY MESSAGE. `no_video_path` replaces the
//     controls; the 507 carries two byte counts and no code and gets its own
//     sentence; neither may post a second time.
//  6. HONEST-DISABLED where it is still true: a viewer's RECORD, a principal
//     without `view.media` and the DOWNLOAD SER anchor, and a camera whose
//     `video_path` is "none" - and STOP, which is never armed by any of them.
//
// Convention: jsdom by hand, createRoot + act, native events, printed tally plus
// the `{ passed, failed, total }` export (shell-and-tests.md section 4).

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------------ css stub
// The VIDEO area root imports `video.css`; Node has no idea what that is. The
// hook has to run BEFORE any import that reaches one, so every import below is
// dynamic. Copied from `hubs/settings/__tests__/filesSheetsDom.test.tsx:44-61`.
{
  const { registerHooks } = await import("node:module");
  registerHooks({
    load(url: string, context: any, nextLoad: any) {
      if (url.endsWith(".css")) {
        return { format: "module", shortCircuit: true, source: "export default {};" };
      }
      return nextLoad(url, context);
    },
  } as any);
}

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/#/rig/capture?mode=video", pretendToBeVisual: true },
);
const win = dom.window as any;

win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} addEventListener() {} send() {} };
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
win.Element.prototype.setPointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.releasePointerCapture = function () { /* jsdom has none */ };
win.scrollTo = () => {};

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "PointerEvent", "localStorage", "sessionStorage", "getComputedStyle",
  "matchMedia", "WebSocket", "ResizeObserver",
  "requestAnimationFrame", "cancelAnimationFrame",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------- fetch recorder
interface Ask { url: string; method: string; body: any }
const asks: Ask[] = [];

const RECORDING = {
  id: "2026-09-10T2214-ser01", ts: 1_757_530_000, bytes: 15_000_000,
  frames: 1000, fps: 28.4, roi: { x: 680, y: 508, w: 640, h: 480, bin: 1 },
  camera: "ASI662MC", has_stack: false,
};

const IDLE_STATE = {
  active: false, id: null, state: "idle", frames: 0, target_frames: 0,
  elapsed_s: 0, fps: 0, dropped: 0, bytes: 0, roi: null,
  started_ts: null, finished_ts: null, error: null,
};

/** The recorder's own state, as `GET /api/capture/video` would report it. The
 *  cold GET the screen fires after a 202 has to answer with the recording that
 *  was just started, or the test would be exercising a merge against an idle
 *  shape the rig would never send. */
let currentState: any = IDLE_STATE;

/** What the next `POST /api/capture/video` answers with. Set per test. */
let startAnswer: { status: number; body: any } = {
  status: 202,
  body: {
    started: "video", id: "2026-09-10T2301-ser02", target_frames: 300,
    actual_fps: 30, clamped: false, clamp_reason: null, est_bytes: 184_802_578,
    roi: { x: 1680, y: 1260, w: 640, h: 480, bin: 1 },
  },
};

g.fetch = async (url: any, init: any) => {
  const u = String(url);
  const method = (init?.method ?? "GET").toUpperCase();
  let body: any = null;
  if (init?.body) { try { body = JSON.parse(init.body); } catch { body = init.body; } }
  asks.push({ url: u, method, body });

  const ok = (b: any) => ({ ok: true, status: 200, statusText: "OK", json: async () => b });
  if (method === "POST" && /\/api\/capture\/video$/.test(u)) {
    if (startAnswer.status >= 400) {
      return {
        ok: false, status: startAnswer.status, statusText: "Conflict",
        json: async () => startAnswer.body,
      };
    }
    const b = startAnswer.body;
    currentState = {
      active: true, id: b.id, state: "arming", frames: 0,
      target_frames: b.target_frames, elapsed_s: 0, fps: 0, dropped: 0, bytes: 0,
      roi: b.roi, requested_fps: 30, actual_fps: b.actual_fps,
      clamped: b.clamped, clamp_reason: b.clamp_reason,
      started_ts: 1_757_530_050, finished_ts: null, error: null,
    };
    return { ok: true, status: 202, statusText: "Accepted", json: async () => b };
  }
  if (method === "GET" && /\/api\/capture\/video$/.test(u)) return ok(currentState);
  if (method === "POST" && /\/api\/capture\/video\/stop$/.test(u)) {
    // `finalising` is still WRITING the file, which is what the route's own
    // unwind means; the terminal state arrives on the bus.
    currentState = { ...currentState, state: "finalising" };
    return ok({ cancelled: true, id: currentState.id, frames: 12, bytes: 400_000 });
  }
  if (method === "GET" && /\/api\/captures\/video$/.test(u)) {
    return ok({ recordings: [RECORDING] });
  }
  return ok({ ok: true });
};

const videoPosts = () => asks.filter(
  (a) => a.method === "POST" && /\/api\/capture\/video$/.test(a.url));

// ------------------------------------------ imports, AFTER the globals are set
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { CaptureScreen } = await import("../CaptureScreen");
const { NEEDS_CAPTURE_REASON } = await import("../captureGate");
const { noVideoPathReason } = await import("../video/videoModel");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
async function testAsync(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg} (expected ${String(want)}, got ${String(got)})`);
}
const settle = async () => {
  for (let i = 0; i < 4; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
const q = (sel: string) => container.querySelector(sel) as any;
const press = async (el: any) => {
  await act(async () => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true })); });
  await settle();
};
/** RECORD is a two-tap arm: the first press swaps the label, the second fires.
 *  Pressing once and asserting a POST would be asserting the wrong contract. */
const armAndPress = async (el: any) => { await press(el); await press(q('[data-testid="video-record"]')); };

const OPERATOR = {
  role: "operator", email: "op@rig",
  caps: ["view.status", "view.preview", "control.capture", "control.mount"],
};
const VIEWER = { role: "viewer", email: null, caps: ["view.status", "view.preview"] };
const ADMIN = {
  role: "admin", email: "admin@rig",
  caps: [
    "view.status", "view.preview", "view.media", "control.capture", "control.mount",
  ],
};

function camera(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    temperature: -10, can_cool: true, width: 4000, height: 3000,
    max_gain: 500, max_bin: 4,
    // S7c's four video capability fields (hub.py:6959-6968). `video_path` is
    // the STRING the engine publishes.
    roi_align: [8, 2], burst_supported: true, max_fps: 120, video_path: "native",
    ...over,
  };
}

function seed(over: Record<string, unknown> = {}, cam: Record<string, unknown> = {}): void {
  // A fresh seed is a fresh rig: nothing is recording. Without this a test that
  // re-mounts after the STOP above would find the recorder still finalising and
  // assert over a screen showing STOP where it expected RECORD.
  currentState = IDLE_STATE;
  useStore.setState({
    principal: OPERATOR,
    equipConnected: true,
    wsPhase: "up",
    authGate: "open",
    video: null,
    status: {
      connected: { camera: { connected: true, name: "ASI662MC" } },
      looping: false,
      live_stack_active: false,
      busy_lanes: [],
      camera: camera(cam),
    },
    frameSettings: {
      capture: { exposure_s: 2, gain: 120, offset: 30, binning: 1, filter: null },
      focus: { exposure_s: 2, gain: 200, offset: 30, binning: 1, filter: null },
      solve: { exposure_s: 0.3, gain: 200, offset: 30, binning: 1, filter: null },
      guide: { exposure_s: 2, gain: 100, offset: 30, binning: 1, filter: null },
    },
    captureTarget: "",
    previews: [],
    livePreviewId: null,
    selectedPreviewId: null,
    sequence: { state: "idle" },
    polar: {
      state: "idle", az_error: 0, alt_error: 0, total_error: 0, progress: 0,
      message: "", source: null,
    },
    toasts: [],
    ...over,
  } as never);
}

/** Re-mount at a hash. The mode is route state, so this is how the two halves
 *  are selected - there is no prop and no local flag to set. */
async function mountAt(hash: string): Promise<void> {
  win.history.replaceState(null, "", `/${hash}`);
  await act(async () => { root.render(createElement("div")); });
  await settle();
  await act(async () => { root.render(createElement(CaptureScreen)); });
  await settle();
}

seed();
await mountAt("#/rig/capture?mode=video");

// ------------------------------------------------------------ 1. the marker
test("VIDEO mode rendered - the precondition every assertion below rests on", () => {
  assert(q('[data-testid="rig-capture"]') != null, "the capture screen did not mount at all");
  assert(q('[data-testid="rig-capture-video"]') != null,
    "no rig-capture-video marker: ?mode=video did not select VIDEO");
  assert(q('[data-testid="video-roi"]') != null, "no subframe picker");
  assert(q('[data-testid="video-record"]') != null, "no RECORD button");
  assert(q('[data-testid="video-recordings"]') != null, "no recordings list");
});

test("the still bench is NOT mounted underneath - both halves own the camera", () => {
  eq(q('[data-testid="capture-go"]'), null,
    "the still CAPTURE button is on screen under VIDEO; its dials could not reach the camera");
  eq(q('[data-testid="capture-controls"]'), null, "the still controls are still mounted");
});

test("the operator's RECORD is live, not locked", () => {
  eq(q('[data-testid="video-record"]').getAttribute("aria-disabled"), null,
    "precondition: an operator with a recording camera found RECORD locked");
});

// ------------------------------------------------- 2. the route IS the mode
await testAsync("#/rig/capture with no parameter renders STILL, not VIDEO", async () => {
  await mountAt("#/rig/capture");
  assert(q('[data-testid="capture-go"]') != null, "the still bench did not render");
  eq(q('[data-testid="rig-capture-video"]'), null,
    "VIDEO rendered without ?mode=video - the route is not driving the mode");
  await mountAt("#/rig/capture?mode=video");
  assert(q('[data-testid="rig-capture-video"]') != null, "VIDEO did not come back");
});

await testAsync("the Sky hand-off's target survives into VIDEO mode", async () => {
  // SkyHub.tsx:695-699 sends `?mode=video&target=&ra=&dec=` for a planet or the
  // Moon. The mode must select VIDEO and the target must reach the screen.
  await mountAt("#/rig/capture?mode=video&target=Jupiter&ra=3.14&dec=17.2");
  assert(q('[data-testid="rig-capture-video"]') != null, "the hand-off landed on the wrong mode");
  assert(/on Jupiter/.test(container.textContent),
    "the hand-off's target never reached the screen - the recording has no context line");
  assert(/named by its timestamp/.test(container.textContent),
    "the screen implies the .ser is filed under the target; it is not, and nothing "
    + "else on the page says so");
  await mountAt("#/rig/capture?mode=video");
});

// --------------------------------------------- 3. the POST, and the arming
await testAsync("RECORD arms, then posts once with the ALIGNED subframe", async () => {
  asks.length = 0;
  const rec = q('[data-testid="video-record"]');
  await press(rec);
  eq(videoPosts().length, 0,
    "the first press fired - a recording claims the camera for minutes and must be armed");
  assert(/CONFIRM RECORD/.test(q('[data-testid="video-record"]').textContent || ""),
    "the armed state does not say what a second press will do");

  await press(q('[data-testid="video-record"]'));
  const posts = videoPosts();
  eq(posts.length, 1, "RECORD did not post exactly once to /api/capture/video");
  const body = posts[0].body;
  eq(JSON.stringify(Object.keys(body).sort()),
    JSON.stringify(["duration_s", "exposure_ms", "format", "fps", "gain", "offset", "roi"]),
    "the recording body's field set changed");
  eq(body.format, "ser", "the container was not named");
  eq(body.fps, 30, "the requested rate");
  eq(body.exposure_ms, 8, "the per-frame exposure");
  eq(body.duration_s, 10, "the duration");
  eq(body.gain, 120, "the gain was not seeded from the bench");
  // The FULL preset over a 4000 x 3000 sensor, aligned: both already land on
  // the grid, so the aligned rectangle is the sensor.
  eq(body.roi.w, 4000, "the subframe width is not the aligned one");
  eq(body.roi.h, 3000, "the subframe height is not the aligned one");
  eq(body.roi.bin, 1, "the binning was dropped from the body");
});

await testAsync("the 202's own subframe replaces the picker's - the server rounds again", async () => {
  // startAnswer's roi is 640 x 480 at (1680, 1260), which is NOT what was asked
  // for. The screen must adopt it: a picker still showing 4000 x 3000 while a
  // 640 x 480 file is being written is the readout that changes by itself.
  const w = q('[data-testid="video-roi-w"]');
  eq(w.value, "640", "the picker still shows the requested width, not the recorded one");
  eq(q('[data-testid="video-roi-x"]').value, "1680", "the origin was not adopted from the 202");
});

// ------------------------------------------------------- 4. the bus event
await testAsync("a `video` bus event moves the bar and the frame counter", async () => {
  assert(q('[data-testid="video-progress"]') != null,
    "the 202 drew no progress at all - the assertions below would be vacuous");
  await act(async () => {
    useStore.getState().handleEvent({
      type: "video",
      data: {
        id: "2026-09-10T2301-ser02", state: "recording", frames: 150,
        target_frames: 300, elapsed_s: 5.1, fps: 29.4, dropped: 0,
        bytes: 92_000_000, path: null,
      },
      ts: 1_757_530_100,
    });
  });
  await settle();
  assert(/150 \/ 300 frames/.test(container.textContent),
    "the frame counter did not move on the bus event - a frozen bar during a recording");
  assert(/29.4 fps measured/.test(container.textContent),
    "the MEASURED rate is missing or unlabelled - it is not the planned rate");
  const bar = q('[data-testid="video-progress-bar"]');
  assert(bar != null, "the bar is gone");
  assert(/150 of 300 frames recorded/.test(bar.getAttribute("aria-label") || ""),
    "the bar's accessible name does not carry the count");
});

await testAsync("STOP is a single tap and is never armed", async () => {
  const stop = q('[data-testid="video-stop"]');
  assert(stop != null, "the primary did not become STOP while a recording was live");
  eq(stop.getAttribute("data-armed"), null,
    "STOP is armed - an escape hatch that needs two taps is one that fails when it matters");
  eq(q('[data-testid="video-record"]'), null, "RECORD is still on screen during a recording");
  asks.length = 0;
  await press(stop);
  assert(asks.some((a) => /\/api\/capture\/video\/stop$/.test(a.url)),
    "STOP posted nothing");
});

await testAsync("a terminal event ends the recording and RECORD comes back", async () => {
  await act(async () => {
    useStore.getState().handleEvent({
      type: "video",
      data: {
        id: "2026-09-10T2301-ser02", state: "done", frames: 300, target_frames: 300,
        elapsed_s: 10.2, fps: 29.4, dropped: 1, bytes: 184_000_000,
        path: "video/2026-09-10T2301-ser02.ser",
      },
      ts: 1_757_530_110,
    });
  });
  await settle();
  assert(q('[data-testid="video-record"]') != null,
    "a finished recording left STOP on screen - the camera reads as claimed forever");
  assert(/1 dropped/.test(container.textContent),
    "a dropped frame was not reported on the finished file");
});

// ------------------------------------------------------------- 5. the 507
await testAsync("a 507 renders the disk sentence with BOTH byte figures, and posts once", async () => {
  startAnswer = {
    status: 507,
    body: {
      detail: "insufficient disk space",
      free_bytes: 2_100_000_000, required_bytes: 5_800_000_000,
    },
  };
  asks.length = 0;
  await armAndPress(q('[data-testid="video-record"]'));
  eq(videoPosts().length, 1, "the refused recording was sent more than once");
  const disk = q('[data-testid="video-disk"]');
  assert(disk != null, "the 507 said nothing - the server sends numbers and no sentence");
  const text = disk.textContent || "";
  // `fmtBytes` is 1024-based on purpose (lib/gallery.ts), so these are the two
  // figures a file manager would show beside them.
  assert(/2.0 GB/.test(text) && /5.4 GB/.test(text),
    `the disk sentence is missing one of its two byte figures: ${text}`);
  assert(/Shorten the duration/.test(text),
    "the disk sentence does not say what to do about it");
  assert(q('[data-testid="video-files-link"]') != null, "no route to the files that fill the disk");
  // The bar is not gone - it still carries the FINISHED recording's tally,
  // which is the point: a 507 starts nothing, so nothing on screen may change
  // to describe a file that was never opened.
  assert(/300 \/ 300 frames/.test(container.textContent),
    "the 507 replaced the finished recording's tally - it armed something on a rig "
    + "that refused the request");
  assert(q('[data-testid="video-record"]') != null,
    "a refused recording put the screen into the recording state");
});

// ------------------------------------------------- 6. no_video_path, by code
await testAsync("a 409 no_video_path replaces the controls with the SERVER's sentence", async () => {
  const sentence = "ASI2600MC cannot record video through AstroDeck yet: this camera is "
    + "driven through ASCOM, which has no subframe or burst path. Video capture needs a "
    + "natively-driven camera.";
  startAnswer = { status: 409, body: { detail: { detail: sentence, code: "no_video_path" } } };
  // The engine does not publish the capability (older than S7c), so the mode
  // renders live and the refusal can only arrive from the wire.
  seed({}, { video_path: undefined, burst_supported: undefined });
  await mountAt("#/rig/capture?mode=video");
  eq(q('[data-testid="video-record"]').getAttribute("aria-disabled"), null,
    "an engine that does not publish the capability locked a control that might work");

  asks.length = 0;
  await armAndPress(q('[data-testid="video-record"]'));
  eq(videoPosts().length, 1, "the refusal was retried");
  const refusal = q('[data-testid="video-refusal"]');
  assert(refusal != null, "no_video_path did not replace the controls");
  assert((refusal.textContent || "").includes(sentence),
    "the server's own sentence was re-worded - it names the brand AND the backend");
  assert(/Single frames, LOOP and Live View still work/.test(container.textContent),
    "the refusal did not say the still path is unaffected");
  eq(q('[data-testid="video-record"]'), null,
    "RECORD is still on screen for a camera that cannot record");
  // The list survives: recordings made on ANOTHER camera are still on the rig.
  assert(q('[data-testid="video-recordings"]') != null,
    "the recordings list vanished with the controls");
});

await testAsync("video_path 'none' locks RECORD BEFORE the press and fires nothing", async () => {
  startAnswer = {
    status: 202,
    body: {
      started: "video", id: "x", target_frames: 1, actual_fps: 1, clamped: false,
      clamp_reason: null, est_bytes: 1, roi: { x: 0, y: 0, w: 8, h: 2, bin: 1 },
    },
  };
  seed({}, { video_path: "none", burst_supported: false });
  await mountAt("#/rig/capture?mode=video");
  asks.length = 0;
  const refusal = q('[data-testid="video-refusal"]');
  assert(refusal != null,
    "camera.video_path 'none' did not reach the screen - the only way to find out "
    + "would be to start a recording and read a 409 back");
  assert((refusal.textContent || "").includes(noVideoPathReason("ASI662MC")),
    "the pre-press refusal does not name the camera it is about");
  eq(q('[data-testid="video-record"]'), null, "RECORD is offered for a camera that cannot record");
  eq(videoPosts().length, 0, "a camera that cannot record still reached the rig");
});

// ------------------------------------------------------------- 7. the list
await testAsync("DOWNLOAD SER is an anchor only for a principal holding view.media", async () => {
  seed({ principal: ADMIN });
  await mountAt("#/rig/capture?mode=video");
  const link = q('[data-testid="video-download"]');
  assert(link != null, "an admin was not offered the .ser at all");
  eq(link.tagName, "A", "the download is not an anchor, so a long-press cannot save it");
  assert(/\/api\/captures\/video\/2026-09-10T2214-ser01\.ser/.test(link.getAttribute("href")),
    "the download points somewhere other than the recording");
  assert(link.hasAttribute("download"), "the anchor does not ask the browser to save the file");

  seed({ principal: OPERATOR });
  await settle();
  eq(q('[data-testid="video-download"]'), null,
    "an operator was handed an anchor to a route that answers 403 - the browser reports "
    + "that as a broken download");
  const locked = q('[data-testid="video-download-locked"]');
  assert(locked != null, "the download vanished instead of explaining itself");
  eq(locked.getAttribute("aria-disabled"), "true", "the locked download is not honest-disabled");
  assert(/syncer or admin access/.test(locked.textContent || ""),
    "the locked download names the wrong roles - a syncer genuinely holds view.media");
});

await testAsync("a recording still being written cannot be deleted, and says why", async () => {
  await act(async () => {
    useStore.getState().handleEvent({
      type: "video",
      data: {
        id: "2026-09-10T2214-ser01", state: "recording", frames: 3, target_frames: 300,
        elapsed_s: 0.2, fps: 15, dropped: 0, bytes: 1000, path: null,
      },
      ts: 1_757_530_200,
    });
  });
  await settle();
  const del = q('[data-testid="video-delete"]');
  assert(del != null, "the delete control is gone");
  eq(del.getAttribute("aria-disabled"), "true",
    "the file being written offered DELETE, which the rig answers 409 lane_busy to");
  assert(/still being written/.test(del.getAttribute("title") || ""),
    "the locked DELETE does not say why");
});

// ------------------------------------------------------------ 8. the viewer
await testAsync("a viewer sees the reason on RECORD and posts nothing", async () => {
  seed({ principal: VIEWER, video: null });
  await mountAt("#/rig/capture?mode=video");
  asks.length = 0;
  const rec = q('[data-testid="video-record"]');
  assert(rec != null, "RECORD was hidden from the viewer instead of locked");
  eq(rec.getAttribute("aria-disabled"), "true", "a viewer's RECORD button looks live");
  eq(rec.getAttribute("title"), NEEDS_CAPTURE_REASON, "the reason is not on the button");
  assert(rec.hasAttribute("disabled") === false,
    "RECORD used the native disabled attribute, which takes the reason out of the a11y tree");
  await press(rec);
  await press(rec);
  eq(videoPosts().length, 0, "a viewer's presses reached the rig");
  const toasts = (useStore.getState() as any).toasts as Array<{ title?: string }>;
  assert(toasts.some((t) => t.title === NEEDS_CAPTURE_REASON),
    "the viewer's press was swallowed - the reason was said nowhere");
});

act(() => { root.unmount(); });

const total = passed + failed;
console.log(`videoDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
