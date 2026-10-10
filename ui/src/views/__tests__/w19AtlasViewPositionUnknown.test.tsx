// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w19AtlasViewPositionUnknown.test.tsx - WP-160 / #913: the classic Atlas's
// live-pointing footprint and its caption while the mount does not know where
// it points.
//
//   Run directly:  npx tsx src/views/__tests__/w19AtlasViewPositionUnknown.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// The Atlas draws "Pointing now": a sky-anchored footprint at the mount's
// RA/Dec, a label naming it, and, when it is off the map, a sentence under the
// canvas that carries the mount's own RA/Dec text. After a power cycle the AM5
// reports its HOME position, the pole, wherever the tube is
// (`status.mount.position_known === false`, #144), so the footprint, its label
// and its sentence each claimed the tube was at the pole. A known position
// does all three (the control); an unknown one does none.
//
// WHAT THIS FIXTURE IS NOT. The sky canvas is mounted but blind (jsdom has no
// WebGL or canvas 2D); the footprint is graded through what it leaves in the
// DOM, the "Pointing now" label and the caption.
//
// Named mutant (the page reads the raw reading again): in AtlasView.tsx replace
// `return m && here ? { ...here, slewing: m.slewing } : null;` with
// `return m ? { ra_hours: m.ra_hours, dec_deg: m.dec_deg, ra_str: m.ra_str, dec_str: m.dec_str, slewing: m.slewing } : null;`.

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
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
win.HTMLCanvasElement.prototype.getContext = () => null;
win.URL.createObjectURL = () => "blob:stub";
win.URL.revokeObjectURL = () => {};

// ------------------------------------------------------- the fake rig backend
function respond(body: any, status = 200): any {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: "OK",
    headers: { get: () => null },
    json: async () => body,
    blob: async () => ({}),
  };
}
const OPTICS = {
  focal_length_mm: 530,
  pixel_size_um: 3.76,
  sensor_width_px: 4144,
  sensor_height_px: 2822,
  guide_focal_length_mm: null,
};
const CONFIG: any = {
  version: 7,
  optics: OPTICS,
  optics_computed: OPTICS,
  survey: { online_fetch: false },
};
const NIGHT: any = {
  alt_limit_deg: 30, never_rises_above_limit: false,
  transit_unix: 1_800_000_000, transit_alt: 72, transit_in_daylight: false,
  dark_start_unix: 1_799_990_000, dark_end_unix: 1_800_020_000,
  darkness_kind: "astronomical", best_window: null, samples: [],
  hours_above_limit: 6,
  moon: {
    illumination: 0.2, phase_name: "Waning Crescent", alt: -20, az: 90,
    separation_deg: 70, rise_unix: null, set_unix: null,
  },
};
win.fetch = async (url: any) => {
  const path = String(url);
  if (path.includes("/api/visibility")) return respond(NIGHT);
  if (path.includes("/api/config")) return respond(CONFIG);
  return respond({});
};

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "localStorage", "requestAnimationFrame",
  "cancelAnimationFrame", "getComputedStyle", "matchMedia", "WebSocket",
  "ResizeObserver", "Image", "URL", "fetch", "Blob",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../store");
const AtlasView = (await import("../AtlasView")).default;

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
// Distinctive strings, so a leak is a plain substring test.
const RA = "00h42m";
const DEC = "+41d16m";

function seedMount(mountExtra: Record<string, unknown>): void {
  useStore.setState({
    status: {
      connected: { mount: true }, looping: false, mode: "sim",
      mount: {
        ra_hours: 0.712, dec_deg: 41.27, ra_str: RA, dec_str: DEC,
        alt: 62, az: 105, tracking: true, parked: false, slewing: false,
        ...mountExtra,
      },
      busy_lanes: [],
    },
    principal: { role: "operator", email: null, caps: ["view.status", "control.mount"] },
    config: CONFIG,
    site: { horizon_min_deg: 30, is_default: false },
  } as never);
}

/** A framing whose centre is `centerRa` hours: the mount's own RA puts the
 *  footprint at the middle of the map, a far one puts it off the map. */
function seedFraming(centerRa: number): void {
  useStore.setState({
    framing: {
      target: {
        id: "M 31", name: "Andromeda Galaxy", type: "Galaxy",
        ra_hours: 0.712, dec_deg: 41.27, size_arcmin: 190,
      },
      center: { ra_hours: centerRa, dec_deg: 41.27 },
      rotation_deg: 0,
      survey: "CDS/P/DSS2/color",
      stretch: "linear",
      fovZoomDeg: 2,
      mosaic: { rows: 1, cols: 1, overlap: 0.25 },
      panels: [],
    },
  } as never);
}

const container = win.document.getElementById("root") as any;
const text = (): string => container.textContent || "";

/** Mount the page fresh for this mount block and framing centre. */
async function run(mountExtra: Record<string, unknown>, centerRa: number): Promise<{ text: string; unmount: () => Promise<void> }> {
  await act(async () => { seedMount(mountExtra); seedFraming(centerRa); });
  const root = createRoot(container);
  await act(async () => { root.render(createElement(AtlasView)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  assert(/Andromeda Galaxy/.test(text()),
    "the framed target's name is not on the page - the fixture never rendered");
  return { text: text(), unmount: async () => { await act(async () => { root.unmount(); }); } };
}

// --------------------------------------------------------------- the control
{
  const r = await run({ position_known: true }, 0.712);
  test("control: a mount that knows where it points draws the 'Pointing now' label", () => {
    assert(/Pointing now/.test(r.text), `no live-pointing label for a known position, page text: ${r.text.slice(0, 200)}`);
  });
  await r.unmount();
}
{
  const r = await run({ position_known: true }, 13);
  test("control: a known position off the map says where the scope is, in the mount's own words", () => {
    assert(r.text.includes(`${RA} ${DEC}`),
      `the off-map sentence does not carry the mount's position: ${r.text.slice(0, 300)}`);
    assert(/Scope is pointing/.test(r.text), "no off-map sentence for a known position");
  });
  await r.unmount();
}
{
  const r = await run({}, 0.712);
  test("control: an ABSENT flag (an engine older than #144) reads as known", () => {
    assert(/Pointing now/.test(r.text), "an absent flag withheld the footprint");
  });
  await r.unmount();
}

// ------------------------------------------------------------------ the bug
{
  const r = await run({ position_known: false }, 0.712);
  test("position_known false: the footprint's 'Pointing now' label is not drawn", () => {
    assert(!/Pointing now/.test(r.text),
      "the Atlas drew the scope's footprint at the mount's home reading");
  });
  await r.unmount();
}
{
  const r = await run({ position_known: false }, 13);
  test("position_known false: no sentence carries the mount's home position", () => {
    assert(!r.text.includes(RA) && !r.text.includes(DEC),
      `the mount's home reading is printed on the page as the tube's: ${r.text.slice(0, 300)}`);
    assert(!/Scope is pointing/.test(r.text),
      "the Atlas describes how far the scope is from this view, from a position the mount cannot vouch for");
  });
  await r.unmount();
}

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`w19AtlasViewPositionUnknown: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
