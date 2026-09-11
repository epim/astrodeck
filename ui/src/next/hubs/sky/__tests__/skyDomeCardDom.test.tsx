// skyDomeCardDom.test.tsx - the skydome card on the Sky finder (D-SKY-2).
//
//   Run directly:  npx tsx src/next/hubs/sky/__tests__/skyDomeCardDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc --noEmit`.
//
// Six things worth a mounted test, and each one is a defect this card could
// have shipped with:
//
//   1. THE CARD IS ACTUALLY A DOME. A card with a heading and no canvas would
//      satisfy every "did it render" assertion ever written, so the precondition
//      asserts the marker, the word SKYDOME and a `<canvas>` INSIDE the card -
//      the finder above has its own drawing surface, so the query is scoped.
//   2. ONE MOUNT, ONE DOME REQUEST. `/api/cloudmap/dome` is 540 rays through a
//      cloud volume and the panel repeats it every 60 s for as long as it is on
//      screen. Two mounts of it on one screen would double that all night.
//   3. A ROLE WITHOUT `view.weather` FIRES NOTHING. `/api/cloudmap*` needs the
//      capability (`app.py:2590-2612`); a mounted panel would put three 403s a
//      minute into the log until dawn - the defect 3f10681c closed for the radar
//      map. So the viewer gets the card, the reason, and NO panel: the assertion
//      is on the absence of the REQUEST, not on the absence of a box.
//   4. THE PILL SCROLLS, IT DOES NOT NAVIGATE. D-SKY-2 turned the SKYDOME pill
//      from a link to the Weather hub into a scroll-to. The hash is the test: a
//      navigation would change it, and a scroll must not.
//   5. ONE TITLE, NOT TWO. The panel's legacy `Panel` chrome is suppressed with
//      `chrome="bare"` inside the new Card - and the DEFAULT still draws it, so
//      `#/classic`'s monitor grid is untouched. Both halves are asserted,
//      because only asserting the first would let the default flip to `bare` and
//      silently strip the title off the classic screen.
//   6. THE MARKS ARE ON IT. A dome with no horizon profile and no path to dawn
//      is the Weather hub's picture minus the two things the finder knows.

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
  `<!doctype html><html><body><div id="root"></div><div id="classic"></div></body></html>`,
  { url: "http://local/#/sky", pretendToBeVisual: true },
);
const win = dom.window as any;

/** Flipped by the reduced-motion test. Everything else this hub asks matchMedia
 *  (the breakpoint's two min-width queries) must keep answering false, or the
 *  hub renders as a desktop and the card's height changes with it. */
let reduceMotion = false;
win.matchMedia = (query: string) => ({
  matches: /prefers-reduced-motion/.test(String(query)) ? reduceMotion : false,
  media: String(query),
  addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
Object.defineProperty(win, "isSecureContext", { value: false, configurable: true });
win.WebSocket = class { close() {} addEventListener() {} send() {} };
win.Element.prototype.setPointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.releasePointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.scrollBy = function () { /* jsdom has none */ };

/** jsdom implements no scrolling at all, so this stub is the only way to see
 *  WHICH element was asked to come into view - which is the whole assertion. */
const scrolled: { id: string; behavior: string }[] = [];
win.Element.prototype.scrollIntoView = function (this: any, opts: any) {
  scrolled.push({ id: String(this.id ?? ""), behavior: String(opts?.behavior ?? "") });
};

// ------------------------------------------------------------- fetch double
const asked: string[] = [];
const NOW = Date.UTC(2026, 8, 10, 5, 0, 0);

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
];

const tonightPicks = regionRows.map((r) => ({
  id: r.id, name: r.label, type: r.type,
  ra_hours: r.ra_hours, dec_deg: r.dec_deg, mag: r.mag, size_arcmin: r.size_arcmin,
  difficulty: "easy", surface_brightness: 21, difficulty_source: "heuristic",
  max_alt: 60, transit_unix: NOW / 1000 + 7200,
  best_window: null, moon_sep_deg: 80, never_rises_above_limit: false, score: 1,
}));

// dark_end five hours out, so the walk to dawn has samples and the overlay has
// a path to draw. An empty track draws nothing, which would make assertion 6
// vacuous.
const visibilityNight = {
  date: "2026-09-10",
  transit_unix: NOW / 1000 + 7200, transit_alt: 62, transit_in_daylight: false,
  dark_start_unix: NOW / 1000 - 3600, dark_end_unix: NOW / 1000 + 5 * 3600,
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
  cell_km: [2.0, 2.0],
  rows: Array.from({ length: 14 }, () => Array.from({ length: 36 }, () => 0.05)),
};

const g = globalThis as any;
g.fetch = async (url: any, init?: any) => {
  const u = String(url);
  asked.push(`${init?.method ?? "GET"} ${u}`);
  if (u.includes("/api/cloudmap/dome")) return ok(domePayload);
  if (u.includes("/api/cloudmap/at")) {
    return ok({ probability: 0.05, basis: "granule", beam_m: 90 });
  }
  if (u.includes("/api/cloudmap")) {
    return ok({
      enabled: true, platform: "G18", observed_at: "2026-09-10T05:00:00Z",
      age_s: 120, stale: false, last_error: null, motion: null,
      credit: { source: "NOAA GOES", url: "" },
    });
  }
  if (u.includes("/api/catalog/tonight")) {
    return ok({ date: "2026-09-10", site_is_default: false, picks: tonightPicks });
  }
  if (u.includes("/api/catalog/region")) {
    return ok({ rows: regionRows, truncated: false, catalog_degraded: false, notes: [] });
  }
  if (u.includes("/api/catalog?q=")) return ok({ rows: [] });
  if (u.includes("/api/visibility")) return ok(visibilityNight);
  if (u.includes("/api/site")) {
    return ok({
      site: {
        name: "Back lawn", latitude: 47.61, longitude: -122.33, elevation_m: 52,
        is_default: false, horizon_min_deg: 20,
        horizon_points: [[0, 12], [180, 8], [359, 12]],
      },
      version: 3,
    });
  }
  return ok({});
};

for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "HTMLVideoElement", "HTMLCanvasElement", "Element", "SVGElement", "Node",
  "Event", "CustomEvent", "MouseEvent", "KeyboardEvent", "PointerEvent",
  "localStorage", "getComputedStyle", "matchMedia", "requestAnimationFrame",
  "cancelAnimationFrame", "WebSocket", "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const realNow = Date.now;
/** The frozen clock, in milliseconds. Movable, because `api/cloudmap.ts`'s dome
 *  cache is keyed on `Date.now()` and one of the tests below has to stand on
 *  the far side of its TTL. Everything else reads it as the constant it was. */
let clockMs = NOW;
Date.now = () => clockMs;

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../store");
const { SkyHub } = await import("../SkyHub");
const { SkyDomePanel } = await import("../../../../components/cloudmap/SkyDomePanel");
const { DOME_CARD_ID, DOME_NEEDS_WEATHER } = await import("../cards/DomeCard");
const { DOME_TTL_MS, getCloudmapDome, resetCloudmapDomeCache } =
  await import("../../../../api/cloudmap");

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
const byId = (id: string): any => q(`[data-testid="${id}"]`);
const card = (): any => byId(DOME_CARD_ID);
const click = (el: any) => {
  assert(el != null, "click: the element is not there - the fixture is wrong, not the component");
  act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
};
const cloudmapCalls = (): string[] => asked.filter((a) => a.includes("/api/cloudmap"));

const CAPS_OPERATOR = [
  "view.status", "view.weather", "view.site_derived", "view.site_precise",
  "control.capture", "control.mount",
];
const CAPS_VIEWER = ["view.status", "view.preview"];

function seedState(caps: string[]): void {
  useStore.setState({
    principal: {
      role: caps.includes("view.weather") ? "operator" : "viewer", caps, name: "tester",
    },
    site: {
      name: "Back lawn", latitude: 47.61, longitude: -122.33, elevation_m: 52,
      is_default: false, horizon_min_deg: 20,
    },
    equipConnected: true,
    wsPhase: "up",
    toasts: [],
    framing: null,
    status: {
      connected: {
        camera: { connected: true, name: "sim camera" },
        telescope: { connected: true, name: "sim mount" },
      },
      looping: false,
      // A real pointing, so the dome has a cross to draw and the panel's
      // look-ahead ladder has somewhere to ask about.
      mount: {
        ra_hours: 0.7, dec_deg: 41, ra_str: "00h42m", dec_str: "+41d16m",
        alt: 57, az: 64, tracking: true, parked: false, slewing: false,
      },
      optics: {
        have_optics: true, source: "config",
        focal_length_mm: 800, pixel_size_um: 3.76,
        sensor_width_px: 6248, sensor_height_px: 4176,
        image_scale_arcsec_px: 0.97, fov_w_deg: 1.68, fov_h_deg: 1.12, fov_diag_deg: 2.02,
      },
    },
    config: {
      optics: {
        focal_length_mm: 800, pixel_size_um: 3.76, sensor_width_px: 6248,
        sensor_height_px: 4176, auto_from_camera: false, telescope_name: "",
      },
      safety: { horizon: null },
      survey: { online_fetch: false },
      cloudmap: { enabled: true },
    },
    weather: {
      enabled: true, fetched_ts: NOW / 1000, stale: false, ignore_tonight: false,
      threshold_pct: 60, sustain_minutes: 30, site_lat: 47.61, site_lon: -122.33,
      forecast: {
        times: [new Date(NOW).toISOString()],
        cloud: [8], cloud_low: [8], cloud_mid: [0], cloud_high: [0],
      },
      astrospheric: null, alert: null,
      now: {
        ts: new Date(NOW).toISOString(), temp_c: 14, dewpoint_c: 6, humidity_pct: 60,
        wind_kmh: 12, wind_dir_deg: 225, gust_kmh: 18, cloud_base_m: 2200,
      },
    },
  } as never);
}

/** The store write IS a render, so it goes inside `act` like every other one. */
function seed(caps: string[]): void {
  act(() => { seedState(caps); });
}

// ===================================================== the operator's screen
seed(CAPS_OPERATOR);
win.location.hash = "#/sky";
// The dome cache is module state that outlives a mount. Cleared here so the
// request count below is this mount's own, whatever else in the process has
// asked for a grid.
resetCloudmapDomeCache();
const root = createRoot(container);
await act(async () => { root.render(createElement(SkyHub)); });
await settle();


test("precondition: the card is on the finder and it is a dome, not a heading", () => {
  const c = card();
  assert(c != null, `no ${DOME_CARD_ID} on the hub - the card never mounted`);
  assert(
    /SKYDOME/.test(c.textContent ?? ""),
    `the card is unlabelled: "${(c.textContent ?? "").slice(0, 80)}"`,
  );
  assert(
    c.querySelector("canvas") != null,
    "the card has no canvas inside it - a heading with no dome under it",
  );
  eq(c.id, DOME_CARD_ID, "the card's anchor id (the pill scrolls to it):");
});

test("it is the LAST card, and the lock card is above it", () => {
  const kids = Array.from(container.firstChild?.childNodes ?? []) as any[];
  const idx = (id: string) => kids.findIndex(
    (n) => n.getAttribute?.("data-testid") === id
        || n.querySelector?.(`[data-testid="${id}"]`) != null,
  );
  const domeAt = idx(DOME_CARD_ID);
  assert(domeAt >= 0, "the card is not a child of the hub root");
  eq(domeAt, kids.length - 1, "the dome card must be the last card in the stack:");
  const lockAt = idx("sky-cta");
  assert(
    lockAt >= 0 && lockAt < domeAt,
    `the lock card (${lockAt}) must come before the dome (${domeAt})`,
  );
});

const domeAsks = (): string[] => asked.filter((a) => a.includes("/api/cloudmap/dome"));

test("the two consumers of the dome grid make ONE request between them", () => {
  const domes = domeAsks();
  // ONE, and the count is exact on purpose.
  //
  // TWO widgets want this grid on `#/sky`: the card's own panel
  // (`SkyDomePanel.tsx`, every 60 s) and the finder's cloud layer, which has
  // asked for the identical 6 x 10 grid since long before this card existed
  // (`finder/model.ts`). It used to be TWO REQUESTS, and this test pinned that
  // number rather than hiding it behind a `>= 1`, because the server WALKS 540
  // rays through the cloud volume for each one. T-R7-21a item 21 put a TTL
  // cache with a shared in-flight promise behind `getCloudmapDome`, so the
  // second consumer is answered from the first one's grid.
  //
  // The count still has to be exact: a panel mounted per render, or mounted
  // twice with the cache reset between, reads 2+ here.
  eq(domes.length, 1, `dome grid requests on one mount (finder + card, coalesced = 1):`);
  assert(
    domes.every((d) => d.startsWith("GET ") && d.includes("/api/cloudmap/dome?alt_step=")),
    `a dome request is not the one the panel documents: "${domes.join(" | ")}"`,
  );
});

await testAsync("and a fresh grid is fetched once the TTL is behind us", async () => {
  // The saving must not become a freeze. Both consumers poll every 60 s, the
  // cache holds for `DOME_TTL_MS` (55 s), and past that the next poll is a real
  // request again - otherwise the card would draw one granule all night.
  const before = domeAsks().length;
  clockMs += DOME_TTL_MS + 1;
  const grid = await getCloudmapDome(6, 10);
  eq(domeAsks().length, before + 1,
    "the cache outlived its TTL - the dome would stop updating:");
  assert(grid != null, "the refetch returned nothing");
  assert(DOME_TTL_MS < 60_000,
    `DOME_TTL_MS is ${DOME_TTL_MS} ms, at or above the consumers' own 60 s poll`);
  clockMs = NOW;
});

test("the new Card is the only frame: no legacy Panel chrome inside it", () => {
  const c = card();
  assert(
    c.querySelector(".panel-title") == null,
    "the legacy Panel title is inside the new Card - two titles for one dome",
  );
  assert(
    c.querySelector("section.panel") == null,
    "the legacy Panel section is inside the new Card - a bordered box inside a bordered box",
  );
  // Not vacuous: the bare wrapper IS there, so the panel rendered and simply
  // rendered without its frame.
  assert(c.querySelector("[data-dome-bare]") != null, "the panel did not render at all");
});

test("the marks the finder knows are drawn over the dome", () => {
  const c = card();
  assert(
    c.querySelector('[data-testid="wx-dome-overlay"]') != null,
    "no overlay over the card's dome",
  );
  assert(
    c.querySelector('[data-testid="wx-dome-horizon"]') != null,
    "the site's horizon profile is not drawn - the card would show a tree line as clear sky",
  );
  assert(
    c.querySelector('[data-testid="wx-dome-path"]') != null,
    "the locked target's path to dawn is not drawn",
  );
});

// ================================================================== the pill
test("the SKYDOME pill scrolls to the card and does NOT navigate", () => {
  const pill = byId("sky-dome");
  assert(pill != null, "no SKYDOME pill in the status row");
  eq(pill.getAttribute("aria-label"), "scroll to the skydome card", "the pill's label:");
  assert(
    !(pill.textContent ?? "").includes("›"),
    `the pill still carries a chevron: "${pill.textContent}"`,
  );
  assert(
    pill.getAttribute("aria-disabled") == null,
    "the pill must never be locked - scrolling needs no capability",
  );

  const before = win.location.hash;
  scrolled.length = 0;
  click(pill);
  // THE HASH FIRST. The hash IS the state in this app, so "did not navigate" is
  // the half that a re-introduced `nav.go` breaks; the scroll assertions below
  // then say the press did the other thing instead of nothing at all.
  eq(win.location.hash, before, "the pill navigated instead of scrolling:");
  eq(scrolled.length, 1, "the pill scrolled nothing:");
  eq(scrolled[0].id, DOME_CARD_ID, "the pill scrolled the wrong element:");
});

test("reduced motion turns the scroll into a jump", () => {
  reduceMotion = true;
  scrolled.length = 0;
  click(byId("sky-dome"));
  reduceMotion = false;
  eq(scrolled.length, 1, "the pill scrolled nothing:");
  eq(scrolled[0].behavior, "auto", "a reduced-motion setting still got a smooth scroll:");
});

test("WEATHER opens the full dome screen, aimed at the lock", () => {
  const btn = byId("sky-dome-weather");
  assert(btn != null, "no WEATHER button on the card header");
  click(btn);
  const h = decodeURIComponent(win.location.hash);
  assert(h.startsWith("#/weather/sky"), `WEATHER went to ${h}`);
  assert(/target=m31/.test(h), `WEATHER did not carry the lock through: ${h}`);
  act(() => { win.location.hash = "#/sky"; });
});

// ================================================================ the viewer
// A FRESH MOUNT AS A VIEWER, not a demotion of the operator's. The case that
// matters is a viewer OPENING the hub - and it is also the only construction
// under which the no-request assertion can see anything: leaving the operator's
// panel mounted and changing the role would keep the panel alive, so it would
// fire nothing new whether the gate worked or not.
act(() => { root.unmount(); });
seed(CAPS_VIEWER);
const cloudmapBeforeViewer = cloudmapCalls().length;
const viewerRoot = createRoot(container);
await act(async () => { viewerRoot.render(createElement(SkyHub)); });
await settle();

/** Two tests and not one on purpose: the no-request assertion is the one that
 *  matters most, and folding it in behind the copy assertions would let a
 *  broken gate report itself as a wording failure. */
await testAsync("a viewer keeps the card and is told who can see the dome", async () => {
  const c = card();
  assert(c != null, "the viewer lost the card entirely - it must be shown, not hidden");
  assert(/SKYDOME/.test(c.textContent ?? ""), "the viewer's card is unlabelled");
  assert(
    (c.textContent ?? "").includes("needs operator or admin access"),
    `the card does not name who can see it: "${(c.textContent ?? "").slice(0, 120)}"`,
  );
  eq((c.textContent ?? "").includes(DOME_NEEDS_WEATHER), true, "the card's own sentence:");
});

test("a role without view.weather never mounts the panel, so /api/cloudmap is not asked", () => {
  const c = card();
  assert(c != null, "precondition: the card is still on screen");
  eq(
    cloudmapCalls().length, cloudmapBeforeViewer,
    "a role without view.weather asked /api/cloudmap anyway - three 403s a minute until dawn:",
  );
  assert(
    c.querySelector("canvas") == null,
    "the panel is mounted for a role that cannot read it",
  );
});

test("the viewer's WEATHER button is honest-disabled, not missing and not native-disabled", () => {
  const btn = byId("sky-dome-weather");
  assert(btn != null, "the viewer lost the WEATHER button");
  eq(btn.getAttribute("aria-disabled"), "true", "the viewer's WEATHER button is not marked disabled:");
  assert(!btn.hasAttribute("disabled"), "the native disabled attribute strips the reason from the tree");
  const before = win.location.hash;
  click(btn);
  eq(win.location.hash, before, "a locked press navigated anyway:");
});

act(() => { viewerRoot.unmount(); });

// ================================================ the classic mount, unchanged
await testAsync("with no chrome prop the panel still draws its own Panel", async () => {
  const el = win.document.getElementById("classic") as any;
  const classicRoot = createRoot(el);
  await act(async () => {
    classicRoot.render(createElement(SkyDomePanel, { pointing: { alt: 57, az: 64 } }));
  });
  await settle();
  const title = el.querySelector(".panel-title");
  assert(
    title != null,
    "the default lost the legacy Panel - #/classic's monitor grid would render an untitled dome",
  );
  eq(title.textContent, "Sky dome", "the classic panel's title:");
  assert(el.querySelector("section.panel") != null, "no panel section in the classic mount");
  assert(el.querySelector("[data-dome-bare]") == null, "the default rendered the bare wrapper");
  act(() => { classicRoot.unmount(); });
});

Date.now = realNow;

const total = passed + failed;
console.log(`skyDomeCardDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
