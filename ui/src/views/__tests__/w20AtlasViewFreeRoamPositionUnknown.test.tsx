// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w20AtlasViewFreeRoamPositionUnknown.test.tsx - WP-173 / #928: the two places
// the classic Atlas lands its view on the mount's RA/Dec, while the mount does
// not know where it points.
//
//   Run directly:  npx tsx src/views/__tests__/w20AtlasViewFreeRoamPositionUnknown.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
//   1. THE FREE-ROAM SEED. `store.openFraming()` with no entry centres the
//      session on the mount and names it from that centre ("Sky 0.71h +41.3"),
//      so the Atlas, and the Sky hub through the same door, opened a free-roam
//      view at the pole when the mount was reporting its home position.
//   2. RECENTER ON MOUNT. In a free-roam session the "Recenter on mount" button
//      copied the mount's RA/Dec into the view. It now refuses out loud, with
//      the position-unknown reason, and the view does not move.
//
// A mount that does not know where it points (`position_known === false`, #144)
// reports its HOME position, the pole, wherever the tube is (#913, #928).
//
// Named mutants (each puts the raw reading back):
//   w20a1_seed_raw -- in store.ts `openFraming`, replace
//     `const here = believedRaDec(get().status?.mount);` with
//     `const here = get().status?.mount;`.
//   w20a2_recenter_raw -- in AtlasView.tsx `recenter`, replace
//     `const here = believedRaDec(m);` with `const here = m;`.

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
const { POSITION_UNKNOWN_COPY_DETAIL } = await import("../../lib/slewController");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function near(a: number | undefined, b: number, msg: string): void {
  if (typeof a !== "number" || Math.abs(a - b) > 1e-9) {
    throw new Error(`${msg} (expected ${b}, got ${String(a)})`);
  }
}

// ------------------------------------------------------------------ fixtures
// The mount's own reading: made up, and far from FREE_ROAM_RA so a move is
// unmistakable. At a known position the view lands here.
const MOUNT_RA = 0.712;
const MOUNT_DEC = 41.27;
const FREE_ROAM_RA = 13;
const FREE_ROAM_DEC = -20;

function seedMount(mountExtra: Record<string, unknown>): void {
  useStore.setState({
    status: {
      connected: { mount: true }, looping: false, mode: "sim",
      mount: {
        ra_hours: MOUNT_RA, dec_deg: MOUNT_DEC, ra_str: "00h42m", dec_str: "+41d16m",
        alt: 62, az: 105, tracking: true, parked: false, slewing: false,
        ...mountExtra,
      },
      busy_lanes: [],
    },
    principal: { role: "operator", email: null, caps: ["view.status", "control.mount"] },
    authGate: "open",
    toasts: [],
    config: CONFIG,
    site: { horizon_min_deg: 30, is_default: false },
  } as never);
}

/** A free-roam session (no `target`) centred well away from the mount. */
function seedFreeRoam(): void {
  useStore.setState({
    framing: {
      target: undefined,
      center: { ra_hours: FREE_ROAM_RA, dec_deg: FREE_ROAM_DEC },
      rotation_deg: 0,
      survey: "CDS/P/DSS2/color",
      stretch: "linear",
      fovZoomDeg: 2,
      mosaic: { rows: 1, cols: 1, overlap: 0.25 },
      panels: [],
      freeroamId: "Sky 13.00h -20.0°",
    },
  } as never);
}

const container = win.document.getElementById("root") as any;

async function mountPage(): Promise<() => Promise<void>> {
  const root = createRoot(container);
  await act(async () => { root.render(createElement(AtlasView)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  return async () => { await act(async () => { root.unmount(); }); };
}

const recenterButton = (): any =>
  [...container.querySelectorAll("button")].find(
    (b: any) => /Recenter on mount/.test(b.getAttribute("aria-label") || b.textContent || ""));
const toasts = (): any[] => (useStore.getState() as any).toasts;

// ============================================ 1. the free-roam seed (store)
test("control: openFraming() with no entry seeds the free-roam view on a KNOWN mount position", () => {
  seedMount({ position_known: true });
  useStore.setState({ framing: null } as never);
  useStore.getState().openFraming();
  const f = (useStore.getState() as any).framing;
  near(f.center.ra_hours, MOUNT_RA, "the seed's RA");
  near(f.center.dec_deg, MOUNT_DEC, "the seed's Dec");
  assert(/^Sky 0\.71h \+41\.3/.test(f.freeroamId), `the session id: ${f.freeroamId}`);
});

test("control: an ABSENT flag (an engine older than #144) seeds on the mount", () => {
  seedMount({});
  useStore.setState({ framing: null } as never);
  useStore.getState().openFraming();
  near((useStore.getState() as any).framing.center.ra_hours, MOUNT_RA, "the seed's RA");
});

test("position_known false: the free-roam seed is not the mount's home reading", () => {
  seedMount({ position_known: false });
  useStore.setState({ framing: null } as never);
  useStore.getState().openFraming();
  const f = (useStore.getState() as any).framing;
  assert(f.center.ra_hours !== MOUNT_RA && f.center.dec_deg !== MOUNT_DEC,
    `the free-roam view opened on the mount's home reading (${f.center.ra_hours}h ${f.center.dec_deg})`);
  assert(!/0\.71h|41\.3/.test(f.freeroamId),
    `the session is named after the mount's home reading: ${f.freeroamId}`);
});

test("position_known false: an entry still seeds the view on the entry", () => {
  seedMount({ position_known: false });
  useStore.setState({ framing: null } as never);
  useStore.getState().openFraming({
    id: "M 31", name: "Andromeda Galaxy", type: "Galaxy",
    ra_hours: 0.712, dec_deg: 41.27, mag: 3.4, size_arcmin: 190,
  } as never);
  near((useStore.getState() as any).framing.center.ra_hours, 0.712, "the entry's RA");
});

// =================================== 2. the classic Atlas opens free-roam
{
  seedMount({ position_known: false });
  useStore.setState({ framing: null } as never);
  const unmount = await mountPage();
  const f = (useStore.getState() as any).framing;
  test("position_known false: the classic Atlas opens its free-roam view away from the home reading", () => {
    assert(f != null, "the Atlas opened no free-roam session at all - the fixture never rendered");
    assert(f.center.ra_hours !== MOUNT_RA && f.center.dec_deg !== MOUNT_DEC,
      `the Atlas opened its free-roam view on the mount's home reading (${f.center.ra_hours}h)`);
  });
  await unmount();
}

// ========================================== 3. RECENTER ON MOUNT (free-roam)
{
  seedMount({ position_known: true });
  seedFreeRoam();
  const unmount = await mountPage();
  const btn = recenterButton();
  test("the 'Recenter on mount' button is on the page (a free-roam session)", () => {
    assert(btn != null, "no 'Recenter on mount' button - the fixture is wrong, not the page");
  });
  if (btn) {
    await act(async () => { btn.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
    test("control: a KNOWN position recenters the view on the mount, with no toast", () => {
      const f = (useStore.getState() as any).framing;
      near(f.center.ra_hours, MOUNT_RA, "the recentred RA");
      near(f.center.dec_deg, MOUNT_DEC, "the recentred Dec");
      assert(toasts().length === 0, `a toast was raised for a good recentre: ${JSON.stringify(toasts())}`);
    });
  }
  await unmount();
}
{
  seedMount({ position_known: false });
  seedFreeRoam();
  const unmount = await mountPage();
  const btn = recenterButton();
  test("the 'Recenter on mount' button is still on the page while the position is unknown", () => {
    assert(btn != null, "no 'Recenter on mount' button - the fixture is wrong, not the page");
  });
  if (btn) {
    await act(async () => { btn.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
    test("position_known false: 'Recenter on mount' does not move the view to the home reading", () => {
      const f = (useStore.getState() as any).framing;
      near(f.center.ra_hours, FREE_ROAM_RA, "the view's RA after the press");
      near(f.center.dec_deg, FREE_ROAM_DEC, "the view's Dec after the press");
    });
    test("position_known false: 'Recenter on mount' says why, with the position-unknown reason", () => {
      const t = toasts();
      assert(t.length === 1, `expected one toast, got ${JSON.stringify(t)}`);
      assert(/Nothing to recenter on/.test(t[0].title), `the toast title: ${t[0].title}`);
      assert(/does not know where it points/.test(t[0].title), `the toast gives no reason: ${t[0].title}`);
      assert(t[0].detail === POSITION_UNKNOWN_COPY_DETAIL, `the toast detail: ${t[0].detail}`);
    });
  }
  await unmount();
}

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`w20AtlasViewFreeRoamPositionUnknown: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
