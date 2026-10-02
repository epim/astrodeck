// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// panelLayer.test.tsx - the mosaic PanelLayer on its own: where each panel is
// drawn, how its five states are told apart, and which panel a point is in
// (#189 S4 item 2; spec 2026-09-23 flows mosaic, 2.3).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/atlas/__tests__/panelLayer.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE PANELS ARE THE SERVER'S. server/tests/fixtures/mosaic_panels_3x2.json is
// a recorded POST /api/framing/mosaic answer that test_framing_panels_fixture.py
// keeps equal to the route. It is READ here, never copied: a panel list written
// out in this file would agree with the layer by construction.
//
// skyCanvasPanels.test.tsx mounts the whole canvas; this file holds the layer
// to its own contract, which is the part a canvas-level test can pass over:
//   * a panel is placed through skyToView and outlined through fovCornersSky,
//     at the size the grid was TILED for (panelFov), not the live optics;
//   * the five states differ in SHAPE (stroke width, dash, hatch, corner ticks,
//     X, "!"), because this app is used at night under a red filter where
//     --accent and --good are the same red (index.css palette note);
//   * a point is in the panel whose outline holds it, and in an overlap the
//     panel whose centre is nearest.
//
// Every mutant below was run in a private scratch copy of ui/ (scratchpad
// s4-usky-mut), never in the shared tree (#254), and the failure it produced
// is quoted verbatim.

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
const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "MouseEvent", "getComputedStyle",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const {
  placePanels, panelAt, panelLabel, PanelShapes, PanelLabels, PANEL_STATES,
} = await import("../PanelLayer");
const { skyToView, fovCornersSky } = await import("../../../lib/atlasFov");
type SkyPanel = import("../PanelLayer").SkyPanel;
type PanelState = import("../PanelLayer").PanelState;

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function near(a: number, b: number, tol: number, what: string): void {
  assert(Math.abs(a - b) <= tol, `${what}: got ${a}, expected ${b} (+-${tol})`);
}

// ------------------------------------------------------------ the fixture
const FIXTURE_REL = "../../../../../server/tests/fixtures/mosaic_panels_3x2.json";
interface Fixture {
  request: { ra_hours: number; dec_deg: number; rows: number; cols: number;
             overlap: number; rotation_deg: number; fov_x_deg: number; fov_y_deg: number };
  response: { panels: { row: number; col: number; ra_hours: number; dec_deg: number;
                        rotation_deg: number }[];
              frame_fov_x_deg: number; frame_fov_y_deg: number };
}
function readFixture(): Fixture {
  let text: string;
  try {
    text = readFileSync(new URL(FIXTURE_REL, import.meta.url), "utf8") as string;
  } catch (e) {
    throw new Error(`cannot read ${FIXTURE_REL}, the server answer this layer is graded against: ${(e as Error).message}`);
  }
  const fx = JSON.parse(text) as Fixture;
  if (!Array.isArray(fx.response?.panels) || fx.response.panels.length !== 6) {
    throw new Error(`${FIXTURE_REL} does not hold the 3x2's six panels`);
  }
  return fx;
}
const FX = readFixture();
const REQ = FX.request;
const PANEL_FOV = { fov_x_deg: FX.response.frame_fov_x_deg, fov_y_deg: FX.response.frame_fov_y_deg };

const VIEW = 1000;
// The canvas's own geometry for a view centred on the mosaic, 8 deg wide: the
// whole 5.0 x 2.33 deg grid is inside the square.
const GEOM = {
  centerRaHours: REQ.ra_hours,
  centerDecDeg: REQ.dec_deg,
  pxPerDeg: VIEW / 8,
  view: VIEW,
};

function fixturePanels(states?: PanelState[]): SkyPanel[] {
  return FX.response.panels.map((p, i) => ({
    row: p.row,
    col: p.col,
    ra_hours: p.ra_hours,
    dec_deg: p.dec_deg,
    rotation_deg: p.rotation_deg,
    order: i + 1,
    state: states ? states[i % states.length] : "pending",
  }));
}

// ========================================================= placement
// Mutant "FovOverlay index layout" (placePanels puts each panel where
// FovOverlay draws index (row, col): x = cx + (col - (cols-1)/2)*stepX,
// y = cy + ((rows-1)/2 - row)*stepY in screen px) failed three tests here:
//   x each panel's centre is skyToView of its server coordinates, exactly:
//     panel 1-1 x: got 312.5, expected 687.5 (+-1e-9)
//   x a label shows the run-order number and the row-col, at the panel centre:
//     label left: got 112.5, expected 247.5 (+-0.5)
//   x a point is in the panel whose outline holds it; in an overlap, the
//     nearest centre: a point at panel 1-1's centre hit 2-3
// Re-run after the two cull cases at the bottom were added, it fails those
// too (6/11): "panel 1-1 is on the canvas but has no label (labels: 1-2,
// 2-2)" and "6 panel shape(s) drawn for a grid 20 deg below an 8 deg canvas".
test("each panel's centre is skyToView of its server coordinates, exactly", () => {
  const placed = placePanels(fixturePanels(), PANEL_FOV, 0, GEOM);
  assert(placed.length === 6, `placed ${placed.length} panels, expected all 6`);
  for (const pp of placed) {
    const want = skyToView(pp.panel.ra_hours, pp.panel.dec_deg, GEOM)!;
    near(pp.center.x, want.x, 1e-9, `panel ${panelLabel(pp.panel)} x`);
    near(pp.center.y, want.y, 1e-9, `panel ${panelLabel(pp.panel)} y`);
  }
});

test("labels are 1-based row-col, so server (0,0) reads 1-1", () => {
  assert(panelLabel({ row: 0, col: 0 }) === "1-1", `(0,0) reads ${panelLabel({ row: 0, col: 0 })}`);
  assert(panelLabel({ row: 1, col: 2 }) === "2-3", `(1,2) reads ${panelLabel({ row: 1, col: 2 })}`);
});

// The outline is the frame the camera will actually shoot at that panel: the
// size the grid was TILED for, centred on the panel, at the angle the server
// laid it out at. The optics may have changed since (the MATCH CAMERA banner's
// case), and drawing the live size would show a grid with no gaps that the run
// will shoot with gaps.
//
// Mutant "outline at the fallback angle" (placePanels ignores each panel's
// rotation_deg and uses the fallback) failed:
//   x a panel is outlined at its own angle and the tiled size, through
//     fovCornersSky: panel 1-1 corner 0 x: got 560.5824771967347, expected
//     617.7006742336707 (+-1e-9)
test("a panel is outlined at its own angle and the tiled size, through fovCornersSky", () => {
  // Rotated panels, with a fallback angle that is deliberately different, so
  // a layer reading the wrong angle is visibly wrong.
  const panels = fixturePanels().map((p) => ({ ...p, rotation_deg: 30 }));
  const placed = placePanels(panels, PANEL_FOV, 0, GEOM);
  for (const pp of placed) {
    assert(pp.outline !== null, `panel ${panelLabel(pp.panel)} has no outline`);
    const want = fovCornersSky(
      { ra_hours: pp.panel.ra_hours, dec_deg: pp.panel.dec_deg },
      PANEL_FOV.fov_x_deg, PANEL_FOV.fov_y_deg, 30,
    ).map((c) => skyToView(c.ra_hours, c.dec_deg, GEOM)!);
    pp.outline!.forEach((pt, i) => {
      near(pt.x, want[i].x, 1e-9, `panel ${panelLabel(pp.panel)} corner ${i} x`);
      near(pt.y, want[i].y, 1e-9, `panel ${panelLabel(pp.panel)} corner ${i} y`);
    });
  }
});

test("control: a panel without its own angle takes the canvas's angle", () => {
  const panels = fixturePanels().map((p) => ({ ...p, rotation_deg: undefined }));
  const placed = placePanels(panels, PANEL_FOV, 30, GEOM);
  const pp = placed[0];
  const want = fovCornersSky(
    { ra_hours: pp.panel.ra_hours, dec_deg: pp.panel.dec_deg },
    PANEL_FOV.fov_x_deg, PANEL_FOV.fov_y_deg, 30,
  ).map((c) => skyToView(c.ra_hours, c.dec_deg, GEOM)!);
  near(pp.outline![0].x, want[0].x, 1e-9, "fallback-angle corner x");
});

test("an unknown panel size draws the centre and no invented outline", () => {
  const placed = placePanels(fixturePanels(), null, 0, GEOM);
  assert(placed.length === 6, "a panel with no size was dropped instead of marked");
  assert(placed.every((pp) => pp.outline === null),
    "an outline was drawn with no size to draw it at");
});

// =================================================== states by shape
// A colour-blind reading of one drawn panel: everything that survives a red
// filter, a mono screen and a colour-blind eye. Colour, opacity and class are
// left out ON PURPOSE - they are what this test refuses to count.
function signature(root: any, row: number, col: number): string {
  const grp = root.querySelector(`[data-role="panel"][data-row="${row}"][data-col="${col}"]`);
  assert(grp != null, `no drawn panel for (${row},${col})`);
  const outline = grp.querySelector('[data-mark="outline"]');
  assert(outline != null, `panel (${row},${col}) has no outline path`);
  const fill = outline.getAttribute("fill") ?? "none";
  const hatchId = /^url\(#(.+)\)$/.exec(fill)?.[1] ?? null;
  // A hatch that names a pattern nobody defined draws nothing at all.
  const hatch = hatchId !== null &&
    root.querySelector(`pattern[id="${hatchId}"] line, pattern[id="${hatchId}"] path`) != null;
  const segs = (sel: string): number => {
    const el = grp.querySelector(sel);
    return el ? (el.getAttribute("d") ?? "").split("M").filter((s: string) => s.trim()).length : 0;
  };
  const label = root.querySelector(`[data-role="panel-label"][data-row="${row}"][data-col="${col}"]`);
  const flag = !!label && /!/.test(label.textContent ?? "");
  return [
    `width=${outline.getAttribute("stroke-width")}`,
    `dash=${outline.getAttribute("stroke-dasharray") ?? "solid"}`,
    `hatch=${hatch}`,
    `ticks=${segs('[data-mark="ticks"]')}`,
    `x=${segs('[data-mark="cross"]')}`,
    `flag=${flag}`,
  ].join(" ");
}

function renderLayer(panels: SkyPanel[]): any {
  const host = win.document.createElement("div");
  win.document.body.appendChild(host);
  const root = createRoot(host);
  const placed = placePanels(panels, PANEL_FOV, 0, GEOM);
  act(() => {
    root.render(createElement("div", null,
      createElement("svg", { viewBox: `0 0 ${VIEW} ${VIEW}` },
        createElement(PanelShapes, { placed, view: VIEW, hatchId: "hatch-t", frameCenter: null })),
      createElement(PanelLabels, { placed, scale: 360 / VIEW, boxPx: 360 })));
  });
  return { host, root };
}

// Mutant "state by colour only" (PANEL_SHAPE gives every state the pending
// shape, and the outline's stroke is picked per state from five colours)
// failed:
//   x the five panel states are five different SHAPES, not five colours: the
//     five states draw 1 distinct shape(s) once colour is ignored:
//     {"pending":"width=1.5 dash=solid hatch=false ticks=0 x=0 flag=false",
//     "skipped":"width=1.5 dash=solid hatch=false ticks=0 x=0 flag=false",
//     "shooting":"width=1.5 dash=solid hatch=false ticks=0 x=0 flag=false",
//     "done":"width=1.5 dash=solid hatch=false ticks=0 x=0 flag=false",
//     "set_aside":"width=1.5 dash=solid hatch=false ticks=0 x=0 flag=false"}
test("the five panel states are five different SHAPES, not five colours", () => {
  const states = [...PANEL_STATES];
  assert(states.length === 5 &&
    ["pending", "skipped", "shooting", "done", "set_aside"].every((s) => states.includes(s as PanelState)),
    `the layer's states are ${JSON.stringify(states)}, not the spec's five`);
  const panels = fixturePanels().slice(0, 5).map((p, i) => ({ ...p, state: states[i] }));
  const { host, root } = renderLayer(panels);
  const sigs: Record<string, string> = {};
  panels.forEach((p) => { sigs[p.state] = signature(host, p.row, p.col); });
  act(() => root.unmount());
  const distinct = new Set(Object.values(sigs));
  assert(distinct.size === 5,
    `the five states draw ${distinct.size} distinct shape(s) once colour is ignored: ${JSON.stringify(sigs)}`);

  // And each is the shape the spec's table names, so two states cannot trade
  // drawings and still pass on being different.
  const has = (s: string, part: string) => sigs[s].includes(part);
  const width = (s: string) => Number(/width=([\d.]+)/.exec(sigs[s])![1]);
  assert(has("pending", "dash=solid") && has("pending", "hatch=false") &&
         has("pending", "ticks=0") && has("pending", "x=0") && has("pending", "flag=false"),
    `pending is not a plain thin stroke: ${sigs.pending}`);
  assert(!has("skipped", "dash=solid") && has("skipped", "x=2"),
    `skipped is not dashed with an X: ${sigs.skipped}`);
  assert(width("shooting") >= 2 * width("pending") && has("shooting", "ticks=8") &&
         has("shooting", "dash=solid"),
    `shooting is not a thick stroke with corner ticks: ${sigs.shooting}`);
  assert(has("done", "hatch=true"), `done is not hatched: ${sigs.done}`);
  assert(!has("set_aside", "dash=solid") && has("set_aside", "flag=true"),
    `set aside is not dotted with "!": ${sigs.set_aside}`);
  const dash = (s: string) => /dash=(\S+)/.exec(sigs[s])![1];
  assert(dash("set_aside") !== dash("skipped"),
    `set aside and skipped share one dash pattern (${dash("skipped")}); dotted and dashed must read apart`);
});

test("control: one state drawn twice is one shape (the signature is not noise)", () => {
  const panels = fixturePanels().slice(0, 2).map((p) => ({ ...p, state: "done" as PanelState }));
  const { host, root } = renderLayer(panels);
  const a = signature(host, panels[0].row, panels[0].col);
  const b = signature(host, panels[1].row, panels[1].col);
  act(() => root.unmount());
  assert(a === b, `two done panels read differently: ${a} / ${b}`);
});

test("a label shows the run-order number and the row-col, at the panel centre", () => {
  const panels = fixturePanels();
  const { host, root } = renderLayer(panels);
  const label = host.querySelector('[data-role="panel-label"][data-row="0"][data-col="0"]');
  assert(label != null, "no label for panel 1-1");
  const text = label.textContent ?? "";
  assert(text.includes("1-1"), `panel (0,0)'s label reads ${JSON.stringify(text)}`);
  const order = label.querySelector('[data-role="panel-order"]');
  assert(order?.textContent === "1", `panel 1-1's run-order number reads ${order?.textContent}`);
  const want = skyToView(panels[0].ra_hours, panels[0].dec_deg, GEOM)!;
  near(parseFloat(label.style.left), (want.x * 360) / VIEW, 0.5, "label left");
  near(parseFloat(label.style.top), (want.y * 360) / VIEW, 0.5, "label top");
  act(() => root.unmount());
});

// ============================================================ hit test
test("a point is in the panel whose outline holds it; in an overlap, the nearest centre", () => {
  const placed = placePanels(fixturePanels(), PANEL_FOV, 0, GEOM);
  for (const pp of placed) {
    const hit = panelAt(placed, pp.center.x, pp.center.y, 20);
    assert(hit?.panel.row === pp.panel.row && hit?.panel.col === pp.panel.col,
      `a point at panel ${panelLabel(pp.panel)}'s centre hit ${hit ? panelLabel(hit.panel) : "nothing"}`);
  }
  // Between 1-1 and 1-2, a little nearer 1-1: inside both outlines (the 25%
  // overlap), so the nearer centre must win.
  const a = placed.find((pp) => pp.panel.row === 0 && pp.panel.col === 0)!;
  const b = placed.find((pp) => pp.panel.row === 0 && pp.panel.col === 1)!;
  const x = a.center.x + (b.center.x - a.center.x) * 0.45;
  const y = a.center.y + (b.center.y - a.center.y) * 0.45;
  const hit = panelAt(placed, x, y, 20);
  assert(hit?.panel.col === 0, `the overlap point nearer 1-1 hit ${hit ? panelLabel(hit.panel) : "nothing"}`);
  assert(panelAt(placed, 5, 5, 20) === null, "a corner of the canvas far from every panel hit a panel");
});

// Added by the S4-USKY re-verification (after the limit reset), with the next
// case and the label-layer clip case at the bottom; their mutants ran in a
// private scratch copy of ui/ (scratchpad s4-usky-verify2-mut), never in the
// shared tree (#254). The case above
// has only a point nearer 1-1, and 1-1 is also FIRST in the server's list, so
// "the first outline that holds the point wins" passed it 11/11. In the overlap
// that is the wrong panel on 1-2's side, and a tap there would toggle the skip
// of a panel the operator did not touch. The mirror point, nearer 1-2, is what
// tells "nearest centre" from "first in the list".
//
// Mutant "overlap goes to the first panel" (panelAt's `hit && d < bestD`
// narrowed to `hit && best === null`) failed:
//   x in an overlap the nearest centre wins, not the first panel in the list:
//     the overlap point nearer 1-2 hit 1-1
test("in an overlap the nearest centre wins, not the first panel in the list", () => {
  const placed = placePanels(fixturePanels(), PANEL_FOV, 0, GEOM);
  const a = placed.find((pp) => pp.panel.row === 0 && pp.panel.col === 0)!;
  const b = placed.find((pp) => pp.panel.row === 0 && pp.panel.col === 1)!;
  assert(placed.indexOf(a) < placed.indexOf(b), "precondition: 1-1 is not ahead of 1-2 in the list");
  const x = a.center.x + (b.center.x - a.center.x) * 0.55;
  const y = a.center.y + (b.center.y - a.center.y) * 0.55;
  // Inside BOTH outlines (the 25% overlap spans 1/3 to 2/3 of the step), or
  // the case would not be about an overlap at all.
  assert(panelAt([a], x, y, 20) !== null && panelAt([b], x, y, 20) !== null,
    "precondition: the point is not inside both 1-1 and 1-2");
  const hit = panelAt(placed, x, y, 20);
  assert(hit?.panel.row === 0 && hit?.panel.col === 1,
    `the overlap point nearer 1-2 hit ${hit ? panelLabel(hit.panel) : "nothing"}`);
});

// A panel with no outline (no tiled size and no optics) is still a panel the
// operator can tap: panelAt's docstring promises a hit within `radius` of its
// centre, and nothing held it.
//
// Mutant "radius fallback dead" (panelAt's no-outline arm answers false)
// failed:
//   x a panel with no outline is hit within the radius of its centre and
//     missed beyond it: a point 19 px from panel 1-1's centre, with no outline
//     and a 20 px radius, hit nothing
test("a panel with no outline is hit within the radius of its centre and missed beyond it", () => {
  const placed = placePanels(fixturePanels(), null, 0, GEOM);
  assert(placed.every((pp) => pp.outline === null), "precondition: a panel has an outline with no size given");
  const c = placed[0].center;
  const inside = panelAt(placed, c.x + 19, c.y, 20);
  assert(inside?.panel.row === 0 && inside?.panel.col === 0,
    `a point 19 px from panel 1-1's centre, with no outline and a 20 px radius, hit ${inside ? panelLabel(inside.panel) : "nothing"}`);
  const beyond = panelAt(placed, c.x + 21, c.y, 20);
  assert(beyond === null,
    `a point 21 px from panel 1-1's centre, with a 20 px radius, hit ${beyond ? panelLabel(beyond.panel) : "nothing"}`);
});

// ============================================================ off the canvas
// Added by the S4-USKY verifier: two culls the layer does that no case above
// could fail on (each mutant below passed the file 9/9 before these existed).
// Their mutants ran in a private scratch copy of ui/ (scratchpad
// s4-usky-verify-mut), never in the shared tree (#254).

// A view 2 deg across, centred on panel 1-1: 1-2 and 1-3 are 1.5 and 3 deg
// east of it, off the left edge.
const NEAR_1_1 = {
  centerRaHours: FX.response.panels[0].ra_hours,
  centerDecDeg: FX.response.panels[0].dec_deg,
  pxPerDeg: VIEW / 2,
  view: VIEW,
};

// A label for a panel whose centre is off the canvas is a button nobody can
// see: Tab would land on it, and Enter would toggle a panel's skip from off
// screen.
//
// Mutant "panel labels not culled" (PanelLabels' on-canvas filter answers
// true for every panel) failed:
//   x a panel whose centre is off the canvas gets no label, so the keyboard
//     cannot land on it: panel 1-2 is off the canvas at x=-250 but has a
//     label (labels: 1-1, 1-2, 1-3, 2-3, 2-2, 2-1)
test("a panel whose centre is off the canvas gets no label, so the keyboard cannot land on it", () => {
  const placed = placePanels(fixturePanels(), PANEL_FOV, 0, NEAR_1_1);
  const onCanvas = (pp: any) => pp.center.x >= 0 && pp.center.x <= VIEW && pp.center.y >= 0 && pp.center.y <= VIEW;
  const off = placed.filter((pp) => !onCanvas(pp));
  assert(off.some((pp) => pp.panel.row === 0 && pp.panel.col === 2) && placed.some(onCanvas),
    "precondition: this view does not put 1-3 off the canvas and 1-1 on it");
  const host = win.document.createElement("div");
  win.document.body.appendChild(host);
  const root = createRoot(host);
  act(() => {
    root.render(createElement(PanelLabels, {
      placed, scale: 360 / VIEW, boxPx: 360, onPanelTap: () => {},
    }));
  });
  const drawn = Array.from(host.querySelectorAll('[data-role="panel-label"]')) as any[];
  const names = drawn.map((el) => `${Number(el.getAttribute("data-row")) + 1}-${Number(el.getAttribute("data-col")) + 1}`);
  act(() => root.unmount());
  for (const pp of off) {
    assert(!names.includes(panelLabel(pp.panel)),
      `panel ${panelLabel(pp.panel)} is off the canvas at x=${pp.center.x.toFixed(0)} but has a label (labels: ${names.join(", ")})`);
  }
  assert(names.includes("1-1"), `panel 1-1 is on the canvas but has no label (labels: ${names.join(", ")})`);
});

// The cull is by the panel CENTRE, so a label whose centre is just inside the
// edge still sticks half its width past it. The layer's clip is what stops
// that widening the page on a phone (the "Object size" legend was measured
// doing exactly that at 390 px). jsdom lays nothing out, so the clip is held
// by its class, as the pointer-events case in skyCanvasPanels holds its.
// Added by the S4-USKY re-verification.
//
// Mutant "label layer unclipped" (`overflow-hidden` dropped from PanelLabels'
// layer) failed:
//   x the panel-label layer clips, so a label at the edge cannot widen the
//     page: the panel-label layer's class is "absolute inset-0
//     pointer-events-none", with no overflow-hidden
test("the panel-label layer clips, so a label at the edge cannot widen the page", () => {
  const { host, root } = renderLayer(fixturePanels());
  const layer = host.querySelector('[data-role="panel-labels"]');
  const cls = layer ? (layer.getAttribute("class") ?? "") : "";
  act(() => root.unmount());
  assert(layer != null, "no panel-label layer drawn for six on-canvas panels");
  assert(cls.split(/\s+/).includes("overflow-hidden"),
    `the panel-label layer's class is ${JSON.stringify(cls)}, with no overflow-hidden`);
});

// Off-canvas is normal (the grid is glued to the sky), and a path millions of
// units wide is work with nothing to show for it: same cull as PointingFrame.
//
// Mutant "panel shapes not culled" (the `stray > view * 1.5` arm dropped from
// PanelShapes' cull) failed:
//   x a panel far off the canvas draws no shape; the centred view draws all
//     six: 6 panel shape(s) drawn for a grid 20 deg below an 8 deg canvas
test("a panel far off the canvas draws no shape; the centred view draws all six", () => {
  // The cull is 1.5 views (1500 units) from the centre; 20 deg below is 2500.
  const away = { ...GEOM, centerDecDeg: REQ.dec_deg + 20 };
  const host = win.document.createElement("div");
  win.document.body.appendChild(host);
  const root = createRoot(host);
  const draw = (geom: typeof GEOM): number => {
    act(() => {
      root.render(createElement("svg", { viewBox: `0 0 ${VIEW} ${VIEW}` },
        createElement(PanelShapes, {
          placed: placePanels(fixturePanels(), PANEL_FOV, 0, geom),
          view: VIEW, hatchId: "hatch-cull", frameCenter: null,
        })));
    });
    return host.querySelectorAll('[data-role="panel"]').length;
  };
  const centred = draw(GEOM);
  const far = draw(away);
  act(() => root.unmount());
  assert(centred === 6, `control: the centred view drew ${centred} panel shapes, expected 6`);
  assert(far === 0, `${far} panel shape(s) drawn for a grid 20 deg below an 8 deg canvas`);
});

// ------------------------------------------------------------------ report
const total = passed + failed;
console.log(`panelLayer.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
export default result;
