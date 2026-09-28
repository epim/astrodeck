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

interface NodeFsLike { readFileSync(path: string, encoding: string): string }
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
  const pct = (s: string | undefined): number | null => {
    if (!s) return null;
    const m = /^(\d+(?:\.\d+)?)(dvh|vh|vw)$/.exec(s);
    return m ? parseFloat(m[1]) : null;
  };
  for (const v of ["center", "sheet", "dock", "corner"] as const) {
    for (const lg of [false, true]) {
      for (const sm of [false, true]) {
        const { vars } = overlayGeometry(v, lg, sm);
        for (const key of ["--ov-max-h", "--ov-max-w", "--ov-w", "--ov-h"]) {
          const p = pct(vars[key]);
          assert(p === null || p <= 100,
            `${v} (lg=${lg} sm=${sm}) ${key}=${vars[key]} exceeds the viewport`);
        }
      }
    }
  }
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

test("control: the four existing variants are byte-identical to before `full`", () => {
  // Captured from overlayGeometry at the commit before S4-UOVERLAY (812fcf9e).
  // Named mutation 'center gains overlay-full' (the center sm surface became
  // "rounded-2xl sheet-enter overlay-full"). Observed:
  //   control: the four existing variants are byte-identical to before
  //   `full`: center|lg=false|sm=true changed: {"wrap":"absolute inset-0 flex
  //   items-center justify-center p-4","surface":"rounded-2xl sheet-enter
  //   overlay-full","vars":{"--ov-max-w":"560px","--ov-max-h":"calc(100dvh -
  //   2rem)"}}
  const BEFORE: Record<string, string> = {
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
  let seen = 0;
  for (const v of ["center", "sheet", "dock", "corner"] as const) {
    for (const lg of [false, true]) {
      for (const sm of [false, true]) {
        const k = `${v}|lg=${lg}|sm=${sm}`;
        const now = JSON.stringify(overlayGeometry(v, lg, sm));
        assert(now === BEFORE[k], `${k} changed: ${now}`);
        seen++;
      }
    }
  }
  assert(seen === Object.keys(BEFORE).length, `compared ${seen} of ${Object.keys(BEFORE).length} captured geometries`);
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
