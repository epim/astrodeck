// captureDom.test.tsx - RIG · CAPTURE, MOUNTED, pressed, and refused.
//
//   Run directly:  npx tsx src/next/hubs/rig/capture/__tests__/captureDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE FIVE THINGS THIS FILE IS FOR, and what goes wrong without each:
//
//  1. A PRECONDITION MARKER. A DOM test that asserts an absence over a blank
//     page passes forever (`verify-on-the-real-thing`). Every test below runs
//     after `rig-capture` has been found, and the refusal test runs after the
//     ACCEPTED case has drawn a bar - a positive control, so "no bar" can only
//     mean "the refusal suppressed it".
//  2. THE EXACT POST BODY. `exposure_s, gain, offset, binning, save, target,
//     frame_type` - CaptureView.tsx:442-450. A field quietly renamed or dropped
//     is a frame shot at the wrong settings with nothing on screen to say so.
//  3. THE arm() GUARANTEE: a REFUSED post draws NO progress. It used to draw
//     one, because the bar was armed before the POST and a 409 was read as "some
//     capture is genuinely in flight" - which is also what "no camera connected"
//     returns. Measured: a full 60 s download bar for a frame that never existed.
//  4. THE LOOP LATCH surviving a second tap. `/api/capture/loop` CANCELS the
//     exposure in progress, so a second tap inside the status frame silently
//     throws away a sub.
//  5. HONEST-DISABLED, twice: VIDEO · PLANETS, which the backend cannot do, and
//     the whole screen for a viewer. Both must SAY why and fire nothing.
//
// Convention: jsdom by hand, createRoot + act, native events, printed tally plus
// the `{ passed, failed, total }` export (shell-and-tests.md section 4).

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
// PreviewStage measures itself with one; jsdom has no layout and no observer.
win.ResizeObserver = class {
  observe() {} unobserve() {} disconnect() {}
};
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
  // NOT `performance`: this jsdom's Performance delegates to the global one, so
  // copying it makes `performance.now()` call itself until the stack blows.
  // Node's own is what `useArm` ticks on here.
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------- fetch recorder
interface Ask { url: string; method: string; body: any }
const asks: Ask[] = [];
let refuse = false;
g.fetch = async (url: any, init: any) => {
  const method = (init?.method ?? "GET").toUpperCase();
  let body: any = null;
  if (init?.body) { try { body = JSON.parse(init.body); } catch { body = init.body; } }
  asks.push({ url: String(url), method, body });
  if (refuse) {
    return {
      ok: false, status: 409, statusText: "Conflict",
      json: async () => ({ detail: "a capture is already running" }),
    };
  }
  return { ok: true, status: 200, statusText: "OK", json: async () => ({ ok: true }) };
};
const captureAsks = () => asks.filter((a) => /\/api\/capture(\?|$)/.test(a.url));
const loopAsks = () => asks.filter((a) => a.url.includes("/api/capture/loop"));

// ------------------------------------------ imports, AFTER the globals are set
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { CaptureScreen } = await import("../CaptureScreen");
const { VIDEO_LOCK_REASON, NEEDS_CAPTURE_REASON } = await import("../captureGate");

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
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
};

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
const q = (sel: string) => container.querySelector(sel) as any;

const OPERATOR = {
  role: "operator", email: "op@rig",
  caps: ["view.status", "view.preview", "control.capture", "control.mount"],
};
const VIEWER = { role: "viewer", email: null, caps: ["view.status", "view.preview"] };

function seed(over: Record<string, unknown> = {}): void {
  useStore.setState({
    principal: OPERATOR,
    equipConnected: true,
    wsPhase: "up",
    status: {
      connected: { camera: { connected: true, name: "sim" } },
      looping: false,
      live_stack_active: false,
      camera: {
        temperature: -10, can_cool: true, width: 1000, height: 1000,
        max_gain: 500, max_bin: 4,
        cooler: { on: true, power: 40, target_c: -10, at_target: true, can_report_power: true },
      },
    },
    // The bench's own numbers, seeded directly so no PUT /api/camera/frame-settings
    // rides along and muddies what the capture POST assertion is reading.
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
    ...over,
  } as never);
}

seed();
await act(async () => { root.render(createElement(CaptureScreen)); });
await settle();

// ------------------------------------------------------------ 1. the marker
test("the screen rendered - the precondition every assertion below rests on", () => {
  assert(q('[data-testid="rig-capture"]') != null,
    "no rig-capture marker: the fixture is wrong, not the component");
  assert(q('[data-testid="capture-go"]') != null, "no CAPTURE button on the bench");
  assert(q('[data-testid="capture-stage"]') != null, "no preview stage on the bench");
  assert(q('[data-testid="capture-cooler"]') != null, "the cooler row is missing");
});

test("the operator's CAPTURE button is live, not locked", () => {
  const go = q('[data-testid="capture-go"]');
  eq(go.getAttribute("aria-disabled"), null,
    "precondition: an operator with a camera found CAPTURE locked");
});

// --------------------------------------------------------------- 2. the VIDEO
await testAsync("VIDEO · PLANETS is aria-disabled, says why, and switches nothing", async () => {
  const before = asks.length;
  const video = q('[data-testid="capture-video"]');
  assert(video != null, "the VIDEO · PLANETS control is not drawn at all");
  eq(video.getAttribute("aria-disabled"), "true",
    "VIDEO looks live; a control that cannot act must be honest-disabled");
  eq(video.getAttribute("title"), VIDEO_LOCK_REASON, "VIDEO carries no reason");
  await act(async () => {
    video.dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
  });
  await settle();
  eq(asks.length, before, "pressing VIDEO reached the server");
  // The STILL half is still the selected one: nothing switched.
  const still = container.querySelectorAll(".nx-chip")[0] as any;
  eq(still.getAttribute("aria-pressed"), "true", "the STILL half stopped being selected");
  // and the reason reached the toast channel, rather than being swallowed.
  const toasts = (useStore.getState() as any).toasts as Array<{ title?: string }>;
  assert(toasts.some((t) => t.title === VIDEO_LOCK_REASON),
    "the video reason was not said anywhere - the press was swallowed");
});

// ------------------------------------------------- 3. the body, and the bar
await testAsync("CAPTURE posts /api/capture with the exact body, and arms the bar", async () => {
  const go = q('[data-testid="capture-go"]');
  await act(async () => { go.dispatchEvent(new win.MouseEvent("click", { bubbles: true })); });
  await settle();

  const posts = captureAsks();
  eq(posts.length, 1, "CAPTURE did not post exactly once to /api/capture");
  eq(posts[0].method, "POST", "the capture was not a POST");
  const body = posts[0].body;
  eq(JSON.stringify(Object.keys(body).sort()),
    JSON.stringify(["binning", "exposure_s", "frame_type", "gain", "offset", "save", "target"]),
    "the capture body's field set changed");
  eq(body.exposure_s, 2, "exposure_s");
  eq(body.gain, 120, "gain");
  eq(body.offset, 30, "offset");
  eq(body.binning, 1, "binning");
  eq(body.save, true, "save defaults to ON - the novice-safe default is to keep what you shot");
  eq(body.target, "", "target");
  eq(body.frame_type, "Light", "frame_type");

  // THE POSITIVE CONTROL for the refusal test below: an ACCEPTED post DOES draw
  // a bar. Without this, "no bar after a refusal" could mean "no bar, ever".
  assert(q('[data-testid="capture-progress"]') != null,
    "an accepted exposure drew no progress at all - the refusal test below would be vacuous");
});

await testAsync("STOP retires the bar and posts /api/capture/stop", async () => {
  const stop = q('[data-testid="capture-stop"]');
  assert(stop != null, "the primary did not become STOP while a frame was in flight");
  await act(async () => { stop.dispatchEvent(new win.MouseEvent("click", { bubbles: true })); });
  await settle();
  assert(asks.some((a) => a.url.includes("/api/capture/stop")), "STOP posted nothing");
  assert(q('[data-testid="capture-progress"]') == null, "the bar outlived STOP");
});

// ------------------------------------------------- 4. the arm() guarantee
await testAsync("a REFUSED capture draws NO progress bar", async () => {
  asks.length = 0;
  refuse = true;
  const go = q('[data-testid="capture-go"]');
  assert(go != null, "precondition: the primary is not CAPTURE again after STOP");
  await act(async () => { go.dispatchEvent(new win.MouseEvent("click", { bubbles: true })); });
  await settle();
  refuse = false;

  eq(captureAsks().length, 1, "precondition: the refused capture was never sent");
  assert(q('[data-testid="capture-progress"]') == null,
    "a refused exposure armed the progress bar - nothing was armed on the rig");
  assert(q('[data-testid="capture-go"]') != null,
    "the primary stayed STOP after a refusal, for a frame that never started");
});

// ------------------------------------------------------------- 4b. the COUNT
await testAsync("COUNT 3 is three POSTs, one per landed frame, and then it stops", async () => {
  asks.length = 0;
  // Pick COUNT on the readout grid, then take the `3` stop on the dial - the
  // two-step the design specifies ("tap a readout, drag the dial").
  await act(async () => {
    q('[data-testid="tile-count"]').dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
  });
  await settle();
  const stop3 = q('[data-testid="capture-dial-count"] [data-value="3"]');
  assert(stop3 != null, "precondition: the COUNT dial has no 3 stop");
  await act(async () => { stop3.dispatchEvent(new win.MouseEvent("click", { bubbles: true })); });
  await settle();
  assert(/CAPTURE 3 /.test(q('[data-testid="capture-go"]').textContent),
    "the primary does not say how many frames it will take");

  await act(async () => {
    q('[data-testid="capture-go"]').dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
  });
  await settle();
  eq(captureAsks().length, 1,
    "a COUNT batch fired more than one POST at once - `/api/capture` takes no count "
    + "and the extras would just queue behind the capture lock and 409");

  assert(/frame 1 of 3/.test(container.textContent),
    "the batch does not say which frame of how many is running");

  // A frame LANDS: a new live-preview id is the only honest completion signal.
  await act(async () => { useStore.setState({ livePreviewId: 101 } as never); });
  await settle();
  eq(captureAsks().length, 2, "the second frame of the batch was never fired");
  assert(/frame 2 of 3/.test(container.textContent),
    "the batch counter did not advance - a constant where it should vary");

  for (const id of [102, 103]) {
    await act(async () => { useStore.setState({ livePreviewId: id } as never); });
    await settle();
  }
  eq(captureAsks().length, 3, "the batch lost its remaining frames after the second POST");
  assert(q('[data-testid="capture-progress"]') == null, "the bar outlived the last frame");
  assert(q('[data-testid="capture-result"]') != null,
    "the batch finished without a result card");

  // Back to one frame for the tests below.
  await act(async () => {
    q('[data-testid="tile-count"]').dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
  });
  await settle();
  await act(async () => {
    q('[data-testid="capture-dial-count"] [data-value="1"]')
      .dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
  });
  await settle();
});

// --------------------------------------------------------- 5. the LOOP latch
await testAsync("LOOP posts once and the starting latch survives a second tap", async () => {
  asks.length = 0;
  const loop = q('[data-testid="capture-loop"]');
  await act(async () => { loop.dispatchEvent(new win.MouseEvent("click", { bubbles: true })); });
  await settle();
  eq(loopAsks().length, 1, "LOOP did not post to /api/capture/loop");

  // The rig has NOT yet confirmed (`status.looping` is still false), so the
  // latch is what stands between a second tap and a cancelled exposure.
  const loop2 = q('[data-testid="capture-loop"]');
  eq(loop2.getAttribute("aria-disabled"), "true",
    "LOOP was pressable again before the rig confirmed - a second tap cancels the exposure");
  await act(async () => { loop2.dispatchEvent(new win.MouseEvent("click", { bubbles: true })); });
  await settle();
  eq(loopAsks().length, 1, "a second LOOP tap reached the rig and cancelled the sub in flight");
});

// ------------------------------------------------------------ 6. the viewer
await testAsync("a viewer sees the reason on every verb and fires nothing", async () => {
  await act(async () => {
    const stop = q('[data-testid="capture-stop"]');
    if (stop) stop.dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
  });
  await settle();
  asks.length = 0;
  await act(async () => { useStore.setState({ principal: VIEWER } as never); });
  await settle();

  const go = q('[data-testid="capture-go"]');
  assert(go != null, "precondition: a viewer cannot see the CAPTURE button at all");
  eq(go.getAttribute("aria-disabled"), "true", "a viewer's CAPTURE button looks live");
  eq(go.getAttribute("title"), NEEDS_CAPTURE_REASON, "the reason is not on the button");
  eq(NEEDS_CAPTURE_REASON, "Capturing needs operator or admin access.",
    "the viewer's sentence drifted");

  for (const id of ["capture-go", "capture-loop", "capture-live", "capture-save"]) {
    const el = q(`[data-testid="${id}"]`);
    assert(el != null, `${id} was hidden from the viewer instead of locked`);
    eq(el.getAttribute("aria-disabled"), "true", `${id} is not locked for a viewer`);
    await act(async () => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true })); });
  }
  await settle();
  eq(asks.length, 0, "a viewer's presses reached the server");
});

act(() => { root.unmount(); });

const total = passed + failed;
console.log(`captureDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
