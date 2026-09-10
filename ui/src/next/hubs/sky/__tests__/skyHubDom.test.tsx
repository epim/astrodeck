// skyHubDom.test.tsx - the SKY hub root, MOUNTED, on a plain-HTTP LAN phone
// (no secure context, so no camera and no gyro - the state a rig on the local
// network actually renders in).
//
//   Run directly:  npx tsx src/next/hubs/sky/__tests__/skyHubDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc --noEmit`.
//
// THE PRECONDITION IS DOING REAL WORK HERE. A hub that rendered a status row and
// an empty box would satisfy every "did it crash" assertion ever written, so the
// first test asserts the suggested-targets pill AND that the lock card's primary
// button says something from the five-case set. Without both, every assertion
// below could be true of a screen with nothing on it.
//
// Five contracts worth a mounted test:
//   1. IMAGE M31 goes to `#/sky/quick?target=m31`. The hash IS the state, so a
//      button that changed the screen without changing the hash would break the
//      back button and deep links alike.
//   2. Hiding a kind on the lens dial reaches BOTH the stored preference and the
//      in-reach count. Persisting without filtering, or filtering without
//      persisting, are two different bugs that look identical for one session.
//   3. The layers toggles persist under their own key, so an overlay switched
//      off at the eyepiece is still off after the phone locks.
//   4. A VIEWER sees the same button an operator does, dimmed, carrying the
//      reason - and pressing it fires no request. Hiding it would be the other
//      design, and a native `disabled` would strip the reason from the tree.
//   5. With no rig the browse banner appears and the CTA becomes CONNECT THE RIG
//      FIRST, which is LIVE: pressing it is how you get a rig.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
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
const asked: string[] = [];
const NOW = Date.UTC(2026, 8, 10, 5, 0, 0);

function ok(data: unknown) {
  return { ok: true, status: 200, statusText: "OK", json: async () => data };
}

// M31 and NGC 7000 with SERVER-SUPPLIED alt/az, so their screen positions are
// exact and the hub's auto-aim lands on M31 rather than on arithmetic.
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

// The same two ids in the ranked list, so the visibility anchor exists and the
// window / transit numbers on the lock card are real ones.
const tonightPicks = regionRows.map((r, i) => ({
  id: r.id, name: r.label, type: r.type,
  ra_hours: r.ra_hours, dec_deg: r.dec_deg, mag: r.mag, size_arcmin: r.size_arcmin,
  difficulty: "easy", surface_brightness: 21, difficulty_source: "heuristic",
  max_alt: 60 - i, transit_unix: NOW / 1000 + 7200,
  best_window: null, moon_sep_deg: 80, never_rises_above_limit: false, score: 1 - i * 0.1,
}));

const visibilityNight = {
  date: "2026-09-10",
  transit_unix: NOW / 1000 + 7200, transit_alt: 62, transit_in_daylight: false,
  dark_start_unix: NOW / 1000 - 3600, dark_end_unix: NOW / 1000 + 5 * 3600,
  darkness_kind: "astronomical", samples: [],
  moon: { illumination: 0.2, phase_name: "Waning Crescent", alt: -20, az: 90, separation_deg: 80, rise_unix: null, set_unix: null },
  best_window: null, alt_limit_deg: 20, never_rises_above_limit: false,
};

const domePayload = {
  enabled: true, observed_at: "2026-09-10T05:00:00Z", stale: false,
  alt_start: 6, alt_step: 6, az_step: 10,
  rows: Array.from({ length: 14 }, () => Array.from({ length: 36 }, () => 0.05)),
};

const g = globalThis as any;
g.fetch = async (url: any, init?: any) => {
  const u = String(url);
  asked.push(`${init?.method ?? "GET"} ${u}`);
  if (u.includes("/api/cloudmap/dome")) return ok(domePayload);
  if (u.includes("/api/cloudmap")) {
    return ok({ enabled: true, platform: "G18", observed_at: null, age_s: null, stale: false, last_error: null, motion: null, credit: { source: "", url: "" } });
  }
  if (u.includes("/api/catalog/tonight")) return ok({ date: "2026-09-10", site_is_default: false, picks: tonightPicks });
  if (u.includes("/api/catalog/region")) return ok({ rows: regionRows, truncated: false, catalog_degraded: false, notes: [] });
  if (u.includes("/api/catalog?q=")) return ok({ rows: [] });
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
const { SkyHub } = await import("../SkyHub");

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
  await act(async () => { await new Promise((r) => setTimeout(r, 400)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
};

const container = win.document.getElementById("root") as any;
const q = (sel: string): any => container.querySelector(sel);
// The layers popover portals to `document.body`, so it is deliberately NOT
// inside `container` - looking for it there would be a test that can only fail.
const qdoc = (sel: string): any => win.document.querySelector(sel);
const byId = (id: string): any => q(`[data-testid="${id}"]`);
const text = (): string => container.textContent as string;
const click = (el: any) => {
  assert(el != null, "click: the element is not there - the fixture is wrong, not the component");
  act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
};

const CAPS_OPERATOR = [
  "view.status", "view.weather", "view.site_derived", "view.site_precise",
  "control.capture", "control.mount",
];

function seedState(caps: string[], equipConnected: boolean): void {
  useStore.setState({
    principal: { role: caps.includes("control.capture") ? "operator" : "viewer", caps, name: "tester" },
    site: { name: "Back lawn", latitude: 47.61, longitude: -122.33, elevation_m: 52, is_default: false, horizon_min_deg: 20 },
    equipConnected,
    wsPhase: "up",
    toasts: [],
    framing: null,
    status: {
      connected: equipConnected ? { camera: { connected: true, name: "sim camera" } } : {},
      looping: false,
      filterwheel: {
        position: 0,
        names: ["L", "R", "G", "B", "Ha", "OIII", "SII"],
        narrowband: [false, false, false, false, true, true, true],
        opaque: [false, false, false, false, false, false, false],
      },
      optics: {
        have_optics: true, source: "config",
        focal_length_mm: 800, pixel_size_um: 3.76,
        sensor_width_px: 6248, sensor_height_px: 4176,
        image_scale_arcsec_px: 0.97, fov_w_deg: 1.68, fov_h_deg: 1.12, fov_diag_deg: 2.02,
      },
    },
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

/** The store write IS a render, so it goes inside `act` like every other one. */
function seed(caps: string[], equipConnected = true): void {
  act(() => { seedState(caps, equipConnected); });
}

// ===================================================== the operator's screen
seed(CAPS_OPERATOR);
win.location.hash = "#/sky";
const root = createRoot(container);
await act(async () => { root.render(createElement(SkyHub)); });
await settle();

// AIM THROUGH THE REACH STRIP rather than trusting the finder's own opening
// aim. The strip is the design's "tap to aim", it is one of this task's own
// components, and it puts the target dead centre - so the lock under test is
// the lock rule, not an arithmetic coincidence about where the finder happened
// to open.
click(q('[data-reach-chip="m31"]'));
await settle();

test("precondition: the hub rendered its own screen, not an empty column", () => {
  assert(byId("hub-sky") != null, "no hub-sky marker - the fixture is wrong, not the component");
  const pill = byId("sky-suggested");
  assert(pill != null, "no suggested-targets pill");
  assert(/Show \d+ suggested targets/.test(pill.textContent), `the pill printed no count: "${pill.textContent}"`);
  const cta = byId("sky-cta");
  assert(cta != null, "no primary CTA - nothing locked, so the whole card is missing");
  assert(
    /^IMAGE |^CONNECT THE RIG FIRST$/.test(cta.querySelector("span")?.textContent ?? ""),
    `the CTA label is not one of the five cases: "${cta.textContent}"`,
  );
});

test("precondition: the finder auto-aimed at the best target and locked it", () => {
  eq(byId("sky-lock-name")?.textContent, "M31", "the top-ranked target should be the lock:");
  assert(/az \d{3}° · alt/.test(text()), "the finder never printed a bearing");
});

test("the status row carries numbers, not captions", () => {
  assert(/clear \d+%/.test(text()), `no clear percentage in the status row: "${text().slice(0, 200)}"`);
  assert(/Back lawn/.test(byId("sky-site")?.textContent ?? ""), "the site pill does not name the site");
  assert(byId("sky-dome") != null, "no SKYDOME link");
});

test("the CTA says what it will queue, not just what it is", () => {
  const sub = byId("sky-cta")?.querySelectorAll("span")[1]?.textContent ?? "";
  assert(/filters|one-shot colour/.test(sub), `the plan summary is missing: "${sub}"`);
  assert(/finishes \d\d:\d\d/.test(sub), `the finish time is missing: "${sub}"`);
});

test("IMAGE M31 opens the quick session for M31, by hash", () => {
  eq(win.location.hash, "#/sky", "precondition: still on the hub");
  click(byId("sky-cta"));
  eq(win.location.hash, "#/sky/quick?target=m31", "the CTA hash:");
  act(() => { win.location.hash = "#/sky"; });
});

test("SINGLE FRAME pre-aims the manual capture screen with BOTH coordinates", () => {
  click(byId("sky-single"));
  const h = decodeURIComponent(win.location.hash);
  assert(h.startsWith("#/rig/capture"), `single frame went to ${h}`);
  assert(/target=M31/.test(h), `no target in ${h}`);
  assert(/ra=0\.71/.test(h) && /dec=41\.2/.test(h), `a flow with no coordinates slews to the wrong object: ${h}`);
  act(() => { win.location.hash = "#/sky"; });
});

await testAsync("the lens dial hides a kind: the store, the count and the finder all move", async () => {
  const before = Number((byId("sky-suggested")?.textContent ?? "").replace(/\D+/g, ""));
  assert(before >= 2, `precondition: both targets should be in reach, got ${before}`);

  click(q("[data-sky-lens]"));
  await settle();
  assert(byId("sky-lens") != null, "the funnel did not open the lens dial");

  const nebula = q('[data-lens-kind="nebula"]');
  assert(nebula != null, "no NEBULAE seat on the dial");
  eq(nebula.getAttribute("data-lens-on"), "true", "precondition: nebulae start visible");
  click(nebula);
  await settle();

  const stored = JSON.parse(win.localStorage.getItem("astrodeck-next-sky-lens") ?? "{}");
  eq(stored.nebula, false, "the hidden kind did not reach localStorage:");

  const after = Number((byId("sky-suggested")?.textContent ?? "").replace(/\D+/g, ""));
  assert(after < before, `hiding a kind did not drop the in-reach count (${before} -> ${after})`);
  assert(
    q('[data-sky-marker="ngc7000"]') == null,
    "the hidden nebula is still drawn on the finder - the lens filtered the count but not the sky",
  );
  assert(
    q('[data-sky-marker="m31"]') != null,
    "hiding NEBULAE took the galaxy with it - the filter is on the wrong axis",
  );

  // Put it back, and close the dial the way a user does.
  click(q('[data-lens-kind="nebula"]'));
  await settle();
  click(byId("sky-lens"));
  await settle();
  assert(byId("sky-lens") == null, "tapping outside did not close the dial");
});

await testAsync("the layers popover persists a switched-off overlay", async () => {
  click(q("[data-sky-layers]"));
  await settle();
  const pop = qdoc('[data-testid="sky-layers"]');
  assert(pop != null, "the layers button did not open the popover");

  const cloudRow = pop.querySelector('[data-layer-row="clouds"] [role="switch"]');
  assert(cloudRow != null, "no Cloud deck switch");
  eq(cloudRow.getAttribute("aria-checked"), "true", "precondition: the cloud layer starts on");
  click(cloudRow);
  await settle();

  const stored = JSON.parse(win.localStorage.getItem("astrodeck-next-sky-layers") ?? "{}");
  eq(stored.clouds, false, "the layer toggle did not reach localStorage:");
  eq(stored.horizon, true, "toggling one layer must not rewrite the others:");

  click(qdoc('[data-testid="sky-layers"] [data-layer-row="clouds"] [role="switch"]'));
  await settle();
  act(() => {
    win.document.dispatchEvent(new win.MouseEvent("mousedown", { bubbles: true }));
  });
  await settle();
});

// ============================================================== the viewer
await testAsync("a viewer sees the same button, dimmed, with the reason - and no request", async () => {
  seed(["view.status", "view.site_derived", "view.weather"]);
  await settle();

  const cta = byId("sky-cta");
  assert(cta != null, "the viewer lost the button entirely - it must be shown, not hidden");
  eq(cta.getAttribute("aria-disabled"), "true", "the viewer's CTA is not marked disabled:");
  assert(
    (cta.getAttribute("title") ?? "").includes("needs operator or admin access"),
    `the reason is not on the control: "${cta.getAttribute("title")}"`,
  );
  assert(!cta.hasAttribute("disabled"), "the native disabled attribute strips the reason from the tree");

  const before = asked.length;
  const hash = win.location.hash;
  click(cta);
  await settle();
  eq(win.location.hash, hash, "a locked press navigated anyway:");
  eq(asked.length, before, "a locked press fired a request:");
  const toasts = useStore.getState().toasts;
  assert(
    toasts.some((t) => (t.title ?? "").includes("needs operator or admin access")),
    "a locked press said nothing at all",
  );
});

// ============================================================== no rig at all
await testAsync("with no rig the browse banner appears and the CTA offers to fix it", async () => {
  seed(CAPS_OPERATOR, false);
  await settle();

  const banner = byId("sky-browse");
  assert(banner != null, "no browse banner with the rig disconnected");
  assert(/Browsing\./.test(banner.textContent), `the banner says nothing: "${banner.textContent}"`);
  assert(/IMAGE THIS needs a rig/.test(banner.textContent), "the banner must name the one thing that needs a rig");

  const cta = byId("sky-cta");
  eq(cta?.querySelector("span")?.textContent, "CONNECT THE RIG FIRST", "the CTA label with no rig:");
  assert(
    cta?.getAttribute("aria-disabled") == null,
    "CONNECT THE RIG FIRST must be LIVE - pressing it is how you get a rig",
  );
  click(cta);
  eq(win.location.hash, "#/rig/devices", "the connect CTA did not go to the devices screen:");
  act(() => { win.location.hash = "#/sky"; });
});

act(() => { root.unmount(); });
Date.now = realNow;

const total = passed + failed;
console.log(`skyHubDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
