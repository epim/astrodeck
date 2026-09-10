// conditionsCapsDom.test.tsx - the ignore-tonight two-cap fix on WEATHER ·
// CONDITIONS, MOUNTED (D-FU-4, T-U7a-H).
//
//   Run directly:  npx tsx src/next/hubs/weather/__tests__/conditionsCapsDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by
//   `npx tsc --noEmit -p tsconfig.json`.
//
// `ConditionsScreen` is mounted DIRECTLY, NOT through `WeatherHub`: the hub
// gates its whole body on `view.weather` (`WeatherHub.tsx:56`), and the shape
// under test needs a principal that holds `control.capture` WITHOUT
// `view.weather` - a principal `WeatherHub` would never let reach this
// screen at all, so the only way to see the fix is to mount the screen on
// its own. Harness copied from `weatherDom.test.tsx:1-80` (jsdom + globals),
// trimmed to what this screen actually calls.
//
// THE GAP: `POST /api/weather/ignore-tonight` requires BOTH `control.capture`
// (the principal) and `view.weather` (a dependency - the response echoes the
// full weather payload, which carries `site_lat`/`site_lon` -
// `server/astrodeck/api/app.py:2476-2481`, reasoned at `:2462-2471`). The
// screen used to declare `control.capture` only (`ConditionsScreen.tsx:101`),
// so a `control.capture` holder without `view.weather` saw the control as
// LIVE. Both caps happen to resolve to the SAME `accessPhrase` text
// ("operator or admin access" - operator and admin are the only holders of
// either one), so no shipped role ever shows the gap: every operator and
// every admin holds both. This file uses a SYNTHETIC split-role principal
// (`control.capture`, no `view.weather`) - the only way to observe the
// second lock doing anything at all.

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
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
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

function forecast(): any {
  const times: string[] = [];
  const cloud: number[] = [];
  for (let i = 0; i < N; i++) { times.push(isoAt(NOW + i * STEP)); cloud.push(5); }
  return {
    times, cloud,
    cloud_low: cloud.map(() => 0),
    cloud_mid: cloud.map(() => 0),
    cloud_high: cloud.map(() => 0),
  };
}

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
    now: null,
    ...over,
  };
}

let ignoreReply: any = null;

// ------------------------------------------------------------- fetch recorder
interface Ask { url: string; method: string; body: any }
const asked: Ask[] = [];

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
  if (u.includes("/api/weather")) return ok(weatherFixture());
  if (u.includes("/api/site/sky")) return ok({ dark_window: null, sun_alt_deg: -30 });
  return notFound();
};

// ------------------------------------------ imports, AFTER the globals are set
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../store");
const { accessPhrase } = await import("../../../../lib/caps");
const { ConditionsScreen } = await import("../conditions/ConditionsScreen");

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
const click = (el: any) => {
  assert(el != null, "click: the element is not there - the fixture is wrong, not the component");
  act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
};

const OPERATOR = {
  role: "operator", email: "op@rig",
  caps: ["view.status", "view.preview", "view.weather", "view.site_derived",
    "control.capture", "control.guide", "control.mount"],
};
// The gap this file exists to close: control.capture WITHOUT view.weather.
// No shipped role is shaped like this (operator and admin hold both) - it is
// exactly the split the server comment at app.py:2469-2472 flags as a future
// custom/split-role risk.
const SPLIT_CAPTURE_ONLY = {
  role: "operator", email: "split@rig",
  caps: ["view.status", "control.capture"],
};

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
    sequence: null,
    plan: null,
    ...over,
  } as never);
}

// ------------------------------------------------------------------ mount
seed();
await act(async () => { root.render(createElement(ConditionsScreen)); });
await settle();

test("precondition: the conditions screen and its override switch are on screen", () => {
  assert(byId("wx-conditions") != null, "no wx-conditions - the fixture is wrong, not the component");
  assert(byId("wx-ignore") != null, "no wx-ignore switch - the fixture is wrong, not the component");
});

await testAsync(
  "positive control: an operator holding BOTH caps sees IGNORE TONIGHT live, and it posts",
  async () => {
    const btn = byId("wx-ignore");
    eq(btn.getAttribute("aria-disabled"), null,
      "an operator holding both control.capture and view.weather must not be locked:");
    const before = asked.length;
    click(btn);
    await settle();
    const posts = asked.slice(before).filter(
      (a) => a.method === "POST" && a.url.includes("/api/weather/ignore-tonight"));
    eq(posts.length, 1, "the positive control did not post exactly once:");
    eq(posts[0].body?.ignore, true, "POST body:");
  },
);

await testAsync(
  "control.capture without view.weather: IGNORE TONIGHT locks on the OTHER cap's phrase, "
  + "and pressing it fires nothing",
  async () => {
    ignoreReply = null;
    act(() => {
      useStore.setState({ principal: SPLIT_CAPTURE_ONLY, weather: weatherFixture(), toasts: [] } as never);
    });
    await settle();

    const btn = byId("wx-ignore");
    eq(btn.getAttribute("aria-disabled"), "true",
      "a control.capture-only principal must see IGNORE TONIGHT locked, not live:");
    const reason = String(btn.getAttribute("title") ?? "");
    eq(reason, `needs ${accessPhrase("view.weather")}`, "the lock reason must be view.weather's, not silence:");
    assert(/needs operator or admin access/.test(reason), `reason text: "${reason}"`);

    const before = asked.length;
    click(btn);
    await settle();
    const posts = asked.slice(before).filter(
      (a) => a.method === "POST" && a.url.includes("/api/weather/ignore-tonight"));
    eq(posts.length, 0,
      `a control.capture-only principal fired the POST: ${posts.map((p) => p.url).join(", ")}`);
  },
);

act(() => { root.unmount(); });

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`conditionsCapsDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
