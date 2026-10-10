// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w20MountViewRaDecPositionUnknown.test.tsx - WP-173 / #928: the classic Mount
// view's Pointing panel prints the mount's RA and Dec only while the mount knows
// where it points.
//
//   Run directly:  npx tsx src/views/__tests__/w20MountViewRaDecPositionUnknown.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// #791 and #913 gated every other reader of the mount's position, and this
// panel was left printing `RA (J2000)` and `Dec (J2000)` straight from the
// status block, beside an Altitude stat that already said `unknown`. A mount
// that does not know where it points (`position_known === false`, #144) reports
// its HOME position, the pole, wherever the tube is, so those two stats claimed
// a precise position the tube does not hold. The strings here are the server's
// own rendering of the same reading, and are withheld with it.
//
// Named mutant (the stats read the raw status again): in MountView.tsx replace
// the two `value=` expressions of the `RA (J2000)` and `Dec (J2000)` stats with
// `value={m?.ra_str ?? "—"}` and `value={m?.dec_str ?? "—"}`.

/* eslint-disable @typescript-eslint/no-explicit-any */

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
win.Element.prototype.hasPointerCapture = function () { return true; };

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

g.fetch = async (url: string) => {
  const json = String(url).includes("/api/catalog") ? [] : { started: "ok" };
  return { ok: true, status: 200, statusText: "OK", json: async () => json } as any;
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../store");
const MountView = (await import("../MountView")).default;

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(a: T, b: T, msg: string): void {
  if (a !== b) throw new Error(`${msg} (expected ${String(b)}, got ${String(a)})`);
}

// Distinctive strings, so a leak is a plain substring test.
const RA = "05h35m";
const DEC = "-05d23m";

const OPERATOR = { role: "operator", email: null, caps: ["view.status", "control.mount"] };

function seed(mount: Record<string, unknown> | null): void {
  act(() => {
    useStore.setState({
      status: {
        connected: {}, looping: false, mode: "sim", busy_lanes: [],
        ...(mount === null ? {} : {
          mount: {
            ra_hours: 5.6, dec_deg: -5.4, ra_str: RA, dec_str: DEC,
            alt: 45, az: 120, tracking: true, parked: false, slewing: false,
            can_find_home: true, can_set_tracking_rate: true,
            tracking_rate: "sidereal", max_rate_deg_s: 1.44,
            ...mount,
          },
        }),
      },
      principal: OPERATOR,
      authGate: "open",
      toasts: [],
      confirm: null,
    } as never);
  });
}

seed({});
const container = win.document.getElementById("root") as any;
let root: ReturnType<typeof createRoot> | null = null;
function mount(): void {
  if (root) act(() => { root!.unmount(); });
  root = createRoot(container);
  act(() => { root!.render(createElement(MountView)); });
}
mount();

const settle = async (ms = 0) => {
  await act(async () => { await new Promise((r) => setTimeout(r, ms)); });
};
const text = () => (container.textContent || "") as string;
/** The Pointing panel's stat by its label: the value is the rest of the text. */
const statValue = (label: string): string => {
  const labels = [...container.querySelectorAll("*")].filter(
    (n: any) => n.children.length === 0 && (n.textContent || "").trim() === label);
  const el = labels[0];
  return el ? ((el.parentElement?.textContent || "").replace(label, "").trim()) : "";
};

await settle();

// --------------------------------------------------------------- the control
test("control: a mount that knows where it points prints its RA and Dec", () => {
  seed({ position_known: true });
  eq(statValue("RA (J2000)"), RA, "the RA stat of a known position");
  eq(statValue("Dec (J2000)"), DEC, "the Dec stat of a known position");
});

test("control: an ABSENT flag (an engine older than #144) reads as known", () => {
  seed({});
  eq(statValue("RA (J2000)"), RA, "the RA stat with no position_known key");
  eq(statValue("Dec (J2000)"), DEC, "the Dec stat with no position_known key");
});

test("control: no mount block at all prints a dash, not unknown", () => {
  seed(null);
  eq(statValue("RA (J2000)"), "—", "the RA stat with no mount");
  eq(statValue("Dec (J2000)"), "—", "the Dec stat with no mount");
});

// ------------------------------------------------------------------ the bug
test("position_known false: the RA and Dec stats say unknown and print no coordinate", () => {
  seed({ position_known: false });
  eq(statValue("RA (J2000)"), "unknown", "the RA stat");
  eq(statValue("Dec (J2000)"), "unknown", "the Dec stat");
  assert(!text().includes(RA) && !text().includes(DEC),
    "the mount's home reading is printed on the page as the tube's position");
});

test("the stats come back with the position, on the next status frame", () => {
  seed({ position_known: false });
  eq(statValue("RA (J2000)"), "unknown", "the RA stat while unknown");
  seed({ position_known: true });
  eq(statValue("RA (J2000)"), RA, "the RA stat after the position is trusted");
  eq(statValue("Dec (J2000)"), DEC, "the Dec stat after the position is trusted");
});

// ------------------------------------------------------------------- tally
act(() => { root!.unmount(); });
const total = passed + failed;
console.log(`w20MountViewRaDecPositionUnknown: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
