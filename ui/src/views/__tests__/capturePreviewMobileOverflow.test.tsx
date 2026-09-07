// capturePreviewMobileOverflow.test.tsx — the Live Preview panel must not
// spill off the right edge of a phone.
//
//   Run directly:  npx tsx src/views/__tests__/capturePreviewMobileOverflow.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE BUG (412px viewport, Capture view, Live Preview panel). Below the `md`
// breakpoint the outer grid (`grid gap-4 md:grid-cols-[1fr_320px] ...`) has no
// explicit column, so its single implicit column is an "auto" track sized by
// the GRID ITEM's automatic minimum size -- which defaults to the item's
// unshrunk content width, not 0. The item in question is the plain
// `<div className="relative h-fit">` in CaptureView.tsx that wraps
// `<LivePreview/>`; `<LivePreview/>`'s own Panel already carried `min-w-0`
// (added for an earlier, unrelated CameraDial bug), but that override lives
// one level too deep to relax the actual grid item's auto-track sizing.
//
// Measured live (Chrome, real DOM, a rig frame at 6252x4176/60s/gain125) by
// constraining that grid to a 412px-equivalent box: without `min-w-0` on the
// grid item, the whole panel subtree sized itself to its natural ~1170px
// content width and got clipped by `<main>`'s `overflow-x-hidden` -- taking
// the meta line, the focus verdict, the preview image AND the scale bar
// overlay off the right edge of the screen together, in one shot. Adding
// `min-w-0` to that one div, live, resolved all four at once.
//
// WHAT THIS FILE CANNOT DO. jsdom performs no layout: every element's
// clientWidth/scrollWidth report 0 (see captureLastFrameDom.test.tsx's own
// note on this), so a real "scrollWidth > clientWidth" assertion would pass
// or fail independent of the actual CSS and prove nothing. This is therefore
// a CLASS-SHAPE test: it renders the real component tree with the real bug
// numbers and asserts the specific classes the fix depends on are present on
// the specific elements that need them, and that nothing along the way
// reintroduces `whitespace-nowrap` on the two long text lines.

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
win.Element.prototype.setPointerCapture = function () {};
win.Element.prototype.releasePointerCapture = function () {};
win.Element.prototype.hasPointerCapture = function () { return false; };
win.HTMLElement.prototype.scrollIntoView = function () {};

win.fetch = async () => ({
  ok: true, status: 200,
  headers: { get: () => "application/json" },
  json: async () => ({}),
  text: async () => "{}",
});

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "localStorage", "requestAnimationFrame", "cancelAnimationFrame",
  "getComputedStyle", "matchMedia", "WebSocket", "fetch",
  "ResizeObserver", "IntersectionObserver",
]) {
  const v = k === "window" ? win
    : k === "ResizeObserver" || k === "IntersectionObserver"
      ? class { observe() {} unobserve() {} disconnect() {} }
      : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../store");
const CaptureView = (await import("../CaptureView")).default;
type PreviewInfo = import("../../types").PreviewInfo;

let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

// The exact numbers from the field report: a real, long meta line
// ("6252x4176 . 60s . gain 125 . bin 1") and a real, long focus verdict
// ("Far out of focus - Blob is 2268 px across ..."), produced by feeding
// focusVerdict.ts a defocus blob (r80=1134 -> "2268 px across") with too few
// stars to be trusted (lib/focusVerdict.ts RESOLVED_MIN_STARS=30).
const PREVIEW: PreviewInfo = {
  id: 1,
  stats: { min: 0, max: 65535, mean: 900, median: 850, std: 120 },
  histogram: [1, 2, 3], histogram_domain: "display",
  exposure_s: 60, gain: 125, binning: 1,
  data_width: 6252, data_height: 4176,
  display_width: 1400, display_height: 933,
  mime: "image/jpeg", source: "camera" as any,
  // is_stretched -> the <img> path (the linear canvas path wants a 2D context
  // jsdom does not have; this file is about layout, not the LUT).
  is_stretched: true, data_is_linear: false, has_lossless: false,
  full_well: null,
  auto_levels: { black: 0, mid: 0.5, white: 1 },
  hfr: 6.1, stars: 5, defocus_r80: 1134,
  ts: Math.floor(Date.now() / 1000),
} as unknown as PreviewInfo;

function mountCapture(): HTMLElement {
  useStore.setState({
    status: {
      connected: { camera: { connected: true, name: "sim cam" } },
      looping: false, mode: "sim", busy_lanes: [],
      camera: { temperature: -10, can_cool: true, has_dew_heater: false,
                width: 6252, height: 4176, max_gain: 600, max_bin: 4 },
    },
    principal: { role: "operator", email: null,
                 caps: ["view.status", "view.preview", "control.capture"] },
    wsPhase: "up", wsConnected: true,
    previews: [PREVIEW], livePreviewId: PREVIEW.id, selectedPreviewId: null,
    sequence: { state: "idle" },
  } as never);
  const host = win.document.createElement("div");
  win.document.body.appendChild(host);
  const root = createRoot(host);
  act(() => { root.render(createElement(CaptureView)); });
  return host as HTMLElement;
}

const all = (root: HTMLElement, sel: string): HTMLElement[] =>
  Array.from(root.querySelectorAll(sel));
/** First element under `root` whose OWN text (not descendants') contains `needle`. */
function leafContaining(root: HTMLElement, needle: string): HTMLElement | null {
  const els = all(root, "*");
  // walk deepest-first isn't necessary: React never nests the same literal
  // text in two elements here, so the first match found by textContent (which
  // includes descendants) closest to a leaf is the one with no children
  // carrying it too.
  return els.find((el) => (el.textContent ?? "").includes(needle)
    && !Array.from(el.children).some((c) => (c.textContent ?? "").includes(needle)))
    ?? null;
}
function hasClass(el: Element | null, cls: string): boolean {
  return !!el && el.className.toString().split(/\s+/).includes(cls);
}
function ancestors(el: Element): Element[] {
  const out: Element[] = [];
  let cur: Element | null = el.parentElement;
  while (cur) { out.push(cur); cur = cur.parentElement; }
  return out;
}

const host = mountCapture();

// ================================================== 1. the grid item itself
await test("the CaptureView grid item wrapping Live Preview allows the track to shrink",
  () => {
    const h2 = all(host, "h2.panel-title").find((h) => /live preview/i.test(h.textContent ?? ""));
    assert(!!h2, "no Live Preview panel rendered at all");
    const panel = h2!.closest("section.panel") as HTMLElement;
    assert(!!panel, "panel title is not inside a .panel section");
    const gridItem = panel.closest("div.h-fit") as HTMLElement;
    assert(!!gridItem, "no h-fit grid-item wrapper found above the panel");
    assert(hasClass(gridItem, "min-w-0"),
      `the grid item CaptureView wraps <LivePreview/> in is missing min-w-0 `
      + `(className="${gridItem.className}") -- below the md breakpoint this `
      + "grid has no explicit column, so its single auto track sizes itself "
      + "to this item's full, unshrunk content width instead of the viewport, "
      + "which is what took the whole panel off the right edge of a phone.");
  });

// ============================================ 2. the meta line can wrap
await test("the frame-meta line (size/exposure/gain/bin) can wrap instead of overflowing",
  () => {
    const meta = leafContaining(host, "6252×4176");
    assert(!!meta, `the meta line never rendered: ${host.textContent?.slice(0, 200)}`);
    assert(!/whitespace-nowrap/.test(meta!.className),
      `the meta span itself carries whitespace-nowrap: ${meta!.className}`);
    // The row that holds it (LiveStackReadout + PreviewMeta) must be allowed
    // to shrink -- that is the row that was full-width-forced before the fix.
    const row = ancestors(meta!).find((a) => a.tagName === "SPAN" && /\bflex\b/.test(a.className));
    assert(!!row, "no flex row wraps the meta line");
    assert(hasClass(row as Element, "min-w-0"),
      `the meta row is missing min-w-0: ${(row as Element).className}`);
    // Nothing between the text and the panel may force single-line text.
    for (const a of [meta!, ...ancestors(meta!)]) {
      assert(!/whitespace-nowrap/.test(a.className?.toString?.() ?? ""),
        `an ancestor of the meta line forces whitespace-nowrap: ${a.tagName}.${a.className}`);
      if (a.tagName === "SECTION" && a.classList.contains("panel")) break;
    }
    // A title carries the full text even though it also wraps -- desktop hover
    // parity, and a fallback if some future layout truncates it instead.
    assert((meta!.getAttribute("title") ?? "").includes("60s"),
      `the meta span has no title carrying the full readout: "${meta!.getAttribute("title")}"`);
  });

// ============================================ 3. the focus verdict can wrap
await test("the far-out-of-focus verdict message can wrap instead of overflowing", () => {
  const headline = leafContaining(host, "Far out of focus");
  assert(!!headline, `the defocused verdict never rendered: ${host.textContent?.slice(0, 300)}`);
  const message = leafContaining(host, "px across");
  assert(!!message, "the blob-size message never rendered");
  assert(/2268 px across/.test(message!.textContent ?? ""),
    `expected the r80=1134 fixture to read 2268px across, got: ${message!.textContent}`);
  // FocusVerdict's own row is `flex flex-wrap` (untouched -- owned elsewhere);
  // what this fix adds is a shrinkable box AROUND it, from LivePreview.tsx.
  const verdictRow = message!.closest("div.flex.flex-wrap") as HTMLElement;
  assert(!!verdictRow, "no flex-wrap row around the verdict spans");
  const wrapper = verdictRow.parentElement as HTMLElement;
  assert(hasClass(wrapper, "min-w-0"),
    `FocusVerdict's wrapping div is missing min-w-0: ${wrapper?.className}`);
  for (const a of [message!, headline!, verdictRow]) {
    assert(!/whitespace-nowrap/.test(a.className?.toString?.() ?? ""),
      `the verdict carries whitespace-nowrap: ${a.tagName}.${a.className}`);
  }
});

// ==================================================== 4. the stage itself
await test("the preview stage is capped to its container's width", () => {
  const stage = all(host, '[class*="preview-stage"]')[0] as HTMLElement;
  assert(!!stage, "no .preview-stage rendered");
  assert(hasClass(stage, "w-full"), `stage missing w-full: ${stage.className}`);
  assert(hasClass(stage, "max-w-full"),
    `stage missing max-w-full (belt-and-suspenders against a forced-wide `
    + `ancestor): ${stage.className}`);
  assert(hasClass(stage, "overflow-hidden"),
    `stage missing overflow-hidden -- without it a stale/oversized transform `
    + `layer would bleed past the box instead of being clipped inside it`);
});

// ------------------------------------------------------------------- report
const total = passed + failed;
console.log(`capturePreviewMobileOverflow.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
