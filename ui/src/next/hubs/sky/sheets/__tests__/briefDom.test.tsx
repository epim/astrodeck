// briefDom.test.tsx - the object brief's TONIGHT block, on a night that has no
// dark window and a moon that is too close.
//
//   Run directly:  npx tsx src/next/hubs/sky/sheets/__tests__/briefDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc --noEmit`.
//
// TWO STRINGS, BOTH OF THEM AN ABSENCE OF INFORMATION WHERE THERE SHOULD BE
// ONE, and neither is visible from a unit test of the helper behind it:
//
//  1. "Above 20 degrees" is `fmtHoursAboveLimit`, which measures across the
//     DARK WINDOW. With no astronomical darkness there is nothing to measure
//     and it returns a bare em-dash - a character this UI's copy rules forbid
//     outright, and one that reads as "no data" rather than as the real and
//     seasonal answer it is. High-latitude summers are not an edge case for the
//     people who have them; they are every night for a third of the year.
//  2. The moon separation was a glyph (a cross, a warning triangle, a
//     crescent). Two of the three are generic severity marks that say nothing
//     about the moon, and none of them survives being read aloud.
//
// The legacy `lib/visibility.ts` still returns both, and is deliberately NOT
// edited: `#/classic` renders it. The fix is at these call sites.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/#/sky/brief?id=m31", pretendToBeVisual: true },
);
const win = dom.window as any;

win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} addEventListener() {} send() {} };
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "SVGElement", "Node", "Event", "CustomEvent", "MouseEvent",
  "KeyboardEvent", "PointerEvent", "localStorage", "sessionStorage",
  "getComputedStyle", "matchMedia", "WebSocket", "ResizeObserver",
  "requestAnimationFrame", "cancelAnimationFrame", "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const NOW = Date.UTC(2026, 5, 21, 6, 0, 0);

const ROW = {
  id: "m31", name: "Andromeda Galaxy", type: "Galaxy", kind: "dso",
  ra_hours: 0.712305, dec_deg: 41.26917, mag: 3.4, size_arcmin: 190,
  alt: 44, az: 70, difficulty: "easy",
};

/** Midsummer at a high latitude: the sun never gets far enough down, so
 *  `dark_start_unix` / `dark_end_unix` are BOTH null. `samples` is a real
 *  curve; there is simply no dark window to measure any of it inside. */
const NO_DARK_NIGHT = {
  date: "2026-06-21",
  transit_unix: NOW / 1000 + 5400, transit_alt: 52, transit_in_daylight: false,
  dark_start_unix: null, dark_end_unix: null,
  darkness_kind: "none",
  samples: Array.from({ length: 12 }, (_, i) => ({
    t_unix: NOW / 1000 + i * 600, alt: 40 + i, moon_alt: 8, sun_alt: -9,
  })),
  moon: {
    illumination: 0.88, phase_name: "Waxing Gibbous", alt: 20, az: 120,
    // Under 15 degrees: the tier the old cross stood for.
    separation_deg: 11.6, rise_unix: null, set_unix: NOW / 1000 + 7200,
  },
  best_window: null, alt_limit_deg: 20, never_rises_above_limit: false,
};

const asked: string[] = [];
const ok = (data: any) => ({ ok: true, status: 200, statusText: "OK", json: async () => data });

g.fetch = async (url: any, init: any) => {
  const u = String(url);
  asked.push(`${init?.method ?? "GET"} ${u}`);
  if (u.includes("/api/catalog/region")) return ok({ rows: [], truncated: false, notes: [] });
  if (u.includes("/api/catalog?q=")) return ok({ results: [ROW], notes: [] });
  if (u.includes("/api/visibility")) return ok(NO_DARK_NIGHT);
  return ok({});
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { BriefSheet } = await import("../brief");
// The legacy helper, so the assertion below grades the character the SCREEN
// would have shown rather than one this file typed out.
const { fmtHoursAboveLimit } = await import("../../../../../lib/visibility");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

const settle = async () => {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 400)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
};

const container = win.document.getElementById("root") as any;
const byId = (id: string) => container.querySelector(`[data-testid="${id}"]`) as any;
const text = (): string => String(container.textContent ?? "");

useStore.setState({
  principal: {
    role: "operator", name: "tester",
    caps: ["view.status", "view.site_derived", "view.site_precise", "control.capture"],
  },
  equipConnected: true,
  wsPhase: "up",
  toasts: [],
  framing: null,
  site: { name: "Nordkapp", latitude: 71.1, longitude: 25.8, elevation_m: 30, is_default: false, horizon_min_deg: 20 },
  config: null,
  status: null,
} as never);

const root = createRoot(container);
await act(async () => {
  root.render(createElement(BriefSheet, { params: { id: "m31" }, depth: 0 as const }));
});
await settle();

test("precondition: the brief is on screen, about this object, with tonight's block", () => {
  assert(byId("sky-brief") != null, "no brief sheet - the fixture is wrong, not the component");
  assert(/Andromeda Galaxy/.test(text()), "the brief is not about the object that was asked for");
  assert(/TONIGHT/.test(text()), "the tonight block never rendered, so neither row below exists");
  assert(/Above 20/.test(text()), "the alt-limit row is missing, so its value cannot be graded");
});

test("a night with no dark window says so, instead of an em-dash", () => {
  const body = text();
  // The character the helper returns for this night, taken from the helper so
  // the assertion cannot drift from it.
  const placeholder = fmtHoursAboveLimit(NO_DARK_NIGHT as any);
  assert(placeholder === "—",
    `precondition: the helper no longer returns the em-dash this guards (got "${placeholder}")`);
  assert(!body.includes("—"),
    "an em-dash reached the screen - the house rule is hyphens, and this one is also the "
    + "only thing the row says about a whole season of nights");
  assert(/no dark window tonight/.test(body),
    `the row does not say why there are no hours to report: "${body.slice(0, 400)}"`);
});

test("the moon separation is a word beside the angle, not a severity glyph", () => {
  const body = text();
  assert(/12° - very close to the moon/.test(body),
    `the separation row does not carry its verdict in words: "${body}"`);
  for (const glyph of ["✕", "⚠", "☾"]) {
    assert(!body.includes(glyph), `the glyph ${glyph} is still on the brief`);
  }
});

await act(async () => { root.unmount(); });

const total = passed + failed;
console.log(`briefDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
