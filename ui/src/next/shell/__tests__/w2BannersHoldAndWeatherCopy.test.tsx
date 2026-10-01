// w2BannersHoldAndWeatherCopy.test.tsx - WP-17 (b) and (c) on the #/next
// cross-hub banner strip.
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/next/shell/__tests__/w2BannersHoldAndWeatherCopy.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// (b) #258: the armed-and-waiting banner's `holding: <reason>` dropped
//     ResumeArm's `hold.site_detail` - the numbers behind a words-only
//     reason (a target's altitude, its start floor, the wait until it rises).
// (c) #260: WEATHER_OVERRIDE_TEXT claimed the weather override also turns off
//     the IN-RUN cloud hold ("the in-run cloud hold is off as well as the
//     forecast"). The override only disarms `WeatherService.veto_reason`'s
//     forecast-rain check (weather.py: "RAIN VETOES. CLOUD DOES NOT.") - a
//     running cloud hold is decided from the rig's own frames and never reads
//     this flag. Corrected to the same sentence `now/NowBanners.tsx`'s
//     WEATHER_OVERRIDE already carries, so the two surfaces agree.
//
// NAMED MUTANTS, each run from a byte copy of Banners.tsx and restored
// byte-identical afterwards (sha256 checked). The observed failure is quoted
// at the test it turns red.
//   B1 "show reason alone"        the site_detail branch removed from `hold`
//   B2 "restore the false claim"  WEATHER_OVERRIDE_TEXT reverted to the old
//                                 "in-run cloud hold is off" sentence

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------------ css stub
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

win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} addEventListener() {} removeEventListener() {} send() {} };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "localStorage", "sessionStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "requestAnimationFrame", "cancelAnimationFrame", "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// `useSetupFacts()` reads `/api/profiles`; everything else is store-seeded.
g.fetch = async (url: any) => {
  const u = String(url);
  if (u === "/api/profiles") {
    return {
      ok: true, status: 200, statusText: "OK",
      headers: { get: () => "application/json" },
      json: async () => [{ id: "p1", name: "the rig" }], text: async () => "[]",
    };
  }
  return {
    ok: false, status: 404, statusText: "Not Found",
    headers: { get: () => "application/json" },
    json: async () => ({}), text: async () => "",
  };
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../store");
const { Banners } = await import("../Banners");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function testAsync(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: unknown, msg: string): void { if (!cond) throw new Error(msg); }

const ROUTE = (hub: string, sub: string) => ({ hub, sub, sheets: [], params: {} }) as any;
const q = (sel: string) => document.getElementById("root")!.querySelector(sel) as any;
const byId = (id: string) => q(`[data-testid="${id}"]`);
const settle = async () => {
  for (let i = 0; i < 4; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};

document.body.innerHTML = `<div id="root"></div>`;
const container = document.getElementById("root")!;
const root = createRoot(container);

// A fully-done rig (so the first-time-setup banner never competes for one of
// the two slots) with nothing else newsworthy, so the armed/weather banners
// under test are deterministically the ones shown.
function seedBase(): void {
  act(() => { useStore.setState({
    wsPhase: "up", telemetryStale: false, equipConnected: true,
    principal: { role: "admin", email: "a@b.c", caps: ["view.status"] } as any,
    authMethods: { methods: [], first_run: false } as any,
    previews: [{ id: "f1" }] as any,
    // A made-up site - not the real one (project rule).
    site: { latitude: 10, longitude: 20, is_default: false, name: "test site" } as any,
    config: {
      version: 1,
      site: { latitude: 10, longitude: 20, is_default: false, name: "test site" },
      optics_computed: { have_optics: true, fov_w_deg: 1.2, fov_h_deg: 0.8 },
      active_profile_id: "p1",
    } as any,
    status: {
      connected: { camera: { connected: true, name: "sim" } },
      camera: { temperature: -10, can_cool: false },
      mode: "sim",
    } as any,
    safety: null, weather: null, runBanner: null, armedBannerDismissed: null,
    sequence: { state: "idle" } as any,
  } as never); });
}

const ARMED = {
  id: "s1", name: "NGC 7000 mosaic", owed: 12, accepted: 48, total: 60,
  origin: "flow", origin_id: "f1",
};
const STALE_HOLD = {
  reason: "NGC 7000 is below its start floor; not slewing yet",
  since: 1_700_000_000, retry_at: 1_700_000_600,
  session_id: "s1", session_name: "NGC 7000 mosaic", owed: 12,
};
// A made-up target and made-up numbers - not the real site.
const SITE_DETAIL =
  "NGC 7000 is at 9 deg, below its 15 deg start floor (it reaches 15 deg in about 3 h)";

async function render(): Promise<void> {
  await act(async () => {
    root.render(createElement(Banners as any, { route: ROUTE("sky", ""), nowMs: Date.now() }));
  });
  await settle();
}

// ======================================================= (b) #258 site_detail

await testAsync("control: an armed hold with no site_detail shows the reason alone", async () => {
  seedBase();
  act(() => {
    useStore.setState({ resumeArm: { armed: ARMED, hold: STALE_HOLD, recovering: false, recovery: null } } as never);
  });
  await render();
  const banner = byId("banner-armed");
  assert(banner != null, "no armed banner - the fixture is wrong, not the component");
  const text = String(banner.textContent);
  assert(text.includes(`holding: ${STALE_HOLD.reason}`), `the plain hold changed shape: "${text}"`);
  assert(!text.includes(SITE_DETAIL), "a site_detail string appeared with no field on the wire");
});

await testAsync("site_detail is shown beside the reason, for an entitled principal (#258)", async () => {
  // B1 "show reason alone", observed:
  //   x site_detail is shown beside the reason, for an entitled principal
  //   (#258): site_detail is missing from the armed banner even though the
  //   server sent it
  seedBase();
  act(() => {
    useStore.setState({
      resumeArm: { armed: ARMED, hold: { ...STALE_HOLD, site_detail: SITE_DETAIL }, recovering: false, recovery: null },
    } as never);
  });
  await render();
  const text = String(byId("banner-armed")?.textContent ?? "");
  assert(text.includes(`holding: ${STALE_HOLD.reason} - ${SITE_DETAIL}`),
    `site_detail is missing from the armed banner even though the server sent it: "${text}"`);
});

// ===================================================== (c) #260 weather copy

await testAsync("the weather-override banner says what the override actually does, not that "
  + "it turns off the in-run cloud hold (#260)", async () => {
  // B2 "restore the false claim", observed:
  //   x the weather-override banner says what the override actually does, not
  //   that it turns off the in-run cloud hold (#260): the false claim is
  //   back: "Weather override on for tonight: the in-run cloud hold is off as
  //   well as the forecast."
  seedBase();
  act(() => {
    useStore.setState({
      resumeArm: { armed: ARMED, hold: null, recovering: false, recovery: null },
      weather: { enabled: true, alert: true, ignore_tonight: true } as any,
    } as never);
  });
  await render();
  const banner = byId("banner-weather-veto");
  assert(banner != null, "no weather-override banner - the fixture is wrong, not the component");
  const text = String(banner.textContent);
  assert(!/in-run cloud hold is off/i.test(text), `the false claim is back: "${text}"`);
  assert(/forecast rain will not hold auto-resume until the next dusk/i.test(text),
    `the corrected sentence is not on the banner: "${text}"`);
  assert(/cloud forecasts never do/i.test(text), `the corrected sentence lost its parenthetical: "${text}"`);
});

act(() => { root.unmount(); });

// ------------------------------------------------------------------- summary
const total = passed + failed;
console.log(`w2BannersHoldAndWeatherCopy: ${passed}/${total} passed`);
for (const f of failures) console.error(f);
export const result = { passed, failed, total };
