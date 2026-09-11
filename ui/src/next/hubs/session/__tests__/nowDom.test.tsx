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

// ------------------------------------------------------------------ css stub
// The Now screen is an area root now: `NowEmpty` imports `now.css`, where a
// runnable row's two sub-lines are taught to say how they end. Node has no idea
// what a `.css` file is, so a load hook answers with an empty module - the same
// stub `sessionSheets.test.tsx` uses.
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

// --- TONIGHT'S LIST fixtures (D-FU-3) ---------------------------------------
// One saved flow, one saved plan, and the tonight payload for the flow. The
// plan is a real `SequencePlan` because `POST /api/sequence/start` takes the
// WHOLE PLAN in its body (app.py:6551-6563) and the assertion below reads it
// back out of the request.
const PLAN_ROW = {
  id: "p1", name: "Veil east", frames: 24, integration_min: 72, targets: 1, mtime: 5,
};
const PLAN = {
  name: "Veil east",
  guide: false, dither_every: 0, dither_pixels: null, autofocus_every: 0,
  cool_to: null, cool_timeout_s: 600, apply_filter_offsets: null,
  refocus_on_temp_delta_c: null, meridian_flip: false, recover_guiding: null,
  hfr_reject_factor: null, park_when_done: false, warm_cooler_when_done: false,
  targets: [{
    id: "pt1", name: "NGC 6960", ra_hours: 20.76, dec_deg: 30.72,
    center: true, autofocus_first: false, calibration: false,
    steps: [{
      id: "ps1", filter: "L", exposure_s: 180, gain: 100, offset: 30,
      binning: 1, count: 24, frame_type: "Light",
    }],
  }],
};
const FLOW_REC = {
  id: "f1", name: "Veil east flow", folder: "", tagline: "", readonly: false,
  updated_ts: 20, graph: { nodes: [], edges: [] },
};
function card(id: string, name: string, lastRun: number | null, updated: number): any {
  return {
    id, name, folder: "", tagline: "", readonly: false, stages: 3, wires: 2,
    last_run: lastRun, last_result: "ok", updated_ts: updated,
  };
}
/** A resolved night whose dark window opens in an hour and holds the target for
 *  three of its six hours - so the fold has something real to say. */
function tonightOk(): any {
  const ds = Math.floor(Date.now() / 1000) + 3600;
  return {
    ok: true, reason: "",
    night: {
      dusk_unix: ds - 1800, dawn_unix: ds + 7 * 3600,
      dark_start_unix: ds, dark_end_unix: ds + 6 * 3600,
    },
    targets: [{ label: "NGC 6960", window: { start_unix: ds, end_unix: ds + 3 * 3600 }, curve: [] }],
    story: [], brief: "",
  };
}

/** `GET /api/sequence/recoverable`. Mutable so the Interrupted card can be put
 *  on screen without a second harness. */
let RECOVERABLE: any = { recoverable: false };

function answer(url: string): any {
  if (url.includes("/api/sessions/sess-1")) return SESSION;
  if (url.includes("/api/sessions")) return { sessions: [{ id: "sess-1", name: "M31 LRGB", status: "active", created_ts: 1, updated_ts: 2, nights: 2, accepted: 2, total: 18, auto_resume: true }] };
  if (url.includes("/api/sequence/stack")) return STACK;
  if (url.includes("/api/sequence/recoverable")) return RECOVERABLE;
  if (url.includes("/api/reports")) return [];
  // Ordered longest-path-first: the catch-alls at the bottom must not swallow
  // the specific routes above them.
  if (url.includes("/api/plans/p1")) return PLAN;
  if (url.includes("/api/plans")) return [PLAN_ROW];
  if (url.includes("/api/flows/f1/tonight")) return tonightOk();
  if (url.includes("/api/flows/f2/tonight")) return { ok: false, reason: "No observatory site is set." };
  if (url.includes("/api/flows/f1/run")) return { frames: 24, unmapped: [] };
  if (url.includes("/api/flows/compile")) return { ok: true, unmapped: [], stages: [], warnings: [] };
  if (url.includes("/api/flows/f1")) return FLOW_REC;
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
const gets = (path: string) => asks.filter((a) => a.method === "GET" && a.url.includes(path));
const idxOfPost = (path: string) => asks.findIndex((a) => a.method === "POST" && a.url.includes(path));

// ------------------------------------------ imports, AFTER the globals are set
const { createElement, Fragment, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../store");
const { NowScreen } = await import("../now/NowScreen");
const { SessionColumn } = await import("../../../shell/SessionColumn");
const { FLIP_SITE_REASON } = await import("../../monitor/live/FlipTile");
const { RUN_CONTROL_REASON } = await import("../now/RunControls");
const { ConfirmCard } = await import("../../../shell/ConfirmCard");
const { TONIGHT_LOCK_NOTE, RUNNABLE_ROW_CAP, TONIGHT_RESOLVE_CAP } = await import("../now/NowEmpty");
const { RERUN_PHONE_REASON } = await import("../now/Interrupted");

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

// ------------------------------------------------------------- the stylesheet
// The load hook above hands the module system an empty object, so the screen's
// own rules are read from disk: they are what the browser gets, so they are
// what is graded.
const { readFileSync } = await import("node:fs");
const NOW_CSS = readFileSync(new URL("../now/now.css", import.meta.url), "utf8");
/** The declaration block of ONE rule, by its exact selector. Throws when the
 *  selector is gone, which is the interesting half of the failure. */
function cssRule(css: string, selector: string): string {
  const at = css.indexOf(`${selector} {`);
  if (at < 0) throw new Error(`now.css has no rule for \`${selector}\``);
  const end = css.indexOf("}", at);
  return css.slice(at, end);
}

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

// ============================================================ 8. TONIGHT'S LIST
//
// D-FU-3's whole point: a phone with the plan editor honest-disabled had NO WAY
// to start a saved plan. The card that replaced RECENT FLOWS carries both kinds
// of runnable, one verdict per flow, and two guards that cost real nights:
//
//   * A LIST ROW NEVER STOPS A RUN. `useFlowRunControls().act()` is a RUN/STOP
//     toggle. A second press while something is running would abort it, from a
//     row that still read RUN. The press has to divert to the running session.
//   * TONIGHT IS `view.site_derived`. Every number in the payload is
//     f(latitude, longitude) (app.py:4655-4662). A viewer must fire NOTHING at
//     that route - not fire it and hide the answer.
//
// The confirm card is mounted alongside the screen here because the plan RUN
// path is a question: `pushConfirm` writes the store slice and `ConfirmCard`
// (the shell's, the same one NextApp mounts) is what turns it into a press.

await act(async () => {
  root.render(createElement(Fragment, null,
    createElement(NowScreen), createElement(ConfirmCard)));
});
await act(async () => {
  const st = useStore.getState() as any;
  useStore.setState({
    principal: OPERATOR,
    sequence: { state: "idle" },
    resumeArm: null,
    site: null,
    masters: [],
    // f2 carries the SAME NAME as the saved plan on purpose - see the test
    // below. f1 is the more recent run, so it sorts first.
    flows: {
      ...st.flows,
      cards: [card("f1", "Veil east flow", 100, 10), card("f2", "Veil east", 50, 20)],
      libraryLoaded: true, libraryError: null,
      record: null,
      run: { ...st.flows.run, phase: "idle" },
    },
  } as never);
});
await settle();
await settle();
await settle();

await testAsync("the list rendered, with both kinds of runnable on it", async () => {
  assert(byId("now-empty") != null, "no now-empty: the fixture is wrong, not the component");
  assert(byId("now-runnables") != null, "no now-runnables card");
  const rows = container.querySelectorAll("[data-runnable]");
  assert(rows.length > 0, "the list is empty - every assertion below would be vacuous");

  assert(byId("run-flow-f1") != null, "the saved flow is missing from the list");
  assert(byId("run-plan-p1") != null,
    "the saved PLAN is missing - a phone still has no way to start one, "
    + "which is the gap D-FU-3 closes");
  eq(byId("run-plan-p1").textContent, "RUN",
    "the plan's verb is not RUN (section 0 Q3: a plan row RUNS, it does not LOAD):");

  // Flows first, most recent first; plans after. And the flow named exactly
  // like the plan is STILL THERE - they are different documents on different
  // routes and dropping either would hide something the operator saved.
  const kinds = [...rows].map((r: any) => r.getAttribute("data-runnable"));
  eq(kinds[0], "flow", "the list does not lead with flows:");
  eq(kinds[kinds.length - 1], "plan", "plans are not last:");
  eq(kinds.filter((k: string) => k === "flow").length, 2, "a flow went missing:");
  assert(byId("run-flow-f2") != null,
    "the flow sharing the plan's name was de-duplicated away");
});

await testAsync("RUN on a flow opens it and posts /api/flows/f1/run exactly once", async () => {
  eq(posts("/api/flows/f1/run").length, 0, "precondition: nothing has run yet");
  const abortsBefore = posts("/api/sequence/abort").length;
  await click(byId("run-flow-f1"));
  await settle();

  eq(posts("/api/flows/f1/run").length, 1, "flow run posts:");
  // The record has to be the OPEN one before `flowsRun` can post against it -
  // it reads `flows.record?.id`, so a run without the GET first posts against
  // whatever was open before, or nothing at all.
  const recordGet = asks.findIndex((a) => a.method === "GET" && /\/api\/flows\/f1$/.test(a.url));
  assert(recordGet >= 0,
    "the flow record was never fetched - RUN posted against a flow it never opened");
  assert(recordGet < idxOfPost("/api/flows/f1/run"),
    "the run was posted BEFORE the flow was opened");
  eq(posts("/api/sequence/abort").length, abortsBefore, "RUN reached the abort route");
});

await testAsync("a SECOND press while a run is live diverts to the session - it never aborts", async () => {
  await act(async () => {
    const st = useStore.getState() as any;
    useStore.setState({
      flows: { ...st.flows, run: { ...st.flows.run, phase: "running" } },
    } as never);
  });
  await settle();
  const abortsBefore = posts("/api/sequence/abort").length;
  const runsBefore = posts("/api/flows/f1/run").length;
  win.location.hash = "#/session/flows";

  await click(byId("run-flow-f1"));
  await settle();

  eq(posts("/api/sequence/abort").length, abortsBefore,
    "the second press ABORTED the live run, from a row that still read RUN");
  eq(posts("/api/flows/f1/run").length, runsBefore,
    "the second press started a second run over a live one");
  eq(win.location.hash, "#/session/now",
    "the second press did not take the operator to the run it refused to touch:");
});

await testAsync("a resolved night reaches the row as a duration and a clock", async () => {
  const text = byId("now-runnables").textContent as string;
  assert(/usable from \d\d:\d\d/.test(text),
    `no tonight verdict on any row: "${text.slice(0, 200)}"`);
  // ...and the server's REFUSAL for the other flow is printed as its own
  // sentence, not folded into a generic error or into a zero.
  assert(/No observatory site is set\./.test(text),
    `the server's refusal was rewritten or swallowed: "${text.slice(0, 200)}"`);
});

// The probe found both sub-lines cut mid-sentence at 390 and 820, with no
// ellipsis and nothing to press: the row's sub is ONE `.nx-row-sub`, which
// ellipsises its own text, and the two stacked lines inside it simply
// overflowed it. The verdict is the sentence the row exists to deliver ("No
// observatory site is set, ..." is what to go and fix), so it wraps in full and
// the description above it ellipsises on one line. jsdom lays nothing out; what
// is graded is the class on each line and the rule in the stylesheet.
await testAsync("both sub-lines say how they end - the verdict whole, the description with an ellipsis", async () => {
  const rows = [...container.querySelectorAll("[data-runnable]")] as any[];
  assert(rows.length > 0, "the list is empty - this would pass over a blank page");
  const row = rows.find((r: any) => r.querySelector(".nx-runnable-verdict") != null);
  assert(row != null, "no row carries a tonight verdict - the fixture is wrong, not the component");

  assert(row.querySelector(".nx-runnable") != null,
    "the row does not carry the class that stops the wrapper clipping its lines");
  const meta = row.querySelector(".nx-runnable-meta");
  assert(meta != null, "the description line does not carry its own class");
  assert((meta.getAttribute("style") || "") === "",
    "the sub-lines are laid out by an inline style again, where no stylesheet can reach them");

  assert(/overflow:\s*visible/.test(cssRule(NOW_CSS, ".nx-runnable .nx-row-sub")),
    "the wrapper still clips its children, which is the cut with no ellipsis");
  assert(/text-overflow:\s*ellipsis/.test(cssRule(NOW_CSS, ".nx-runnable-meta")),
    "the description can be cut without an ellipsis again");
  assert(/white-space:\s*normal/.test(cssRule(NOW_CSS, ".nx-runnable-verdict")),
    "the verdict is back on one nowrap line, so the sentence that says what to fix is cut");

  // ...and the sentence itself is whole in the DOM: a fix that shortened the
  // server's refusal to make it fit would pass every assertion above.
  const verdict = (row.querySelector(".nx-runnable-verdict").textContent || "") as string;
  assert(verdict.trim().length > 0, "the verdict line is empty");
  assert(!verdict.includes("…") && !verdict.includes("..."),
    `the verdict was truncated in the copy instead of wrapped in the layout: "${verdict}"`);
});

await testAsync("a viewer gets the list and the verbs, locked, and fires NOTHING at /tonight", async () => {
  await act(async () => {
    const st = useStore.getState() as any;
    useStore.setState({
      principal: VIEWER,
      // A third flow nobody has resolved yet: without it the tonight effect
      // would not re-run at all and "no request fired" would be vacuous.
      flows: {
        ...st.flows,
        cards: [...st.flows.cards, card("f9", "Never resolved", null, 1)],
        run: { ...st.flows.run, phase: "idle" },
      },
    } as never);
  });
  await settle();
  await settle();
  const before = asks.length;

  assert(byId("now-runnables") != null, "the viewer lost the list entirely");
  const verb = byId("run-flow-f1");
  assert(verb != null, "the verb was hidden from the viewer instead of locked");
  eq(verb.getAttribute("aria-disabled"), "true", "the verb is not locked for a viewer");
  eq(verb.getAttribute("title"), "Running a flow needs operator or admin access.",
    "the verb does not say who may press it:");

  // T-R7-21a item 11: the same gate, the row's own noun. A saved PLAN refused
  // with the word "flow" sends the reader looking for a flow that is not on
  // the screen; `runBlockedReason` takes the noun, and this is the caller that
  // has two kinds of row under one reason.
  const planVerb = byId("run-plan-p1");
  assert(planVerb != null, "the plan row is gone, so the noun below is not being graded");
  eq(planVerb.getAttribute("title"), "Running a plan needs operator or admin access.",
    "the plan row is refused with the wrong noun:");

  await click(verb);
  eq(asks.length, before, "a viewer's press reached the server");
  eq(gets("/api/flows/f9/tonight").length, 0,
    "a viewer's screen fired the site-derived tonight route - the whole point of the gate");
  const text = byId("now-runnables").textContent as string;
  assert(!/usable from/.test(text), "a viewer was shown a site-derived window");
  // The note sits above the rows, so it is read off the whole card.
  const cardText = byId("now-empty").textContent as string;
  assert(cardText.includes(TONIGHT_LOCK_NOTE),
    `the viewer is not told why the verdicts are missing: "${cardText.slice(0, 400)}"`);
});

await testAsync("RUN on a saved plan asks first, and KEEP posts nothing", async () => {
  await act(async () => { useStore.setState({ principal: OPERATOR } as never); });
  await settle();
  eq(posts("/api/sequence/start").length, 0, "precondition: nothing has started yet");

  await click(byId("run-plan-p1"));
  await settle();

  const scrim = byId("confirm-scrim");
  assert(scrim != null, "pressing RUN on a plan started it with no question at all");
  const q = scrim.textContent as string;
  assert(/Veil east/.test(q), `the question does not name the plan: "${q}"`);
  assert(/NGC 6960/.test(q), `the question does not name the first target: "${q}"`);
  eq(posts("/api/sequence/start").length, 0, "the plan started before the question was answered");

  await click(byId("confirm-keep"));
  await settle();
  eq(posts("/api/sequence/start").length, 0, "KEEP started the run anyway");
  assert(byId("confirm-scrim") == null, "KEEP left the question on screen");
});

await testAsync("confirming LOADS the plan and posts /api/sequence/start exactly once", async () => {
  await click(byId("run-plan-p1"));
  await settle();
  assert(byId("confirm-yes") != null,
    "no affirmative on the card - the pre-flight refused a plan this fixture does not block");

  await click(byId("confirm-yes"));
  await settle();

  eq(posts("/api/sequence/start").length, 1, "sequence starts:");
  assert(gets("/api/plans/p1").length >= 1,
    "the plan document was never read - RUN started a plan the engine was never handed");

  // The body IS the plan (StartSequenceBody subclasses SequencePlan,
  // app.py:6551-6563). A start that posted an id or an empty body would 422.
  const body = posts("/api/sequence/start")[0].body;
  eq(body?.name, "Veil east", "the posted body is not the plan:");
  assert(Array.isArray(body?.targets) && body.targets.length === 1,
    "the posted body carries no targets - the engine would refuse it as an empty plan");
  eq(body?.force, false, "a phone press silently forced past the horizon pre-flight:");

  // ...and the plan really was loaded into the editor's slot, so the run and
  // the editor are looking at the same document.
  const st = useStore.getState() as any;
  eq(st.plan?.name, "Veil east", "the store's plan was not set:");
  eq(st.loadedPlanId, "p1", "the loaded plan id was not set:");
});

await testAsync("the list caps at twelve rows and at five tonight requests", async () => {
  // Fourteen flows, none of them ever resolved. Two caps are on trial:
  //   * RUNNABLE_ROW_CAP - twelve rows and a door, not the Flows screen;
  //   * TONIGHT_RESOLVE_CAP - `/api/flows/{id}/tonight` is one astropy pass per
  //     resolved target, so a screen that fired one per row would spend a
  //     minute of the rig's CPU every time the app was opened.
  const many: any[] = [];
  for (let i = 0; i < 14; i++) many.push(card(`m${i}`, `Flow ${i}`, 1000 - i, 1));
  await act(async () => {
    const st = useStore.getState() as any;
    useStore.setState({
      principal: OPERATOR,
      flows: { ...st.flows, cards: many, run: { ...st.flows.run, phase: "idle" } },
    } as never);
  });
  await settle();
  await settle();
  await settle();

  const rows = container.querySelectorAll("[data-runnable]");
  eq(rows.length, RUNNABLE_ROW_CAP, "rows on the list:");
  assert(byId("now-runnables-more") != null,
    "twelve of fifteen runnables are on screen and nothing says where the rest are");

  const asked = asks.filter((a) => a.method === "GET" && /\/api\/flows\/m\d+\/tonight$/.test(a.url));
  eq(asked.length, TONIGHT_RESOLVE_CAP,
    "tonight requests fired for the new flows (the cap is what keeps a phone off the rig's CPU):");
  // Newest first: m0 has the most recent `last_run`, m13 the oldest, and the
  // five that were asked have to be the top five of the list.
  eq(asked[0].url.includes("/m0/"), true, "the newest flow was not asked first:");
  assert(!asked.some((a) => /\/m[5-9]|\/m1[0-3]/.test(a.url)),
    "a row below the fold was resolved ahead of one above it");
});

// ===================== 10. a STALE run phase is not a run, and RUN still runs
//
// `flows.run.phase` is written once by `flowsRun` and by nothing else in the
// app - no socket topic, no run-state GET (`flowsSlice.ts` §G-1). So after ONE
// flow run it reads "running" for the life of the page, and the guard above it
// was stuck on: every later RUN press navigated to a Now screen with no run on
// it, silently, on the only phone-reachable way to start a saved plan. And with
// the guard simply removed, `act()`'s toggle would have sent
// `POST /api/sequence/abort` from a row labelled RUN.
//
// SABOTAGE: drop the `runIsLive(seq)` half of `divertedWhileRunning`, or the
// `clearStaleRunPhase()` call in `runFlow`, and this goes red.
await testAsync("a leftover run phase with an idle engine neither diverts nor aborts",
  async () => {
    // A fresh mount, so the screen's own "I just pressed RUN" memory is empty -
    // which is the state of a page whose run ended hours ago.
    await act(async () => { root.render(createElement("div")); });
    await act(async () => {
      const st = useStore.getState() as any;
      useStore.setState({
        principal: OPERATOR,
        sequence: { state: "idle" },
        resumeArm: null,
        flows: {
          ...st.flows,
          cards: [card("f1", "Veil east flow", 100, 10)],
          libraryLoaded: true, libraryError: null,
          record: null,
          // The leftover: the engine is idle and this still says running.
          run: { ...st.flows.run, phase: "running" },
        },
      } as never);
    });
    await act(async () => {
      root.render(createElement(Fragment, null,
        createElement(NowScreen), createElement(ConfirmCard)));
    });
    await settle();
    await settle();

    assert(byId("run-flow-f1") != null,
      "the flow row never rendered - the assertions below would be vacuous");
    const abortsBefore = posts("/api/sequence/abort").length;
    const runsBefore = posts("/api/flows/f1/run").length;
    win.location.hash = "#/session/flows";

    await click(byId("run-flow-f1"));
    await settle();
    await settle();

    eq(posts("/api/sequence/abort").length, abortsBefore,
      "a press on a row labelled RUN sent the ABORT route, because act() read the "
      + "leftover phase as a live run:");
    eq(posts("/api/flows/f1/run").length, runsBefore + 1,
      "RUN did not start the flow - the leftover phase swallowed the press:");
    eq(win.location.hash, "#/session/flows",
      "the press was diverted to a Now screen with no run on it:");
  });

// ============================ 11. the interrupted card names what its verb does

await testAsync("RE-RUN names the door it opens, not a run it does not start", async () => {
  // The verb read RE-RUN FROM FRAME 1 and opened the plan editor. It is not
  // wired to POST /api/sequence/start instead, because the recoverable record is
  // the SERVER'S and the editor starts the STORE'S draft - the two are the same
  // plan only if nothing has been loaded since, so a one-press re-run here would
  // start whatever plan happened to be open under this run's frame count.
  RECOVERABLE = {
    recoverable: true, session_id: "sess-1", name: "M31 LRGB",
    frames_done: 7, frames_total: 18, ts: 1_757_000_000,
  };
  await act(async () => { root.render(createElement("div")); });
  await act(async () => {
    useStore.setState({ principal: OPERATOR, sequence: { state: "idle" } } as never);
  });
  await act(async () => { root.render(createElement(NowScreen)); });
  await settle();
  await settle();

  const card2 = byId("now-interrupted");
  assert(card2 != null,
    "the interrupted card never rendered - the fixture is wrong, not the label");
  const rerun = byId("interrupted-rerun");
  assert(rerun != null, "the second verb never rendered");
  const label = (rerun.textContent ?? "").trim();
  assert(!/RE-RUN FROM FRAME 1/.test(label),
    `the verb still promises a run it does not start: "${label}"`);
  assert(/PLAN EDITOR/.test(label),
    `the verb does not name the door it opens: "${label}"`);
  const before = posts("/api/sequence/start").length;
  const hashBefore = win.location.hash;
  await click(rerun);
  await settle();
  eq(posts("/api/sequence/start").length, before,
    "the press started a run, which is not what its label now says:");
  // This harness reports phone width (every media query answers false), and at
  // phone width the editor itself is honest-locked - so the press explains
  // rather than navigating, and the reason is the same constant every other
  // locked door to that editor quotes.
  eq(rerun.getAttribute("aria-disabled"), "true",
    "the door to a tablet-only editor is open on a phone");
  assert((rerun.getAttribute("title") ?? "").includes(RERUN_PHONE_REASON),
    `the phone lock does not quote the shared reason: "${rerun.getAttribute("title")}"`);
  eq(win.location.hash, hashBefore,
    "a locked press navigated anyway:");
  const text = card2.textContent as string;
  assert(/whichever plan is LOADED/.test(text),
    `the card does not say why re-running is two steps: "${text.slice(0, 300)}"`);
  RECOVERABLE = { recoverable: false };
});

act(() => { root.unmount(); });

const total = passed + failed;
console.log(`nowDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
