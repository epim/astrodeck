// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w21AtlasViewNoPosition.test.tsx - WP-194 / #956 and #957: the classic Atlas's
// free-roam door and its "Recenter on mount" button while the mount has no
// position to offer, either because it does not know where it points
// (`position_known === false`, #144) or because there is no mount block at all.
//
//   Run directly:  npx tsx src/views/__tests__/w21AtlasViewNoPosition.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
//   1. THE OPENING VIEW (#956). `store.openFraming()` with no entry used to fall
//      back, with no believed mount position, to RA 0h Dec 0 whatever the viewer
//      had been looking at. It now opens on the LAST FIELD THIS VIEWER LOOKED AT
//      (kept in this browser, lib/atlasLastField), else on that one fixed field.
//   2. THE OPENING VIEW IS NOT A FUNCTION OF THE SITE. This is the load-bearing
//      case. An earlier version of this fix opened on the zenith of the saved
//      site, which is (local sidereal time, latitude): the site's latitude as
//      the view centre, and the centre leaves the rig (the opt-in online survey
//      fetch sends it to CDS; the wizard's prefill writes it into a flow's
//      TARGET; GOTO slews to it) - the #19 / #140 class. So the view is opened
//      under different saved sites, and different clocks, and must land on the
//      same centre every time.
//   3. THE NOTE (#956). A free-roam map with no scope on it says why, and that
//      the view is not centred on the scope: no mount, a mount that does not
//      know where it points, or one that has not reported a position. A framed
//      object has nothing to say about the scope.
//   4. RECENTER ON MOUNT (#957). With no mount block the button used to do
//      nothing and say nothing; it now raises the Sky hub's "has not reported a
//      position" toast and the view does not move. (The position-unknown toast
//      is graded by w20AtlasViewFreeRoamPositionUnknown.test.tsx.)
//
// Both sites below are INVENTED and are nobody's: a test file is a public
// artefact. (40 N 105 W is the one lib/__tests__/altaz.test.ts uses.)
//
// Named mutants (each is the smallest edit that puts the defect back, run from a
// byte backup and restored byte-identically; the first failing case is quoted,
// out of 38):
//   w21b_site_latitude_seed -- store.ts `openFraming`, replace
//     `lastField ?? { ra_hours: 0, dec_deg: 0 }` with
//     `lastField ?? { ra_hours: 0, dec_deg: get().site?.latitude ?? 0 }`. 29/38;
//     among the reds, "opened under two different saved sites, the classic Atlas
//     lands on the same centre: site A opened on 0 / 40, site B on 0 / -33.9" and
//     "online fetch on ... the survey request names the fixed field, whatever
//     site is saved: site A sent 0.000000 40.000000".
//   w21b_site_zenith_seed -- the same line seeded from the site's zenith
//     (`lstHours(longitude, now)`, latitude). 28/38; also red on the clock case.
//   w21b_last_field_ignored -- `readLastField()` -> `null`. 31/38: "the view opens
//     on the remembered field: the seed's RA (expected 18.25, got 0)".
//   w21b_pan_not_remembered -- `setFraming`, delete the `rememberLastField` line.
//     34/38: "a pan, nudge or pick ... is remembered: remembered: null".
//   w21b_gate_keeps_field -- `setAuthGate`, delete `forgetLastField()`. 37/38.
//   w21b_default_remembered -- remember the fixed field too. 37/38.
//   w21b_remembered_beats_mount -- the remembered field ahead of a KNOWN mount
//     position. 36/38.
//   w21b_unvalidated_readback -- atlasLastField.ts, return the parsed value
//     unchecked. 37/38.
//   w21b_storage_throw_escapes -- atlasLastField.ts `readLastField`, rethrow.
//     36/38: "storage that throws never breaks the opening".
//   w21a2_note_dropped -- AtlasView.tsx: `{!target && markerAbsent && (` ->
//     `{false && (`. 32/38.
//   w21a3_recenter_silent -- AtlasView.tsx `recenter`, delete the final
//     `enqueueToast({ ... has not reported a position. ... })` call. 36/38: "no
//     mount block: 'Recenter on mount' says why it did nothing: expected one
//     toast, got []".

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
/** The same config with the opt-in online survey fetch on: the one switch that
 *  sends the view centre off the rig (to CDS hips2fits, through the server). */
const CONFIG_ONLINE: any = { ...CONFIG, survey: { online_fetch: true } };
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
let configOnline = false;
/** Every /api/survey request the page made: its URL carries the view centre. */
const surveyRequests: string[] = [];
win.fetch = async (url: any) => {
  const path = String(url);
  if (path.includes("/api/survey")) surveyRequests.push(path);
  if (path.includes("/api/visibility")) return respond(NIGHT);
  if (path.includes("/api/config")) return respond(configOnline ? CONFIG_ONLINE : CONFIG);
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
const { LAST_FIELD_KEY } = await import("../../lib/atlasLastField");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function near(a: number | undefined, b: number, tol: number, msg: string): void {
  if (typeof a !== "number" || !(Math.abs(a - b) <= tol)) {
    throw new Error(`${msg} (expected ${b} +/- ${tol}, got ${String(a)})`);
  }
}

// ------------------------------------------------------------------ fixtures
// The mount's own reading: made up, and far from every other landing place so
// each is distinguishable. Distinctive strings, so a leak is a plain substring
// test.
const MOUNT_RA = 7.5;
const MOUNT_DEC = -35;
const RA_STR = "07h30m";
const DEC_STR = "-35d00m";
const SITE_A = { latitude: 40.0, longitude: -105.0, is_default: false, horizon_min_deg: 30 };
const SITE_B = { latitude: -33.9, longitude: 151.2, is_default: false, horizon_min_deg: 20 };
const SITE_DEFAULT = { latitude: 0, longitude: 0, is_default: true, horizon_min_deg: 15 };
const SITE_STRIPPED = { is_default: false, horizon_min_deg: 30 };
/** Every shape of "the saved site" a principal can hold. */
const SITES: [string, Record<string, unknown> | null][] = [
  ["site A", SITE_A], ["site B", SITE_B], ["the unset default", SITE_DEFAULT],
  ["coordinates withheld", SITE_STRIPPED], ["no site block", null],
];
/** The fixed field the view opens on with nothing remembered and no position. */
const DEFAULT_RA = 0;
const DEFAULT_DEC = 0;
/** A field this viewer looked at, far from the default, the mount and both sites. */
const REMEMBERED = { ra_hours: 18.25, dec_deg: -12.5 };
const FREE_ROAM_RA = 13;
const FREE_ROAM_DEC = -20;
/** A frozen instant for the store-level cases, so a clock-dependent seed is a
 *  constant and not a flake. */
const NOW_MS = 1787800000_000;

/** `mount: null` is a rig with no mount block in the status frame at all.
 *  `stored` is what this browser has remembered: `undefined` nothing, a string
 *  is written verbatim (to plant garbage), an object is written as JSON. */
function seed(
  mount: Record<string, unknown> | null,
  site: Record<string, unknown> | null,
  stored?: unknown,
): void {
  try { localStorage.removeItem(LAST_FIELD_KEY); } catch { /* none */ }
  if (stored !== undefined) {
    localStorage.setItem(LAST_FIELD_KEY, typeof stored === "string" ? stored : JSON.stringify(stored));
  }
  useStore.setState({
    status: {
      connected: { mount: mount != null }, looping: false, mode: "sim",
      ...(mount ? {
        mount: {
          ra_hours: MOUNT_RA, dec_deg: MOUNT_DEC, ra_str: RA_STR, dec_str: DEC_STR,
          alt: 62, az: 105, tracking: true, parked: false, slewing: false,
          ...mount,
        },
      } : {}),
      busy_lanes: [],
    },
    principal: { role: "operator", email: null, caps: ["view.status", "control.mount"] },
    authGate: "open",
    toasts: [],
    config: CONFIG,
    site,
    framing: null,
  } as never);
}

const storedField = (): any => {
  const raw = localStorage.getItem(LAST_FIELD_KEY);
  return raw == null ? null : JSON.parse(raw);
};

/** A free-roam session (no `target`) centred away from every candidate seed. */
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
      freeroamId: "Sky 13.00h -20.0",
    },
  } as never);
}

/** The same session with an object framed. */
function seedFramedObject(): void {
  useStore.setState({
    framing: {
      target: {
        id: "M 31", name: "Andromeda Galaxy", type: "Galaxy",
        ra_hours: 0.712, dec_deg: 41.27, mag: 3.4, size_arcmin: 190,
      },
      center: { ra_hours: 0.712, dec_deg: 41.27 },
      rotation_deg: 0,
      survey: "CDS/P/DSS2/color",
      stretch: "linear",
      fovZoomDeg: 2,
      mosaic: { rows: 1, cols: 1, overlap: 0.25 },
      panels: [],
    },
  } as never);
}

/** `openFraming()` with the clock frozen. */
function openFramingAt(nowMs: number): any {
  const realNow = Date.now;
  Date.now = () => nowMs;
  try {
    useStore.setState({ framing: null } as never);
    useStore.getState().openFraming();
  } finally {
    Date.now = realNow;
  }
  return (useStore.getState() as any).framing;
}

const M31 = {
  id: "M 31", name: "Andromeda Galaxy", type: "Galaxy",
  ra_hours: 0.712, dec_deg: 41.27, mag: 3.4, size_arcmin: 190,
} as never;

const container = win.document.getElementById("root") as any;

async function mountPage(): Promise<() => Promise<void>> {
  const root = createRoot(container);
  await act(async () => { root.render(createElement(AtlasView)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  return async () => { await act(async () => { root.unmount(); }); };
}

/** Open the classic Atlas cold (no framing) and return the session it opened. */
async function openAtlasPage(
  mount: Record<string, unknown> | null,
  site: Record<string, unknown> | null,
  stored?: unknown,
): Promise<{ framing: any; text: string; note: string | null }> {
  seed(mount, site, stored);
  const unmount = await mountPage();
  const framing = (useStore.getState() as any).framing;
  const text = container.textContent || "";
  const note = noteEl()?.textContent ?? null;
  await unmount();
  return { framing, text, note };
}

const recenterButton = (): any =>
  [...container.querySelectorAll("button")].find(
    (b: any) => /Recenter on mount/.test(b.getAttribute("aria-label") || b.textContent || ""));
const noteEl = (): any => container.querySelector('[data-testid="atlas-no-marker-note"]');
const toasts = (): any[] => (useStore.getState() as any).toasts;
const pageText = (): string => container.textContent || "";

// ============================== 1. the opening view (the store, free-roam)

test("control: a KNOWN mount position still seeds the free-roam view on the mount", () => {
  seed({ position_known: true }, SITE_A, REMEMBERED);
  const f = openFramingAt(NOW_MS);
  near(f.center.ra_hours, MOUNT_RA, 1e-9, "the seed's RA");
  near(f.center.dec_deg, MOUNT_DEC, 1e-9, "the seed's Dec");
});

test("position_known false + a remembered field: the view opens on the remembered field", () => {
  seed({ position_known: false }, SITE_A, REMEMBERED);
  const f = openFramingAt(NOW_MS);
  near(f.center.ra_hours, REMEMBERED.ra_hours, 1e-9, "the seed's RA");
  near(f.center.dec_deg, REMEMBERED.dec_deg, 1e-9, "the seed's Dec");
  assert(f.freeroamId === "Sky 18.25h -12.5°", `the session is not named for the remembered field: ${f.freeroamId}`);
});

test("no mount block + a remembered field: the view opens on the remembered field", () => {
  seed(null, SITE_A, REMEMBERED);
  const f = openFramingAt(NOW_MS);
  near(f.center.ra_hours, REMEMBERED.ra_hours, 1e-9, "the seed's RA");
  near(f.center.dec_deg, REMEMBERED.dec_deg, 1e-9, "the seed's Dec");
});

test("position_known false + nothing remembered: the view opens on the one fixed field", () => {
  seed({ position_known: false }, SITE_A);
  const f = openFramingAt(NOW_MS);
  near(f.center.ra_hours, DEFAULT_RA, 0, "the seed's RA");
  near(f.center.dec_deg, DEFAULT_DEC, 0, "the seed's Dec");
  assert(f.freeroamId === "Sky 0.00h +0.0°", `the session name: ${f.freeroamId}`);
});

test("no mount block + nothing remembered: the view opens on the one fixed field", () => {
  seed(null, SITE_A);
  const f = openFramingAt(NOW_MS);
  near(f.center.ra_hours, DEFAULT_RA, 0, "the seed's RA");
  near(f.center.dec_deg, DEFAULT_DEC, 0, "the seed's Dec");
});

test("the fixed field is a place nobody looked at, so opening on it remembers nothing", () => {
  seed({ position_known: false }, SITE_A);
  openFramingAt(NOW_MS);
  assert(storedField() === null, `the default was remembered: ${localStorage.getItem(LAST_FIELD_KEY)}`);
});

test("a KNOWN mount position beats a remembered field, and becomes the remembered field", () => {
  seed({ position_known: true }, SITE_A, REMEMBERED);
  const f = openFramingAt(NOW_MS);
  near(f.center.ra_hours, MOUNT_RA, 1e-9, "the seed's RA");
  const s = storedField();
  assert(s && s.ra_hours === MOUNT_RA && s.dec_deg === MOUNT_DEC, `remembered: ${JSON.stringify(s)}`);
});

test("an entry seeds the view on the entry whatever is remembered, and is remembered", () => {
  seed({ position_known: false }, SITE_A, REMEMBERED);
  useStore.setState({ framing: null } as never);
  useStore.getState().openFraming(M31);
  near((useStore.getState() as any).framing.center.ra_hours, 0.712, 1e-9, "the entry's RA");
  const s = storedField();
  assert(s && s.ra_hours === 0.712 && s.dec_deg === 41.27, `remembered: ${JSON.stringify(s)}`);
});

test("a remembered value that is not a sky position opens on the fixed field", () => {
  for (const bad of [
    "not json at all", "null", "[]", "{}",
    { ra_hours: 25, dec_deg: 10 }, { ra_hours: -1, dec_deg: 10 }, { ra_hours: 5, dec_deg: 91 },
    { ra_hours: "5", dec_deg: 10 }, { ra_hours: 5, dec_deg: null },
  ]) {
    seed({ position_known: false }, SITE_A, bad);
    const f = openFramingAt(NOW_MS);
    assert(f.center.ra_hours === DEFAULT_RA && f.center.dec_deg === DEFAULT_DEC,
      `${JSON.stringify(bad)} opened the view on ${f.center.ra_hours} / ${f.center.dec_deg}`);
  }
});

test("storage that throws never breaks the opening: the fixed field, then an entry", () => {
  seed({ position_known: false }, SITE_A, REMEMBERED);
  const real = Object.getOwnPropertyDescriptor(g, "localStorage") as PropertyDescriptor;
  const boom = () => { throw new Error("storage blocked"); };
  Object.defineProperty(g, "localStorage", {
    value: { getItem: boom, setItem: boom, removeItem: boom }, writable: true, configurable: true,
  });
  try {
    const f = openFramingAt(NOW_MS);
    assert(f.center.ra_hours === DEFAULT_RA && f.center.dec_deg === DEFAULT_DEC,
      `blocked storage opened the view on ${f.center.ra_hours} / ${f.center.dec_deg}`);
    useStore.setState({ framing: null } as never);
    useStore.getState().openFraming(M31);
    useStore.getState().setFraming({ center: { ra_hours: 3, dec_deg: 4 } });
    near((useStore.getState() as any).framing.center.ra_hours, 3, 0, "the view still moves with storage blocked");
  } finally {
    Object.defineProperty(g, "localStorage", real);
  }
});

// ------------------------------------------- the field is what was last viewed
test("a pan, nudge or pick (setFraming with a centre) is remembered; other patches are not", () => {
  seed({ position_known: false }, SITE_A);
  openFramingAt(NOW_MS);
  useStore.getState().setFraming({ center: { ra_hours: 3.5, dec_deg: 61 } });
  const s = storedField();
  assert(s && s.ra_hours === 3.5 && s.dec_deg === 61, `remembered: ${JSON.stringify(s)}`);
  useStore.getState().setFraming({ rotation_deg: 40, fovZoomDeg: 5 });
  const s2 = storedField();
  assert(s2 && s2.ra_hours === 3.5 && s2.dec_deg === 61, `a non-centre patch changed it: ${JSON.stringify(s2)}`);
});

test("an RA a drag carries past 24h is folded into [0, 24) before it is remembered", () => {
  seed({ position_known: false }, SITE_A);
  openFramingAt(NOW_MS);
  useStore.getState().setFraming({ center: { ra_hours: 24.5, dec_deg: 10 } });
  near(storedField()?.ra_hours, 0.5, 1e-9, "the remembered RA");
});

test("setFraming with no session open remembers nothing", () => {
  seed({ position_known: false }, SITE_A);
  useStore.getState().setFraming({ center: { ra_hours: 3.5, dec_deg: 61 } });
  assert(storedField() === null, `remembered with no session: ${localStorage.getItem(LAST_FIELD_KEY)}`);
});

test("the round trip: look somewhere, lose the session, open again with no position: same field", () => {
  seed({ position_known: false }, SITE_A);
  openFramingAt(NOW_MS);
  useStore.getState().setFraming({ center: { ra_hours: 3.5, dec_deg: 61 } });
  useStore.setState({ framing: null } as never);          // a reload
  const f = openFramingAt(NOW_MS + 86_400_000);
  near(f.center.ra_hours, 3.5, 1e-9, "the reopened view's RA");
  near(f.center.dec_deg, 61, 1e-9, "the reopened view's Dec");
});

test("the sign-in gate closing over a session forgets the remembered field", () => {
  seed({ position_known: false }, SITE_A, REMEMBERED);
  assert(storedField() != null, "the fixture did not store a field");
  useStore.getState().setAuthGate("login");
  assert(storedField() === null, `the field survived the gate: ${localStorage.getItem(LAST_FIELD_KEY)}`);
  useStore.setState({ authGate: "open" } as never);
});

// ================ 2. the opening view is NOT a function of the site (#956)
//
// The centre of the view is what the online survey fetch, the wizard's prefill
// and GOTO carry. If it depended on the saved site it would carry the site.

test("not a function of the site: every shape of saved site opens the same centre, nothing remembered", () => {
  let first: any = null;
  for (const [label, site] of SITES) {
    seed({ position_known: false }, site);
    const f = openFramingAt(NOW_MS);
    const c = `${f.center.ra_hours} / ${f.center.dec_deg} / ${f.freeroamId}`;
    if (first === null) first = c;
    assert(c === first, `${label} opened on ${c}, the first site on ${first}`);
    assert(f.center.ra_hours === DEFAULT_RA && f.center.dec_deg === DEFAULT_DEC, `${label}: ${c}`);
  }
});

test("not a function of the site: every shape of saved site opens the same centre, a field remembered", () => {
  for (const [label, site] of SITES) {
    seed(null, site, REMEMBERED);
    const f = openFramingAt(NOW_MS);
    assert(f.center.ra_hours === REMEMBERED.ra_hours && f.center.dec_deg === REMEMBERED.dec_deg,
      `${label} opened on ${f.center.ra_hours} / ${f.center.dec_deg}`);
  }
});

test("not a function of the clock either: the centre does not move with the sidereal time", () => {
  const seen = new Set<string>();
  for (const dt of [0, 3600_000, 6 * 3600_000, 86_164_000 / 2]) {
    seed({ position_known: false }, SITE_A);
    const f = openFramingAt(NOW_MS + dt);
    seen.add(`${f.center.ra_hours} / ${f.center.dec_deg}`);
  }
  assert(seen.size === 1, `the opening centre moved with the clock: ${[...seen].join(" | ")}`);
});

test("neither coordinate of the opening centre is the site's latitude or its longitude", () => {
  for (const site of [SITE_A, SITE_B]) {
    seed({ position_known: false }, site);
    const f = openFramingAt(NOW_MS);
    for (const v of [f.center.ra_hours, f.center.dec_deg]) {
      assert(v !== site.latitude && v !== site.longitude && Math.abs(v) !== Math.abs(site.latitude as number),
        `the centre carries a site coordinate: ${v}`);
    }
  }
});

// ============================ 2b. the classic Atlas door, under two sites
{
  const a = await openAtlasPage({ position_known: false }, SITE_A);
  const b = await openAtlasPage({ position_known: false }, SITE_B);
  test("position_known false, nothing remembered: the classic Atlas opens on the fixed field", () => {
    assert(a.framing != null, "the Atlas opened no free-roam session at all - the fixture never rendered");
    near(a.framing.center.ra_hours, DEFAULT_RA, 0, "the Atlas's opening RA");
    near(a.framing.center.dec_deg, DEFAULT_DEC, 0, "the Atlas's opening Dec");
  });
  test("position_known false: opened under two different saved sites, the classic Atlas lands on the same centre", () => {
    assert(b.framing != null, "the Atlas opened no free-roam session at all - the fixture never rendered");
    assert(a.framing.center.ra_hours === b.framing.center.ra_hours
      && a.framing.center.dec_deg === b.framing.center.dec_deg,
      `site A opened on ${a.framing.center.ra_hours} / ${a.framing.center.dec_deg}, `
      + `site B on ${b.framing.center.ra_hours} / ${b.framing.center.dec_deg}`);
    assert(a.framing.freeroamId === b.framing.freeroamId, "the two sessions are named differently");
  });
}
{
  const a = await openAtlasPage(null, SITE_A, REMEMBERED);
  const b = await openAtlasPage(null, SITE_B, REMEMBERED);
  test("no mount block, a field remembered: the classic Atlas opens on it, whichever site is saved", () => {
    assert(a.framing != null && b.framing != null, "the Atlas opened no free-roam session at all");
    for (const f of [a.framing, b.framing]) {
      near(f.center.ra_hours, REMEMBERED.ra_hours, 1e-9, "the Atlas's opening RA");
      near(f.center.dec_deg, REMEMBERED.dec_deg, 1e-9, "the Atlas's opening Dec");
    }
  });
}
{
  // The remembered field is what was LAST VIEWED: pan on the page, leave, and
  // the next cold open is there.
  seed({ position_known: false }, SITE_A);
  const unmount = await mountPage();
  await act(async () => {
    useStore.getState().setFraming({ center: { ra_hours: 9.25, dec_deg: -47 } });
  });
  await unmount();
  const remembered = storedField();
  const again = await openAtlasPage({ position_known: false }, SITE_B, remembered);
  test("the classic Atlas round trip: the field panned to is the field the next cold open lands on", () => {
    near(remembered?.ra_hours, 9.25, 1e-9, "the remembered RA");
    near(again.framing?.center.ra_hours, 9.25, 1e-9, "the reopened RA");
    near(again.framing?.center.dec_deg, -47, 1e-9, "the reopened Dec");
  });
}

// ===== 2c. the one request that leaves the rig: the online survey fetch
//
// With `survey.online_fetch` on, the page asks the server for a cutout centred
// on the view, and the server asks CDS. The request's ra and dec ARE the view
// centre, so they must not differ between two rigs that differ only in site.

/** The (ra, dec) of every survey request, as the URL spells them. */
function surveyCentres(): string[] {
  return surveyRequests.map((u) => {
    const q = new URL(u, "http://local").searchParams;
    return `${q.get("ra")} ${q.get("dec")}`;
  });
}

/** Open the Atlas cold with online fetch on and collect what it asked for. */
async function surveyAsked(site: Record<string, unknown> | null, stored?: unknown): Promise<string[]> {
  configOnline = true;
  try {
    seed({ position_known: false }, site, stored);
    useStore.setState({ config: CONFIG_ONLINE } as never);
    surveyRequests.length = 0;
    const unmount = await mountPage();
    await act(async () => { await new Promise((r) => setTimeout(r, 700)); });      // the 300 ms debounce
    await unmount();
    return surveyCentres();
  } finally {
    configOnline = false;
  }
}
{
  const a = await surveyAsked(SITE_A);
  const b = await surveyAsked(SITE_B);
  test("online fetch on, nothing remembered: the survey request names the fixed field, whatever site is saved", () => {
    assert(a.length > 0 && b.length > 0, `no survey request was made (${a.length} / ${b.length}) - the fixture never reached the fetch`);
    const fixed = "0.000000 0.000000";
    assert(a.every((c) => c === fixed), `site A sent ${a.join(" | ")}`);
    assert(b.every((c) => c === fixed), `site B sent ${b.join(" | ")}`);
  });
  const ra = await surveyAsked(SITE_A, REMEMBERED);
  const rb = await surveyAsked(SITE_B, REMEMBERED);
  test("online fetch on, a field remembered: the survey request names the remembered field, whatever site is saved", () => {
    assert(ra.length > 0 && rb.length > 0, `no survey request was made (${ra.length} / ${rb.length})`);
    const want = `${REMEMBERED.ra_hours.toFixed(6)} ${REMEMBERED.dec_deg.toFixed(6)}`;
    assert(ra.every((c) => c === want), `site A sent ${ra.join(" | ")}`);
    assert(rb.every((c) => c === want), `site B sent ${rb.join(" | ")}`);
  });
}

// ================================== 3. the note under a free-roam map (page)
{
  seed({ position_known: true }, SITE_A);
  seedFreeRoam();
  const unmount = await mountPage();
  test("control: a mount that knows where it points leaves no note under the map", () => {
    assert(recenterButton() != null, "no free-roam page - the fixture never rendered");
    assert(noteEl() === null, `a note under the map for a drawn scope: ${noteEl()?.textContent}`);
  });
  await unmount();
}
{
  seed(null, SITE_A);
  seedFreeRoam();
  const unmount = await mountPage();
  test("no mount block: the map says no mount is connected, so the view is not on the scope", () => {
    assert(recenterButton() != null, "no free-roam page - the fixture never rendered");
    const n = noteEl();
    assert(n != null, "no note says why the scope is not on the map");
    const t = n.textContent || "";
    assert(/No mount is connected/.test(t), `the note does not name the missing mount: ${t}`);
    assert(/not centred on the scope/.test(t), `the note does not say the view is not on the scope: ${t}`);
    assert(!/did not open/.test(t), `the note claims how the view opened, which nothing keeps: ${t}`);
  });
  await unmount();
}
{
  // Cold open, through the door: the note is on the page the Atlas opened.
  const r = await openAtlasPage({ position_known: false }, SITE_A);
  test("position_known false, opened cold: the note says the position is unknown and the view is not on the scope", () => {
    assert(r.note != null, "no note on the Atlas the free-roam door opened");
    const note = r.note ?? "";
    assert(/mount position is unknown/.test(note), `the note does not name the cause: ${note}`);
    assert(/not centred on the scope/.test(note), `the note does not say the view is not on the scope: ${note}`);
    assert(note.includes(POSITION_UNKNOWN_COPY_DETAIL), `the note drops the shared reason: ${note}`);
    assert(!/did not open/.test(note), `the note claims how the view opened: ${note}`);
    assert(!r.text.includes(RA_STR) && !r.text.includes(DEC_STR),
      `the mount's home reading is printed on the page: ${r.text.slice(0, 300)}`);
  });
}
{
  // The note is the same whether the view opened on a remembered field or on
  // the fixed one: it is about the scope, not about the opening.
  const r = await openAtlasPage({ position_known: false }, SITE_A, REMEMBERED);
  test("position_known false, a field remembered: the same note", () => {
    assert(r.note != null && /mount position is unknown/.test(r.note) && /not centred on the scope/.test(r.note),
      `the note: ${r.note}`);
  });
}
{
  seed({ position_known: true, ra_hours: undefined, dec_deg: undefined }, SITE_A);
  seedFreeRoam();
  const unmount = await mountPage();
  test("a mount block with no readable RA/Dec: the map says the mount has not reported a position", () => {
    assert(recenterButton() != null, "no free-roam page - the fixture never rendered");
    const n = noteEl();
    assert(n != null, "no note says why the scope is not on the map");
    assert(/has not reported a position/.test(n.textContent || ""), `the note: ${n.textContent}`);
    assert(/not centred on the scope/.test(n.textContent || ""), `the note: ${n.textContent}`);
    assert(!/did not open/.test(n.textContent || ""), `the note claims how the view opened: ${n.textContent}`);
  });
  await unmount();
}
{
  // An engine older than #144 sends no `position_known` at all, and a mount
  // block whose RA/Dec cannot be read is not a pointing either. No flag is not
  // `false`: the cause is that the mount has not reported, and TRUST POSITION is
  // advice for a mount that said it does not know.
  seed({ ra_hours: undefined, dec_deg: undefined }, SITE_A);
  seedFreeRoam();
  const unmount = await mountPage();
  test("no position_known flag and no readable RA/Dec: the note says the mount has not reported, with no TRUST POSITION advice", () => {
    assert(recenterButton() != null, "no free-roam page - the fixture never rendered");
    const n = noteEl();
    assert(n != null, "no note says why the scope is not on the map");
    const t = n.textContent || "";
    assert(/has not reported a position/.test(t), `the note does not name the cause: ${t}`);
    assert(!t.includes(POSITION_UNKNOWN_COPY_DETAIL) && !/TRUST POSITION/.test(t),
      `TRUST POSITION advice for a mount that never said it does not know: ${t}`);
    assert(!/position is unknown/.test(t), `the note says the position is unknown: ${t}`);
  });
  await unmount();
}
{
  // The cause is read LIVE, so a mount that loses its position mid-session
  // changes the note and the note stays true: it says nothing about how the
  // view opened, because the session keeps no record of that.
  seed({ position_known: true }, SITE_A);
  seedFreeRoam();
  const unmount = await mountPage();
  const before = noteEl();
  await act(async () => {
    useStore.setState({
      status: { ...(useStore.getState() as any).status, mount: { ...(useStore.getState() as any).status.mount, position_known: false } },
    } as never);
  });
  test("a mount that loses its position mid-session: the note appears, names the cause, and claims nothing about the opening", () => {
    assert(before === null, "a note while the scope was drawn");
    const n = noteEl();
    assert(n != null, "no note after the mount lost its position");
    const t = n.textContent || "";
    assert(/mount position is unknown/.test(t), `the note: ${t}`);
    assert(!/did not open/.test(t), `the note says the view did not open on the scope, but it did: ${t}`);
  });
  await unmount();
}
{
  seed({ position_known: false }, SITE_A);
  seedFramedObject();
  const unmount = await mountPage();
  test("a framed object has no scope note: the view is where it is because of the object", () => {
    assert(/Andromeda Galaxy/.test(pageText()), "the framed object is not on the page - the fixture never rendered");
    assert(noteEl() === null, `a free-roam note on a framed object: ${noteEl()?.textContent}`);
  });
  await unmount();
}

// ================================ 4. RECENTER ON MOUNT with no mount (#957)
{
  seed(null, SITE_A);
  seedFreeRoam();
  const unmount = await mountPage();
  const btn = recenterButton();
  test("the 'Recenter on mount' button is on the page with no mount block", () => {
    assert(btn != null, "no 'Recenter on mount' button - the fixture is wrong, not the page");
  });
  if (btn) {
    await act(async () => { btn.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
    test("no mount block: 'Recenter on mount' says why it did nothing", () => {
      const t = toasts();
      assert(t.length === 1, `expected one toast, got ${JSON.stringify(t)}`);
      assert(/Nothing to recenter on/.test(t[0].title), `the toast title: ${t[0].title}`);
      assert(/has not reported a position/.test(t[0].title), `the toast gives no reason: ${t[0].title}`);
      assert(t[0].level === "warning", `the toast level: ${t[0].level}`);
    });
    test("no mount block: 'Recenter on mount' does not move the view", () => {
      const f = (useStore.getState() as any).framing;
      near(f.center.ra_hours, FREE_ROAM_RA, 1e-9, "the view's RA after the press");
      near(f.center.dec_deg, FREE_ROAM_DEC, 1e-9, "the view's Dec after the press");
    });
  }
  await unmount();
}
{
  seed({ position_known: true, ra_hours: undefined, dec_deg: undefined }, SITE_A);
  seedFreeRoam();
  const unmount = await mountPage();
  const btn = recenterButton();
  if (btn) {
    await act(async () => { btn.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
    test("a mount block with no readable RA/Dec: 'Recenter on mount' says why it did nothing", () => {
      const t = toasts();
      assert(t.length === 1, `expected one toast, got ${JSON.stringify(t)}`);
      assert(/has not reported a position/.test(t[0].title), `the toast title: ${t[0].title}`);
    });
  } else {
    test("the 'Recenter on mount' button is on the page with an unreadable mount block", () => {
      throw new Error("no 'Recenter on mount' button - the fixture is wrong, not the page");
    });
  }
  await unmount();
}
{
  seed({ position_known: true }, SITE_A);
  seedFreeRoam();
  const unmount = await mountPage();
  const btn = recenterButton();
  if (btn) {
    await act(async () => { btn.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
    test("control: a KNOWN position recenters on the mount with no toast", () => {
      const f = (useStore.getState() as any).framing;
      near(f.center.ra_hours, MOUNT_RA, 1e-9, "the recentred RA");
      near(f.center.dec_deg, MOUNT_DEC, 1e-9, "the recentred Dec");
      assert(toasts().length === 0, `a toast for a good recentre: ${JSON.stringify(toasts())}`);
    });
  }
  await unmount();
}

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`w21AtlasViewNoPosition: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
