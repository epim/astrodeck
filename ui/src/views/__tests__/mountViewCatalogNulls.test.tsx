// mountViewCatalogNulls.test.tsx — the NGC 604 crash.
//
// GET /api/catalog?q=604 returns rows whose `mag` is null (NGC 604 and IC 1604
// have no published magnitude) and, for a caller without view.site_derived, no
// `alt`/`az` at all. MountView's catalog table used to call `r.mag.toFixed(1)`
// and `r.alt.toFixed(0)` unguarded, which threw during render and took down the
// WHOLE Mount view behind the "Couldn't load this screen" chunk-failure pane —
// a render bug reported to the user as a WiFi drop. This file mounts the real
// view against exactly that payload and asserts it renders instead of throwing,
// with the app's own "—" placeholder standing in for the missing numbers
// (lib/catalogFormat.ts).
//
// Run directly:  npx tsx src/views/__tests__/mountViewCatalogNulls.test.tsx

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
// Same ordering constraint as mountViewDom.test.tsx: installed before the
// store, api client or view are imported.
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
win.Element.prototype.setPointerCapture = function () {};
win.Element.prototype.releasePointerCapture = function () {};

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "localStorage", "requestAnimationFrame",
  "cancelAnimationFrame", "getComputedStyle", "matchMedia", "WebSocket",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------- the fake rig
// The row that crashed the view: no magnitude, and — like a viewer-role
// caller — no alt/az on the row at all.
const NULL_MAG_ROW = {
  id: "NGC604", name: "NGC 604", type: "nebula",
  ra_hours: 1.596, dec_deg: 30.79, size_arcmin: 1.5,
  mag: null, surface_brightness: null, difficulty: "unknown",
};
g.fetch = async (url: string) => {
  const path = String(url);
  const json =
    path.includes("/api/catalog") ? [NULL_MAG_ROW]
      : path.includes("/api/sequence/preflight")
        ? { alt: 55, az: 175, verdict: "ok", horizon_min_deg: 10, site_is_default: false }
        : { started: "ok" };
  return { ok: true, status: 200, statusText: "OK", json: async () => json } as any;
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../store");
const MountView = (await import("../MountView")).default;

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

useStore.setState({
  status: {
    connected: {}, looping: false, mode: "sim", busy_lanes: [],
    mount: {
      ra_hours: 5.6, dec_deg: -5.4, ra_str: "05h35m", dec_str: "-05°23'",
      alt: 45, az: 120, tracking: true, parked: false, slewing: false,
      can_find_home: true, can_set_tracking_rate: true, tracking_rate: "sidereal",
    },
  },
  principal: { role: "operator", email: null, caps: ["view.status", "control.mount"] },
} as never);

const container = win.document.getElementById("root") as any;
const root = createRoot(container);

// The render itself is the assertion: a thrown error inside MountView would
// propagate out of this act() call (there is no ViewBoundary wrapping it
// here, deliberately — this test is about the view, not the boundary that
// used to hide its crash).
let renderError: Error | null = null;
try {
  await act(async () => { root.render(createElement(MountView)); });
} catch (e) {
  renderError = e as Error;
}

test("a null-mag, alt-less catalog row does not crash the Mount view", () => {
  assert(renderError === null, `MountView threw during render: ${renderError?.message}`);
});

// Let the debounced /api/catalog fetch resolve and React flush the result.
await act(async () => { await new Promise((r) => setTimeout(r, 400)); });

test("the row renders with the app's own placeholder for the missing numbers", () => {
  const text = container.textContent || "";
  assert(/NGC 604/.test(text), "the NGC 604 row never rendered at all");
  // "mag —" (narrow layout) and a bare "—" in the Mag column (wide layout) —
  // either is proof the null was handled rather than thrown on.
  assert(/mag —/.test(text) || /—/.test(text),
    "no placeholder for the missing magnitude/altitude anywhere in the row");
});

test("no lingering DOM node still carries a literal 'null' or 'NaN'", () => {
  // The regression this guards against isn't only a throw: `${null}` or
  // `NaN.toFixed` rendered as text is the same lie in a quieter shape.
  const text = container.textContent || "";
  assert(!/\bnull\b/.test(text), `literal "null" leaked into the row: ${text}`);
  assert(!/NaN/.test(text), `NaN leaked into the row: ${text}`);
});

await act(async () => { root.unmount(); });

// ------------------------------------------------------------------- report
const total = passed + failed;
console.log(`mountViewCatalogNulls.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
