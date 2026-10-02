// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// overlay.test.ts — regression guard for the ONE overlay primitive and the two
// CSS rules that made every overlay in the app land off the viewport.
//
// Run directly:  npx tsx src/__tests__/overlay.test.ts
//
// These are not presentational assertions. Each one pins a property that was
// MEASURED broken in the four-persona review and whose failure mode is silent —
// nothing throws, nothing logs, the dialog just renders somewhere the user
// cannot reach it:
//
//   * an entry animation left in `animation-fill-mode: both` keeps an identity
//     matrix() transform forever, which makes its element the containing block
//     for every `position: fixed` descendant. That is what put the preflight's
//     `fixed inset-0` at x=-80/right=620 on an 820px tablet.
//   * a translucent overlay surface lets the layer underneath read through it
//     (event-log lines through MORE rows; AUTOMATION toggles through frame
//     metadata).
//   * the overlay surface's size must come from CSS custom properties, because
//     index.css is unlayered and its authored `max-width`/`max-height` beat any
//     Tailwind utility on the same element. Asserting the vars are present in
//     BOTH the CSS and the geometry resolver keeps those two halves in sync.
//   * the height clamp has to survive a browser without dvh. A var holding a
//     dvh value made the @supports fallback invalid at computed-value time,
//     which drops max-height to `none` (#354). jsdom computes no CSS lengths,
//     so the tests for that carry a small evaluator of their own.

interface NodeFsLike {
  readFileSync(path: string, encoding: string): string;
  readdirSync(path: string): string[];
  statSync(path: string): { isDirectory(): boolean };
}
const nodeImport = (s: string): Promise<unknown> =>
  (Function("m", "return import(m)") as (m: string) => Promise<unknown>)(s);
const fs = (await nodeImport("node:fs")) as NodeFsLike;

const resolve = (rel: string): string =>
  decodeURIComponent(new URL(rel, import.meta.url).pathname).replace(/^\/([A-Za-z]:)/, "$1");
const css = fs.readFileSync(resolve("../index.css"), "utf8");

// jsdom goes in BEFORE react-dom is imported: react-dom decides whether it can
// use the DOM once, at module load, so globals installed after the import would
// leave it rendering as if on a server. Only the mounted `full` tests at the
// bottom use these; the CSS and resolver tests never touch a document.
/* eslint-disable @typescript-eslint/no-explicit-any */
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(`<!doctype html><html><body></body></html>`,
  { url: "http://local/", pretendToBeVisual: true });
const win = dom.window as any;
/** What every media query answers. Flipped so a mounted overlay can be tried at
 *  phone width (no query matches) and desktop width (all of them match). */
let mqMatches = false;
win.matchMedia = () => ({
  get matches() { return mqMatches; },
  addEventListener() {}, removeEventListener() {},
});
// jsdom does no layout, so every element has zero client rects and the
// primitive's tabbable filter (`getClientRects().length > 0`, which is there to
// drop hidden roving-tabindex options) would find nothing to focus or trap. One
// rect each makes every rendered control count as visible.
win.Element.prototype.getClientRects = function () { return [{ width: 1, height: 1 }]; };
const gl = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "KeyboardEvent", "getComputedStyle", "matchMedia",
  "requestAnimationFrame", "cancelAnimationFrame",
]) {
  Object.defineProperty(gl, k, { value: k === "window" ? win : win[k], writable: true, configurable: true });
}
gl.IS_REACT_ACT_ENVIRONMENT = true;

const { createElement: h, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { overlayGeometry, Overlay } = await import("../components/Overlay");

// ---------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`✗ ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

/** Every declaration block whose selector list mentions `selector`, concatenated.
 *  A class can legitimately appear in more than one rule (the base rule plus a
 *  reduced-motion or @supports override), so matching only the FIRST one reads
 *  the wrong rule — which is exactly how this helper failed on its first run. */
function block(selector: string): string {
  const re = new RegExp(`\\${selector}(?![\\w-])[^{}]*\\{([^}]*)\\}`, "g");
  const parts = [...css.matchAll(re)].map((m) => m[1]);
  assert(parts.length > 0, `no CSS rule for ${selector}`);
  return parts.join("\n");
}
/** One declaration's value out of a block, e.g. `background`. */
function decl(blockText: string, prop: string): string {
  const m = new RegExp(`(?:^|;|\\n)\\s*${prop}\\s*:([^;}]*)`).exec(blockText);
  assert(!!m, `no \`${prop}\` declaration`);
  return m![1].trim();
}

// ------------------------------------------------- the containing-block trap
test("entry animations never use fill-mode `both` (identity-matrix trap)", () => {
  for (const cls of ["view-enter", "sheet-enter", "more-sheet-in"]) {
    const decl = block(`.${cls}`);
    assert(/animation:/.test(decl), `.${cls} has no animation`);
    assert(!/\bboth\b/.test(decl),
      `.${cls} uses animation-fill-mode: both — a filled identity transform makes ` +
      `the element a containing block for every fixed descendant (review S1/#3)`);
    assert(/\bbackwards\b/.test(decl), `.${cls} should fill \`backwards\``);
  }
});

// -------------------------------------------------------- the host contract
test("the overlay host is viewport-sized, dimmed, and click-through", () => {
  const b = block(".overlay-host");
  assert(/position:\s*fixed/.test(b), "host must be position: fixed");
  assert(/inset:\s*0/.test(b), "host must be inset: 0 — its padding box IS the viewport");
  assert(/pointer-events:\s*none/.test(b), "host must not eat taps outside its children");
  assert(/filter:\s*brightness\(var\(--screen-brightness/.test(b),
    "host must carry the dimmer — portalling out of .dim-content must not un-dim a dialog at 2am");
});

test("the overlay surface is OPAQUE and clamped to the viewport", () => {
  const b = block(".overlay-surface");
  const bg = decl(b, "background");
  assert(bg === "var(--bg-raise)",
    `surface must use the OPAQUE --bg-raise, never the translucent --bg-panel (#38/#44) — got ${bg}`);
  assert(!/rgba|\/\s*0?\.\d/.test(bg), "surface background must have no alpha");
  assert(/max-height:\s*min\(100dvh,\s*var\(--ov-max-h/.test(b),
    "surface height must be clamped to the viewport AND driven by --ov-max-h");
  assert(/max-width:\s*min\(100vw,\s*var\(--ov-max-w/.test(b),
    "surface width must be clamped to the viewport AND driven by --ov-max-w");
  assert(/flex-direction:\s*column/.test(b), "surface is head / body / foot");
});

test("the footer sits outside the scroll region and clears the home indicator", () => {
  const body = block(".overlay-body");
  assert(/overflow-y:\s*auto/.test(body), "the BODY is the only scroll region");
  const foot = block(".overlay-foot");
  assert(/flex:\s*none/.test(foot),
    "the footer must not scroll — that is what keeps CANCEL / RUN SEQUENCE reachable");
  assert(/env\(safe-area-inset-bottom/.test(foot), "footer must clear the home indicator (#46)");
});

test("safe-area insets exist for the bottom nav and page padding (#46)", () => {
  assert(/nav\.fixed\[aria-label="Primary"\][^{]*\{[^}]*env\(safe-area-inset-bottom/.test(css),
    "the fixed bottom nav has no safe-area padding");
  assert(/env\(safe-area-inset-bottom/.test(block(".main-safe-pad")),
    "<main>'s bottom padding must clear the nav AND the inset");
  assert(/env\(safe-area-inset-bottom/.test(block(".float-safe-b")),
    "bottom-floating controls must clear the inset");
});

test("the header icon cluster is one component (#47)", () => {
  const m = /\.app-header\s+\.btn,\s*\n?\s*\.app-header\s+\.step-btn\s*\{([^}]*)\}/.exec(css);
  assert(!!m, "no .app-header icon-set rule");
  assert(/min-width:\s*44px/.test(m![1]) && /min-height:\s*44px/.test(m![1]),
    "header icon buttons must meet the 44px touch minimum");
  assert(/border-radius:\s*10px/.test(m![1]), "header icon buttons must share one radius");
});

// ------------------------------------------------------- the size resolver
test("every variant positions itself inside the host, never in page flow", () => {
  for (const v of ["center", "sheet", "dock", "corner"] as const) {
    for (const lg of [false, true]) {
      for (const sm of [false, true]) {
        const g = overlayGeometry(v, lg, sm);
        assert(/\babsolute\b/.test(g.wrap),
          `${v} (lg=${lg} sm=${sm}) wrapper is not absolutely positioned inside the host`);
        assert(!/\bfixed\b/.test(g.wrap),
          `${v} wrapper must not re-introduce position: fixed — the host already is`);
      }
    }
  }
});

test("no variant asks for a surface bigger than the viewport", () => {
  // Since #354 a variant's height cap is `--ov-max-h-frac`, a bare fraction of
  // the viewport, and no longer a dvh value the percentage parse below would
  // read. Without the fraction check this test would have gone on passing
  // while grading none of the height caps, so it also counts what it graded.
  // Named mutation 'sheet asks for 120%' (Overlay.tsx: sheet's
  // --ov-max-h-frac "1.2"). Observed:
  //   no variant asks for a surface bigger than the viewport: sheet (lg=false
  //   sm=false) --ov-max-h-frac=1.2 is not a fraction of the viewport in (0,
  //   1]
  const pct = (s: string | undefined): number | null => {
    if (!s) return null;
    const m = /^(\d+(?:\.\d+)?)(dvh|vh|vw)$/.exec(s);
    return m ? parseFloat(m[1]) : null;
  };
  let fractions = 0;
  for (const v of ["center", "sheet", "dock", "corner"] as const) {
    for (const lg of [false, true]) {
      for (const sm of [false, true]) {
        const { vars } = overlayGeometry(v, lg, sm);
        const where = `${v} (lg=${lg} sm=${sm})`;
        for (const key of ["--ov-max-h", "--ov-max-w", "--ov-w", "--ov-h"]) {
          const p = pct(vars[key]);
          assert(p === null || p <= 100,
            `${where} ${key}=${vars[key]} exceeds the viewport`);
        }
        const frac = vars["--ov-max-h-frac"];
        if (frac !== undefined) {
          fractions++;
          assert(/^(?:0?\.\d+|1(?:\.0+)?)$/.test(frac) && Number(frac) > 0,
            `${where} --ov-max-h-frac=${frac} is not a fraction of the viewport in (0, 1]`);
        }
        const gap = vars["--ov-max-h-gap"];
        assert(gap === undefined || /^\d+(?:\.\d+)?(?:px|rem)$/.test(gap),
          `${where} --ov-max-h-gap=${gap} must be a length of zero or more, in px or rem`);
      }
    }
  }
  assert(fractions > 0, "no variant sets --ov-max-h-frac, so this test graded no height cap");
});

test("the non-modal corner card clears the phone bottom nav", () => {
  const phone = overlayGeometry("corner", false, false);
  assert(/overlay-above-nav/.test(phone.wrap),
    "the first-run wizard must not cover the tabs it is telling the user to press");
  assert(/bottom:\s*calc\(3\.5rem \+ env\(safe-area-inset-bottom/.test(block(".overlay-above-nav")),
    ".overlay-above-nav must offset by the nav height plus the inset");
  const desktop = overlayGeometry("corner", true, true);
  assert(!/overlay-above-nav/.test(desktop.wrap),
    "there is no bottom nav above sm — the offset would just be a gap");
});

test("dock is a right column on lg and a bottom sheet below it", () => {
  const desk = overlayGeometry("dock", true, true);
  assert(/right-0/.test(desk.wrap) && desk.vars["--ov-w"] === "420px", "lg dock is a right column");
  // On lg the wrapper is only as wide as the drawer, so the dismiss catcher it
  // contains cannot blanket the viewport — the app stays interactive behind a
  // docked log, which is how it behaved before the drawer was portalled.
  assert(!/inset-0/.test(desk.wrap), "lg dock wrapper must not cover the whole viewport");
  const sheet = overlayGeometry("dock", false, false);
  assert(/inset-0/.test(sheet.wrap) && /justify-end/.test(sheet.wrap), "below lg the dock is a bottom sheet");
});

// ------------------------------------------------ the `full` variant (S4, #189)
// The Target framing modal on tablet and desktop is the whole screen: the sky
// flexes on the left beside a control column (flows-mosaic spec 2.2). Before S4
// the primitive had no full-screen variant, and building one outside it would
// re-open every bug this file guards. So `full` is a fifth variant, sized ONLY
// by --ov-* values that live in index.css.
//
// Every test from here down was run RED under a named mutation in a private
// copy of ui/ (never this tree). Each "Observed" quote is the run's failure
// line verbatim, wrapped, less the harness's leading failure marker.

test("full is one full-viewport surface at every width", () => {
  // Named mutation 'full maps to center' (Overlay.tsx: the `case "full"`
  // deleted, so "full" falls through to the default centred dialog). Observed:
  //   full is one full-viewport surface at every width: full (lg=false
  //   sm=false) surface lacks .overlay-full, got "rounded-t-2xl sheet-enter"
  // Named mutation 'full sets --ov-h inline' (vars: { "--ov-h": "100dvh" }).
  // Observed:
  //   full is one full-viewport surface at every width: full (lg=false
  //   sm=false) sets --ov-h inline, and an inline custom property outranks
  //   the .overlay-full rule in index.css, so the size would no longer come
  //   from the CSS alone
  const phone = overlayGeometry("full", false, false);
  for (const lg of [false, true]) {
    for (const sm of [false, true]) {
      const g = overlayGeometry("full", lg, sm);
      const where = `full (lg=${lg} sm=${sm})`;
      const surf = g.surface.split(/\s+/);
      assert(surf.includes("overlay-full"), `${where} surface lacks .overlay-full, got "${g.surface}"`);
      assert(!surf.some((c) => c.startsWith("rounded")),
        `${where} rounded corners on a full-screen surface show the scrim through them, got "${g.surface}"`);
      const wrap = g.wrap.split(/\s+/);
      assert(wrap.includes("absolute") && wrap.includes("inset-0"),
        `${where} wrapper must fill the host (absolute inset-0), got "${g.wrap}"`);
      assert(!wrap.includes("fixed"), `${where} wrapper must not re-introduce position: fixed`);
      // Anything that pads, aligns or offsets the surface inside the wrapper
      // would leave a strip of scrim showing at an edge.
      const shrink = wrap.filter((c) =>
        /^(?:-?[mp][xytblr]?|items|justify|self|place|top|bottom|left|right|inset-[xy])-/.test(c));
      assert(shrink.length === 0, `${where} wrapper pads or aligns the surface off the edges: ${shrink.join(" ")}`);
      const inline = Object.keys(g.vars).filter((k) => k.startsWith("--ov-"));
      assert(inline.length === 0,
        `${where} sets ${inline.join(", ")} inline, and an inline custom property outranks the ` +
        `.overlay-full rule in index.css, so the size would no longer come from the CSS alone`);
      assert(!/\dpx/.test(JSON.stringify(g)), `${where} hard-codes px in the component: ${JSON.stringify(g)}`);
      assert(JSON.stringify(g) === JSON.stringify(phone),
        `${where} differs from the phone geometry; full has ONE geometry at every width: ${JSON.stringify(g)}`);
    }
  }
});

test("full's size is --ov-* values in index.css that fill the host", () => {
  // 100%, not 100vw / 100dvh: the wrapper is `absolute inset-0` in the fixed
  // host, so 100% is exactly the viewport the host covers. See the CSS comment.
  // Named mutation 'full sizes dropped from index.css' (the four --ov-* lines
  // removed from `.overlay-surface.overlay-full`). Observed:
  //   full's size is --ov-* values in index.css that fill the host: no
  //   `--ov-w` declaration
  const b = block(".overlay-full");
  for (const v of ["--ov-w", "--ov-h", "--ov-max-w", "--ov-max-h"]) {
    const got = decl(b, v);
    assert(got === "100%", `.overlay-full ${v} must be 100% of the host, got ${got}`);
  }
});

test("full clears the notch and the side insets; the foot keeps the bottom one", () => {
  // index.html sets viewport-fit=cover, so on a notched phone a full surface's
  // head row would sit under the status bar without this.
  // Named mutation 'full drops the top inset' (padding-top removed). Observed:
  //   full clears the notch and the side insets; the foot keeps the bottom
  //   one: no `padding-top` declaration
  const b = block(".overlay-full");
  for (const side of ["top", "left", "right"]) {
    const got = decl(b, `padding-${side}`);
    assert(new RegExp(`^env\\(safe-area-inset-${side}\\b`).test(got),
      `.overlay-full padding-${side} must be env(safe-area-inset-${side}), got ${got}`);
  }
  assert(!/padding-bottom\s*:|(?:^|;|\n)\s*padding\s*:/.test(b),
    "the bottom inset belongs to the foot row (or .overlay-safe-b); padding it on the surface too counts it twice");
});

test("full draws no frame at the screen edge, outranking the base surface rule", () => {
  // The base rule is unlayered and sets a 1px border, so a Tailwind `border-0`
  // would lose to it. The compound selector wins on specificity, not on where
  // it happens to sit in the file.
  // Named mutation 'full keeps the surface border' (`border: 0` removed).
  // Observed:
  //   full draws no frame at the screen edge, outranking the base surface
  //   rule: `.overlay-surface.overlay-full` must set border: 0, the base
  //   rule's 1px frame is cut by rounded display corners
  const m = /\.overlay-surface\.overlay-full\s*\{([^}]*)\}/.exec(css);
  assert(!!m, "the full rule must be `.overlay-surface.overlay-full` so it outranks `.overlay-surface`");
  assert(/(?:^|;|\n)\s*border:\s*0\s*(?:;|$)/.test(m![1]),
    "`.overlay-surface.overlay-full` must set border: 0, the base rule's 1px frame is cut by rounded display corners");
});

/** overlayGeometry for the four older variants, captured at the commit before
 *  S4-UOVERLAY (812fcf9e). It answered the same until #354 moved their height
 *  caps out of dvh values and into `--ov-max-h-frac` / `--ov-max-h-gap`. The
 *  S4 control below holds the wrap and the surface to it byte for byte, and
 *  the #354 control further down holds what the vars compute to it. */
const PRE_354: Record<string, string> = {
    "center|lg=false|sm=false": `{"wrap":"absolute inset-0 flex items-end justify-center","surface":"rounded-t-2xl sheet-enter","vars":{"--ov-max-h":"92dvh"}}`,
    "center|lg=false|sm=true": `{"wrap":"absolute inset-0 flex items-center justify-center p-4","surface":"rounded-2xl sheet-enter","vars":{"--ov-max-w":"560px","--ov-max-h":"calc(100dvh - 2rem)"}}`,
    "center|lg=true|sm=false": `{"wrap":"absolute inset-0 flex items-end justify-center","surface":"rounded-t-2xl sheet-enter","vars":{"--ov-max-h":"92dvh"}}`,
    "center|lg=true|sm=true": `{"wrap":"absolute inset-0 flex items-center justify-center p-4","surface":"rounded-2xl sheet-enter","vars":{"--ov-max-w":"560px","--ov-max-h":"calc(100dvh - 2rem)"}}`,
    "sheet|lg=false|sm=false": `{"wrap":"absolute inset-0 flex flex-col justify-end","surface":"rounded-t-2xl more-sheet-in","vars":{"--ov-max-h":"85dvh"}}`,
    "sheet|lg=false|sm=true": `{"wrap":"absolute inset-0 flex flex-col justify-end","surface":"rounded-t-2xl more-sheet-in","vars":{"--ov-max-h":"85dvh"}}`,
    "sheet|lg=true|sm=false": `{"wrap":"absolute inset-0 flex flex-col justify-end","surface":"rounded-t-2xl more-sheet-in","vars":{"--ov-max-h":"85dvh"}}`,
    "sheet|lg=true|sm=true": `{"wrap":"absolute inset-0 flex flex-col justify-end","surface":"rounded-t-2xl more-sheet-in","vars":{"--ov-max-h":"85dvh"}}`,
    "dock|lg=false|sm=false": `{"wrap":"absolute inset-0 flex flex-col justify-end","surface":"rounded-t-2xl sheet-enter","vars":{"--ov-max-h":"60dvh"}}`,
    "dock|lg=false|sm=true": `{"wrap":"absolute inset-0 flex flex-col justify-end","surface":"rounded-t-2xl sheet-enter","vars":{"--ov-max-h":"60dvh"}}`,
    "dock|lg=true|sm=false": `{"wrap":"absolute inset-y-0 right-0 flex","surface":"","vars":{"--ov-w":"420px","--ov-max-w":"92vw","--ov-h":"100%"}}`,
    "dock|lg=true|sm=true": `{"wrap":"absolute inset-y-0 right-0 flex","surface":"","vars":{"--ov-w":"420px","--ov-max-w":"92vw","--ov-h":"100%"}}`,
    "corner|lg=false|sm=false": `{"wrap":"absolute inset-x-0 bottom-0 overlay-above-nav flex justify-center","surface":"rounded-t-2xl sheet-enter","vars":{"--ov-max-h":"70dvh"}}`,
    "corner|lg=false|sm=true": `{"wrap":"absolute right-4 bottom-4 flex justify-end","surface":"rounded-2xl sheet-enter","vars":{"--ov-w":"380px","--ov-max-w":"380px","--ov-max-h":"80dvh"}}`,
    "corner|lg=true|sm=false": `{"wrap":"absolute inset-x-0 bottom-0 overlay-above-nav flex justify-center","surface":"rounded-t-2xl sheet-enter","vars":{"--ov-max-h":"70dvh"}}`,
    "corner|lg=true|sm=true": `{"wrap":"absolute right-4 bottom-4 flex justify-end","surface":"rounded-2xl sheet-enter","vars":{"--ov-w":"380px","--ov-max-w":"380px","--ov-max-h":"80dvh"}}`,
};

test("control: the four existing variants' classes are byte-identical to before `full`", () => {
  // The wrap and the surface only. The vars moved on purpose for #354, and
  // "control: a dvh browser computes every size it computed before #354"
  // holds what they compute instead.
  // Named mutation 'center gains overlay-full' (the center sm surface became
  // "rounded-2xl sheet-enter overlay-full"). Observed:
  //   control: the four existing variants' classes are byte-identical to
  //   before `full`: center|lg=false|sm=true changed: {"wrap":"absolute
  //   inset-0 flex items-center justify-center p-4","surface":"rounded-2xl
  //   sheet-enter overlay-full"}
  let seen = 0;
  for (const v of ["center", "sheet", "dock", "corner"] as const) {
    for (const lg of [false, true]) {
      for (const sm of [false, true]) {
        const k = `${v}|lg=${lg}|sm=${sm}`;
        const g = overlayGeometry(v, lg, sm);
        const was = JSON.parse(PRE_354[k]) as { wrap: string; surface: string };
        assert(g.wrap === was.wrap && g.surface === was.surface,
          `${k} changed: ${JSON.stringify({ wrap: g.wrap, surface: g.surface })}`);
        seen++;
      }
    }
  }
  assert(seen === Object.keys(PRE_354).length, `compared ${seen} of ${Object.keys(PRE_354).length} captured geometries`);
});

// ------------------------------- the height clamp without dvh (S5, #354)
// CSS-OVERLAY clamps the surface with `min(100dvh, ...)` and gives a browser
// without dvh an @supports fallback in vh. Until #354 every variant but the
// lg dock fed its cap through --ov-max-h as a dvh value (`85dvh`,
// `calc(100dvh - 2rem)`), and the fallback read that same var. A declaration
// holding var() is only checked once the var is substituted, so without dvh
// the fallback came out as `min(100vh, 85dvh)`: invalid at computed-value
// time, which sets max-height back to its initial value, `none`. The
// fallback removed the clamp it exists to provide, and on a short screen
// the footer (CANCEL / RUN SEQUENCE) could sit below the edge with nothing
// to scroll to, the S1 bug this primitive was built to end.
//
// jsdom computes no CSS lengths, so these tests carry an evaluator: it
// substitutes the vars into the declaration the way a browser does, then
// does calc() / min() / max() against one browser's unit table, where a unit
// that browser lacks is exactly what makes the declaration invalid. The
// first test holds the evaluator itself to that, so the rest cannot pass
// because the evaluator accepts everything.
//
// Every test here was run RED under a named mutation in a private copy of
// ui/, and each "Observed" quote is that run's failure line, as above. Under
// the control 'an unrelated comment reworded' (index.css: contract item 4's
// "not the translucent" made "never the translucent") all 24 tests passed.

/** px per unit on one axis, `%` being 1% of the host on that axis. A unit
 *  that is not a key is one the browser cannot parse. */
type Units = Record<string, number>;
interface Browser { name: string; height: Units; width: Units; hostH: number }
type Computed = { ok: true; px: number } | { ok: true; keyword: string } | { ok: false; why: string };
class InvalidAtComputedTime extends Error {}

/** Replace every var() the way the cascade does, before anything is parsed:
 *  the value when the property is set, else the fallback (itself
 *  substituted), else the declaration is invalid. */
function substituteVars(text: string, vars: Record<string, string>, depth = 0): string {
  if (depth > 16) throw new InvalidAtComputedTime("var() nests too deep, or cycles");
  let out = "";
  let i = 0;
  for (;;) {
    const at = text.indexOf("var(", i);
    if (at < 0) return out + text.slice(i);
    out += text.slice(i, at);
    let end = at + 4;
    let open = 1;
    let comma = -1;
    for (; end < text.length && open > 0; end++) {
      if (text[end] === "(") open++;
      else if (text[end] === ")") open--;
      else if (text[end] === "," && open === 1 && comma < 0) comma = end;
    }
    if (open !== 0) throw new InvalidAtComputedTime(`unbalanced var( in "${text}"`);
    const name = text.slice(at + 4, comma < 0 ? end - 1 : comma).trim();
    if (name in vars) out += substituteVars(vars[name], vars, depth + 1);
    else if (comma >= 0) out += substituteVars(text.slice(comma + 1, end - 1), vars, depth + 1);
    else throw new InvalidAtComputedTime(`${name} is not set and has no fallback`);
    i = end;
  }
}

type Tok = { t: "num"; v: number; unit: string } | { t: "fn"; name: string } | { t: "op"; v: string };
/** A resolved value: px when `len`, else a bare number. */
type Qty = { v: number; len: boolean };

/** calc() / min() / max() over one browser's units. Enough CSS math for the
 *  overlay's four sizes, and strict where CSS is strict: a unit the browser
 *  lacks, a length times a length, a length plus a bare number, and a bare
 *  number where a length goes all make the declaration invalid. */
function evalMath(src: string, units: Units): Qty {
  const toks: Tok[] = [];
  for (let i = 0; i < src.length;) {
    const c = src[i];
    if (/\s/.test(c)) { i++; continue; }
    const prev = toks[toks.length - 1];
    // A sign belongs to the number only where no operand precedes it; CSS
    // puts spaces round a binary + or -.
    const signed = !prev || prev.t === "fn" || (prev.t === "op" && prev.v !== ")");
    const num = /^[+-]?(?:\d+\.?\d*|\.\d+)(%|[a-z]+)?/i.exec(src.slice(i));
    if (num && (/[\d.]/.test(c) || signed)) {
      toks.push({ t: "num", v: parseFloat(num[0]), unit: (num[1] ?? "").toLowerCase() });
      i += num[0].length;
      continue;
    }
    const fn = /^([a-z-]+)\(/i.exec(src.slice(i));
    if (fn) { toks.push({ t: "fn", name: fn[1].toLowerCase() }); i += fn[0].length; continue; }
    if ("+-*/(),".includes(c)) { toks.push({ t: "op", v: c }); i++; continue; }
    throw new InvalidAtComputedTime(`cannot read "${src.slice(i)}"`);
  }
  let p = 0;
  const isOp = (v: string): boolean => { const t = toks[p]; return !!t && t.t === "op" && t.v === v; };
  const expect = (v: string): void => {
    if (!isOp(v)) throw new InvalidAtComputedTime(`expected "${v}" in "${src}"`);
    p++;
  };
  const leaf = (): Qty => {
    const t = toks[p++];
    if (!t) throw new InvalidAtComputedTime(`"${src}" ends early`);
    if (t.t === "num") {
      if (t.unit === "") return { v: t.v, len: false };
      const per = units[t.unit];
      if (per === undefined) throw new InvalidAtComputedTime(`this browser cannot parse the unit "${t.unit}"`);
      return { v: t.v * per, len: true };
    }
    if (t.t === "op" && t.v === "(") { const q = sum(); expect(")"); return q; }
    if (t.t === "fn") {
      const args = [sum()];
      while (isOp(",")) { p++; args.push(sum()); }
      expect(")");
      if (t.name === "calc" && args.length === 1) return args[0];
      if (t.name === "min" || t.name === "max") {
        if (args.some((a) => a.len !== args[0].len)) {
          throw new InvalidAtComputedTime(`${t.name}() mixes a length and a bare number in "${src}"`);
        }
        return { v: (t.name === "min" ? Math.min : Math.max)(...args.map((a) => a.v)), len: args[0].len };
      }
      throw new InvalidAtComputedTime(`${t.name}() with ${args.length} arguments in "${src}"`);
    }
    throw new InvalidAtComputedTime(`unexpected "${t.v}" in "${src}"`);
  };
  const product = (): Qty => {
    let a = leaf();
    while (isOp("*") || isOp("/")) {
      const times = isOp("*");
      p++;
      const b = leaf();
      if (times) {
        if (a.len && b.len) throw new InvalidAtComputedTime(`a length times a length in "${src}"`);
        a = { v: a.v * b.v, len: a.len || b.len };
      } else {
        if (b.len) throw new InvalidAtComputedTime(`a division by a length in "${src}"`);
        a = { v: a.v / b.v, len: a.len };
      }
    }
    return a;
  };
  function sum(): Qty {
    let a = product();
    while (isOp("+") || isOp("-")) {
      const plus = isOp("+");
      p++;
      const b = product();
      if (a.len !== b.len) throw new InvalidAtComputedTime(`a length and a bare number added in "${src}"`);
      a = { v: plus ? a.v + b.v : a.v - b.v, len: a.len };
    }
    return a;
  }
  const q = leaf();
  if (p !== toks.length) throw new InvalidAtComputedTime(`"${src}" is not one value`);
  return q;
}

/** One declaration's computed value on one axis: a length in px, a keyword,
 *  or invalid at computed-value time (which a browser turns into the
 *  property's initial value, `none` for max-height). */
function evalLength(text: string, vars: Record<string, string>, units: Units): Computed {
  try {
    const s = substituteVars(text, vars).trim();
    if (/^[a-z-]+$/i.test(s)) return { ok: true, keyword: s };
    const q = evalMath(s, units);
    if (!q.len) throw new InvalidAtComputedTime(`"${s}" is a bare number where a length goes`);
    return { ok: true, px: q.v };
  } catch (e) {
    if (e instanceof InvalidAtComputedTime) return { ok: false, why: e.message };
    throw e;
  }
}
const show = (c: Computed): string =>
  !c.ok ? `invalid at computed-value time (${c.why})` : "px" in c ? `${+c.px.toFixed(3)}px` : c.keyword;
const sameSize = (a: Computed, b: Computed): boolean =>
  a.ok && b.ok && ("px" in a && "px" in b ? Math.abs(a.px - b.px) < 1e-6
    : "keyword" in a && "keyword" in b && a.keyword === b.keyword);

interface Viewport { w: number; h: number }
/** Phone portrait and landscape, a desktop, and a screen short enough that
 *  center's 2rem gap and a 92dvh cap stop agreeing. */
const VIEWPORTS: Viewport[] = [{ w: 390, h: 844 }, { w: 844, h: 390 }, { w: 1440, h: 900 }, { w: 640, h: 300 }];
/** A phone with its toolbar showing: vh measures the LARGE viewport, while the
 *  fixed host (so `%` of it) and dvh, where the browser has it, measure the
 *  visible one, 90% of it here. That gap is why the overlay uses dvh at all. */
function phone(vp: Viewport, dvh: boolean): Browser {
  const visible = 0.9 * vp.h;
  const u: Units = { px: 1, rem: 16, vw: vp.w / 100, vh: vp.h / 100, ...(dvh ? { dvh: visible / 100 } : {}) };
  return { name: `a phone browser ${dvh ? "with" : "without"} dvh at ${vp.w}x${vp.h}`,
    height: { ...u, "%": visible / 100 }, width: { ...u, "%": vp.w / 100 }, hostH: visible };
}
/** No toolbar, as on a desktop: vh, dvh and the host all measure the window,
 *  so a browser without dvh ought to reach the number one with dvh reaches. */
function flat(vp: Viewport, dvh: boolean): Browser {
  const u: Units = { px: 1, rem: 16, vw: vp.w / 100, vh: vp.h / 100, ...(dvh ? { dvh: vp.h / 100 } : {}) };
  return { name: `a browser ${dvh ? "with" : "without"} dvh and no toolbar at ${vp.w}x${vp.h}`,
    height: { ...u, "%": vp.h / 100 }, width: { ...u, "%": vp.w / 100 }, hostH: vp.h };
}

/** One rule body's declarations, comments stripped. */
function declarations(body: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const part of body.replace(/\/\*[\s\S]*?\*\//g, "").split(";")) {
    const at = part.indexOf(":");
    if (at > 0) out[part.slice(0, at).trim()] = part.slice(at + 1).trim();
  }
  return out;
}
/** The top-level surface rule (not the indented one inside @supports), and
 *  the @supports block with the unit its condition names. */
const BASE_RULE = /(?:^|\n)\.overlay-surface\s*\{([^}]*)\}/.exec(css);
const FALLBACK = /@supports\s+not\s+\(height:\s*100(\w+)\)\s*\{\s*\.overlay-surface\s*\{([^}]*)\}\s*\}/.exec(css);
type SizeProp = "width" | "height" | "max-width" | "max-height";
const SIZE_PROPS: SizeProp[] = ["width", "height", "max-width", "max-height"];
const KEYWORD: Record<SizeProp, string> = { width: "auto", height: "auto", "max-width": "none", "max-height": "none" };

/** What a browser computes for the surface's four sizes: the base rule, the
 *  @supports fallback over it when the browser lacks the unit the condition
 *  names, and the vars of any `.overlay-surface.<class>` rule the surface
 *  matches (`full`'s) under the inline ones, which outrank them. */
function surfaceSizes(b: Browser, surfaceClasses: string, inline: Record<string, string>,
  rules: Record<string, string> | null = null): Record<SizeProp, Computed> {
  assert(!!BASE_RULE, "no top-level .overlay-surface rule in index.css");
  assert(!!FALLBACK, "no `@supports not (height: 100<unit>) { .overlay-surface { ... } }` block in index.css");
  let decls = rules;
  if (!decls) {
    decls = declarations(BASE_RULE![1]);
    if (!(FALLBACK![1] in b.height)) decls = { ...decls, ...declarations(FALLBACK![2]) };
  }
  const classes = surfaceClasses.split(/\s+/);
  const classVars: Record<string, string> = {};
  for (const m of css.matchAll(/\.overlay-surface\.([\w-]+)\s*\{([^}]*)\}/g)) {
    if (!classes.includes(m[1])) continue;
    for (const [k, v] of Object.entries(declarations(m[2]))) if (k.startsWith("--")) classVars[k] = v;
  }
  const vars = { ...classVars, ...inline };
  const out = {} as Record<SizeProp, Computed>;
  for (const prop of SIZE_PROPS) {
    assert(prop in decls, `.overlay-surface declares no ${prop}`);
    const c = evalLength(decls[prop], vars, prop.endsWith("height") ? b.height : b.width);
    out[prop] = c.ok && "keyword" in c && c.keyword !== KEYWORD[prop]
      ? { ok: false, why: `${prop} cannot be \`${c.keyword}\`` } : c;
  }
  return out;
}
const VARIANTS = ["center", "sheet", "dock", "corner", "full"] as const;

test("the length evaluator refuses what a browser without dvh refuses (#354)", () => {
  // Named mutation 'the evaluator parses every unit' (this file: a unit the
  // browser lacks counted as 1px instead of refused). Observed:
  //   the length evaluator refuses what a browser without dvh refuses (#354):
  //   min(100vh, 85dvh) must be invalid in a browser without dvh, got 85px
  const vp = { w: 390, h: 844 };
  const without = phone(vp, false).height;
  const withDvh = phone(vp, true).height;
  const dropped = evalLength("min(100vh, 85dvh)", {}, without);
  assert(!dropped.ok && /"dvh"/.test(dropped.why),
    `min(100vh, 85dvh) must be invalid in a browser without dvh, got ${show(dropped)}`);
  const kept = evalLength("min(100vh, 85dvh)", {}, withDvh);
  assert(kept.ok && "px" in kept && Math.abs(kept.px - 0.85 * 0.9 * 844) < 1e-6,
    `min(100vh, 85dvh) with dvh must be 85% of the visible height, got ${show(kept)}`);
  // Substitution comes before the parse; a fallback is used only while the
  // var is unset, and a fallback may itself hold var().
  const frac = evalLength("calc(var(--f, 1) * 100vh - var(--g, 0px))", { "--f": "0.5", "--g": "2rem" }, without);
  assert(frac.ok && "px" in frac && Math.abs(frac.px - (0.5 * 844 - 32)) < 1e-6,
    `half the viewport less 2rem must be ${0.5 * 844 - 32}px, got ${show(frac)}`);
  const nested = "var(--m, calc(var(--f, 1) * 100vh))";
  const unset = evalLength(nested, {}, without);
  assert(unset.ok && "px" in unset && Math.abs(unset.px - 844) < 1e-6, `${nested} with nothing set, got ${show(unset)}`);
  const over = evalLength(nested, { "--m": "100%", "--f": "0.5" }, without);
  assert(over.ok && "px" in over && Math.abs(over.px - 0.9 * 844) < 1e-6,
    `${nested} with --m set must be --m (the host), got ${show(over)}`);
  for (const [expr, vars] of [
    ["calc(var(--f) * 100vh)", { "--f": "85%" }],   // a percentage times a length
    ["min(100vh, var(--f))", { "--f": "0.85" }],    // a bare number beside a length
    ["min(100vh, var(--unset))", {}],               // unset, and no fallback
    ["calc(100vh - 2)", {}],                        // a length less a bare number
    ["var(--f)", { "--f": "0.85" }],                // a bare number where a length goes
  ] as [string, Record<string, string>][]) {
    const c = evalLength(expr, vars, withDvh);
    assert(!c.ok, `${expr} with ${JSON.stringify(vars)} must be invalid, got ${show(c)}`);
  }
  // Without dvh the base rule alone is invalid, whatever the vars: the
  // @supports fallback is the only thing holding the clamp there.
  assert(!!BASE_RULE, "no top-level .overlay-surface rule in index.css");
  const base = evalLength(decl(BASE_RULE![1], "max-height"), {}, without);
  assert(!base.ok, `the base rule's max-height must need dvh, got ${show(base)} without it`);
});

test("every variant keeps a valid height clamp in a browser without dvh (#354)", () => {
  // Named mutation 'dvh inside --ov-max-h' (Overlay.tsx: sheet's vars back
  // to { "--ov-max-h": "85dvh" }, the shape every variant had before #354).
  // Observed:
  //   every variant keeps a valid height clamp in a browser without dvh
  //   (#354): sheet (lg=false sm=false, vars {"--ov-max-h":"85dvh"}): a phone
  //   browser without dvh at 390x844 computes max-height invalid at
  //   computed-value time (this browser cannot parse the unit "dvh")
  // The dvh-browser control below stays green under it: the clamp only moved
  // where dvh is missing. Named mutation 'dvh inside --ov-h' (Overlay.tsx:
  // the lg dock's --ov-h "100dvh"), a height var with no fallback at all.
  // Observed:
  //   every variant keeps a valid height clamp in a browser without dvh
  //   (#354): dock (lg=true sm=false, vars
  //   {"--ov-w":"420px","--ov-max-w":"92vw","--ov-h":"100dvh"}): a phone
  //   browser without dvh at 390x844 computes height invalid at
  //   computed-value time (this browser cannot parse the unit "dvh")
  // The second half, the same clamp and not merely a valid one, under
  // 'fallback reads --ov-max-h alone' (see the next test). Observed:
  //   every variant keeps a valid height clamp in a browser without dvh
  //   (#354): center (lg=false sm=false, vars {"--ov-max-h-frac":"0.92"}): at
  //   390x844 with no toolbar, a browser without dvh computes max-height
  //   844px where one with dvh computes 776.48px
  let graded = 0;
  for (const v of VARIANTS) {
    for (const lg of [false, true]) {
      for (const sm of [false, true]) {
        const g = overlayGeometry(v, lg, sm);
        const where = `${v} (lg=${lg} sm=${sm}, vars ${JSON.stringify(g.vars)})`;
        for (const vp of VIEWPORTS) {
          const old = phone(vp, false);
          const got = surfaceSizes(old, g.surface, g.vars);
          for (const prop of SIZE_PROPS) {
            assert(got[prop].ok, `${where}: ${old.name} computes ${prop} ${show(got[prop])}`);
          }
          const mh = got["max-height"];
          assert("px" in mh && mh.px > 0 && mh.px <= vp.h + 1e-6,
            `${where}: ${old.name} computes max-height ${show(mh)}, which is no clamp inside the screen`);
          // Where vh and dvh measure the same thing, the fallback must reach
          // the number the main rule does: the same clamp, not merely a valid one.
          const a = surfaceSizes(flat(vp, true), g.surface, g.vars)["max-height"];
          const b = surfaceSizes(flat(vp, false), g.surface, g.vars)["max-height"];
          assert(sameSize(a, b),
            `${where}: at ${vp.w}x${vp.h} with no toolbar, a browser without dvh computes max-height ` +
            `${show(b)} where one with dvh computes ${show(a)}`);
          graded++;
        }
      }
    }
  }
  assert(graded === VARIANTS.length * 4 * VIEWPORTS.length, `graded ${graded} variant-viewport pairs`);
});

test("the fallback rule reads the fraction, in vh (#354)", () => {
  // Named mutation 'fallback reads --ov-max-h alone' (index.css: the
  // @supports rule back to `min(100vh, var(--ov-max-h, 100vh))`). Observed:
  //   the fallback rule reads the fraction, in vh (#354): the fallback's
  //   max-height must be min(100vh, var(--ov-max-h, calc(var(--ov-max-h-frac,
  //   1) * 100vh - var(--ov-max-h-gap, 0px)))), the fraction in vh; it is
  //   min(100vh, var(--ov-max-h, 100vh))
  assert(!!FALLBACK && FALLBACK[1] === "dvh",
    "the fallback must be `@supports not (height: 100dvh) { .overlay-surface { ... } }`");
  assert(!!BASE_RULE, "no top-level .overlay-surface rule in index.css");
  const fb = decl(FALLBACK![2], "max-height");
  const base = decl(BASE_RULE![1], "max-height");
  assert(!/dvh/.test(fb), `the fallback's max-height holds a dvh term, which a browser without dvh cannot parse: ${fb}`);
  const reads = (unit: string): RegExp => new RegExp(
    `^min\\(\\s*100${unit}\\s*,\\s*var\\(\\s*--ov-max-h\\s*,\\s*calc\\(\\s*var\\(\\s*--ov-max-h-frac\\s*,\\s*1\\s*\\)` +
    `\\s*\\*\\s*100${unit}\\s*-\\s*var\\(\\s*--ov-max-h-gap\\s*,\\s*0px\\s*\\)\\s*\\)\\s*\\)\\s*\\)$`);
  assert(reads("vh").test(fb),
    "the fallback's max-height must be min(100vh, var(--ov-max-h, calc(var(--ov-max-h-frac, 1) * 100vh - " +
    `var(--ov-max-h-gap, 0px)))), the fraction in vh; it is ${fb}`);
  assert(reads("dvh").test(base),
    "the main max-height must read the same fraction in dvh, min(100dvh, var(--ov-max-h, " +
    `calc(var(--ov-max-h-frac, 1) * 100dvh - var(--ov-max-h-gap, 0px)))); it is ${base}`);
});

/** The six callers that handed the Overlay a `surfaceStyle` height cap, with
 *  the caps they handed it as of S5: a dvh value in `--ov-max-h`, which a
 *  browser without dvh cannot parse (#417). The #354 control below grades the
 *  variants under these; the #417 tests further down hold what each file
 *  passes now to what these computed. `file` is relative to ui/src. */
const CALLERS_PRE_417: { who: string; file: string; variant: "center" | "sheet"; style: Record<string, string> }[] = [
  { who: "FlowWizard", file: "components/flows/FlowWizard.tsx", variant: "center",
    style: { "--ov-max-h": "90dvh" } },
  { who: "ClassicSkyTools", file: "components/sky/ClassicSkyTools.tsx", variant: "center",
    style: { "--ov-w": "940px", "--ov-max-w": "96vw", "--ov-max-h": "92dvh" } },
  { who: "TonightPanel", file: "components/flows/TonightPanel.tsx", variant: "center",
    style: { "--ov-max-w": "880px", "--ov-max-h": "90dvh" } },
  { who: "QuickFlow", file: "components/flows/QuickFlow.tsx", variant: "center",
    style: { "--ov-max-h": "90dvh" } },
  { who: "FlowEditSheet", file: "components/flows/FlowEditSheet.tsx", variant: "sheet",
    style: { "--ov-max-h": "76dvh" } },
  { who: "FlowPaletteSheet", file: "components/flows/FlowPaletteSheet.tsx", variant: "sheet",
    style: { "--ov-max-h": "72dvh" } },
];

test("control: a dvh browser computes every size it computed before #354", () => {
  // The fix must move nothing where dvh exists: each older variant, alone and
  // under each caller's own `surfaceStyle` cap (their values as of S5), gets
  // the four sizes the rule and vars before #354 gave it. A caller's
  // --ov-max-h still REPLACES the variant's cap rather than meeting it in a
  // min(), so FlowWizard's 90dvh stays 90dvh on a screen 300px tall, where
  // center's `100dvh - 2rem` would be smaller. Those callers' dvh caps left
  // the fallback invalid without dvh until #417 moved them to fractions as
  // well; the #417 tests below hold the fractions to these caps.
  // Named mutation 'the fraction in vh in the main rule' (index.css: the
  // main rule's `* 100dvh` made `* 100vh`). Observed:
  //   control: a dvh browser computes every size it computed before #354:
  //   center|lg=false|sm=false under no caller: a phone browser with dvh at
  //   390x844 computes max-height 759.6px, before #354 698.832px
  // Named mutation 'a caller's cap meets the variant's' (index.css: the main
  // rule made min(100dvh, var(--ov-max-h, 100dvh), calc(...)), the cap and
  // the fraction as two arguments). Observed:
  //   control: a dvh browser computes every size it computed before #354:
  //   center|lg=false|sm=true under FlowWizard: a phone browser with dvh at
  //   640x300 computes max-height 238px, before #354 243px
  // Named mutation 'center loses its gap' (Overlay.tsx: center sm without
  // --ov-max-h-gap). Observed:
  //   control: a dvh browser computes every size it computed before #354:
  //   center|lg=false|sm=true under no caller: a phone browser with dvh at
  //   390x844 computes max-height 759.6px, before #354 727.6px
  const PRE_354_RULE: Record<string, string> = {
    width: "var(--ov-w, 100%)",
    height: "var(--ov-h, auto)",
    "max-width": "min(100vw, var(--ov-max-w, 100vw))",
    "max-height": "min(100dvh, var(--ov-max-h, 100dvh))",
  };
  const CALLERS = CALLERS_PRE_417;
  let graded = 0;
  for (const v of ["center", "sheet", "dock", "corner"] as const) {
    for (const lg of [false, true]) {
      for (const sm of [false, true]) {
        const k = `${v}|lg=${lg}|sm=${sm}`;
        const was = JSON.parse(PRE_354[k]) as { surface: string; vars: Record<string, string> };
        const g = overlayGeometry(v, lg, sm);
        const styles = [{ who: "no caller", style: {} as Record<string, string> },
          ...CALLERS.filter((c) => c.variant === v)];
        for (const { who, style } of styles) {
          for (const vp of VIEWPORTS) {
            const b = phone(vp, true);
            const before = surfaceSizes(b, was.surface, { ...was.vars, ...style }, PRE_354_RULE);
            const now = surfaceSizes(b, g.surface, { ...g.vars, ...style });
            for (const prop of SIZE_PROPS) {
              assert(sameSize(before[prop], now[prop]),
                `${k} under ${who}: ${b.name} computes ${prop} ${show(now[prop])}, before #354 ${show(before[prop])}`);
            }
            graded++;
          }
        }
      }
    }
  }
  // 16 variant geometries alone, then each caller under its variant's 4.
  assert(graded === (16 + 4 * CALLERS.length) * VIEWPORTS.length, `graded ${graded} cases`);
});

test("control: full is unchanged at 100% of the host, with and without dvh (#354)", () => {
  // `full` sets no vars of its own; `.overlay-surface.overlay-full` sets
  // --ov-max-h: 100%, which both rules must still read ahead of the fraction.
  // On a phone the host is the visible viewport and 100vh the larger one, so
  // a rule that skipped --ov-max-h would show here as 100vh.
  // Named mutation 'the fallback skips --ov-max-h' (index.css: the @supports
  // rule made min(100vh, calc(var(--ov-max-h-frac, 1) * 100vh -
  // var(--ov-max-h-gap, 0px)))). Observed:
  //   control: full is unchanged at 100% of the host, with and without dvh
  //   (#354): a phone browser without dvh at 390x844 computes full's
  //   max-height 844px, not 100% of the host (759.6px)
  const want = `{"wrap":"absolute inset-0 flex","surface":"overlay-full sheet-enter","vars":{}}`;
  for (const lg of [false, true]) {
    for (const sm of [false, true]) {
      const got = JSON.stringify(overlayGeometry("full", lg, sm));
      assert(got === want, `full (lg=${lg} sm=${sm}) changed: ${got}`);
    }
  }
  const g = overlayGeometry("full", false, false);
  for (const vp of VIEWPORTS) {
    for (const dvh of [true, false]) {
      const b = phone(vp, dvh);
      const got = surfaceSizes(b, g.surface, g.vars);
      for (const prop of ["height", "max-height"] as const) {
        const c = got[prop];
        assert("px" in c && Math.abs(c.px - b.hostH) < 1e-6,
          `${b.name} computes full's ${prop} ${show(c)}, not 100% of the host (${b.hostH}px)`);
      }
    }
  }
});

// -------------------------- the callers' caps without dvh (S7, #417)
// #354 took the dvh values out of overlayGeometry, but six callers still
// handed the Overlay a dvh cap of their own through `surfaceStyle`, and
// --ov-max-h REPLACES the fraction in both rules. On those six surfaces the
// @supports fallback was still `min(100vh, 90dvh)`, invalid without dvh, so
// max-height fell to `none` there: #354's defect, reached by another path.
// Each caller now passes a fraction, and a center caller zeroes the 2rem gap
// center's sm geometry sets, which it did not have before. Two tests hold
// that: a scan of ui/src that no `--ov-*` value holds a small, large or
// dynamic viewport unit, whoever sets it, and the six callers' own values,
// read out of their files, computed against the caps they replaced.
//
// Every test here was run RED under a named mutation in a private copy of
// ui/, and each "Observed" quote is that run's failure line, as above.

/** Every `--ov-*` custom property a source text SETS, with what it sets it
 *  to: a quoted key in an object (a React style bag, `"--ov-max-h": "90dvh"`),
 *  a `setProperty("--ov-...", ...)` call, and in a stylesheet a declaration
 *  (`--ov-max-h: 100%;`, comments stripped first). A read, `var(--ov-max-h,
 *  ...)`, sets nothing: the dvh in the main rule's fallback is fine, since
 *  that rule is only used where dvh parses. A value that is not a plain
 *  string literal (a variable, a template with `${}`) is kept with
 *  `literal: false`, because nothing here can say what it holds. */
interface OvSet { key: string; value: string; literal: boolean; line: number }
function ovSettings(text: string, stylesheet: boolean): OvSet[] {
  const out: OvSet[] = [];
  const lineAt = (i: number): number => text.slice(0, i).split("\n").length;
  if (stylesheet) {
    // Blank the comments out character for character, so line numbers hold.
    const bare = text.replace(/\/\*[\s\S]*?\*\//g, (c) => c.replace(/[^\n]/g, " "));
    for (const m of bare.matchAll(/(?<![\w-])(--ov-[\w-]+)\s*:\s*([^;}]*)/g)) {
      out.push({ key: m[1], value: m[2].trim(), literal: true, line: lineAt(m.index!) });
    }
    return out;
  }
  // An object key is quoted with " or ', never a backtick, which is no key
  // at all in JS; so a comment's markdown (`--ov-max-h`: ...) is not read as
  // one. setProperty's argument may be any string.
  const keyed = /(["'])(--ov-[\w-]+)\1\s*:\s*|setProperty\(\s*(["'`])(--ov-[\w-]+)\3\s*,\s*/g;
  for (const m of text.matchAll(keyed)) {
    const key = m[2] ?? m[4];
    const rest = text.slice(m.index! + m[0].length);
    const lit = /^(["'`])((?:(?!\1)[^\\]|\\.)*)\1/.exec(rest);
    const literal = !!lit && !(lit[1] === "`" && lit[2].includes("${"));
    const value = lit ? lit[2] : (/^[^,}\n)]*/.exec(rest)?.[0] ?? "").trim();
    out.push({ key, value, literal, line: lineAt(m.index!) });
  }
  return out;
}
/** A small, large or dynamic viewport unit: dvh, svh and lvh, and their
 *  width, inline, block, min and max forms, which a browser that predates
 *  them cannot parse either. */
const NEW_VIEWPORT_UNIT = /(?:^|[^a-z])[dsl]v(?:h|w|i|b|min|max)\b/i;

/** Every .ts, .tsx and .css file under ui/src but the tests, which quote the
 *  old values on purpose (this file's CALLERS_PRE_417 among them). */
const SRC_DIR = resolve("../");
function sourceFiles(dir: string, out: string[] = []): string[] {
  for (const name of fs.readdirSync(dir)) {
    const p = `${dir.replace(/[\\/]$/, "")}/${name}`;
    if (fs.statSync(p).isDirectory()) {
      if (name !== "node_modules" && name !== "__tests__") sourceFiles(p, out);
    } else if (/\.(?:tsx?|css)$/.test(name) && !/\.test\.tsx?$/.test(name)) {
      out.push(p);
    }
  }
  return out;
}
const rel = (p: string): string => p.slice(SRC_DIR.replace(/[\\/]$/, "").length + 1);

test("the --ov-* scanner finds what it is for, and only that (#417)", () => {
  // The scan below grades nothing if this reads nothing, so it is held to
  // shapes it must flag and shapes it must pass.
  // Named mutation 'the scanner skips style bags' (this file: the quoted-key
  // arm of `keyed` removed, leaving the setProperty arm). Observed:
  //   the --ov-* scanner finds what it is for, and only that (#417): missed
  //   "--ov-max-h" in surfaceStyle={{ "--ov-max-h": "90dvh" } as
  //   CSSProperties}
  const flags = (text: string, stylesheet: boolean): string[] =>
    ovSettings(text, stylesheet).filter((s) => !s.literal || NEW_VIEWPORT_UNIT.test(s.value)).map((s) => s.key);
  for (const [text, stylesheet, key] of [
    [`surfaceStyle={{ "--ov-max-h": "90dvh" } as CSSProperties}`, false, "--ov-max-h"],
    [`surfaceStyle={{"--ov-w":"940px","--ov-max-h":"92svh"} as CSSProperties}`, false, "--ov-max-h"],
    [`{ '--ov-h': 'calc(100lvh - 2rem)' }`, false, "--ov-h"],
    [`el.style.setProperty("--ov-max-h", "90dvh")`, false, "--ov-max-h"],
    ["{ \"--ov-max-w\": `${cap}` }", false, "--ov-max-w"],
    [`{ "--ov-max-h": cap }`, false, "--ov-max-h"],
    [`.x { --ov-max-w: 96dvw; }`, true, "--ov-max-w"],
    [`.x {\n  --ov-h: 100svh\n}`, true, "--ov-h"],
  ] as [string, boolean, string][]) {
    assert(JSON.stringify(flags(text, stylesheet)) === JSON.stringify([key]), `missed "${key}" in ${text}`);
  }
  for (const [text, stylesheet] of [
    [`max-height: min(100dvh, var(--ov-max-h, calc(var(--ov-max-h-frac, 1) * 100dvh)));`, true],
    [`/* it used to be --ov-max-h: 85dvh */ .x { --ov-max-h: 100%; }`, true],
    [`{ "--ov-max-h-frac": "0.9", "--ov-max-h-gap": "0px" }`, false],
    [`{ "--ov-w": "940px", "--ov-max-w": "96vw" }`, false],
    ["// never `76dvh` in `--ov-max-h`: index.css multiplies the fraction", false],
  ] as [string, boolean][]) {
    const got = flags(text, stylesheet);
    assert(got.length === 0, `flagged ${got.join(", ")} in ${text}, which holds no new viewport unit in an --ov-* value`);
  }
  const lines = ovSettings(`a\n.x {\n  /* --ov-h: 1dvh */\n  --ov-h: 100%;\n}`, true);
  assert(lines.length === 1 && lines[0].line === 4, `the declaration is on line 4, got ${JSON.stringify(lines)}`);
});

test("no --ov-* value anywhere in ui/src holds dvh, svh or lvh (#417)", () => {
  // A dvh value in any --ov-* var reaches the @supports fallback, or a rule
  // with no fallback at all, and leaves it invalid in a browser without dvh.
  // Named mutation 'one caller keeps 90dvh' (QuickFlow.tsx: surfaceStyle back
  // to { "--ov-max-h": "90dvh" }). Observed:
  //   no --ov-* value anywhere in ui/src holds dvh, svh or lvh (#417):
  //   components/flows/QuickFlow.tsx:275 sets --ov-max-h to "90dvh", a unit
  //   a browser without dvh cannot parse
  // Under 'the scanner skips style bags' (above) it goes red on its floor
  // instead, having read none of the files it must. Observed:
  //   no --ov-* value anywhere in ui/src holds dvh, svh or lvh (#417): the
  //   scan found no --ov-* setting in components/Overlay.tsx,
  //   components/flows/FlowWizard.tsx, components/sky/ClassicSkyTools.tsx,
  //   components/flows/TonightPanel.tsx, components/flows/QuickFlow.tsx,
  //   components/flows/FlowEditSheet.tsx, components/flows/FlowPaletteSheet.tsx
  //   (read 887 files, settings in index.css)
  const files = sourceFiles(SRC_DIR);
  const bad: string[] = [];
  const setters = new Map<string, number>();
  for (const f of files) {
    const found = ovSettings(fs.readFileSync(f, "utf8"), f.endsWith(".css"));
    if (found.length) setters.set(rel(f), found.length);
    for (const s of found) {
      if (!s.literal) {
        bad.push(`${rel(f)}:${s.line} sets ${s.key} to ${s.value || "an expression"}, which is not a string ` +
          "literal this scan can grade");
      } else if (NEW_VIEWPORT_UNIT.test(s.value)) {
        bad.push(`${rel(f)}:${s.line} sets ${s.key} to "${s.value}", a unit a browser without dvh cannot parse`);
      }
    }
  }
  assert(bad.length === 0, bad.join("; "));
  // What the scan must have read for its silence to mean anything: the
  // variants, the stylesheet's `full` rule and every caller.
  const must = ["components/Overlay.tsx", "index.css", ...CALLERS_PRE_417.map((c) => c.file)];
  const unread = must.filter((f) => !setters.has(f));
  assert(unread.length === 0,
    `the scan found no --ov-* setting in ${unread.join(", ")} (read ${files.length} files, ` +
    `settings in ${[...setters.keys()].join(", ")})`);
});

/** The one `surfaceStyle={{ ... }}` bag a caller's file passes, parsed. */
function callerStyle(file: string): Record<string, string> {
  const text = fs.readFileSync(`${SRC_DIR.replace(/[\\/]$/, "")}/${file}`, "utf8");
  const bags = [...text.matchAll(/surfaceStyle=\{\{([^{}]*)\}/g)];
  assert(bags.length === 1, `${file} has ${bags.length} surfaceStyle bags, expected one`);
  const out: Record<string, string> = {};
  for (const s of ovSettings(bags[0][1], false)) out[s.key] = s.value;
  assert(Object.keys(out).length > 0, `${file}'s surfaceStyle sets no --ov-* var: ${bags[0][1]}`);
  return out;
}

test("control: in a dvh browser the six callers compute what their dvh caps did (#417)", () => {
  // The fractions must move nothing where dvh exists. Each caller's style is
  // read out of its own file, so this grades what ships, and set against the
  // cap it replaced (CALLERS_PRE_417), under both the variant's phone and sm
  // geometry, on a phone with its toolbar showing and on a flat desktop.
  // Named mutation 'a center caller keeps the gap' (FlowWizard.tsx: the
  // surfaceStyle without "--ov-max-h-gap": "0px", so center's sm 2rem stays).
  // Observed:
  //   control: in a dvh browser the six callers compute what their dvh caps
  //   did (#417): FlowWizard (center lg=false sm=true, now
  //   {"--ov-max-h-frac":"0.9"}): a phone browser with dvh at 390x844
  //   computes max-height 651.64px, with its dvh cap 683.64px
  // Named mutation 'the sheet's fraction off by one' (FlowEditSheet.tsx:
  // "0.75" for "0.76"). Observed:
  //   control: in a dvh browser the six callers compute what their dvh caps
  //   did (#417): FlowEditSheet (sheet lg=false sm=false, now
  //   {"--ov-max-h-frac":"0.75"}): a phone browser with dvh at 390x844
  //   computes max-height 569.7px, with its dvh cap 577.296px
  // 'one caller keeps 90dvh' leaves this green, as it should: a dvh cap
  // computes the same where dvh parses.
  let graded = 0;
  for (const c of CALLERS_PRE_417) {
    const now = callerStyle(c.file);
    for (const lg of [false, true]) {
      for (const sm of [false, true]) {
        const g = overlayGeometry(c.variant, lg, sm);
        const where = `${c.who} (${c.variant} lg=${lg} sm=${sm}, now ${JSON.stringify(now)})`;
        for (const vp of VIEWPORTS) {
          for (const b of [phone(vp, true), flat(vp, true)]) {
            const before = surfaceSizes(b, g.surface, { ...g.vars, ...c.style });
            const after = surfaceSizes(b, g.surface, { ...g.vars, ...now });
            for (const prop of SIZE_PROPS) {
              assert(sameSize(before[prop], after[prop]),
                `${where}: ${b.name} computes ${prop} ${show(after[prop])}, with its dvh cap ${show(before[prop])}`);
            }
            graded++;
          }
        }
      }
    }
  }
  assert(graded === CALLERS_PRE_417.length * 4 * VIEWPORTS.length * 2, `graded ${graded} cases`);
});

test("the six callers keep their height clamp in a browser without dvh (#417)", () => {
  // What #417 buys: without dvh each caller's surface still has a clamp
  // inside the screen, and where vh and dvh measure the same window it is the
  // clamp a dvh browser computes. Under their dvh caps every one of the six
  // computed max-height invalid here.
  // Named mutation 'one caller keeps 90dvh' (QuickFlow.tsx, as above).
  // Observed:
  //   the six callers keep their height clamp in a browser without dvh
  //   (#417): QuickFlow (center lg=false sm=false, now
  //   {"--ov-max-h":"90dvh"}): a phone browser without dvh at 390x844
  //   computes max-height invalid at computed-value time (this browser
  //   cannot parse the unit "dvh")
  let graded = 0;
  for (const c of CALLERS_PRE_417) {
    const now = callerStyle(c.file);
    for (const lg of [false, true]) {
      for (const sm of [false, true]) {
        const g = overlayGeometry(c.variant, lg, sm);
        const where = `${c.who} (${c.variant} lg=${lg} sm=${sm}, now ${JSON.stringify(now)})`;
        for (const vp of VIEWPORTS) {
          const old = phone(vp, false);
          const got = surfaceSizes(old, g.surface, { ...g.vars, ...now });
          for (const prop of SIZE_PROPS) {
            assert(got[prop].ok, `${where}: ${old.name} computes ${prop} ${show(got[prop])}`);
          }
          const mh = got["max-height"];
          assert("px" in mh && mh.px > 0 && mh.px <= vp.h + 1e-6,
            `${where}: ${old.name} computes max-height ${show(mh)}, which is no clamp inside the screen`);
          const a = surfaceSizes(flat(vp, true), g.surface, { ...g.vars, ...now })["max-height"];
          const b = surfaceSizes(flat(vp, false), g.surface, { ...g.vars, ...now })["max-height"];
          assert(sameSize(a, b),
            `${where}: at ${vp.w}x${vp.h} with no toolbar, a browser without dvh computes max-height ` +
            `${show(b)} where one with dvh computes ${show(a)}`);
          graded++;
        }
      }
    }
  }
  assert(graded === CALLERS_PRE_417.length * 4 * VIEWPORTS.length, `graded ${graded} cases`);
});

// ------------------------------------------- `full`, mounted: the behaviours
// The CSS and the resolver say where the surface goes. These say it still
// behaves like every other overlay once it is there: head and foot outside the
// one scroll region, Tab kept inside, Escape closes, focus goes back to the
// control that opened it. Each is asserted on a real render in jsdom, at phone
// and desktop width where width could matter.

const doc = win.document;
const byId = (id: string): any => doc.getElementById(id);
const surfaceEl = (): any => doc.querySelector('#ad-overlay-root [role="dialog"]');
function press(key: string, shiftKey = false): void {
  act(() => {
    doc.dispatchEvent(new win.KeyboardEvent("keydown", { key, shiftKey, bubbles: true, cancelable: true }));
  });
}
const container = doc.createElement("div");
doc.body.appendChild(container);
const opener = doc.createElement("button");
opener.id = "opener";
opener.textContent = "FRAME ON SKY";
doc.body.appendChild(opener);

/** Mount the framing-modal shape: CANCEL in the head, a control in the body,
 *  DONE in the foot. Tab order is therefore cancel, grid, done. */
function mountFull(open = true): { rerender(o: boolean): void; unmount(): void; closes(): number } {
  let closes = 0;
  const root = createRoot(container);
  const el = (o: boolean) => h(Overlay, {
    open: o,
    label: "Frame M31",
    variant: "full",
    onClose: () => { closes++; },
    head: h("div", null, h("button", { id: "cancel" }, "CANCEL"), h("span", null, "FRAME M31")),
    foot: h("div", null, h("button", { id: "done" }, "DONE")),
    children: h("div", { id: "sky" }, h("button", { id: "grid" }, "GRID")),
  });
  act(() => { root.render(el(open)); });
  return {
    rerender: (o) => act(() => { root.render(el(o)); }),
    unmount: () => act(() => { root.unmount(); }),
    closes: () => closes,
  };
}

test("mounted full keeps the head and foot rows outside the scroll region", () => {
  // Named mutation 'head rendered inside the body' (Overlay.tsx: the
  // `overlay-head` row moved inside the `overlay-body` div). Observed:
  //   mounted full keeps the head and foot rows outside the scroll region:
  //   phone: surface rows are ["overlay-body","overlay-foot"], expected head /
  //   body / foot
  // 'full maps to center' also turns this red. Observed:
  //   mounted full keeps the head and foot rows outside the scroll region:
  //   phone: the mounted surface lacks .overlay-full: "overlay-surface
  //   rounded-t-2xl sheet-enter "
  for (const wide of [false, true]) {
    mqMatches = wide;
    const where = wide ? "desktop" : "phone";
    const m = mountFull();
    try {
      const s = surfaceEl();
      assert(!!s, `${where}: no dialog rendered into the portal host`);
      assert(/(?:^|\s)overlay-full(?:\s|$)/.test(s.className),
        `${where}: the mounted surface lacks .overlay-full: "${s.className}"`);
      const rows = [...s.children].map((c: any) => String(c.className).trim().split(/\s+/)[0]);
      assert(JSON.stringify(rows) === JSON.stringify(["overlay-head", "overlay-body", "overlay-foot"]),
        `${where}: surface rows are ${JSON.stringify(rows)}, expected head / body / foot`);
      const body = s.querySelector(".overlay-body");
      assert(!body.contains(byId("cancel")) && !body.contains(byId("done")),
        `${where}: CANCEL / DONE are inside the scroll region and can scroll out of reach`);
      assert(body.contains(byId("sky")), `${where}: the content is not in the scroll region`);
    } finally {
      m.unmount();
    }
  }
  mqMatches = false;
});

test("mounted full keeps Tab inside the surface", () => {
  // Named mutation 'Tab wrap removed' (Overlay.tsx: the
  // `!e.shiftKey && document.activeElement === last` branch deleted).
  // Observed:
  //   mounted full keeps Tab inside the surface: Tab from DONE (the last
  //   control) must wrap to CANCEL, focus is on done
  const m = mountFull();
  try {
    assert(doc.activeElement === byId("cancel"),
      `on open focus must land on the first control (CANCEL), got ${doc.activeElement?.id || doc.activeElement?.tagName}`);
    act(() => { byId("done").focus(); });
    press("Tab");
    assert(doc.activeElement === byId("cancel"),
      `Tab from DONE (the last control) must wrap to CANCEL, focus is on ${doc.activeElement?.id}`);
    press("Tab", true);
    assert(doc.activeElement === byId("done"),
      `Shift+Tab from CANCEL (the first control) must wrap to DONE, focus is on ${doc.activeElement?.id}`);
    // Control: Tab from a middle control is left to the browser.
    act(() => { byId("grid").focus(); });
    press("Tab");
    assert(doc.activeElement === byId("grid"), "Tab from a middle control must not be intercepted");
  } finally {
    m.unmount();
  }
});

test("mounted full closes on Escape", () => {
  // Named mutation 'Escape ignored' (Overlay.tsx: `onClose();` removed from
  // the Escape branch). Observed:
  //   mounted full closes on Escape: Escape called onClose 0 times,
  //   expected once
  const m = mountFull();
  try {
    press("a");
    assert(m.closes() === 0, `a plain key must not close the modal, onClose ran ${m.closes()} times`);
    press("Escape");
    assert(m.closes() === 1, `Escape called onClose ${m.closes()} times, expected once`);
  } finally {
    m.unmount();
  }
});

test("mounted full hands focus back to the control that opened it", () => {
  // Named mutation 'restore-focus dropped' (Overlay.tsx: the effect's cleanup
  // no longer calls `openerRef.current?.focus?.()`). Observed:
  //   mounted full hands focus back to the control that opened it: after
  //   close focus is on BODY, not the opener
  act(() => { opener.focus(); });
  const m = mountFull(true);
  try {
    const s = surfaceEl();
    assert(doc.activeElement !== opener && s.contains(doc.activeElement),
      "on open focus must move into the surface");
    m.rerender(false);
    assert(!surfaceEl(), "a closed overlay must render nothing");
    assert(doc.activeElement === opener,
      `after close focus is on ${doc.activeElement?.id || doc.activeElement?.tagName}, not the opener`);
  } finally {
    m.unmount();
  }
});

// ---------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\noverlay.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
