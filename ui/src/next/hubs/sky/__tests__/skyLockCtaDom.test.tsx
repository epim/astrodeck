// skyLockCtaDom.test.tsx - the lock card's two claims: what IMAGE will queue,
// and what + PLAN will do.
//
//   Run directly:  npx tsx src/next/hubs/sky/__tests__/skyLockCtaDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc --noEmit`.
//
// `skyHubDom.test.tsx` mounts this hub over a SEVEN-SLOT wheel and a rig with
// no stored quick defaults, which is the one shape in which every wrong answer
// below happens to look right. This file mounts the two that do not:
//
//   1. A ONE-SHOT-COLOUR RIG WITH NO WHEEL. The CTA counted the seven names
//      `resolveWheel` hands back as a FALLBACK and printed "7 filters", while
//      the quick sheet the same button opens built one channel and posted
//      `filters: []`. The button and the sheet behind it described two
//      different nights.
//   2. A RIG WHOSE STORED WINDOW IS "UNTIL DAWN". `QuickPrefs.hours` is
//      meaningless while `dawn` is set - the window is tonight's dawn, a
//      different length every night - and the CTA replayed the stored number
//      raw, as a float ("5.216388888h"), with a clock formatted so a session
//      ending after midnight read "(+1d)".
//   3. A POOL OF TWO. "PLAN 2 TARGETS" was printed from the pool SIZE alone, so
//      a target that was not in the pool carried a label for an action the
//      press does not perform (it adds; the label says it opens the sheet) -
//      and once a target WAS in a pool of two the button stopped being a toggle
//      and left no way back out of the pool at all.
//
// Part A drives `LockCard` directly, because the label matrix and the satellite
// refusals are decisions of that component and a hub cannot produce a satellite
// lock at all today (`finder/targets.ts SATELLITE_MARKERS` is false). Part B
// mounts the whole hub, because the plan summary is a hub-level composition of
// the wheel, the planning store and the visibility anchor.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------------ css stub
// `SkyHub` reaches `hubs/sky/atlas/AtlasHost.tsx`, which imports `atlas.css`
// (the r7Css rule: every area owns a stylesheet and a module of that area
// imports it). Node has no idea what a `.css` file is, so a synchronous load
// hook answers with an empty module - the same stub `shellDom.test.tsx` and
// every other area's DOM test already use.
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
  `<!doctype html><html><body><div id="root"></div><div id="card"></div></body></html>`,
  { url: "http://local/#/sky", pretendToBeVisual: true },
);
const win = dom.window as any;

win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
Object.defineProperty(win, "isSecureContext", { value: false, configurable: true });
win.WebSocket = class { close() {} addEventListener() {} send() {} };
win.Element.prototype.setPointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.releasePointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.scrollBy = function () { /* jsdom has none */ };

// ------------------------------------------------------------- fetch double
//
// THE CLOCK IS CHOSEN TO CROSS LOCAL MIDNIGHT. `new Date(y, m, d, 23)` is 23:00
// in the RUNNER's own zone, so a five-hour window ends at 04:00 the next local
// day whatever zone that is. That is what makes the "(+1d)" assertion below a
// real guard rather than one that happens to pass: `fmtClock(at)` with no
// second argument prints the day delta, and `finishLabel` exists to suppress it.
const NOW = new Date(2026, 8, 10, 23, 0, 0).getTime();

const asked: { method: string; url: string; body: unknown }[] = [];

function ok(data: unknown) {
  return { ok: true, status: 200, statusText: "OK", json: async () => data };
}

const regionRows = [
  {
    id: "m31", label: "M31", kind: "dso", type: "Galaxy",
    ra_hours: 0.7123, dec_deg: 41.269, mag: 3.4, size_arcmin: 190,
    constellation: "And", describe: "Andromeda Galaxy", alias: "NGC 224",
    alt: 58, az: 64,
  },
  {
    id: "ngc7000", label: "NGC 7000", kind: "dso", type: "Emission Nebula",
    ra_hours: 20.98, dec_deg: 44.5, mag: 4, size_arcmin: 120,
    constellation: "Cyg", describe: "North America Nebula", alias: null,
    alt: 52, az: 70,
  },
];

const tonightPicks = regionRows.map((r, i) => ({
  id: r.id, name: r.label, type: r.type,
  ra_hours: r.ra_hours, dec_deg: r.dec_deg, mag: r.mag, size_arcmin: r.size_arcmin,
  difficulty: "easy", surface_brightness: 21, difficulty_source: "heuristic",
  max_alt: 60 - i, transit_unix: NOW / 1000 + 7200,
  best_window: null, moon_sep_deg: 80, never_rises_above_limit: false, score: 1 - i * 0.1,
}));

/** Dark ends five hours from now, so "until dawn" is a five-hour window and the
 *  finish time lands after local midnight. */
const DAWN_HOURS = 5;
const visibilityNight = {
  date: "2026-09-10",
  transit_unix: NOW / 1000 + 7200, transit_alt: 62, transit_in_daylight: false,
  dark_start_unix: NOW / 1000 - 3600, dark_end_unix: NOW / 1000 + DAWN_HOURS * 3600,
  darkness_kind: "astronomical", samples: [],
  moon: {
    illumination: 0.2, phase_name: "Waning Crescent", alt: -20, az: 90,
    separation_deg: 80, rise_unix: null, set_unix: null,
  },
  best_window: null, alt_limit_deg: 20, never_rises_above_limit: false,
};

const domePayload = {
  enabled: true, observed_at: "2026-09-10T05:00:00Z", stale: false,
  alt_start: 6, alt_step: 6, az_step: 10,
  rows: Array.from({ length: 14 }, () => Array.from({ length: 36 }, () => 0.05)),
};

/** What `GET /api/planning` answers. Swapped between mounts. */
let planningBody: unknown = null;

const g = globalThis as any;
g.fetch = async (url: any, init?: any) => {
  const u = String(url);
  let body: unknown = null;
  if (typeof init?.body === "string") { try { body = JSON.parse(init.body); } catch { body = init.body; } }
  asked.push({ method: init?.method ?? "GET", url: u, body });
  if (u.includes("/api/planning")) return ok(planningBody ?? {});
  if (u.includes("/api/cloudmap/dome")) return ok(domePayload);
  if (u.includes("/api/cloudmap")) {
    return ok({ enabled: true, platform: "G18", observed_at: null, age_s: null, stale: false, last_error: null, motion: null, credit: { source: "", url: "" } });
  }
  if (u.includes("/api/catalog/tonight")) return ok({ date: "2026-09-10", site_is_default: false, picks: tonightPicks });
  if (u.includes("/api/catalog/region")) return ok({ rows: regionRows, truncated: false, catalog_degraded: false, notes: [] });
  if (u.includes("/api/catalog?q=")) return ok({ results: [], notes: [] });
  if (u.includes("/api/visibility")) return ok(visibilityNight);
  if (u.includes("/api/site")) {
    return ok({
      site: { name: "Back lawn", latitude: 47.61, longitude: -122.33, elevation_m: 52, is_default: false, horizon_min_deg: 20, horizon_points: [[0, 5], [180, 5], [359, 5]] },
      version: 3,
    });
  }
  return ok({});
};

for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "HTMLVideoElement", "Element", "SVGElement", "Node", "Event", "CustomEvent",
  "MouseEvent", "KeyboardEvent", "PointerEvent", "localStorage", "getComputedStyle",
  "matchMedia", "requestAnimationFrame", "cancelAnimationFrame", "WebSocket",
  "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const realNow = Date.now;
Date.now = () => NOW;

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../store");
const { resetPlanningForTests } = await import("../../../lib/planning");
const { SkyHub } = await import("../SkyHub");
const { SAT_NO_PLAN, SAT_NO_SINGLE } = await import("../SkyHub");
const { LockCard } = await import("../cards/LockCard");
const { oscCount } = await import("../sheets/quickModel");
import type { SkyTarget } from "../finder";

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];

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
  await act(async () => { await new Promise((r) => setTimeout(r, 400)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
};

const hubBox = win.document.getElementById("root") as any;
const cardBox = win.document.getElementById("card") as any;
const click = (el: any) => {
  assert(el != null, "click: the element is not there - the fixture is wrong, not the component");
  act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
};

// ===================================================================== part A
//
// `LockCard` alone, driven by props. No store, no fetch: the label matrix and
// the two satellite refusals are decisions of this component.

const TARGET = {
  id: "m31", name: "M31", full: "Andromeda Galaxy", kind: "galaxy",
  ra_hours: 0.7123, dec_deg: 41.269, altNow: 58, azNow: 64,
  cloudPct: 5, obstructed: false, clouded: false,
  color: "#00d2ff", statusTxt: "CLEAR", palette: "LRGB",
  transitLabel: "23:52", windowMinutes: 300, score: 1, moonSepDeg: 80,
} as const;

const SATELLITE = { ...TARGET, id: "iss", name: "ISS", full: "ISS (ZARYA)", kind: "satellite" } as const;

interface CardOpts {
  lock?: typeof TARGET | typeof SATELLITE;
  inPool?: boolean;
  poolCount?: number;
  planReason?: string | null;
  singleReason?: string | null;
}

let lastPlanPress = 0;
let lastRemovePress = 0;
const explained: string[] = [];

async function mountCard(o: CardOpts = {}): Promise<{ unmount(): Promise<void> }> {
  lastPlanPress = 0;
  lastRemovePress = 0;
  explained.length = 0;
  const root = createRoot(cardBox);
  await act(async () => {
    root.render(createElement(LockCard, {
      lock: (o.lock ?? TARGET) as unknown as SkyTarget,
      equipConnected: true,
      planSummary: "until dawn \u00b7 4 filters \u00b7 finishes 04:00",
      framed: null,
      onAdjustFrame: () => {},
      onClearFrame: () => {},
      onPrimary: () => {},
      onInfo: () => {},
      onSingleFrame: () => {},
      onPlan: () => { lastPlanPress++; },
      inPool: o.inPool ?? false,
      poolCount: o.poolCount ?? 0,
      onRemoveFromPool: () => { lastRemovePress++; },
      primaryReason: null,
      singleReason: o.singleReason ?? null,
      planReason: o.planReason ?? null,
      onExplain: (r: string) => { explained.push(r); },
    }));
  });
  await settle();
  return { unmount: async () => { await act(async () => { root.unmount(); }); } };
}

const cardQ = (sel: string): any => cardBox.querySelector(sel);

await testAsync("precondition: the card renders its own three controls", async () => {
  const m = await mountCard();
  try {
    assert(cardQ('[data-testid="sky-lock"]') != null, "no lock card at all - the fixture is wrong");
    assert(cardQ('[data-testid="sky-cta"]') != null, "no primary CTA");
    assert(cardQ('[data-testid="sky-single"]') != null, "no SINGLE FRAME");
    assert(cardQ('[data-testid="sky-plan"]') != null, "no + PLAN");
  } finally { await m.unmount(); }
});

await testAsync("a pool of two this target is NOT in still says + PLAN, and pressing adds", async () => {
  const m = await mountCard({ inPool: false, poolCount: 2 });
  try {
    const plan = cardQ('[data-testid="sky-plan"]');
    assert(/\+ PLAN/.test(plan.textContent),
      `a target outside the pool must not be labelled for the pool: "${plan.textContent}"`);
    assert(!/PLAN 2 TARGETS/.test(plan.textContent),
      "the label names an action this press does not perform - it adds, it does not open the sheet");
    assert(/2 already queued/.test(plan.textContent),
      `the sub line drops the fact that a pool exists: "${plan.textContent}"`);
    eq(plan.getAttribute("data-plan-opens"), "toggle", "this press is the toggle, not the door:");
    assert(cardQ('[data-testid="sky-plan-remove"]') == null,
      "a target that is not in the pool cannot be removed from it");
  } finally { await m.unmount(); }
});

await testAsync("a member of a pool of two opens the sheet AND gets a way back out", async () => {
  const m = await mountCard({ inPool: true, poolCount: 2 });
  try {
    const plan = cardQ('[data-testid="sky-plan"]');
    assert(/PLAN 2 TARGETS/.test(plan.textContent), `the label: "${plan.textContent}"`);
    eq(plan.getAttribute("data-plan-opens"), "sheet", "this press opens the multi-target sheet:");

    const x = cardQ('[data-testid="sky-plan-remove"]');
    assert(x != null,
      "the button beside it stopped being a toggle, so without this there is no way out of the pool");
    assert((x.getAttribute("aria-label") ?? "").includes("M31"),
      `the remove control does not name what it removes: "${x.getAttribute("aria-label")}"`);
    click(x);
    eq(lastRemovePress, 1, "the x did not call the remove handler:");
    eq(lastPlanPress, 0, "the x pressed the plan button instead of the remove one:");
  } finally { await m.unmount(); }
});

await testAsync("the only member of a pool of one is still a toggle, with no x", async () => {
  const m = await mountCard({ inPool: true, poolCount: 1 });
  try {
    const plan = cardQ('[data-testid="sky-plan"]');
    assert(/IN TONIGHT'S PLAN/.test(plan.textContent), `the label: "${plan.textContent}"`);
    assert(/tap to remove/.test(plan.textContent), "the toggle must say it is a toggle");
    assert(cardQ('[data-testid="sky-plan-remove"]') == null,
      "a second remove control beside a button that already removes is two doors for one act");
  } finally { await m.unmount(); }
});

await testAsync("a satellite lock refuses SINGLE FRAME and + PLAN, honestly", async () => {
  const m = await mountCard({
    lock: SATELLITE, singleReason: SAT_NO_SINGLE, planReason: SAT_NO_PLAN,
  });
  try {
    const cta = cardQ('[data-testid="sky-cta"]');
    eq(cta.getAttribute("data-cta"), "passes", "a satellite's CTA is the pass list:");

    for (const [id, reason] of [
      ["sky-single", SAT_NO_SINGLE] as const,
      ["sky-plan", SAT_NO_PLAN] as const,
    ]) {
      const el = cardQ(`[data-testid="${id}"]`);
      eq(el.getAttribute("aria-disabled"), "true", `${id} is not marked disabled:`);
      eq(el.getAttribute("title"), reason, `${id} does not carry its reason:`);
      assert(!el.hasAttribute("disabled"),
        `${id} used the native attribute, which strips the reason from the tree`);
    }

    const before = lastPlanPress;
    click(cardQ('[data-testid="sky-plan"]'));
    eq(lastPlanPress, before, "a locked + PLAN queued the satellite anyway:");
    assert(explained.includes(SAT_NO_PLAN), "a locked press said nothing at all");
  } finally { await m.unmount(); }
});

// ===================================================================== part B
//
// The whole hub, so the plan summary is composed from the real wheel, the real
// planning store and the real visibility anchor.

const CAPS_OPERATOR = [
  "view.status", "view.weather", "view.site_derived", "view.site_precise",
  "control.capture", "control.mount",
];

/** A camera and a mount, and NO `filterwheel` key at all - which is exactly
 *  what `hub.poll_status` publishes with no wheel connected, and the signal
 *  `wheelModel` reads to tell a one-channel rig from a laptop with no rig. */
const OSC_STATUS = {
  connected: {
    camera: { connected: true, name: "sim camera" },
    telescope: { connected: true, name: "sim mount" },
  },
  looping: false,
  camera: { temperature: -10, can_cool: true, is_color: true, bayer_pattern: "RGGB" },
  optics: {
    have_optics: true, source: "config",
    focal_length_mm: 800, pixel_size_um: 3.76,
    sensor_width_px: 6248, sensor_height_px: 4176,
    image_scale_arcsec_px: 0.97, fov_w_deg: 1.68, fov_h_deg: 1.12, fov_diag_deg: 2.02,
  },
};

function seedState(): void {
  useStore.setState({
    principal: { role: "operator", caps: CAPS_OPERATOR, name: "tester" },
    site: { name: "Back lawn", latitude: 47.61, longitude: -122.33, elevation_m: 52, is_default: false, horizon_min_deg: 20 },
    equipConnected: true,
    wsPhase: "up",
    toasts: [],
    framing: null,
    status: OSC_STATUS,
    config: {
      optics: { focal_length_mm: 800, pixel_size_um: 3.76, sensor_width_px: 6248, sensor_height_px: 4176, auto_from_camera: false, telescope_name: "" },
      safety: { horizon: null },
      survey: { online_fetch: false },
    },
    weather: {
      enabled: true, fetched_ts: NOW / 1000, stale: false, ignore_tonight: false,
      threshold_pct: 60, sustain_minutes: 30, site_lat: 47.61, site_lon: -122.33,
      forecast: { times: [new Date(NOW).toISOString()], cloud: [8], cloud_low: [8], cloud_mid: [0], cloud_high: [0] },
      astrospheric: null, alert: null,
      now: { ts: new Date(NOW).toISOString(), temp_c: 14, dewpoint_c: 6, humidity_pct: 60, wind_kmh: 12, wind_dir_deg: 225, gust_kmh: 18, cloud_base_m: 2200 },
    },
  } as never);
}

/** Mount the hub against a given `GET /api/planning` body, with the reticle
 *  aimed at M31 through the reach strip (the design's own "tap to aim", which
 *  puts the target dead centre so the lock is the lock RULE and not an
 *  arithmetic coincidence about where the finder happened to open). */
async function mountHub(planning: unknown): Promise<{ unmount(): Promise<void> }> {
  planningBody = planning;
  resetPlanningForTests();
  seedState();
  win.location.hash = "#/sky";
  const root = createRoot(hubBox);
  await act(async () => { root.render(createElement(SkyHub)); });
  await settle();
  click(hubBox.querySelector('[data-reach-chip="m31"]'));
  await settle();
  return { unmount: async () => { await act(async () => { root.unmount(); }); } };
}

const hubQ = (sel: string): any => hubBox.querySelector(sel);
const ctaSub = (): string =>
  String(hubQ('[data-testid="sky-cta"]')?.querySelectorAll("span")[1]?.textContent ?? "");

await testAsync("a wheel-less rig's CTA prints the one channel, never the assumed seven", async () => {
  const m = await mountHub({
    quick: { hours: 2, dawn: false, on: {}, exp: {}, extras: {}, dither_n: 3, learned: true },
    pool: [],
  });
  try {
    assert(hubQ('[data-testid="sky-lock"]') != null,
      "precondition: nothing is locked, so there is no CTA and nothing below is about one");
    eq(hubQ('[data-testid="sky-lock-name"]')?.textContent, "M31", "precondition: the lock is:");

    const sub = ctaSub();
    assert(sub !== "", "the CTA has no second line at all");
    assert(!/filters/.test(sub),
      `a rig with no wheel has no filter cycle, and the CTA claimed one: "${sub}"`);
    assert(!/\b7\b/.test(sub),
      `the seven names are a FALLBACK for a planner with no rig, not this rig's wheel: "${sub}"`);
    // 2 h of 120 s subs, the same arithmetic the quick sheet's one-channel card
    // shows - read off `oscCount` rather than hardcoded, so the two cannot be
    // made to disagree by a change to the allocation.
    assert(sub.includes(`120s \u00d7 ${oscCount(2, 120)}`),
      `the one-channel summary is missing: "${sub}"`);
  } finally { await m.unmount(); }
});

await testAsync("an 'until dawn' default resolves tonight's dawn, not the stored number", async () => {
  const m = await mountHub({
    // The stored `hours` is 2 and is meaningless: `dawn` says the choice was
    // "all night", and tonight's all-night is five hours.
    quick: { hours: 2, dawn: true, on: {}, exp: {}, extras: {}, dither_n: 3, learned: true },
    pool: [],
  });
  try {
    const sub = ctaSub();
    assert(/until dawn/.test(sub),
      `the stored dawn flag was ignored and a fixed window printed instead: "${sub}"`);
    assert(!/\b2h\b/.test(sub),
      `the stored 2 h was replayed over tonight's five-hour dawn: "${sub}"`);
    assert(!/\d\.\d/.test(sub),
      `a raw float reached the button: "${sub}"`);
    assert(/finishes \d\d:\d\d/.test(sub), `no finish time: "${sub}"`);
    // The window ends at 04:00 local, the NEXT day - see the NOW constant. A
    // clock formatted without its own "now" prints the day delta on every
    // session worth setting up, which is every session.
    assert(!/\(\+\d+d\)/.test(sub),
      `the day-delta suffix reached the button: "${sub}"`);
    assert(sub.includes(`120s \u00d7 ${oscCount(DAWN_HOURS, 120)}`),
      `the sub count is not the one this five-hour window actually buys: "${sub}"`);
  } finally { await m.unmount(); }
});

await testAsync("the pool the RIG holds drives the label, and the x writes it back", async () => {
  const m = await mountHub({
    quick: { hours: 2, dawn: false, on: {}, exp: {}, extras: {}, dither_n: 3, learned: true },
    pool: ["m31", "ngc7000"],
  });
  try {
    const plan = hubQ('[data-testid="sky-plan"]');
    assert(/PLAN 2 TARGETS/.test(plan.textContent),
      `the lock is in a pool of two: "${plan.textContent}"`);

    const before = asked.length;
    click(hubQ('[data-testid="sky-plan-remove"]'));
    await settle();
    const put = asked.slice(before).find((r) => r.method === "PUT" && r.url.includes("/api/planning"));
    assert(put != null, "removing from the pool never reached the rig");
    const body = put?.body as { pool?: string[] } | undefined;
    assert(Array.isArray(body?.pool) && !body.pool.includes("m31"),
      `the written pool still holds the removed target: ${JSON.stringify(body)}`);
    assert(Array.isArray(body?.pool) && body.pool.includes("ngc7000"),
      `removing one target took the other with it: ${JSON.stringify(body)}`);
  } finally { await m.unmount(); }
});

Date.now = realNow;

const total = passed + failed;
console.log(`skyLockCtaDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
