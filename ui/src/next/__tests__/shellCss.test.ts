// shellCss.test.ts - a source-reading guard on shell.css for the sheet-panel
// containment rule.
//
//   Run directly:  npx tsx src/next/__tests__/shellCss.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHY THIS TEST EXISTS (jsdom has no layout, so this cannot be a DOM test):
//
// It now guards TWO measured layout escapes. The second one (2026-09-10) is the
// header row's width policy - see "the header row's width policy" section at
// the bottom of this file, and Header.tsx's own note. Same reason for the same
// shape of test: the defect is a set of computed widths, jsdom computes none,
// and the browser run that DID measure it is not something `npm test` can
// re-run. What a source read can hold is the handful of declarations the fix
// consists of, so that removing one fails here rather than in a screenshot
// three weeks later.
//
// FIRST ESCAPE (the sheet panel):
// measured with Playwright at 820x1180 on #/rig/devices/camera, `.nx-sheet-slot`
// came back x=0 w=820 - the WHOLE viewport - with the `.nx-sheet` inside it
// 674 px wide starting at x=0, covering both the 72 px rail and the hub
// column. `.nx-rail` (x=0 w=72), `.nx-col` (x=72 w=387) and `.nx-sheet-panel`
// (x=459 w=360.8) were all correct.
//
// The cause is a containing-block bug, not a sizing bug. `.nx-sheet-slot` is
// `position: absolute; inset: 0` so that it fills `.nx-sheet-layer`
// (`position: fixed`) on the PHONE layer. `.nx-sheet-panel` is the tablet/
// desktop equivalent of that layer, but it was never itself a positioned
// element - so the ABSOLUTELY POSITIONED slot inside it skips right past the
// panel and resolves its `inset: 0` against the initial containing block
// (the viewport), which is exactly the 0..820 span the probe measured.
//
// The fix is `.nx-sheet-panel { position: relative }`, which makes the panel
// the slot's containing block so `inset: 0` fills the PANEL instead of the
// viewport. A source-reading test is what this file can check: it parses
// shell.css and asserts the `.nx-sheet-panel` rule declares `position:
// relative` (the chosen fix - the smallest change, and it keeps
// `.nx-sheet-slot` as the one absolute-positioning rule rather than adding a
// second override rule for the panel case), and that the panel also narrows
// its slot with `min-width: 0` so a wide sheet body cannot stretch the 420 px
// panel out past its own width.

interface NodeFsLike {
  readFileSync(path: string, encoding: string): string;
}
const nodeImport = (s: string): Promise<unknown> =>
  (Function("m", "return import(m)") as (m: string) => Promise<unknown>)(s);
const fs = (await nodeImport("node:fs")) as NodeFsLike;

const CSS_URL = new URL("../shell/shell.css", import.meta.url);
const CSS_PATH = decodeURIComponent(CSS_URL.pathname).replace(/^\/([A-Za-z]:)/, "$1");
const css = fs.readFileSync(CSS_PATH, "utf8");

// next.css: the component classes, parsed separately from shell.css so the
// two files' rules cannot be confused with each other by a selector that
// happens to share a name.
const NEXT_CSS_URL = new URL("../next.css", import.meta.url);
const NEXT_CSS_PATH = decodeURIComponent(NEXT_CSS_URL.pathname).replace(/^\/([A-Za-z]:)/, "$1");
const nextCss = fs.readFileSync(NEXT_CSS_PATH, "utf8");

// boundary.css: the third sheet, imported after shell.css. It owns the night
// toggle's touch target, which is half of the header fix below.
const BOUNDARY_CSS_URL = new URL("../shell/boundary.css", import.meta.url);
const BOUNDARY_CSS_PATH = decodeURIComponent(BOUNDARY_CSS_URL.pathname).replace(/^\/([A-Za-z]:)/, "$1");
const boundaryCss = fs.readFileSync(BOUNDARY_CSS_PATH, "utf8");

// ---------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];

function test(name: string, fn: () => void): void {
  try {
    fn();
    passed++;
  } catch (e) {
    failed++;
    failures.push(`x ${name}: ${(e as Error).message}`);
  }
}
function assert(cond: boolean, msg: string): void {
  if (!cond) throw new Error(msg);
}

/** Pull the declaration block of the first `.cls { ... }` (or `.cls, ... { }`)
 *  rule whose selector list contains `.cls` as a standalone selector (not as
 *  a compound like `.other .cls` or `.cls-suffix`). Returns null if no such
 *  rule exists. Comments are stripped first so a note mentioning the class
 *  cannot be mistaken for a selector. Defaults to shell.css; pass `nextCss`
 *  to look in next.css instead. */
function ruleBodyFor(cls: string, source: string = css): string | null {
  const stripped = source.replace(/\/\*[\s\S]*?\*\//g, "");
  const re = /([^{}]+)\{([^}]*)\}/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(stripped)) !== null) {
    const selectors = m[1].split(",").map((s) => s.trim());
    if (selectors.includes(`.${cls}`)) return m[2];
  }
  return null;
}

/** True if `.ancestor .descendant { ... }` (a two-token descendant selector,
 *  exactly the shape of the override rule this fix could have used instead)
 *  declares `prop: value`. */
function descendantRuleDeclares(ancestor: string, descendant: string, prop: string, value: string): boolean {
  const stripped = css.replace(/\/\*[\s\S]*?\*\//g, "");
  const re = /([^{}]+)\{([^}]*)\}/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(stripped)) !== null) {
    const selectors = m[1].split(",").map((s) => s.trim());
    if (selectors.includes(`.${ancestor} .${descendant}`)) {
      const declRe = new RegExp(`\\b${prop}\\s*:\\s*${value}\\b`);
      if (declRe.test(m[2])) return true;
    }
  }
  return false;
}

test("the sheet panel is a positioned containing block for its slot", () => {
  // Measured escape: without this, `.nx-sheet-slot`'s `position: absolute;
  // inset: 0` resolves against the viewport instead of the panel, so the slot
  // (and the sheet inside it) span x=0 w=820 at 820px, covering the rail and
  // the hub column that sit to its left.
  const panelBody = ruleBodyFor("nx-sheet-panel");
  assert(panelBody != null, "no .nx-sheet-panel rule in shell.css");
  const declaresRelative = /\bposition\s*:\s*relative\b/.test(panelBody as string);
  // The alternative fix the brief allowed: a `.nx-sheet-panel .nx-sheet-slot`
  // override rule pinning the slot back to `position: static`. Either makes
  // the slot stop resolving against the viewport, so either satisfies this
  // guard - but the chosen fix is the panel declaring `position: relative`.
  const overrideStatic = descendantRuleDeclares("nx-sheet-panel", "nx-sheet-slot", "position", "static");
  assert(
    declaresRelative || overrideStatic,
    ".nx-sheet-panel must declare `position: relative` (or a " +
      "`.nx-sheet-panel .nx-sheet-slot { position: static }` override) so the " +
      "absolutely positioned .nx-sheet-slot fills the panel instead of the viewport",
  );
});

test("the panel's own slot cannot be widened by a wide sheet body", () => {
  // Without this, a sheet body wider than 420px (a long unbroken token, a
  // table, a code block) can stretch the flex-child slot out past the panel's
  // own `width: 420px; max-width: 44vw`, which is the same class of overflow
  // this fix closes, just triggered by content instead of positioning.
  assert(
    descendantRuleDeclares("nx-sheet-panel", "nx-sheet-slot", "min-width", "0"),
    ".nx-sheet-panel .nx-sheet-slot must declare `min-width: 0`",
  );
});

test("the sheet itself cannot be stretched wider than its slot by its own content", () => {
  // Measured escape: `.nx-sheet-slot` is `display: flex` and `.nx-sheet` is
  // one of its flex items, which defaults to `min-width: auto` - so the sheet
  // refuses to shrink below its content's min-content width (~888px for the
  // safety sheet) even though `.nx-sheet-panel .nx-sheet-slot` is pinned to
  // `min-width: 0`. The oversized sheet then gets clipped unreachably by
  // `.nx-app { overflow: hidden }` instead of fitting its 420px panel and
  // wrapping or scrolling its own content.
  const sheetBody = ruleBodyFor("nx-sheet", nextCss);
  assert(sheetBody != null, "no .nx-sheet rule in next.css");
  assert(
    /\bmin-width\s*:\s*0\b/.test(sheetBody as string),
    ".nx-sheet must declare `min-width: 0` so it can shrink to fit " +
      "`.nx-sheet-panel .nx-sheet-slot` instead of being clipped by `.nx-app { overflow: hidden }`",
  );
});

// ======================================================= the header row's width
//
// SECOND MEASURED ESCAPE (2026-09-10, probe at 390x844). Every chip in the
// header truncated at once: `CAM ...`, `MOUNT...`, `S...`, `D` for an admin,
// and `C...`, `MO...`, `S.`, `V...` for a viewer. The cause was that the row
// had no width policy at all - `.nx-header-chips > * { flex-shrink: 1 }` plus
// `.nx-pill-text { text-overflow: ellipsis }` in next.css, which together mean
// "when short of room, take a little off everything", and a little off a
// four-character readout is all of it.
//
// The fix has two halves, and BOTH have to hold or the row silently goes back
// to shaving:
//
//   * each chip is whole or absent - `flex-shrink: 0` + `white-space: nowrap`,
//     and nothing ellipsises;
//   * the ROW sheds whole payloads in a fixed order (the mount's state word,
//     the wordmark's tracking, the flows count, the flows pill, the row's own
//     air, and below 360 px the role chip) rather than the chips shrinking.
//
// ...and the whole thing is only safe because the row still clips instead of
// widening the app: `.nx-header-chips` keeps `min-width: 0`, and `min-width: 0`
// goes on the wordmark and NOWHERE else, so nothing in this row can set a
// min-content floor. (Memory: one un-shrinkable header row set a 382 px
// app-wide floor and scrolled a 375 px phone sideways.)
//
// Measured after the fix, `#/session/now`, `CAM -10°`, SIM backend:
//   admin  390: flows 37, CAM 74, MOUNT 56, SIM 32, night 28 - 24 px spare
//   admin  320: CAM 72, MOUNT 54, SIM 30, night 26 - flows dropped, 7 px spare
//   viewer 390: CAM 74, MOUNT 56, SIM 32, VIEW 54, night 28 - 7 px spare
//   viewer 320: as admin 320 - flows and VIEW dropped
//   documentElement.scrollWidth == clientWidth at both widths and both roles.

/** The declaration block of the first rule whose selector list contains
 *  `selector` verbatim (whitespace-normalised), in `source`. Unlike
 *  `ruleBodyFor` this takes a WHOLE selector, so it can address
 *  `.nx-header-chips > *` and `.nx-night-toggle::after`, neither of which is a
 *  bare class. Comments are stripped first. */
function bodyForSelector(selector: string, source: string = css): string | null {
  const stripped = source.replace(/\/\*[\s\S]*?\*\//g, "");
  const re = /([^{}]+)\{([^}]*)\}/g;
  const want = selector.replace(/\s+/g, " ").trim();
  let m: RegExpExecArray | null;
  while ((m = re.exec(stripped)) !== null) {
    const selectors = m[1].split(",").map((x) => x.replace(/\s+/g, " ").trim());
    if (selectors.includes(want)) return m[2];
  }
  return null;
}

/** Does a declaration block declare `prop: value`? `value` is spliced into a
 *  RegExp, so a caller matching a literal dot escapes it. */
function declares(body: string | null, prop: string, value: string): boolean {
  if (body == null) return false;
  return new RegExp(`(^|[;{\\s])${prop}\\s*:\\s*${value}\\s*(;|$)`).test(body);
}

/** True when `selector` sits inside an `@media (max-width: <= maxPx)` block in
 *  `source` and declares `display: none` there. Written by walking braces from
 *  each media header to its match, because the flat rule regex above cannot
 *  see which rules are nested inside which block. */
function droppedUnder(selector: string, maxPx: number, source: string = css): boolean {
  const stripped = source.replace(/\/\*[\s\S]*?\*\//g, "");
  const want = selector.replace(/\s+/g, " ").trim();
  const media = /@media[^{]*\(\s*max-width\s*:\s*(\d+)px\s*\)[^{]*\{/g;
  let m: RegExpExecArray | null;
  while ((m = media.exec(stripped)) !== null) {
    if (Number(m[1]) > maxPx) continue;
    let depth = 1;
    let i = m.index + m[0].length;
    const start = i;
    while (i < stripped.length && depth > 0) {
      if (stripped[i] === "{") depth++;
      else if (stripped[i] === "}") depth--;
      i++;
    }
    const inner = stripped.slice(start, i - 1);
    const re = /([^{}]+)\{([^}]*)\}/g;
    let r: RegExpExecArray | null;
    while ((r = re.exec(inner)) !== null) {
      const sels = r[1].split(",").map((x) => x.replace(/\s+/g, " ").trim());
      if (sels.includes(want) && /\bdisplay\s*:\s*none\b/.test(r[2])) return true;
    }
  }
  return false;
}

test("a header chip is whole or absent - it never shrinks and never wraps", () => {
  const body = bodyForSelector(".nx-header-chips > *");
  assert(body != null, "no `.nx-header-chips > *` rule in shell.css");
  assert(
    declares(body, "flex-shrink", "0"),
    "`.nx-header-chips > *` must declare `flex-shrink: 0` - with `1` the row " +
      "takes a little off every chip at once, which is how CAM/MOUNT/SIM/NIGHT " +
      "all ellipsised at 390px",
  );
  assert(
    declares(body, "white-space", "nowrap"),
    "`.nx-header-chips > *` must declare `white-space: nowrap`",
  );
});

test("nothing in the header ellipsises", () => {
  // next.css gives every `.nx-pill-text` `overflow: hidden; text-overflow:
  // ellipsis`. That is right for a pill in a card and wrong here: an
  // ellipsised readout still costs the row its full width and reports nothing.
  // The header overrides it so a dormant `text-overflow` cannot fire the
  // moment some future rule reintroduces shrinking.
  const body = bodyForSelector(".nx-header-chips .nx-pill-text");
  assert(body != null, "no `.nx-header-chips .nx-pill-text` override in shell.css");
  assert(declares(body, "text-overflow", "clip"), "the header must not ellipsise its chips");
  assert(declares(body, "overflow", "visible"), "the header's chip text must not be clipped");
});

test("min-width: 0 is on the wordmark and on nothing else in the row", () => {
  // The wordmark is the one item allowed to be clipped rather than dropped, so
  // it is the one item that gets `min-width: 0`. Putting it on the chips too
  // would let a chip shrink below its content again by a different route.
  assert(
    declares(bodyForSelector(".nx-header-wordmark"), "min-width", "0"),
    "`.nx-header-wordmark` must declare `min-width: 0`",
  );
  assert(
    !declares(bodyForSelector(".nx-header-chips > *"), "min-width", "0"),
    "`.nx-header-chips > *` must NOT declare `min-width: 0` - the chips are " +
      "whole or absent, and a shrinkable chip is the defect this rule fixes",
  );
  // ...and the CONTAINER still has it, which is what stops an un-shrinkable
  // row from setting an app-wide minimum width (the 382px floor, memory).
  assert(
    declares(bodyForSelector(".nx-header-chips"), "min-width", "0"),
    "`.nx-header-chips` must keep `min-width: 0` or the chips set the app's floor",
  );
});

test("the row clips its inline axis and leaves the block axis alone", () => {
  // `overflow: hidden` clipped the night control's 44px touch target down to
  // the row's 26px - a hit area boundary.css claims and has never had. `clip`
  // is the only overflow value that does not force the other axis to `auto`,
  // so it is what lets the inline axis keep clipping an over-wide row while
  // the block axis lets that target reach into the header's own padding.
  const body = bodyForSelector(".nx-header-chips");
  assert(body != null, "no `.nx-header-chips` rule in shell.css");
  assert(declares(body, "overflow-x", "clip"), "`.nx-header-chips` must clip its inline axis");
  assert(
    declares(body, "overflow-y", "visible"),
    "`.nx-header-chips` must leave its block axis visible, or the night " +
      "toggle's 44px touch target is clipped to the row's 26px",
  );
  assert(
    !declares(body, "overflow", "hidden"),
    "`overflow: hidden` on `.nx-header-chips` re-clips the night toggle's touch target",
  );
});

test("the night toggle's touch target is a real 44 x 44", () => {
  const body = bodyForSelector(".nx-night-toggle::after", boundaryCss);
  assert(body != null, "no `.nx-night-toggle::after` rule in boundary.css");
  assert(declares(body, "width", "44px"), "the night toggle's hit area must be 44px wide");
  assert(declares(body, "height", "44px"), "the night toggle's hit area must be 44px tall");
  // Anchored to the button's right edge, not centred on it: this is the last
  // chip in an end-justified row whose inline axis is clipped, so a centred
  // box would lose its outer 8px and the target would be 36px wide again.
  assert(declares(body, "right", "0"), "the hit area must grow leftwards from the button's right edge");
});

test("the row sheds whole payloads, in the documented order", () => {
  const bare = css.replace(/\/\*[\s\S]*?\*\//g, "");
  // 1. the mount's state word at phone (`MOUNT TRACK` -> `MOUNT`)
  assert(
    /\.nx-app\[data-bp="phone"\][^{]*\.nx-mount-state\s*\{[^}]*display\s*:\s*none/.test(bare),
    'the mount chip\'s state word must be hidden at `data-bp="phone"`',
  );
  // 2. the wordmark's tracking, as the classic header already does it
  assert(
    declares(bodyForSelector('.nx-app[data-bp="phone"] .nx-header-wordmark'), "letter-spacing", "\\.14em"),
    "the wordmark's tracking must fall to .14em at phone " +
      "(App.tsx: `tracking-[0.14em] sm:tracking-[0.3em]`)",
  );
  // 3. the flows COUNT, when the row also carries a role chip
  assert(
    declares(
      bodyForSelector(
        '.nx-app[data-bp="phone"] .nx-header-chips[data-dense="true"] .nx-flows-pill .nx-pill-text',
      ),
      "display",
      "none",
    ),
    "a dense phone row must drop the flows pill's count before it touches a readout",
  );
  // 4. the flows pill itself - dense below 400px, everyone below 360px
  assert(
    droppedUnder('.nx-app[data-bp="phone"] .nx-header-chips[data-dense="true"] .nx-flows-pill', 399),
    "a dense phone row must drop the whole flows pill below 400px",
  );
  assert(
    droppedUnder('.nx-app[data-bp="phone"] .nx-flows-pill', 359),
    "every phone row must drop the flows pill below 360px",
  );
  // 6. and the role chip, last, below 360px - measured, the alternative was a
  //    camera chip clipped 49px in and reading "10°" without its minus sign.
  assert(
    droppedUnder('.nx-app[data-bp="phone"] .nx-header-chips[data-dense="true"] .nx-role-chip', 359),
    "below 360px a dense row must drop the role chip rather than slice the camera chip",
  );
});

// ================================================ dial/seg/bar flex-shrink
//
// THIRD MEASURED ESCAPE (2026-09-10, probe at 820x1180 on
// #/rig/devices/mount). Every `Dial` on the mount sheet - SLEW RATE, RA STEP,
// DEC STEP, and the mount-dial itself - measured a bounding box of 328 x 2 px,
// with its own children (`.nx-dial-head` 23px + `.nx-dial-track` 56px) adding
// to far more than that. Computed style was `display: block; height: 2px;
// overflow: hidden`, and the parent `.nx-sheet-body` is `display: flex;
// flex-direction: column`.
//
// The cause: `.nx-dial` sets `overflow: hidden`, and a flex item's
// `min-height: auto` computes to 0 whenever its own overflow is not visible
// (the CSS Flexbox "automatic minimum size" carve-out). With the column's
// default `flex-shrink: 1` and not enough room in the sheet body for every
// control, the dial shrank straight past its content down to its 2px border.
// Playwright still counted the element as "visible" at 2px tall, so this
// shipped on every route and every width without failing the probe (see the
// `--only 16 x 16` guard in tools/ui_probe/probe.py).
//
// The same shape recurs wherever a primitive both (a) sets `overflow: hidden`
// on its own root and (b) is dropped bare - no wrapper of its own - directly
// into a `display: flex; flex-direction: column` body: `.nx-seg` (the
// `<Segmented>` radiogroup root, used bare in safety.tsx/driver.tsx/etc.) and
// `.nx-bar` (the non-segmented `<Bar>`, used bare in guider.tsx's local
// `Stack`, CoolerRow, CaptureStage). Both get the same `flex-shrink: 0` fix.
//
// Audited and left alone (not the same shape): `.nx-pill-text`,
// `.nx-btn-label`, `.nx-readout-sub`, `.nx-row-sub`, `.nx-sheet-sub` /
// `.nx-sheet-live` are all text-truncation utilities nested inside a
// ROW-direction (or otherwise unconstrained) parent, where `overflow: hidden`
// is the intended single-line-ellipsis behaviour, not an accidental
// height-collapse; `.nx-dial-track` sets `overflow: hidden` but its parent
// `.nx-dial` is a plain block element, not a flex container, so the flex
// auto-min-size rule never applies to it; `.nx-bar-seg` is a flex item of a
// ROW (`.nx-bar-segs`), so its own `overflow: hidden` only zeroes its
// auto-min-WIDTH, which is exactly the proportional-width behaviour its
// explicit `flexGrow`/`flexBasis: 0` styling already wants.

test("a bare Dial cannot be shrunk to its border by a height-constrained flex column", () => {
  const body = ruleBodyFor("nx-dial", nextCss);
  assert(body != null, "no .nx-dial rule in next.css");
  assert(
    declares(body, "flex-shrink", "0"),
    ".nx-dial must declare `flex-shrink: 0` - its `overflow: hidden` zeroes " +
      "the flex item's automatic min-height, so inside `.nx-sheet-body` " +
      "(display: flex; flex-direction: column) the dial shrank to its 2px " +
      "border and rendered invisible while still measuring as visible",
  );
});

test("a bare Segmented control cannot be shrunk the same way as the dial", () => {
  const body = ruleBodyFor("nx-seg", nextCss);
  assert(body != null, "no .nx-seg rule in next.css");
  assert(
    declares(body, "flex-shrink", "0"),
    ".nx-seg must declare `flex-shrink: 0` - same defect class as .nx-dial: " +
      "it is dropped bare into flex-column sheet bodies and its `overflow: " +
      "hidden` (there only to round the option buttons) zeroes its automatic " +
      "min-height the same way",
  );
});

test("a bare (non-segmented) Bar cannot be shrunk the same way as the dial", () => {
  const body = ruleBodyFor("nx-bar", nextCss);
  assert(body != null, "no .nx-bar rule in next.css");
  assert(
    declares(body, "flex-shrink", "0"),
    ".nx-bar must declare `flex-shrink: 0` - same defect class as .nx-dial: " +
      "the non-segmented <Bar> renders this div bare into flex-column " +
      "stacks, and its `overflow: hidden` zeroes its automatic min-height",
  );
});

// ---------------------------------------------------------------- report
const total = passed + failed;
console.log(`shellCss.test: ${passed}/${total} passed`);
if (failures.length) console.error(failures.join("\n"));

export const result = { passed, failed, total };
