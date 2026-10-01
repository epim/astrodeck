// w2AppHoldSiteDetail.test.tsx - WP-17 (b) on the classic root's RUN ARMED
// banner (#258).
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/__tests__/w2AppHoldSiteDetail.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// `GET /api/sequence/resume-arm`'s `hold.site_detail` carries the numbers
// behind a words-only `hold.reason` (#233) - a target's altitude, its start
// floor, the wait until it rises, or a slew-limit gate's azimuth. The classic
// header's armed banner (App.tsx) rendered `hold.reason` alone, so an operator
// entitled to the numbers (the server already withholds the key from a viewer
// via `_redact_resume_arm_for`) never saw them.
//
// NAMED MUTANT, run from a byte copy of App.tsx and restored byte-identical
// afterwards (sha256 checked). The observed failure is quoted at the test.
//   A1 "show reason alone"   the site_detail branch removed from the banner

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
win.WebSocket = class {
  static OPEN = 1;
  readyState = 0;
  close() {}
  send() {}
  addEventListener() {}
  removeEventListener() {}
};
win.Element.prototype.setPointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.releasePointerCapture = function () { /* jsdom has none */ };
win.HTMLElement.prototype.scrollIntoView = function () { /* jsdom has none */ };
win.HTMLCanvasElement.prototype.getContext = function () { return null; };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "localStorage", "sessionStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "requestAnimationFrame", "cancelAnimationFrame", "location", "history",
  "ResizeObserver", "IntersectionObserver",
]) {
  const v = k === "window" ? win
    : k === "ResizeObserver" || k === "IntersectionObserver"
      ? class { observe() {} unobserve() {} disconnect() {} }
      : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// Every GET answers 404 unless seeded - none of this test's loaders are under
// test, so the seeded store value is what the banner shows, not a race.
g.fetch = async () => ({
  ok: false, status: 404, statusText: "Not Found",
  headers: { get: () => "application/json" },
  json: async () => ({}), text: async () => "",
});

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../store");
const App = (await import("../App")).default;

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: unknown, msg: string): void { if (!cond) throw new Error(msg); }

async function flush(): Promise<void> {
  for (let i = 0; i < 4; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
}
async function seed(state: Record<string, unknown>): Promise<void> {
  await act(async () => { useStore.setState(state as never); });
  await flush();
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
// A made-up target and made-up numbers - not the real site (project rule).
const SITE_DETAIL =
  "NGC 7000 is at 9 deg, below its 15 deg start floor (it reaches 15 deg in about 3 h)";

win.location.hash = "#/classic";
await seed({
  authMethods: { methods: [], first_run: false }, // open LAN: never a login gate
  principal: { role: "admin", email: null, caps: ["view.status", "control.sequence"] },
  wsPhase: "up", telemetryStale: false, equipConnected: false, view: "connect",
  weather: null, runBanner: null, armedBannerDismissed: null,
  sequence: { state: "idle" },
  status: { connected: {}, looping: false, mode: "sim", busy_lanes: [] },
  resumeArm: { armed: ARMED, hold: STALE_HOLD, recovering: false, recovery: null },
});

const box = document.createElement("div");
document.body.appendChild(box);
const root = createRoot(box);
await act(async () => { root.render(createElement(App)); });
await flush();

const text = () => (box.textContent || "") as string;

await test("precondition: the RUN ARMED banner is up and names the hold", () => {
  assert(/RUN ARMED/.test(text()), `no RUN ARMED banner: ${text().slice(0, 200)}`);
  assert(text().includes(`holding: ${STALE_HOLD.reason}`),
    `the banner does not say the hold at all: ${text().slice(0, 300)}`);
});

await test("control: with no site_detail on the wire, nothing extra prints", () => {
  assert(!text().includes(SITE_DETAIL), "a site_detail string appeared with no field on the wire");
});

await test("site_detail is shown beside the reason, for an entitled principal (#258)", async () => {
  // A1 "show reason alone", observed:
  //   x site_detail is shown beside the reason, for an entitled principal
  //   (#258): site_detail is missing from the banner even though the server
  //   sent it
  await seed({
    resumeArm: { armed: ARMED, hold: { ...STALE_HOLD, site_detail: SITE_DETAIL }, recovering: false, recovery: null },
  });
  assert(text().includes(`holding: ${STALE_HOLD.reason} - ${SITE_DETAIL}`),
    `site_detail is missing from the banner even though the server sent it: ${text().slice(0, 400)}`);
});

act(() => { root.unmount(); });
box.remove();

// ------------------------------------------------------------------ summary
const total = passed + failed;
console.log(`w2AppHoldSiteDetail: ${passed}/${total} passed`);
for (const f of failures) console.error(f);
export const result = { passed, failed, total };
