// monitorDom.test.tsx - the MONITOR hub, mounted, pressed and refused.
//
//   Run directly:  npx tsx src/next/hubs/monitor/__tests__/monitorDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc`.
//
// WHAT THIS FILE IS FOR, and what goes wrong without each part:
//
//  1. A PRECONDITION MARKER, before every assertion. A DOM test that asserts an
//     ABSENCE over a blank page passes forever, which is how "the screen never
//     rendered" reads as "the alarm correctly stayed quiet".
//  2. THE TWO CLOCKS (#206). `lastFrameAtMs` is a claim about PICTURES and
//     `lastCaptureAtMs` is a claim about the CAMERA. Conflating them made a slow
//     relay dropping preview JPEGs read as CAPTURE STALLED over a perfectly
//     healthy camera. Both directions are asserted, and the healthy direction is
//     the positive control for the alarming one.
//  3. THE CRY-WOLF GUARD. `meridian.status === "due"` is raw hour-angle
//     geometry, not a flip the sequencer owes; the old tile blinked "FLIP DUE"
//     for the ~12 h a target sat west of the meridian.
//  4. USER ABORT IS NOT A FAULT. `detail === "sequence aborted"` must render
//     "You stopped the run" with no fix line and no Help deep-link.
//  5. THE READ PATHS. TONIGHT must issue NO request (the ring is in the store);
//     a past night must carry BOTH `night=` and `level=`; the export button must
//     be a real link at the real path.
//  6. HONEST-DISABLED, twice: a viewer's run controls and a viewer's alert
//     sinks. Both must SAY why and fire nothing.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------------ css stub
// `AlertsScreen` is an AREA ROOT and imports its own `alerts.css` (wave R7's
// rule: `next.css` belongs to one task, every other area ships its own
// stylesheet). Node has no idea what a `.css` file is, so a load hook answers
// with an empty module - the same stub `rigRotatorDom.test.tsx` uses.
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

// Query-AWARE, and mutable: `breakpoint.ts` asks two media queries, and the
// weather block on LIVE only exists above phone. A stub that answers `false` to
// everything can only ever render the phone layout, and every assertion about
// the tablet-only block would then be an assertion about a screen that is not
// on the page. `bpMatch` is read at call time, so flipping it and re-mounting
// changes the layout under test.
const PHONE = () => false;
const TABLET = (q: string) => q.includes("768");
let bpMatch: (q: string) => boolean = PHONE;
win.matchMedia = (q: unknown) => ({
  matches: bpMatch(String(q)), addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} addEventListener() {} send() {} };
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
win.Element.prototype.setPointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.releasePointerCapture = function () { /* jsdom has none */ };
win.scrollTo = () => {};

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement", "HTMLAnchorElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent", "PointerEvent",
  "localStorage", "sessionStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "ResizeObserver", "requestAnimationFrame", "cancelAnimationFrame", "Image",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------- fetch recorder
interface Ask { url: string; method: string; body: any }
const asks: Ask[] = [];
// THE TWO SECRETS BELOW ARE DELIBERATE. The server blanks every stored token
// outbound (`api/alerts.ts`: "the server always blanks it outbound"), so a
// fixture that also blanked them would make the secret-hygiene assertions
// vacuous - "the box is empty" would be a statement about the PAYLOAD, not
// about the component. Seeding them proves the editor never echoes a secret it
// was handed, and never sends one back it did not receive from the user.
const TELEGRAM_SECRET = "TELEGRAM-BOT-TOKEN-MUST-NOT-RENDER";
const SMTP_SECRET = "SMTP-PASSWORD-MUST-NOT-RENDER";
const CONFIG: any = {
  version: 7,
  alerts: [
    {
      id: "sink-1", kind: "ntfy", enabled: true, url: "https://ntfy.sh/astrodeck",
      events: ["run_start", "run_end", "safety"], min_level: "warning", heartbeat_min: 0,
      token_configured: false, verified: false,
    },
    {
      id: "sink-2", kind: "telegram", enabled: true, url: "", chat_id: "123456789",
      events: ["safety"], min_level: "error", heartbeat_min: 0,
      token_configured: true, verified: true, token: TELEGRAM_SECRET,
    },
    {
      id: "sink-3", kind: "email", enabled: false, url: "",
      smtp_host: "smtp.example.com", smtp_port: 587, smtp_from: "rig@example.com",
      smtp_to: "me@example.com", smtp_user: "rig@example.com", smtp_starttls: true,
      events: ["run_end"], min_level: "warning", heartbeat_min: 0,
      token_configured: true, verified: false, token: SMTP_SECRET,
    },
  ],
  deadman_configured: false,
  weather: { enabled: true },
  site: { is_default: false },
};

const ok = (data: any) => ({
  ok: true, status: 200, statusText: "OK", json: async () => data,
});

g.fetch = async (url: any, init: any) => {
  const u = String(url);
  const method = (init?.method ?? "GET").toUpperCase();
  let body: any = null;
  if (init?.body) { try { body = JSON.parse(init.body); } catch { body = init.body; } }
  asks.push({ url: u, method, body });

  if (u.includes("/api/monitor/snapshot")) return ok({});
  if (u.includes("/api/dome/state")) return ok({ connected: false, shutter: "unknown" });
  if (u.includes("/api/sequence/recoverable")) {
    return ok({ recoverable: true, name: "M31 LRGB", frames_done: 12, frames_total: 30 });
  }
  if (u.includes("/api/site/sky")) {
    return ok({ dark_window: { start_iso: new Date(Date.now() - 3600e3).toISOString(),
      end_iso: new Date(Date.now() + 4 * 3600e3).toISOString() } });
  }
  if (u.includes("/api/logs/nights")) {
    return ok({
      current: "2026-09-10", persisted: true,
      nights: [{ night: "2026-09-09", bytes: 4200 }, { night: "2026-09-10", bytes: 90 }],
    });
  }
  if (u.includes("/api/logs?")) return ok([]);
  if (u.includes("/api/alerts/health")) {
    return ok({ undelivered: 0, undelivered_by_sink: {}, deadman: { configured: false, healthy: false, last_ping_age_s: null } });
  }
  if (u.includes("/api/alerts/") && u.includes("/test")) return ok({ ok: true, verified: true });
  if (u.includes("/api/config")) return ok(CONFIG);
  return ok({ ok: true });
};

// ------------------------------------------ imports, AFTER the globals are set
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../store");
const { MonitorHub } = await import("../MonitorHub");
const { LogScreen } = await import("../log/LogScreen");
const { AlertsScreen } = await import("../alerts/AlertsScreen");
const { VIEW_ONLY_NOTE } = await import("../live/RecoveryCards");
const { FLIP_SITE_REASON } = await import("../live/FlipTile");
const { accessPhrase } = await import("../../../../lib/caps");
const { WEATHER_OFF_HINT, WEATHER_OFF_TITLE } = await import(
  "../../weather/conditions/verdict"
);

// -------------------------------------------------------------------- harness
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
const all = (sel: string) => Array.from(container.querySelectorAll(sel)) as any[];
const text = () => (container.textContent as string) ?? "";
const click = async (el: any) => {
  await act(async () => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true })); });
  await settle();
};
/** Type into a controlled React input: React installs its own value tracker,
 *  so assigning `el.value` alone leaves it thinking nothing changed. */
const type = async (el: any, value: string) => {
  await act(async () => {
    Object.getOwnPropertyDescriptor(win.HTMLInputElement.prototype, "value")!.set!.call(el, value);
    el.dispatchEvent(new win.Event("input", { bubbles: true }));
  });
  await settle();
};
/** Requests to exactly `/api/alerts` - the upsert route - and not to any of the
 *  paths BELOW it (`/health`, `/{id}/test`, `/{id}`). */
const upserts = () =>
  asks.filter((a) => a.method === "POST" && a.url.replace(/\?.*$/, "").endsWith("/api/alerts"));

const OPERATOR = {
  role: "operator", email: "op@rig",
  caps: ["view.status", "view.preview", "view.weather", "view.site_derived",
    "control.capture", "control.guide", "control.mount"],
};
// Alert sinks are `config.alerts`, which is ADMIN-only in the role table
// (`capabilities.py:82-124`) - an operator sees them read-only too, so the
// "can edit" half of the sinks section has to be exercised as an admin.
const ADMIN = {
  role: "admin", email: "admin@rig",
  caps: [...OPERATOR.caps, "config.alerts", "config.site_optics", "admin.users", "system.update"],
};
const VIEWER = { role: "viewer", email: null, caps: ["view.status", "view.preview"] };

const NOW = Date.now();

// Named, because the weather tests at the bottom need the SAME slice with one
// field flipped: a second hand-written fixture could differ in some other field
// and then "the radar did not mount" would have a second possible cause.
const WEATHER_ON = {
  enabled: true, stale: false, fetched_ts: NOW / 1000, ignore_tonight: false,
  threshold_pct: 60, sustain_minutes: 45, site_lat: null, site_lon: null,
  forecast: null, astrospheric: null, alert: null,
  now: { ts: "", temp_c: 11.2, dewpoint_c: 7.0, humidity_pct: 74, wind_kmh: 5,
    wind_dir_deg: 225, gust_kmh: 9, cloud_base_m: 1200 },
};

function seed(over: Record<string, unknown> = {}): void {
  useStore.setState({
    principal: OPERATOR,
    authGate: "open",
    equipConnected: true,
    wsPhase: "up",
    wsConnected: true,
    wsLastEvent: NOW,
    telemetryStale: false,
    config: CONFIG,
    logs: [
      { type: "log", ts: NOW / 1000 - 60, data: { level: "info", message: "R 60s captured", source: "sequence" } },
      { type: "log", ts: NOW / 1000 - 30, data: { level: "warning", message: "guiding was lost", source: "guide" } },
    ],
    sequence: {
      state: "running", target: "M31", target_index: 0,
      progress: {
        frames_done: 12, frames_total: 30, percent: 40, elapsed_s: 900, rejected: 0,
        current_exposure_s: 60,
      },
    },
    plan: { name: "M31 LRGB", targets: [{ name: "M31" }, { name: "NGC 7331" }] },
    status: {
      connected: { camera: { connected: true, name: "sim" }, guider: { connected: true, name: "sim" } },
      disk: { free_gb: 412, low: false, critical: false },
      camera: {
        temperature: -10, can_cool: true, width: 100, height: 100, max_gain: 500,
        cooler: { on: true, power: 62, target_c: -10, at_target: true, can_report_power: true },
      },
      mount: { alt: 61, az: 120 },
      meridian: { status: "counting", hours_to_flip: 1.6333, flip_enabled: true, pier_side: "east" },
    },
    guide: { rms_total: 0.91, rms_ra: 0.6, rms_dec: 0.5, recent: [
      { t: 1, ra: 0.2, dec: -0.1 }, { t: 2, ra: -0.3, dec: 0.2 }, { t: 3, ra: 0.1, dec: 0.05 },
    ] },
    weather: WEATHER_ON,
    // Frames are landing; pictures are landing. The stall tests move these.
    lastFrameAtMs: NOW - 4000,
    lastCaptureAtMs: NOW - 4000,
    lastGuideAtMs: NOW - 2000,
    resumeArm: null,
    armedBannerDismissed: null,
    ...over,
  } as never);
}

// ============================================================ LIVE
seed();
await act(async () => { root.render(createElement(MonitorHub)); });
await settle();

test("the hub rendered LIVE - the precondition every assertion below rests on", () => {
  assert(q('[data-testid="hub-monitor"]') != null,
    "no hub-monitor marker: the fixture is wrong, not the component");
  assert(q('[data-testid="monitor-live"]') != null, "the LIVE screen is not the default sub");
  assert(q('[data-testid="monitor-guiding"]') != null, "no guiding card");
  assert(q('[data-testid="monitor-health"]') != null, "no health strip");
  assert(q('[data-testid="monitor-lastframe"]') != null, "no last-frame tile");
  assert(q('[data-testid="monitor-gallery"]') != null, "no GALLERY link");
});

test("all six vitals tiles are drawn, including the two the design dropped", () => {
  for (const id of ["flip", "sensor", "dew", "disk", "dawn", "next"]) {
    assert(q(`[data-testid="vital-${id}"]`) != null, `the ${id} vitals tile is missing`);
  }
});

test("the flip tile counts the seeded schedule down, and names the pier", () => {
  const tile = q('[data-testid="vital-flip"]');
  assert(/1h 38m/.test(tile.textContent), `the flip countdown did not render: "${tile.textContent}"`);
  assert(/auto/.test(tile.textContent), "the flip tile does not say whether the flip is automatic");
  assert(/pier east/.test(tile.textContent), "the flip tile dropped the pier side");
});

test("the other five tiles carry their real numbers, not placeholders", () => {
  assert(/-10\.0/.test(q('[data-testid="vital-sensor"]').textContent), "sensor temperature missing");
  assert(/62% power/.test(q('[data-testid="vital-sensor"]').textContent), "cooler power missing");
  assert(/4\.2°C/.test(q('[data-testid="vital-dew"]').textContent),
    "the dew margin is not ambient minus dew point");
  assert(/412 GB/.test(q('[data-testid="vital-disk"]').textContent), "free disk missing");
  assert(/NGC 7331/.test(q('[data-testid="vital-next"]').textContent),
    "NEXT TARGET names the current target, not the next one");
});

// ------------------------------------------------------------- the two clocks
await testAsync("a stale PICTURE clock with a fresh CAPTURE clock is NOT a stall (#206)", async () => {
  await act(async () => {
    useStore.setState({ lastFrameAtMs: NOW - 600000, lastCaptureAtMs: Date.now() - 4000 } as never);
  });
  await settle();
  const strip = q('[data-testid="monitor-stall"]');
  assert(strip != null, "the stall strip vanished entirely - this assertion would be vacuous");
  assert(!/CAPTURE STALLED/.test(strip.textContent),
    "a dropped preview JPEG was reported as a stalled camera");
});

await testAsync("a stale CAPTURE clock IS a stall, and it says how long", async () => {
  await act(async () => {
    useStore.setState({ lastFrameAtMs: Date.now() - 1000, lastCaptureAtMs: Date.now() - 600000 } as never);
  });
  await settle();
  const strip = q('[data-testid="monitor-stall"]');
  assert(strip != null, "no stall strip for a rig whose frame counter stopped 10 minutes ago");
  assert(/CAPTURE STALLED\?/.test(strip.textContent),
    `the stall alarm did not fire: "${strip.textContent}"`);
  assert(/last frame/.test(strip.textContent), "the strip does not say how stale it is");
});

// -------------------------------------------------------------- the cry-wolf guard
await testAsync("a meridian passed 6 h ago reads calm, not FLIP DUE", async () => {
  await act(async () => {
    useStore.setState({
      status: {
        ...(useStore.getState() as any).status,
        meridian: { status: "due", hours_to_flip: -6, flip_enabled: true, pier_side: "west" },
      },
    } as never);
  });
  await settle();
  const tile = q('[data-testid="vital-flip"]');
  assert(tile != null, "the flip tile disappeared - the assertion below would be vacuous");
  assert(!/FLIP DUE/.test(tile.textContent),
    `raw hour-angle geometry rendered as an alarm: "${tile.textContent}"`);
  assert(/no flip owed/.test(tile.textContent),
    `the calm branch did not render: "${tile.textContent}"`);
});

// ------------------------------------------------------------------ user abort
await testAsync("a run the operator stopped is not dressed as a failure", async () => {
  await act(async () => {
    useStore.setState({
      sequence: {
        state: "aborted", detail: "sequence aborted", target: "M31",
        progress: { frames_done: 15, frames_total: 18, percent: 83, elapsed_s: 900, rejected: 0 },
      },
    } as never);
  });
  await settle();
  const card = q('[data-testid="monitor-failure"]');
  assert(card != null, "no end-of-run card at all");
  assert(/You stopped the run/.test(card.textContent),
    `an abort the operator asked for read as a fault: "${card.textContent}"`);
  assert(/15\/18 frames/.test(card.textContent), "the card does not say where the run stopped");
  assert(q('[data-testid="failure-help"]') == null,
    "a deliberate abort offered a troubleshooting guide for a fault that never happened");
});

// -------------------------------------------------- the interrupted-run recovery
await testAsync("RESUME FROM FRAME posts /api/sequence/recover", async () => {
  await act(async () => { useStore.setState({ sequence: { state: "idle" } } as never); });
  await settle();
  const card = q('[data-testid="run-interrupted"]');
  assert(card != null, "no interrupted-run card for a recoverable run");
  assert(/12\/30/.test(card.textContent), "the card does not name the frame it would resume from");
  const btn = q('[data-action="recover"]');
  assert(btn != null, "the interrupted-run card has no RESUME action");
  eq(btn.getAttribute("aria-disabled"), null, "precondition: an operator found RESUME locked");

  asks.length = 0;
  await click(btn);
  const posts = asks.filter((a) => a.url.includes("/api/sequence/recover"));
  eq(posts.length, 1, "RESUME did not post exactly once");
  eq(posts[0].method, "POST", "the resume was not a POST");
});

// -------------------------------------------------------------- the armed state
await testAsync("RUN ARMED is its own state, with the sentence that says why to wait", async () => {
  await act(async () => {
    useStore.setState({
      sequence: { state: "idle" },
      resumeArm: {
        armed: { id: "s1", name: "M31 LRGB", owed: 58, accepted: 122, total: 180,
          origin: "flow", origin_id: "f1" },
        hold: null,
      },
    } as never);
  });
  await settle();
  const card = q('[data-testid="run-armed"]');
  assert(card != null, "an armed session rendered no card - 'No run active' would be the lie");
  assert(/It starts by itself when its window opens\./.test(card.textContent),
    `the armed copy was lost: "${card.textContent}"`);
  assert(/58 frames owed/.test(card.textContent), "the card does not say what is still owed");
});

// ------------------------------------------------ the run-progress readout
//
// MONITOR > LIVE shipped with none of this (review #18): no frame counter, no
// target, no finish clock, no sub-frame bar, no PAUSING badge. The semantics
// had moved to `session/now/{useEta,useSubFrame}`, which `SessionColumn` mounts
// at tablet and desktop only - so on a phone the glance screen could not say
// how far along the run was or when it would end.
await testAsync("LIVE says how far along the run is, which target, and when it ends", async () => {
  const nowMs = Date.now();
  await act(async () => {
    useStore.setState({
      principal: OPERATOR,
      sequence: {
        state: "running", target: "M31", target_index: 0,
        progress: {
          frames_done: 12, frames_total: 30, percent: 40, elapsed_s: 900, rejected: 2,
          current_exposure_s: 60, eta_s: 3720, eta_confident: true,
          server_now_ms: nowMs, frame_started_at_ms: nowMs - 20_000,
        },
      },
      status: {
        ...(useStore.getState() as any).status,
        filterwheel: { position: 1, names: ["L", "Ha", "Oiii"] },
      },
    } as never);
  });
  await settle();

  const panel = q('[data-testid="monitor-progress"]');
  assert(panel != null,
    "MONITOR > LIVE has no run-progress readout at all - every assertion below would be vacuous");
  const t = panel.textContent as string;
  assert(/12\/30 frames/.test(t), `the frame counter is missing: "${t}"`);
  assert(/40%/.test(t), `the percentage is missing: "${t}"`);
  assert(/M31/.test(t), `the target is missing: "${t}"`);
  assert(/Ha/.test(t), `the filter in front of the sensor is missing: "${t}"`);
  assert(/2 flagged/.test(t), `the flagged count is missing: "${t}"`);

  // The ETA is anchored, not echoed: 3720 s is 1:02 on `fmtCountdown`, and the
  // absolute clock beside it comes from the SAME instant, so the two can never
  // disagree.
  const eta = q('[data-testid="monitor-progress-eta"]');
  assert(eta != null, "no finish clock");
  assert(/^1:0[12]$/.test((eta.textContent as string).trim()),
    `the countdown did not derive from eta_s: "${eta.textContent}"`);
  assert(/done \d\d:\d\d/.test(t), `the absolute finish time is missing: "${t}"`);

  // The exposure in flight, from `useSubFrame`'s client-clock anchor. This is
  // the number that reads 0.0 for five minutes when the skew is re-derived
  // every tick instead of anchored once per frame.
  assert(/into this sub/.test(t), `the sub-frame elapsed figure is missing: "${t}"`);
});

await testAsync("a PAUSED run with a shutter still open says PAUSING, and prints no finish time", async () => {
  const nowMs = Date.now();
  await act(async () => {
    useStore.setState({
      sequence: {
        state: "paused", target: "M31", target_index: 0,
        progress: {
          frames_done: 12, frames_total: 30, percent: 40, elapsed_s: 900, rejected: 0,
          current_exposure_s: 300, eta_s: 3720, eta_confident: true,
          server_now_ms: nowMs, frame_started_at_ms: nowMs - 30_000,
        },
      },
    } as never);
  });
  await settle();
  const panel = q('[data-testid="monitor-progress"]');
  assert(panel != null, "the progress panel vanished - the assertions below would be vacuous");
  assert(q('[data-testid="monitor-pausing"]') != null,
    "a pause with 4:30 of shutter left read as PAUSED, so someone can walk out under an open shutter");
  const t = panel.textContent as string;
  // The finish slot carries the number that IS honest while pausing - how long
  // until the shutter shuts - and never a completion time, which `progress`
  // still carries an `eta_s` for.
  assert(/shutter open - this frame first/.test(t),
    `the finish slot does not say what it is counting: "${t}"`);
  assert(!/done \d\d:\d\d/.test(t),
    `a paused run has no honest finish, yet a completion clock was printed: "${t}"`);
  assert(/PAUSING/.test(t), `the phase is not named: "${t}"`);
});

// ------------------------------------------------------- STOP's in-flight latch
//
// `ActionButton`'s `busy` deliberately does not block a press, so without a
// latch of its own STOP could be re-tapped through the whole ~210 s wind-down
// with no acknowledgement at all (review #19).
await testAsync("STOP latches to STOPPING and refuses to re-post while the teardown runs", async () => {
  await act(async () => {
    useStore.setState({
      sequence: {
        state: "running", target: "M31", target_index: 0,
        progress: { frames_done: 12, frames_total: 30, percent: 40, elapsed_s: 900, rejected: 0,
          current_exposure_s: 60 },
      },
    } as never);
  });
  await settle();
  const stop = q('[data-testid="run-abort"]');
  assert(stop != null, "no STOP control");
  eq(stop.getAttribute("aria-disabled"), null, "precondition: an operator found STOP locked");
  eq((stop.textContent as string).trim(), "STOP", "precondition: STOP is not already latched");

  asks.length = 0;
  await click(stop);                       // arms
  await click(stop);                       // fires
  const posts = () => asks.filter((a) => a.url.includes("/api/sequence/abort"));
  eq(posts().length, 1, "STOP did not post exactly once");

  const after = q('[data-testid="run-abort"]');
  eq((after.textContent as string).trim(), "STOPPING",
    "the button still says STOP over a teardown that is already running");
  eq(after.getAttribute("aria-busy"), "true", "and it does not announce itself as busy");

  // Two more taps through the wind-down: the latch, not the label, is what has
  // to hold.
  await click(after);
  await click(after);
  eq(posts().length, 1, "a re-tap during the teardown re-posted the abort");
});

// -------------------------------------------------------------- viewer, live
await testAsync("a viewer's run controls state the reason and fire nothing", async () => {
  await act(async () => {
    useStore.setState({
      principal: VIEWER,
      resumeArm: null,
      sequence: {
        state: "running", target: "M31", target_index: 0,
        progress: { frames_done: 12, frames_total: 30, percent: 40, elapsed_s: 900, rejected: 0,
          current_exposure_s: 60 },
      },
    } as never);
  });
  await settle();
  const controls = q('[data-testid="run-controls"]');
  assert(controls != null, "the run controls are hidden from a viewer instead of read-only");
  assert(text().includes(VIEW_ONLY_NOTE),
    `the view-only sentence is missing: expected "${VIEW_ONLY_NOTE}"`);
  assert(VIEW_ONLY_NOTE.includes(accessPhrase("control.mount")),
    "the note guesses at a role instead of deriving it from the capability table");

  const pause = q('[data-testid="run-pause"]');
  const stop = q('[data-testid="run-abort"]');
  eq(pause.getAttribute("aria-disabled"), "true", "PAUSE looks live to a viewer");
  eq(stop.getAttribute("aria-disabled"), "true", "STOP looks live to a viewer");
  asks.length = 0;
  await click(pause);
  await click(stop);
  eq(asks.length, 0, "a viewer's press reached the rig");
  const toasts = (useStore.getState() as any).toasts as Array<{ title?: string }>;
  assert(toasts.some((t) => t.title === VIEW_ONLY_NOTE),
    "the refusal was silent - the reason never reached the user");
});

// ------------------------------------------------- the flip clock is site data
//
// `meridian.hours_to_flip` is derived from the site (`lst - ra_hours`), and it
// inverts to the rig's longitude to about 120 m - so the server nulls it and
// collapses `meridian.status` to "unknown" for a principal without
// `view.site_derived`. THE TILE MUST NOT READ THAT AS A BROKEN MOUNT. The
// fixture below is exactly what a viewer receives, and the assertion is that the
// tile blames the redaction rather than the hardware.
await testAsync("a viewer is told the flip clock needs site access, not that the mount is mute", async () => {
  await act(async () => {
    useStore.setState({
      principal: VIEWER,
      status: {
        ...(useStore.getState() as any).status,
        // what `_redact_ws_event` leaves behind: the two non-derived fields,
        // no countdown, no status.
        meridian: { status: "unknown", hours_to_flip: null, flip_enabled: true, pier_side: "east" },
      },
    } as never);
  });
  await settle();
  const tile = q('[data-testid="vital-flip"]');
  assert(tile != null, "the flip tile disappeared - the assertion below would be vacuous");
  assert(tile.textContent.includes(FLIP_SITE_REASON),
    `the viewer is not told why the clock is blank: "${tile.textContent}"`);
  assert(!/does not report a flip/.test(tile.textContent),
    `a withheld countdown was reported as a mount fault: "${tile.textContent}"`);
});

await testAsync("an operator, with the same tile, still gets the countdown", async () => {
  await act(async () => {
    useStore.setState({
      principal: OPERATOR,
      status: {
        ...(useStore.getState() as any).status,
        meridian: { status: "counting", hours_to_flip: 1.63, flip_enabled: true, pier_side: "east" },
      },
    } as never);
  });
  await settle();
  const tile = q('[data-testid="vital-flip"]');
  assert(/1h 3[0-9]m/.test(tile.textContent),
    `the countdown did not come back for a holder: "${tile.textContent}"`);
  assert(!tile.textContent.includes(FLIP_SITE_REASON),
    "a holder is told they need access they already have");
});

await act(async () => { root.unmount(); });

// ============================================================ LOG
const logRoot = createRoot(container);
seed();
asks.length = 0;
await act(async () => { logRoot.render(createElement(LogScreen)); });
await settle();

test("the LOG screen rendered, and TONIGHT asked the server for nothing", () => {
  assert(q('[data-testid="monitor-log"]') != null,
    "no monitor-log marker: the fixture is wrong, not the component");
  assert(q('[data-testid="log-rows"]') != null, "no rows region");
  assert(q('[data-testid="log-night-tonight"]') != null, "no TONIGHT chip");
  assert(q('[data-testid="log-night-2026-09-09"]') != null,
    "the persisted nights never reached the chip row");
  const logReads = asks.filter((a) => a.url.includes("/api/logs?"));
  eq(logReads.length, 0, "TONIGHT re-fetched the ring that is already in the store");
});

test("the ring renders newest first, humanised", () => {
  const rows = q('[data-testid="log-rows"]').textContent as string;
  assert(/Guiding was lost/.test(rows), `the humanised line is missing: "${rows}"`);
  const guideAt = rows.indexOf("Guiding was lost");
  const captureAt = rows.indexOf("R 60s captured");
  assert(guideAt >= 0 && captureAt >= 0 && guideAt < captureAt,
    "the log is oldest-first: the newest line is not at the top");
});

await testAsync("a past night plus ERROR asks for both on the wire", async () => {
  asks.length = 0;
  await click(q('[data-testid="log-level-error"]'));
  await click(q('[data-testid="log-night-2026-09-09"]'));
  const reads = asks.filter((a) => a.url.includes("/api/logs?"));
  assert(reads.length > 0, "selecting a past night asked for nothing at all");
  const last = reads[reads.length - 1].url;
  assert(last.includes("night=2026-09-09"), `the night is missing from the request: ${last}`);
  assert(last.includes("level=error"), `the level filter never reached the route: ${last}`);
});

await testAsync("the export buttons are real links at the real path", async () => {
  const txt = q('[data-testid="log-export-txt"]');
  const jsonl = q('[data-testid="log-export-jsonl"]');
  assert(txt != null && jsonl != null, "the export row is missing");
  eq(txt.tagName, "A", "EXPORT .TXT is not a link, so the browser cannot download it");
  eq(txt.getAttribute("href"), "/api/logs/export?night=2026-09-09&format=txt",
    "the .txt export points somewhere else");
  eq(jsonl.getAttribute("href"), "/api/logs/export?night=2026-09-09&format=jsonl",
    "the .jsonl export points somewhere else");
  eq(txt.getAttribute("target"), "_blank", "the download would replace the running screen");
});

await testAsync("TONIGHT exports the night the SERVER named, not the empty string", async () => {
  await click(q('[data-testid="log-night-tonight"]'));
  const txt = q('[data-testid="log-export-txt"]');
  eq(txt.getAttribute("href"), "/api/logs/export?night=2026-09-10&format=txt",
    "TONIGHT exported a night nobody named");
});

await act(async () => { logRoot.unmount(); });

// ============================================================ ALERTS
const alertRoot = createRoot(container);
seed({ principal: ADMIN });
asks.length = 0;
await act(async () => { alertRoot.render(createElement(AlertsScreen)); });
await settle();

test("the ALERTS screen rendered all four sections, one row per sink", () => {
  assert(q('[data-testid="monitor-alerts"]') != null,
    "no monitor-alerts marker: the fixture is wrong, not the component");
  assert(q('[data-testid="alerts-recent"]') != null, "no RECENT list");
  assert(q('[data-testid="alerts-sinks"]') != null, "no SINKS section");
  assert(q('[data-testid="alerts-deadman"]') != null, "no dead-man's-switch card");
  assert(q('[data-testid="alerts-phone"]') != null, "no THIS PHONE section");
  eq(all('[data-testid="alerts-sink-row"]').length, CONFIG.alerts.length,
    "the sink list does not have one row per configured sink");
  assert(/Guiding was lost/.test(q('[data-testid="alerts-recent"]').textContent),
    "a warning line never reached RECENT");
  // The rebuild renders the destination, so two ntfy topics can be told apart
  // in the dark, and the health verdict as a WORD, not a colour.
  const sinks = q('[data-testid="alerts-sinks"]').textContent as string;
  assert(/https:\/\/ntfy\.sh\/astrodeck/.test(sinks), `the destination line is missing: "${sinks}"`);
  assert(/Verified/.test(sinks), `the per-sink health verdict is missing: "${sinks}"`);
});

await testAsync("TEST on a sink posts /api/alerts/<id>/test, and saves nothing", async () => {
  const testBtn = q('[data-testid="alerts-sink-test"]');
  assert(testBtn != null, "the sink row has no TEST button");
  eq(testBtn.getAttribute("aria-disabled"), null, "precondition: an admin found TEST locked");
  asks.length = 0;
  await click(testBtn);
  const posts = asks.filter((a) => a.url.includes("/api/alerts/sink-1/test"));
  eq(posts.length, 1, "TEST did not post exactly once to the sink's test route");
  eq(posts[0].method, "POST", "the test was not a POST");
  // A test is a round trip, not a save. The legacy panel and this one both
  // reload the config afterwards to refresh the `verified` badge - that is a
  // GET; an upsert here would write whatever draft happened to be open.
  eq(upserts().length, 0, "TEST wrote the sink as well as testing it");
});

await testAsync("adding a sink posts exactly once, with what was typed", async () => {
  await click(q('[data-testid="alerts-sink-add"]'));
  const form = q('[data-testid="alerts-sink-form"]');
  assert(form != null, "ADD SINK opened no form - every assertion below would be vacuous");
  const url = q('[data-testid="alerts-url"]');
  assert(url != null, "a new sink defaults to ntfy, so its topic URL field must be on screen");
  await type(url, "https://ntfy.sh/new-topic");

  asks.length = 0;
  await click(q('[data-testid="alerts-sink-save"]'));
  eq(upserts().length, 1, "SAVE SINK did not post exactly once to /api/alerts");
  const body = upserts()[0].body;
  eq(body.kind, "ntfy", "the saved sink lost its channel kind");
  eq(body.url, "https://ntfy.sh/new-topic", "the typed URL never reached the request body");
  assert(Array.isArray(body.events) && body.events.length > 0,
    "the sink was saved with no events, so it would never send anything");
  assert(q('[data-testid="alerts-sink-form"]') == null,
    "the form stayed open after a successful save, so the next tap re-posts it");
});

await testAsync("a blank URL is refused before it reaches the rig", async () => {
  await click(q('[data-testid="alerts-sink-add"]'));
  assert(q('[data-testid="alerts-sink-form"]') != null, "ADD SINK opened no form");
  asks.length = 0;
  await click(q('[data-testid="alerts-sink-save"]'));
  eq(upserts().length, 0, "an ntfy sink with no URL was posted to the rig");
  const toasts = (useStore.getState() as any).toasts as Array<{ title?: string }>;
  assert(toasts.some((t) => t.title === "Enter an http(s) URL"),
    "the refusal was silent - the validator's reason never reached the user");
  await click(q('[data-testid="alerts-sink-cancel"]'));
  assert(q('[data-testid="alerts-sink-form"]') == null, "CANCEL left the form open");
});

// ------------------------------------------------------------ SECRET HYGIENE
//
// The bot token and the SMTP password are WRITE-ONLY: the rig stores them and
// never sends them back. Two ways to break that in a rebuild - seed the input
// from the sink object, or re-send whatever was seeded - and this asserts both
// are shut, for both kinds, against a payload that DOES carry the secret.
await testAsync("SECRET HYGIENE: a stored bot token is neither rendered nor re-sent", async () => {
  await click(all('[data-testid="alerts-sink-edit"]')[1]);   // the telegram sink
  const form = q('[data-testid="alerts-sink-form"]');
  assert(form != null, "EDIT opened no form");
  // Positive control: the form really did load THIS sink, so an empty secret
  // box below is hygiene and not a form that failed to populate.
  eq(q('[data-testid="alerts-chatid"]').value, "123456789",
    "the edit form did not load the sink - the secret assertion would be vacuous");

  const secret = q('[data-testid="alerts-secret-token"]');
  assert(secret != null, "the telegram form has no bot-token field");
  eq(secret.value, "", "the stored bot token was echoed back into the input");
  eq(secret.getAttribute("type"), "password", "the secret box is not masked");
  assert(!text().includes(TELEGRAM_SECRET),
    "the stored bot token is rendered somewhere on the ALERTS screen");
  // An empty box over a stored secret is ambiguous unless it says which it is.
  eq(secret.getAttribute("placeholder"), "(unchanged)",
    "an already-configured secret does not say that leaving it blank keeps it");
  assert(/Bot token - set/.test(form.textContent as string),
    `the field does not say whether a secret is stored: "${form.textContent}"`);

  asks.length = 0;
  await click(q('[data-testid="alerts-sink-save"]'));
  eq(upserts().length, 1, "saving an edited sink did not post exactly once");
  eq(upserts()[0].body.token, "",
    "the edit re-sent a token the user never typed - anything but '' overwrites the stored one");
  assert(!JSON.stringify(asks).includes(TELEGRAM_SECRET),
    "the stored bot token left the browser");
});

await testAsync("SECRET HYGIENE: the same holds for the SMTP password", async () => {
  await click(all('[data-testid="alerts-sink-edit"]')[2]);   // the email sink
  assert(q('[data-testid="alerts-sink-form"]') != null, "EDIT opened no form");
  eq(q('[data-testid="alerts-smtp-host"]').value, "smtp.example.com",
    "the edit form did not load the sink - the secret assertion would be vacuous");
  eq(q('[data-testid="alerts-smtp-port"]').value, "587",
    "the SMTP port did not load, so the numeric field is not bound to the sink");
  const secret = q('[data-testid="alerts-secret-token"]');
  eq(secret.value, "", "the stored SMTP password was echoed back into the input");
  assert(!text().includes(SMTP_SECRET),
    "the stored SMTP password is rendered somewhere on the ALERTS screen");
  await click(q('[data-testid="alerts-sink-cancel"]'));
});

await testAsync("DELETE goes through the confirm, and posts nothing until it is accepted", async () => {
  asks.length = 0;
  await click(all('[data-testid="alerts-sink-delete"]')[0]);
  const confirm = (useStore.getState() as any).confirm;
  assert(confirm != null, "DELETE removed a channel with no confirm at all");
  assert(/Delete the ntfy sink\?/.test(String(confirm.title)),
    `the confirm does not name what would be deleted: "${String(confirm.title)}"`);
  eq(asks.filter((a) => a.method === "DELETE").length, 0,
    "the sink was deleted before the confirm was answered");
  await act(async () => { (useStore.getState() as any).resolveConfirm(false); });
  await settle();
  eq(asks.filter((a) => a.method === "DELETE").length, 0,
    "declining the confirm deleted the sink anyway");
});

await testAsync("a viewer sees the sinks read-only, with the reason, and writes nothing", async () => {
  await act(async () => { useStore.setState({ principal: VIEWER } as never); });
  await settle();
  const phrase = accessPhrase("config.alerts");
  const reason = `Read-only - changing alerts needs ${phrase}.`;
  const deadReason = `Read-only - changing the monitor URL needs ${phrase}.`;
  assert(text().includes(reason), `the read-only reason is missing: expected "${reason}"`);
  assert(text().includes(deadReason),
    `the dead-man's-switch has no read-only note of its own: expected "${deadReason}"`);

  // Every action, not just the first one. A panel that locks TEST and leaves
  // DELETE live is worse than one that locks nothing.
  const acts: Array<[string, string]> = [
    ["alerts-sink-add", "Adding a sink"],
    ["alerts-sink-test", "Testing"],
    ["alerts-sink-edit", "Editing"],
    ["alerts-sink-delete", "Deleting"],
    ["alerts-deadman-save", "Changing this"],
  ];
  for (const [id, verb] of acts) {
    const el = q(`[data-testid="${id}"]`);
    assert(el != null, `${id} was hidden from a viewer instead of rendered read-only`);
    eq(el.getAttribute("aria-disabled"), "true", `${id} looks live to a viewer`);
    eq(el.getAttribute("title"), `${verb} needs ${phrase}`,
      `${id} does not say what IT would have done`);
  }
  // The write-only boxes are read-only, not `disabled`: still focusable, still
  // carrying the reason.
  for (const id of ["alerts-deadman-input"]) {
    const el = q(`[data-testid="${id}"]`);
    assert(el != null, `${id} vanished for a viewer`);
    eq(el.hasAttribute("disabled"), false, `${id} uses the native disabled attribute`);
    eq(el.getAttribute("aria-disabled"), "true", `${id} looks editable to a viewer`);
  }

  // One press at a time, with the queue emptied first: the store caps the
  // toast list at three (`store.ts` TOAST_MAX), so pressing all five and then
  // looking for the second one's sentence would be reading an eviction rule,
  // not a refusal.
  asks.length = 0;
  for (const [id, verb] of acts) {
    await act(async () => { useStore.setState({ toasts: [] } as never); });
    await click(q(`[data-testid="${id}"]`));
    const toasts = (useStore.getState() as any).toasts as Array<{ title?: string }>;
    assert(toasts.some((t) => t.title === `${verb} needs ${phrase}`),
      `${id} refused in silence - the reason never reached the user`);
  }
  eq(asks.length, 0, "a viewer's press reached the rig");
  const writes = asks.filter((a) => a.method !== "GET");
  eq(writes.length, 0, "the alerts screen wrote something for a viewer");
});

await act(async () => { alertRoot.unmount(); });

// ================================================== LIVE · the weather block
//
// `RadarMap` fetches a GRID of `/api/weather/tile/...` images and re-fires them
// on its own TTL timer for as long as it is mounted. With weather switched off
// every one of those is a 404 (`{"detail":"weather disabled"}`), so the gate is
// not cosmetic: it is the difference between a quiet screen and a request loop
// that outlives the route. WEATHER · RADAR already refuses to mount it; this
// asserts LIVE asks the same question.
//
// jsdom stubs ResizeObserver, so `RadarMap` measures width 0 and lays out no
// <img> even when it IS mounted - "no tile element" alone would therefore pass
// over a mounted map. The map's own `role="application"` box is the positive
// control that says which of the two branches actually rendered.
const RADAR_BOX = '[role="application"][aria-label^="Radar map"]';
const tileAsks = () => asks.filter((a) => a.url.includes("/api/weather/tile"));
const tileImgs = () =>
  Array.from(container.querySelectorAll("img")).filter(
    (n: any) => String(n.getAttribute("src") ?? "").includes("/api/weather/tile"),
  );

bpMatch = TABLET;
const wxRoot = createRoot(container);
seed({ weather: { ...(useStore.getState() as any).weather, enabled: false } });
asks.length = 0;
await act(async () => { wxRoot.render(createElement(MonitorHub)); });
await settle();

test("weather OFF: LIVE mounts no radar and asks for no tile", () => {
  assert(q('[data-testid="monitor-live"]') != null,
    "the LIVE screen is not on the page - every assertion below would be vacuous");
  assert(q('[data-testid="monitor-weather"]') != null,
    "the tablet weather block never rendered, so this is not the case under test");
  assert(q(RADAR_BOX) == null,
    "RadarMap is mounted with weather switched off - its tile grid and its refresh "
    + "timer will keep asking a route that answers 'weather disabled'");
  eq(tileAsks().length, 0,
    `a tile was requested with weather off: ${JSON.stringify(tileAsks().map((a) => a.url))}`);
  eq(tileImgs().length, 0, "a tile <img> was laid out with weather off");
});

test("weather OFF: the conditions card is still framed by the design, the radar card is not", () => {
  assert(q('[data-testid="live-weather-card"]') != null,
    "the sky-conditions widget is mounted bare - wave R7 rewraps it in a Card");
  assert(q('[data-testid="live-radar-card"]') == null,
    "a radar card was drawn around a radar that never mounted");
});

test("weather OFF: the block says so, in WEATHER · RADAR's own words", () => {
  const card = q('[data-testid="monitor-radar-off"]');
  assert(card != null, "the radar was removed with nothing in its place - an empty gap "
    + "is not an answer to 'why is there no radar'");
  const t = card.textContent as string;
  assert(/WEATHER IS OFF/.test(t), `the off-card lost its label: "${t}"`);
  assert(t.includes(`${WEATHER_OFF_TITLE} ${WEATHER_OFF_HINT}`),
    `the off-card paraphrases the shared reason copy: "${t}"`);
  assert(/No radar or satellite tiles are fetched while it is off\./.test(t),
    `the off-card does not say what is NOT happening: "${t}"`);
  assert(q('[data-testid="monitor-radar-off-cta"]') != null,
    "the off-card states the reason and offers no way to change it");
});

await testAsync("weather ON: the map mounts, and the off-card is gone", async () => {
  // The whole fixture, not a spread of whatever is in the store: mounting
  // `SkyConditionsPanel` fires its own cold `GET /api/weather`, the recorder
  // answers that route with `{ok:true}`, and `normalizeWeather` turns that into
  // a slice with no forecast and no `fetched_ts`. Re-seeding here puts a REAL
  // feed back under the card so the age line is derived from a fetch time
  // rather than from the mock's fall-through.
  await act(async () => {
    useStore.setState({
      weather: { ...WEATHER_ON, enabled: true, stale: false,
        fetched_ts: Date.now() / 1000 - 8 * 60 },
    } as never);
  });
  await settle();
  assert(q('[data-testid="monitor-weather"]') != null, "the weather block vanished");
  assert(q(RADAR_BOX) != null,
    "the gate is stuck shut: weather is on and the radar still did not mount");
  assert(q('[data-testid="monitor-radar-off"]') == null,
    "the off-card is still up over a live map");

  // Both widgets are keeps; their CHROME is the design's (wave R7 T-R7-10).
  const wx = q('[data-testid="live-weather-card"]');
  const radar = q('[data-testid="live-radar-card"]');
  assert(wx != null, "the sky-conditions widget lost its Card");
  assert(radar != null, "the radar widget lost its Card");
  assert(radar.contains(q(RADAR_BOX)),
    "the radar card is drawn somewhere other than around the radar");
  // The eyebrow lines carry what the widgets cannot say about themselves: how
  // old the forecast is, and that the map keeps fetching while this is open.
  assert(/8m old/.test(wx.textContent as string),
    `the conditions card does not say how old the forecast is: "${wx.textContent}"`);
  assert(/tiles refresh while this screen is open/.test(radar.textContent as string),
    `the radar card does not say that it keeps fetching: "${radar.textContent}"`);
});

await act(async () => { wxRoot.unmount(); });
bpMatch = PHONE;

const total = passed + failed;
console.log(`monitorDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
