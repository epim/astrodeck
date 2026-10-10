// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w21MountAltAzSubLabel.test.tsx - WP-194 / #958: the MOUNT sheet's ALT / AZ tile
// prints its RA / Dec sub-label through `believedRaDec`, the one gate for the
// mount's equatorial reading, and not through a ternary of its own.
//
//   Run directly:  npx tsx src/next/hubs/rig/sheets/__tests__/w21MountAltAzSubLabel.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE STATE. A mount that does not know where it points (`position_known ===
// false`, #144) reports its HOME position, the pole, wherever the tube is, and
// its RA/Dec strings are that reading's rendering. `believedRaDec` withholds the
// numbers and the strings together. The tile's sub-label used to print
// `m.ra_str` / `m.dec_str` behind `positionIsKnown && altAzKnown`, which was
// correct and rested on that one ternary: drop the guard and the home reading
// printed, with no test noticing (the w16 sheet tests grade the alt/az value and
// the tile's text, not that these two strings are absent from the page).
//
// WHAT EACH CASE HOLDS THE SHEET TO:
//   1. control: a known position prints `RA ... Dec ...` under the angles.
//   2. an unknown position prints neither string anywhere on the page. This is
//      the case the issue asked for; it holds on the old ternary and on the
//      selector, and it is the one the mutant below breaks.
//   3. a reading whose numbers are unreadable is withheld WITH its strings (the
//      selector's contract) rather than half-printed from the wire's text.
//   4. a wire with no strings prints "not reported", never the literal
//      "undefined" the old template produced.
//   5. control: an ABSENT flag (an engine older than #144) reads as known.
//
// Named mutants (each is an edit to mount.tsx, run from a byte backup and
// restored byte-identically). The first failing assertion of each is quoted:
//   w21m1_hand_written_gate -- the sub-label template put back to
//     `RA ${m.ra_str} · Dec ${m.dec_str}`. 4/6: "x a reading with no numbers is
//     withheld with its strings ...: the tile prints RA / Dec strings the
//     selector withholds: ALT / AZ62° / 41°RA 07h31m · Dec -35d12m" and "x a
//     wire with no RA / Dec strings prints 'not reported', never 'undefined':
//     the tile prints undefined: ... RA undefined · Dec undefined".
//   w21m2_guard_dropped_on_old_gate -- m1 AND the tile's
//     `: !positionIsKnown ? "the mount's home reading, not the tube"` arm
//     deleted: the next edit the issue feared, on the code it feared it on.
//     2/6: "x position_known false: neither the RA string nor the Dec string is
//     anywhere on the page: the mount's home RA is printed on the sheet".
//   w21m3_arm_deleted_on_the_selector -- ONLY the arm deleted, selector kept.
//     5/6: case 2 (neither string on the page) stays green and only "x
//     position_known false: the tile still says whose reading it would have
//     been" fails. That is what the gate buys: one edit no longer leaks.

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

// Made-up numbers throughout, and distinctive, so a leak is a plain substring
// test. 62 / 41 are the angles a known position prints.
const RA_STR = "07h31m";
const DEC_STR = "-35d12m";
const BELIEVED_ALT = 62;
const BELIEVED_AZ = 41;

function mountStatus(over: Record<string, unknown> = {}) {
  return {
    connected: { telescope: { connected: true, name: "ZWO AM5N" } },
    looping: false,
    busy_lanes: [],
    busy: null,
    backend_links: [{ role: "telescope", connected: true, error: null }],
    mount: {
      ra_hours: 7.52, dec_deg: -35.2, ra_str: RA_STR, dec_str: DEC_STR,
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
function seed(over: Record<string, unknown> = {}, drop: string[] = []): void {
  const status: any = mountStatus(over);
  for (const k of drop) delete status.mount[k];
  act(() => {
    useStore.setState({
      status,
      equipConnected: true,
      wsPhase: "up",
      authGate: "open",
      principal: OPERATOR,
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
const tileText = (): string => {
  const tile = q('[data-testid="tile-altaz"]');
  assert(tile != null, "the ALT / AZ tile is gone - the sheet did not render");
  return tile.textContent || "";
};

// ================================================================ 1. control
seed();
mount();

test("control: a KNOWN position prints the angles and the RA / Dec under them", () => {
  const t = tileText();
  assert(t.includes(`${BELIEVED_ALT}° / ${BELIEVED_AZ}°`), `the tile does not print the angles: ${t}`);
  assert(t.includes(`RA ${RA_STR} · Dec ${DEC_STR}`), `the tile does not print the RA / Dec: ${t}`);
});

// ====================================================== 2. unknown position
seed({ position_known: false });
mount();

test("position_known false: neither the RA string nor the Dec string is anywhere on the page", () => {
  assert(!text().includes(RA_STR), `the mount's home RA is printed on the sheet: ${text().slice(0, 300)}`);
  assert(!text().includes(DEC_STR), `the mount's home Dec is printed on the sheet: ${text().slice(0, 300)}`);
  assert(!text().includes(`${BELIEVED_ALT}°`), "a believed altitude is printed on the sheet");
});

test("position_known false: the tile still says whose reading it would have been", () => {
  assert(/home reading, not the tube/.test(tileText()), `the tile: ${tileText()}`);
});

// ================================================= 3. unreadable numbers
seed({ position_known: true, ra_hours: undefined, dec_deg: undefined });
mount();

test("a reading with no numbers is withheld with its strings, not half-printed from the wire's text", () => {
  const t = tileText();
  assert(!t.includes(RA_STR) && !t.includes(DEC_STR),
    `the tile prints RA / Dec strings the selector withholds: ${t}`);
  assert(/RA \/ Dec not reported/.test(t), `the tile does not say the RA / Dec is not reported: ${t}`);
});

// =================================================== 4. a wire with no strings
seed({ position_known: true }, ["ra_str", "dec_str"]);
mount();

test("a wire with no RA / Dec strings prints 'not reported', never 'undefined'", () => {
  const t = tileText();
  assert(!/undefined/.test(t), `the tile prints undefined: ${t}`);
  assert(!/RA\s+·\s+Dec/.test(t), `the tile prints an empty RA / Dec: ${t}`);
  assert(/RA \/ Dec not reported/.test(t), `the tile does not say the RA / Dec is not reported: ${t}`);
  assert(!/undefined/.test(text()), "the sheet prints undefined somewhere");
});

// ============================================ 5. an engine older than #144
seed({}, ["position_known"]);
mount();

test("control: an ABSENT position_known (an engine older than #144) reads as known", () => {
  const t = tileText();
  assert(t.includes(`RA ${RA_STR} · Dec ${DEC_STR}`), `the tile withheld a known reading: ${t}`);
});

if (rootRef) act(() => { rootRef!.unmount(); });

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`w21MountAltAzSubLabel: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
