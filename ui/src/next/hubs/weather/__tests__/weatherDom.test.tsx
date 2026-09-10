// weatherDom.test.tsx - the WEATHER hub, MOUNTED: the verdict, the band, the
// attribution, the override, the dome's wind arrow, and the two refusals.
//
//   Run directly:  npx tsx src/next/hubs/weather/__tests__/weatherDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE SEVEN THINGS THIS FILE IS FOR, and what goes wrong without each:
//
//  1. A PRECONDITION MARKER on every assertion of an absence. Each block below
//     runs after `hub-weather` and its screen marker have been found, and the
//     two refusal tests run after the ACCEPTED case has drawn the thing they
//     assert is missing - a positive control, so "not there" can only mean the
//     refusal did it (`verify-on-the-real-thing`).
//  2. THE VERDICT AGAINST A SEEDED BREACH. The headline and the chart shading
//     are two renderings of one `breachSpans` call; a verdict computed from a
//     different window is a screen that contradicts itself.
//  3. THE ATTRIBUTION ANCHOR. Open-Meteo's CC BY 4.0 asks for a LINK, not a
//     name. A <span> that says "Open-Meteo" is the naming half only, and the
//     whole point of the seam note in SkyConditionsPanel.tsx:329-336 is that
//     the anchor IS the compliance.
//  4. THE OVERRIDE'S APPLICATION PATH. The POST answers with a full
//     WeatherState, and it has to land through `handleEvent` like the socket
//     push and the cold GET. The fixture answers with an out-of-range
//     threshold: if the response were applied with `setState` the store would
//     show 150 %, because only `normalizeWeather` clamps it.
//  5. THE METEOROLOGICAL TURNAROUND, twice - as words in the WIND tile and as
//     the rotation of the dome's arrow. `wind_dir_deg` is where the wind comes
//     FROM; drawing it unturned points every arrow at where the cloud has been.
//  6. NO NaN WHEN THE FEED CARRIES NO SURFACE BLOCK. Each field of `now` is
//     independently nullable, so each clause has to drop on its own.
//  7. A NON-HOLDER FIRES NOTHING. `view.weather` is the whole hub's gate, and
//     the reason has to be on screen with an empty `asked[]` - not three
//     requests eating three redactions.
//
// Convention: jsdom by hand, createRoot + act, native events, printed tally plus
// the `{ passed, failed, total }` export (shell-and-tests.md section 4).

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/#/weather/conditions", pretendToBeVisual: true },
);
const win = dom.window as any;

win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} addEventListener() {} send() {} };
// RadarMap measures its own box with one; SkyDome takes a 2D context jsdom does
// not have and returns early, which is exactly the headless path it guards for.
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
win.Element.prototype.setPointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.releasePointerCapture = function () { /* jsdom has none */ };
win.scrollTo = () => {};

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "PointerEvent", "localStorage", "sessionStorage", "getComputedStyle",
  "matchMedia", "WebSocket", "ResizeObserver",
  "requestAnimationFrame", "cancelAnimationFrame",
  // `api.ts` and `lib/base.ts` read the BARE `location` at module scope.
  "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------ the fixture data
const NOW = Math.floor(Date.now() / 1000);
const STEP = 900;
const N = 96;
const isoAt = (ts: number): string => new Date(ts * 1000).toISOString();

/** A sustained breach from +5 h to +7 h, inside the seeded dark window. */
function forecast(): any {
  const times: string[] = [];
  const cloud: number[] = [];
  for (let i = 0; i < N; i++) {
    times.push(isoAt(NOW + i * STEP));
    cloud.push(i >= 20 && i <= 28 ? 80 : 5);
  }
  return {
    times, cloud,
    cloud_low: cloud.map(() => 0),
    cloud_mid: cloud.map(() => 0),
    cloud_high: cloud.map((c) => c),
  };
}

const NOW_BLOCK = {
  ts: isoAt(NOW),
  temp_c: 11.2,
  dewpoint_c: 7.0,
  humidity_pct: 68,
  wind_kmh: 12,
  // METEOROLOGICAL: the wind comes FROM the south-west, so the cloud drifts
  // toward the north-east. Every reading of this on screen must say NE.
  wind_dir_deg: 225,
  gust_kmh: 19,
  cloud_base_m: 2200,
};

function weatherFixture(over: Record<string, unknown> = {}): any {
  return {
    enabled: true,
    fetched_ts: NOW - 120,
    stale: false,
    ignore_tonight: false,
    threshold_pct: 50,
    sustain_minutes: 30,
    site_lat: 37.4,
    site_lon: -122.1,
    forecast: forecast(),
    astrospheric: null,
    alert: null,
    surface: null,
    now: NOW_BLOCK,
    ...over,
  };
}

/** What the fake server currently answers `GET /api/weather` with. Kept equal
 *  to the seeded slice so the screen's cold load is a no-op rather than a race
 *  with the fixture. */
let served: any = weatherFixture();
/** What the ignore-tonight POST answers with. */
let ignoreReply: any = null;

// ------------------------------------------------------------- fetch recorder
interface Ask { url: string; method: string; body: any }
const asked: Ask[] = [];

const DARK = { start_iso: isoAt(NOW + 2 * 3600), end_iso: isoAt(NOW + 10 * 3600) };

const MOON = {
  illumination: 0.71, phase_name: "Waning Gibbous", alt: 27, az: 168,
  separation_deg: 61.4, rise_unix: null, set_unix: NOW + 4 * 3600,
};

/** One low altitude band, all 36 azimuth samples read. LOW on purpose: at 15
 *  degrees the ray's ground track is 8 km long, so a 30 min drift at 12 km/h
 *  moves the ghosts without collapsing every one of them onto the same bearing,
 *  and the southern half of the band stays on the near side of the dome where
 *  it is drawn rather than culled. */
const CLOUD_ROWS: (number | null)[][] = [new Array(36).fill(0.7)];

function ok(json: any) {
  return {
    ok: true, status: 200, statusText: "OK",
    headers: { get: () => "application/json" },
    json: async () => json,
    text: async () => JSON.stringify(json),
  };
}
function notFound() {
  return {
    ok: false, status: 404, statusText: "Not Found",
    headers: { get: () => "application/json" },
    json: async () => ({}),
    text: async () => "",
  };
}

g.fetch = async (url: any, init: any) => {
  const u = String(url);
  const method = (init?.method ?? "GET").toUpperCase();
  let body: any = null;
  if (init?.body) { try { body = JSON.parse(init.body); } catch { body = init.body; } }
  asked.push({ url: u, method, body });

  if (method === "POST" && u.includes("/api/weather/ignore-tonight")) {
    return ok(ignoreReply ?? weatherFixture({ ignore_tonight: true }));
  }
  if (u.includes("/api/weather")) return ok(served);
  if (u.includes("/api/site/sky")) return ok({ dark_window: DARK, sun_alt_deg: -30 });
  if (u.includes("/api/visibility")) {
    return ok({
      date: "2026-09-10", transit_unix: NOW + 900, transit_alt: 56,
      transit_in_daylight: false, dark_start_unix: NOW, dark_end_unix: NOW + 6 * 3600,
      darkness_kind: "astronomical",
      samples: [{ t_unix: NOW, alt: 55, moon_alt: 10, sun_alt: -20 }],
      moon: MOON, best_window: null, alt_limit_deg: 30, never_rises_above_limit: false,
    });
  }
  if (u.includes("/api/site")) {
    return ok({ site: { is_default: false, horizon_min_deg: 20, horizon_points: [[0, 12], [180, 30]] }, version: 3 });
  }
  if (u.includes("/api/cloudmap")) {
    return ok({
      enabled: true, platform: "G18", observed_at: null, age_s: 90, stale: false,
      last_error: null, motion: null, credit: { source: "NOAA GOES", url: "x" },
      rows: CLOUD_ROWS, alt_start: 15, alt_step: 6, az_step: 10,
    });
  }
  return notFound();
};

// ------------------------------------------ imports, AFTER the globals are set
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../store");
const { nav } = await import("../../../router");
const { WeatherHub } = await import("../WeatherHub");
const { WeatherSettingsSheet } = await import("../sheets/WeatherSettingsSheet");
const { CloudmapSheet } = await import("../sheets/CloudmapSheet");

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
const q = (sel: string) => container.querySelector(sel) as any;
const all = (sel: string) => Array.from(container.querySelectorAll(sel)) as any[];
const byId = (id: string) => q(`[data-testid="${id}"]`);
const text = (id: string): string => String(byId(id)?.textContent ?? "");
const click = (el: any) => {
  assert(el != null, "click: the element is not there - the fixture is wrong, not the component");
  act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
};

const OPERATOR = {
  role: "operator", email: "op@rig",
  caps: ["view.status", "view.preview", "view.weather", "view.site_derived",
    "control.capture", "control.guide", "control.mount"],
};
const VIEWER = { role: "viewer", email: null, caps: ["view.status", "view.preview"] };

function seed(over: Record<string, unknown> = {}): void {
  useStore.setState({
    principal: OPERATOR,
    equipConnected: true,
    wsPhase: "up",
    wsConnected: true,
    telemetryStale: false,
    toasts: [],
    confirm: null,
    weather: weatherFixture(),
    config: {
      version: 3,
      cloudmap: { enabled: true, platform: "auto", poll_minutes: 10, half_px: 120 },
    },
    site: { is_default: false, horizon_min_deg: 20, latitude: 37.4, longitude: -122.1 },
    sequence: null,
    status: {
      connected: {}, looping: false, mode: "sim",
      mount: {
        ra_hours: 0, dec_deg: 0, ra_str: "00:00:00", dec_str: "+00:00:00",
        alt: 45, az: 90, tracking: true, parked: false, slewing: false,
      },
    },
    ...over,
  } as never);
}

// ------------------------------------------------------------------ mount
seed();
await act(async () => { root.render(createElement(WeatherHub)); });
await settle();

// ------------------------------------------------------- 1. the conditions screen

test("the hub and the conditions screen are on screen", () => {
  assert(byId("hub-weather") != null, "no hub-weather - the fixture is wrong, not the component");
  assert(byId("wx-conditions") != null, "the conditions screen did not render");
  assert(byId("wx-settings-btn") != null, "the weather-settings gear is missing from the hub header");
});

test("the verdict is computed from the seeded breach, in the dark window", () => {
  const v = text("wx-verdict");
  assert(/^GOOD UNTIL \d\d:\d\d$/.test(v), `verdict headline: got "${v}"`);
  const body = String(byId("wx-conditions").textContent);
  assert(/high cloud \d\d:\d\d - \d\d:\d\d/.test(body), "the sub-line does not span the breach");
  // The engine's cloud gate is gone (weather.py:685-726). A headline that
  // promised a hold would be describing an engine that no longer exists.
  assert(!/hold armed/.test(body), "the screen promises a cloud hold the engine does not arm");
});

test("the provider chip is a LINK, which is what CC BY 4.0 asks for", () => {
  const a = q('a[href="https://open-meteo.com/"]');
  assert(a != null, "no anchor to open-meteo.com - the label alone is the naming half only");
  eq(String(a.textContent), "Open-Meteo", "source label:");
  assert(/updated \d+ min ago/.test(text("wx-provider")), `chip: got "${text("wx-provider")}"`);
});

test("all six tiles are present, and none of them is blank", () => {
  for (const id of ["wind", "humidity", "seeing", "transparency", "dew", "moon"]) {
    const el = byId(`wx-tile-${id}`);
    assert(el != null, `no ${id} tile - the band must keep every slot`);
    assert(String(el.textContent).trim().length > 0, `the ${id} tile rendered empty`);
  }
});

test("the wind is turned around: from SW, drifting to NE", () => {
  const t = text("wx-tile-wind");
  assert(/12 km\/h/.test(t), `wind speed: got "${t}"`);
  assert(/SW to NE at cloud base/.test(t),
    `wind_dir_deg 225 is METEOROLOGICAL and must print both ends, got "${t}"`);
  assert(/gust 19/.test(t), `gust clause: got "${t}"`);
});

test("the dew margin is ambient minus dew point, and warns when it closes", () => {
  const t = text("wx-tile-dew");
  assert(/4\.2°C/.test(t), `11.2 - 7.0 = 4.2, got "${t}"`);
  assert(/ambient 11\.2 · dew 7\.0/.test(t), `dew sub: got "${t}"`);
  eq(byId("wx-tile-dew").getAttribute("data-tone"), null, "a 4.2 C margin is not a warning:");
});

test("no Astrospheric key: the two tiles say so and keep their slot", () => {
  assert(/not measured/.test(text("wx-tile-seeing")), text("wx-tile-seeing"));
  assert(/add an Astrospheric key/.test(text("wx-tile-seeing")), text("wx-tile-seeing"));
  assert(/not measured/.test(text("wx-tile-transparency")), text("wx-tile-transparency"));
});

test("the moon rides on the picked target, and there is none picked here", () => {
  const t = text("wx-tile-moon");
  assert(/no target/.test(t), `with nothing picked the tile must say so, got "${t}"`);
  assert(!/°\s*from/.test(t), `a separation from nothing leaked: "${t}"`);
  assert(!asked.some((a) => a.url.includes("/api/visibility")),
    "the visibility request fired with no target to ask about");
});

test("the fail-open veto sentence is on screen and matches the engine", () => {
  const body = String(byId("wx-conditions").textContent);
  assert(/fails OPEN/.test(body), "the fail-open half of the veto explanation is missing");
  assert(/RAIN/.test(body), "the veto is a RAIN veto; the sentence must say so");
  assert(/fails closed/.test(body), "the safety monitor's opposite behaviour is missing");
});

test("the chart drew the series and its threshold rule", () => {
  assert(byId("wx-cloud-chart") != null, "no chart");
  const body = String(byId("wx-cloud-chart").textContent);
  assert(/hold ≥50% for 30m/.test(body), `the rule label must name the active policy, got "${body}"`);
  assert(all("#root path").length >= 4, "fewer than four series paths were drawn");
});

// ------------------------------------------------------- 2. the override POST

await testAsync("IGNORE WEATHER TONIGHT posts, and the reply lands through handleEvent", async () => {
  // The reply carries an out-of-range threshold on purpose: only
  // `normalizeWeather` (i.e. `handleEvent`) clamps it to 100, so a `setState`
  // shortcut would leave 150 in the store and this assertion goes red.
  ignoreReply = weatherFixture({ ignore_tonight: true, threshold_pct: 150 });
  const before = asked.length;
  click(byId("wx-ignore"));
  await settle();

  const posts = asked.slice(before).filter(
    (a) => a.method === "POST" && a.url.includes("/api/weather/ignore-tonight"));
  eq(posts.length, 1, "exactly one ignore-tonight POST:");
  eq(posts[0].body?.ignore, true, "POST body:");

  const w = useStore.getState().weather;
  eq(w?.ignore_tonight, true, "the store did not take the reply:");
  eq(w?.threshold_pct, 100,
    "the reply was applied without normalizeWeather - it did not go through handleEvent:");
  assert(byId("wx-override-card") != null, "the override banner did not appear");
});

// ------------------------------------------------- 3. an absent surface block

await testAsync("with no `now` block the tiles say 'not reported' and print no NaN", async () => {
  ignoreReply = null;
  served = weatherFixture({ now: null });
  act(() => { useStore.setState({ weather: served } as never); });
  await settle();

  assert(byId("wx-tile-wind") != null, "no wind tile - the band lost a slot instead of a value");
  assert(/not reported/.test(text("wx-tile-wind")), text("wx-tile-wind"));
  assert(/this feed carries no wind/.test(text("wx-tile-wind")), text("wx-tile-wind"));
  assert(/not reported/.test(text("wx-tile-humidity")), text("wx-tile-humidity"));
  assert(/not reported/.test(text("wx-tile-dew")), text("wx-tile-dew"));
  const body = String(byId("wx-conditions").textContent);
  assert(!/NaN/.test(body), "NaN reached the screen");
  assert(!/undefined/.test(body), "an undefined reached the screen");
});

// ---------------------------------------------------------------- 4. the dome

await testAsync("the dome screen draws the wind arrow along the DRIFT bearing", async () => {
  served = weatherFixture();
  act(() => { useStore.setState({ weather: served } as never); });
  act(() => { nav.replace("/weather/sky"); });
  await settle();

  assert(byId("wx-sky") != null, "the sky screen did not render");
  const arrow = byId("wx-wind-arrow");
  assert(arrow != null, "no wind arrow on the dome screen");
  const rot = arrow.querySelector("g")?.getAttribute("transform");
  eq(rot, "rotate(45 12 12)",
    "wind_dir_deg 225 is where the wind comes FROM; the arrow must point at 45 (NE):");
  eq(arrow.getAttribute("data-toward"), "45", "drift bearing:");

  const body = String(byId("wx-sky").textContent);
  assert(/wind 12 km\/h SW to NE/.test(body), `the legend must name both ends, got "${body}"`);
  assert(/2\.2 km base/.test(body), "the cloud-base height is missing from the legend");
});

await testAsync("the overlay is ON the canvas, in the canvas's own projection", async () => {
  // The whole point of D-WX-1. `SkyDome` returns before painting in jsdom -
  // getContext is null - so this can only be here because the geometry is
  // published ABOVE that guard and the overlay is registered on the panel.
  const svg = byId("wx-dome-overlay");
  assert(svg != null, "no overlay over the dome canvas");
  const wrap = q("[data-dome-wrap]");
  assert(wrap != null, "the overlay is not inside the canvas's own wrapper");
  assert(Number(svg.getAttribute("width")) > 0,
    `the overlay has no width, so it never saw a geometry: "${svg.getAttribute("width")}"`);

  // The site's ACTIVE polyline is seeded (`/api/site` -> horizon_points), so
  // the profile has to be drawn on the sphere rather than only written out.
  const hz = byId("wx-dome-horizon");
  assert(hz != null, "the horizon profile is not drawn on the dome");
  assert(hz.querySelectorAll("polygon").length > 0,
    "the horizon group is empty, which reads as 'nothing is in the way'");
  assert(/horizon profile/.test(text("wx-sky")),
    "the legend does not name the horizon profile it just drew");
});

await testAsync("no measured wind SPEED means NO ghost tiles, and the legend drops them", async () => {
  // Positive control first: with a speed in the feed the ghosts ARE drawn, so
  // the absence below can only be the missing speed.
  assert(byId("wx-dome-ghost") != null,
    "precondition: the +30 min ghosts are drawn when the wind is measured");
  assert(byId("wx-dome-ghost").querySelectorAll("polygon").length > 0,
    "the ghost group is empty - the positive control drew nothing to compare against");
  assert(/cloud in 30 min/.test(text("wx-sky")), "the legend does not name the ghosts it drew");

  // A direction but no speed. The arrow stays (the bearing IS measured); the
  // ghosts must go, because advecting at the prototype's 12 km/h would be a
  // drawing of a wind nobody measured.
  act(() => {
    useStore.setState({
      weather: weatherFixture({ now: { ...NOW_BLOCK, wind_kmh: null } }),
    } as never);
  });
  await settle();
  assert(byId("wx-wind-arrow") != null,
    "precondition: a measured bearing still draws the rose");
  assert(byId("wx-dome-ghost") == null,
    "ghosts were advected at a wind speed the feed does not carry");
  assert(!/cloud in 30 min/.test(text("wx-sky")),
    "the legend still names a mark the picture no longer has");
});

await testAsync("with no wind in the feed there is NO arrow and the legend says so", async () => {
  act(() => { useStore.setState({ weather: weatherFixture({ now: null }) } as never); });
  await settle();
  assert(byId("wx-sky") != null, "precondition: the sky screen is still up");
  assert(byId("wx-wind-arrow") == null,
    "an arrow was drawn for a wind nobody measured (the prototype's 12 km/h default)");
  assert(/wind not reported/.test(String(byId("wx-sky").textContent)),
    "the legend does not say the wind is absent");
});

// --------------------------------------------------------------- 5. the radar

await testAsync("the radar map mounts when weather is on", async () => {
  act(() => { useStore.setState({ weather: weatherFixture() } as never); });
  act(() => { nav.replace("/weather/radar"); });
  await settle();
  assert(byId("wx-radar") != null, "the radar screen did not render");
  const layers = all("button").filter((b) => /IR satellite/.test(String(b.textContent)));
  eq(layers.length, 1, "RadarMap's layer buttons (the positive control):");
  assert(/dashed ray is your line of sight/.test(String(byId("wx-radar").textContent)),
    "the pierce-ray explanation is missing");
});

await testAsync("weather off does NOT mount the radar map", async () => {
  // The screens cold-load `/api/weather` on mount, so the fake server has to
  // agree that it is off - otherwise the reply switches it back on underneath
  // the assertion and the test passes for the wrong reason.
  served = weatherFixture({ enabled: false });
  act(() => { useStore.setState({ weather: served } as never); });
  await settle();
  assert(byId("wx-radar-off") != null, "no off card on the radar screen");
  const layers = all("button").filter((b) => /IR satellite/.test(String(b.textContent)));
  eq(layers.length, 0,
    "RadarMap is mounted for a feature that is switched off, and its tiles will be fetched:");
  assert(all('img[src*="/api/weather/tile/"]').length === 0, "a tile image was rendered");
});

await testAsync("weather off on CONDITIONS names the sheet that turns it back on", async () => {
  act(() => { nav.replace("/weather/conditions"); });
  await settle();
  assert(byId("wx-off") != null, "no off card on the conditions screen");
  assert(/Turn it on in Weather settings\./.test(text("wx-off")), text("wx-off"));
  assert(byId("wx-cloud-chart") == null, "the chart is still drawn over a disabled feed");
  assert(byId("wx-band") == null, "the tile band is still drawn over a disabled feed");
});

// ------------------------------------------------------------- 6. the refusal

await testAsync("a role without view.weather sees the reason and fires NOTHING", async () => {
  served = weatherFixture();
  act(() => { useStore.setState({ weather: served, principal: VIEWER } as never); });
  await settle();
  asked.length = 0;
  act(() => { nav.replace("/weather/conditions"); });
  await settle();

  assert(byId("hub-weather") != null, "the hub frame vanished for a viewer instead of explaining");
  const why = byId("wx-no-cap");
  assert(why != null, "no reason card for a role without view.weather");
  assert(/Weather needs operator or admin access\./.test(String(why.textContent)),
    `the capability sentence is missing: "${String(why.textContent).slice(0, 140)}"`);
  assert(/radar map discloses roughly where the rig is/.test(String(why.textContent)),
    "the WHY of the split is missing, which makes it read as an arbitrary lock");
  assert(byId("wx-conditions") == null, "the conditions screen mounted for a non-holder");
  eq(asked.length, 0, `a non-holder issued ${asked.map((a) => a.url).join(", ")}:`);
});

// ------------------------------------------------------------- 7. the sheets

await testAsync("the gear is honest-disabled for an operator: it explains, it does not open", async () => {
  // `config.site_optics` is admin-only, and an operator holds weather but not
  // the config that shapes it. The button still RENDERS - hiding it would make
  // the screen a different screen for a different role - and a press has to say
  // why rather than doing nothing.
  act(() => { useStore.setState({ principal: OPERATOR, toasts: [] } as never); });
  await settle();
  assert(byId("wx-conditions") != null, "precondition: the hub is back for an operator");
  const gear = byId("wx-settings-btn");
  eq(gear.getAttribute("aria-disabled"), "true", "the gear must be honest-disabled, not native-disabled:");
  click(gear);
  await settle();
  eq(String(win.location.hash), "#/weather/conditions", "a locked gear opened the sheet anyway:");
  const toasts = useStore.getState().toasts;
  eq(toasts.length, 1, "a locked press must say why:");
  assert(/needs admin access/.test(String(toasts[0].title)),
    `the lock reason must name the access, got "${String(toasts[0].title)}"`);
});

await testAsync("an admin's gear opens the weatherSettings sheet", async () => {
  act(() => {
    useStore.setState({
      principal: { role: "admin", email: "a@rig", caps: [...OPERATOR.caps, "config.site_optics"] },
    } as never);
  });
  await settle();
  click(byId("wx-settings-btn"));
  await settle();
  assert(/weatherSettings/.test(String(win.location.hash)),
    `the gear must push the sheet onto the route, got "${String(win.location.hash)}"`);
});

await testAsync("the weatherSettings sheet mounts the panel that owns the write-only key", async () => {
  await act(async () => { root.render(createElement(WeatherSettingsSheet)); });
  await settle();
  assert(byId("sheet-weather-settings") != null, "the sheet frame did not render");
  const t = String(container.textContent);
  assert(/Cloud threshold \(%\)/.test(t), "the threshold field is missing");
  assert(/Sustained for \(min\)/.test(t), "the sustain field is missing");
  assert(/Astrospheric API key/.test(t), "the seeing-feed key field is missing");
  // The secret echo pattern: a password field that is never seeded from the
  // masked config, so an empty write means "leave the stored key alone".
  const key = container.querySelector('input[type="password"]') as any;
  assert(key != null, "the Astrospheric key must be a password field");
  eq(String(key.value), "", "the panel echoed a stored secret back into the form:");
});

await testAsync("the cloudmap sheet mounts the panel and keeps its advisory caveat", async () => {
  await act(async () => { root.render(createElement(CloudmapSheet)); });
  await settle();
  assert(byId("sheet-cloudmap") != null, "the sheet frame did not render");
  const t = String(container.textContent);
  assert(/Satellite/.test(t), "the satellite pin control is missing");
  assert(/Poll every \(min\)/.test(t), "the poll interval is missing");
  assert(/advisory/i.test(t),
    "the model gates nothing and the panel must keep saying so");
});

act(() => { root.unmount(); });

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`weatherDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
