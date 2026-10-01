// mosaicNightHeading.test.tsx - both night cards write a grid's size columns x
// rows (#339 residue; S4 orchestrator ruling 1, owner list item 31; spec
// 2026-09-23 flows mosaic, 2.4 PANELS).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/atlas/__tests__/mosaicNightHeading.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// A grid size is written columns x rows, like the camera field (width x
// height): the eighth Example "M31 3x2" is 3 columns by 2 rows, and the
// Example's name, the card footer, the modal's readout strip, the brief, the
// compile's skip note and doctor M6 all say it that way. Panel labels stay
// row-column ("2-1" is row 2, column 1), and that pull is what put the two
// night cards' headings rows first: "ACROSS THE 2x3 MOSAIC" for the 3x2, in
// the Target modal's PANELS section and the Sky hub's framing card
// (MosaicNightCard), and under the classic Atlas's visibility panel
// (MosaicNight).
//
// Each card is rendered for a grid of 3 columns by 2 rows and for its
// transpose (2 columns by 3 rows), so a heading that is right for one shape
// only cannot pass. The control is a square grid (3 by 3), which reads the
// same in either order: it stays green under the rows-first mutant, which
// shows that what turns the other cases red is the order of the two numbers,
// not a heading the harness failed to find or read.
//
// Every mutant below was run in a private scratch copy of ui/ (scratchpad
// S5-SKY-mut), never in the shared tree (#254), and the failure it produced is
// quoted verbatim.

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
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
// Both cards ask POST /api/framing/mosaic for the panels' altitudes after a
// 400 ms settle. The heading is drawn before that, and every render below is
// unmounted well inside the settle, so nothing here should reach the network.
win.fetch = async (url: string) => { throw new Error(`no network in this fixture: ${url}`); };
win.WebSocket = class { close() {} addEventListener() {} send() {} };
const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "KeyboardEvent", "localStorage", "sessionStorage",
  "getComputedStyle", "matchMedia", "ResizeObserver", "requestAnimationFrame",
  "cancelAnimationFrame", "fetch", "WebSocket", "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../store");
const { MosaicNight } = await import("../MosaicNight");
const { MosaicNightCard } = await import("../../../next/hubs/sky/frame/MosaicNightCard");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: unknown, msg: string): void { if (!cond) throw new Error(msg); }

// Optics that give a real field (1.68 x 1.15 deg): both cards draw nothing at
// all without one, since the panel positions would be a guess.
const OPTICS = {
  focal_length_mm: 530,
  pixel_size_um: 3.76,
  sensor_width_px: 4144,
  sensor_height_px: 2822,
};

const container = win.document.getElementById("root") as any;

/** Render `el`, hand back the text of the node `find` picks, and unmount at
 *  once (inside the cards' 400 ms settle). */
function renderRead(el: any, find: (root: any) => any): { text: string; node: any } {
  const root = createRoot(container);
  act(() => { root.render(el); });
  const node = find(container);
  const text = node ? String(node.textContent ?? "").replace(/\s+/g, " ").trim() : "";
  const klass = node ? String(node.className ?? "") : "";
  act(() => { root.unmount(); });
  return { text, node: node ? { className: klass } : null };
}

// ================================================= #/next: MosaicNightCard
function card(cols: number, rows: number): string {
  const got = renderRead(
    createElement(MosaicNightCard, {
      raHours: 0.7123, decDeg: 41.269, rows, cols, overlap: 0.25, rotationDeg: 0,
      fovXDeg: 2.0, fovYDeg: 1.33, altLimitDeg: 20,
    }),
    (c) => Array.from(c.querySelectorAll('[data-testid="sky-mosaic-night"] div'))
      .find((d: any) => /MOSAIC/.test(d.textContent ?? "") && d.children.length === 0),
  );
  assert(got.node, "the card drew no heading at all - nothing below can grade its words");
  return got.text;
}

// Mutant "rows-first heading, the card" (MosaicNightCard.tsx writes
// `ACROSS THE ${p.rows}×${p.cols} MOSAIC`, as it did before this change):
//   failed, 4/6 (run in scratchpad S5-SKY-mut):
//   x MosaicNightCard heads a grid of 3 columns by 2 rows 'ACROSS THE 3×2
//     MOSAIC': the 3x2 (3 columns by 2 rows) reads "ACROSS THE 2×3 MOSAIC"
//   x MosaicNightCard heads the transpose, 2 columns by 3 rows, 'ACROSS THE
//     2×3 MOSAIC': the 2x3 (2 columns by 3 rows) reads "ACROSS THE 3×2 MOSAIC"
test("MosaicNightCard heads a grid of 3 columns by 2 rows 'ACROSS THE 3×2 MOSAIC'", () => {
  const text = card(3, 2);
  assert(text === "ACROSS THE 3×2 MOSAIC",
    `the 3x2 (3 columns by 2 rows) reads ${JSON.stringify(text)}`);
});

test("MosaicNightCard heads the transpose, 2 columns by 3 rows, 'ACROSS THE 2×3 MOSAIC'", () => {
  const text = card(2, 3);
  assert(text === "ACROSS THE 2×3 MOSAIC",
    `the 2x3 (2 columns by 3 rows) reads ${JSON.stringify(text)}`);
});

test("control: MosaicNightCard heads a square 3 by 3 'ACROSS THE 3×3 MOSAIC', in either order", () => {
  const text = card(3, 3);
  assert(text === "ACROSS THE 3×3 MOSAIC", `the 3x3 reads ${JSON.stringify(text)}`);
});

// ============================================= classic Atlas: MosaicNight
// MosaicNight reads the grid from the global framing session, as the classic
// Atlas does, and writes its heading in sentence case under the `.label`
// class, which index.css uppercases. What the operator reads is therefore the
// upper-cased text, and the rule that makes it so is read, not assumed.
const INDEX_CSS_REL = "../../../index.css";
function labelUppercases(): boolean {
  let css: string;
  try {
    css = readFileSync(new URL(INDEX_CSS_REL, import.meta.url), "utf8") as string;
  } catch (e) {
    throw new Error(`cannot read ${INDEX_CSS_REL}: ${(e as Error).message}`);
  }
  const rule = /(?:^|\n)\.label\s*\{([^}]*)\}/.exec(css.replace(/\/\*[\s\S]*?\*\//g, ""));
  return !!rule && /text-transform\s*:\s*uppercase/.test(rule[1]);
}

function atlas(cols: number, rows: number): string {
  act(() => {
    useStore.setState({
      config: { optics: OPTICS } as any,
      framing: { mosaic: { rows, cols, overlap: 0.25 }, rotation_deg: 0 } as any,
      status: null as any,
    });
  });
  const got = renderRead(
    createElement(MosaicNight, { raHours: 0.7123, decDeg: 41.269, altLimitDeg: 20 }),
    (c) => Array.from(c.querySelectorAll("span"))
      .find((s: any) => /mosaic/i.test(s.textContent ?? "")),
  );
  assert(got.node, "MosaicNight drew no heading at all - nothing below can grade its words");
  assert(/(^|\s)label(\s|$)/.test(got.node.className),
    `the heading is not a .label (class ${JSON.stringify(got.node.className)}), so nothing says it is upper-cased`);
  return got.text.toUpperCase();
}

// Mutant "rows-first heading, the Atlas" (MosaicNight.tsx writes
// `Across the {rows}×{cols} mosaic`, as it did before this change):
//   failed, 4/6 (run in scratchpad S5-SKY-mut):
//   x MosaicNight heads a grid of 3 columns by 2 rows 'ACROSS THE 3×2
//     MOSAIC': the 3x2 (3 columns by 2 rows) reads "ACROSS THE 2×3 MOSAIC"
//   x MosaicNight heads the transpose, 2 columns by 3 rows, 'ACROSS THE 2×3
//     MOSAIC': the 2x3 (2 columns by 3 rows) reads "ACROSS THE 3×2 MOSAIC"
test("MosaicNight heads a grid of 3 columns by 2 rows 'ACROSS THE 3×2 MOSAIC'", () => {
  assert(labelUppercases(), "index.css's .label rule no longer upper-cases its text");
  const text = atlas(3, 2);
  assert(text === "ACROSS THE 3×2 MOSAIC",
    `the 3x2 (3 columns by 2 rows) reads ${JSON.stringify(text)}`);
});

test("MosaicNight heads the transpose, 2 columns by 3 rows, 'ACROSS THE 2×3 MOSAIC'", () => {
  const text = atlas(2, 3);
  assert(text === "ACROSS THE 2×3 MOSAIC",
    `the 2x3 (2 columns by 3 rows) reads ${JSON.stringify(text)}`);
});

test("control: MosaicNight heads a square 3 by 3 'ACROSS THE 3×3 MOSAIC', in either order", () => {
  const text = atlas(3, 3);
  assert(text === "ACROSS THE 3×3 MOSAIC", `the 3x3 reads ${JSON.stringify(text)}`);
});

// ------------------------------------------------------------------- report
dom.window.close();
const total = passed + failed;
console.log(`mosaicNightHeading.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
export default result;
