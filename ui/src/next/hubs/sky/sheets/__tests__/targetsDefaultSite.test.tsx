// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// targetsDefaultSite.test.tsx - the suggested-targets sheet counts nothing at a
// DEFAULT site's placeholder (#503's class, in the sheet), MOUNTED.
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/next/hubs/sky/sheets/__tests__/targetsDefaultSite.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT. `useTargetsModel` ranks the sheet's own list, and it took the
// site block's coordinates whenever they were numbers - `?? 0` for absent
// ones, and a default site's placeholder 0 N 0 E as they stood. The server
// withholds its alt/az from a default site (`/api/catalog`, #24), so the
// planets arrive unplaced, and the sheet placed them at the placeholder itself:
// Jupiter, up over the Gulf of Guinea at this hour, was counted in the sheet's
// "N SUGGESTED TARGETS" title - the count the status row's pill opens it with -
// while tonight's list itself was refused for having no site. Found while
// fixing the finder's copy of the same rule (#503).
//
// WHAT IS HELD: on a default site the title counts 0 and the lens dial's
// planet seat counts 0. THE CONTROL: the same coordinates SAVED count Jupiter,
// so the sheet can be seen counting a body it placed. And for a role that is
// ranked but not given the site's coordinates, the body the SERVER placed is
// listed with its altitude and no window, where the sheet used to walk one at
// the `?? 0` placeholder (#508's class, in the rows).
//
// MUTATION RECORD: see the block at the foot of this file.

/* eslint-disable @typescript-eslint/no-explicit-any */

{
  const { registerHooks } = await import("node:module");
  registerHooks?.({
    load(url: string, context: any, nextLoad: any) {
      if (url.endsWith(".css")) {
        return { format: "module", shortCircuit: true, source: "export default {};" };
      }
      return nextLoad(url, context);
    },
  } as any);
}

const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/#/sky/targets", pretendToBeVisual: true },
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
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "PointerEvent", "localStorage", "sessionStorage", "getComputedStyle",
  "matchMedia", "WebSocket", "ResizeObserver",
  "requestAnimationFrame", "cancelAnimationFrame",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

/** Jupiter is 60 degrees up at the placeholder at this hour (computed below). */
const NOW = Date.UTC(2026, 8, 10, 9, 0, 0);
const realNow = Date.now;
Date.now = () => NOW;

const NO_SITE_MSG =
  "no observing site is saved, so tonight's windows cannot be computed - save the site in Settings";
const DEFAULT_SITE = { name: "", latitude: 0, longitude: 0, elevation_m: 0, is_default: true, horizon_min_deg: 15 };
const SAVED_SITE = { name: "Equator test", latitude: 0, longitude: 0, elevation_m: 0, is_default: false, horizon_min_deg: 15 };

/** Jupiter as `/api/catalog` sends it to an unsited rig: no alt/az. */
const JUPITER = {
  id: "Jupiter", name: "Jupiter, the largest planet.", type: "Planet", kind: "solar_system",
  ra_hours: 7.1, dec_deg: 22.5, mag: -2.2, size_arcmin: 0.7,
};

/** What `view.site_precise` leaves a role without it: coordinates STRIPPED. */
const STRIPPED_SITE = { is_default: false, horizon_min_deg: 15 };
/** The server places Jupiter itself for a ranked role on a saved site. */
let jupiterAltAz: { alt: number; az: number } | null = null;

let siteNow: any = DEFAULT_SITE;
function res(status: number, data: unknown) {
  return { ok: status < 400, status, statusText: status < 400 ? "OK" : "Conflict", json: async () => data };
}
g.fetch = async (url: any) => {
  const u = String(url);
  const saved = siteNow.is_default !== true;
  if (u.includes("/api/catalog/tonight")) {
    return saved
      ? res(200, { date: "2026-09-10", site_is_default: false, picks: [] })
      : res(409, { detail: { detail: NO_SITE_MSG, code: "no_site" } });
  }
  if (u.includes("/api/catalog?q=planet")) {
    return res(200, { results: [jupiterAltAz ? { ...JUPITER, ...jupiterAltAz } : JUPITER], notes: [] });
  }
  if (u.includes("/api/catalog?q=")) return res(200, { results: [], notes: [] });
  if (u.includes("/api/visibility")) return res(409, { detail: { detail: NO_SITE_MSG, code: "no_site" } });
  if (u.includes("/api/cloudmap/dome")) {
    return res(200, { enabled: false, observed_at: null, stale: true, alt_start: 6, alt_step: 6, az_step: 10, rows: [] });
  }
  if (u.includes("/api/site")) return res(200, { site: siteNow, version: 3 });
  return res(200, {});
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { altAzOf } = await import("../../../../../lib/altaz");
const { TargetsSheet } = await import("../targets");

let passed = 0;
let failed = 0;
const failures: string[] = [];
async function testAsync(name: string, fn: () => Promise<void> | void): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg = ""): void {
  if (got !== want) throw new Error(`${msg} expected ${JSON.stringify(want)}, got ${JSON.stringify(got)}`);
}

const container = win.document.getElementById("root") as any;
const wait = async (ms: number): Promise<void> => {
  await act(async () => { await new Promise((r) => setTimeout(r, ms)); });
};
async function until(what: string, pred: () => boolean, budgetMs = 6000): Promise<void> {
  const t0 = performance.now();
  while (performance.now() - t0 < budgetMs) {
    if (pred()) return;
    await wait(25);
  }
  throw new Error(`timed out after ${budgetMs}ms waiting for ${what}`);
}
const title = (): string => container.querySelector(".nx-sheet-title")?.textContent ?? "";

const OPERATOR = {
  role: "operator",
  caps: ["view.status", "view.weather", "view.site_derived", "view.site_precise", "control.capture"],
  name: "tester",
};

async function mount(site: any, principal: any = OPERATOR): Promise<() => void> {
  siteNow = site;
  win.localStorage.clear();
  act(() => {
    useStore.setState({
      principal,
      equipConnected: true,
      wsPhase: "up",
      site,
      status: { connected: { camera: { connected: true, name: "sim" } } },
      weather: null,
      config: null,
    } as never);
  });
  const root = createRoot(container);
  await act(async () => { root.render(createElement(TargetsSheet, { params: {}, depth: 0 as const })); });
  await wait(600);
  return () => act(() => { root.unmount(); });
}

/** The lens dial's planet seat, as it reads to a screen reader: "<label>,
 *  <count> in reach". */
async function planetSeat(): Promise<string> {
  if (container.querySelector('[data-lens-kind="planet"]') == null) {
    await act(async () => {
      container.querySelector('[data-testid="targets-funnel"]')
        .dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
    });
    await wait(50);
  }
  return container.querySelector('[data-lens-kind="planet"]')?.getAttribute("aria-label") ?? "(no planet seat)";
}

await testAsync("premise: Jupiter is well up at the placeholder at this hour", () => {
  const alt = altAzOf(JUPITER.ra_hours, JUPITER.dec_deg, 0, 0, NOW / 1000).altDeg;
  assert(alt > 15, `Jupiter is at ${alt.toFixed(1)} deg at the placeholder, not above the 15 degree horizon`);
});

{
  const unmount = await mount(DEFAULT_SITE);
  await testAsync("default site: the sheet counts nothing it placed at the placeholder", async () => {
    await until("the refusal of tonight's list", () => container.querySelector('[data-testid="targets-error"]') != null);
    eq(title(), "0 SUGGESTED TARGETS", "the title counts a body placed at the placeholder:");
    const seat = await planetSeat();
    assert(/\b0\b/.test(seat) && !/\b1\b/.test(seat), `the lens dial counts a planet at the placeholder: "${seat}"`);
  });
  unmount();
}

{
  const unmount = await mount(SAVED_SITE);
  await testAsync("control: the same coordinates, saved, count Jupiter", async () => {
    await until("a counted title", () => /^1 SUGGESTED TARGETS$/.test(title()));
    const seat = await planetSeat();
    assert(/\b1\b/.test(seat), `the lens dial does not count the placed planet: "${seat}"`);
  });
  unmount();
}

// The other way to have no coordinates: a role that is ranked but not given
// the site. The server places Jupiter for it (its own alt/az), so the row is
// listed and counted - with no window, because the sheet's walk needs the
// site's latitude and sidereal time, and it used to walk at the `?? 0`.
{
  jupiterAltAz = { alt: 40, az: 120 };
  const DERIVED_ONLY = { role: "custom", caps: ["view.status", "view.site_derived"], name: "ranked, not sited" };
  const unmount = await mount(STRIPPED_SITE, DERIVED_ONLY);
  await testAsync("no coordinates for this role: the rig's placement is listed, with no window walked at 0,0", async () => {
    await until("Jupiter's row", () => container.querySelector('[data-target-row="Jupiter"]') != null);
    const row = container.querySelector('[data-target-row="Jupiter"]')?.textContent ?? "";
    assert(/40°/.test(row), `precondition: Jupiter's row does not carry the server's 40 degrees: "${row}"`);
    assert(/40°\s*·\s*-/.test(row), `Jupiter's row prints a window walked at 0,0 for a role with no coordinates: "${row}"`);
  });
  unmount();
  jupiterAltAz = null;
}

Date.now = realNow;

// MUTATION RECORD, 2026-09-29 (H4-USKY), in a private scratch copy of ui/
// (the session scratchpad's H4-USKY-mut, never the shared tree, #254), from a
// byte backup restored with its sha256 checked. Output verbatim.
//
//   MUTANT "sheet places at the placeholder" (sheets/targetsModel.ts: the
//   `&& site.is_default !== true` term dropped, the code as it was).
//   Observed ("targetsDefaultSite.test: 3/4 passed"):
//     x default site: the sheet counts nothing it placed at the placeholder: the title counts a body placed at the placeholder: expected "0 SUGGESTED TARGETS", got "1 SUGGESTED TARGETS"
//
//   MUTANT "sheet walks with no coordinates" (sheets/targetsModel.ts:
//   `const winMin = true ? minutesAboveFloor(...)`). Observed ("3/4"):
//     x no coordinates for this role: the rig's placement is listed, with no window walked at 0,0: Jupiter's row prints a window walked at 0,0 for a role with no coordinates: "JupiterJupiter, the largest planet.UP · cloud -40° · 3h 0mmoon 90°RGB videoi"

const total = passed + failed;
console.log(`targetsDefaultSite.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
