// shellCss.test.ts - a source-reading guard on shell.css for the sheet-panel
// containment rule.
//
//   Run directly:  npx tsx src/next/__tests__/shellCss.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHY THIS TEST EXISTS (jsdom has no layout, so this cannot be a DOM test):
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

// ---------------------------------------------------------------- report
const total = passed + failed;
console.log(`shellCss.test: ${passed}/${total} passed`);
if (failures.length) console.error(failures.join("\n"));

export const result = { passed, failed, total };
