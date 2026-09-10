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
//  5. THE MODE SWITCH IS REAL and it is ROUTE STATE. `VIDEO · PLANETS` used to
//     be honest-disabled because the backend had no video surface; D-RIG-1
//     landed `imaging/video_routes.py`, so the chip now SWITCHES and the mode
//     lives in `?mode=video` - which is what the Sky hub's RECORD hand-off
//     depends on. The old assertion (the chip is aria-disabled and carries
//     VIDEO_LOCK_REASON) was REPLACED, not deleted: it asserted a shortfall
//     that no longer exists, and a test pinning a refusal to a feature that
//     shipped is a test that would have blocked the feature.
//     Honest-disabled is still asserted, once, where it is still true: the
//     whole screen for a viewer.
//
// Convention: jsdom by hand, createRoot + act, native events, printed tally plus
// the `{ passed, failed, total }` export (shell-and-tests.md section 4).

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------------ css stub
// VIDEO mode's area root imports `video.css` (the wave rule: `next.css` belongs
// to one task, every other area carries its own stylesheet beside its
// components). Node has no idea what a `.css` file is, so a synchronous load
// hook answers with an empty module. It has to run BEFORE any import that
// reaches one, which is why every import in this file is dynamic and below it.
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
const {
  NEEDS_CAPTURE_REASON, EXPOSURE_FIX_REASON, GAIN_FIX_REASON,
} = await import("../captureGate");
const { modeFromRoute, modeHash } = await import("../CaptureScreen");

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
const press = async (el: any) => {
  await act(async () => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true })); });
  await settle();
};
/** Type into a React-controlled input the way a finger does: set the value
 *  through the native setter React's own tracker watches, then dispatch `input`.
 *  Deliberately NO blur and NO Enter - those are the two events this whole
 *  section exists because iOS does not reliably deliver (r4 #1). */
const typeInto = async (el: any, value: string) => {
  const setter = Object.getOwnPropertyDescriptor(
    win.HTMLInputElement.prototype, "value",
  )!.set as (v: string) => void;
  await act(async () => {
    setter.call(el, value);
    el.dispatchEvent(new win.Event("input", { bubbles: true }));
  });
  await settle();
};
const captureExposure = () =>
  (useStore.getState() as any).frameSettings.capture.exposure_s as number;

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

// --------------------------------------------------------------- 2. the MODE
// REPLACES the old "VIDEO is honest-disabled and switches nothing" test. That
// assertion pinned a shortfall (`VIDEO_LOCK_REASON`) that D-RIG-1 closed; left
// in place it would have failed the moment the recorder shipped, which is the
// wrong way round for a test to earn its keep.

test("the mode is read off the route, so the Sky hand-off lands on the right half", () => {
  eq(modeFromRoute({}), "still", "no parameter is the bench, not a blank screen");
  eq(modeFromRoute({ mode: "video" }), "video", "?mode=video did not select VIDEO");
  eq(modeFromRoute({ mode: "vidoe" }), "still",
    "a typo in a shared link opened neither mode");
  // The hand-off's payload survives the switch: a mode chip that dropped
  // `target`/`ra`/`dec` would silently un-aim the screen.
  const h = modeHash({ target: "Jupiter", ra: "3.1", dec: "17.2" }, "video");
  assert(h.includes("mode=video"), "the VIDEO hash does not select VIDEO");
  assert(h.includes("target=Jupiter"), "the mode switch dropped the target");
  assert(h.includes("ra=3.1") && h.includes("dec=17.2"),
    "the mode switch dropped the coordinates the hand-off carried");
  assert(!modeHash({ target: "Jupiter" }, "still").includes("mode="),
    "switching back to STILL left ?mode= behind");
});

await testAsync("VIDEO · PLANETS switches modes - it is not a locked chip any more", async () => {
  const before = asks.length;
  const video = q('[data-testid="capture-video"]');
  assert(video != null, "the VIDEO · PLANETS control is not drawn at all");
  eq(video.getAttribute("aria-disabled"), null,
    "VIDEO is still honest-disabled - the recorder shipped and the chip must switch");
  await press(video);

  assert(win.location.hash.includes("mode=video"),
    "pressing VIDEO did not put the mode in the route");
  assert(q('[data-testid="rig-capture-video"]') != null,
    "VIDEO mode did not render after the chip was pressed");
  assert(q('[data-testid="capture-go"]') == null,
    "the still bench is still mounted under VIDEO - both halves own the camera");
  // The mode switch is navigation, not a command: nothing is sent to the rig.
  assert(!asks.slice(before).some((a) => a.method !== "GET"),
    "switching modes wrote to the rig");

  await press(q('[data-testid="capture-still"]'));
  assert(!win.location.hash.includes("mode=video"), "STILL did not clear the mode");
  assert(q('[data-testid="capture-go"]') != null, "the bench did not come back");
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

// ------------------------------- 4c. the draft the shutter reads (r4 #1, P0)
// The whole point: `useFrameDraft.commit()` REFUSES an invalid draft, so the
// committed store number can never be invalid - feed THAT to the gate and
// EXPOSURE_FIX_REASON / GAIN_FIX_REASON are unreachable in the product while a
// typed-but-unblurred value is shot at the OLD number with nothing on screen to
// say so. These four assertions are the ones that go red if the gate is ever
// fed `String(settings.exposure_s)` again.
await testAsync("a typed exposure that was never blurred is the one that gets shot", async () => {
  asks.length = 0;
  // 4b left the COUNT tile selected; the EXPOSURE draft box is the one under it.
  await press(q('[data-testid="tile-exposure"]'));
  const box = q('[data-testid="capture-entry-exposure"]');
  assert(box != null, "precondition: the EXPOSURE draft box is not on screen");
  await typeInto(box, "180");
  eq(captureExposure(), 2,
    "precondition: the draft committed itself without a blur, so this test proves nothing");

  const go = q('[data-testid="capture-go"]');
  eq(go.getAttribute("aria-disabled"), null, "precondition: CAPTURE is locked for a valid draft");
  // `fmtExposure` renders 180 s as "3m", so that string IS the 180 on screen.
  assert(/3m/.test(go.textContent || ""),
    "the primary still advertises the OLD exposure - the button says one number and shoots another");
  assert(/3m/.test(q('[data-testid="tile-exposure"]').textContent || ""),
    "the EXPOSURE tile still reads the committed number while the box beside it reads 180");
  await press(go);

  const posts = captureAsks();
  eq(posts.length, 1, "CAPTURE did not post exactly once");
  eq(posts[0].body.exposure_s, 180,
    "the rig was sent the committed number, not the 180 on screen");
  eq(captureExposure(), 180,
    "the press never committed the draft - every other surface still reads the old exposure");

  await press(q('[data-testid="capture-stop"]'));
});

await testAsync("an unbounded exposure honest-disables CAPTURE and reaches no rig", async () => {
  asks.length = 0;
  const box = q('[data-testid="capture-entry-exposure"]');
  await typeInto(box, "1e9");
  const go = q('[data-testid="capture-go"]');
  eq(go.getAttribute("aria-disabled"), "true",
    "CAPTURE stayed live over an exposure of 1e9 seconds");
  eq(go.getAttribute("title"), EXPOSURE_FIX_REASON,
    "CAPTURE is locked but does not say the exposure is why");
  assert(go.hasAttribute("disabled") === false,
    "CAPTURE used the native disabled attribute, which takes the reason out of the a11y tree");
  await press(go);
  eq(captureAsks().length, 0, "a refused exposure reached the rig anyway");
  eq(captureExposure(), 180, "the refused draft was committed into the store");
});

await testAsync("a BLANK exposure is refused the same way - not shot as 0 s", async () => {
  asks.length = 0;
  await typeInto(q('[data-testid="capture-entry-exposure"]'), "");
  const go = q('[data-testid="capture-go"]');
  eq(go.getAttribute("aria-disabled"), "true",
    "CAPTURE stayed live over an empty exposure box - CAP-02's blank frame, again");
  eq(go.getAttribute("title"), EXPOSURE_FIX_REASON, "the blank box does not say why");
  await press(go);
  eq(captureAsks().length, 0, "an empty exposure box reached the rig");
});

await testAsync("a gain past the sensor's ceiling is refused, with its own sentence", async () => {
  asks.length = 0;
  // Put a shootable exposure back first: the exposure guard outranks the gain
  // one, so a leftover blank box would make this assertion vacuous.
  await typeInto(q('[data-testid="capture-entry-exposure"]'), "30");
  await press(q('[data-testid="tile-gain"]'));
  const gainBox = q('[data-testid="capture-entry-gain"]');
  assert(gainBox != null, "precondition: the GAIN draft box is not on screen");
  await typeInto(gainBox, "9999");   // the fixture camera's max_gain is 500
  const go = q('[data-testid="capture-go"]');
  eq(go.getAttribute("aria-disabled"), "true", "CAPTURE stayed live over a gain of 9999");
  eq(go.getAttribute("title"), GAIN_FIX_REASON, "the over-range gain does not say why");
  await press(go);
  eq(captureAsks().length, 0, "an out-of-range gain reached the rig");

  // Back to a shootable bench for the sections below.
  await typeInto(gainBox, "120");
  await press(q('[data-testid="tile-exposure"]'));
  eq(q('[data-testid="capture-go"]').getAttribute("aria-disabled"), null,
    "the bench did not recover - the tests below would assert over a locked screen");
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
