// skyViewDom.test.tsx - the finder, MOUNTED, in MAP mode with no camera and no
// gyro (which is what jsdom is, and what a desktop is).
//
//   Run directly:  npx tsx src/next/hubs/sky/finder/__tests__/skyViewDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE PRECONDITION MATTERS MORE THAN USUAL HERE. A finder that renders an empty
// 370 px box passes every "no crash" check ever written, so the first assertions
// are that the box exists AND that it is printing a real azimuth. Without those,
// every assertion below could be true of a blank div.
//
// The four contracts worth a mounted test:
//   1. The drag arithmetic reaches the screen: 61 px is ten degrees at 6.1 px per
//      degree, and the readout says so.
//   2. The lock radius is real: a marker 20 px from the reticle locks, the same
//      marker 61 px away does not. This is the sabotage target.
//   3. The cloud dome is asked for ONCE per mount, at the server's own
//      resolution - and NOT AT ALL for a principal without view.weather, who
//      instead gets a sentence saying why the layer is missing.
//   4. The wind arrows are drawn from the METEOROLOGICAL convention turned
//      around. A feed saying 225 (from the south-west) must drift the sky toward
//      the north-east, which on a view centred there is downward on screen.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
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
// Plain-HTTP LAN: no secure context, so no AR camera and no gyro. That is the
// state the finder must render usefully in, and it is the one under test.
Object.defineProperty(win, "isSecureContext", { value: false, configurable: true });
win.WebSocket = class { close() {} addEventListener() {} send() {} };
win.Element.prototype.setPointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.releasePointerCapture = function () { /* jsdom has none */ };

// ------------------------------------------------------------- fetch double
const asked: string[] = [];
const NOW = Date.UTC(2026, 8, 10, 5, 0, 0); // frozen: every altitude below is fixed

function ok(data: unknown) {
  return { ok: true, status: 200, statusText: "OK", json: async () => data };
}

const tonightPicks = [
  // Deep south from a northern site: never above the horizon, so it can never
  // wander into the reticle and make the SWEEP assertion pass or fail by luck.
  {
    id: "n104", name: "NGC 104", type: "Globular Cluster",
    ra_hours: 6.0, dec_deg: -75, mag: 4.1, size_arcmin: 50,
    difficulty: "easy", surface_brightness: 20, difficulty_source: "heuristic",
    max_alt: -20, transit_unix: NOW / 1000 + 3600,
    best_window: null, moon_sep_deg: 80, never_rises_above_limit: true, score: 1,
  },
];

// Two bodies with SERVER-SUPPLIED alt/az, so their screen positions are exact.
const solarRows = [
  {
    id: "Jupiter",
    name: "Jupiter, 44 arcseconds across, high in the north-east.",
    type: "Planet", kind: "solar_system",
    ra_hours: 4.2, dec_deg: 21.1, alt: 40, az: 45,
  },
  {
    id: "Saturn",
    name: "Saturn, 18 arcseconds across, rings four degrees open.",
    type: "Planet", kind: "solar_system",
    ra_hours: 22.4, dec_deg: -11.2, alt: 30, az: 200,
  },
];

// A dome whose every cell reads 30 per cent: enough cloud for the wind field to
// be worth drawing, not enough for anything to count as clouded. No sample lands
// in the 6-degree bin containing alt 40 / az 45 (the azimuth grid is every 10
// degrees, and no multiple of 10 falls in [42,48)), so the locked target's own
// cloud figure comes from the hourly forecast - which is the fallback under test.
const domePayload = {
  enabled: true, observed_at: "2026-09-10T05:00:00Z", stale: false,
  alt_start: 6, alt_step: 6, az_step: 10,
  rows: Array.from({ length: 14 }, () => Array.from({ length: 36 }, () => 0.3)),
};

const visibilityNight = {
  date: "2026-09-10",
  transit_unix: NOW / 1000 + 7200,
  transit_alt: 62,
  transit_in_daylight: false,
  dark_start_unix: NOW / 1000 - 3600,
  dark_end_unix: NOW / 1000 + 5 * 3600,
  darkness_kind: "astronomical",
  samples: [],
  moon: {
    illumination: 0.2, phase_name: "Waning Crescent", alt: -20, az: 90,
    separation_deg: 80, rise_unix: null, set_unix: null,
  },
  best_window: null,
  alt_limit_deg: 20,
  never_rises_above_limit: false,
};

const g = globalThis as any;
g.fetch = async (url: any) => {
  const u = String(url);
  asked.push(u);
  if (u.includes("/api/cloudmap/dome")) return ok(domePayload);
  if (u.includes("/api/cloudmap")) return ok({ enabled: true, platform: "G18", observed_at: null, age_s: null, stale: false, last_error: null, motion: null, credit: { source: "", url: "" } });
  if (u.includes("/api/catalog/tonight")) return ok({ date: "2026-09-10", site_is_default: false, picks: tonightPicks });
  if (u.includes("/api/catalog/region")) return ok({ rows: [], truncated: false, catalog_degraded: false, notes: [] });
  // `{results}`, not `{rows}`: `/api/catalog?q=…&explain=1` answers
  // `{"results": […], "notes": […]}` (app.py's catalog handler). This fixture
  // used to send the shape the finder was WRONGLY reading, which is how a model
  // that never saw a planet went on passing a test about planets.
  if (u.includes("/api/catalog?q=planet")) return ok({ results: solarRows, notes: [] });
  if (u.includes("/api/catalog?q=moon")) return ok({ results: [], notes: [] });
  if (u.includes("/api/visibility")) return ok(visibilityNight);
  if (u.includes("/api/site")) {
    return ok({
      site: {
        name: "Back lawn", latitude: 47.61, longitude: -122.33, elevation_m: 52,
        is_default: false, horizon_min_deg: 20,
        horizon_points: [[0, 5], [180, 5], [359, 5]],
      },
      version: 3,
    });
  }
  return ok({});
};

for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "HTMLVideoElement", "Element", "SVGElement", "Node", "Event", "CustomEvent",
  "MouseEvent", "KeyboardEvent", "localStorage", "getComputedStyle", "matchMedia",
  "requestAnimationFrame", "cancelAnimationFrame", "WebSocket",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// The clock is frozen AFTER the globals are installed and BEFORE anything reads
// it, so every altitude, every LST and every "now" label below is fixed.
const realNow = Date.now;
Date.now = () => NOW;

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { useSkyModel } = await import("../model");
const { SkyView } = await import("../SkyView");
const { AIM_SETTLE_MS, LAYERS_NOTE_DEFAULT, LAYERS_NOTE_NO_WEATHER, NO_COORDS_NOTE } =
  await import("../model");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];

function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg} (expected ${String(want)}, got ${String(got)})`);
}

const settle = async () => {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 320)); });
  // ...AND past the model's settle timer, because the opening aim waits for it.
  //
  // The finder aims once the merged ranking has settled - both catalogue
  // sources answered, or AIM_SETTLE_MS gone by. The region fixture above
  // answers with NO rows, and an empty answer is indistinguishable through
  // `useSkyRegion`'s public state from one that has not landed yet, so this
  // mount takes the timer's route every time. Aiming earlier is the bug the
  // timer exists for: the ranked picks carry no alt/az and the region rows
  // carry the server's, so a finder that aimed on the first answer aimed at
  // arithmetic and then sat still while the marker moved under it.
  await act(async () => { await new Promise((r) => setTimeout(r, AIM_SETTLE_MS + 200)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
};

const locked: string[] = [];

function Harness(): any {
  const model = useSkyModel(370);
  return createElement(
    "div",
    null,
    createElement(SkyView, {
      model, boxPx: 370, frameOn: false,
      onLock: (id: string) => { locked.push(id); },
    }),
    // The layers popover and the reach strip belong to T-SKY-2; the harness
    // surfaces the two model fields they render so this test can assert on them
    // without owning a file it does not own.
    createElement("span", { "data-layers-note": "" }, model.layersNote),
    createElement("span", { "data-reach-count": "" }, String(model.reachCount)),
    createElement("span", { "data-clear-pct": "" }, String(model.clearPct)),
    createElement("span", { "data-placement-note": "" }, model.placementNote ?? ""),
    createElement("span", { "data-target-ids": "" }, model.targets.map((t) => t.id).join(",")),
    createElement("button", {
      "data-testid": "aim",
      onClick: () => model.setView({ az: 45, alt: 40, trackId: "Jupiter" }),
    }, "aim"),
  );
}

function seed(caps: string[], precise = true): void {
  useStore.setState({
    principal: { role: "operator", caps, name: "tester" },
    // `view.site_precise` STRIPS the four precise keys rather than nulling
    // them, which is exactly the shape `precise: false` reproduces here.
    site: precise
      ? {
          name: "Back lawn", latitude: 47.61, longitude: -122.33, elevation_m: 52,
          is_default: false, horizon_min_deg: 20,
        }
      : { name: "Back lawn", is_default: false, horizon_min_deg: 20 },
    equipConnected: true,
    wsPhase: "up",
    status: {
      connected: {}, looping: false,
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
      optics: {
        focal_length_mm: 800, pixel_size_um: 3.76,
        sensor_width_px: 6248, sensor_height_px: 4176,
        auto_from_camera: false, telescope_name: "",
      },
      safety: { horizon: null },
    },
    weather: {
      enabled: true,
      fetched_ts: NOW / 1000,
      stale: false,
      ignore_tonight: false,
      threshold_pct: 60,
      sustain_minutes: 30,
      site_lat: 47.61,
      site_lon: -122.33,
      forecast: {
        times: [new Date(NOW).toISOString()],
        cloud: [8], cloud_low: [8], cloud_mid: [0], cloud_high: [0],
      },
      astrospheric: null,
      alert: null,
      // FROM the south-west. The arrows must drift the sky toward the north-east.
      now: {
        ts: new Date(NOW).toISOString(),
        temp_c: 14, dewpoint_c: 6, humidity_pct: 60,
        wind_kmh: 12, wind_dir_deg: 225, gust_kmh: 18, cloud_base_m: 2200,
      },
    },
  } as never);
}

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
const q = (sel: string): any => container.querySelector(sel);
const text = (): string => container.textContent as string;
const readout = (): string => (q("[data-sky-readout]")?.textContent ?? "") as string;
const lockNote = (): string => (q("[data-sky-locknote]")?.textContent ?? "") as string;

const pointer = (el: any, type: string, clientX: number, clientY: number, buttons = 1) => {
  act(() => {
    const ev = new win.MouseEvent(type, {
      bubbles: true, cancelable: true, clientX, clientY, button: 0, buttons,
    });
    el.dispatchEvent(ev);
  });
};
/** One complete drag of `dx` px, starting at an arbitrary interior point. */
const drag = (dx: number) => {
  const box = q("[data-sky-view]");
  pointer(box, "pointerdown", 100, 100);
  pointer(box, "pointermove", 100 + dx, 100);
  pointer(box, "pointerup", 100 + dx, 100, 0);
};

// ==================================================== the operator's finder
seed(["view.status", "view.weather", "view.site_derived", "control.capture", "control.mount"]);
await act(async () => { root.render(createElement(Harness)); });
await settle();

test("precondition: the finder rendered a real box with a real bearing in it", () => {
  assert(q("[data-sky-view]") != null, "no finder box - the fixture is wrong, not the component");
  assert(
    /az \d{3}° · alt/.test(text()),
    `the az/alt readout never reached the DOM (readout was "${readout()}")`,
  );
  assert(q("[data-sky-reticle]") != null, "no reticle");
  assert(q("[data-sky-marker='Jupiter']") != null, "the seeded planet drew no marker");
});

test("precondition: the ranked and merged sources actually landed", () => {
  const ids = (q("[data-target-ids]")?.textContent ?? "").split(",").filter(Boolean);
  assert(ids.includes("Jupiter") && ids.includes("Saturn") && ids.includes("n104"),
    `all three seeded rows should be placed with real coordinates: ${ids.join("|")}`);
  eq(q("[data-reach-count]")?.textContent, "2", "both bodies should be clear and in reach");
  eq(q("[data-clear-pct]")?.textContent, "92", "8% cloud in the hourly feed is 92% clear");
});

test("aiming the finder moves the bearing and locks what is under the reticle", () => {
  act(() => { q('[data-testid="aim"]').dispatchEvent(new win.MouseEvent("click", { bubbles: true })); });
  eq(readout(), "az 045° · alt 40°", "setView did not reach the readout");
  eq(lockNote(), "LOCKED · CLEAR", "a target dead centre and clear did not lock");
});

test("the wind field drifts DOWNWIND: from 225 means toward the north-east", () => {
  const arrows = Array.from(container.querySelectorAll("[data-wind-arrow]")) as any[];
  assert(arrows.length >= 8, `too few wind arrows to be a field (${arrows.length})`);
  const rots = arrows.map((a) => Number(a.getAttribute("data-wind-arrow")));
  // The view is centred on az 45 and the wind blows toward az 45, so every sky
  // point in the box sinks toward that horizon: every rotation is downward.
  // Drop the +180 conversion and every one of these reverses.
  assert(rots.every((r) => r > 0 && r < 180), `an arrow pointed upwind: ${rots.join(", ")}`);
});

test("the lock radius is 46 px: 20 px still locks, 61 px sweeps", () => {
  drag(20);
  eq(lockNote(), "LOCKED · CLEAR", "a marker 20 px from the reticle should still be the lock");
  drag(41);
  eq(readout(), "az 035° · alt 40°", "61 px of drag is ten degrees at 6.1 px per degree");
  eq(lockNote(), "SWEEP", "a marker 61 px from the reticle must NOT be the lock");
});

test("tapping a marker reports the lock and swings the finder to it", () => {
  const before = locked.length;
  act(() => {
    q("[data-sky-marker='Jupiter']").dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
  });
  eq(locked.length, before + 1, "onLock did not fire");
  eq(locked[locked.length - 1], "Jupiter", "onLock fired with the wrong id");
  eq(readout(), "az 045° · alt 40°", "the tap did not centre the finder on the marker");
  eq(lockNote(), "LOCKED · CLEAR", "the tapped target did not become the lock");
});

test("the cloud dome is asked for exactly once per mount, at the server's own steps", () => {
  const domeCalls = asked.filter((u) => u.includes("/api/cloudmap/dome?alt_step=6&az_step=10"));
  eq(domeCalls.length, 1, `the dome was requested ${domeCalls.length} times`);
  assert(container.querySelector("[data-cloud-tile]") != null, "the dome answered but nothing was tiled");
});

test("the overlays explain themselves", () => {
  eq(q("[data-layers-note]")?.textContent, LAYERS_NOTE_DEFAULT, "the layers note is wrong");
  assert(/wind 12 km\/h → NE/.test(text()), `the wind pill did not name the drift: "${text().slice(0, 200)}"`);
});

await act(async () => { root.unmount(); });

// ============================================== the same screen for a VIEWER
asked.length = 0;
const root2 = createRoot(container);
seed(["view.status", "view.site_derived"]); // no view.weather
await act(async () => { root2.render(createElement(Harness)); });
await settle();

test("a principal without view.weather asks for no cloud and is told why", () => {
  assert(q("[data-sky-view]") != null, "precondition: the finder still renders for a viewer");
  assert(
    !asked.some((u) => u.includes("/api/cloudmap")),
    `a cloud request fired without view.weather: ${asked.filter((u) => u.includes("cloudmap")).join(", ")}`,
  );
  eq(
    q("[data-layers-note]")?.textContent,
    LAYERS_NOTE_NO_WEATHER,
    "the missing cloud layer did not say why",
  );
  assert(container.querySelector("[data-cloud-tile]") == null, "cloud was drawn without the capability");
  assert(container.querySelector("[data-wind-arrow]") == null, "wind was drawn without the capability");
});

test("the finder is still usable read-only: the sky, the markers and the bearing stay", () => {
  assert(/az \d{3}° · alt/.test(text()), "the readout vanished for a viewer");
  // SATURN, where the operator's screen aims at Jupiter, and the difference is
  // the point: without `view.weather` there is no dome, so the 30% cloud that
  // sinks Saturn on the operator's screen is not there to sink it - and
  // Saturn's 255-minute window then outranks Jupiter's 120. A viewer's ranking
  // is a function of what that viewer is allowed to see, and the finder opens
  // on the top of it either way.
  eq(readout(), "az 200° · alt 30°", "the viewer's finder did not aim at its own top target");
  assert(q("[data-sky-marker='Saturn']") != null, "the markers vanished for a viewer");
});

await act(async () => { root2.unmount(); });

// ================================ a role that cannot see where the rig IS
asked.length = 0;
const root3 = createRoot(container);
seed(["view.status", "view.weather", "view.site_derived"], false);
await act(async () => { root3.render(createElement(Harness)); });
await settle();

test("with no coordinates the finder places only what the RIG placed, and says so", () => {
  assert(q("[data-sky-view]") != null, "precondition: the finder still renders");
  eq(q("[data-placement-note]")?.textContent, NO_COORDS_NOTE, "the empty sky did not explain itself");
  // The bodies arrive with server-computed alt/az; the deep-sky pick does not,
  // and computing it from a defaulted 0,0 would draw the sky over the Gulf of
  // Guinea while every label still looked right.
  assert(q("[data-sky-marker='Jupiter']") != null, "a server-placed body should still draw");
  const ids = (q("[data-target-ids]")?.textContent ?? "").split(",").filter(Boolean);
  assert(ids.includes("Jupiter"), `the server-placed body left the list: ${ids.join("|")}`);
  assert(!ids.includes("n104"), `an unplaceable row was placed anyway: ${ids.join("|")}`);
  assert(
    !asked.some((u) => u.includes("/api/catalog/region")),
    "a region query fired around a centre that cannot be computed",
  );
});

await act(async () => { root3.unmount(); });
Date.now = realNow;

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`skyViewDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) {
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}

export default { passed, failed, total };
export { passed, failed, total };
