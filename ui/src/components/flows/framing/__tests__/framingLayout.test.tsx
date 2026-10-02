// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// framingLayout.test.tsx - the Target modal's layout contract (#189 S4 item 4;
// spec 2026-09-23 flows mosaic, 2.2).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/framing/__tests__/framingLayout.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT IS WORTH GUARDING HERE
//   1. The sky is pinned OUTSIDE the scroller. A sky inside the scroller
//      scrolls the grid being edited off the screen and fights the scroller
//      for every drag (the Atlas scroll-trap lesson).
//   2. Phone landscape is chosen by the sheet's shape, a container query on
//      its aspect, never by `lg:`: a phone in landscape is 667-932 px wide and
//      a width breakpoint would keep it in the portrait stack.
//   3. The numbers of spec 2.2: a 48 px header, a sky of min(100vw, 52svh)
//      that is `flex: none` and shrinks to 40svh while a text field has focus,
//      a 32 px strip, 44 px rows, and a 360 px control column beside the sky
//      on a tablet or desktop, in Overlay's `full` variant.
//
// jsdom computes no layout, so the arrangement is graded on the two things
// that decide it, the DOM's nesting and framing.css's rules, parsed below.
// The live sheet was ALSO measured in headless Chromium at five viewports;
// the boxes are recorded beside the test they back.
//
// Every mutant below was run in a private scratch copy of ui/ (scratchpad
// s4-umodal-mut), never in the shared tree (#254), and the failure it
// produced is quoted verbatim. After the limit reset every one was run
// again against the current tree in s4-umodal-r2-mut (2026-09-27), and
// each was red with the failure quoted.

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
win.HTMLCanvasElement.prototype.getContext = () => null;
win.URL.createObjectURL = () => "blob:stub";
win.URL.revokeObjectURL = () => {};
win.fetch = async (url: string) => { throw new Error(`no network in this fixture: ${url}`); };
win.WebSocket = class { close() {} addEventListener() {} send() {} };
const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement", "HTMLSelectElement",
  "HTMLTextAreaElement", "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "FocusEvent", "localStorage", "sessionStorage", "getComputedStyle", "matchMedia", "ResizeObserver",
  "requestAnimationFrame", "cancelAnimationFrame", "Image", "URL", "fetch", "Blob", "WebSocket",
  "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------------- imports
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../store");
const { FLOWS_INIT } = await import("../../flowsSlice");
const { NODE_DEFS } = await import("../../nodeDefs");
const { framingApi, framingTiming } = await import("../framingApi");
const Sheet = (await import("../TargetFramingSheet")).default;

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: unknown, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (JSON.stringify(got) !== JSON.stringify(want)) {
    throw new Error(`${msg}: expected ${JSON.stringify(want)}, got ${JSON.stringify(got)}`);
  }
}

// ------------------------------------------------------------------ the css
const CSS_REL = "../framing.css";
function readCss(): string {
  try {
    return readFileSync(new URL(CSS_REL, import.meta.url), "utf8") as string;
  } catch (e) {
    throw new Error(`cannot read ${CSS_REL}: ${(e as Error).message}`);
  }
}

/** One style rule: the at-rule preludes it sits inside (outermost first),
 *  its selector list, and its declarations IN ORDER (a later declaration of
 *  a property wins, which is how the vh fallbacks are written). */
interface Rule { at: string[]; selector: string; decls: [string, string][] }

/** A small CSS reader: comments stripped, blocks nested to any depth. Enough
 *  for framing.css, which uses no strings containing braces. */
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
      if (prelude.startsWith("@")) { block([...at, prelude]); continue; }
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

const RULES = parseCss(readCss());

/** The rules whose selector list includes `sel` exactly. */
function rulesFor(sel: string, at?: (a: string[]) => boolean): Rule[] {
  return RULES.filter((r) => r.selector.split(",").map((s) => s.trim()).includes(sel) && (at ? at(r.at) : true));
}
/** The value a property ends with in one rule (the last declaration wins). */
function last(r: Rule | undefined, prop: string): string | null {
  if (!r) return null;
  const hits = r.decls.filter(([p]) => p === prop);
  return hits.length ? hits[hits.length - 1][1] : null;
}
const top = (a: string[]) => a.length === 0;
/** An at-rule condition that reads the sheet's SHAPE: its aspect or its
 *  orientation, on the container. */
const byShape = (a: string[]) => a.length === 1 && /^@container\b/.test(a[0])
  && /\b(min-aspect-ratio|aspect-ratio|orientation)\b/.test(a[0]);
/** A condition that keys off width alone: the breakpoint shape. */
const widthOnly = (prelude: string) =>
  /\(\s*min-width\s*:/.test(prelude) && !/aspect-ratio|orientation|height/.test(prelude);

// ------------------------------------------------------------------ the mount
(framingApi as any).mosaic = () => new Promise(() => {});
(framingApi as any).compileDraft = () => new Promise(() => {});
framingTiming.settleMs = 0;
const graph = {
  nodes: [
    { id: "n2", type: "target", x: 0, y: 0, params: {
      ...NODE_DEFS.target.params, name: "M31", ra: "00h 42m 44s", dec: "+41 00 00",
      rows: 2, cols: 3, overlap: 25, fovX: 2.0, fovY: 1.33, rotation: 0, angle: "Rotate to PA",
      counts: "Accepted subs" } },
    { id: "cy", type: "cycle", x: 260, y: 0, params: { ...NODE_DEFS.cycle.params } },
  ],
  edges: [{ id: "a", from: "n2", fromPort: "target", to: "cy", toPort: "run" }],
};
useStore.setState({
  principal: { role: "operator", email: null, caps: ["view.status", "view.site_derived"] },
  wsConnected: false, status: null, config: null, site: null,
  flows: { ...FLOWS_INIT, graph,
    record: { id: "f1", name: "M31 mosaic", folder: "", tagline: "", graph, created_ts: 0,
      updated_ts: 0, last_run: null, last_result: "", readonly: false } },
} as any);
const root = createRoot(win.document.getElementById("root"));
act(() => { root.render(createElement(Sheet, { nodeId: "n2", onClose: () => {} })); });
const doc = win.document;
const q = (id: string) => doc.querySelector(`[data-testid="${id}"]`) as any;
const sheet = q("target-framing-sheet");

// ======================================================================
// 1. the sky is pinned outside the scroller

// MEASURED IN HEADLESS CHROMIUM, LIVE (scratchpad s4-umodal-mut/probe: this
// sheet mounted by React on a seeded store, with the app's own built
// stylesheet and framing.css, served from 127.0.0.1), boxes as x,y w x h in
// CSS px:
//   phone portrait 390 x 844:   header 0,0 390x48; sky 0,78 390x390, canvas
//                               390x390; strip 0,468 390x32; scroller 0,500
//                               390x344 of 44 px rows; sky 390x338 (40svh)
//                               with the RA field focused
//   phone landscape 844 x 390:  sky 0,78 464x312 (55%), canvas 312x312
//                               centred in it; controls 464,78 380x312
//   small landscape 667 x 375:  sky 0,78 367x297, canvas 297x297; controls
//                               367,78 300x297
//   tablet portrait 820 x 1180: sky 0,78 460x1102, canvas 460x460; controls
//                               460,78 360 wide
//   desktop 1280 x 800:         sky 0,78 920x722, canvas 680x680 (its 85svh
//                               cap); controls 920,78 360 wide
// At every one, each panel label sat on its drawn panel (0 px between their
// centres), the canvas lay inside the sky, nothing scrolled sideways and the
// page logged no error. The 30 px between the header and the sky is the
// offline chip line. A first, static form of the probe (markup frozen by
// jsdom, utilities hand-picked) could not see SkyCanvas's 320 px floor;
// the built stylesheet showed it pushing a landscape canvas to 320 x 320 in
// a 312 px sky, which the canvas-floor rule below now lifts.

// MUTANT "sky inside the scroller" (the `.tfs-sky` div moved to be the first
// child of the `.tfs-scroller` div). Observed:
//   x the sky sits outside the scroller, beside the strip and scroller, never scrolled with them:
//     the sky is inside the scroller
test("the sky sits outside the scroller, beside the strip and scroller, never scrolled with them", () => {
  const sky = q("framing-sky");
  const scroller = q("framing-scroller");
  const strip = q("framing-strip");
  assert(sky && scroller && strip, "the sheet lacks its sky, strip or scroller");
  assert(!scroller.contains(sky), "the sky is inside the scroller");
  assert(!sky.contains(scroller), "the scroller is inside the sky");
  const body = sky.parentElement;
  eq(body?.className, "tfs-body", "the sky's parent");
  const controls = scroller.parentElement;
  eq(controls?.className, "tfs-controls", "the scroller's parent");
  eq(controls.parentElement === body, true, "the sky and the controls are siblings in .tfs-body");
  eq(Array.from(body.children).indexOf(sky) < Array.from(body.children).indexOf(controls), true,
    "the sky comes before the controls (left, or on top)");
  eq(controls.firstElementChild === strip && strip.nextElementSibling === scroller, true,
    "the strip is between the sky and the scroller, outside the scroller");
  // The canvas itself is in the sky.
  assert(sky.querySelector('[role="application"]'), "the SkyCanvas is not in the pinned sky");
  // And the header is the sheet's first row, outside everything that moves.
  eq(sheet.firstElementChild === q("framing-header"), true, "the header is the sheet's first row");
});

// ======================================================================
// 2. landscape by shape, never by lg:

// MUTANT "lg: breakpoint" (the landscape container query's prelude replaced
// by `@media (min-width: 1024px)`). Observed:
//   x phone landscape is chosen by a container query on the sheet's aspect, never by a width
//     breakpoint: no container query on the sheet's aspect and height chooses the landscape
//     arrangement
// MUTANT "lg: class" (the `.tfs-body` div given `className="tfs-body flex
// flex-col lg:flex-row"`, the breakpoint written as a utility). Observed:
//   x phone landscape is chosen by a container query on the sheet's aspect, never by a width
//     breakpoint: sheet elements with a breakpoint class: expected [], got ["tfs-body flex flex-col
//     lg:flex-row"]
//   (The nesting test went red with it too, on the body's class.)
test("phone landscape is chosen by a container query on the sheet's aspect, never by a width breakpoint", () => {
  const land = RULES.filter((r) => byShape(r.at) && /\(\s*max-height\s*:\s*540px\s*\)/.test(r.at[0]));
  assert(land.length > 0, "no container query on the sheet's aspect and height chooses the landscape arrangement");
  const sky = land.find((r) => r.selector.endsWith(".tfs-sky"));
  const body = land.find((r) => r.selector.endsWith(".tfs-body"));
  eq(last(sky, "width"), "55%", "the landscape sky's width");
  eq(last(body, "flex-direction"), "row", "the landscape body's direction");
  eq(/@container tfs\b/.test(land[0].at[0]), true, `the query names the sheet's container: ${land[0].at[0]}`);
  // No rule anywhere arranges the sheet off a width-only condition...
  for (const r of RULES) {
    for (const a of r.at) {
      assert(!(widthOnly(a) && !/min-height/.test(a)),
        `a width-only condition arranges the sheet: ${a} { ${r.selector} }`);
    }
  }
  // ...and no element of the sheet carries a breakpoint utility.
  const bp = /(^|\s)(sm|md|lg|xl|2xl):/;
  const offenders = Array.from(sheet.querySelectorAll("*") as any[])
    .filter((el: any) => typeof el.className === "string" && /(^|\s)tfs-/.test(el.className) && bp.test(el.className))
    .map((el: any) => el.className);
  eq(offenders, [], "sheet elements with a breakpoint class");
  assert(!bp.test(sheet.className), `the sheet root has a breakpoint class: ${sheet.className}`);
});

// ======================================================================
// 3. spec 2.2's numbers

// MUTANT "canvas floor" (the `.tfs-sky-fit [role="application"]` rule
// removed from framing.css). Observed:
//   x the header is 48 px, the sky min(100vw, 52svh) and flex none, the strip 32 px, rows 44 px, the
//     column 360 px: the canvas floor inside the sky: expected "0", got null
// MUTANT "sky shrinks" (`flex: none` removed from `.tfs-sky`). Observed:
//   x the header is 48 px, the sky min(100vw, 52svh) and flex none, the strip 32 px, rows 44 px, the
//     column 360 px: the sky's flex (a squeezable sky shrinks to its borders): expected "none", got
//     null
test("the header is 48 px, the sky min(100vw, 52svh) and flex none, the strip 32 px, rows 44 px, the column 360 px", () => {
  eq(last(rulesFor(".tfs-head", top)[0], "height"), "48px", "the header's height");
  eq(last(rulesFor(".tfs-head", top)[0], "position"), "sticky", "the header's position");
  const sky = rulesFor(".tfs-sky", top)[0];
  eq(last(sky, "height"), "min(100vw, 52svh)", "the sky's height");
  eq(sky?.decls.filter(([p]) => p === "height").map(([, v]) => v), ["min(100vw, 52vh)", "min(100vw, 52svh)"],
    "the sky's height, with the vh fallback first");
  eq(last(sky, "flex"), "none", "the sky's flex (a squeezable sky shrinks to its borders)");
  const typing = rulesFor('.tfs[data-typing="true"] .tfs-sky', top)[0];
  eq(last(typing, "height"), "40svh", "the sky's height while a text field has focus");
  // SkyCanvas's own 320 px floor would push a landscape phone's canvas past
  // its 312 px sky (measured with the app's built stylesheet); the sheet
  // lifts it, so the fit box alone sizes the square.
  eq(last(rulesFor('.tfs-sky-fit [role="application"]', top)[0], "min-width"), "0",
    "the canvas floor inside the sky");
  eq(last(rulesFor(".tfs-sky-fit", top)[0], "width"), "min(100cqw, 100cqh)", "the fit box's square");
  eq(last(rulesFor(".tfs-strip", top)[0], "min-height"), "32px", "the strip's height");
  eq(last(rulesFor(".tfs-row", top)[0], "min-height"), "44px", "a row's height");
  eq(last(rulesFor(".tfs-btn", top)[0], "min-height"), "44px", "a button's height");
  eq(last(rulesFor(".tfs-scroller", top)[0], "overflow-y"), "auto", "the scroller scrolls");
  eq(last(rulesFor(".overlay-body.tfs-host", top)[0], "overflow"), "hidden", "the overlay body does not scroll");
  const wide = RULES.filter((r) => r.at.length === 1 && /^@container tfs\b/.test(r.at[0])
    && /min-width\s*:\s*720px/.test(r.at[0]) && /min-height/.test(r.at[0]));
  eq(last(wide.find((r) => r.selector.endsWith(".tfs-controls")), "width"), "360px", "the tablet/desktop column");
  eq(last(wide.find((r) => r.selector.endsWith(".tfs-body")), "flex-direction"), "row", "the tablet/desktop body");
  // The side-by-side sky outranks the typing rule: three selectors deep,
  // later in the file.
  for (const r of [...wide, ...RULES.filter((x) => byShape(x.at))].filter((x) => x.selector.endsWith(".tfs-sky"))) {
    eq(r.selector, ".tfs .tfs-body > .tfs-sky", "a side-by-side sky's selector");
    eq(last(r, "height"), "auto", "a side-by-side sky's height");
  }
});

// MUTANT "typing never shrinks" (the scroller's onFocus handler removed).
// Observed:
//   x a text field with focus marks the sheet as typing, and leaving it clears the mark: typing with
//     the RA field focused: expected "true", got null
// MUTANT "blur never clears" (the scroller's onBlur handler made a no-op).
// A move to another control INSIDE the scroller is the focus handler's, so
// only focus leaving the scroller altogether reaches this one. Observed
// (verifier, scratch copy s4-umodal-verify-mut):
//   x a text field with focus marks the sheet as typing, and leaving it clears the mark: typing with
//     focus in the header: expected null, got "true"
test("a text field with focus marks the sheet as typing, and leaving it clears the mark", () => {
  const ra = doc.querySelector("#tfs-ra") as any;
  assert(ra, "no RA field");
  eq(sheet.getAttribute("data-typing"), null, "typing before any focus");
  act(() => { ra.focus(); });
  eq(sheet.getAttribute("data-typing"), "true", "typing with the RA field focused");
  const btn = Array.from(q("framing-scroller").querySelectorAll("button") as any[])[0] as any;
  act(() => { btn.focus(); });
  eq(sheet.getAttribute("data-typing"), null, "typing with a button focused");
  // Focus leaving the scroller for the header: the sky must grow back.
  act(() => { ra.focus(); });
  eq(sheet.getAttribute("data-typing"), "true", "typing with the RA field focused again");
  const cancel = q("framing-header").querySelector("button") as any;
  assert(cancel && !q("framing-scroller").contains(cancel), "the header's button is inside the scroller");
  act(() => { cancel.focus(); });
  eq(sheet.getAttribute("data-typing"), null, "typing with focus in the header");
});

// MUTANT "sheet not full" (the sheet's Overlay variant "center"). Observed:
//   x the sheet is Overlay's full variant at every width, and the body scroller is the sheet's own:
//     the surface is not full: overlay-surface rounded-t-2xl sheet-enter
test("the sheet is Overlay's full variant at every width, and the body scroller is the sheet's own", () => {
  const surface = sheet.closest(".overlay-surface");
  assert(surface, "the sheet is not in an Overlay surface");
  assert(/(^|\s)overlay-full(\s|$)/.test(surface.className), `the surface is not full: ${surface.className}`);
  const body = sheet.parentElement;
  assert(/(^|\s)tfs-host(\s|$)/.test(body.className), `the overlay body lacks tfs-host: ${body.className}`);
});

act(() => { root.render(null); });

// ------------------------------------------------------------------ report
const total = passed + failed;
console.log(`framingLayout.test: ${passed}/${total} passed`);
for (const f of failures) console.log(f);
export const result = { passed, failed, total };
export default result;
