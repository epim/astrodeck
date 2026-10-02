// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// framingSkyDegraded.test.tsx - the Target modal's sky on a rig with no survey
// source says so, instead of LOADING for good (#404, UX-07; spec 2026-09-23
// flows mosaic, 2.3); what it says is right for the rig's online fetch and for
// the render path (#426), on both hosts that show SkyCanvas's own copy; and
// the modal's sky keeps the lines SkyCanvas draws under its canvas inside its
// fixed height (#440, #465).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/framing/__tests__/framingSkyDegraded.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT WAS WRONG. SkyCanvas gates its tile-engine LOADING skeleton on
// `!surveyDegraded` and shows UX-07's empty state instead ("No sky survey
// available - download the offline pack or enable online fetch in
// Settings."), but `surveyDegraded` is a PROP: each host keeps the state from
// `onSurveyError` / `onSurveyLoad` and hands it back. AtlasView, SkyHub and
// CompassSurvey do. FramingSky wired none of the three, so in the modal
// `surveyDegraded` was always false, and on the S4 probe's fresh config (no
// sky pack, online fetch off: every tile 404) the sky pulsed "LOADING color..."
// in the middle of the grid for the whole walk, 3 to 7 s past the last 404.
//
// HOW THIS DRIVES IT. Nothing here calls onSurveyError by hand: a test that
// fired the callback itself would pass whether or not SkyCanvas ever fires it
// on the path the modal really takes. jsdom has no WebGL, so a context whose
// every method is a no-op is handed to the canvas; SkyCanvas's probe passes,
// the TileEngine mounts exactly as in a browser, asks /api/survey/tile for its
// tiles, gets the probe's 404 for each, and after its eighth consecutive
// failure on a blank view reports `onAllFailing`, which SkyCanvas turns into
// `onSurveyError`. Then the tiles are made to answer, and the engine's first
// drawn frame is what must clear the state (`onSurveyLoad`).
//
// The second half is the other render path: a survey with no tile slug (or a
// browser with no WebGL) takes SkyCanvas's <img> cutout pipeline, whose own
// first-load skeleton said LOADING for good in the same way, degraded or not.
//
// THE COPY (#426, S7-USKY). With no `degradedText` from its host, SkyCanvas
// said UX-07's sentence whatever the rig's online fetch was, so an operator
// whose fetch was ON, on a rig with no network, was told to turn it on; and
// the line under the canvas said "schematic framing" over the tile engine,
// which draws no schematic backdrop, behind a warning glyph and an em dash.
// FramingSky and CompassSurvey (ClassicAtlasSky.tsx) are the two hosts that
// show that default, so each is driven here in both states of onlineFetch.
//
// NO SOURCE WITHOUT THE WAIT (#493, H4-UFRAME). Two things kept a sky with no
// source saying LOADING long after it knew. The tile engine called a view
// all-failing only after 8 consecutive failures, which a view of fewer than 8
// tiles reaches only one 45 s negative-cache pass at a time, so the compass
// sky, which opens 55 deg wide on three tiles, took about 90 s: its cases here
// used to zoom in with the + key to get past that, and now grade it at the
// width it opens at (the engine's own rule is graded in
// atlas/__tests__/tileEngineAllFailing.test.tsx). And FramingSky kept its
// degraded state itself, so a sky remounted on a phone turn started again
// from "not degraded" on a fresh engine and pulsed LOADING until that engine
// had failed its way back; the verdict is now remembered per survey across
// mounts.
//
// Every mutant below was run in a private scratch copy of ui/ (scratchpad
// S5-SKY-mut for S5's, S7-USKY-mut for S7's, H4-UFRAME-mut for H4's), never
// in the shared tree (#254), and the failure it produced is quoted verbatim.
// S5's quotes carry the copy as it read then.

/* eslint-disable @typescript-eslint/no-explicit-any */

// @ts-ignore  node built-ins; tsx supplies them at runtime
import { readFileSync } from "node:fs";

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
win.Element.prototype.setPointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.releasePointerCapture = function () { /* jsdom has none */ };
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
win.URL.createObjectURL = () => "blob:stub";
win.URL.revokeObjectURL = () => {};
// The store (CompassSurvey reads it) opens nothing at import, but the shell
// it belongs to expects a socket class to exist.
win.WebSocket = class { close() {} addEventListener() {} send() {} };

// A WebGL context that accepts every call and answers every query with a
// truthy object: enough for initTileGL to compile, link and draw (it checks
// only that each create/compile/link answer is truthy). The 2D context, which
// SkyCanvas's label measurement asks for, stays absent, as in every other
// canvas test.
const GL_STUB: any = new Proxy({}, { get: () => () => ({}) });
win.HTMLCanvasElement.prototype.getContext = function (kind: string) {
  return kind === "webgl" || kind === "experimental-webgl" ? GL_STUB : null;
};

// The network. `tiles` decides what /api/survey/tile answers; the cutout
// route (the <img> path) always fails. Every request is counted, so a case
// can say how many 404s it took, and every survey id a cutout asked for is
// kept, so a case can say whether the server would have served it at all.
const net = {
  tiles: "404" as "404" | "ok", tileCalls: 0, cutoutCalls: 0, cutoutSurveys: [] as string[],
  /** Every tile asked, as "slug/order/npix", so a case can say whether any
   *  tile was asked twice (a second pass). */
  tileKeys: [] as string[],
};
win.fetch = async (url: string) => {
  const s = String(url);
  if (s.includes("/api/survey/tile/")) {
    net.tileCalls++;
    net.tileKeys.push(s.replace(/^.*\/api\/survey\/tile\//, "").replace(/\.jpg$/, ""));
    if (net.tiles === "ok") return { ok: true, status: 200, blob: async () => ({}) };
    return { ok: false, status: 404, blob: async () => ({}) };
  }
  if (s.includes("/api/survey/cutout")) {
    net.cutoutCalls++;
    net.cutoutSurveys.push(new URL(s, "http://local").searchParams.get("survey") ?? "");
    return { ok: false, status: 404, headers: { get: () => null }, blob: async () => ({}) };
  }
  // CompassSurvey asks what is catalogued in its patch of sky; nothing is.
  if (s.includes("/api/catalog/region")) {
    return { ok: true, status: 200, headers: { get: () => "application/json" }, json: async () => ({ rows: [] }) };
  }
  throw new Error(`no network in this fixture: ${s}`);
};
(globalThis as any).createImageBitmap = async () => ({ close() {} });

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "KeyboardEvent", "localStorage", "getComputedStyle",
  "matchMedia", "ResizeObserver", "requestAnimationFrame", "cancelAnimationFrame",
  "Image", "URL", "fetch", "Blob", "WebSocket", "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { FramingSky } = await import("../FramingSky");
const { CompassSurvey } = await import("../../../sky/ClassicAtlasSky");
const { useStore } = await import("../../../../store");
type SkyPanel = import("../../../atlas/PanelLayer").SkyPanel;

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => Promise<void> | void): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: unknown, msg: string): void { if (!cond) throw new Error(msg); }

/** Let real time pass inside act(), so the rAF draw loop and the fetches run
 *  and every state update they cause is flushed before the next look. */
async function waitFor(what: () => boolean, ms: number): Promise<boolean> {
  const until = Date.now() + ms;
  while (Date.now() < until) {
    if (what()) return true;
    await act(async () => { await new Promise((r) => setTimeout(r, 25)); });
  }
  return what();
}

// ------------------------------------------------------------ the fixture
// The 3x2 the probe walked: the server's own answer, read, never copied.
const FIXTURE_REL = "../../../../../../server/tests/fixtures/mosaic_panels_3x2.json";
function readPanels(): { centre: { ra_hours: number; dec_deg: number }; panels: SkyPanel[];
                         fov: { fov_x_deg: number; fov_y_deg: number } } {
  let text: string;
  try {
    text = readFileSync(new URL(FIXTURE_REL, import.meta.url), "utf8") as string;
  } catch (e) {
    throw new Error(`cannot read ${FIXTURE_REL}: ${(e as Error).message}`);
  }
  const fx = JSON.parse(text);
  return {
    centre: { ra_hours: fx.request.ra_hours, dec_deg: fx.request.dec_deg },
    panels: fx.response.panels.map((p: any) => ({
      row: p.row, col: p.col, ra_hours: p.ra_hours, dec_deg: p.dec_deg,
      rotation_deg: p.rotation_deg, state: "pending",
    })),
    fov: { fov_x_deg: fx.response.frame_fov_x_deg, fov_y_deg: fx.response.frame_fov_y_deg },
  };
}
const FX = readPanels();

const OPTICS = {
  focal_length_mm: 530,
  pixel_size_um: 3.76,
  sensor_width_px: 4144,
  sensor_height_px: 2822,
};

const noop = () => {};
function props(over: Record<string, unknown> = {}): any {
  return {
    frameCentre: FX.centre,
    viewCentre: null,
    moveGrid: false,
    panels: FX.panels,
    panelFov: FX.fov,
    rotationDeg: 0,
    zoomDeg: 8,
    survey: "CDS/P/DSS2/color",
    optics: OPTICS,
    mosaic: { rows: 2, cols: 3, overlap: 0.25 },
    night: false,
    onlineFetch: false,
    readOnly: false,
    onMoveGrid: noop, onFrameCentre: noop, onViewCentre: noop,
    onRotate: noop, onZoom: noop, onPanelTap: noop,
    ...over,
  };
}

/** UX-07's empty state, as SkyCanvas words it when the host gives no text of
 *  its own (FramingSky has no pack status to choose a better one from). */
const EMPTY_STATE = /No sky survey available/;
const LOADING = /LOADING/;
/** The tile engine's line under the canvas while the survey is degraded (it
 *  also reaches the screen reader through the canvas's status line). */
const TILE_LINE = /Survey tiles not loading/;

/** The copy, graded by what it tells the operator, not by its exact words. */
const UX07 = /^No sky survey available: download the offline pack or enable online fetch in Settings\.$/;
const TURN_ON_FETCH = /(enable|turn on) online fetch/i;
const NOT_ARRIVING = /not arriving/i;
const CHECK_CONNECTION = /check the (rig's )?connection/i;
const INSTALL_PACK = /(install|download) the offline sky pack/i;
const NO_DASH_OR_GLYPH = /[\u2014\u2013\u26A0]/;

const container = win.document.getElementById("root") as any;
const text = () => String(container.textContent ?? "");
const skyBox = () => container.querySelector('[role="application"]') as any;
/** UX-07's empty state inside the canvas (its text, or null when not drawn). */
const emptyText = (): string | null => {
  const el = container.querySelector('[data-role="survey-empty"]') as any;
  return el ? String(el.textContent) : null;
};
/** The line under the canvas: the element the real-page probe grades
 *  (routes_s5_s6.json, s5-frame-running-phone: `[data-testid="framing-sky"]
 *  div.text-warn`), found the same way. */
const lineText = (): string | null => {
  const el = container.querySelector("div.text-warn") as any;
  return el ? String(el.textContent) : null;
};

// ============================================ the tile engine (the probe's path)
const root = createRoot(container);
act(() => { root.render(createElement(FramingSky, props())); });

await test("control: before any tile has answered, the tile engine is mounted and says LOADING, not the empty state", () => {
  assert(skyBox() != null, "no sky canvas in the document - the fixture is wrong");
  assert(skyBox().querySelector("canvas") != null,
    "the tile engine did not mount (no <canvas> in the sky), so this file would grade the <img> path, not the probe's");
  assert(LOADING.test(text()), `the first-load skeleton is not drawn: ${JSON.stringify(text().slice(0, 200))}`);
  assert(!EMPTY_STATE.test(text()), "the empty state is drawn before any tile has failed");
});

// Mutant "degraded not wired" (FramingSky passes no surveyDegraded, and wires
// neither onSurveyError nor onSurveyLoad, as before this change):
//   failed, 2/5, the two controls green:
//   x a canvas whose tiles all 404 shows UX-07's empty state and no LOADING:
//     after 11 tile 404s the modal's sky shows no empty state; it reads "MOVE
//     SKYMOVE GRIDLOADING color…NW1.46″/px8.00° wide1-1, pending1-2,
//     pending1-3, pending2-3, pending2-2, pending2-1, pending"
//   x when the tiles come back, the engine's first drawn frame clears the
//     degraded state: precondition: the sky is not degraded, so there is
//     nothing for the tiles to clear: "MOVE SKYMOVE GRIDLOADING
//     color…NW1.46″/px8.00° wide1-1, pending1-2, pending1-3, pending2-3,
//     pending2-2, pending2-1, pending"
//   x on the <img> path a failed cutout shows the empty state and no LOADING:
//     after the cutout failed the <img> path shows no empty state; it reads
//     "MOVE SKYMOVE GRIDLOADING color…NW1.46″/px8.00° wide1-1, pending1-2,
//     pending1-3, pending2-3, pending2-2, pending2-1, pending"
await test("a canvas whose tiles all 404 shows UX-07's empty state and no LOADING", async () => {
  const shown = await waitFor(() => EMPTY_STATE.test(text()), 3000);
  assert(net.tileCalls >= 8,
    `only ${net.tileCalls} tile requests were made, under the engine's 8-failure threshold - the fixture never degraded`);
  assert(shown,
    `after ${net.tileCalls} tile 404s the modal's sky shows no empty state; it reads ${JSON.stringify(text().slice(0, 160))}`);
  assert(!LOADING.test(text()),
    `LOADING is still drawn beside the empty state after ${net.tileCalls} tile 404s`);
});

// Online fetch OFF, the tile engine: UX-07's advice is right here, and the
// line under the canvas names what the tile engine does, not a schematic.
//
// Mutant "line not worded by path" (SkyCanvas's default line under the canvas
// back to the one sentence for both paths, "Survey unreachable, showing a
// schematic. Retrying." when there is no last image): failed, 7/11:
//   x online fetch off, tile engine: the canvas says UX-07's sentence and the
//     line under it names no schematic: the tile engine's line under the
//     canvas reads "Survey unreachable, showing a schematic. Retrying."
//   x when the tiles come back, the engine's first drawn frame clears the
//     degraded state: precondition: the sky is not degraded, so there is
//     nothing for the tiles to clear: "MOVE SKYMOVE GRIDSurvey unreachable,
//     showing a schematic. Retrying.No sky survey available: download the
//     offline pack or enable online fetch in Settings.NW1.46″/px8.00° wide1-1,
//     pending1-2, pending1-3"
//   x online fetch on, FramingSky: the canvas says the survey is not arriving,
//     check the connection or install the pack, and never to turn fetch on:
//     the tile engine's line under the canvas reads "Survey unreachable,
//     showing a schematic. Retrying."
//   x CompassSurvey, online fetch off: it asks for a survey the server serves,
//     and says UX-07's sentence: the compass sky's line under the canvas reads
//     "Survey unreachable, showing a schematic. Retrying."
await test("online fetch off, tile engine: the canvas says UX-07's sentence and the line under it names no schematic", () => {
  const empty = emptyText();
  assert(empty !== null, `precondition: the empty state is not drawn: ${JSON.stringify(text().slice(0, 200))}`);
  assert(UX07.test(empty!), `with online fetch off the canvas says ${JSON.stringify(empty)}, not UX-07's sentence`);
  const line = lineText();
  assert(line !== null, `no line under the canvas while the survey is degraded: ${JSON.stringify(text().slice(0, 200))}`);
  assert(TILE_LINE.test(line!), `the tile engine's line under the canvas reads ${JSON.stringify(line)}`);
  assert(!/schematic|last image/i.test(line!),
    `the tile engine's line names ${JSON.stringify(line)} - it draws no schematic backdrop and keeps no last image`);
  for (const [what, t] of [["the canvas's sentence", empty!], ["the line under it", line!]] as const) {
    assert(!NO_DASH_OR_GLYPH.test(t), `${what} carries an em or en dash or a warning glyph: ${JSON.stringify(t)}`);
  }
});

// ================================================ a remount keeps the verdict
// The modal's sky remounted on a phone turn (390 x 844 to 844 x 390, #493)
// came back pulsing LOADING over a survey it had already found has no source:
// the degraded state lived in the FramingSky that was thrown away, and the new
// one began at "not degraded" with a fresh engine that had to fail its way
// back. The same sky is unmounted and mounted again here, and graded in the
// commit that mounts it, before its new engine has asked for a single tile, so
// nothing this mount did can be what made it degraded.
act(() => { root.unmount(); });
const rootR = createRoot(container);
const callsBeforeRemount = net.tileCalls;
act(() => { rootR.render(createElement(FramingSky, props())); });

// Mutant "state reset on remount" (FramingSky's degraded state back in its own
// useState(false), as before this change): 12/13 passed, this case red:
//   x a remounted sky on a survey already found degraded is degraded from its
//     first commit, before its new engine has asked for a tile: the remounted
//     sky says LOADING over a survey already found to have no source (0 tile
//     requests since the remount): "MOVE SKYMOVE GRIDLOADING
//     color…NW1.46″/px8.00° wide1-1, pending1-2, pending1-3, pending2-3,
//     pending2-2, pending2-1, pending"
await test("a remounted sky on a survey already found degraded is degraded from its first commit, before its new engine has asked for a tile", () => {
  const since = net.tileCalls - callsBeforeRemount;
  assert(skyBox()?.querySelector("canvas") != null, "the remounted sky has no tile engine - this would grade the wrong path");
  assert(since === 0, `precondition: the new engine has already asked for ${since} tiles, so this mount could have degraded itself`);
  assert(!LOADING.test(text()),
    `the remounted sky says LOADING over a survey already found to have no source (${since} tile requests since the remount): ${JSON.stringify(text().slice(0, 200))}`);
  const empty = emptyText();
  assert(empty !== null && UX07.test(empty), `the remounted sky does not say UX-07's sentence: ${JSON.stringify(empty)}`);
  const line = lineText();
  assert(line !== null && TILE_LINE.test(line), `the remounted sky's line under the canvas reads ${JSON.stringify(line)}`);
});

// Control: the verdict is the survey's. A sky on a survey nobody has found
// degraded opens LOADING, as it always did. Graded in the commit that mounts
// it, in a container of its own, and unmounted at once, so its engine never
// gets to find anything.
//
// Mutant "one flag for every survey" (the remembered verdict is one entry for
// every survey, not keyed by survey): 11/13 passed:
//   x control: a sky on a survey never found degraded opens LOADING, not the
//     empty state: a sky on CDS/P/DSS2/red, which nothing has found degraded,
//     opens on the empty state: "MOVE SKYMOVE GRIDSurvey tiles not loading.
//     Retrying automatically.No sky survey available: download the offline
//     pack or enable online fetch in Settings.NW1.46″/px8.00° wide1-1,
//     pending1-2, pending1-3,"
//   x online fetch on, FramingSky: the canvas says the survey is not arriving,
//     check the connection or install the pack, and never to turn fetch on:
//     only 0 tile requests with online fetch on, under the engine's 8-failure
//     threshold - the fixture never degraded
// (the second: the <img> half's Mellinger verdict, never cleared, opened the
// DSS2 color sky degraded before its engine had asked for anything).
await test("control: a sky on a survey never found degraded opens LOADING, not the empty state", () => {
  const box = win.document.createElement("div");
  win.document.body.appendChild(box);
  const r = createRoot(box);
  try {
    act(() => { r.render(createElement(FramingSky, props({ survey: "CDS/P/DSS2/red" }))); });
    const t = String(box.textContent ?? "");
    assert(box.querySelector("canvas") != null, "the DSS2 red sky has no tile engine - this control would grade the wrong path");
    assert(box.querySelector('[data-role="survey-empty"]') == null && !TILE_LINE.test(t),
      `a sky on CDS/P/DSS2/red, which nothing has found degraded, opens on the empty state: ${JSON.stringify(t.slice(0, 200))}`);
    assert(LOADING.test(t), `a sky on CDS/P/DSS2/red does not open LOADING: ${JSON.stringify(t.slice(0, 200))}`);
  } finally {
    act(() => { r.unmount(); });
    box.remove();
  }
});

// Mutant "load never clears" (FramingSky wires onSurveyError but not
// onSurveyLoad, so nothing ever takes the state down):
//   failed, 4/5 (the canvas's status line opens with a warning-sign glyph,
//     written [warning sign] here):
//   x when the tiles come back, the engine's first drawn frame clears the
//     degraded state: the engine drew its tiles and the sky still says the
//     survey is unreachable: "MOVE SKYMOVE GRIDSurvey unreachable, retrying,
//     showing schematicNW1.46″/px3.00° wide1-1, pending1-2, pending2-2,
//     pending2-1, pending[warning sign] Survey unreachable — schematic framing;
//     retrying automatically."
await test("when the tiles come back, the engine's first drawn frame clears the degraded state", async () => {
  // Waited for, not read at once: this case grades the recovery, and the
  // remounted sky's own engine degrades it in one pass whether or not the
  // remount kept the verdict (the case above grades that).
  const degraded = await waitFor(() => EMPTY_STATE.test(text()) && TILE_LINE.test(text()), 3000);
  assert(degraded,
    `precondition: the sky is not degraded, so there is nothing for the tiles to clear: ${JSON.stringify(text().slice(0, 200))}`);
  net.tiles = "ok";
  // The failed tiles sit in the engine's 45 s negative cache, so the sky is
  // moved to fresh tiles, which is what a pan or zoom back to fetchable sky
  // does in the modal. It is the remounted sky (above) that recovers: a
  // verdict remembered across mounts has to be cleared by the tiles too.
  act(() => { rootR.render(createElement(FramingSky, props({ zoomDeg: 3 }))); });
  const calls = net.tileCalls;
  // The in-canvas empty state is drawn only until the engine's first drawn
  // frame (its own first-draw flag), whatever the host does, so its going is
  // what says the tiles really arrived. The degraded state itself is the
  // host's, and only onSurveyLoad clears it: the status line under the canvas
  // and the screen reader's line both say the tiles are not loading until
  // then.
  const drew = await waitFor(() => !EMPTY_STATE.test(text()) && net.tileCalls > calls, 3000);
  assert(drew,
    `the tiles were made to answer and the engine never drew one (${net.tileCalls - calls} requests since): ` +
    JSON.stringify(text().slice(0, 200)));
  const cleared = await waitFor(() => !TILE_LINE.test(text()), 1000);
  assert(cleared,
    `the engine drew its tiles and the sky still says the survey tiles are not loading: ${JSON.stringify(text().slice(0, 200))}`);
  assert(!LOADING.test(text()), "LOADING came back after the tiles answered");
});

act(() => { rootR.unmount(); });
net.tiles = "404";

// ================================================ the <img> cutout pipeline
// A survey the tile engine has no slug for takes the <img> path even with
// WebGL; so does every survey on a browser without it.
const root2 = createRoot(container);
act(() => {
  root2.render(createElement(FramingSky, props({ survey: "CDS/P/Mellinger/color" })));
});

await test("control: a survey with no tile slug takes the <img> path and says LOADING until its first answer", () => {
  assert(skyBox().querySelector("canvas") == null,
    "the tile engine mounted for a survey it has no slug for - this half would grade the wrong path");
  assert(LOADING.test(text()), `the <img> path's first-load skeleton is not drawn: ${JSON.stringify(text().slice(0, 200))}`);
  assert(!EMPTY_STATE.test(text()), "the <img> path shows the empty state before its cutout has failed");
});

// Mutant "img skeleton ungated" (SkyCanvas's <img>-path skeleton loses its
// `!surveyDegraded` and the <img>-path empty state is not drawn, as before
// this change):
//   failed, 4/5:
//   x on the <img> path a failed cutout shows the empty state and no LOADING:
//     after the cutout failed the <img> path shows no empty state; it reads
//     "MOVE SKYMOVE GRIDSurvey unreachable, retrying, showing schematicLOADING
//     color…NW1.46″/px8.00° wide1-1, pending1-2, pending1-3, pending2-3,
//     pending2-2, pending2-"
await test("on the <img> path a failed cutout shows the empty state and no LOADING", async () => {
  const shown = await waitFor(() => EMPTY_STATE.test(text()), 3000);
  assert(net.cutoutCalls >= 1, "the <img> path never asked for its cutout - the fixture never degraded");
  assert(shown,
    `after the cutout failed the <img> path shows no empty state; it reads ${JSON.stringify(text().slice(0, 160))}`);
  assert(!LOADING.test(text()), "LOADING is still drawn beside the empty state on the <img> path");
});

// The <img> path with no good frame draws the schematic backdrop, so there the
// line under the canvas is right to say so.
await test("on the <img> path with no image yet, the line under the canvas says the sky is schematic", () => {
  const line = lineText();
  assert(line !== null, "no line under the canvas on the <img> path while the survey is degraded");
  assert(/showing a schematic/.test(line!) && !TILE_LINE.test(line!),
    `the <img> path's line under the canvas reads ${JSON.stringify(line)}`);
  assert(!NO_DASH_OR_GLYPH.test(line!), `the <img> path's line carries a dash or a warning glyph: ${JSON.stringify(line)}`);
});

act(() => { root2.unmount(); });

// ============================================ online fetch ON, the modal's sky
// The probe's path again, on a rig whose online fetch is on and whose tiles
// still do not arrive (a dark site with no network, and no pack).
//
// Mutant "enable online fetch said while on" (SkyCanvas's default in the
// canvas is UX-07's sentence whatever `onlineFetch` says, as before this
// change): failed, 9/11:
//   x online fetch on, FramingSky: the canvas says the survey is not arriving,
//     check the connection or install the pack, and never to turn fetch on:
//     with online fetch ON the canvas tells the operator to turn it on: "No
//     sky survey available: download the offline pack or enable online fetch
//     in Settings."
//   x CompassSurvey, online fetch on: it says the survey is not arriving,
//     check the connection or install the pack, and never to turn fetch on:
//     with online fetch ON the compass sky tells the operator to turn it on:
//     "No sky survey available: download the offline pack or enable online
//     fetch in Settings."
const root3 = createRoot(container);
act(() => { root3.render(createElement(FramingSky, props({ onlineFetch: true }))); });

await test("online fetch on, FramingSky: the canvas says the survey is not arriving, check the connection or install the pack, and never to turn fetch on", async () => {
  const calls = net.tileCalls;
  const shown = await waitFor(() => emptyText() !== null, 3000);
  assert(net.tileCalls - calls >= 8,
    `only ${net.tileCalls - calls} tile requests with online fetch on, under the engine's 8-failure threshold - the fixture never degraded`);
  assert(shown, `with online fetch on the degraded sky shows no empty state; it reads ${JSON.stringify(text().slice(0, 200))}`);
  const empty = emptyText()!;
  assert(!TURN_ON_FETCH.test(empty), `with online fetch ON the canvas tells the operator to turn it on: ${JSON.stringify(empty)}`);
  assert(NOT_ARRIVING.test(empty) && CHECK_CONNECTION.test(empty) && INSTALL_PACK.test(empty),
    `with online fetch on the canvas says ${JSON.stringify(empty)}: not that the survey is not arriving, to check the connection, or to install the pack`);
  assert(!NO_DASH_OR_GLYPH.test(empty), `the canvas's sentence carries a dash or a warning glyph: ${JSON.stringify(empty)}`);
  const line = lineText();
  assert(line !== null && TILE_LINE.test(line), `the tile engine's line under the canvas reads ${JSON.stringify(line)}`);
});

// ================================ the modal's sky keeps its lines (#440, #465)
// `.tfs-sky` is a fixed height with overflow hidden (framing.css), and
// SkyCanvas draws its status lines UNDER its square, in its own column. The
// square used to be as wide as the fit box, so it filled the sky's height by
// itself and every line went past the clip: the real-page probe saw the
// degraded line cut to its descenders and 18 px off the canvas's top and
// bottom on the 390 x 844 phone (#465). jsdom lays nothing out, so this case
// holds the CONTRACT the layout is built from, on both sides of it: the
// stylesheet sizes the square from the height the lines leave, and the DOM
// puts the square alone in the slot that stylesheet reads, with the lines
// beside the slot, not in it. The layout itself is graded on the real page
// (routes_s5_s6.json, s5-frame-running-phone, text_intact on the line).
const CSS_REL = "../framing.css";
interface Rule { at: string[]; selector: string; decls: [string, string][] }
/** framing.css's rules, comments stripped, blocks nested to any depth: the
 *  same small reader framingLayout.test.tsx uses (the file has no strings
 *  containing braces). */
function parseCss(src: string): Rule[] {
  const css = src.replace(/\/\*[\s\S]*?\*\//g, "");
  const out: Rule[] = [];
  let i = 0;
  function block(at: string[]): void {
    while (i < css.length) {
      const open = css.indexOf("{", i);
      const close = css.indexOf("}", i);
      if (close !== -1 && (open === -1 || close < open)) { i = close + 1; return; }
      if (open === -1) { i = css.length; return; }
      const prelude = css.slice(i, open).trim();
      i = open + 1;
      if (prelude.startsWith("@")) { block([...at, prelude.replace(/\s+/g, " ")]); continue; }
      const end = css.indexOf("}", i);
      const body = css.slice(i, end);
      i = end + 1;
      const decls = body.split(";").map((d) => d.trim()).filter(Boolean).map((d) => {
        const c = d.indexOf(":");
        return [d.slice(0, c).trim(), d.slice(c + 1).trim()] as [string, string];
      });
      out.push({ at, selector: prelude.replace(/\s+/g, " "), decls });
    }
  }
  block([]);
  return out;
}
function readCss(): string {
  try {
    return readFileSync(new URL(CSS_REL, import.meta.url), "utf8") as string;
  } catch (e) {
    throw new Error(`cannot read ${CSS_REL}: ${(e as Error).message}`);
  }
}
const RULES = parseCss(readCss());
const rulesFor = (sel: string, at: (a: string[]) => boolean): Rule[] =>
  RULES.filter((r) => r.selector.split(",").map((s) => s.trim()).includes(sel) && at(r.at));
const last = (r: Rule | undefined, prop: string): string | null => {
  const hits = (r?.decls ?? []).filter(([p]) => p === prop);
  return hits.length ? hits[hits.length - 1][1] : null;
};
const top = (a: string[]) => a.length === 0;
const withCq = (a: string[]) => a.length === 1 && a[0] === "@supports (container-type: size)";
const hasClass = (el: any, c: string) => el != null && String(el.className).split(/\s+/).includes(c);

// Mutant "square fills the box" (`container-type: size` dropped from the
// slot, so the square's container units read `.tfs-sky`, the whole box, and
// the square fills it as it did before this change): failed, 10/11:
//   x the modal's square is sized from the height SkyCanvas's lines leave, and
//     the lines sit beside its slot: the square's slot is not a size container
//     (container-type null), so the square's container units read the whole
//     sky and it fills the box
// Built into the app and walked on the real page (390 x 844, no sky source),
// the same mutant PASSES the probe's s5-frame-running-phone and s4-frame-phone:
// the line is whole, so text_intact is satisfied, while the 390 px square
// overhangs its 354 px slot by 18 px each way, its top (the N, and the top
// 10 px of MOVE SKY) under the sky's clip and its bottom under the line. So
// this case, not the probe, is what holds the contract (the probe's blind
// spot is #495); the layout before this change failed the probe on the line
// (text_intact, clipped 11 px).
await test("the modal's square is sized from the height SkyCanvas's lines leave, and the lines sit beside its slot", () => {
  // The stylesheet. The fit box is the largest square the sky holds, in both
  // axes, and SkyCanvas's column fills it.
  const fit = rulesFor(".tfs-sky-fit", top)[0];
  assert(last(fit, "width") === "min(100cqw, 100cqh)" && last(fit, "height") === "min(100cqw, 100cqh)",
    `the fit box is not the sky's largest square in both axes: width ${last(fit, "width")}, height ${last(fit, "height")}`);
  const col = rulesFor(".tfs-sky-fit > .sky-canvas", withCq)[0];
  assert(last(col, "height") === "100%", `SkyCanvas's column does not fill the fit box: height ${last(col, "height")}`);
  // The slot takes what height the lines leave, and nothing more (a flex
  // basis of 0, allowed below its content), and is what the square is read
  // off.
  const slot = rulesFor(".tfs-sky-fit .sky-canvas-square", withCq)[0];
  const flex = String(last(slot, "flex")).split(/\s+/);
  assert(flex[0] === "1" && /^0(px|%)?$/.test(flex[2] ?? ""),
    `the square's slot does not take the height the lines leave (flex ${JSON.stringify(last(slot, "flex"))}, wanted a grow of 1 on a basis of 0)`);
  assert(last(slot, "min-height") === "0", `the square's slot keeps a content floor (min-height ${last(slot, "min-height")})`);
  assert(last(slot, "container-type") === "size",
    `the square's slot is not a size container (container-type ${last(slot, "container-type")}), so the square's container units read the whole sky and it fills the box`);
  const square = rulesFor('.tfs-sky-fit .sky-canvas-square > [role="application"]', withCq)[0];
  assert(last(square, "width") === "min(100cqw, 100cqh)",
    `the square is not the largest one its slot holds: width ${last(square, "width")}`);
  // Nothing between the slot and the sky reads the square's container units
  // instead: SkyCanvas's column is not a container of its own.
  for (const r of RULES.filter((x) => /\.sky-canvas(?![-\w])/.test(x.selector))) {
    assert(last(r, "container-type") === null || /sky-canvas-square/.test(r.selector),
      `${r.selector} is a container too, and the square could read it instead of its slot`);
  }
  // The DOM the stylesheet names. The degraded sky is up (root3, above), so
  // the line under the canvas is drawn.
  const fitEl = container.querySelector(".tfs-sky-fit") as any;
  assert(fitEl != null, "no .tfs-sky-fit in the modal's sky");
  const colEl = fitEl.firstElementChild;
  assert(hasClass(colEl, "sky-canvas") && hasClass(colEl, "flex") && hasClass(colEl, "flex-col"),
    `the fit box's child is not SkyCanvas's column (${JSON.stringify(colEl ? String(colEl.className) : null)})`);
  const sq = skyBox();
  assert(hasClass(sq?.parentElement, "sky-canvas-square") && sq.parentElement.parentElement === colEl,
    "the square is not alone in the slot framing.css sizes it from, directly in SkyCanvas's column");
  assert(sq.parentElement.children.length === 1, "the square's slot holds more than the square");
  const lineEl = container.querySelector("div.text-warn") as any;
  assert(lineEl != null, "precondition: no degraded line under the canvas, so where it sits goes ungraded");
  assert(lineEl.parentElement === colEl,
    "the degraded line is not beside the square's slot in SkyCanvas's column, so the slot cannot leave it room");
});

act(() => { root3.unmount(); });

// ========================================================= CompassSurvey
// The classic Atlas's compass sky (ClassicAtlasSky.tsx) is the other host
// with no pack status: it passes `config.survey.online_fetch` and no text, so
// SkyCanvas's default is what it shows. It is driven through the store the
// way the page drives it.
const COMPASS_CENTRE = { ra_hours: FX.centre.ra_hours, dec_deg: FX.centre.dec_deg };
const DISPLAY = { accepts: () => true, imagery: true, objects: false, controls: null };
/** The HiPS ids GET /api/survey/cutout.jpg accepts (its `survey` Literal in
 *  server/astrodeck/catalog/survey.py). */
const SERVED = new Set(["CDS/P/DSS2/color", "CDS/P/DSS2/red", "CDS/P/2MASS/color"]);

/** How long a compass case waits for the empty state: far inside the 45 s
 *  negative-cache pass, so only a view called in its FIRST pass can meet it. */
const ONE_PASS_MS = 3000;

/** Mount the compass sky with the store saying `onlineFetch`, at the width it
 *  opens at, and let its survey degrade. The root is handed back for the case
 *  to unmount; if anything here throws, it is unmounted first, so the next
 *  case does not mount a second root on the same container. */
async function compass(onlineFetch: boolean): Promise<{ root: any; empty: string | null; line: string | null;
                                                        tiles: number; repeats: string[]; unserved: string[] }> {
  useStore.setState({ config: { survey: { online_fetch: onlineFetch } }, night: false, framing: null } as never);
  const r = createRoot(container);
  try {
    const calls = net.tileCalls;
    const keys = net.tileKeys.length;
    const cutouts = net.cutoutSurveys.length;
    act(() => { r.render(createElement(CompassSurvey, { center: COMPASS_CENTRE, display: DISPLAY })); });
    // The compass sky opens 55 deg wide, where the whole view is three tiles
    // (#493). These cases used to zoom it in with the + key to about 8 deg,
    // because the engine's fixed 8-failure threshold was out of a 3-tile
    // view's reach for 45 s at a time; it is graded where it opens now.
    assert(/55\.00° wide/.test(text()), `the compass sky did not open 55 deg wide: ${JSON.stringify(text().slice(0, 200))}`);
    await waitFor(() => emptyText() !== null, ONE_PASS_MS);
    const asked = net.tileKeys.slice(keys);
    return {
      root: r, empty: emptyText(), line: lineText(), tiles: net.tileCalls - calls,
      repeats: asked.filter((k, i) => asked.indexOf(k) !== i),
      unserved: net.cutoutSurveys.slice(cutouts).filter((s) => !SERVED.has(s)),
    };
  } catch (e) {
    act(() => { r.unmount(); });
    throw e;
  }
}
const UNSERVED = (asked: string[]) =>
  `the compass sky asked the cutout route for ${JSON.stringify(asked)}, which it does not serve: every request is refused, whatever the rig's pack or network`;

// Mutant "fixed 8" (TileEngine's all-failing threshold back to a fixed 8
// consecutive failures, as before #493), graded here on the real host at the
// width it opens at: 11/13 passed, both compass cases red:
//   x CompassSurvey, online fetch off: it asks for a survey the server serves,
//     and says UX-07's sentence: the compass sky's 3 tiles all answered 404
//     and after 3000 ms it still says no empty state: "LOADING
//     color…NW55.00° wideFollowing your phone · compass alignment is
//     approximate. Turn the compass off to return to your saved framing."
//   x CompassSurvey, online fetch on: it says the survey is not arriving,
//     check the connection or install the pack, and never to turn fetch on:
//     the compass sky with online fetch on shows no empty state: "LOADING
//     color…NW55.00° wideFollowing your phone · compass alignment is
//     approximate. Turn the compass off to return to your saved framing."

// Mutant "compass survey bare id" (CompassSurvey's fallback survey back to
// 'DSS2/color', as before this change): failed, 9/11:
//   x CompassSurvey, online fetch off: it asks for a survey the server serves,
//     and says UX-07's sentence: the compass sky asked the cutout route for
//     ["DSS2/color"], which it does not serve: every request is refused,
//     whatever the rig's pack or network
//   x CompassSurvey, online fetch on: it says the survey is not arriving,
//     check the connection or install the pack, and never to turn fetch on:
//     the compass sky asked the cutout route for ["DSS2/color"], which it does
//     not serve: every request is refused, whatever the rig's pack or network
await test("CompassSurvey, online fetch off: it asks for a survey the server serves, and says UX-07's sentence", async () => {
  const c = await compass(false);
  try {
    assert(c.unserved.length === 0, UNSERVED(c.unserved));
    assert(skyBox()?.querySelector("canvas") != null,
      "the compass sky's tile engine did not mount: it has no tile slug for the survey it names");
    assert(c.tiles > 0 && c.tiles < 8,
      `precondition: the compass sky's opening view made ${c.tiles} tile requests; #493 is a view of fewer than 8`);
    assert(c.empty !== null,
      `the compass sky's ${c.tiles} tiles all answered 404 and after ${ONE_PASS_MS} ms it still says no empty state: ` +
      JSON.stringify(text().slice(0, 200)));
    assert(c.repeats.length === 0, `the compass sky asked ${JSON.stringify(c.repeats)} twice: that is a second pass, not the first`);
    assert(UX07.test(c.empty!), `with online fetch off the compass sky says ${JSON.stringify(c.empty)}, not UX-07's sentence`);
    assert(c.line !== null && TILE_LINE.test(c.line) && !NO_DASH_OR_GLYPH.test(c.line),
      `the compass sky's line under the canvas reads ${JSON.stringify(c.line)}`);
  } finally {
    act(() => { c.root.unmount(); });
  }
});

await test("CompassSurvey, online fetch on: it says the survey is not arriving, check the connection or install the pack, and never to turn fetch on", async () => {
  const c = await compass(true);
  try {
    assert(c.unserved.length === 0, UNSERVED(c.unserved));
    assert(c.empty !== null, `the compass sky with online fetch on shows no empty state: ${JSON.stringify(text().slice(0, 200))}`);
    assert(!TURN_ON_FETCH.test(c.empty!), `with online fetch ON the compass sky tells the operator to turn it on: ${JSON.stringify(c.empty)}`);
    assert(NOT_ARRIVING.test(c.empty!) && CHECK_CONNECTION.test(c.empty!) && INSTALL_PACK.test(c.empty!),
      `with online fetch on the compass sky says ${JSON.stringify(c.empty)}`);
  } finally {
    act(() => { c.root.unmount(); });
  }
});

// ------------------------------------------------------------------- report
dom.window.close();
const total = passed + failed;
console.log(`framingSkyDegraded.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
export default result;
