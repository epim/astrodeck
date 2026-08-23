// captureStandInStaleness.test.tsx — the stand-in must stop being shown the
// moment it stops being true, and must never be fetched over a dead link.
//
//   Run directly:  npx tsx src/views/__tests__/captureStandInStaleness.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT WENT WRONG. The first version of this feature decided the stand-in's
// verdict ONCE — when the gallery reply landed — and then held the finished URL
// forever. Every refusal in lib/lastSessionFrame.ts (the run must be live, the
// target must match, the frame must be younger than 45 minutes) was applied at
// pick time and never again, and the held picture was dropped only when a live
// preview arrived. Measured against the mounted view, all three escaped:
//
//   * sequence -> "complete": the chip still read "Last saved frame, 23:14 —
//     not live" over a finished run. Mounting the SAME view on the SAME idle
//     rig gets the logo, so the screen depended on when you arrived.
//   * sequence.target -> "M 31": the NGC 6946 frame stayed up, and the chip
//     names no target, so nothing on screen said the picture was of a different
//     object. This is the "lie about what the camera is doing" the picker's own
//     header says it exists to prevent.
//   * the clock advanced 90 minutes: a frame twice SESSION_FRAME_MAX_AGE_S old
//     was still on the stage.
//
// AND THE DEAD LINK. `sequence` is written only by the WebSocket "sequence"
// event, so a socket that dies mid-run leaves the whole object frozen —
// including `progress.server_now_ms`, which is the clock the age gate measures
// against. Two frozen numbers compare as fresh forever: an hours-old frame
// measured 41 seconds old. Nothing inside the picker can see that, because both
// of its inputs are stale in the same direction. `linkDown` is the only honest
// signal, and it now gates the fetch — and the "Stale — link down" ribbon, which
// used to be written after the stage's empty-state early return and therefore
// never drew on the one branch that most needed it, now rides over the empty
// state too.
//
// EXPECT ACT WARNINGS ON STDERR, for the same reason the sibling files do:
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

const gets: string[] = [];
let framesAll: any[] = [];

win.fetch = async (input: any, init: any = {}) => {
  const path = String(input);
  if ((init.method ?? "GET") === "GET") gets.push(path);
  if (path.startsWith("/api/gallery/frames")) {
    const body = {
      frames: framesAll, total: framesAll.length, bytes: 0, offset: 0,
      limit: 12, truncated: false, scan_ms: 1,
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
const CaptureView = (await import("../CaptureView")).default;

// ------------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
// A fetch that throws inside the hook's void async IIFE would otherwise take
// this file's whole tally with it — the process dies and the runner can only
// report "unscorable". Name it instead.
process.on("unhandledRejection", (e) => {
  failed++;
  failures.push(`x UNHANDLED REJECTION escaped a test: ${String(e)}`);
});

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

function seed(opts: { sequence?: any; wsPhase?: string } = {}): void {
  const phase = opts.wsPhase ?? "up";
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
    // jsdom has no WebSocket, so the store's default phase is "connecting" and
    // `useLinkDown` (wsPhase !== "up") reads TRUE. A `sequence` object can only
    // have reached a browser over a live socket, so "up" is the honest default
    // here and "down" is the case under test.
    wsPhase: phase,
    wsConnected: phase === "up",
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
const standIn = (): any => container.querySelector('img[src*="/api/gallery/view"]');
const framesCalls = (): string[] => gets.filter((p) => p.startsWith("/api/gallery/frames"));
async function settle(): Promise<void> {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}
function statusFrame(seq: any): void {
  act(() => { useStore.setState({ sequence: seq } as never); });
}

/** Mount mid-run, wait for the reply, and prove the stand-in is on the stage.
 *  Every test below denies something about a picture that has to be there
 *  first — "it is gone" is also true of a view that never asked. */
async function standInOnStage(): Promise<void> {
  gets.length = 0;
  framesAll = [galleryFrame()];
  seed({ sequence: running() });
  mount();
  await settle();
  assert(/Exposure/.test(text()), "CaptureView never rendered");
  assert(standIn() != null,
    `PRECONDITION FAILED: no stand-in to retire (${framesCalls().length} gallery calls)`);
}

// ================================================== the verdict is re-run
await test("the stand-in leaves when the run does", async () => {
  await standInOnStage();
  // The rig has finished. Mounting fresh on this same state gets the logo —
  // captureLastFrameDom pins that — so a browser already on the tab must not
  // get a different answer.
  statusFrame({ state: "complete", target: TARGET, plan_name: "Tonight" });
  await settle();
  assert(standIn() == null,
    "a finished run is still showing a saved frame where the live view goes — " +
    `text: ${text().slice(0, 200)}`);
  assert(/No capture yet/.test(text()),
    "the stand-in went but the empty state did not arrive");
});

await test("the stand-in leaves when the plan moves to another target", async () => {
  await standInOnStage();
  // Multi-target plans do exactly this: SequenceState carries target_index and
  // the engine republishes with the new target the moment the step changes.
  statusFrame(running({ target: "M 31", target_index: 1 }));
  await settle();
  assert(standIn() == null,
    "an NGC 6946 frame is on the stage under an M 31 step, and the chip names " +
    "no target — nothing on screen says the picture is of a different object");
});

await test("the stand-in leaves when it ages out of the session window", async () => {
  await standInOnStage();
  // The RIG's clock moves on 90 minutes — twice SESSION_FRAME_MAX_AGE_S — with
  // no new frame. A paused run parked for an hour is the real version of this.
  statusFrame(running({
    progress: {
      frames_done: 42, frames_total: 120, percent: 35, elapsed_s: 13200,
      rejected: 0, server_now_ms: NOW_MS + 90 * 60 * 1000,
    },
  }));
  await settle();
  assert(standIn() == null,
    "a frame twice the 45-minute session window old is still on the stage — " +
    "the age gate is being applied at fetch time only");
});

await test("a stand-in that is still true is not thrown away", async () => {
  // The control for the three above: the mechanism has to refuse the frames
  // that went bad WITHOUT retiring the one the feature exists to show.
  await standInOnStage();
  for (let i = 0; i < 5; i++) {
    statusFrame(running({
      progress: {
        frames_done: 42, frames_total: 120, percent: 35, elapsed_s: 7800 + i,
        rejected: 0, server_now_ms: NOW_MS + i * 2000,
      },
    }));
  }
  await settle();
  assert(standIn() != null,
    "five ordinary status frames retired a stand-in that is still the newest " +
    "frame of the running target");
  assert(framesCalls().length === 1,
    `re-judging turned into ${framesCalls().length} library walks — it must read ` +
    "the frame it already has, not fetch another");
});

// ============================================================== a cloud hold
await test("a run holding for cloud is still a live run", async () => {
  gets.length = 0;
  framesAll = [galleryFrame()];
  // sequence/engine.py PROMOTES any routine `running` publish to "holding"
  // while the hold is up, so this is what a real overcast night looks like on
  // the wire. The rig is still imaging: cloud probes on a timer, plus hold
  // darks.
  seed({ sequence: running({ state: "holding", hold: "clouds" }) });
  mount();
  await settle();
  assert(/Exposure/.test(text()), "CaptureView never rendered");
  assert(framesCalls().length === 1,
    `a holding run made ${framesCalls().length} gallery requests — "holding" is ` +
    "not being read as a live run");
  assert(standIn() != null,
    `the logo is on the stage over a rig that is still imaging: ${text().slice(0, 200)}`);
});

await test("a hold does not change the answer for a browser already looking", async () => {
  // The worse half of the same bug: with the stand-in already painted, a hold
  // left it up — so the same rig state gave two different screens depending on
  // when you arrived. Both must now say the same thing.
  await standInOnStage();
  statusFrame(running({ state: "holding", hold: "clouds" }));
  await settle();
  assert(standIn() != null,
    "the hold retired a stand-in that a fresh mount on the same state does show");
});

// ============================================================== a dead link
await test("a dead link is never used to justify a stand-in", async () => {
  gets.length = 0;
  // The socket died three hours ago. `sequence` is the last thing it delivered,
  // frozen at state=running, and its `server_now_ms` is frozen with it — so the
  // newest frame, written just before the freeze, measures 40 seconds old
  // against a clock that stopped. The age gate cannot see that.
  const FROZEN_MS = NOW_MS - 3 * 3600 * 1000;
  const FROZEN_S = Math.floor(FROZEN_MS / 1000);
  framesAll = [galleryFrame({ ts: FROZEN_S - 40, mtime: FROZEN_S - 35, local_clock: "20:14" })];
  seed({
    wsPhase: "down",
    sequence: running({
      progress: {
        frames_done: 42, frames_total: 120, percent: 35, elapsed_s: 7800,
        rejected: 0, server_now_ms: FROZEN_MS,
      },
    }),
  });
  mount();
  await settle();
  assert(/Exposure/.test(text()), "CaptureView never rendered");
  assert(framesCalls().length === 0,
    "the library was walked to find a stand-in for a run nobody has heard from " +
    "in three hours — every number that would refuse it is frozen too");
  assert(standIn() == null, "a three-hour-old frame is on the stage");
});

await test("and the stage says the link is down, with no frame of its own", async () => {
  // PRECONDITION: we are in the empty state, over a dead link. This ribbon used
  // to be written AFTER the empty-state early return, so this branch — a
  // browser that opened Capture after the socket died — showed nothing at all.
  assert(standIn() == null, "not in the empty state (precondition)");
  assert((useStore.getState() as any).wsPhase !== "up", "the link is up (precondition)");
  assert(/Stale — link down/.test(text()),
    `the empty stage is silent about a dead connection: ${text().slice(0, 200)}`);
});

await test("the fetch it refused is taken as soon as the link returns", async () => {
  // The gate is on the FETCH, not on `enabled`: flipping `enabled` would also
  // run the hook's clearing effect while `asked` stays set, retiring the
  // stand-in permanently over a momentary blip.
  assert(framesCalls().length === 0, "already fetched (precondition)");
  framesAll = [galleryFrame()];
  act(() => {
    useStore.setState({
      wsPhase: "up", wsConnected: true, sequence: running(),
    } as never);
  });
  await settle();
  assert(framesCalls().length === 1,
    `the reconnect produced ${framesCalls().length} gallery requests, expected 1 — ` +
    "a link that drops and comes back inside one mount must still get its answer");
  assert(standIn() != null, "the stand-in never arrived after the link came back");
});

await test("a link that dies UNDER a stand-in is called out over it", async () => {
  // The other order, and the one that actually happens: the picture arrived
  // honestly and then the socket died. The frame stays — it is a real sub of
  // the running target and its chip says when it was taken — but the frozen
  // sequence can no longer vouch for anything, so the stage has to say so. This
  // is the case that measured "stand-in ON STAGE, no ribbon" before the ribbon
  // moved above the empty-state early return.
  await standInOnStage();
  assert(!/Stale — link down/.test(text()), "the ribbon is already up (precondition)");
  act(() => { useStore.setState({ wsPhase: "down", wsConnected: false } as never); });
  await settle();
  assert(standIn() != null,
    "the dropped link threw away a picture that was true when it arrived");
  assert(/Stale — link down/.test(text()),
    `a saved frame is on the stage under a dead socket with nothing saying so: ${text().slice(0, 200)}`);
});

// ==================================================== the chip's prominence
await test("the not-live chip reads at the same weight as the other two", async () => {
  await standInOnStage();
  // Three states on this stage mean "what you are looking at is not the live
  // view", and they were not equally legible. Pinned draws a scrim and a panel
  // banner; link-down draws a scrim and a warn ribbon with an alert icon; this
  // one drew a 10px mono chip in the ORDINARY text colour, with no icon — the
  // weakest of the three, over the most confusable state, because a stand-in
  // looks exactly like a live frame. No scrim here on purpose (the picture is
  // the point; the other two are dimming something to be distrusted), but the
  // warn colour and the icon are the shared vocabulary and it has to carry them.
  const chip = container.querySelector('[role="status"].preview-chip');
  assert(chip != null, "no not-live chip on the stage (precondition)");
  assert(/not live/i.test(String(chip.textContent ?? "")),
    `the role=status chip is not the not-live one: "${chip.textContent}"`);
  assert(/text-warn/.test(String(chip.className ?? "")),
    `the not-live chip is in the ordinary text colour ("${chip.className}") while ` +
    "the link-down ribbon beside it is !text-warn");
  assert(chip.querySelector("svg") != null,
    "the not-live chip has no icon, so it is colour-only — and the two states " +
    "it must be told apart from both carry one");
});

// ======================================================== the zoom controls
await test("the zoom cluster does not claim a zoom the stage is not doing", async () => {
  await standInOnStage();
  // The stand-in is painted in the empty-state early return, OUTSIDE the
  // `.preview-transform` layer, so these buttons move nothing. Before this the
  // readout stepped 100% -> 125% over a picture that did not budge.
  const zoomIn = container.querySelector('button[aria-label="Zoom in"]');
  assert(zoomIn != null, "no zoom control to inspect (precondition)");
  assert(zoomIn.getAttribute("aria-disabled") === "true",
    "the zoom controls are live beside a picture they cannot transform");
  assert((zoomIn.getAttribute("title") || "").length > 0,
    "the zoom control is inert with no stated reason — a dimmed button that " +
    "says nothing is the defect the honest-disabled rule exists to remove");
  // The readout, not the page text — "100%" is also a button label here.
  const readout = () => String(
    container.querySelector('span[aria-live="off"].tabular-nums')?.textContent ?? "");
  assert(readout().length > 0, "no zoom readout to inspect (precondition)");
  assert(!/\d/.test(readout()),
    `the toolbar reports a zoom of "${readout()}" over a stage that is not ` +
    "transforming anything");
  act(() => { zoomIn.click(); });
  await settle();
  assert(!/\d/.test(readout()),
    `a blocked press moved the readout to "${readout()}" while the picture stayed put`);
  assert(standIn() != null, "the press retired the stand-in");
});

await test("and it comes back with the live frame", async () => {
  // The control: gating on "no live frame" must not leave the cluster dead once
  // a real frame arrives.
  act(() => {
    useStore.setState({
      previews: [{
        id: 7,
        stats: { min: 0, max: 65535, mean: 900, median: 850, std: 120 },
        histogram: [1, 2, 3], histogram_domain: "display",
        exposure_s: 180, gain: 120, binning: 1,
        data_width: 6252, data_height: 4176,
        display_width: 1400, display_height: 935,
        mime: "image/jpeg", source: "camera",
        is_stretched: true, data_is_linear: false, has_lossless: false,
        full_well: null,
        auto_levels: { black: 0, mid: 0.5, white: 1 },
        ts: NOW_S,
      }],
      livePreviewId: 7,
    } as never);
  });
  await settle();
  const zoomIn = container.querySelector('button[aria-label="Zoom in"]');
  assert(zoomIn != null, "no zoom control (precondition)");
  assert(zoomIn.getAttribute("aria-disabled") == null,
    "the zoom controls stayed locked over a live frame");
  const readout = String(
    container.querySelector('span[aria-live="off"].tabular-nums')?.textContent ?? "");
  assert(/^\d+%$/.test(readout),
    `the zoom readout never came back — it reads "${readout}"`);
});

// -------------------------------------------------------------------- report
if (root) act(() => { root!.unmount(); });
const total = passed + failed;
console.log(`captureStandInStaleness: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
