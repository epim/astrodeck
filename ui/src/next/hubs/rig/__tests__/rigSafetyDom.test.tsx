// rigSafetyDom.test.tsx - the SAFETY MONITOR sheet, MOUNTED (plan B.9, D.2).
//
//   Run directly:  npx tsx src/next/hubs/rig/__tests__/rigSafetyDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT THIS IS ABOUT. Six decisions this sheet makes, each of which is a
// promise about a rig nobody is standing next to:
//
//   1. It renders. The precondition names THREE separate cards, because "no
//      control has the wrong attribute" is trivially true of a blank page
//      (astrodeck-ui-probe-traps: a broken probe reads as a passing app).
//   2. NO FABRICATED BARS. With `reading.detail` absent, the single status row
//      renders and the words RAIN / WIND / CLOUD are nowhere on the screen. This
//      is the assertion the sabotage run is aimed at.
//   3. The trip chain is BUILT: `park` and `abort_park_warm` produce different
//      node sets on the same rig, and a dim node states its reason.
//   4. A config write echoes the WHOLE block. The exact body is asserted, not a
//      substring, because a partial echo is how the server's wholesale-replace
//      contract silently drops a field.
//   5. The sun cone is the app's one FIELD-LEVEL DOUBLE GATE: an operator who
//      holds config.safety still cannot touch it, while the rest of the sheet
//      stays live. And disarming asks first - a hold confirm, before any POST.
//   6. A viewer sees every control, dimmed, with the reason, and pressing them
//      sends nothing.
//
// Convention: jsdom by hand, createRoot + act, native events, a recording fetch
// mock, printed tally + the counts export (shell-and-tests.md section 4).

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/#/rig/devices/safety", pretendToBeVisual: true },
);
const win = dom.window as any;

// `wide` drives the ONE breakpoint decision on this sheet: the escalation card
// is a read-only list on a phone and the real EscalationPanel above it.
let wide = false;
win.matchMedia = (q: string) => ({
  matches: wide && /min-width:\s*768px/.test(q),
  addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} addEventListener() {} send() {} };
win.Element.prototype.setPointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.releasePointerCapture = function () { /* jsdom has none */ };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "HTMLSelectElement", "Element", "Node", "Event", "CustomEvent", "MouseEvent",
  "KeyboardEvent", "localStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "requestAnimationFrame", "cancelAnimationFrame",
  // NOT `performance`: this jsdom's delegates to the global one, so copying it
  // onto globalThis makes performance.now() recurse until the stack blows.
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------- fetch recorder
// A faithful little server: it REMEMBERS what was posted, so a save followed by
// the store's loadConfig() re-seeds the sheet from the value that was actually
// stored rather than from the fixture. A mock that always returned the seed
// would let a write that silently dropped a field still look green.
const asked: string[] = [];
const bodies: Record<string, unknown>[] = [];

type SafetyBlock = Record<string, unknown>;

const SEED_SAFETY: SafetyBlock = {
  enabled: true,
  preset: "backyard",
  poll_each_frame: true,
  sky_fallback_hold: true,
  meridian_flip_warn_min: 15,
  min_alt_deg: 10,
  max_alt_deg: 90,
  horizon: null,
  nogo_box: null,
  enforce_pier_limits: false,
  twilight_deg: -12,
  solar_avoidance: true,
  solar_exclusion_deg: 30,
  on_unsafe: "pause",
  unsafe_consecutive: 3,
  resume_when_safe: true,
  resume_safe_consecutive: 3,
  max_pause_min: 120,
  close_dome_on_unsafe: false,
  close_dome_when_done: false,
  reopen_dome_when_safe: false,
};

const SEED_ESCALATION = {
  require_cooling: true,
  cooling_action: "abort",
  require_guiding: false,
  guiding_action: "warn",
  af_failure_action: "skip",
  hfr_reject_action: "retake",
  hfr_retake_limit_per_target: 4,
  hfr_reject_factor: 1.5,
  no_progress_watchdog_s: 900,
  reconnect_resume: true,
  reconnect_retries: 3,
};

let serverConfig: Record<string, unknown> = {};
let domeState = { connected: false, shutter: "unknown", requires_park_before_close: true, can_bind: false };
let alertHealth: unknown = {
  undelivered: 0,
  undelivered_by_sink: {},
  deadman: { configured: true, healthy: true, last_ping_age_s: 42 },
};

function freshConfig(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    version: 7,
    site: { name: "home", latitude: 37, longitude: -122, elevation_m: 20 },
    optics: { focal_length_mm: 500, aperture_mm: 100 },
    active_profile_id: null,
    safety: JSON.parse(JSON.stringify(SEED_SAFETY)),
    cooling: { warm_ramp: true, warm_rate_c_per_min: 2, warm_ambient_c: null },
    escalation: JSON.parse(JSON.stringify(SEED_ESCALATION)),
    alerts: [],
    deadman_url: "",
    ...over,
  };
}

g.fetch = async (url: string, init?: { method?: string; body?: string }) => {
  const method = init?.method ?? "GET";
  const u = String(url);
  asked.push(`${method} ${u}`);
  const body = init?.body ? (JSON.parse(init.body) as Record<string, unknown>) : {};
  if (method !== "GET") bodies.push({ url: u, ...body });
  const ok = (data: unknown) => ({ ok: true, status: 200, statusText: "OK", json: async () => data });

  if (u.includes("/api/dome/state")) return ok(domeState);
  if (u.includes("/api/alerts/health")) return ok(alertHealth);
  if (u.includes("/api/dome/close")) return ok({ started: "goto" });
  if (u.includes("/api/safety/simulate")) return ok({ is_safe: false });
  if (u.includes("/api/config")) {
    if (method === "POST") serverConfig = { ...serverConfig, ...body };
    return ok(serverConfig);
  }
  return ok({});
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../store");
const { SafetySheet, escalationRows, SUN_DOUBLE_GATE_NOTE, EMPTY_SINKS_WARNING } =
  await import("../sheets/safety");
const { NO_ROOF_REASON, NO_SINK_REASON } = await import("../lib/safetyChain");
const { FLIP_SITE_REASON } = await import("../../monitor/live/FlipTile");
const { sheetsSafety } = await import("../sheets/reg-safety");

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

const VIEWER = { role: "viewer", email: null, caps: ["view.status", "view.preview"] };
// The persona the double gate exists for: config.safety but NOT
// config.solar_override. In the shipped role map only admin holds the override,
// so this is what an "operator promoted to safety edits" would look like.
const SAFETY_NO_SUN = {
  role: "admin", email: "op@rig",
  caps: ["view.status", "view.preview", "control.mount", "config.safety", "config.alerts"],
};
const ADMIN = {
  role: "admin", email: "admin@rig",
  caps: ["view.status", "view.preview", "view.media", "view.site_precise", "view.site_derived",
    "view.weather", "control.capture", "control.mount", "control.guide", "control.power",
    "config.safety", "config.solar_override", "config.backend", "config.site_optics",
    "config.alerts", "admin.users", "system.update"],
};

const container = win.document.getElementById("root") as any;
let rootRef: ReturnType<typeof createRoot> | null = null;

function seed(over: Record<string, unknown> = {}): void {
  serverConfig = freshConfig((over.config as Record<string, unknown>) ?? {});
  act(() => {
    useStore.setState({
      wsPhase: "up",
      equipConnected: true,
      principal: ADMIN,
      authGate: "open",
      toasts: [],
      confirm: null,
      config: JSON.parse(JSON.stringify(serverConfig)),
      status: {
        connected: {}, looping: false, mode: "sim", busy_lanes: [],
        meridian: { status: "counting", hours_to_flip: 1.63, flip_enabled: true, pier_side: "east" },
      },
      safety: {
        connected: true,
        reading: {
          is_safe: true, reason: "", source: "Boltwood II", stale: false,
          ts: Math.round(Date.now() / 1000), detail: { wind_kmh: 12, humidity: 64 },
        },
        streak: 0,
      },
      ...over,
    } as never);
  });
}

async function mount(): Promise<void> {
  if (rootRef) act(() => { rootRef!.unmount(); });
  rootRef = createRoot(container);
  act(() => { rootRef!.render(createElement(SafetySheet as any, { params: {}, depth: 0 })); });
  await settle();
}

function click(node: any): void {
  assert(node != null, "cannot press a control that is not on screen");
  act(() => { node.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
}
const q = (sel: string) => container.querySelector(sel) as any;
const qa = (sel: string) => [...container.querySelectorAll(sel)] as any[];
const text = () => (container.textContent || "") as string;
/** The inputs card's own text. Scoped on purpose: the words RAIN, WIND and
 *  CLOUD appear LEGITIMATELY elsewhere on this sheet ("HOLD ON A CLOUDY SKY",
 *  "rain, wind and power are trips"), so a whole-page search for them would be
 *  a test that can only fail. What must never happen is a BAR with no reading. */
const inputsText = () => (q('[data-testid="safety-inputs"]')?.textContent || "") as string;
// `asked` is cumulative across the whole file, so every "it sent nothing"
// assertion takes a MARK first. Without it the first real write in the file
// makes every later no-write assertion fail for the wrong reason.
const mark = () => asked.length;
const writesSince = (m: number) => asked.slice(m).filter((a) => !a.startsWith("GET "));
const lastPost = () => bodies[bodies.length - 1];

// ====================================================== 1. it actually rendered

seed();
await mount();

test("the sheet mounted, with the three cards the plan names", () => {
  // Vacuity guard. Three DIFFERENT cards, so a shell that rendered only the
  // header cannot satisfy the assertions below by accident.
  assert(q('[data-testid="rig-safety"]') != null, "no sheet marker - the sheet did not render at all");
  assert(q('[data-testid="safety-inputs"]') != null, "no inputs card");
  assert(q('[data-testid="safety-chain"]') != null, "no WHEN A LIMIT TRIPS card");
  assert(q('[data-testid="safety-sun"]') != null, "no sun avoidance card");
  assert(q('[data-testid="safety-limits"]') != null, "no pier and limits card");
  assert(q('[data-testid="safety-escalation"]') != null, "no escalation policy card");
  assert(q('[data-testid="safety-deadman"]') != null, "no dead-man card");
});

test("the live line carries the verdict, the source and the age", () => {
  const live = q('[data-testid="safety-live"]');
  assert(live != null, "the sheet has no live line");
  assert(/^safe · Boltwood II · last read \d+s ago$/.test((live.textContent || "").trim()),
    `the live line is not the safe readout: "${live.textContent}"`);
  eq(live.getAttribute("data-tone"), "good", "a safe rig is not toned good");
});

// ============================================ 2. bars, and the ones NOT drawn

test("one bar per key the monitor sent - and only those", () => {
  eq(qa('[data-testid^="safety-bar-"]').length, 2,
    "the bar count does not match the two keys in reading.detail");
  assert(q('[data-testid="safety-bar-wind_kmh"]') != null, "the wind reading has no bar");
  assert(q('[data-testid="safety-bar-humidity"]') != null, "the humidity reading has no bar");
  assert(!/RAIN/.test(inputsText()), "a RAIN bar appeared for a key the monitor never sent");
  assert(!/CLOUD/.test(inputsText()), "a CLOUD bar appeared for a key the monitor never sent");
});

test("no red tick is drawn, because no config supplies one of these limits", () => {
  eq(qa('[data-testid^="safety-tick-"]').length, 0,
    "a limit tick was drawn from a config block that carries no per-input threshold");
  assert(/the monitor decides this limit/.test(text()),
    "a tickless bar does not say who owns the limit, so it reads as having none");
});

await testAsync("a reading with NO detail renders ONE status row and no bars at all", async () => {
  seed({
    safety: {
      connected: true,
      reading: {
        is_safe: false, reason: "rain detected", source: "RainSensor",
        stale: false, ts: Math.round(Date.now() / 1000),
      },
      streak: 2,
    },
  });
  await mount();
  // THE SABOTAGE TARGET. Five bars over a device that sent no per-input numbers
  // is the "claim outrunning its evidence" failure, and it is invisible on a
  // screenshot - the picture looks better when it lies.
  eq(qa('[data-testid^="safety-bar-"]').length, 0, "bars were drawn for a reading with no detail");
  assert(q('[data-testid="safety-status-row"]') != null, "no single status row replaced the bars");
  for (const fabricated of ["RAIN", "WIND", "CLOUD", "POWER", "HUMIDITY"]) {
    assert(!new RegExp(fabricated).test(inputsText()),
      `${fabricated} is on the inputs card with no reading behind it`);
  }
  assert(/UNSAFE/.test(text()) && /rain detected/.test(text()) && /RainSensor/.test(text()),
    "the status row does not carry the verdict, the reason and the source");
  assert(/2 of 3 unsafe reads/.test(text()),
    "the hysteresis streak is not shown, so a wet sensor that has not acted yet looks broken");
});

await testAsync("a stale reading says the value is the LAST one, not the current one", async () => {
  seed({
    safety: {
      connected: false,
      reading: {
        is_safe: true, reason: "", source: "Boltwood II", stale: true,
        ts: Math.round(Date.now() / 1000), detail: { wind_kmh: 12 },
      },
      streak: 0,
    },
  });
  await mount();
  assert(q('[data-testid="safety-stale"]') != null, "a stale read is not flagged on the inputs card");
  eq(q('[data-testid="safety-live"]').getAttribute("data-tone"), "warn",
    "a stale monitor still reads as good news");
});

// ==================================================== 3. the chain is BUILT

await testAsync("on_unsafe park and abort_park_warm draw DIFFERENT chains", async () => {
  seed({ config: { safety: { ...SEED_SAFETY, on_unsafe: "park" } } });
  await mount();
  const parkNodes = qa('[data-testid^="safety-chain-"]')
    .filter((n) => n.getAttribute("data-lit") != null)
    .map((n) => (n.textContent || "").trim());
  eq(parkNodes.join(" -> "), "STOP CAPTURE -> PARK -> CLOSE -> NOTIFY",
    "the park chain is not what the engine does");

  seed({ config: { safety: { ...SEED_SAFETY, on_unsafe: "abort_park_warm" } } });
  await mount();
  const warmNodes = qa('[data-testid^="safety-chain-"]')
    .filter((n) => n.getAttribute("data-lit") != null)
    .map((n) => (n.textContent || "").trim());
  eq(warmNodes.join(" -> "), "STOP CAPTURE -> PARK -> CLOSE -> WARM COOLER -> NOTIFY",
    "park-and-warm does not add the cooler ramp");
  assert(parkNodes.length !== warmNodes.length,
    "the two settings drew the same chain, so the chain is a constant, not a reading");
});

test("a node the rig will not run is dim AND says why", () => {
  const close = q('[data-testid="safety-chain-close"]');
  eq(close.getAttribute("data-lit"), "false", "CLOSE is lit on a rig with no roof connected");
  const notify = q('[data-testid="safety-chain-notify"]');
  eq(notify.getAttribute("data-lit"), "false", "NOTIFY is lit with no alert channel configured");
  const reasons = q('[data-testid="safety-chain-reasons"]');
  assert(reasons != null, "the dim nodes explain nothing");
  assert((reasons.textContent || "").includes(NO_ROOF_REASON), "CLOSE is dim without saying why");
  assert((reasons.textContent || "").includes(NO_SINK_REASON), "NOTIFY is dim without saying why");
  // A reason with no route to the fix is half an answer: each dim node's
  // setting lives on a different screen from this one.
  assert(q('[data-testid="safety-chain-alerts-link"]') != null,
    "NOTIFY is dim with no way through to the screen that owns alert channels");
});

await testAsync("a dim WARM COOLER points at the camera sheet that owns the ramp", async () => {
  seed({
    config: {
      safety: { ...SEED_SAFETY, on_unsafe: "abort_park_warm" },
      cooling: { warm_ramp: false, warm_rate_c_per_min: 2, warm_ambient_c: null },
    },
  });
  await mount();
  eq(q('[data-testid="safety-chain-warm"]').getAttribute("data-lit"), "false",
    "WARM COOLER is lit while the ramp is switched off - the TEC is cut dead");
  assert(q('[data-testid="safety-chain-warm-link"]') != null,
    "the ramp is off and there is no way from here to the control that turns it on");
});

test("clouds are named as a hold, not a trip", () => {
  assert(/Clouds are a hold, not a trip/.test(text()),
    "the one sentence that stops a user reading the cloud path as a park is missing");
});

// ============================================ 4. writes echo the WHOLE block

await testAsync("the unsafe action posts the WHOLE safety block with the new value", async () => {
  seed();
  await mount();
  bodies.length = 0;
  const picker = q('[data-testid="safety-on-unsafe"]');
  assert(picker != null, "no when-conditions-turn-unsafe picker");
  click(picker.querySelector('[data-value="park"]'));
  await settle();

  const post = lastPost();
  assert(post != null && String(post.url).includes("/api/config"),
    `the picker did not post to /api/config - got ${JSON.stringify(asked.slice(-3))}`);
  const sent = post.safety as Record<string, unknown>;
  eq(sent.on_unsafe, "park", "the new action did not reach the server");
  // WHOLESALE REPLACE: the server's set_safety replaces the block, so a partial
  // echo silently zeroes everything it left out.
  eq(Object.keys(sent).length, Object.keys(SEED_SAFETY).length,
    `the echo is not the whole block - sent ${JSON.stringify(Object.keys(sent))}`);
  eq(sent.solar_exclusion_deg, 30, "the sun cone was dropped from the echo");
  eq(sent.sky_fallback_hold, true,
    "sky_fallback_hold - a field ui/src/types.ts does not declare - was lost in the echo");
  // on_unsafe is one of the five fields SAFETY_PRESETS owns, so the label must
  // stop claiming "backyard" in the same write.
  eq(sent.preset, "custom", "editing a preset-owned field left the preset label lying");
});

await testAsync("a pier-floor edit posts EXACTLY the seeded block with min_alt_deg changed", async () => {
  seed();
  await mount();
  bodies.length = 0;
  const up = q('[data-testid="safety-min-alt"]')?.querySelector('[aria-label="Pier floor degrees up"]');
  assert(up != null, "the pier floor stepper is not on screen");
  click(up);
  await settle();
  // A stepper edits the DRAFT: a POST per press would walk a mount limit
  // through values nobody asked for.
  eq(bodies.length, 0, "a single stepper press posted a mount limit to the rig");
  click(q('[data-testid="safety-limits-save"]'));
  await settle();

  const post = lastPost();
  assert(post != null, "SAVE sent nothing");
  eq(JSON.stringify(post.safety), JSON.stringify({ ...SEED_SAFETY, min_alt_deg: 11 }),
    "the saved block is not the seed with one field changed");
  // min_alt_deg is NOT preset-owned, so the label must survive the edit.
  eq((post.safety as Record<string, unknown>).preset, "backyard",
    "editing a field no preset owns flipped the preset label anyway");
});

// ============================================= 5. the field-level double gate

await testAsync("config.safety alone does NOT unlock the sun cone, and says so", async () => {
  seed({ principal: SAFETY_NO_SUN });
  await mount();
  bodies.length = 0;
  const m = mark();
  const sun = q('[data-testid="safety-sun-switch"]');
  const cone = q('[data-testid="safety-cone"]');
  const picker = q('[data-testid="safety-on-unsafe"]');
  assert(sun != null && cone != null && picker != null, "the sun card or the picker did not render");

  eq(sun.getAttribute("aria-disabled"), "true",
    "config.safety alone unlocked the sun cone - the server would 403 on tap");
  eq(sun.getAttribute("title"), "needs admin access",
    "the sun switch does not name who may change it");
  const note = q('[data-testid="safety-sun-lock"]');
  assert(note != null, "the sun card is locked with no explanation of the second permission");
  eq((note.textContent || "").trim(), SUN_DOUBLE_GATE_NOTE,
    "the lock note does not name config.solar_override, so the lock looks like a bug");

  // ...WHILE the rest of the sheet stays editable. Locking the whole sheet off
  // one field would be the easy wrong answer.
  eq(picker.getAttribute("aria-disabled"), null,
    "a config.safety holder lost the unsafe-action picker to the solar gate");

  click(sun);
  await settle();
  eq(writesSince(m).length, 0, "a locked sun switch still sent a write");
  eq(useStore.getState().confirm, null, "a locked sun switch still raised the disarm confirm");
});

await testAsync("disarming the cone asks first, with a HOLD, and posts nothing until it is held", async () => {
  seed({ principal: ADMIN });
  await mount();
  bodies.length = 0;
  const m = mark();
  const sun = q('[data-testid="safety-sun-switch"]');
  eq(sun.getAttribute("aria-checked"), "true", "precondition: the cone is not armed in the fixture");
  click(sun);
  await settle();

  const req = useStore.getState().confirm;
  assert(req != null, "disarming the sun cone did not ask - it just did it");
  eq(req?.mode, "hold", "the disarm confirm is not a hold, so a stray tap can disarm the cone");
  eq(req?.tone, "danger", "the disarm confirm is not toned as destructive");
  assert(/destroy the camera and instantly blind/.test(String(req?.body ?? "")),
    "the confirm does not name what pointing unfiltered optics at the Sun costs");
  eq(writesSince(m).length, 0, "the cone was posted BEFORE the confirm was answered");

  act(() => { useStore.getState().resolveConfirm(true); });
  await settle();
  const post = lastPost();
  assert(post != null, "holding the confirm did not post the disarm");
  eq((post.safety as Record<string, unknown>).solar_avoidance, false,
    "the confirmed disarm did not reach the server");
  assert(q('[data-testid="safety-sun-off"]') != null,
    "the mount may now slew at the Sun and nothing on screen says so");
  eq((q('[data-testid="safety-sun-badge"]').textContent || "").trim(), "DISARMED",
    "the badge still reads armed after a disarm");
});

await testAsync("backing out of the confirm leaves the cone armed and posts nothing", async () => {
  seed({ principal: ADMIN });
  await mount();
  bodies.length = 0;
  const m = mark();
  click(q('[data-testid="safety-sun-switch"]'));
  await settle();
  act(() => { useStore.getState().resolveConfirm(false); });
  await settle();
  eq(writesSince(m).length, 0, "cancelling the confirm still disarmed the cone");
  eq(q('[data-testid="safety-sun-switch"]').getAttribute("aria-checked"), "true",
    "the switch moved even though the write never happened");
});

await testAsync("a 0 degree cone with the switch ON is reported INERT", async () => {
  seed({ config: { safety: { ...SEED_SAFETY, solar_exclusion_deg: 0 } } });
  await mount();
  eq((q('[data-testid="safety-sun-badge"]').textContent || "").trim(), "DISARMED",
    "a 0 degree cone with the switch on still reads ARMED");
  assert(q('[data-testid="safety-cone-inert"]') != null,
    "a 0 degree cone blocks nothing and the sheet does not say so");
});

// ========================================================= 6. the other rows

await testAsync("the escalation policy is a read-only list on a phone, in human wording", async () => {
  wide = false;
  seed();
  await mount();
  const rows = qa('[data-testid="safety-escalation-row"]').map((r) => (r.textContent || "").trim());
  eq(rows.length, escalationRows(SEED_ESCALATION as never).length,
    "the escalation list does not have one row per policy field");
  assert(rows.some((r) => /Camera must be at temperature before a run - End the run/.test(r)),
    `the cooling rule is not in human wording: ${JSON.stringify(rows)}`);
  assert(rows.some((r) => /Autofocus failed - Skip this target/.test(r)),
    "the autofocus rule does not use the server's own action vocabulary");
  assert(rows.some((r) => /1\.5x the reference - shoot a replacement/.test(r)),
    "the quality gate does not name the factor and what happens to the frame");
  assert(rows.some((r) => /no frame lands for 15 min/.test(r)),
    "the no-progress watchdog is not shown in minutes");
  // require_safety_monitor is the one escalation field that decides what an
  // ABSENT monitor means, and this rig's seed leaves it off. OFF is not silence:
  // the engine still runs and logs the gap once per run (sequence/engine.py
  // `_no_safety_source`), so the row has to say WHICH of the two this rig does -
  // omitting it read as "a monitor is required", which is the opposite.
  assert(rows.some((r) => /A run may start with no safety monitor/.test(r)),
    `the absent-monitor policy has no row: ${JSON.stringify(rows)}`);
  assert(escalationRows({ ...SEED_ESCALATION, require_safety_monitor: true } as never)
    .some((r) => r === "A safety monitor is required before a run"),
    "turning require_safety_monitor ON does not change what the row says");
  assert(q('[data-testid="safety-escalation-edit"]') != null,
    "a phone has no way through to the tablet editor");
});

await testAsync("at tablet width the real EscalationPanel replaces the read-only list", async () => {
  wide = true;
  seed();
  await mount();
  eq(qa('[data-testid="safety-escalation-row"]').length, 0,
    "the phone list is still on screen beside the real editor");
  assert(/When something fails/.test(text()),
    "the real EscalationPanel did not mount at tablet width - the policy is unreachable");
  wide = false;
});

// THE PANEL IS WIDER THAN THE SHEET IT IS IN.
//
// `EscalationPanel` is shared with the settings page, where it has the whole
// window; its rows are Tailwind grids (`sm:grid-cols-[1fr_13rem]`,
// `sm:grid-cols-2`) and `sm:` asks the VIEWPORT, not this container. Mounted in
// `.nx-sheet-panel` - `min(420px, 44vw)`, so 360.8 px at an 820 px viewport -
// its contents overflow their tracks, and with every ancestor at
// `overflow: visible` that overflow reached the PAGE: the browser probe
// measured 100 px of horizontal page scroll at 820 and none at 390, which is
// the tell, because 390 is the narrower sheet and simply does not render this
// branch.
//
// jsdom has no layout engine, so the pixels are the probe's job. This grades
// the structure that decides them: the panel sits in its own scroll container,
// so whatever it does inside stays inside.
await testAsync("the tablet escalation panel is boxed in its own horizontal scroller", async () => {
  wide = true;
  seed();
  await mount();
  const box = q('[data-testid="safety-escalation-scroll"]');
  assert(box != null,
    "the settings-page panel is mounted straight into the sheet, so anything it "
    + "lays out wider than 360 px scrolls the whole PAGE sideways");
  eq(box.style.overflowX, "auto", "the wrapper does not scroll its own overflow");
  // One axis at `auto` computes the other from `visible` to `auto`, which would
  // hang a second scrollbar down a panel that fits vertically perfectly well.
  eq(box.style.overflowY, "hidden", "the wrapper grew a vertical scrollbar it does not need");
  eq(box.style.minWidth, "0px", "the wrapper cannot shrink, so it is not a boundary at all");
  eq(box.style.maxWidth, "100%", "the wrapper may grow past the card that holds it");
  assert(/When something fails/.test(box.textContent || ""),
    "the wrapper is empty - it boxes in nothing");
  wide = false;
});

await testAsync("the dead-man row reports the live health, and empty sinks are called out", async () => {
  seed();
  await mount();
  assert(asked.some((a) => a === "GET /api/alerts/health"),
    "the sheet never asked for the dead-man health");
  const row = q('[data-testid="safety-deadman-row"]');
  assert(/last ping 42s ago/.test(row.textContent || ""),
    `the dead-man row does not carry the live ping age: "${row.textContent}"`);
  const warn = q('[data-testid="safety-no-sinks"]');
  assert(warn != null, "a rig with no alert channel is not warned about it");
  eq((warn.textContent || "").trim(), EMPTY_SINKS_WARNING,
    "the empty-sinks warning is not the sentence that makes NOTIFY meaningful");
});

await testAsync("the roof rows appear only when a dome is actually connected", async () => {
  seed();
  await mount();
  eq(q('[data-testid="safety-roof"]'), null,
    "roof controls rendered on a rig with no roof - three switches that do nothing");
  domeState = { ...domeState, connected: true, shutter: "open" };
  seed();
  await mount();
  assert(q('[data-testid="safety-roof"]') != null, "a connected roof has no controls");
  assert(/Roof open/.test(q('[data-testid="safety-roof-status"]').textContent || ""),
    "the shutter badge does not report the shutter");
  // The manual close is control.mount, NOT one of the config caps: folding them
  // together used to hide the app's only manual roof close from the operator.
  const closeNow = q('[data-testid="safety-close-now"]');
  eq(closeNow.getAttribute("aria-disabled"), null,
    "an admin cannot close the roof - the close is gated on the wrong capability");
  const reopen = q('[data-testid="safety-reopen"]');
  eq(reopen.getAttribute("title"),
    'Turn on "Close roof on unsafe" first - reopening requires closing',
    "reopen is offered without the close it depends on");
  domeState = { ...domeState, connected: false, shutter: "unknown" };
});

await testAsync("the simulator pair is sim-only and posts the trip", async () => {
  seed();
  await mount();
  bodies.length = 0;
  click(q('[data-testid="safety-sim-unsafe"]'));
  await settle();
  const post = lastPost();
  assert(post != null && String(post.url).includes("/api/safety/simulate"),
    "SIMULATE UNSAFE did not reach the simulate route");
  eq(post.unsafe, true, "the simulate body did not ask for an unsafe verdict");

  seed({ status: { connected: {}, looping: false, mode: "alpaca", busy_lanes: [] } });
  await mount();
  eq(q('[data-testid="safety-simulate"]'), null,
    "the simulator pair rendered on a real rig, where the route 404s");
});

await testAsync("the meridian row reports the pier side and the countdown", async () => {
  seed();
  await mount();
  const line = (q('[data-testid="safety-flip-state"]').textContent || "").trim();
  eq(line, "pier east · flip in 1h 38m", "the flip line is not built from status.meridian");
  assert(/only sets how much warning you get/.test(text()),
    "the flip lead time does not say that the flip itself is scheduled by the plan");
});

// The countdown is site data: `hours_to_flip` is `lst - ra_hours`, which
// inverts to the rig's longitude. The server nulls it and collapses
// `meridian.status` to "unknown" for a principal without `view.site_derived`,
// and this line used to print "no mount data - the flip clock is not running"
// over that - a claim about the HARDWARE, made about a working mount.
await testAsync("a viewer's meridian row blames the redaction, not the mount", async () => {
  seed({
    principal: VIEWER,
    status: {
      connected: {}, looping: false, mode: "sim", busy_lanes: [],
      // exactly what `_redact_ws_event` leaves a non-holder: the two
      // non-derived fields, no countdown, no status.
      meridian: { status: "unknown", hours_to_flip: null, flip_enabled: true, pier_side: "east" },
    },
  });
  await mount();
  const line = (q('[data-testid="safety-flip-state"]').textContent || "").trim();
  eq(line, FLIP_SITE_REASON, "the viewer's flip line does not name the missing capability:");
});

await testAsync("a site-derived holder's meridian row still counts down", async () => {
  seed();
  await mount();
  const line = (q('[data-testid="safety-flip-state"]').textContent || "").trim();
  eq(line, "pier east · flip in 1h 38m", "the countdown did not come back for a holder:");
});

// ================================================================ 7. a viewer

await testAsync("a viewer sees every control, dimmed, with the reason - and sends nothing", async () => {
  seed({ principal: VIEWER });
  await mount();
  bodies.length = 0;
  const m = mark();
  const controls = [
    "safety-on-unsafe", "safety-sun-switch", "safety-enabled", "safety-sky-hold",
    "safety-floor-switch", "safety-twilight", "safety-sim-unsafe", "safety-limits-save",
  ];
  for (const id of controls) {
    const el = q(`[data-testid="${id}"]`);
    assert(el != null, `${id} is HIDDEN from a viewer - a viewer must see the same screen`);
    eq(el.getAttribute("aria-disabled"), "true", `${id} is live for a viewer`);
    eq(el.getAttribute("title"), "needs admin access", `${id} is dimmed without naming the reason`);
    click(el);
  }
  await settle();
  eq(writesSince(m).length, 0, `a viewer's presses reached the rig: ${JSON.stringify(writesSince(m))}`);
  // Honest-disabled, not the native attribute: the reason has to be reachable.
  for (const id of controls) {
    eq(q(`[data-testid="${id}"]`).hasAttribute("disabled"), false,
      `${id} uses the native disabled attribute, which takes the reason out of the tree`);
  }
  assert(useStore.getState().toasts.length > 0,
    "a locked press explained nothing - the control just swallowed the tap");
});

// ============================================================ 8. the registry

test("the sheet is registered under the name the router will ask for", () => {
  eq(Object.keys(sheetsSafety).join(","), "safety", "the registry fragment does not export `safety`");
  eq(sheetsSafety.safety, SafetySheet as never, "the registry points at something other than this sheet");
});

act(() => { rootRef?.unmount(); });

const total = passed + failed;
console.log(`rigSafetyDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
