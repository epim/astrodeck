// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// quickImagesCatalogue.test.tsx - the quick sheet says where GENERATE FLOW
// images when a kept framing is centred somewhere else (#459), MOUNTED.
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/next/hubs/sky/__tests__/quickImagesCatalogue.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE CLAIM NOTHING KEPT. The FRAME card's note and the "Framing kept" toast
// said a kept framing's CENTRE went into the quick flow. GENERATE FLOW builds
// its target from the catalogue row (or the coordinates the sheet was opened
// with) and reads the framing for the ANGLE only, so a framing dragged off the
// object was imaged at the catalogue position under two sentences saying
// otherwise. S6 took the sentences out; #459's second shape - taken here, and
// changing no behaviour - has the sheet say it: GENERATE FLOW images the
// catalogue position, and SEND TO FLOW WIZARD (shown beside it) carries the
// framing. Whether GENERATE FLOW should take the centre instead waits on an
// owner ruling, recorded on #459.
//
// WHAT IS HELD:
//   1. A kept single frame dragged off M31: the line shows, in
//      `framingCentreNote`'s words with the measured offset, SEND TO FLOW
//      WIZARD is offered beside it and carries the dragged centre, and the
//      quick POST's target ra/dec are the CATALOGUE's, unchanged.
//   2. Controls: an undragged kept framing, and one dragged by less than the
//      payload rounds to (the same posted coordinates), say nothing and offer
//      no wizard; a framing merely open on M31 (nothing kept) says nothing.
//   3. A patch (opened with coordinates) names the position the sheet opened
//      with, not a catalogue position it does not have.
//
// MUTATION RECORD: see the block at the foot of this file.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/#/sky/quick?target=m31", pretendToBeVisual: true },
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

win.localStorage.setItem("astrodeck-next-sky-quick", JSON.stringify({ hours: 6 }));

// ------------------------------------------------------------- fetch recorder
interface Ask { url: string; method: string; body: any }
const asks: Ask[] = [];
const M31 = {
  id: "m31", name: "Andromeda Galaxy", type: "Galaxy", kind: "dso",
  ra_hours: 0.712305, dec_deg: 41.26917, mag: 3.4, size_arcmin: 190,
  difficulty: "easy",
};
const flowRecord = () => ({
  id: "f1", name: "Quick session: Andromeda Galaxy", folder: "My flows",
  tagline: "", graph: {
    nodes: [{ id: "n3", type: "target", x: 0, y: 0, params: { name: "M31", ra: "00h 42m 44s", dec: "+41° 16′ 09″", rotation: -1 } }],
    edges: [],
  },
  created_ts: 0, updated_ts: 0, last_run: null, last_result: "", readonly: false,
});

g.fetch = async (url: any, init: any) => {
  const u = String(url);
  const method = (init?.method ?? "GET").toUpperCase();
  let body: any = null;
  if (init?.body) { try { body = JSON.parse(init.body); } catch { body = init.body; } }
  asks.push({ url: u, method, body });
  const ok = (data: any) => ({ ok: true, status: 200, statusText: "OK", json: async () => data });
  if (u.includes("/api/catalog?q=")) return ok({ results: [M31], notes: [] });
  if (u.includes("/api/visibility")) return ok({});
  if (u.includes("/api/flows/quick")) return ok({ flow: flowRecord(), started: false });
  if (u.includes("/api/flows/compile")) return ok({ plan: {}, structural: [], issues: [], unmapped: [] });
  if (u.includes("/api/flows/f1")) return ok(flowRecord());
  return ok({});
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../store");
const { QuickSessionSheet } = await import("../sheets/quick");
const { SEND_TO_WIZARD, framingCentreNote } = await import("../sheets/quickCopy");
const { parseHash } = await import("../../../router");
const { DEFAULT_OVERLAP } = await import("../../../../lib/framing");
const { angularSepDeg } = await import("../../../../lib/atlasFov");
const { raHms, decDms } = await import("../../session/flows/create/quickPayload");

// ------------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function testAsync(name: string, fn: () => Promise<void> | void): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg} (expected ${JSON.stringify(want)}, got ${JSON.stringify(got)})`);
}
function near(got: number, want: number, tol: number, msg: string): void {
  if (!(Math.abs(got - want) <= tol)) throw new Error(`${msg} (expected ${want} +/- ${tol}, got ${got})`);
}
const settle = async () => {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 400)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
};

const container = win.document.getElementById("root") as any;
const q = (sel: string) => container.querySelector(sel) as any;
const line = () => q('[data-testid="quick-framing-centre"]');
const wizardButton = () => q('[data-testid="quick-send-to-wizard"]');

const OPERATOR = {
  role: "operator", email: "op@rig",
  caps: ["view.status", "view.preview", "view.weather", "view.site_derived",
    "control.capture", "control.mount", "control.guide"],
};

/** Dragged off the catalogue position by 13.85 arcmin in Dec and 0.0085 h in RA,
 *  which is 5.76 arcmin of sky at Dec 41.4: 15.0 arcmin in all. */
const DRAGGED = { ra_hours: 0.7208333, dec_deg: 41.5 };

/** A framing on M31 at `center`, KEPT (one engine panel, as FRAME's DONE keeps
 *  a single frame) unless `kept` is false. */
function m31Framing(center: { ra_hours: number; dec_deg: number }, kept = true) {
  return {
    target: { id: "m31", name: "Andromeda Galaxy", type: "Galaxy", ra_hours: M31.ra_hours, dec_deg: M31.dec_deg, mag: 3.4, size_arcmin: 190 },
    center,
    rotation_deg: 30,
    survey: "CDS/P/DSS2/color",
    stretch: "linear",
    fovZoomDeg: 2,
    mosaic: { rows: 1, cols: 1, overlap: DEFAULT_OVERLAP },
    panels: kept ? [{ row: 0, col: 0, ra_hours: center.ra_hours, dec_deg: center.dec_deg, rotation_deg: 30 }] : [],
  };
}

function seed(framing: unknown): void {
  act(() => {
    useStore.setState({
      principal: OPERATOR,
      equipConnected: true,
      wsPhase: "up",
      site: { name: "Back lawn", latitude: 47.6, longitude: -122.3, elevation_m: 50, is_default: false, horizon_min_deg: 20 },
      status: {
        connected: { camera: { connected: true, name: "sim" }, telescope: { connected: true, name: "sim" } },
        filterwheel: {
          position: 0, names: ["L", "R", "G", "B"],
          opaque: [false, false, false, false], narrowband: [false, false, false, false],
          exposures: [60, 60, 60, 60],
        },
      },
      config: { optics: { focal_length_mm: 1000, pixel_size_um: 3.76, sensor_width_px: 6248, sensor_height_px: 4176 } },
      weather: null,
      framing,
      frameSettings: {
        capture: { exposure_s: 60, gain: 100, offset: 30, binning: 1, filter: null },
        focus: { exposure_s: 2, gain: 200, offset: 30, binning: 1, filter: null },
        solve: { exposure_s: 0.3, gain: 200, offset: 30, binning: 1, filter: null },
        guide: { exposure_s: 2, gain: 100, offset: 30, binning: 1, filter: null },
      },
    } as never);
  });
}

async function mount(framing: unknown, params: Record<string, string>): Promise<{ unmount: () => void }> {
  seed(framing);
  const qs = new URLSearchParams(params).toString();
  win.location.hash = `#/sky/quick?${qs}`;
  const root = createRoot(container);
  await act(async () => {
    root.render(createElement(QuickSessionSheet, { params, depth: 0 as const }));
  });
  await settle();
  assert(q('[data-testid="sky-quick"]') != null, "no sky-quick marker: the fixture is wrong, not the component");
  return { unmount: () => act(() => { root.unmount(); }) };
}

const press = async (testid: string): Promise<void> => {
  const el = q(`[data-testid="${testid}"]`);
  assert(el != null, `no ${testid} to press - the fixture is wrong, not the component`);
  await act(async () => {
    el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
  });
  await settle();
};
const quickPosts = () => asks.filter((a) => a.method === "POST" && a.url.includes("/api/flows/quick"));

// ============================ 1. a kept single frame, dragged off the object
{
  const m = await mount(m31Framing(DRAGGED), { target: "m31" });

  await testAsync("a kept framing dragged off M31: the sheet says GENERATE FLOW images the catalogue position", () => {
    const el = line();
    assert(el != null, "a framing dragged off the object produced no word about where GENERATE FLOW images");
    const off = angularSepDeg(DRAGGED, { ra_hours: M31.ra_hours, dec_deg: M31.dec_deg });
    eq(el.textContent, framingCentreNote(off, "Andromeda Galaxy", true), "the line is not framingCentreNote's words:");
    assert(/GENERATE FLOW images the catalogue position/.test(el.textContent),
      `the line does not say where GENERATE FLOW images: "${el.textContent}"`);
    assert(el.textContent.includes(SEND_TO_WIZARD), `the line does not name the door that carries the framing: "${el.textContent}"`);
    near(off * 60, 15.0, 0.05, "precondition: the drag is 15 arcmin");
    assert(/ 15′ /.test(el.textContent), `the line lost the measured offset (15 arcmin): "${el.textContent}"`);
  });

  await testAsync("SEND TO FLOW WIZARD is offered beside the line, and carries the dragged centre", async () => {
    const b = wizardButton();
    assert(b != null, "the line names SEND TO FLOW WIZARD and the sheet does not offer it for a single frame");
    eq(b.textContent, SEND_TO_WIZARD, "the button's label:");
    const hash0 = String(win.location.hash);
    await press("quick-send-to-wizard");
    const r = parseHash(String(win.location.hash));
    eq(r.sheets.join("/"), "quick/flowWizard", `the wizard did not open over the quick sheet: "${win.location.hash}"`);
    eq(r.params.wz_ra, raHms(DRAGGED.ra_hours), "the wizard was not handed the framing's RA:");
    eq(r.params.wz_dec, decDms(DRAGGED.dec_deg), "the wizard was not handed the framing's Dec:");
    act(() => { win.location.hash = hash0; });
  });

  await testAsync("GENERATE FLOW still posts the catalogue position, not the framing's centre (no behaviour changed)", async () => {
    await press("quick-generate");
    const posts = quickPosts();
    eq(posts.length, 1, "GENERATE FLOW did not post the quick route exactly once:");
    eq(posts[0].body?.target?.ra, raHms(M31.ra_hours), "the quick POST's RA moved off the catalogue's:");
    eq(posts[0].body?.target?.dec, decDms(M31.dec_deg), "the quick POST's Dec moved off the catalogue's:");
    assert(posts[0].body.target.ra !== raHms(DRAGGED.ra_hours) && posts[0].body.target.dec !== decDms(DRAGGED.dec_deg),
      "precondition: the dragged centre posts differently from the catalogue position");
  });

  m.unmount();
}

// ============================================================== 2. controls
{
  const m = await mount(m31Framing({ ra_hours: M31.ra_hours, dec_deg: M31.dec_deg }), { target: "m31" });
  await testAsync("control: a kept framing on the catalogue position says nothing and offers no wizard", () => {
    eq(line()?.textContent ?? null, null, "the line showed for a framing centred where GENERATE FLOW images:");
    eq(wizardButton()?.textContent ?? null, null, "a single frame on the catalogue position was offered the wizard:");
  });
  m.unmount();
}
{
  // A millionth of an hour of RA is 0.05 arcsec: the payload spells it the same.
  const hair = { ra_hours: M31.ra_hours + 1e-6, dec_deg: M31.dec_deg };
  const m = await mount(m31Framing(hair), { target: "m31" });
  await testAsync("control: a centre that posts the same coordinates is the same target, and says nothing", () => {
    eq(raHms(hair.ra_hours), raHms(M31.ra_hours), "precondition: the hair's RA posts the same");
    eq(decDms(hair.dec_deg), decDms(M31.dec_deg), "precondition: the hair's Dec posts the same");
    eq(line()?.textContent ?? null, null, "the line showed for a centre that would post the very same coordinates:");
  });
  m.unmount();
}
{
  const m = await mount(m31Framing(DRAGGED, false), { target: "m31" });
  await testAsync("control: a framing merely open on M31 (nothing kept) says nothing", () => {
    eq(line()?.textContent ?? null, null, "the line showed for a framing nothing was kept from:");
  });
  m.unmount();
}

// ======================================= 3. a patch names its own position
{
  const PATCH = { ra: "5.5", dec: "22.0", name: "05h 30m +22° 00′" };
  const framing = {
    target: undefined,
    freeroamId: PATCH.name,
    center: { ra_hours: 5.52, dec_deg: 22.1 },
    rotation_deg: 0,
    survey: "CDS/P/DSS2/color",
    stretch: "linear",
    fovZoomDeg: 2,
    mosaic: { rows: 1, cols: 1, overlap: DEFAULT_OVERLAP },
    panels: [{ row: 0, col: 0, ra_hours: 5.52, dec_deg: 22.1, rotation_deg: 0 }],
  };
  const m = await mount(framing, PATCH);
  await testAsync("a dragged patch framing names the position the sheet opened with, not a catalogue position", async () => {
    const el = line();
    assert(el != null, "a patch framing dragged off its position produced no line");
    const off = angularSepDeg(framing.center, { ra_hours: 5.5, dec_deg: 22 });
    eq(el.textContent, framingCentreNote(off, PATCH.name, false), "the patch's line:");
    assert(!/catalogue/.test(el.textContent), `a patch has no catalogue position: "${el.textContent}"`);
    const before = quickPosts().length;
    await press("quick-generate");
    const posts = quickPosts();
    eq(posts.length, before + 1, "GENERATE FLOW did not post:");
    eq(posts[posts.length - 1].body?.target?.ra, raHms(5.5), "the patch's POST left its own RA:");
    eq(posts[posts.length - 1].body?.target?.dec, decDms(22), "the patch's POST left its own Dec:");
  });
  m.unmount();
}

// MUTATION RECORD, 2026-09-28 (S7-USKYHUB), each mutant run in a private
// scratch copy of ui/ (scratchpad/S7-USKYHUB-mut in the session scratchpad,
// never the shared tree, #254) from a byte backup restored with its sha256
// checked. Output verbatim.
//
//   MUTANT "line missing" (the acceptance's: quick.tsx's
//   `quick-framing-centre` paragraph guarded `false &&`). Observed
//   ("quickImagesCatalogue.test: 5/7 passed"):
//     x a kept framing dragged off M31: the sheet says GENERATE FLOW images the catalogue position: a framing dragged off the object produced no word about where GENERATE FLOW images
//     x a dragged patch framing names the position the sheet opened with, not a catalogue position: a patch framing dragged off its position produced no line
//
//   MUTANT "compare numbers, not the payload" (offCentreDeg's test on the
//   raw floats instead of raHms/decDms). Observed ("6/7 passed"):
//     x control: a centre that posts the same coordinates is the same target, and says nothing: the line showed for a centre that would post the very same coordinates: (expected null, got "This framing is centred 1″ from Andromeda Galaxy's catalogue position. GENERATE FLOW images the catalogue position, at the framing's angle; SEND TO FLOW WIZARD carries the framing's centre.")
//
//   MUTANT "a patch is called a catalogue position" (framingCentreNote's
//   third argument `true`). Observed ("6/7 passed"):
//     x a dragged patch framing names the position the sheet opened with, not a catalogue position: the patch's line: (expected "This framing is centred 18′ from the position this sheet opened with. [...]", got "This framing is centred 18′ from 05h 30m +22° 00′'s catalogue position. [...]")
//
//   MUTANT "an open framing counts as kept" (the `keptPanels < 1` term
//   dropped). Observed ("6/7 passed"):
//     x control: a framing merely open on M31 (nothing kept) says nothing: the line showed for a framing nothing was kept from: (expected null, got "This framing is centred 15′ from Andromeda Galaxy's catalogue position. [...]")
//
//   MUTANT "SEND TO FLOW WIZARD only for a mosaic" (the block's condition
//   back to `isMosaic`). Observed ("4/7 passed"):
//     x a kept framing dragged off M31: [...] a framing dragged off the object produced no word about where GENERATE FLOW images
//     x SEND TO FLOW WIZARD is offered beside the line, and carries the dragged centre: the line names SEND TO FLOW WIZARD and the sheet does not offer it for a single frame
//     x a dragged patch framing names the position the sheet opened with, not a catalogue position: a patch framing dragged off its position produced no line
//
//   MUTANT "GENERATE FLOW takes the framing's centre" (the issue's FIRST
//   shape, which waits on an owner ruling: quickPayload's target given the
//   framing's centre when the framing is this sheet's). Observed ("5/7 passed"):
//     x GENERATE FLOW still posts the catalogue position, not the framing's centre (no behaviour changed): the quick POST's RA moved off the catalogue's: (expected "00h 42m 44s", got "00h 43m 15s")
//     x a dragged patch framing names the position the sheet opened with, not a catalogue position: the patch's POST left its own RA: (expected "05h 30m 00s", got "05h 31m 12s")

const total = passed + failed;
console.log(`quickImagesCatalogue.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
