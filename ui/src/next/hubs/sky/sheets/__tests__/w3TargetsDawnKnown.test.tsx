// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w3TargetsDawnKnown.test.tsx - the Suggested-targets sheet's row window with
// no dawn known yet (#551 remainder, WP-24a new defect, backlog wave 3
// integration).
//
//   Run directly:  npx tsx src/next/hubs/sky/sheets/__tests__/w3TargetsDawnKnown.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT. `targetsModel.ts`'s `all` memo hard-coded `trackCtx.hoursToDawn`
// at 8 regardless of whether `/api/visibility` had ever answered for this
// site, on the reasoning that the window column only compares rows against
// each other and every row walks the same span - so a row nobody has walked
// yet printed a computed-looking "Nm" off a made-up eight hours, instead of
// the honest "no number yet" `finder/model.ts` already shows for the same
// situation (#551, fixed there with `dawnKnown` - see
// `w3SkyReachWindowDawnKnown.test.tsx`). The comment in `targetsModel.ts`
// that shipped the finder's fix said in so many words that this sheet "keeps
// its own copy of this walk ... and carries the same defect" - this file
// closes that return.
//
// THE FIX. `targetsModel.ts` now fetches the SAME `/api/visibility` signal
// (anchored on `tonight.rows[0]`, pre-ranking, with a site-only fallback
// point so the fetch does not wait on the ranked list it feeds), derives
// `dawnKnown` from `dark_end_unix`, and gates `winMin` on `haveCoords &&
// dawnKnown`, the same shape `finder/model.ts`'s `ranked` memo uses.
//
// MUTATION RECORD: see the block at the foot of this file.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
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

win.localStorage.setItem("astrodeck-next-sky-lens", JSON.stringify({
  galaxy: true, nebula: true, cluster: true, planet: true, moon: true,
}));
win.localStorage.setItem("astrodeck-next-sky-floor", "0");

// M31, well clear of the floor at this fixture's 0N 0E site for hours either
// side of this instant - the same pairing `w3SkyReachWindowDawnKnown.test.tsx`
// uses, re-proved there rather than trusted here too.
const NOW = Date.UTC(2026, 8, 10, 22, 0, 0);
const realNow = Date.now;
Date.now = () => NOW;

const M31 = { ra_hours: 0.7123, dec_deg: 41.269 };
const PICKS = [{
  id: "m31", name: "M31", type: "Galaxy",
  ra_hours: M31.ra_hours, dec_deg: M31.dec_deg, mag: 3.4, size_arcmin: 190,
  difficulty: "easy", max_alt: 60, transit_unix: NOW / 1000 + 7200,
  moon_sep_deg: 80, never_rises_above_limit: false, score: 1,
}];

const SAVED_SITE = { name: "Fixture site", latitude: 0, longitude: 0, elevation_m: 0, is_default: false, horizon_min_deg: 15 };

/** Flipped per section to choose whether `/api/visibility` ever answers. */
let nightAnswers = false;

function ok(data: unknown) {
  return { ok: true, status: 200, statusText: "OK", json: async () => data };
}
function fail(status: number) {
  return { ok: false, status, statusText: "Internal Server Error", json: async () => ({ detail: "Internal Server Error" }) };
}

g.fetch = async (url: any) => {
  const u = String(url);
  if (u.includes("/api/catalog/tonight")) return ok({ date: "2026-09-10", site_is_default: false, picks: PICKS });
  if (u.includes("/api/catalog?q=")) return ok({ results: [], notes: [] });
  if (u.includes("/api/cloudmap/dome")) {
    return ok({ enabled: false, observed_at: null, stale: true, alt_start: 6, alt_step: 6, az_step: 10, rows: [] });
  }
  if (u.includes("/api/site")) return ok({ site: SAVED_SITE, version: 3 });
  if (u.includes("/api/visibility")) {
    if (!nightAnswers) return fail(500);
    return ok({
      date: "2026-09-10", transit_unix: NOW / 1000 + 3600, transit_alt: 48, transit_in_daylight: false,
      dark_start_unix: NOW / 1000 - 3600, dark_end_unix: NOW / 1000 + 5 * 3600,
      darkness_kind: "astronomical", samples: [],
      moon: { illumination: 0.2, phase_name: "Waning Crescent", alt: -20, az: 90, separation_deg: 80, rise_unix: null, set_unix: null },
      best_window: null, alt_limit_deg: 15, never_rises_above_limit: false,
    });
  }
  return ok({});
};

// ------------------------------------------ imports, AFTER the globals are set
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { TargetsSheet } = await import("../targets");
const { altAzOf, lstHours } = await import("../../../../../lib/altaz");
const { D2R, FLOOR_DEG, minutesAboveFloor, walkTrack, windowLabel } = await import("../../finder");

// ------------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function testAsync(name: string, fn: () => Promise<void> | void): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

const container = win.document.getElementById("root") as any;
const q = (sel: string) => container.querySelector(sel) as any;
/** The row's `altDeg° · windowLabel` chip specifically - NOT the row's whole
 *  textContent, which also carries the moon-separation text right next to it
 *  with no separating whitespace ("...0mmoon 80°...") and would let "0m"
 *  slip past a `\b` check by borrowing a word boundary from the wrong
 *  neighbour. Two of the row's spans use ".nx-mono": `statusTxt` ("UP ·
 *  cloud -" with no measured cloud reading here, ALSO carrying a middle dot)
 *  and this chip - told apart by the degree sign, which only the altitude
 *  chip carries. */
function windowChipText(row: any): string {
  const spans = Array.from(row.querySelectorAll(".nx-mono")) as any[];
  const chip = spans.find((el) => {
    const t = el.textContent ?? "";
    return t.includes("·") && t.includes("°");
  });
  if (!chip) throw new Error("no alt/window chip (.nx-mono with ° and ·) found in the row");
  return chip.textContent as string;
}
const settle = async (): Promise<void> => {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 400)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
};

const OPERATOR = {
  role: "operator", email: "op@rig",
  caps: ["view.status", "view.preview", "view.weather", "view.site_derived",
    "control.capture", "control.mount"],
};

function seed(): void {
  useStore.setState({
    principal: OPERATOR,
    equipConnected: true,
    wsPhase: "up",
    site: SAVED_SITE,
    status: { connected: { camera: { connected: true, name: "sim" } } },
    weather: null,
    config: null,
  } as never);
}

/** The walk the MODEL ought to produce once a dawn is known: 0N 0E, dawn five
 *  hours off (the double's own `dark_end_unix`), the fixture's 15 degree
 *  floor, no horizon line - the app's own `walkTrack`, not a restated number,
 *  so this pins the real contract rather than a guess at it. */
function modelWindow(): number {
  const lst = lstHours(0, NOW / 1000);
  return minutesAboveFloor(walkTrack(M31.dec_deg * D2R, (lst - M31.ra_hours) * 15 * D2R, {
    latDeg: 0, hoursToDawn: 5, horizon: [], horizonMinDeg: 15, maskOn: true, holdAt: () => false,
  }));
}

async function mount(): Promise<{ root: ReturnType<typeof createRoot>; unmount: () => void }> {
  seed();
  const root = createRoot(container);
  await act(async () => {
    root.render(createElement(TargetsSheet, { params: {}, depth: 0 as const }));
  });
  await settle();
  await settle();
  return { root, unmount: () => act(() => { root.unmount(); }) };
}

await testAsync("premise: M31 clears the 15 degree floor at the fixture site for the next five hours", () => {
  const now = altAzOf(M31.ra_hours, M31.dec_deg, 0, 0, NOW / 1000).altDeg;
  assert(now > FLOOR_DEG, `M31 is at ${now.toFixed(1)} deg now, not above the ${FLOOR_DEG} degree floor`);
});

// ================================================== 1. no dawn known yet
{
  nightAnswers = false;
  const m = await mount();

  await testAsync("no dawn known: the targets row does not read a 0m walked to a dawn nobody knows", () => {
    const row = q('[data-target-row="m31"]');
    assert(row != null, "M31 never reached the targets sheet - the fixture is wrong, not the component");
    const text = windowChipText(row);
    assert(!/(?:^|\D)0m$/.test(text), `the row reads a computed-looking zero window with no night known: "${text}"`);
    assert(text.endsWith(windowLabel(null)), `the row does not print the absent window: "${text}"`);
  });

  m.unmount();
}

// ======================================= 2. control: the night does answer
{
  nightAnswers = true;
  const m = await mount();

  await testAsync("control: once a dawn is known, the targets row carries the walked window", () => {
    const want = modelWindow();
    assert(want > 0, `premise: the model's own walk gives M31 ${want} minutes, not a window`);
    const row = q('[data-target-row="m31"]');
    assert(row != null, "M31 never reached the targets sheet - the fixture is wrong, not the component");
    const text = windowChipText(row);
    assert(text.endsWith(windowLabel(want)), `the row does not carry the walked window: "${text}", want "${windowLabel(want)}"`);
  });

  m.unmount();
  nightAnswers = false;
}

Date.now = realNow;

// MUTATION RECORD, 2026-10-01 (W3 integration, #551 remainder / WP-24a new
// defect), from a byte backup of targetsModel.ts restored byte-identically.
// Output verbatim.
//
//   MUTANT "all walks with no dawn" (targetsModel.ts: the `all` memo's
//   `winMin` condition `haveCoords && dawnKnown` written back to
//   `haveCoords`, the code as it was before this fix). Observed
//   ("w3TargetsDawnKnown.test: 2/3 passed"):
//     x no dawn known: the targets row does not read a 0m walked to a dawn
//     nobody knows: the row reads a computed-looking zero window with no
//     night known: "28° · 0m"
//
//   MUTANT (control) "all never walks" (`winMin` forced to `null`
//   unconditionally, which would pass the case above by printing "-" for
//   every row, even once a night is known). Observed ("2/3 passed"):
//     x control: once a dawn is known, the targets row carries the walked
//     window: the row does not carry the walked window: "28° · -", want
//     "5h 15m"

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`w3TargetsDawnKnown.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) {
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}

export default { passed, failed, total };
export { passed, failed, total };
