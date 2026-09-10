// nowDom.test.tsx - SESSION / NOW, MOUNTED, pressed, and refused.
//
//   Run directly:  npx tsx src/next/hubs/session/__tests__/nowDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc --noEmit`.
//
// FOUR THINGS THIS FILE IS FOR, and what breaks without each:
//
//  1. A PRECONDITION MARKER. A DOM test that asserts an absence over a blank
//     page passes forever (`verify-on-the-real-thing`). Every assertion below
//     runs after `session-now` has been found AND after the run header, the
//     live stack, the vitals band and the controls have each been found, so
//     "the viewer fired nothing" cannot pass because nothing rendered.
//  2. PAUSE REACHES `/api/sequence/pause`. That route is the only thing that
//     stops the run at a frame boundary; a control wired to the wrong path is a
//     button that looks like it worked.
//  3. STOP TAKES TWO PRESSES. One press must NOT abort - the design's two-tap
//     arm is the whole guard on a control that ends an unattended six-hour run,
//     and the second press must reach `/api/sequence/abort` (the flows run
//     stops through the same route: STOP is not a flows verb).
//  4. A VIEWER SEES THE SAME SCREEN, honest-disabled, and fires NOTHING.
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

// Everything answers false: the PHONE layout, which is the one the design is
// written for and the one where the plan editor is honest-disabled.
win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class {
  static OPEN = 1;
  readyState = 0;
  close() {} send() {} addEventListener() {} removeEventListener() {}
};
win.Element.prototype.setPointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.releasePointerCapture = function () { /* jsdom has none */ };
win.scrollTo = () => {};

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "localStorage", "sessionStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "requestAnimationFrame", "cancelAnimationFrame", "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------- fetch recorder
interface Ask { url: string; method: string; body: any }
const asks: Ask[] = [];

const SESSION = {
  id: "sess-1",
  schema_version: 3,
  name: "M31 LRGB",
  created_ts: 1_700_000_000,
  updated_ts: 1_700_000_900,
  status: "active",
  nights: ["M31-20260909-210000"],
  auto_resume: true,
  origin: "plan",
  origin_id: "",
  plan: {
    name: "M31 LRGB",
    guide: true, dither_every: 1, dither_pixels: null, autofocus_every: 0,
    cool_to: -10, cool_timeout_s: 600, apply_filter_offsets: null,
    refocus_on_temp_delta_c: null, meridian_flip: true, recover_guiding: null,
    hfr_reject_factor: 1.15, park_when_done: true, warm_cooler_when_done: true,
    count_mode: "attempts", min_stars: 20, max_guide_rms: 2, max_eccentricity: 0,
    max_consecutive_rejects: 3, max_consecutive_rejects_night: 0,
    targets: [{
      id: "t1", name: "M31", ra_hours: 0.71, dec_deg: 41.2,
      center: true, autofocus_first: true, calibration: false,
      steps: [
        { id: "s-L", filter: "L", exposure_s: 120, gain: 100, offset: 30, binning: 1, count: 12, frame_type: "Light" },
        { id: "s-R", filter: "R", exposure_s: 120, gain: 100, offset: 30, binning: 1, count: 6, frame_type: "Light" },
      ],
    }],
  },
  frames: [
    { id: "f1", ts: 1, night: "M31-20260909-210000", target_id: "t1", step_id: "s-L", thumb: null, metrics: {}, auto_accepted: true, override: null },
    { id: "f2", ts: 2, night: "M31-20260909-210000", target_id: "t1", step_id: "s-L", thumb: null, metrics: {}, auto_accepted: true, override: null },
    { id: "f3", ts: 3, night: "M31-20260909-210000", target_id: "t1", step_id: "s-R", thumb: null, metrics: {}, auto_accepted: false, override: null },
  ],
};

const STACK = {
  enabled: true, target: "M31", seq: 7,
  channels: [{ channel: "L", frames: 2, integrated_s: 240, rejected: 0 }],
  frames: 2, integrated_s: 240, rejected: 0, mode: "mono", downsample: 2,
  has_image: true, render_age_s: 3,
  backfill: { running: false, total: 0, done: 0, added: 0, skipped: 0, failed: 0, channel: "", error: "", started_ts: null, finished_ts: null, available: 0 },
};

function answer(url: string): any {
  if (url.includes("/api/sessions/sess-1")) return SESSION;
  if (url.includes("/api/sessions")) return { sessions: [{ id: "sess-1", name: "M31 LRGB", status: "active", created_ts: 1, updated_ts: 2, nights: 2, accepted: 2, total: 18, auto_resume: true }] };
  if (url.includes("/api/sequence/stack")) return STACK;
  if (url.includes("/api/sequence/recoverable")) return { recoverable: false };
  if (url.includes("/api/reports")) return [];
  if (url.includes("/api/flows")) return [];
  return { ok: true };
}

g.fetch = async (url: any, init: any) => {
  const method = (init?.method ?? "GET").toUpperCase();
  let body: any = null;
  if (init?.body) { try { body = JSON.parse(init.body); } catch { body = init.body; } }
  asks.push({ url: String(url), method, body });
  return {
    ok: true, status: 200, statusText: "OK",
    headers: { get: () => "application/json" },
    json: async () => answer(String(url)),
    text: async () => "",
  };
};
const posts = (path: string) => asks.filter((a) => a.method === "POST" && a.url.includes(path));

// ------------------------------------------ imports, AFTER the globals are set
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../store");
const { NowScreen } = await import("../now/NowScreen");
const { SessionColumn } = await import("../../../shell/SessionColumn");
const { FLIP_SITE_REASON } = await import("../../monitor/live/FlipTile");
const { RUN_CONTROL_REASON } = await import("../now/RunControls");

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
function eq<T>(got: T, want: T, msg = ""): void {
  if (got !== want) throw new Error(`${msg} expected ${String(want)}, got ${String(got)}`);
}
const settle = async () => {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
};

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
const byId = (id: string) => container.querySelector(`[data-testid="${id}"]`) as any;
const click = async (el: any) => {
  assert(el != null, "click: the element is not there - the fixture is wrong, not the component");
  await act(async () => {
    el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
  });
  await settle();
};

const OPERATOR = {
  role: "operator", email: "op@rig",
  caps: ["view.status", "view.preview", "view.site_derived", "control.capture", "control.mount", "control.guide"],
};
const VIEWER = { role: "viewer", email: null, caps: ["view.status", "view.preview"] };

const NOW = Date.now();

function seed(over: Record<string, unknown> = {}): void {
  useStore.setState({
    principal: OPERATOR,
    equipConnected: true,
    wsPhase: "up",
    wsConnected: true,
    telemetryStale: false,
    wsLastEvent: NOW,
    lastCaptureAtMs: NOW,
    toasts: [],
    safety: { connected: true, streak: 0, reading: { is_safe: true, reason: "", source: "sim", stale: false, ts: NOW / 1000 } },
    weather: null,
    resumeArm: null,
    focus: null,
    mountOp: null,
    lastAutofocusResult: null,
    guide: null,
    preview: { id: 4, hfr: 2.37 },
    status: {
      connected: { camera: { connected: true, name: "sim" }, telescope: { connected: true, name: "sim mount" } },
      looping: false,
      busy_lanes: ["capture"],
      disk: { free_gb: 210, low: false, critical: false },
      camera: {
        temperature: -10, can_cool: true, width: 100, height: 100, max_gain: 100,
        cooler: { on: true, power: 42, target_c: -10, at_target: true, can_report_power: true },
      },
      meridian: { status: "counting", hours_to_flip: 1.63, flip_enabled: true, pier_side: "east" },
    },
    sequence: {
      state: "running",
      detail: "M31: L 120s  [3/12]",
      target: "M31",
      target_index: 0,
      plan_name: "M31 LRGB",
      session: { id: "sess-1", name: "M31 LRGB", count_mode: "attempts", accepted: 2, target: "M31" },
      progress: {
        frames_done: 3, frames_total: 18, percent: 17, elapsed_s: 3600, rejected: 1,
        eta_s: 5400, eta_confident: true, server_now_ms: NOW,
        current_exposure_s: 120, frame_started_at_ms: NOW - 30_000,
      },
    },
    flows: { ...(useStore.getState() as any).flows, cards: [], libraryLoaded: true, libraryError: null },
    ...over,
  } as never);
}

seed();
await act(async () => { root.render(createElement(NowScreen)); });
await settle();

// ------------------------------------------------------------ 1. the markers
test("the screen rendered - the precondition every assertion below rests on", () => {
  assert(byId("session-now") != null, "no session-now marker: the fixture is wrong, not the component");
  assert(byId("now-run-header") != null, "no run header");
  assert(byId("now-live-stack") != null, "no live stack");
  assert(byId("now-vitals") != null, "no vitals band");
  assert(byId("now-integration") != null, "no integration bar");
  assert(byId("now-run-controls") != null, "no run controls");
  assert(byId("now-empty") == null, "the empty state is up over a running run");
});

test("the phase pill reads the LANE, not just the state", () => {
  const pill = byId("now-phase-pill");
  assert(pill != null, "no phase pill");
  eq(pill.textContent, "CAPTURING", "the capture lane must reach the pill:");
});

test("the target line and the kind line carry the run, not a placeholder", () => {
  eq(byId("now-target-line").textContent, "M31", "target line:");
  const header = byId("now-run-header").textContent as string;
  assert(/quick session|night plan|campaign/.test(header), `no kind word in "${header}"`);
  assert(/finishes/.test(header), `no finish clock in "${header}"`);
});

test("the integration bar is per-filter off the ledger, with the flagged line", () => {
  const bar = byId("now-integration").textContent as string;
  assert(/L 2\/12/.test(bar), `the L segment did not count the ledger: "${bar}"`);
  assert(/R 0\/6/.test(bar), `the R segment did not count the ledger: "${bar}"`);
  assert(byId("now-rejected-line") != null, "1 rejected frame and no line saying the frames are kept");
});

test("the live stack shows the composite, not the empty face", () => {
  assert(byId("live-stack-image") != null, "no stack image over has_image: true");
  assert(byId("live-stack-empty") == null, "the empty face is up over a stacked run");
  assert(byId("live-stack-linklost") == null, "the LINK LOST overlay is up on a healthy link");
});

// ---------------------------------------------------------------- 2. PAUSE
await testAsync("PAUSE posts /api/sequence/pause, once", async () => {
  const before = posts("/api/sequence/pause").length;
  await click(byId("now-pause"));
  eq(posts("/api/sequence/pause").length, before + 1, "pause posts:");
  eq(posts("/api/sequence/abort").length, 0, "PAUSE reached the abort route");
});

// ----------------------------------------------------------------- 3. STOP
await testAsync("STOP needs TWO presses, and the second one aborts", async () => {
  const stop = byId("now-stop");
  assert(stop != null, "no STOP control");
  eq(posts("/api/sequence/abort").length, 0, "precondition: nothing has aborted yet");

  await click(stop);
  eq(posts("/api/sequence/abort").length, 0,
    "ONE press aborted the run - the two-tap arm is not guarding anything");
  eq(byId("now-stop").textContent, "TAP AGAIN TO STOP",
    "the armed button does not say what a second press will do");

  await click(byId("now-stop"));
  eq(posts("/api/sequence/abort").length, 1, "the second press did not abort");
});

// --------------------------------------------------------------- 4. viewer
await testAsync("a viewer sees the same screen, locked, and fires nothing", async () => {
  await act(async () => { useStore.setState({ principal: VIEWER } as never); });
  await settle();
  const before = asks.length;

  assert(byId("session-now") != null, "the viewer lost the screen entirely");
  assert(byId("now-run-header") != null, "the viewer lost the run header");

  for (const id of ["now-pause", "now-stop"]) {
    const el = byId(id);
    assert(el != null, `${id} was hidden from the viewer instead of locked`);
    eq(el.getAttribute("aria-disabled"), "true", `${id} is not locked for a viewer`);
    const reason = el.getAttribute("title") ?? "";
    assert(/operator or admin access/.test(reason),
      `${id} does not say who may press it; got "${reason}"`);
    await click(el);
  }
  eq(asks.length, before, "a viewer's presses reached the server");

  // and the reason was SAID, not swallowed
  const toasts = (useStore.getState() as any).toasts as Array<{ title?: string }>;
  assert(toasts.some((t) => t.title === RUN_CONTROL_REASON),
    "the lock reason never reached the toast channel");
});

// ------------------------------------------------- 5. the empty / armed state
await testAsync("with no run at all the empty state takes over, and RUN ARMED is not 'no run'", async () => {
  await act(async () => {
    useStore.setState({
      principal: OPERATOR,
      sequence: { state: "idle" },
      resumeArm: {
        armed: { id: "sess-1", name: "M31 LRGB", owed: 58, accepted: 2, total: 60, origin: "flow", origin_id: "f1" },
        hold: null,
      },
      safety: { connected: false, streak: 0, reading: null },
    } as never);
  });
  await settle();

  assert(byId("now-empty") != null, "no empty state with an idle engine");
  assert(byId("now-run-controls") == null, "PAUSE/STOP are still on screen with no run");
  const text = byId("now-empty").textContent as string;
  assert(/RUN ARMED/.test(text), `an armed session reads as "no run": "${text.slice(0, 120)}"`);
  assert(/58 frames owed/.test(text), "the owed frames are not on screen");
  assert(byId("now-find-target") != null, "no FIND A TARGET");
  assert(byId("now-armed-no-safety") != null,
    "armed with no safety monitor and nothing warns about it");
  assert(/rig may start in bad weather/.test(byId("now-banners")?.textContent ?? ""),
    "the no-safety-monitor banner is missing");
});

// ---------------------------------- 6. a run with no progress frame yet (#56)
//
// BOTH SIDES OF ONE PREDICATE. `NowScreen` and `shell/SessionColumn` each decide
// "is there a run to show", and both used to require `sequence.progress != null`
// as well as the state. The engine publishes `state: "running"` when it accepts
// a run and attaches `progress` only at the first frame boundary, so for the
// slew / filter change / first sub in between, both surfaces said NO SESSION
// RUNNING over a rig that was working - and the desktop column, which is the
// thing being glanced at from another hub, said it in the corner of every
// screen. Put the `progress != null` clause back into either file and the
// matching half of this test goes red.
await testAsync("a run accepted but not yet reporting progress shows on BOTH surfaces", async () => {
  await act(async () => {
    useStore.setState({
      principal: OPERATOR,
      sequence: { state: "running", target: "NGC 6946", plan_name: "NGC 6946 HaOiii" },
      resumeArm: null,
    } as never);
  });
  await settle();

  assert(byId("now-empty") == null,
    "NOW claims NO SESSION RUNNING over an accepted run that has not reached its first frame");
  assert(byId("now-run-header") != null, "NOW dropped the run header before the first progress frame");
  assert(byId("now-run-controls") != null,
    "NOW dropped PAUSE/STOP before the first progress frame - the controls that stop the run");

  await act(async () => { root.render(createElement(SessionColumn)); });
  await settle();
  assert(byId("session-column") != null,
    "no session-column marker: the fixture is wrong, not the component");
  assert(byId("now-empty") == null,
    "the desktop column claims NO SESSION RUNNING over an accepted run");
  assert(byId("now-run-header") != null,
    "the desktop column dropped the run header before the first progress frame");

  // and the resting state is still the empty card, which is what makes the
  // clause safe to drop rather than merely shorter.
  await act(async () => { useStore.setState({ sequence: { state: "idle" } } as never); });
  await settle();
  assert(byId("now-empty") != null, "an idle engine no longer gets the empty card in the column");

  await act(async () => { root.render(createElement(NowScreen)); });
  await settle();
  assert(byId("now-empty") != null, "an idle engine no longer gets the empty card on NOW");
});

// ------------------------------------------- 7. the FLIP cell is site data
//
// The countdown is computed from the site and inverts to the rig's longitude,
// so the server nulls `hours_to_flip` and collapses `meridian.status` to
// "unknown" for a principal without `view.site_derived`. The cell used to
// render that as "- / unknown", which reads as a mount that has stopped
// answering. The fixture is exactly what a viewer receives.
await testAsync("a viewer's FLIP cell names the missing capability, not a mute mount", async () => {
  await act(async () => { root.render(createElement(NowScreen)); });
  await act(async () => {
    seed({
      principal: VIEWER,
      status: {
        ...((useStore.getState() as any).status),
        meridian: { status: "unknown", hours_to_flip: null, flip_enabled: true, pier_side: "east" },
      },
    });
  });
  await settle();
  const cell = byId("vital-flip");
  assert(cell != null, "no FLIP cell - the assertion below would be vacuous");
  assert(cell.textContent.includes(FLIP_SITE_REASON),
    `the viewer's FLIP cell does not say why it is blank: "${cell.textContent}"`);
  assert(!/unknown/.test(cell.textContent),
    `a withheld countdown still reads as an unknown mount: "${cell.textContent}"`);
});

await testAsync("an operator's FLIP cell still counts down", async () => {
  await act(async () => {
    seed({
      status: {
        ...((useStore.getState() as any).status),
        meridian: { status: "counting", hours_to_flip: 1.63, flip_enabled: true, pier_side: "east" },
      },
    });
  });
  await settle();
  const cell = byId("vital-flip");
  assert(!cell.textContent.includes(FLIP_SITE_REASON),
    "a holder is told they need access they already have");
  assert(/auto/.test(cell.textContent) && /pier east/.test(cell.textContent),
    `the countdown did not come back for a holder: "${cell.textContent}"`);
});

act(() => { root.unmount(); });

const total = passed + failed;
console.log(`nowDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
