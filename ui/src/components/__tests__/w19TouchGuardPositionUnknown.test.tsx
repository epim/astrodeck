// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w19TouchGuardPositionUnknown.test.tsx - WP-160 / #913: the lock screen's
// status chip while the mount does not know where it points.
//
//   Run directly:  npx tsx src/components/__tests__/w19TouchGuardPositionUnknown.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// The locked screen carries a solid status chip (STATE, RA / DEC, RMS, SEQ,
// SENSOR) so the rig can be read through the lock. After a power cycle the AM5
// reports its HOME position, the pole, wherever the tube is
// (`status.mount.position_known === false`, #144), and the chip's RA / DEC row
// printed that as the tube's. The chip must say "unknown" for that row and
// print no coordinate, and print the coordinates again the moment the flag
// clears.
//
// Named mutant (the chip prints the raw reading again): in TouchGuard.tsx replace
// `{raDec.ra_str} {raDec.dec_str}` with `{mount.ra_str} {mount.dec_str}` and
// `{raDec ? (` with `{mount ? (`.

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
win.fetch = () => new Promise(() => { /* nothing here sends a request */ });

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "KeyboardEvent", "localStorage", "getComputedStyle",
  "matchMedia", "WebSocket", "requestAnimationFrame", "cancelAnimationFrame", "fetch",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../store");
const TouchGuard = (await import("../TouchGuard")).default;

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

// ------------------------------------------------------------------- fixture
// Distinctive strings, so a leak is a plain substring test.
const RA = "01:30:00";
const DEC = "+30:00:00";
function seed(mountExtra: Record<string, unknown>): void {
  act(() => {
    useStore.setState({
      // Set directly rather than through setLocked(), which fires its own
      // /api/mount/stop: this test is about the chip, not about engaging the lock.
      locked: true,
      lockAvailable: true,
      principal: { role: "admin", email: null, caps: ["view.status", "control.mount"] },
      status: {
        connected: {}, looping: false, mode: "sim",
        mount: {
          ra_hours: 1.5, dec_deg: 30, ra_str: RA, dec_str: DEC,
          tracking: true, parked: false, slewing: false, alt: 52, az: 200,
          ...mountExtra,
        },
      },
    } as never);
  });
}

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
seed({ position_known: true });
act(() => { root.render(createElement(TouchGuard)); });

const overlay = () => container.querySelector('[role="dialog"][aria-modal="true"]');
const overlayText = (): string => overlay()?.textContent || "";
/** The value cell of the chip's RA / DEC row. */
function raDecValue(): string | null {
  const label = [...container.querySelectorAll("span")]
    .find((s: any) => (s.textContent || "").trim() === "RA / DEC");
  return label ? String(label.nextElementSibling?.textContent ?? "") : null;
}

// --------------------------------------------------------------- the control
test("control: the lock is up and the chip has an RA / DEC row", () => {
  assert(overlay() != null, "no lock overlay rendered - the fixture is wrong, not the component");
  assert(/SCREEN LOCKED/.test(overlayText()), "this is not the locked screen");
  assert(raDecValue() != null, "the status chip has no RA / DEC row");
});

test("control: a mount that knows where it points shows its RA and Dec", () => {
  assert(raDecValue() === `${RA} ${DEC}`, `got "${raDecValue()}"`);
});

// ------------------------------------------------------------------ the bug
seed({ position_known: false });

test("position_known false: the chip prints no RA or Dec", () => {
  assert(!overlayText().includes(RA) && !overlayText().includes(DEC),
    `the mount's home reading is printed on the lock screen as the tube's: "${overlayText()}"`);
});

test("position_known false: the RA / DEC row says unknown instead of vanishing", () => {
  assert(raDecValue() === "unknown", `the row reads "${raDecValue()}"`);
});

// ------------------------------------------------- recovery and older engines
seed({ position_known: true });

test("clearing the flag brings the RA and Dec back", () => {
  assert(raDecValue() === `${RA} ${DEC}`, `got "${raDecValue()}"`);
});

{
  // An engine older than #144 sends no flag: absent reads as KNOWN.
  act(() => {
    useStore.setState({
      status: {
        connected: {}, looping: false, mode: "sim",
        mount: {
          ra_hours: 1.5, dec_deg: 30, ra_str: RA, dec_str: DEC,
          tracking: true, parked: false, slewing: false,
        },
      },
    } as never);
  });
  test("an ABSENT flag (an engine older than #144) reads as known", () => {
    assert(raDecValue() === `${RA} ${DEC}`, `got "${raDecValue()}"`);
  });
}

act(() => { root.unmount(); });

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`w19TouchGuardPositionUnknown: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
