// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w22MountAltAzTile.test.tsx - WP-208 / #996: the MOUNT sheet's ALT / AZ tile
// prints its angles, and picks its tone, through `believedPointing`, the one
// gate for the mount's horizontal reading (#791), and not through a ternary of
// its own.
//
//   Run directly:  npx tsx src/next/hubs/rig/sheets/__tests__/w22MountAltAzTile.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE STATE. A mount that does not know where it points (`position_known ===
// false`, #144) reports its HOME position, the pole, wherever the tube is, and
// at the pole the altitude IS the site latitude (#140). The tile used to print
// `${m.alt}° / ${m.az}°` behind a hand-written `positionIsKnown` / `altAzKnown`
// ternary and to take its tone from `m.alt < 20`, so what kept the pole off the
// page was one arm of one ternary, and the only thing that would notice that
// arm going was the unknown-state text assertion in w16MountPositionUnknown.
// The #958 fix gave the RA / Dec sub-label the same treatment
// (w21MountAltAzSubLabel); this is the other half of the tile.
//
// WHAT EACH CASE HOLDS THE SHEET TO:
//   1. control: a known position prints the angles, and a high one is not warned.
//   2. control: a known low altitude is warned (the tone still works).
//   3. an unknown position prints neither angle anywhere on the page and the
//      tile says whose reading it would have been. Holds on the old ternary and
//      on the selector; it is the case the mutants below that delete the arm
//      are graded by.
//   4. an altitude that is not a finite number is withheld, not printed as
//      NaN / Infinity. The old gate was `typeof m.alt === "number"`, which NaN
//      and the infinities satisfy; the selector's contract is a FINITE number.
//   5. the same for the tone: -Infinity is `< 20`, so the old tone warned about
//      a reading the tile did not print. Cases 4 to 6 grade the TILE, not the
//      page: the pad hosted below it prints its own near-horizon line from the
//      raw altitude (SlewPad.tsx), which is outside this file's change.
//   6. one unreadable angle withholds the pair, not just its own half.
//   7. control: a viewer (alt / az absent) is told "hidden", never "undefined".
//   8. control: an ABSENT flag (an engine older than #144) reads as known.
//
// Named mutants (each is an edit to mount.tsx, run from a byte backup and
// restored byte-identically). The first failing assertion of each is quoted:
//   w22m1_value_raw -- the value arm reads the raw wire again, in the original
//     order: `!positionIsKnown ? "unknown" : typeof m.alt === "number" && typeof
//     m.az === "number" ? `${m.alt}° / ${m.az}°` : "hidden"`. The defect itself.
//     6/9: "x a NaN altitude is withheld, not printed: the tile prints NaN: ALT
//     / AZNaN° / 41°needs operator or admin access".
//   w22m2_raw_and_arm_deleted -- m1 AND the `!positionIsKnown ? "unknown"` arm
//     deleted: the edit the issue feared, on the code it feared it on. 4/9:
//     "x position_known false: neither angle is anywhere on the page: the home
//     altitude is printed on the sheet".
//   w22m3_arm_deleted_on_the_selector -- ONLY the arm deleted, selector kept.
//     8/9: case 3's "neither angle on the page" stays green and only "x
//     position_known false: the tile says unknown, whose reading it would have
//     been, and is warned: the tile value: ALT / AZhidden..." fails. That is
//     what the gate buys: the next edit to that ternary no longer leaks.
//   w22m4_tone_raw -- the tone reads `typeof m.alt === "number" && m.alt < 20`.
//     8/9: "x a -Infinity altitude is withheld and not warned about: a reading
//     the tile does not print is toned warn".

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/#/rig/devices/mount", pretendToBeVisual: true },
);
const win = dom.window as any;

win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} addEventListener() {} send() {} };
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
win.Element.prototype.setPointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.releasePointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.hasPointerCapture = function () { return true; };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "PointerEvent", "Image", "localStorage", "getComputedStyle", "matchMedia",
  "WebSocket", "requestAnimationFrame", "cancelAnimationFrame", "ResizeObserver",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

g.fetch = async (url: string) => {
  if (String(url).includes("/api/catalog")) {
    return { ok: true, status: 200, statusText: "OK", json: async () => [] };
  }
  return { ok: true, status: 200, statusText: "OK", json: async () => ({}) };
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { MountSheet } = await import("../mount");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

// ------------------------------------------------------------------ fixtures
const OPERATOR = {
  role: "operator", email: "op@rig",
  caps: ["view.status", "view.preview", "view.site_derived", "control.capture",
    "control.mount", "control.guide"],
};
const VIEWER = {
  role: "viewer", email: "viewer@rig",
  caps: ["view.status", "view.preview"],
};

// Made-up numbers throughout, and distinctive, so a leak is a plain substring
// test. 62 / 41 are the angles a known position prints and the 12.5 is a low
// altitude; none of them is anybody's site.
const BELIEVED_ALT = 62;
const BELIEVED_AZ = 41;
const LOW_ALT = 12.5;

function mountStatus(over: Record<string, unknown> = {}) {
  return {
    connected: { telescope: { connected: true, name: "ZWO AM5N" } },
    looping: false,
    busy_lanes: [],
    busy: null,
    backend_links: [{ role: "telescope", connected: true, error: null }],
    mount: {
      ra_hours: 7.52, dec_deg: -35.2, ra_str: "07h31m", dec_str: "-35d12m",
      alt: BELIEVED_ALT, az: BELIEVED_AZ, tracking: true, parked: false, slewing: false,
      tracking_rate: "sidereal", can_set_tracking_rate: true, can_find_home: true,
      max_rate_deg_s: 1.44,
      pointing: { verified: false, reason: "solve failed: not enough stars", error_arcmin: 2.4 },
      ...over,
    },
    meridian: {
      status: "counting", hours_to_flip: 1.63, flip_enabled: true, pier_side: "east",
    },
  };
}

/** `drop` names mount keys to REMOVE from the block (an absent key, which is
 *  not the same as one set to undefined on the wire). */
function seed(
  over: Record<string, unknown> = {}, drop: string[] = [], principal: unknown = OPERATOR,
): void {
  const status: any = mountStatus(over);
  for (const k of drop) delete status.mount[k];
  act(() => {
    useStore.setState({
      status,
      equipConnected: true,
      wsPhase: "up",
      authGate: "open",
      principal,
      sequence: { state: "idle" },
      polar: {
        state: "idle", az_error: 0, alt_error: 0, total_error: 0,
        progress: 0, message: "", source: null,
      },
      logs: [],
      config: { safety: { solar_avoidance: true, solar_exclusion_deg: 30 } },
      mountOp: null,
      toasts: [],
      confirm: null,
      locked: false,
    } as never);
  });
}

const container = win.document.getElementById("root") as any;
let rootRef: ReturnType<typeof createRoot> | null = null;
function mount(): void {
  if (rootRef) act(() => { rootRef!.unmount(); });
  rootRef = createRoot(container);
  act(() => { rootRef!.render(createElement(MountSheet as any, { params: {}, depth: 0 })); });
}
const q = (sel: string) => container.querySelector(sel) as any;
const text = () => (container.textContent || "") as string;
const tile = (): any => {
  const t = q('[data-testid="tile-altaz"]');
  assert(t != null, "the ALT / AZ tile is gone - the sheet did not render");
  return t;
};
const tileText = (): string => tile().textContent || "";
const tileValue = (): string => tile().querySelector(".nx-readout-value")?.textContent || "";
const tileTone = (): string | null => tile().getAttribute("data-tone");

// ================================================================ 1. control
seed();
mount();

test("control: a KNOWN position prints the angles and is not warned", () => {
  assert(tileValue() === `${BELIEVED_ALT}° / ${BELIEVED_AZ}°`, `the tile value: ${tileText()}`);
  assert(tileTone() == null, `a high altitude is toned ${tileTone()}`);
});

// ============================================================ 2. low altitude
seed({ alt: LOW_ALT });
mount();

test("control: a known LOW altitude is printed and warned", () => {
  assert(tileValue() === `${LOW_ALT}° / ${BELIEVED_AZ}°`, `the tile value: ${tileText()}`);
  assert(tileTone() === "warn", `a low altitude is toned ${tileTone()}, not warn`);
});

// ====================================================== 3. unknown position
seed({ position_known: false });
mount();

test("position_known false: neither angle is anywhere on the page", () => {
  assert(!text().includes(`${BELIEVED_ALT}°`), `the home altitude is printed on the sheet: ${text().slice(0, 300)}`);
  assert(!text().includes(`${BELIEVED_AZ}°`), `the home azimuth is printed on the sheet: ${text().slice(0, 300)}`);
});

test("position_known false: the tile says unknown, whose reading it would have been, and is warned", () => {
  assert(tileValue() === "unknown", `the tile value: ${tileText()}`);
  assert(/home reading, not the tube/.test(tileText()), `the tile: ${tileText()}`);
  assert(tileTone() === "warn", `the tile is toned ${tileTone()}, not warn`);
});

// ========================================= 4-6. a reading that is not a number
seed({ alt: Number.NaN });
mount();

test("a NaN altitude is withheld, not printed", () => {
  assert(!/NaN/.test(tileText()), `the tile prints NaN: ${tileText()}`);
  assert(tileValue() === "hidden", `the tile value: ${tileText()}`);
});

seed({ alt: Number.NEGATIVE_INFINITY });
mount();

test("a -Infinity altitude is withheld and not warned about", () => {
  assert(!/Infinity/.test(tileText()), `the tile prints Infinity: ${tileText()}`);
  assert(tileValue() === "hidden", `the tile value: ${tileText()}`);
  assert(tileTone() == null, `a reading the tile does not print is toned ${tileTone()}`);
});

seed({ az: Number.POSITIVE_INFINITY });
mount();

test("one unreadable angle withholds the pair, not just its own half", () => {
  assert(!/Infinity/.test(tileText()), `the tile prints Infinity: ${tileText()}`);
  assert(!tileText().includes(`${BELIEVED_ALT}°`),
    `the altitude is half-printed beside an azimuth the gate withholds: ${tileText()}`);
  assert(tileValue() === "hidden", `the tile value: ${tileText()}`);
});

// ================================================================= 7. viewer
seed({}, ["alt", "az"], VIEWER);
mount();

test("control: a viewer's tile (alt / az absent) says hidden, never undefined", () => {
  assert(tileValue() === "hidden", `the tile value: ${tileText()}`);
  assert(!/undefined/.test(text()), `the sheet prints undefined: ${tileText()}`);
});

// ============================================ 8. an engine older than #144
seed({}, ["position_known"]);
mount();

test("control: an ABSENT position_known (an engine older than #144) reads as known", () => {
  assert(tileValue() === `${BELIEVED_ALT}° / ${BELIEVED_AZ}°`, `the tile withheld a known reading: ${tileText()}`);
});

if (rootRef) act(() => { rootRef!.unmount(); });

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`w22MountAltAzTile: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
