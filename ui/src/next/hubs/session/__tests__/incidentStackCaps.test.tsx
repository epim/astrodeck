// incidentStackCaps.test.tsx - the ignore-weather two-cap fix on SESSION / NOW's
// incident stack (D-FU-4-follow-up, T-U7a-H's `capLockReason`).
//
//   Run directly:  npx tsx src/next/hubs/session/__tests__/incidentStackCaps.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by
//   `npx tsc --noEmit -p tsconfig.json`.
//
// `IncidentStack` is mounted DIRECTLY, NOT through `NowScreen`: it needs only
// the store slices `useNowIncidents` and its own hooks read, and mounting the
// whole screen would pull in `RunHeader`/`LiveStack`/`RunControls` fixtures
// that have nothing to do with this gap. Harness copied from
// `conditionsCapsDom.test.tsx` (jsdom + globals), trimmed to this component.
//
// THE GAP `IncidentStack.tsx`'s own comment (above `capLockReason` in
// `incidentActions.ts`) names: `lockedFor` used to resolve every incident
// action's lock from `s.cap` ALONE, so `ignore_weather` - which the table
// declares needs BOTH `control.capture` AND `view.weather`
// (`incidentActions.ts` `INCIDENT_ACTIONS.ignore_weather`, server reasoning at
// `server/astrodeck/api/app.py:2462-2481`) - unlocked for a principal holding
// `control.capture` without `view.weather`. Both caps resolve to the SAME
// `accessPhrase` text ("operator or admin access"), so no shipped role is
// shaped like the gap (operator and admin hold both); this file uses a
// SYNTHETIC split-role principal, the only way to observe `cap2` doing
// anything at all.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/#/session/now", pretendToBeVisual: true },
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
    return ok({ ok: true });
  }
  return notFound();
};

// ------------------------------------------ imports, AFTER the globals are set
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../store");
const { accessPhrase } = await import("../../../../lib/caps");
const { IncidentStack } = await import("../now/IncidentStack");

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
const ignoreBtn = () =>
  container.querySelector('[data-testid="incident-cloud"] [data-action="ignore_weather"]') as any;
const click = (el: any) => {
  assert(el != null, "click: the element is not there - the fixture is wrong, not the component");
  act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
};

const OPERATOR = {
  role: "operator", email: "op@rig",
  caps: ["view.status", "view.preview", "view.weather", "view.site_derived",
    "control.capture", "control.guide", "control.mount"],
};
// The gap this file exists to close: control.capture WITHOUT view.weather. No
// shipped role is shaped this way (operator and admin hold both) - it is
// exactly the split the server comment at app.py:2469-2472 flags as a future
// custom/split-role risk, and the same synthetic shape
// `conditionsCapsDom.test.tsx` uses for the WEATHER hub's own copy of this gap.
const SPLIT_CAPTURE_ONLY = {
  role: "operator", email: "split@rig",
  caps: ["view.status", "control.capture"],
};

// A cloud hold, and nothing else active: `sky.holding: true` is on its own
// sufficient for `deriveIncidents` to raise the CLOUD card
// (`next/lib/incidents.ts` `cloudIncident`), and every other kind's trigger
// (`safety`, `wsPhase`, `mountOp`, `focus`, `guide`, `disk`, `cooler`,
// `lastCaptureAtMs` vs a running state) is left at the store's cold-boot
// default, which is inert for every one of them - so CLOUD is incident[0],
// the one `IncidentStack` renders un-expanded.
const HOLDING_SEQUENCE = {
  state: "holding",
  detail: "held for cloud - waiting for clear sky",
  sky: {
    cloudy: true, age_s: 30, score: 80,
    reason: "cloud score over threshold", text: "cloudy, building", holding: true,
  },
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
    sequence: HOLDING_SEQUENCE,
    ...over,
  } as never);
}

// ------------------------------------------------------------------ mount
seed();
await act(async () => { root.render(createElement(IncidentStack)); });
await settle();

test("precondition: the cloud incident and its ignore-weather action are on screen", () => {
  assert(container.querySelector('[data-testid="now-incidents"]') != null,
    "no now-incidents - the fixture is wrong, not the component");
  assert(container.querySelector('[data-testid="incident-cloud"]') != null,
    "no incident-cloud card - the fixture never raised the cloud hold");
  assert(ignoreBtn() != null, "no ignore_weather action rendered on the cloud card");
});

await testAsync(
  "positive control: an operator holding BOTH caps sees IGNORE WEATHER live, and it posts",
  async () => {
    const btn = ignoreBtn();
    eq(btn.getAttribute("aria-disabled"), null,
      "an operator holding both control.capture and view.weather must not be locked:");
    const before = asked.length;
    click(btn);
    await settle();
    const posts = asked.slice(before).filter(
      (a) => a.method === "POST" && a.url.includes("/api/weather/ignore-tonight"));
    eq(posts.length, 1, "the positive control did not post exactly once:");
  },
);

await testAsync(
  "control.capture without view.weather: IGNORE WEATHER TONIGHT locks on the OTHER cap's "
  + "phrase, and pressing it fires nothing",
  async () => {
    act(() => { useStore.setState({ principal: SPLIT_CAPTURE_ONLY, toasts: [] } as never); });
    await settle();

    const btn = ignoreBtn();
    eq(btn.getAttribute("aria-disabled"), "true",
      "a control.capture-only principal must see IGNORE WEATHER TONIGHT locked, not live:");
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
console.log(`incidentStackCaps.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) {
  (globalThis as unknown as { process?: { exitCode?: number } }).process!.exitCode = 1;
}
export default { passed, failed, total };
export { passed, failed, total };
