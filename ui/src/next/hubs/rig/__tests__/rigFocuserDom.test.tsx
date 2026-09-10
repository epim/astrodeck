// rigFocuserDom.test.tsx - the FOCUSER device sheet, MOUNTED (plan hub-rig.md
// B.5, task T-RIG-4).
//
//   Run directly:  npx tsx src/next/hubs/rig/__tests__/rigFocuserDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// FIVE THINGS WORTH A TEST, each a specific way this sheet could look right and
// be wrong:
//
//   1. PRECONDITION. The three tiles, the V-curve, the six jogs and AUTOFOCUS
//      NOW are all on screen. Without this every assertion below could pass on
//      a sheet that mounted nothing - "no control carries the wrong attribute"
//      is trivially true of no controls (astrodeck-ui-probe-traps: a broken
//      probe reads as a passing app).
//   2. A JOG REACHES THE RIG, WITH THE CLAMPED NUMBER. `POST /api/focuser/move`
//      carrying `{position}`. The clamp is not cosmetic: OUT 100 from 19 950 on
//      a focuser whose travel ends at 20 000 must send 20 000, not 20 050, so a
//      user error is caught before the round trip rather than by the firmware
//      (which refuses silently - the 2026-07-31 "pressing Go did nothing").
//   3. HALT IS NEVER LOCKED BY A LANE. With the `focuser` lane busy the jogs
//      carry the lane sentence and HALT does not: it is the only way to end a
//      move early, and gating it on the lane it exists to end is the bug the
//      seam inventory records three separate times.
//   4. A VIEWER SEES THE SAME SCREEN, INERT. AUTOFOCUS NOW carries
//      `focusButtonState`'s own sentence and `aria-disabled`, stays in the tree,
//      and pressing it fires NO request.
//   5. TEMPERATURE COMPENSATION IS NOT HERE. Deviation E3: the engine has no
//      such field, coefficient or loop, so the design's toggle would be a switch
//      for a feature that does not exist. Asserting the STRING's absence is what
//      makes re-adding the dead toggle turn the suite red.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/#/rig/devices/focuser", pretendToBeVisual: true },
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

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "localStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "requestAnimationFrame", "cancelAnimationFrame", "ResizeObserver",
  // NOT `performance`: this jsdom's delegates to the global one, so copying it
  // makes performance.now() recurse until the stack blows.
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------- fetch recorder
interface Asked { method: string; url: string; body: unknown }
const asked: Asked[] = [];
g.fetch = async (url: string, init?: { method?: string; body?: string }) => {
  const method = init?.method ?? "GET";
  asked.push({ method, url: String(url), body: init?.body ? JSON.parse(init.body) : null });
  return { ok: true, status: 200, statusText: "OK", json: async () => ({}) };
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../store");
const {
  FocuserSheet, FOCUSER_FOOTER, FOCUS_FRAME_NOTE, LOOP_RUNNING_REASON, groupSteps,
  shutterWait,
} = await import("../sheets/focuser");
const { POLAR_REASON } = await import("../capture/captureGate");
type RigStatus = import("../../../../types").RigStatus;

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

// ------------------------------------------------------------------ fixtures
const OPERATOR = {
  role: "operator",
  email: "op@rig",
  caps: ["view.status", "view.preview", "view.weather", "view.site_derived",
    "control.capture", "control.guide", "control.mount"],
};
const VIEWER = { role: "viewer", email: null, caps: ["view.status", "view.preview"] };
const ADMIN = {
  role: "admin",
  email: "admin@rig",
  caps: ["view.status", "view.preview", "view.media", "view.site_precise",
    "view.site_derived", "view.weather", "control.capture", "control.mount",
    "control.guide", "control.power", "config.safety", "config.solar_override",
    "config.backend", "config.site_optics", "config.alerts"],
};

function focStatus(over: Record<string, unknown> = {}): RigStatus {
  return {
    connected: {
      focuser: { name: "ZWO EAF", kind: "focuser", connected: true },
      camera: { name: "ZWO ASI2600MM Pro", kind: "camera", connected: true },
    },
    looping: false,
    busy_lanes: [],
    backend_links: [
      { role: "focuser", connected: true, error: null },
      { role: "camera", connected: true, error: null },
      { role: "filterwheel", connected: true, error: null },
    ],
    focuser: {
      position: 19950,
      max: 20000,
      temperature: 11.2,
      moving: false,
      can_set_position: true,
      sweep: { step: 350, steps_each_side: 4, basis: "measured slope", measured: true },
    },
    camera: {
      temperature: -10, can_cool: true, width: 6248, height: 4176,
      max_gain: 300, max_bin: 4,
    },
    filterwheel: {
      position: 0, names: ["L", "R", "G", "B", "Ha", "OIII", "SII"], current: "L",
    },
    ...over,
  } as unknown as RigStatus;
}

const FRAMES = {
  capture: { exposure_s: 120, gain: 100, offset: 50, binning: 1, filter: null },
  focus: { exposure_s: 3, gain: 200, offset: 30, binning: 1, filter: null },
  solve: { exposure_s: 3, gain: 100, offset: 50, binning: 1, filter: null },
  guide: { exposure_s: 2, gain: 100, offset: 10, binning: 1, filter: null },
};

const PREVIEW = {
  id: 7,
  stats: { min: 100, max: 40000, mean: 800, median: 700, std: 200 },
  histogram: [1, 2, 3], histogram_domain: "linear",
  exposure_s: 3, gain: 200, binning: 1,
  data_width: 6248, data_height: 4176, display_width: 1400, display_height: 935,
  mime: "image/jpeg", source: "alpaca", is_stretched: false, data_is_linear: true,
  has_lossless: true, full_well: 51000, pixel_scale_arcsec: 1.46,
  auto_levels: { black: 0, mid: .4, white: 1 },
  hfr: 2.1, stars: 640, ts: Math.round(Date.now() / 1000),
};

const CONFIG = {
  standards: {
    apply_filter_offsets: true, refocus_on_temp_delta_c: 1.5, min_stars: 0,
    max_guide_rms: 0, max_eccentricity: 0, max_consecutive_rejects: 0,
    max_consecutive_rejects_night: 0,
  },
  escalation: { hfr_reject_factor: 1.15, hfr_reject_action: "discard" },
  providers: { autofocus: "auto", polar_align: "auto", solve: "auto", guide: "auto" },
};

function seed(over: Record<string, unknown> = {}): void {
  act(() => {
    useStore.setState({
      status: focStatus(),
      equipConnected: true,
      wsPhase: "up",
      principal: OPERATOR,
      frameSettings: FRAMES,
      config: CONFIG,
      sequence: { state: "idle" },
      previews: [PREVIEW],
      livePreviewId: 7,
      selectedPreviewId: null,
      focus: null,
      lastAutofocusResult: null,
      plan: { autofocus_every: 30, steps: [] },
      hfrGood: 3.5,
      hfrWarn: 5,
      toasts: [],
      ...over,
    } as never);
  });
}

const container = win.document.getElementById("root") as any;
let rootRef: ReturnType<typeof createRoot> | null = null;
function mount(): void {
  if (rootRef) act(() => { rootRef!.unmount(); });
  rootRef = createRoot(container);
  act(() => { rootRef!.render(createElement(FocuserSheet as any, { params: {}, depth: 0 })); });
}
function click(node: any): void {
  act(() => { node.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
}
const q = (sel: string) => container.querySelector(sel) as any;
const qa = (sel: string) => Array.from(container.querySelectorAll(sel)) as any[];
const text = () => (container.textContent || "") as string;
/** A button by the text on its face. The sheet has six jogs whose only
 *  distinguishing mark IS their label, which is also what the user reads. */
const button = (label: string) =>
  qa("button").find((b: any) => (b.textContent || "").trim() === label);
const moves = () => asked.filter((a) => a.url.includes("/api/focuser/move"));
const captures = () => asked.filter((a) => /\/api\/capture(\?|$)/.test(a.url));
const loops = () => asked.filter((a) => a.url.includes("/api/capture/loop"));
const testid = (id: string) => q(`[data-testid="${id}"]`);
const byAria = (label: string) =>
  qa("[aria-label]").find((n: any) => n.getAttribute("aria-label") === label);

// ====================================================== 1. the precondition
seed();
mount();

test("the sheet mounted, with its three readouts, the V-curve and every jog", () => {
  assert(q('[data-testid="rig-focuser"]') != null,
    "no sheet marker - the sheet did not render at all");
  const labels = qa(".nx-readout-label").map((n: any) => (n.textContent || "").trim());
  for (const want of ["POSITION", "HFR", "TUBE"]) {
    assert(labels.includes(want), `no ${want} readout - got ${JSON.stringify(labels)}`);
  }
  assert(/19\s?950/.test(text()),
    "the POSITION tile does not show the focuser's own position");
  assert(/11\.2°C/.test(text()), "the TUBE tile does not show the tube temperature");
  // The V-curve is an SVG with axis text; assert the card's own header, which is
  // the thing that says WHICH run is drawn.
  assert(/V-CURVE/.test(text()), "the V-curve card did not render");
  for (const label of ["IN 100", "IN 10", "OUT 10", "OUT 100", "IN 1000", "OUT 1000"]) {
    assert(button(label) != null, `no ${label} jog button`);
  }
  assert(button("AUTOFOCUS NOW") != null, "no AUTOFOCUS NOW button");
  assert(button("HALT") != null, "no HALT button");
  assert(text().includes(FOCUSER_FOOTER), "the footer note is missing");
  eq(asked.length, 0,
    "the sheet asked the rig for something on mount - a device sheet renders from the status frame");
});

// =================================================== 2. a jog reaches the rig
await testAsync("IN 10 sends the focuser exactly where it says it will", async () => {
  asked.length = 0;
  click(button("IN 10"));
  await settle();
  eq(moves().length, 1, `IN 10 did not POST /api/focuser/move (asked ${JSON.stringify(asked)})`);
  eq(JSON.stringify(moves()[0].body), JSON.stringify({ position: 19940 }),
    "IN 10 from 19950 must ask for 19940");
  eq(moves()[0].method, "POST", "the move must be a POST");
});

await testAsync("OUT 100 is clamped to the travel, before the round trip", async () => {
  // A fresh mount: the previous move is still in flight as far as the narrator
  // is concerned, and a second one is refused on purpose (the 409 guard).
  seed();
  mount();
  asked.length = 0;
  click(button("OUT 100"));
  await settle();
  eq(moves().length, 1, "OUT 100 did not POST /api/focuser/move");
  eq(JSON.stringify(moves()[0].body), JSON.stringify({ position: 20000 }),
    "OUT 100 from 19950 with max 20000 must clamp to 20000, not ask for 20050");
});

await testAsync("a second jog while the first is in flight is refused, not queued", async () => {
  seed();
  mount();
  asked.length = 0;
  click(button("IN 10"));
  await settle();
  eq(moves().length, 1, "precondition: the first jog must have gone out");
  click(button("IN 10"));
  await settle();
  eq(moves().length, 1,
    "a second move went out while one was in flight - the server answers that with a 409 "
    + "and the narrator loses the first move's target");
});

// ============================================ 3. HALT is never lane-locked
await testAsync("with the focuser lane busy the jogs lock and HALT does not", async () => {
  seed({ status: focStatus({ busy_lanes: ["focuser"] }) });
  mount();
  await settle();
  const jog = button("IN 10");
  assert(jog != null, "precondition: the jog must be on screen");
  eq(jog.getAttribute("aria-disabled"), "true",
    "a jog stayed live while the focuser lane was busy - the second POST is a 409");
  assert(/already travelling to a position/.test(jog.getAttribute("title") || ""),
    `the jog's reason is not the lane sentence (got ${JSON.stringify(jog.getAttribute("title"))})`);

  const halt = button("HALT");
  assert(halt != null, "precondition: HALT must be on screen");
  eq(halt.getAttribute("aria-disabled"), null,
    "HALT was locked by the very lane it exists to end - the escape hatch is gone");

  asked.length = 0;
  click(halt);
  await settle();
  eq(asked.filter((a) => a.url.includes("/api/focuser/halt")).length, 1,
    "pressing HALT during a move did not reach /api/focuser/halt");
});

// ====================================================== 4. the viewer's screen
await testAsync("a viewer sees AUTOFOCUS NOW, told why, and pressing it asks nothing",
  async () => {
    seed({ principal: VIEWER });
    mount();
    await settle();
    const af = button("AUTOFOCUS NOW");
    assert(af != null, "a viewer cannot see AUTOFOCUS NOW at all - the screen was replaced");
    eq(af.getAttribute("aria-disabled"), "true",
      "AUTOFOCUS NOW is live for a viewer");
    eq(af.getAttribute("title"), "Read-only — focusing needs operator access",
      "the viewer is not given focusButtonState's own sentence for this button");
    assert(af.hasAttribute("disabled") === false,
      "AUTOFOCUS NOW uses the native disabled attribute, which takes the reason "
      + "out of the accessibility tree");

    asked.length = 0;
    click(af);
    click(button("IN 10"));
    click(button("HALT"));
    await settle();
    eq(asked.length, 0,
      `a viewer's presses reached the rig: ${JSON.stringify(asked)}`);
    // …and every one of them said why, rather than doing nothing silently.
    const toasts = (useStore.getState() as any).toasts as { title?: string }[];
    assert(toasts.length >= 1,
      "a locked press was silent - the reason must be stated, not merely withheld");
  });

// ================================================= 5. the omitted toggle (E3)
await testAsync("TEMPERATURE COMPENSATION is nowhere on this sheet", async () => {
  seed();
  mount();
  await settle();
  assert(!/TEMPERATURE COMPENSATION/i.test(text()),
    "the design's temperature-compensation toggle is on screen, and the engine has "
    + "no such field - it would be a switch for a feature that does not exist");
  // What replaced it must be here, or the deviation is a deletion.
  assert(/REFOCUS AFTER 1\.5°C OF DRIFT/.test(text()),
    "the temperature rule the engine DOES have is missing, so E3 dropped a "
    + "capability instead of re-shaping one");
  assert(/SHIFT BY FILTER OFFSET ON A CHANGE/.test(text()),
    "the filter-offset rule (E4) is missing");
  assert(/REFOCUS EVERY 30 FRAMES/.test(text()),
    "the per-plan refocus cadence (E5) is not shown");
  assert(/HFR GATE x1\.15/.test(text()),
    "the HFR quality gate (E6) is not shown");
});

await testAsync("the filter-offset rule writes the WHOLE standards block", async () => {
  // `setStandardsConfig` is a wholesale replace (plan 0.4): read the current
  // block, spread it, patch one field, send all of it. Sending the one field
  // alone would blank min_stars, the guide-RMS ceiling and both reject counters.
  // `config.safety` is ADMIN-only in the shipped role map, and that split is
  // itself worth seeing: an operator may jog the focuser and may not rewrite
  // the rig's refocus rules.
  seed();
  mount();
  await settle();
  const opSwitch = byAria("SHIFT BY FILTER OFFSET ON A CHANGE");
  assert(opSwitch != null, "no filter-offset switch on the sheet");
  eq(opSwitch.getAttribute("aria-disabled"), "true",
    "an operator can rewrite the rig's standards block from this sheet");
  eq(button("IN 10").getAttribute("aria-disabled"), null,
    "precondition: the same operator's jogs must stay live - this is a split "
    + "gate, not a read-only screen");

  seed({ principal: ADMIN });
  mount();
  await settle();
  asked.length = 0;
  const sw = byAria("SHIFT BY FILTER OFFSET ON A CHANGE");
  assert(sw != null, "no filter-offset switch for an admin");
  eq(sw.getAttribute("aria-disabled"), null, "the switch is locked for an admin");
  click(sw);
  await settle();
  const posts = asked.filter((a) => a.url.includes("/api/config") && a.method === "POST");
  eq(posts.length, 1, `the switch did not POST /api/config (asked ${JSON.stringify(asked)})`);
  const body = posts[0].body as any;
  eq(body.standards.apply_filter_offsets, false,
    "the switch did not send the field it toggled");
  eq(body.standards.refocus_on_temp_delta_c, 1.5,
    "the temperature rule was dropped from the block - a partial write blanks "
    + "every standard the user did not touch");
  assert("min_stars" in body.standards,
    "the standards block was not sent whole");
});

// ================================= 6. the shutter at the `focus` frame scope
// r4 #11. Before this row `focusCaptureBody` appeared exactly once on the whole
// sheet, inside `applyPreset` behind `if (!looping) return`, so the focus
// exposure, gain and binning could only reach a live frame by RESTARTING a loop
// somebody else had started from Rig - Capture at the `capture` scope - while
// AF_SETTINGS_NOTE and `sweepReadiness`'s refusal both told the user to press a
// Single that did not exist.
await testAsync("SINGLE posts /api/capture with the FOCUS scope's own body", async () => {
  seed();
  mount();
  await settle();
  asked.length = 0;
  const single = testid("focus-single");
  assert(single != null, "there is no SINGLE on the focuser sheet at all");
  eq(single.getAttribute("aria-disabled"), null,
    "precondition: an operator with a camera found SINGLE locked");
  click(single);
  await settle();

  const posts = captures();
  eq(posts.length, 1, `SINGLE did not POST /api/capture (asked ${JSON.stringify(asked)})`);
  eq(posts[0].method, "POST", "the capture was not a POST");
  const body = posts[0].body as Record<string, unknown>;
  eq(JSON.stringify(Object.keys(body).sort()),
    JSON.stringify(["binning", "exposure_s", "gain", "offset", "save"]),
    "the focus capture body's field set is not focusCaptureBody's");
  // FRAMES.focus, not FRAMES.capture: the whole point of the row is that the
  // numbers in AUTOFOCUS SETTINGS are the numbers that get shot.
  eq(body.exposure_s, 3, "SINGLE shot the capture scope's exposure, not the focus scope's");
  eq(body.gain, 200, "SINGLE shot the capture scope's gain, not the focus scope's");
  eq(body.binning, 1, "binning");
  eq(body.save, false,
    "a focus frame was written to the library - focusCaptureBody sends save:false and "
    + "`hub.start_loop` has no save parameter at all");

  // The one surface that says where the frame just went. Without it a Single
  // that saves nothing and draws nothing on THIS sheet is indistinguishable
  // from a Single that did nothing at all.
  assert(text().includes(FOCUS_FRAME_NOTE),
    "nothing on the sheet says the frames land on the live stage on Rig - Capture");
});

await testAsync("LOOP posts /api/capture/loop with that same body", async () => {
  seed();
  mount();
  await settle();
  asked.length = 0;
  click(testid("focus-loop"));
  await settle();
  eq(loops().length, 1, `LOOP did not POST /api/capture/loop (asked ${JSON.stringify(asked)})`);
  const body = loops()[0].body as Record<string, unknown>;
  eq(body.exposure_s, 3, "the loop was started at the wrong exposure");
  eq(body.gain, 200, "the loop was started at the wrong gain");
});

await testAsync("STOP is never lane-locked, and SINGLE and LOOP are", async () => {
  // Every lane that owns the camera at once. STOP is the only way out of any of
  // them, and gating it on the lane it exists to end is the bug the seam
  // inventory records three separate times.
  seed({ status: focStatus({ busy_lanes: ["capture", "looping"], looping: true }) });
  mount();
  await settle();

  const single = testid("focus-single");
  eq(single.getAttribute("aria-disabled"), "true",
    "SINGLE stayed live over a running loop - /api/capture would 409 at the capture lock");
  eq(single.getAttribute("title"), LOOP_RUNNING_REASON,
    "SINGLE is locked but does not say a loop is why");

  const stop = testid("focus-stop");
  assert(stop != null, "precondition: STOP must be on screen");
  eq(stop.getAttribute("aria-disabled"), null,
    "STOP was locked by the very lane it exists to end - the escape hatch is gone");
  asked.length = 0;
  click(stop);
  await settle();
  eq(asked.filter((a) => a.url.includes("/api/capture/stop")).length, 1,
    "pressing STOP during a loop did not reach /api/capture/stop");
});

await testAsync("a viewer sees the shutter, told why, and pressing it asks nothing",
  async () => {
    seed({ principal: VIEWER });
    mount();
    await settle();
    asked.length = 0;
    for (const id of ["focus-single", "focus-loop", "focus-stop"]) {
      const el = testid(id);
      assert(el != null, `${id} was hidden from the viewer instead of locked`);
      eq(el.getAttribute("aria-disabled"), "true", `${id} is live for a viewer`);
      assert((el.getAttribute("title") || "").length > 0, `${id} carries no reason`);
      assert(el.hasAttribute("disabled") === false,
        `${id} used the native disabled attribute, which takes the reason out of the a11y tree`);
      click(el);
    }
    await settle();
    eq(asked.length, 0, `a viewer's presses reached the rig: ${JSON.stringify(asked)}`);
  });

test("the wait narration says both numbers when the frame never arrives", () => {
  const t0 = 1_000_000;
  eq(shutterWait({ startedAt: null, exposureS: 4, now: t0 }), null,
    "something was narrated before any frame was accepted");
  eq(shutterWait({ startedAt: t0, exposureS: 4, now: t0 + 1000 })?.text,
    "exposing · 3 s left", "the countdown is wrong");
  eq(shutterWait({ startedAt: t0, exposureS: 4, now: t0 + 5000 })?.text,
    "reading out…", "a finished exposure does not say it is reading out");
  const late = shutterWait({ startedAt: t0, exposureS: 4, now: t0 + 74_000 })!;
  eq(late.tone, "warn", "a dropped frame is not marked as a problem");
  assert(/74 s/.test(late.text) && /4 s exposure/.test(late.text),
    `the dropped-frame sentence must carry BOTH numbers, got ${JSON.stringify(late.text)}`);
  for (const n of [
    shutterWait({ startedAt: t0, exposureS: 4, now: t0 + 1000 })!.text,
    shutterWait({ startedAt: t0, exposureS: 4, now: t0 + 5000 })!.text,
    late.text,
  ]) {
    assert(!/[\u2014\u2013]/.test(n), `an em-dash or en-dash reached a UI string: ${n}`);
  }
});

// ============================== 7. polar alignment owns the camera (r4 #25)
await testAsync("with the polar lane busy every camera control says so", async () => {
  seed({ status: focStatus({ busy_lanes: ["polar"] }) });
  mount();
  await settle();

  for (const [what, el] of [
    ["AUTOFOCUS NOW", button("AUTOFOCUS NOW")],
    ["FIND FOCUS ROUGHLY FIRST", button("FIND FOCUS ROUGHLY FIRST")],
    ["BAHTINOV START", button("START")],
    ["SINGLE", testid("focus-single")],
    ["LOOP", testid("focus-loop")],
  ] as [string, any][]) {
    assert(el != null, `precondition: ${what} is not on the sheet`);
    eq(el.getAttribute("aria-disabled"), "true",
      `${what} pressed through a polar alignment - the rig answers that with a raw 409`);
    eq(el.getAttribute("title"), POLAR_REASON,
      `${what} is locked but does not name the alignment`);
  }

  // The escape hatches stay live: an alignment does not own the drawtube, and
  // STOP is the only way to end a capture it may have left running.
  eq(button("HALT").getAttribute("aria-disabled"), null,
    "HALT was locked by an alignment that does not own the focuser");
  eq(testid("focus-stop").getAttribute("aria-disabled"), null,
    "STOP was locked by an alignment - the escape hatch is gone");

  asked.length = 0;
  click(button("AUTOFOCUS NOW"));
  click(testid("focus-single"));
  await settle();
  eq(asked.length, 0, `a press during an alignment reached the rig: ${JSON.stringify(asked)}`);
});

test("the position is grouped the way the design writes it", () => {
  eq(groupSteps(14318), "14 318", "14318 must read as 14 318 with a thin space");
  eq(groupSteps(360), "360", "a three-digit position is not grouped");
});

// ============================ 9. clearing an autofocus profile pin (#24)
//
// An `autofocus` pin written into a profile could be EDITED but never REMOVED:
// the only unpin on this branch was polar's, so a profile that pinned the sweep
// to the backend left this row permanently editable and permanently pinned.
// Delete the button and the first assertion fails; hide it from a non-holder
// instead of locking it and the viewer half does.
const PIN = {
  "providers.autofocus": {
    value: "backend", layer: "profile", profile: "backend", config: "auto",
    default: "auto", profile_id: "p1", profile_name: "Rig1", reason: "override: profile",
  },
};

await testAsync("an autofocus pin from a profile can be cleared, and the call names the cap", async () => {
  seed({ principal: ADMIN, config: { ...CONFIG, effective: PIN } });
  mount();
  await settle();

  const unpin = testid("focuser-provider-unpin");
  assert(unpin != null, "a profile-pinned autofocus provider offers no way to clear the pin");
  eq(unpin.getAttribute("aria-disabled"), null, "precondition: an admin found the unpin locked");

  asked.length = 0;
  click(unpin);
  await settle();
  const posts = asked.filter((a) => a.url.includes("/api/profiles/p1/clear-overrides"));
  eq(posts.length, 1, `the unpin did not reach clear-overrides (asked ${JSON.stringify(asked)})`);
  eq(JSON.stringify((posts[0].body as any)?.providers), '["autofocus"]',
    "the unpin cleared the wrong capability (it must name autofocus and nothing else):");
  eq((posts[0].body as any)?.optics, false,
    "the unpin also cleared the profile's OPTICS block, which nobody asked it to:");
});

await testAsync("a viewer sees the unpin locked with its reason, not hidden", async () => {
  seed({ principal: VIEWER, config: { ...CONFIG, effective: PIN } });
  mount();
  await settle();

  const unpin = testid("focuser-provider-unpin");
  assert(unpin != null,
    "the unpin was HIDDEN from a viewer - ARCHITECTURE section 8 says nothing is hidden");
  eq(unpin.getAttribute("aria-disabled"), "true", "the unpin looks live to a viewer");
  assert(unpin.hasAttribute("disabled") === false,
    "the unpin uses the native disabled attribute, which takes the reason out of the "
    + "accessibility tree");
  assert(/access/.test(unpin.getAttribute("title") ?? ""),
    `the locked unpin does not say who may press it: "${unpin.getAttribute("title")}"`);

  asked.length = 0;
  click(unpin);
  await settle();
  eq(asked.length, 0, `a viewer's unpin press reached the rig: ${JSON.stringify(asked)}`);
});

act(() => { if (rootRef) rootRef.unmount(); });

const total = passed + failed;
console.log(`rigFocuser.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
