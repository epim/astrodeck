// focusHonesty.test.tsx — the Focus page MOUNTED, and asked the six questions
// the 2026-08-05 audit found it answering wrongly.
//
//   Run directly:  npx tsx src/views/__tests__/focusHonesty.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// Every one of them is a claim about state OVER TIME — what the control says
// after the rig answers, after a second finger, after somebody else takes the
// focuser — so none can be asserted from one rendered tree. Hence jsdom, a real
// root, and re-renders driven by the same store writes the WebSocket makes.
//
//   #43  A second nudge during a move 409'd off the `focuser` lane, and that
//        409 cleared the FIRST move's target, its distance-to-go and its stall
//        clock — a tap that changed nothing on the rig blinded the narrator
//        watching the move that was really happening.
//   #44  The commanded target was never retired, so the next thing the focuser
//        did — a sweep, a coarse walk, the sequencer — was narrated as that
//        finished command and ended in a false orange "not moving" fault.
//   #45  "Find focus roughly first" was the file's only native `disabled` and
//        it missed the two blockers that bite: over a loop or a sequence it
//        looked fully live and bought a 409.
//   #46  Tapping "Bahtinov focus" changed nothing for up to 2s, so the label
//        could flip under a second finger.
//   #47  Halt cleared the narration BEFORE the POST and never put it back.
//   #48  An exposure preset lit up and reported aria-pressed for a loop restart
//        that was silently dropped.
//
// WHAT THIS FIXTURE IS NOT: the preview stage is mounted but blind (no canvas,
// no layout in jsdom), and nothing here asserts anything about the picture —
// only about the controls and the sentences around it.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
// Before the store, the api client or the view are imported: api.ts reads
// window.location at module scope and several components read matchMedia while
// evaluating module-level constants.
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
// PreviewStage and FocusPod both measure themselves with one; jsdom ships none,
// and a throw inside that effect would fail the commit and take the page down.
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
win.HTMLCanvasElement.prototype.getContext = () => null;
win.URL.createObjectURL = () => "blob:stub";
win.URL.revokeObjectURL = () => {};

// ------------------------------------------------------- the fake rig backend
// Routed by path so a test can make ONE route fail without inventing an offline
// mode. `posts` is the record of what actually went to the rig, which is how
// these tests tell a refused tap from a silent one.
const posts: { path: string; body: any }[] = [];
/** path fragment -> HTTP status to answer with. Absent = 200. */
const failing = new Map<string, number>();
/** path fragment -> a promise the ANSWER waits on. The request is recorded in
 *  `posts` first, so a test can hold the round trip open, press the same button
 *  again inside it, and see whether the rig got two. Without this every POST
 *  resolves within one microtask and the pre-accept window cannot be entered at
 *  all — which is why nothing here caught it. */
const holds = new Map<string, Promise<void>>();
function respond(body: any, status = 200): any {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 409 ? "Conflict" : "OK",
    headers: { get: () => null },
    json: async () => body,
  };
}
win.fetch = async (url: any, init: any = {}) => {
  const path = String(url);
  const method = (init.method ?? "GET").toUpperCase();
  if (method === "POST") {
    posts.push({ path, body: init.body ? JSON.parse(init.body) : null });
    for (const [frag, gate] of holds) if (path.includes(frag)) await gate;
    for (const [frag, status] of failing) {
      if (path.includes(frag)) return respond({ detail: `'${frag}' is already running` }, status);
    }
    return respond({ started: "ok" });
  }
  return respond({});
};

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "localStorage", "requestAnimationFrame",
  "cancelAnimationFrame", "getComputedStyle", "matchMedia", "WebSocket",
  "ResizeObserver", "Image", "URL", "fetch", "Blob",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../store");
const FocusView = (await import("../FocusView")).default;

// ------------------------------------------------------------------ fixtures
interface Rig {
  position: number;
  moving: boolean;
  looping: boolean;
  busyLanes: string[];
  bahtinov: boolean;
  /** the filter wheel the rig reports, or null for a wheel-less rig */
  wheel: { position: number; names: string[]; opaque: boolean[] } | null;
}
const RIG: Rig = {
  position: 12000, moving: false, looping: false, busyLanes: [], bahtinov: false,
  wheel: null,
};

/** A connected camera and focuser, and an operator who may drive them — the only
 *  state in which these controls are controls at all (otherwise the page renders
 *  the locked stand-ins and every assertion below would be about the wrong
 *  element). `busyLanes` is the rig's own answer, which is the thing under test. */
function seed(over: Partial<Rig> = {}): void {
  Object.assign(RIG, over);
  useStore.setState({
    status: {
      connected: { camera: true, focuser: true }, mode: "sim",
      looping: RIG.looping,
      busy_lanes: RIG.busyLanes,
      bahtinov_active: RIG.bahtinov,
      camera: { max_gain: 500, max_bin: 4 },
      focuser: {
        position: RIG.position, max: 40000, moving: RIG.moving, temperature: 5.5,
      },
      ...(RIG.wheel ? { filterwheel: RIG.wheel } : {}),
    },
    principal: { role: "operator", email: null, caps: ["view.status", "control.capture"] },
  } as never);
}

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

seed();
const container = win.document.getElementById("root") as any;
const root = createRoot(container);
await act(async () => { root.render(createElement(FocusView)); });

/** The 2 s status frame — the same store write the WebSocket makes. */
async function statusFrame(over: Partial<Rig> = {}): Promise<void> {
  await act(async () => { seed(over); });
}
/** Let a POST → then/catch → setState chain settle, inside act so React commits
 *  whatever it produced. */
async function flush(): Promise<void> {
  await act(async () => {
    for (let i = 0; i < 5; i++) await Promise.resolve();
    await new Promise((r) => setTimeout(r, 0));
  });
}
function click(node: any): void {
  node.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
}
const text = (): string => container.textContent || "";
/** Any element (button, or a LockedChip's role=button trigger) whose accessible
 *  name starts with `prefix` — the two are deliberately interchangeable here,
 *  because "which of the two is rendered" is exactly what several of these
 *  tests are about. */
function byLabel(prefix: string): any {
  return [...container.querySelectorAll("[aria-label]")]
    .find((el: any) => (el.getAttribute("aria-label") || "").startsWith(prefix));
}
function byText(re: RegExp): any {
  return [...container.querySelectorAll('button, [role="button"]')]
    .find((el: any) => re.test(el.textContent || ""));
}
const plus = () => byLabel("Move focuser +100");
const halt = () => byText(/^Halt$/);
const coarse = () => byText(/Find focus roughly first/);
const bahtinov = () => byText(/Bahtinov focus|Arming…|Stopping…|Stop Bahtinov aid/);
const preset = (s: number) => byLabel(`${s} second exposure`);
const postsTo = (frag: string) => posts.filter((p) => p.path.includes(frag));
const blocked = (el: any) => el?.getAttribute("aria-disabled") === "true";

// -------------------------------------------------------------- preconditions
await test("the Focus page mounted with live focuser and camera controls", () => {
  // The anti-blank-page guard. Most assertions below are "X no longer does Y",
  // which a page that never rendered would pass without trying.
  assert(/Focuser/.test(text()), "the Focuser panel is not on the page");
  assert(plus() != null, "no +100 nudge button — the focuser/caps fixture is wrong");
  assert(!blocked(plus()), "the nudge starts blocked, so the tests below prove nothing");
  assert(coarse() != null, "no 'Find focus roughly first' control");
  assert(!blocked(coarse()), "coarse focus starts blocked with an idle rig");
});

// --------------------------------------------- #43 a second nudge mid-move
await test("a nudge the server accepts is narrated with its target", async () => {
  click(plus());
  await flush();
  assert(postsTo("/api/focuser/move").length === 1,
    `expected one move POST, saw ${postsTo("/api/focuser/move").length}`);
  assert(/→ 12100/.test(text()),
    "the accepted move is not narrated — the precondition for the next test");
});

// From here on the assertions ask for the TARGET, not for "→ 12100": past
// STALL_GRACE_MS of wall clock the same command legitimately re-words itself to
// "not moving — stopped at 12040, asked for 12100", and a test that fails
// because the box it runs on was slow is a test nobody trusts. What is under
// test is whether the commanded target survived at all.
await test("a second nudge mid-move neither reaches the rig nor blinds the first", async () => {
  await statusFrame({ position: 12040, moving: true });
  assert(/12100/.test(text()), "the narration vanished on the first status frame");
  assert(blocked(plus()),
    "the nudge is still live while a move is in flight; the tap it invites is a " +
    "409 off the `focuser` lane");
  assert(/still moving/.test(plus().getAttribute("aria-label") || ""),
    "the blocked nudge does not say why");

  const before = postsTo("/api/focuser/move").length;
  click(plus());
  await flush();
  assert(postsTo("/api/focuser/move").length === before,
    "a second move went to the rig while one was in flight");
  assert(/12100/.test(text()),
    "the second tap erased the first move's target and its stall clock — the " +
    "narrator is now blind to the move that is actually happening");
});

// ------------------------------------------------ #44 retiring the command
await test("an arrived move is confirmed, then not held against the next one", async () => {
  await statusFrame({ position: 12100, moving: false });
  assert(/at 12100/.test(text()), "arrival was not confirmed");
  assert(!blocked(plus()), "the nudge stayed blocked after the move arrived");

  // Somebody else takes the focuser: a sweep (or a coarse walk — same lane).
  await statusFrame({ busyLanes: ["autofocus"] });
  await statusFrame({ busyLanes: ["autofocus"], position: 18300, moving: true });
  assert(!/12100/.test(text()),
    "the sweep's own motion is still being narrated as the user's last Go — " +
    "which is how it ends in an orange 'not moving, asked for 12100' fault");
});

// ---------------------------------------------- #45 coarse focus vs the camera
await test("coarse focus is refused, with a reason, while a loop owns the camera", async () => {
  await statusFrame({ busyLanes: [], position: 18300, moving: false, looping: true });
  const c = coarse();
  assert(c != null, "the coarse-focus control vanished");
  assert(blocked(c),
    "'Find focus roughly first' is still live while a capture loop is running — " +
    "the route 409s on hub.looping, so this tap buys a red toast");
  assert(/loop/i.test(c.getAttribute("aria-label") || ""),
    `the refusal does not name the loop: ${c.getAttribute("aria-label")}`);
  const before = postsTo("/api/focuser/coarse").length;
  click(c);
  await flush();
  assert(postsTo("/api/focuser/coarse").length === before,
    "the blocked coarse control still posted to the rig");
});

// ------------------------------------------------- #48 a preset over a loop
await test("a preset over a running loop restarts it and only then lights up", async () => {
  const p = preset(3);
  assert(p != null, "no 3s preset button");
  assert(!blocked(p), "the preset is blocked with only a loop running");
  click(p);
  await flush();
  assert(postsTo("/api/capture/loop").length === 1,
    "tapping a preset over a running loop did not restart it, so the loop is " +
    "still shooting the old exposure");
  assert(preset(3).getAttribute("aria-pressed") === "true",
    "the restarted exposure is not the one shown as pressed");
});

await test("a preset the rig will not accept neither lights up nor posts", async () => {
  // A loop keeps running through an autofocus sweep (the server refuses a loop
  // only for polar and sequences), and the sweep owns the camera — so the
  // restart cannot be issued.
  await statusFrame({ busyLanes: ["autofocus", "looping"] });
  const p = preset(10);
  assert(blocked(p), "the preset is still live while the sweep owns the camera");
  const before = postsTo("/api/capture/loop").length;
  click(p);
  await flush();
  assert(postsTo("/api/capture/loop").length === before,
    "the blocked preset still posted a loop restart");
  assert(preset(10).getAttribute("aria-pressed") !== "true",
    "the preset lit up for an exposure the running loop is not using");
  assert(preset(3).getAttribute("aria-pressed") === "true",
    "the highlight left the exposure the loop is actually shooting");
});

await test("a preset restart the rig REFUSES puts the highlight back", async () => {
  await statusFrame({ busyLanes: [] });
  failing.set("/api/capture/loop", 409);
  click(preset(5));
  await flush();
  assert(postsTo("/api/capture/loop").length >= 2, "the restart was never attempted");
  assert(preset(5).getAttribute("aria-pressed") !== "true",
    "the preset stayed lit after the rig refused the restart");
  assert(preset(3).getAttribute("aria-pressed") === "true",
    "the highlight does not name the exposure the loop is still shooting");
  failing.delete("/api/capture/loop");
  await statusFrame({ looping: false });
});

// ------------------------------------------------------------- #46 Bahtinov
await test("arming the Bahtinov aid says so before the next status frame", async () => {
  const b = bahtinov();
  assert(b != null, "no Bahtinov button");
  assert(/Bahtinov focus/.test(b.textContent || ""), "the aid is already armed");
  click(b);
  await flush();
  assert(postsTo("/bahtinov/start").length === 1, "arming never reached the rig");
  assert(/Arming…/.test(bahtinov().textContent || ""),
    `the button still reads "${(bahtinov().textContent || "").trim()}" after the ` +
    "tap — for the next two seconds it looks exactly like not having pressed it");
  assert(bahtinov().getAttribute("aria-busy") === "true", "no aria-busy while arming");

  const before = postsTo("/bahtinov").length;
  click(bahtinov());
  await flush();
  assert(postsTo("/bahtinov").length === before,
    "a second finger sent another bahtinov command while the first was in flight");
});

await test("…and hands over to the rig's own answer when it lands", async () => {
  await statusFrame({ bahtinov: true });
  assert(/Stop Bahtinov aid/.test(bahtinov().textContent || ""),
    "the button did not follow the rig once it reported the aid armed");
});

// ----------------------------------------------------------------- #47 Halt
await test("a Halt that never reaches the rig keeps the move on screen", async () => {
  await statusFrame({ bahtinov: false, position: 12000, moving: false });
  click(plus());
  await flush();
  assert(/→ 12100/.test(text()), "no move to halt — the precondition failed");
  await statusFrame({ position: 12040, moving: true });

  failing.set("/api/focuser/halt", 500);
  click(halt());
  await flush();
  assert(/12100/.test(text()),
    "the halt was refused and the screen forgot the move anyway — no target, no " +
    "distance-to-go and no stall warning for a focuser that is still travelling");
  failing.delete("/api/focuser/halt");
});

await test("a Halt the rig takes does clear it — it is not a fault report", async () => {
  click(halt());
  await flush();
  assert(!/12100/.test(text()),
    "after a deliberate halt the panel still narrates the abandoned target, " +
    "which becomes 'not moving — stopped at 12040' six seconds later");
});

/** click + let React commit what the handler did SYNCHRONOUSLY, before any
 *  answer. The tests above can use bare `click` because everything they assert
 *  on lands after their POST resolves inside `flush`; the two below are about
 *  the window before it resolves, which is exactly that pre-answer commit. */
async function clickNow(node: any): Promise<void> {
  await act(async () => { click(node); });
}

// ------------------------------------- #43b the window before the rig answers
// `cmd` is armed only AFTER the POST resolves — deliberately, so a refused move
// is never drawn as a real one — which left the round trip itself uncovered:
// no `cmd`, so no `waiting`, so no blocked control and nothing on screen. On a
// rig that answers in 40ms that is invisible; on a busy one, or over the wifi
// this rig is actually driven on, it is seconds of a screen that looks exactly
// as it did before the tap, and a second tap inside it is a 409 off the
// `focuser` lane. Every other test in this file resolves its POST within a
// microtask, which is why the suite was green over it.
await test("a second tap inside the move's round trip cannot reach the rig", async () => {
  await statusFrame({ position: 12000, moving: false });
  assert(!blocked(plus()),
    "the nudge is already blocked before the tap — this test would pass vacuously");
  assert(!/sending/i.test(text()),
    "the page already says 'sending' with nothing in flight");

  let release: () => void = () => {};
  holds.set("/api/focuser/move", new Promise<void>((r) => { release = r; }));
  const before = postsTo("/api/focuser/move").length;

  // TWO TAPS IN ONE BATCH — the case a `useState` latch cannot catch, because
  // both handlers run against the same render closure and both read it as
  // null. This is a double tap on a tablet, or one finger and one stylus.
  await act(async () => { click(plus()); click(plus()); });
  await flush();
  assert(postsTo("/api/focuser/move").length === before + 1,
    postsTo("/api/focuser/move").length === before
      ? "the first tap never reached the rig — the hold is wired wrong"
      : "both taps of one double tap reached the rig; the second is a 409 off " +
        "the `focuser` lane, and only a ref written inside the handler can " +
        "stop it");
  // Visible, and honest about what it is: the rig has not agreed to anything
  // yet, so the arrow narration must NOT be up.
  assert(/sending 12100/.test(text()),
    "nothing on the page changed between the tap and the rig's answer — the " +
    "2026-07-31 complaint, at the width of a round trip");
  assert(!/→ 12100/.test(text()),
    "the move is narrated as under way before the server has accepted it");
  assert(blocked(plus()), "the nudge is still live inside its own round trip");
  assert(/sending/i.test(plus().getAttribute("aria-label") || ""),
    `the blocked nudge does not say why: ${plus().getAttribute("aria-label")}`);

  // …and a later, separate tap — the one the gate above has to turn away.
  await clickNow(plus());
  await flush();
  assert(postsTo("/api/focuser/move").length === before + 1,
    "a tap inside the round trip reached the rig too — that POST is a 409 off " +
    "the `focuser` lane");

  holds.delete("/api/focuser/move");
  // Inside act: releasing the gate resolves the POST, and everything React does
  // with that answer happens in the microtasks that follow it.
  await act(async () => {
    release();
    for (let i = 0; i < 5; i++) await Promise.resolve();
    await new Promise((r) => setTimeout(r, 0));
  });
  assert(/→ 12100/.test(text()),
    "the accepted move is not narrated once the rig answers");
  assert(!/sending/i.test(text()), "the sending line outlived the request");
});

await test("a REFUSED move hands the buttons straight back", async () => {
  // The latch must not survive its own request. A 409 or a dropped connection
  // leaves nothing in flight, so leaving the nudges locked behind it would be a
  // dead page with no way out but a reload.
  click(halt());   // retire the move the test above left travelling
  await flush();
  await statusFrame({ position: 12000, moving: false });
  assert(!blocked(plus()),
    "the nudge is blocked before the refused tap — this test would prove nothing");
  failing.set("/api/focuser/move", 409);
  await clickNow(plus());
  await flush();
  assert(!blocked(plus()),
    "the focuser controls stayed locked after the rig refused the move");
  assert(!/sending/i.test(text()), "the 'sending' line survived the refusal");
  failing.delete("/api/focuser/move");
});

// ------------------------------- what the CAMERA is exposing at, over a loop
/** A frame as the preview ring holds it. Only exposure_s/gain/binning are under
 *  test; the rest is filled so PreviewStage and FrameStats have a real record to
 *  render rather than a stub that throws inside a commit. */
function FRAME(id: number, exposureS: number): any {
  return {
    id,
    stats: { min: 0, max: 65535, mean: 1000, median: 900, std: 50 },
    histogram: new Array(64).fill(0),
    histogram_domain: "display",
    exposure_s: exposureS, gain: 200, binning: 1,
    data_width: 1000, data_height: 1000, display_width: 500, display_height: 500,
    mime: "image/jpeg", source: "sim",
    is_stretched: false, data_is_linear: true, has_lossless: false, full_well: null,
    auto_levels: { black: 0, mid: 0.5, white: 1 },
    hfr: 3.2, stars: 500,
  };
}
async function pushFrame(id: number, exposureS: number): Promise<void> {
  await act(async () => { (useStore.getState() as any).pushPreview(FRAME(id, exposureS)); });
}

await test("a loop this screen did not start owns the exposure claim", async () => {
  // `capExposure` is seeded to "2" and never seeded from the rig, so any loop
  // this box did not just start — one from the Capture screen, one from another
  // tablet, one that outlived a reload — leaves a filled, aria-pressed preset
  // making a claim about the CAMERA out of a local draft.
  assert(preset(3).getAttribute("aria-pressed") === "true",
    "the 3s preset is not lit going in, so 'it stops being lit' proves nothing");
  await statusFrame({ looping: true });
  assert(preset(3).getAttribute("aria-pressed") !== "true",
    "the box still claims the camera over a loop nothing on this screen started — and the "
    + "pod's badge over the picture repeats it");
  assert(![1, 2, 3, 5, 10].some((s) => preset(s).getAttribute("aria-pressed") === "true"),
    "some preset is lit while nothing on this screen knows what the loop is shooting");

  // …and the rig's own first frame settles it.
  await pushFrame(1, 10);
  assert(preset(10).getAttribute("aria-pressed") === "true",
    "the lit preset does not follow the exposure the loop is actually delivering");
  assert(preset(3).getAttribute("aria-pressed") !== "true",
    "two presets claim the camera at once");
  assert(/last frame 10s/.test(text()),
    "the panel's own provenance line does not say 10s — the fixture is wrong");
  await statusFrame({ looping: false });
});

// ------------------------------------------- the frame that never came (#B)
await test("a dropped frame hands the shutter back instead of latching Exposing…", async () => {
  await statusFrame({ busyLanes: [], moving: false });
  assert(byText(/^Single$/) != null,
    "no live Single button — every assertion below would be about the wrong element");

  // The only lever a unit test has on a sixty-second readout grace: have the
  // server accept the exposure in the past. `shoot` stamps `shotAt` with
  // Date.now() at acceptance and the narrator compares it against this view's
  // own one-second clock, which is restored to real time before it next ticks.
  const realNow = Date.now;
  try {
    Date.now = () => realNow() - 200_000;
    click(byText(/^Single$/));
    await flush();
  } finally { Date.now = realNow; }
  assert(posts.filter((p) => p.path.endsWith("/api/capture")).length === 1,
    "the tap never reached the rig");
  assert(byText(/Exposing…/) != null,
    "the accepted exposure is not shown as in flight — the precondition for the rest of this");

  // One tick of the view's own second-hand later, with no frame in sight. Only
  // a new preview id or a deliberate Stop ever cleared `shotAt`, and on a USB
  // drop, an aborted exposure or a camera error neither ever happens.
  await act(async () => { await new Promise((r) => setTimeout(r, 1200)); });
  assert(/may have dropped it/.test(text()),
    "the screen never reaches its own dropped-frame verdict — the fixture is wrong");
  assert(byText(/Exposing…/) == null,
    "the Single button is still latched on 'Exposing…', aria-pressed and inert, for a frame "
    + "the screen has ALREADY declared lost two lines below it");
  assert(byText(/^Single$/) != null,
    "the shutter was never handed back — the only way out was Stop or navigating away");
});

// ---------------------------- #188 the AF dial offered slots with no glass
// A blackout slot is a carrier with no glass. A sweep through one measures
// nothing at EVERY position — and because the engine only advances when a
// measurement is added, that is not a slow failure but the hang: the rig
// re-exposed one unmeasurable position fourteen times on 2026-08-08. Polar
// excludes these slots and so does FilterPicker; the two controls below did
// not.
await test("neither the settings dial nor the Filter select offers a blackout slot", async () => {
  await statusFrame({
    wheel: { position: 0, names: ["L", "Ha", "Dark"], opaque: [false, false, true] },
  });
  // Precondition: the rig really is reporting a blackout slot, or every
  // assertion below passes on a wheel that has nothing to exclude.
  assert((useStore.getState() as any).status.filterwheel.names.includes("Dark"),
    "the fixture never gave the rig a blackout slot");

  // The dial is "Focus frame settings" since #180 — it carries the CAMERA's
  // exposure/gain/binning (the shared `focus` scope) rather than a private copy
  // of the sweep's, and the FILT ring it has always had is still the sweep's
  // pin. The label moved; what this test grades did not.
  const dial = byLabel("Focus frame settings");
  assert(dial != null, "no camera-settings dial over the preview");
  click(dial);
  await flush();
  const cat = container.querySelector('[data-dial-item="filter"]');
  assert(cat != null, "the dial has no filter ring");
  click(cat);
  await flush();
  const rings = [...container.querySelectorAll("[data-dial-item]")]
    .map((el: any) => el.getAttribute("data-dial-item"));
  assert(rings.includes("L") && rings.includes("Ha"),
    `the filter ring lost the real filters: ${rings.join("|")}`);
  assert(!rings.includes("Dark"),
    `the dial offers a slot with no glass to sweep through: ${rings.join("|")}`);
  click(container.querySelector('[data-dial-item="__back"]') ?? dial);
  await flush();

  // …and the same list in the settings panel's <select>.
  const gear = byLabel("Autofocus settings");
  assert(gear != null, "no autofocus settings toggle");
  click(gear);
  await flush();
  const sel = [...container.querySelectorAll("select")].find((s: any) =>
    [...s.options].some((o: any) => o.textContent === "L"));
  assert(sel != null, "the Filter select never rendered");
  const opts = [...(sel as any).options].map((o: any) => o.textContent);
  assert(opts.includes("Ha"), `the select lost a real filter: ${opts.join("|")}`);
  assert(!opts.includes("Dark"),
    `the Filter select offers a blackout slot: ${opts.join("|")}`);
});

// ------------------------------------------------------------------- report
const {useExperience}=await import("../../guided/experience");
const {useGuidedSetup,observeSetupChecks}=await import("../../guided/setup");
const field={ra_hours:10,dec_deg:30};
await act(async()=>{
  useStore.setState({focus:null,telemetryStale:false,wsPhase:"up",status:{...useStore.getState().status,mount:{...field,slewing:false}}} as never);
  useGuidedSetup.setState({field,location:true,horizon:true,focus:false});
  useExperience.setState({mode:"guided",home:false,wizard:"focus"});
});
await test("Guided camera lesson is separate from the live workspace",()=>{
  assert(!!byText(/Open camera view/),"lesson has no route to live camera");
  assert(container.querySelector('.guided-focus-workspace')?.hidden,"camera workspace should not be underneath the lesson");
});
await act(async()=>click(byText(/Open camera view/)));
await test("opening camera view does not offer filter calibration before focus",()=>{
  assert(!container.querySelector('.guided-focus-workspace')?.hidden,"camera view did not open");
  assert(!win.document.querySelector('[role="dialog"]'),"filter prompt appeared before a run");
});
await act(async()=>useStore.setState({focus:{state:"running",points:[{position:12000,hfr:3}],best:null}} as never));
await test("Guided autofocus exposes the curve and explanation without advanced controls",()=>{
  const progress=container.querySelector('[aria-label="Autofocus progress"]');
  assert(!!progress?.querySelector("svg"),"no live focus curve");
  assert(progress.textContent.includes("measuring star size in pixels"),"missing explanation");
  assert(container.querySelector('.guided-focus-camera')?.hidden,"image competes with active curve");
  assert(!container.textContent.includes("Bahtinov Focus")&&!container.querySelector(".guided-focus-details"),"First Light contains redundant advanced panels");
});
await act(async()=>useStore.setState({focus:{state:"failed",points:[],best:null,message:"No stars"}} as never));
await test("failed autofocus never offers filter calibration",()=>assert(!win.document.querySelector('[role="dialog"]'),"failed run prompted calibration"));
await act(async()=>useStore.setState({focus:{state:"running",points:[],best:null}} as never));
await act(async()=>useStore.setState({wsPhase:"down",focus:{state:"done",points:[],best:{position:12000,hfr:1.5}}} as never));
await test("a disconnected focus result cannot certify Guided readiness",()=>assert(!useGuidedSetup.getState().focus,"disconnected result certified focus"));
await act(async()=>useStore.setState({wsPhase:"up",focus:{state:"running",points:[],best:null}} as never));
await act(async()=>useStore.setState({status:{...useStore.getState().status,mount:{...field,slewing:true}},focus:{state:"done",points:[],best:{position:12000,hfr:1.5}}} as never));
await test("focus cannot be certified while the telescope is moving",()=>assert(!useGuidedSetup.getState().focus,"moving mount certified focus"));
await act(async()=>useStore.setState({status:{...useStore.getState().status,mount:{...field,slewing:false}},focus:{state:"running",points:[],best:null},filterOffsetsLearn:{state:"running"}} as never));
await act(async()=>useStore.setState({focus:{state:"done",points:[],best:{position:12000,hfr:1.5}}} as never));
await test("an individual filter sweep cannot certify an unfinished calibration",()=>assert(!useGuidedSetup.getState().focus,"mid-calibration result certified focus"));
await act(async()=>{useStore.setState({filterOffsetsLearn:null,focus:{state:"running",points:[],best:null}} as never);root.render(createElement(FocusView,{key:"after-filter-check"}));});
await act(async()=>useStore.setState({focus:{state:"done",points:[],best:{position:12000,hfr:Infinity}}} as never));
await test("non-finite star measurements cannot certify focus",()=>assert(!useGuidedSetup.getState().focus,"infinite star radius certified focus"));
let confirmFocus!:(accepted:boolean)=>void;
observeSetupChecks((action,fact)=>action==="complete"&&fact==="focus"?new Promise<boolean>(resolve=>{confirmFocus=resolve;}):undefined);
await act(async()=>useStore.setState({focus:{state:"running",points:[],best:null}} as never));
await act(async()=>useStore.setState({focus:{state:"done",points:[{position:12000,hfr:1.5}],best:{position:12000,hfr:1.5}}} as never));
await test("the filter offer waits for controller confirmation",()=>assert(!win.document.querySelector('[role="dialog"]'),"filter offer preceded controller confirmation"));
await act(async()=>{confirmFocus(false);useGuidedSetup.setState({focus:false});});
await test("a rejected checkpoint cannot display a focus-ready filter offer",()=>assert(!win.document.querySelector('[role="dialog"]'),"rejected check still showed focus-ready offer"));
observeSetupChecks(null);
await act(async()=>useStore.setState({focus:{state:"running",points:[],best:null}} as never));
await act(async()=>useStore.setState({focus:{state:"done",points:[{position:12000,hfr:1.5}],best:{position:12000,hfr:1.5}}} as never));
await test("successful autofocus offers only usable filters after completion",()=>{
  const dialog=win.document.querySelector('[role="dialog"]');
  assert(!!dialog,"successful run did not offer filter calibration");
  assert(dialog.textContent.includes("2 filters configured"),"opaque slot counted as a filter");
  assert(!postsTo("learn-offsets").length,"prompt started hardware without review");
});
await act(async()=>[...win.document.querySelectorAll('[role="dialog"] button')].find((b:any)=>b.textContent.includes("Review filter calibration"))?.click());
await test("accepting the filter offer opens calibration controls directly",()=>assert(win.document.body.textContent.includes("Learn offsets"),"calibration controls did not open"));
await act(async()=>useStore.setState({focus:{state:"running",points:[],best:null}} as never));
await test("calibration controls remain mounted during a filter focus sweep",()=>assert(win.document.body.textContent.includes("Learn offsets"),"sweep unmounted calibration controls"));
await act(async()=>useGuidedSetup.setState({field:null,focus:false}));
await test("joining a running focus without a local field still exposes Stop",()=>assert(!container.querySelector('.guided-focus-workspace')?.hidden&&byText(/Stop autofocus/),"joining browser hid the active operation"));
await act(async()=>{root.render(createElement(FocusView,{key:"resumed-filters"}));useStore.setState({filterOffsetsLearn:{state:"running",slot:1,of:2,name:"Ha"},focus:{state:"done",points:[],best:{position:12000,hfr:1.5}}} as never);});
await test("recovered filter calibration exposes the sweep and its own Stop action",()=>{
  assert(!container.querySelector('.guided-focus-workspace')?.hidden,"recovered sweep was hidden without a field");
  assert(byText(/Stop filter measurements/),"recovered filter sweep has no dedicated stop");
  assert(win.document.querySelector('[role="dialog"]')?.textContent.includes("Ha"),"recovered calibration dialog was not opened");
});
await act(async () => { root.unmount(); });
const total = passed + failed;
console.log(`focusHonesty.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
export default result;
