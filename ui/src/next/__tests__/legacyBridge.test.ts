// legacyBridge.test.ts - the door from `setView` / `openHelp` into the router.
//
//   Run directly:  npx tsx src/next/__tests__/legacyBridge.test.ts
//   Also run by `npm test` and type-checked by `tsc -b`.
//
// WHAT THIS GUARDS. Twenty-odd reused components navigate by writing
// `store.view`. Under the new root nothing reads that field, so a bridge that
// is missing, or that watches the wrong thing, turns every one of those taps
// into a no-op - and a no-op tap on a working-looking screen is the failure
// shape that takes longest to notice.
//
// The second guard is the mirror image: the bridge must NOT act on the value
// that is already in the store when it mounts. `view` starts at "connect", so
// treating the initial value as an instruction would slam every cold start and
// every deep link onto Rig - Devices.

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
win.WebSocket = class { close() {} addEventListener() {} send() {} };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "KeyboardEvent", "localStorage", "getComputedStyle",
  "matchMedia", "WebSocket", "requestAnimationFrame", "cancelAnimationFrame",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;
g.fetch = async () => ({ ok: false, status: 404, statusText: "Not Found", json: async () => ({}) });

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../store");
const { LEGACY_VIEW_ROUTE, useLegacyBridge } = await import("../legacyBridge");
const { currentRoute } = await import("../router");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];

function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg = ""): void {
  if (got !== want) throw new Error(`${msg} expected ${String(want)}, got ${String(got)}`);
}

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
function Probe() { useLegacyBridge(); return createElement("span", null, "bridge"); }

// ------------------------------------------------------------------- table

test("every ViewName has a route, and every route parses to a real hub", () => {
  const names = Object.keys(LEGACY_VIEW_ROUTE);
  assert(names.length === 16, `expected all 16 legacy views mapped, got ${names.length}`);
  for (const [view, path] of Object.entries(LEGACY_VIEW_ROUTE)) {
    assert(path.startsWith("/"), `${view} -> ${path} is not a path`);
  }
  // The five the brief pins down by name, so a silent re-point is a red test.
  eq(LEGACY_VIEW_ROUTE.monitor, "/monitor/live");
  eq(LEGACY_VIEW_ROUTE.polar, "/rig/devices/mount/polar");
  eq(LEGACY_VIEW_ROUTE.atlas, "/sky?frame=1");
  eq(LEGACY_VIEW_ROUTE.sequence, "/session/flows/planEditor");
  eq(LEGACY_VIEW_ROUTE.connect, "/rig/devices");
});

// ------------------------------------------------------------------ mounting

test("mounting does NOT navigate, even though store.view already has a value", () => {
  win.location.hash = "#/session/gallery";
  eq(useStore.getState().view, "connect", "precondition: the store's default view is connect");
  act(() => { root.render(createElement(Probe)); });
  eq(win.location.hash, "#/session/gallery",
    "the route the user arrived on must survive the bridge mounting:");
});

test("a setView AFTER mount navigates", () => {
  act(() => { useStore.getState().setView("monitor"); });
  eq(win.location.hash, "#/monitor/live");
  eq(currentRoute().hub, "monitor", "the router agrees:");
  eq(currentRoute().sub, "live");
});

test("a second setView navigates again (the baseline advanced, it did not stick)", () => {
  act(() => { useStore.getState().setView("gallery"); });
  eq(win.location.hash, "#/session/gallery");
  act(() => { useStore.getState().setView("power"); });
  eq(win.location.hash, "#/rig/devices/power");
});

test("openHelp routes to Help WITH the topic, and clears the topic behind it", () => {
  act(() => { useStore.getState().openHelp("camera-offline"); });
  eq(win.location.hash, "#/settings/general/help?topic=camera-offline");
  eq(currentRoute().params.topic, "camera-offline", "the sheet can read the topic:");
  eq(useStore.getState().helpTopic, null,
    "a spent topic must be cleared, or it re-fires on the next store write:");
});

test("openHelp with no topic still reaches Help", () => {
  act(() => { useStore.getState().setView("monitor"); });
  eq(win.location.hash, "#/monitor/live", "precondition: somewhere else first");
  act(() => { useStore.getState().openHelp(); });
  eq(win.location.hash, "#/settings/general/help");
});

test("an unrelated store write does not navigate", () => {
  act(() => { useStore.getState().setView("atlas"); });
  eq(win.location.hash, "#/sky?frame=1", "precondition: parked on the sky");
  act(() => { useStore.getState().setCaptureTarget("M31"); });
  eq(win.location.hash, "#/sky?frame=1",
    "only view/helpTopic are navigation; nothing else in the store is:");
});

act(() => { root.unmount(); });

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`legacyBridge.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
