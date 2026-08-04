// stretchHistogram.test.tsx — containment contract for the Advanced disclosure
// on the stretch histogram (components/preview/StretchHistogram.tsx).
//
//   Run directly:  npx tsx src/components/preview/__tests__/stretchHistogram.test.tsx
//   Also type-checked by `tsc -b` in the build.
//
// WHAT THIS SUITE CAN AND CANNOT SEE. The bug it guards is geometric: opening
// Advanced pushed absolutely-positioned furniture out of the panel, and the only
// reason the page did not scroll sideways was an ancestor several levels up that
// clips (`main` in App.tsx carries overflow-x:hidden) — which is also why the
// escaped content was CUT rather than reachable. There is no layout engine in
// this runner (no jsdom, no browser, nothing measures anything), so NOTHING here
// asserts a pixel. The geometry was measured in a real browser instead —
// Chromium and WebKit, the real app shell, a frame seeded into the store, seven
// viewport widths from 320 to 1440:
//
//   before:  Advanced put content 17px past the Live Preview panel's right edge
//            (the "⚠ CLIPPED" tag, centred on a white handle that itself sits
//            16px past the end of the track), and took main.scrollWidth 1px past
//            main.clientWidth at 320/390/600.
//   after:   escape 0px at every width in both engines, main.scrollWidth ==
//            main.clientWidth, and the 68px chip renders whole with its right
//            edge exactly on the track's right edge.
//
// So what is asserted below is the CONTRACT that produced those numbers — which
// classes carry the containment and how the chips are anchored. If someone
// deletes the clip box or restores the always-centred chip, these fail; whether
// the result is still 0px on a device has to be re-measured live.

import { renderToStaticMarkup } from "react-dom/server";
import { StretchHistogram } from "../StretchHistogram";
import type { PreviewInfo, StretchParams } from "../../../types";

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(a: T, b: T, msg = ""): void {
  if (a !== b) throw new Error(`${msg} — expected ${JSON.stringify(b)}, got ${JSON.stringify(a)}`);
}

// ------------------------------------------------------------------ fixtures
/** A linear frame that CLIPS, because the clip tag is the widest thing the
 *  handles hang off and therefore the worst case for escaping the track. */
function frame(over: Partial<PreviewInfo> = {}): PreviewInfo {
  return {
    id: 7,
    stats: { min: 120, max: 65535, mean: 900, median: 810, std: 220 },
    histogram: [1, 40, 900, 400, 90, 12, 3, 1],
    histogram_domain: "linear",
    exposure_s: 120,
    gain: 100,
    binning: 1,
    data_width: 6248,
    data_height: 4176,
    display_width: 1400,
    display_height: 936,
    mime: "image/jpeg",
    source: "alpaca",
    is_stretched: false,
    data_is_linear: true,
    has_lossless: true,
    full_well: 51000,           // stats.max >= full_well => the CLIPPED tag renders
    auto_levels: { black: 0.02, mid: 0.35, white: 0.98 },
    ts: 1_700_000_000,
    ...over,
  };
}

function stretch(over: Partial<StretchParams> = {}): StretchParams {
  return { auto: true, black: 0, mid: 0.5, white: 1, brightness: 0, contrast: 0, advancedOpen: true, ...over };
}

function markup(p: PreviewInfo | null, s: StretchParams): string {
  return renderToStaticMarkup(
    <StretchHistogram preview={p} stretch={s} onStretch={() => {}} />,
  );
}

/** The class attribute of the element that renders `text`, e.g. the clip tag. */
function classesAround(html: string, text: string): string {
  const at = html.indexOf(text);
  if (at < 0) throw new Error(`markup does not contain ${JSON.stringify(text)}`);
  const open = html.lastIndexOf("<span", at);
  const cls = /class="([^"]*)"/.exec(html.slice(open, at));
  if (!cls) throw new Error(`no class attribute on the element rendering ${JSON.stringify(text)}`);
  return cls[1];
}

// ------------------------------------------------------- containment contract
test("the track sits inside a horizontal clip box, so Advanced cannot widen anything", () => {
  const html = markup(frame(), stretch());
  // overflow-x:clip, NOT hidden: clip contains the overflow without creating a
  // scroll container, so nothing can be scrolled to a place the layout does not
  // go. Measured effect: panel escape 17px -> 0px, main.scrollWidth back to
  // clientWidth (Chromium + WebKit, 320..1440).
  assert(html.includes("overflow-x-clip"), "no overflow-x-clip containment box around the histogram");
  // overflow-y MUST stay visible — the chips are drawn above (-top-5) and below
  // (-bottom-5) the track, and `overflow:hidden` on both axes would eat them.
  // `visible` is only legal beside `clip`; beside `hidden` the used value would
  // be coerced to `auto` and we would have made a scrollbar instead.
  assert(html.includes("overflow-y-visible"), "the clip box must keep overflow-y visible or it eats the chips");
  // the clip edge is the PANEL's border (borrow p-4 back with -mx-4 px-4), not
  // the track's edge, so the 32px handle boxes may still straddle their value
  // without their grip caps being sliced at 0% and 100%.
  const clipBox = /class="(-mx-4 px-4[^"]*)"/.exec(html);
  assert(!!clipBox, "the clip box must borrow the panel padding (-mx-4 px-4)");
  assert(clipBox![1].includes("overflow-x-clip"), "the padding-borrowing box IS the clip box");
  // and it must actually WRAP the track, not sit beside it
  const clipAt = html.indexOf("overflow-x-clip");
  const trackAt = html.indexOf("relative select-none");
  assert(clipAt >= 0 && trackAt > clipAt, "the clip box must wrap the relative track, not follow it");
});

test("the clip box is present with Advanced closed too (no toggle-dependent containment)", () => {
  const html = markup(frame(), stretch({ advancedOpen: false }));
  assert(html.includes("overflow-x-clip"), "containment must not depend on the disclosure state");
});

// ------------------------------------------------------------- chip anchoring
test("at white = 1.0 (the Auto default) the clip tag is anchored inward, not centred", () => {
  // This is the ordinary case, not an edge case: Auto pins white at 1.0, so the
  // old `left-1/2 -translate-x-1/2` chip hung half its width past the end of the
  // track EVERY time a clipped frame arrived.
  const cls = classesAround(markup(frame(), stretch()), "CLIPPED");
  assert(cls.includes("right-4"), `clip tag must end ON the value at the top of the range — got ${cls}`);
  assert(!cls.includes("-translate-x-1/2"), `clip tag must not be centred at white=1 — got ${cls}`);
});

test("at a low white the clip tag anchors the other way (starts on the value)", () => {
  const s = stretch({ auto: false, black: 0, mid: 0.1, white: 0.2 });
  const cls = classesAround(markup(frame(), s), "CLIPPED");
  assert(cls.includes("left-4"), `clip tag must start ON the value near the bottom of the range — got ${cls}`);
  assert(!cls.includes("-translate-x-1/2"), `clip tag must not be centred at white=0.2 — got ${cls}`);
});

test("mid-track the chip stays centred on its handle", () => {
  const s = stretch({ auto: false, black: 0, mid: 0.3, white: 0.5 });
  const cls = classesAround(markup(frame(), s), "CLIPPED");
  assert(cls.includes("left-1/2") && cls.includes("-translate-x-1/2"),
    `a chip with room on both sides should stay centred — got ${cls}`);
});

// -------------------------------------------------------------- shrinkability
test("the brightness slider can shrink below its intrinsic width", () => {
  const html = markup(frame(), stretch());
  // A flex item's default min-width:auto refuses to go below its content, and a
  // range input's intrinsic width measured 129px — an un-shrinkable floor is how
  // a panel ends up pushing its column instead of fitting into it.
  assert(html.includes("w-full min-w-0 accent-(--accent)"), "the range input needs min-w-0 to be able to shrink");
});

test("the Brightness label has room for the word it renders", () => {
  const html = markup(frame(), stretch());
  // MEASURED: ".label" upper-cases, so this box renders BRIGHTNESS at 88px of
  // content; at w-20 (80px) it spilled the whole 8px gap onto the slider.
  assert(html.includes('class="label w-24 shrink-0">Brightness'), "Brightness label must be w-24, not w-20");
});

test("NINA frames get the same treatment (locked handles are still handles)", () => {
  // The locked path pins the handles to 0/0.5/1 — i.e. two of the three sit at
  // the very ends of the track, which is exactly where furniture escapes.
  const html = markup(frame({ is_stretched: true, data_is_linear: false, histogram_domain: "display" }), stretch());
  assert(html.includes("overflow-x-clip"), "the NINA path must be contained too");
  assert(html.includes('class="label w-24 shrink-0">Contrast'), "Contrast label must be w-24, not w-20");
  assert(html.includes("w-full min-w-0 accent-(--accent)"), "the NINA contrast slider needs min-w-0 too");
});

// ------------------------------------------------- unchanged behaviour guards
test("Advanced still discloses exactly three handles, and closed discloses none", () => {
  const open = markup(frame(), stretch());
  const closed = markup(frame(), stretch({ advancedOpen: false }));
  eq((open.match(/role="slider"/g) ?? []).length, 3, "advanced handle count");
  eq((closed.match(/role="slider"/g) ?? []).length, 0, "collapsed handle count");
});

test("the handle box still straddles its value (the line marks the true level)", () => {
  // The containment fix must not have moved the handles: the visible line is
  // centred in a 32px box whose left edge is `calc(<value>% - 16px)`. If that
  // ever becomes a clamp, the line stops pointing at the level it names.
  const html = markup(frame(), stretch());
  assert(html.includes("calc(100% - 16px)"), "the white handle must still sit ON 100%, not be clamped inward");
  assert(html.includes("calc(0% - 16px)"), "the black handle must still sit ON 0%, not be clamped inward");
});

test("no frame still renders the honest empty line", () => {
  eq(markup(null, stretch()), "<p class=\"text-dim text-xs\">Awaiting first frame.</p>", "empty state");
});

// ------------------------------------------------------------------- summary
const total = passed + failed;
console.log(failures.join("\n"));
console.log(`stretchHistogram: ${passed}/${total} passed`);
export const result = { passed, failed, total };
