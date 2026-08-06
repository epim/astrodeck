// captureRestDom.test.tsx — CaptureView MOUNTED: does each control REST when
// resting is right, and STAY ENGAGED when staying engaged is what it means?
//
//   Run directly:  npx tsx src/views/__tests__/captureRestDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// Separate from captureDom.test.tsx (which owns the #11/#12/#13 set) because
// these are a different question about the same screen — the press-and-release
// sweep of 2026-08-05 — and four findings landed here at once:
//
//   LOOP / LIVE VIEW had no engaged state until the rig confirmed. `pending` was
//     dropped in arm()'s `finally`, ~40 ms after the tap, while `status.looping`
//     / `status.live_stack_active` are a 2 s status frame behind it. In between,
//     the loop WAS running and its button looked untouched — and the second tap
//     is not a duplicate: hub.start_loop cancels the exposure in progress, and a
//     second Live View start assigns a fresh LiveStacker, discarding the stack.
//
//   PRESET marked the last preset you TAPPED. `aria-selected` plus the • glyph
//     is a selection assertion, and Suggest settings / Match last lights / the
//     darks prefill / any hand edit left it standing over settings that were no
//     longer the preset's.
//
//   "SET" wore aria-pressed + accent chrome off `cooler.on` — lit because the
//     cooler was running, which is the one thing that button does not control —
//     so the press that was still owed looked already applied.
//
//   In-flight SINGLE carried aria-disabled AND aria-pressed, announcing a
//     momentary shutter as a latched, unavailable toggle.
//
// EVERY test states its PRECONDITION as its own assertion first: "nothing is
// engaged" and "no request was sent" are both true of a screen that never
// rendered, so each is preceded by proof that the state it denies was reached.

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
  "PointerEvent",
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
/** AWAITED — most of these drive a POST, and an un-awaited async body both
 *  scores itself as a pass before it has asserted anything and crashes the
 *  process on rejection. */
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

/** A connected imaging camera with a cooler, plus whatever the rig says is in
 *  flight. `looping` / `liveStack` are the SERVER truths the Loop and Live View
 *  buttons render from — the ones that arrive a status frame after the tap. */
function seed(opts: {
  lanes?: string[]; looping?: boolean; liveStack?: boolean;
  targetC?: number | null; coolerOn?: boolean;
} = {}): void {
  useStore.setState({
    status: {
      connected: { camera: { connected: true, name: "sim cam" } },
      looping: !!opts.looping,
      live_stack_active: !!opts.liveStack,
      mode: "sim",
      busy_lanes: opts.lanes ?? [],
      camera: {
        temperature: -19.4, can_cool: true, has_dew_heater: false,
        width: 1000, height: 800, max_gain: 300, max_bin: 2,
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

seed({ targetC: -20 });
const container = win.document.getElementById("root") as any;
const root = createRoot(container);
act(() => { root.render(createElement(CaptureView)); });

/** The 2 s status frame — real fields the view really subscribes to. */
const statusFrame = (opts: Parameters<typeof seed>[0]) => { act(() => { seed(opts); }); };
const text = () => String(container.textContent ?? "");
const byLabel = (label: string) =>
  container.querySelector(`[aria-label="${label}"]`) as any;
const buttons = () => [...container.querySelectorAll("button")] as any[];
const buttonNamed = (label: string) =>
  buttons().find((b: any) => b.textContent?.trim() === label) as any;
const labelled = (re: RegExp) =>
  buttons().find((b: any) => re.test(b.getAttribute("aria-label") ?? "")) as any;
/** The input inside the <Field> with this label — `Field` renders a wrapping
 *  <label>, so this is the honest handle for a box that has no aria-label. */
const fieldInput = (labelText: string) => {
  const lab = [...container.querySelectorAll("label")].find((l: any) =>
    (l.querySelector("span.label")?.textContent ?? "").trim().startsWith(labelText));
  return lab?.querySelector("input.field") as any;
};
/** The rows of the open preset listbox. */
const options = () => [...container.querySelectorAll('[role="option"]')] as any[];
const optionNamed = (name: string) =>
  options().find((o: any) => (o.textContent ?? "").includes(name)) as any;

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
await test("the view mounted with a cooled camera and live capture controls", () => {
  // The anti-blank-page guard: most assertions below are about a control being
  // absent or unmarked, which is vacuously true of a screen that never rendered.
  assert(/Exposure/.test(text()), "no Exposure panel — the fixture never rendered CaptureView");
  assert(/Cooler/.test(text()), "no Cooler panel — cam.can_cool did not reach the view");
  assert(buttonNamed("Single") != null && buttonNamed("Loop") != null,
    "the capture controls are blocked — every press below would be testing a lock");
});

// ------------------------------------------- the preset picker is a RADIO set
await test("picking a preset loads its four values and marks that row", () => {
  const trigger = byLabel("Preset — custom");
  assert(trigger != null,
    `the collapsed picker does not read "custom" over settings that are no ` +
    `preset. Buttons: ${buttons().map((b: any) => b.getAttribute("aria-label")).join(" | ")}`);
  click(trigger);
  const galaxy = optionNamed("Galaxy");
  assert(galaxy != null, "the listbox did not open (precondition)");
  click(galaxy);
  // PRECONDITION: the pick really rewrote the boxes, so a mark on that row
  // afterwards is a claim about settings that ARE loaded.
  assert(fieldInput("Exposure")?.value === "120",
    `Galaxy did not load its exposure (box reads ${fieldInput("Exposure")?.value})`);
  assert(fieldInput("Gain")?.value === "100", "Galaxy did not load its gain");
  assert(byLabel("Preset — Galaxy") != null,
    "the collapsed picker does not name the preset that is loaded");
});

await test("an edit that leaves the preset behind clears the mark, not just the label", () => {
  click(byLabel("Preset — Galaxy"));
  const galaxy = optionNamed("Galaxy");
  assert(galaxy != null, "the listbox did not open (precondition)");
  // PRECONDITION: the row really is marked right now, so "nothing is marked"
  // below is a change of state and not the state it started in.
  assert(galaxy.getAttribute("aria-selected") === "true",
    "the Galaxy row is not selected while Galaxy's own values are loaded");
  assert((galaxy.textContent ?? "").includes("•"),
    "the selected row carries no • glyph — night mode kills hue as a channel");
  // Now type an exposure that is not Galaxy's. The listbox stays open: React
  // re-renders it in place, and nothing here dispatches the pointerdown its
  // outside-press listener watches for.
  typeInto(fieldInput("Exposure"), "33");
  assert(fieldInput("Exposure")?.value === "33", "the typed exposure never landed");
  const marked = options().filter((o: any) => o.getAttribute("aria-selected") === "true");
  assert(marked.length === 0,
    `${marked.length} preset row(s) still announce themselves as selected over ` +
    `settings none of them describes: ${marked.map((o: any) => o.textContent).join(", ")}`);
  assert(byLabel("Preset — custom") != null,
    "the collapsed picker still names a preset that is not in effect");
  click(byLabel("Preset — custom")); // close the listbox for the tests below
  assert(options().length === 0, "the listbox is still open (teardown)");
});

// --------------------------------------------- LOOP: engaged until the rig says
await test("an accepted Loop stays engaged through the status frame that has not caught up", async () => {
  posts.length = 0;
  const loop = buttonNamed("Loop");
  assert(loop != null, "no live Loop button (precondition)");
  click(loop);
  await settle();
  // PRECONDITION 1: the request really went, so the rig IS looping from here.
  const started = posts.filter((p) => /\/api\/capture\/loop$/.test(p.path));
  assert(started.length === 1,
    `Loop sent ${started.length} requests to /api/capture/loop, expected 1`);
  // PRECONDITION 2: the engaged state was actually reached, so "it is still
  // engaged" below cannot pass on a control that never changed.
  const busy = labelled(/the loop has been requested/);
  assert(busy != null,
    `Loop did not report itself as starting after the POST was accepted. ` +
    `Buttons: ${buttons().map((b: any) => b.textContent?.trim()).join(" | ")}`);
  assert(busy.getAttribute("aria-busy") === "true",
    "the starting Loop is not marked aria-busy");
  assert(busy.getAttribute("aria-disabled") === "true",
    "the starting Loop is still offered as pressable to assistive tech");
  assert(busy.getAttribute("aria-pressed") == null,
    "a start that the rig has not confirmed is being announced as a pressed toggle");

  // The 2 s gap: a status frame arrives that does not yet report the loop.
  statusFrame({ targetC: -20, looping: false });
  assert(buttonNamed("Loop") == null,
    "Loop went back to looking ready and pressable before the rig confirmed the " +
    "loop — the second tap this invites reaches hub.start_loop, which cancels " +
    "the exposure in progress and throws it away");
  posts.length = 0;
  click(labelled(/the loop has been requested/));
  await settle();
  assert(posts.filter((p) => /capture\/loop/.test(p.path)).length === 0,
    "a second tap inside the gap started another loop, discarding the sub in flight");
});

await test("the rig reporting the loop hands the engaged state over to server truth", () => {
  statusFrame({ targetC: -20, looping: true });
  const looping = buttonNamed("Looping…");
  assert(looping != null,
    "the loop is running on the rig and no control says so");
  assert(looping.getAttribute("aria-pressed") === "true",
    "Looping is a latched mode whose off switch is Stop — it must declare " +
    "aria-pressed, and the fix for Single's false toggle must not have taken it");
  assert(labelled(/the loop has been requested/) == null,
    "the click-side latch is still up underneath the server's own answer");
});

await test("the loop ending gives the button back", () => {
  statusFrame({ targetC: -20, looping: false });
  assert(buttonNamed("Looping…") == null, "the loop ended and the button still claims it (precondition)");
  assert(buttonNamed("Loop") != null,
    "Loop never came back after the loop ended — the latch outlived the run");
});

// --------------------------------------- LIVE VIEW: the same, with a stack to lose
await test("an accepted Live View start stays engaged until the stack is reported", async () => {
  posts.length = 0;
  const live = buttonNamed("Live View");
  assert(live != null, "no live Live View button (precondition)");
  click(live);
  await settle();
  const started = posts.filter((p) => /livestack\/start$/.test(p.path));
  assert(started.length === 1,
    `Live View sent ${started.length} start requests, expected 1`);
  const busy = labelled(/Live View has been requested/);
  assert(busy != null,
    "Live View did not report itself as starting after the POST was accepted");
  assert(busy.getAttribute("aria-busy") === "true", "the starting Live View is not aria-busy");

  statusFrame({ targetC: -20, liveStack: false });   // the frame that has not caught up
  assert(buttonNamed("Live View") == null,
    "Live View looked ready again before the rig reported the stack — the second " +
    "tap re-enters the START branch and assigns a fresh LiveStacker, throwing " +
    "away everything accumulated so far");
  posts.length = 0;
  click(labelled(/Live View has been requested/));
  await settle();
  assert(posts.filter((p) => /livestack\/start/.test(p.path)).length === 0,
    "a second tap inside the gap restarted the stack from zero");
});

await test("the reported stack takes the engaged state over, and can still be turned off", async () => {
  statusFrame({ targetC: -20, liveStack: true });
  const on = buttonNamed("Live View · on");
  assert(on != null, "the stack is running on the rig and no control says so");
  assert(on.getAttribute("aria-pressed") === "true",
    "Live View is a real toggle over a persistent device state — it must declare it");
  posts.length = 0;
  click(on);
  await settle();
  assert(posts.some((p) => /livestack\/stop$/.test(p.path)),
    "pressing a lit Live View sent no stop — the off half of the toggle is inert");
  statusFrame({ targetC: -20, liveStack: false });
  assert(buttonNamed("Live View") != null, "Live View did not come back after the stop");
});

// ----------------------------------- SINGLE in flight is BUSY, not a pressed toggle
await test("an in-flight Single announces itself as busy, not as a latched toggle", async () => {
  posts.length = 0;
  const single = buttonNamed("Single");
  assert(single != null, "no live Single button (precondition)");
  click(single);
  await settle();
  assert(posts.some((p) => /\/api\/capture$/.test(p.path)), "pressing Single sent nothing");
  // PRECONDITION: the in-flight face really is on screen.
  const shot = buttonNamed("Exposing…");
  assert(shot != null,
    `Single is not in its in-flight state. Buttons: ` +
    `${buttons().map((b: any) => b.textContent?.trim()).join(" | ")}`);
  assert(shot.getAttribute("aria-disabled") === "true",
    "the in-flight Single is not marked unavailable (precondition)");
  assert(shot.getAttribute("aria-pressed") == null,
    "a momentary shutter is announced as a pressed toggle for the whole exposure — " +
    "aria-disabled + aria-pressed together says latched AND unavailable");
  assert(shot.getAttribute("aria-busy") === "true",
    "the in-flight Single lost its busy state along with the false pressed one");
});

// ------------------------------------------ COOL/SET is an apply, not a toggle
await test("Set does not wear a pressed state it can never release", () => {
  statusFrame({ targetC: -20, coolerOn: true, looping: false });
  // PRECONDITION: the cooler IS on — the state the button used to be lit by.
  assert(/Cooling/.test(text()), "the cooler is not on, so this proves nothing");
  const set = buttonNamed("Set");
  assert(set != null, "no Set button while the cooler is on (precondition)");
  assert(set.getAttribute("aria-pressed") == null,
    "Set is a one-way apply — its partner is the separate Warm button, so the " +
    "press can never un-press it, yet it announces itself as a pressed toggle");
  assert(!/btn-accent/.test(set.className),
    "Set is lit while there is nothing owed — the box already matches the camera");
});

await test("an unsent set-point lights the button that would send it, and says so", async () => {
  // "Target °C", not "Target" — the save-to-library block puts a "Target name"
  // field above it, and matching the shorter prefix silently tested that one.
  const box = fieldInput("Target °C");
  assert(box != null, "no cooler set-point box (precondition)");
  assert(box.value === "-20",
    `the box is not following the camera (it reads ${box.value}), so an edit ` +
    "away from the camera's number cannot be told from inertia");
  typeInto(box, "-25");
  assert(box.value === "-25", "the typed set-point never reached the component");
  const set = buttonNamed("Set");
  assert(set != null, "the Set button vanished on a valid edit");
  assert(/btn-accent/.test(set.className),
    "the button that would send the typed -25 is not lit while the camera is " +
    "still holding -20 — the only unsent state this control has is unmarked");
  assert(/holding -20\.0/.test(text()) && /-25/.test(text()),
    `the panel does not put the two numbers together. Text: ${text().slice(0, 400)}`);
  posts.length = 0;
  click(set);
  await settle();
  const cool = posts.filter((p) => /camera\/cooler/.test(p.path));
  assert(cool.length === 1 && cool[0].body?.target_c === -25,
    `Set sent ${JSON.stringify(cool.map((c) => c.body))}, expected target_c -25`);
  statusFrame({ targetC: -25, coolerOn: true });
  const after = buttonNamed("Set");
  assert(after != null && !/btn-accent/.test(after.className),
    "the camera took the new set-point and the button is still lit as if a " +
    "press were owed");
  assert(!/holding -20\.0/.test(text()), "the unsent-set-point note outlived the send");
});

// ------------------------------------------------------------------ report
act(() => { root.unmount(); });
const total = passed + failed;
console.log(`captureRestDom: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
