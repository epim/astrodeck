// captureDom.test.tsx — CaptureView MOUNTED, driven by status frames.
//
//   Run directly:  npx tsx src/views/__tests__/captureDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHY A MOUNTED TEST. All three defects here are about STATE OVER TIME — what
// the screen says between one 2 s status frame and the next — so inspecting a
// single render tree cannot see any of them:
//
//   #11  a capture stopped from another device left this screen counting a
//        frame nobody was taking down to zero, then accusing a healthy camera
//        of dropping it 60 s later.
//   #13  the cooler set-point box was a hardcoded "-10" that never looked at
//        the camera, so Set commanded -10 at a rig holding -20.
//   #12  the dew slider committed on mouseup/touchend only, so a keyboard user
//        watched the number climb and sent nothing.
//
// EVERY test below states its PRECONDITION as its own assertion first. "The bar
// is gone" and "no request was sent" are both true of a screen that never
// rendered and of a control that was never reached, so each is preceded by
// proof that the state it denies was actually reached.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
// Installed BEFORE the store, the api client or the view are imported: BASE
// reads window.location at module scope and several components read matchMedia.
// run-tests.mjs gives this file its own process, so these globals cannot leak.
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

// The one network seam. Every api.* call in the view funnels through fetch, so
// recording it here is how "did the tap actually send anything" is answered.
const posts: { path: string; body: any }[] = [];
win.fetch = async (input: any, init: any = {}) => {
  const path = String(input);
  if ((init.method ?? "GET") === "POST") {
    posts.push({ path, body: init.body ? JSON.parse(init.body) : null });
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
  "localStorage", "requestAnimationFrame", "cancelAnimationFrame",
  "getComputedStyle", "matchMedia", "WebSocket", "fetch",
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
const CaptureView = (await import("../CaptureView")).default;

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
/** AWAITED — half these tests drive a POST, and an un-awaited async body both
 *  scores itself as a pass before it has asserted anything and crashes the
 *  process on rejection. */
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

/** A connected imaging camera with a cooler, plus whatever lanes the rig says
 *  are in flight. `targetC` is the camera's OWN set-point — the number #13 is
 *  about; `lanes: undefined` is a server too old to publish them at all.
 *
 *  `coolerOn` and `warm` exist because `target_c` is the DRIVER's live
 *  set-point, and hub._warm_ramp walks it from the user's number up to ambient
 *  (re-asserting set_cooler(True, setpoint) every 15 s) and then leaves it
 *  there with the cooler off. Those are real published states, not hypotheses. */
function seed(opts: {
  lanes?: string[]; looping?: boolean; targetC?: number | null;
  coolerOn?: boolean; warm?: any;
} = {}): void {
  useStore.setState({
    status: {
      connected: { camera: { connected: true, name: "sim cam" } },
      looping: !!opts.looping,
      mode: "sim",
      busy_lanes: opts.lanes,
      camera: {
        temperature: -4.2, can_cool: true, has_dew_heater: true,
        width: 1000, height: 800, max_gain: 300, max_bin: 2,
        warm: opts.warm,
        cooler: opts.targetC == null ? undefined : {
          on: opts.coolerOn ?? true, power: 40, target_c: opts.targetC,
          at_target: false, can_report_power: true,
        },
      },
    },
    principal: {
      role: "operator", email: null,
      caps: ["view.status", "control.capture"],
    },
  } as never);
}

seed({ lanes: [], targetC: -20 });
const container = win.document.getElementById("root") as any;
const root = createRoot(container);
act(() => { root.render(createElement(CaptureView)); });

/** The 2 s status frame — real fields the view really subscribes to. */
const statusFrame = (opts: Parameters<typeof seed>[0]) => { act(() => { seed(opts); }); };
const text = () => String(container.textContent ?? "");
const byLabel = (label: string) =>
  container.querySelector(`[aria-label="${label}"]`) as any;
/** The capture progress meter — present only while a frame is running. */
const meter = () => container.querySelector('[role="meter"]') as any;
const toastTitles = () =>
  ((useStore.getState() as any).toasts ?? []).map((t: any) => String(t.title ?? ""));
const buttonNamed = (label: string) =>
  [...container.querySelectorAll("button")]
    .find((b: any) => b.textContent?.trim() === label) as any;
/** Find an <input> by the value it is currently showing. The cooler set-point
 *  box has no aria-label of its own (it is labelled by its <Field>), and its
 *  VALUE is the thing under test, so this is the honest handle for it. */
const inputShowing = (v: string) =>
  [...container.querySelectorAll("input")].find((i: any) => i.value === v) as any;

/** Type into a controlled input the way a user does — React listens to the
 *  native `input` event, and only sees it if the value was set through the
 *  prototype setter it patched. */
function typeInto(node: any, value: string): void {
  act(() => {
    const setter = Object.getOwnPropertyDescriptor(
      win.HTMLInputElement.prototype, "value")!.set!;
    setter.call(node, value);
    node.dispatchEvent(new win.Event("input", { bubbles: true }));
  });
}
function click(node: any): void {
  act(() => {
    node.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
  });
}
/** Flush the awaits inside `arm()` / `act()` — the POST resolves on a
 *  microtask and the state it sets lands a tick later. */
async function settle(): Promise<void> {
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
}

// ------------------------------------------------------------- the fixture
await test("the view mounted with a cooled camera and a dew heater", () => {
  // The anti-blank-page guard: several assertions below are about something
  // being ABSENT, and would pass vacuously against a screen that never rendered.
  assert(/Exposure/.test(text()), "no Exposure panel — the fixture never rendered CaptureView");
  assert(/Cooler/.test(text()), "no Cooler panel — cam.can_cool did not reach the view");
  assert(byLabel("dew heater power to set") != null,
    "no dew slider — cam.has_dew_heater did not reach the view");
});

// ------------------------------------------------------ #13 cooler set-point
await test("the set-point box seeds from the camera, not from a hardcoded -10", () => {
  assert(inputShowing("-10") == null,
    "the Target °C box still reads the hardcoded -10 while the camera is " +
    "holding -20 — Set would command that number and warm the sensor");
  assert(inputShowing("-20") != null,
    "no field is showing the camera's own set-point of -20");
});

await test("the box keeps following the camera when its set-point changes", () => {
  statusFrame({ lanes: [], targetC: -15 });
  assert(inputShowing("-15") != null,
    "the box did not follow the camera to -15 — it is showing a stale set-point");
});

await test("a typed set-point survives the status frames that used to overwrite it", () => {
  const box = inputShowing("-15");
  assert(box != null, "no set-point box to type into (precondition)");
  typeInto(box, "-25");
  // PRECONDITION: the edit reached React's state, not just the DOM node.
  assert(box.value === "-25", "the typed value never reached the component");
  // The frames must actually MOVE the camera's set-point (-15 → -18 → -22),
  // or an unguarded tracking effect would never re-run and this would pass
  // whether or not the in-progress edit is respected. A moving set-point is
  // also the real case: another client, or a driver clamping the request.
  statusFrame({ lanes: [], targetC: -18 });
  statusFrame({ lanes: [], targetC: -22 });
  assert(box.value === "-25",
    "a status frame overwrote the set-point being typed — the tracking effect " +
    "does not respect an in-progress edit");
});

await test("pressing Set sends the typed number and hands the box back to the camera", async () => {
  posts.length = 0;
  const set = buttonNamed("Set") ?? buttonNamed("Cool");
  assert(set != null, "no Set/Cool button on a camera that can cool");
  click(set);
  await settle();
  const cool = posts.filter((p) => /cooler/.test(p.path));
  assert(cool.length === 1 && cool[0].body?.target_c === -25,
    `Set sent ${JSON.stringify(cool.map((c) => c.body))}, expected target_c -25`);
  statusFrame({ lanes: [], targetC: -25 });
  assert(inputShowing("-25") != null,
    "the box lost the value it just committed");
  statusFrame({ lanes: [], targetC: -12 });
  assert(inputShowing("-12") != null,
    "after a successful Set the box never resumed following the camera, so a " +
    "driver that clamped the request could never say so in the field");
});

// ------------------------------------------- #13 the set-point is not the ramp
// `cooler.target_c` is what the DRIVER is holding, which is only the user's
// number while the cooler is actually holding it. hub._warm_ramp re-asserts
// set_cooler(True, setpoint) every 15 s while walking that set-point from -20 up
// to ambient, so a tracking effect that follows it unconditionally hands the two
// cooler buttons the ramp's number instead of the user's.
await test("a warm ramp does not drag the set-point box up with it", async () => {
  statusFrame({ lanes: [], targetC: -20 });
  // PRECONDITION: the box IS following the camera right now (the Set above
  // handed it back), so "it did not move" below is a decision, not inertia.
  assert(inputShowing("-20") != null,
    "the box is not following the camera at -20, so this test proves nothing");
  const ramp = (setpoint: number) => ({
    active: true, ramped: true, start_c: -20, ambient_c: 12,
    setpoint_c: setpoint, temp_c: setpoint + 0.4, ambient_from: "assumed",
    eta_s: 300, rate_c_per_min: 1.7,
  });
  statusFrame({ lanes: [], targetC: -14.5, warm: ramp(-14.5) });
  statusFrame({ lanes: [], targetC: -3.2, warm: ramp(-3.2) });
  // PRECONDITION: the ramp really reached the screen — otherwise the box could
  // be holding -20 simply because nothing changed.
  assert(/Warming/.test(text()), "no warm ramp on screen — the fixture never ramped");
  assert(inputShowing("-20") != null,
    `the box followed the warm ramp (it now reads ` +
    `${[...container.querySelectorAll("input")].map((i: any) => i.value).join("/")}), ` +
    "so Set — pressed to abort a ramp, which the server documents as an expected " +
    "flow — would command the ramp's own set-point instead of the user's");
  posts.length = 0;
  const set = buttonNamed("Set");
  assert(set != null, "no Set button while the cooler is on mid-ramp");
  click(set);
  await settle();
  const cool = posts.filter((p) => /cooler/.test(p.path));
  assert(cool.length === 1 && cool[0].body?.target_c === -20,
    `Set sent ${JSON.stringify(cool.map((c) => c.body))}, expected target_c -20 — ` +
    "the number the user is looking at");
});

await test("after the ramp the box still holds a cooling set-point, not room temperature", async () => {
  // The ramp finished: the cooler is OFF and the driver keeps reporting the last
  // set-point it was asked for, which is ambient. This state persists — it is
  // what the panel shows the NEXT EVENING.
  statusFrame({
    lanes: [], targetC: 12, coolerOn: false,
    warm: { active: false, ramped: true, note: "sensor reached 11.6 °C" },
  });
  // PRECONDITION: we really are in the post-warm state, not still cooling.
  assert(buttonNamed("Cool") != null,
    "the cooler still reads as ON, so this is not the post-warm case");
  assert(inputShowing("12") == null,
    "the box took the driver's post-ramp set-point — it is showing room " +
    "temperature, and Cool would switch the TEC on against an above-ambient target");
  assert(inputShowing("-20") != null, "the box lost the user's set-point entirely");
  posts.length = 0;
  click(buttonNamed("Cool"));
  await settle();
  const cool = posts.filter((p) => /cooler/.test(p.path));
  assert(cool.length === 1 && cool[0].body?.target_c === -20,
    `Cool sent ${JSON.stringify(cool.map((c) => c.body))}, expected target_c -20`);
});

// --------------------------------------------------------- #12 dew keyboard
await test("a keyboard change to the dew slider is actually sent", async () => {
  const slider = byLabel("dew heater power to set");
  assert(slider != null, "no dew slider (precondition)");
  posts.length = 0;
  typeInto(slider, "65");
  // PRECONDITION: the slider really moved. Without this, "a request was sent"
  // could be satisfied by any other control on the screen.
  assert(slider.value === "65", "the dew slider did not take the change");
  assert(posts.length === 0,
    "the dew heater was sent on every keystroke — the draft-then-commit shape " +
    "is gone and a keyboard sweep would flood the link");
  act(() => {
    slider.dispatchEvent(new win.KeyboardEvent("keyup", { key: "ArrowUp", bubbles: true }));
  });
  await settle();
  const dew = posts.filter((p) => /dew-heater/.test(p.path));
  assert(dew.length === 1,
    `releasing an arrow key sent ${dew.length} dew-heater requests, expected 1 — ` +
    "a keyboard user's change never reaches the heater");
  assert(dew[0].body?.power === 65, `sent power=${dew[0].body?.power}, expected 65`);
});

await test("the panel says whose number the dew level is", () => {
  // PRECONDITION: the send above landed, so there IS something to attribute.
  assert(/Last set to 65%/.test(text()),
    `after a successful send the panel does not say what was set. Text: ${text().slice(0, 300)}`);
  assert(/no read-back/i.test(text()),
    "the panel presents the dew number as a reading from the camera; nothing " +
    "in the stack reports one, which is why a reload used to show 0% under a " +
    "heater that was still running");
});

// ------------------------------------------------------- #11 external abort
await test("an accepted Single arms the progress bar (precondition)", async () => {
  statusFrame({ lanes: [], targetC: -12 });
  const single = buttonNamed("Single");
  assert(single != null, "no live Single button — the capture controls are blocked");
  posts.length = 0;
  click(single);
  await settle();
  assert(posts.some((p) => /\/api\/capture$/.test(p.path)),
    "pressing Single sent no capture request");
  assert(meter() != null,
    "no progress meter after an accepted Single — the bar never armed, so " +
    "every assertion below about it stopping would be vacuous");
  assert(/exposing/i.test(text()), "the bar is up but not in the exposing phase");
});

await test("the rig taking the capture lane leaves the bar running", () => {
  statusFrame({ lanes: ["capture"], targetC: -12 });
  assert(meter() != null,
    "the bar vanished while the rig was REPORTING the capture in flight — the " +
    "lane handover killed the very frame it is meant to follow");
});

await test("the lane disappearing retires the bar instead of accusing the camera", () => {
  const before = toastTitles().length;
  statusFrame({ lanes: [], targetC: -12 });   // stopped from another device
  assert(meter() == null,
    "the bar is still running after the rig stopped capturing — it will count " +
    "down a frame nobody is taking and then blame the camera for dropping it");
  assert(!/exposing|downloading/i.test(text()),
    "the phase caption still narrates an exposure that is over");
  const said = toastTitles().slice(before);
  assert(said.some((m: string) => /stopped on the rig/i.test(m)),
    `the screen said nothing about why the frame ended. Toasts: ${JSON.stringify(said)}`);
  assert(!said.some((m: string) => /dropped the frame/i.test(m)),
    "the readout-timeout accusation fired for a capture that was simply stopped");
});

await test("a stale idle frame cannot kill a bar armed since (the handover guard)", async () => {
  // The 2 s gap. A frame that predates our POST says nothing about our POST;
  // acting on it is how a naive `busy_lanes` read makes every Single flicker.
  const single = buttonNamed("Single");
  assert(single != null, "Single is not live again after the abort");
  posts.length = 0;
  click(single);
  await settle();
  assert(meter() != null, "the second Single never armed the bar (precondition)");
  statusFrame({ lanes: [], targetC: -12 });   // the pre-POST frame, arriving late
  assert(meter() != null,
    "an idle status frame retired a bar armed 40 ms earlier — the lane must be " +
    "SEEN live before its absence means anything");
});

await test("a frame that ended the honest way disarms the guard for the next one", async () => {
  // The guard says "the lane must be SEEN before its absence means anything".
  // A frame that completes the honest way — a new preview id — never delivers a
  // lane-ABSENT frame, so nothing clears "seen" and the NEXT exposure inherits
  // it: one status frame serialised before that POST then kills a bar armed 40
  // ms earlier and blames the rig for it.
  statusFrame({ lanes: ["capture"], targetC: -12 });
  // PRECONDITION 1: the lane really has been seen live this time round.
  assert(meter() != null, "the lane frame retired the bar — nothing to follow");
  act(() => { useStore.setState({ livePreviewId: 4242 } as never); });
  // PRECONDITION 2: the frame really landed and retired the bar by preview id,
  // which is the completion path that leaves the flag set.
  assert(meter() == null, "the new preview id did not retire the bar");
  const single = buttonNamed("Single");
  assert(single != null, "Single is not live after the frame landed");
  posts.length = 0;
  click(single);
  await settle();
  assert(posts.some((p) => /\/api\/capture$/.test(p.path)), "the second Single sent nothing");
  assert(meter() != null, "the second Single never armed the bar (precondition)");
  const before = toastTitles().length;
  statusFrame({ lanes: [], targetC: -12 });   // the pre-POST frame, arriving late
  assert(meter() != null,
    "a status frame older than this POST killed the bar — 'seen the lane' has to " +
    "mean 'seen since THIS exposure', and the previous frame ended without ever " +
    "clearing it");
  assert(!toastTitles().slice(before).some((m: string) => /stopped on the rig/i.test(m)),
    "the screen accused the rig of stopping a frame it had just started");
});

await test("a server too old to publish lanes leaves the bar alone", () => {
  // useBusy returns FALSE on such a server; a control that read that as "idle"
  // would be permanently broken there instead of merely un-improved.
  assert(meter() != null, "no bar to test the old-server path against (precondition)");
  statusFrame({ lanes: undefined, targetC: -12 });
  assert(meter() != null,
    "the bar was retired by a server that publishes no busy_lanes at all — an " +
    "unknown answer was treated as 'idle'");
});

// ------------------------------------------------------------------ report
act(() => { root.unmount(); });
const total = passed + failed;
console.log(`captureDom: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
