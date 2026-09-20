// r7Css.test.ts - the wave-R7 rule that every area owns its own stylesheet.
//
//   Run directly:  npx tsx src/next/__tests__/r7Css.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc --noEmit`.
//
// WHY THIS TEST EXISTS. Wave R7 rebuilt 77 legacy panels across 22 areas with
// about 20 agents working in parallel, and `next.css` is a single shared file
// that 20 tasks cannot own. The rule the wave adopted instead (wave-r7.md
// section 1) is: every area writes its `nx-*` classes into its OWN
// `<area>/<area>.css`, which some always-loaded module of that area imports.
//
// That rule has four failure modes, none of which any DOM test can see,
// because jsdom neither loads stylesheets nor computes layout:
//
//   1. the css file exists and nobody imports it - every class in it is inert
//      and the area renders unstyled (the fix is one import line, and the
//      symptom is a screenshot three weeks later);
//   2. a component emits a class NO stylesheet defines - a promise with no
//      rule behind it, invisible until someone looks at the pixels;
//   3. a component emits ANOTHER area's class without reaching that area's
//      stylesheet - the class is defined, the component still renders
//      unstyled, and it renders CORRECTLY as soon as you visit the other area
//      first, which is the worst kind of intermittent;
//   4. an area css redefines a class `next.css` owns - now the shared
//      primitive means two different things depending on which chunk loaded
//      last, and the loser is whichever area the operator visited first.
//
// So this file reads the source: every `.css` under `hubs/**`, every `nx-*`
// class every module emits, and the whole `next/**` import graph. It is the
// same shape of guard as `shellCss.test.ts` (which owns the SHELL sheets:
// `shell.css` and `boundary.css` are app-level, imported by `NextApp.tsx`, and
// are treated here only as a source of already-defined class names).
//
// SABOTAGE CHECKS (each named beside its test below):
//   * delete `import "./inspect.css"` from `rig/inspect/index.ts`
//     -> "every area stylesheet is imported inside its own area" goes red.
//   * rename any rule in an area css (e.g. `.nx-tn-cal` -> `.nx-tn-cal-x`)
//     -> "every class an area emits has a rule" goes red with the class name.
//   * delete `import "./tonight.css"` from `tonight/CalibrationMatrixCard.tsx`
//     -> "a module that emits an area's class reaches that area's stylesheet"
//     goes red for the two out-of-area mount sites (this is the real defect
//     that fix closed - see the FINDING note on that test).
//   * copy a `.nx-card` rule into any area css
//     -> "no area stylesheet redefines a class next.css owns" goes red.

/* eslint-disable @typescript-eslint/no-explicit-any */

const { readFileSync, readdirSync, statSync, existsSync } = await import("node:fs");
const { fileURLToPath } = await import("node:url");

/** `ui/src/next/`, with a trailing separator, in native path form. */
const NEXT = fileURLToPath(new URL("../", import.meta.url));
const SEP = NEXT.includes("\\") ? "\\" : "/";
const join = (...parts: string[]): string => parts.join(SEP).replace(/[\\/]+/g, SEP);
const rel = (p: string): string => p.slice(NEXT.length).replace(/\\/g, "/");

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

// ------------------------------------------------------------- the corpus

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) walk(p, out);
    else out.push(p);
  }
  return out;
}

const ALL = walk(NEXT.slice(0, -1));
const isTest = (p: string): boolean =>
  p.includes(`${SEP}__tests__${SEP}`) || /\.test\.tsx?$/.test(p);

/** Every module the app can load: `.ts`/`.tsx` under `next/`, tests excluded.
 *  Tests are excluded on purpose - a test file may name a class in an
 *  assertion string without ever rendering it. */
const MODULES = ALL.filter((p) => /\.tsx?$/.test(p) && !p.endsWith(".d.ts") && !isTest(p));

/** Every stylesheet, split into the three app-level sheets `NextApp.tsx`
 *  imports (always loaded, so any module may use their classes) and the area
 *  sheets under `hubs/**`, which are the subject of this file. */
const CSS_FILES = ALL.filter((p) => p.endsWith(".css"));
const SHARED_CSS = [join(NEXT, "next.css"), join(NEXT, "shell", "shell.css"),
  join(NEXT, "shell", "boundary.css")];
const AREA_CSS = CSS_FILES.filter((p) => p.includes(`${SEP}hubs${SEP}`));

const src = new Map<string, string>();
const read = (p: string): string => {
  let s = src.get(p);
  if (s === undefined) { s = readFileSync(p, "utf8"); src.set(p, s); }
  return s;
};

// ------------------------------------------------------------- css parsing

const stripCssComments = (s: string): string => s.replace(/\/\*[\s\S]*?\*\//g, "");

/** Every `nx-*` class MENTIONED anywhere in a selector - including scoped
 *  overrides like `.nx-planauto-row > .nx-field`. This is the set that answers
 *  "does this class have a rule at all". The lookbehind keeps `var(--nx-filter-L)`
 *  out: a custom property is not a class. */
function cssMentions(text: string): Set<string> {
  const out = new Set<string>();
  for (const m of stripCssComments(text).matchAll(/(?<![-\w])\.(nx-[a-zA-Z0-9_-]+)/g)) out.add(m[1]);
  return out;
}

/** Every `nx-*` class this sheet DEFINES - the classes appearing in the
 *  LEFTMOST compound of a selector, which is what makes a rule global. A
 *  scoped override (`.nx-planauto-row > .nx-field`) does not define
 *  `.nx-field`; it narrows it, which is allowed and is how an area adjusts a
 *  shared primitive inside its own subtree. */
function cssKeys(text: string): Set<string> {
  const out = new Set<string>();
  const flat = stripCssComments(text);
  for (const m of flat.matchAll(/([^{}]+)\{/g)) {
    const selectors = m[1];
    if (selectors.trim().startsWith("@")) continue;
    for (const one of selectors.split(",")) {
      const first = one.trim().split(/[\s>+~]/)[0];
      for (const c of first.matchAll(/(?<![-\w])\.(nx-[a-zA-Z0-9_-]+)/g)) out.add(c[1]);
    }
  }
  return out;
}

const definedIn = new Map<string, Set<string>>();  // class -> css files defining it
const mentionedIn = new Map<string, Set<string>>();  // class -> css files with any rule
for (const p of CSS_FILES) {
  for (const c of cssKeys(read(p))) {
    if (!definedIn.has(c)) definedIn.set(c, new Set());
    (definedIn.get(c) as Set<string>).add(p);
  }
  for (const c of cssMentions(read(p))) {
    if (!mentionedIn.has(c)) mentionedIn.set(c, new Set());
    (mentionedIn.get(c) as Set<string>).add(p);
  }
}
const SHARED_CLASSES = new Set<string>();
for (const p of SHARED_CSS) for (const c of cssMentions(read(p))) SHARED_CLASSES.add(c);

// ------------------------------------------------------- what a module emits

/** Comments out of a TS/TSX source, LINE comments first. Order matters and
 *  cost an hour: `ProfilesEditor.tsx:5` is a line comment containing the path
 *  `ui/src/next/**`, whose `/*` opens a block comment for any regex that
 *  strips blocks first - which then swallows the file down to the next `*\/`,
 *  including every import statement. */
const stripTsComments = (s: string): string =>
  s.replace(/(^|[^:])\/\/[^\n]*/g, "$1").replace(/\/\*[\s\S]*?\*\//g, "");

/** An `nx-*` token used as an ELEMENT ID, not a class: `id="nx-main"`,
 *  `getElementById("nx-popover-root")`, `aria-describedby={`nx-port-...`}`.
 *  Four of these exist in the tree and none of them wants a rule. Decided by
 *  which marker sits closest to the token, so `<main className="nx-body"
 *  id="nx-main">` classifies each of its two tokens correctly. */
function isIdContext(text: string, at: number): boolean {
  const win = text.slice(Math.max(0, at - 60), at);
  const idAt = Math.max(win.lastIndexOf("id="), win.lastIndexOf("getElementById"),
    win.lastIndexOf("aria-describedby"), win.lastIndexOf("aria-controls"),
    win.lastIndexOf("aria-labelledby"), win.lastIndexOf("ROOT_ID"));
  if (idAt < 0) return false;
  return idAt > win.lastIndexOf("className");
}

interface Emission { exact: Set<string>; prefix: Set<string> }

/** Class names a module emits. A token followed by `${` (or ending in `-`) is
 *  a PREFIX - `` `nx-tn-cal-${verdict}` `` - and is satisfied by any defined
 *  class starting with it. */
function emissionsOf(p: string): Emission {
  const text = stripTsComments(read(p));
  const exact = new Set<string>();
  const prefix = new Set<string>();
  for (const m of text.matchAll(/(?<![-\w])nx-[a-zA-Z0-9_-]*/g)) {
    const tok = m[0];
    const at = m.index ?? 0;
    if (isIdContext(text, at)) continue;
    if (text.slice(at + tok.length, at + tok.length + 2) === "${" || tok.endsWith("-")) prefix.add(tok);
    else exact.add(tok);
  }
  return { exact, prefix };
}

const EMITS = new Map<string, Emission>(MODULES.map((p) => [p, emissionsOf(p)]));

// ------------------------------------------------------------ import graph

/** Resolve a relative specifier the way the bundler does. Bare specifiers
 *  (react, ../../store) that land outside `next/` are dropped: this graph only
 *  has to answer "which stylesheet does loading this module pull in", and
 *  every stylesheet is inside `next/`. */
function resolveSpec(from: string, spec: string): string | null {
  if (!spec.startsWith(".")) return null;
  const parts = from.split(SEP).slice(0, -1).concat(spec.split(/[\\/]/));
  const stack: string[] = [];
  for (const part of parts) {
    if (part === "." || part === "") continue;
    if (part === "..") stack.pop();
    else stack.push(part);
  }
  const base = stack.join(SEP);
  if (base.endsWith(".css")) return existsSync(base) ? base : null;
  for (const cand of [`${base}.ts`, `${base}.tsx`, join(base, "index.ts"), join(base, "index.tsx"), base]) {
    if (existsSync(cand) && statSync(cand).isFile()) return cand;
  }
  return null;
}

// Issue #39: the previous version of this function matched double-quoted
// specifiers only (`/from\s+"([^"]+)"/`), so `import './x.css'` and
// `from '../horizon'` contributed no edges at all - invisible to test 3
// ("every area stylesheet is imported by a module inside its own area") and
// test 5 ("a module that emits an area's class reaches that area's
// stylesheet"), silently, in both directions. `QUOTED` now accepts a
// double-quoted, single-quoted, or substitution-free template-literal
// specifier (a `${` inside the backtick form falls through - that is a
// computed specifier, not a static one this scanner can resolve).
const QUOTED = `"([^"]+)"|'([^']+)'|\`((?:(?!\\\$\\{)[^\`])+)\``;
const pick = (m: RegExpMatchArray): string => (m[1] ?? m[2] ?? m[3]) as string;

function specsOf(text: string): string[] {
  const out: string[] = [];
  for (const m of text.matchAll(new RegExp(`from\\s+(?:${QUOTED})`, "g"))) out.push(pick(m));
  for (const m of text.matchAll(new RegExp(`import\\s+(?:${QUOTED})`, "g"))) out.push(pick(m));
  for (const m of text.matchAll(new RegExp(`import\\(\\s*(?:${QUOTED})\\s*\\)`, "g"))) out.push(pick(m));
  return out;
}

const IN_NEXT = new Set(MODULES);
const edges = new Map<string, string[]>();
const cssEdges = new Map<string, string[]>();
for (const p of MODULES) {
  const text = stripTsComments(read(p));
  const mods: string[] = [];
  const sheets: string[] = [];
  for (const spec of specsOf(text)) {
    const r = resolveSpec(p, spec);
    if (r == null) continue;
    if (r.endsWith(".css")) sheets.push(r);
    else if (IN_NEXT.has(r)) mods.push(r);  // legacy modules outside next/ carry no area sheet
  }
  edges.set(p, mods);
  cssEdges.set(p, sheets);
}

/** Every stylesheet loading this module pulls in, transitively (its own css
 *  imports plus everything the modules it imports pull in). Computed as a
 *  fixpoint rather than a recursion so import cycles settle instead of
 *  memoising a half-answer. */
const reach = new Map<string, Set<string>>(MODULES.map((p) => [p, new Set(cssEdges.get(p) ?? [])]));
for (let spin = true; spin;) {
  spin = false;
  for (const p of MODULES) {
    const mine = reach.get(p) as Set<string>;
    for (const m of edges.get(p) ?? []) {
      for (const s of reach.get(m) ?? []) {
        if (!mine.has(s)) { mine.add(s); spin = true; }
      }
    }
  }
}

/** Who imports this module (the reverse graph), used to decide whether every
 *  way IN to a component already carries its stylesheet. */
const importedBy = new Map<string, string[]>(MODULES.map((p) => [p, []]));
for (const p of MODULES) {
  for (const m of edges.get(p) ?? []) (importedBy.get(m) as string[]).push(p);
}

/** The modules that are safe to render a class owned by stylesheet `sheet`:
 *  either they pull the sheet in themselves, or every module that imports them
 *  is itself safe - which is what makes a leaf component fine when the screen
 *  above it carries the import. A module with NO importer is an entry point
 *  and has to carry the sheet itself. */
const coveredBy = new Map<string, Set<string>>();
function coveredFor(sheet: string): Set<string> {
  const hit = coveredBy.get(sheet);
  if (hit) return hit;
  const covered = new Set(MODULES.filter((p) => (reach.get(p) as Set<string>).has(sheet)));
  for (let spin = true; spin;) {
    spin = false;
    for (const p of MODULES) {
      if (covered.has(p)) continue;
      const ins = importedBy.get(p) as string[];
      if (ins.length > 0 && ins.every((i) => covered.has(i))) { covered.add(p); spin = true; }
    }
  }
  coveredBy.set(sheet, covered);
  return covered;
}

// ------------------------------------------------------------- allow-lists

/** Classes a module emits that NO stylesheet defines, with the reason each is
 *  allowed to stay that way. Found by this file, reported by T-R7-21.
 *
 *  EMPTY as of the style-hook audit below (`nx-tn-sheet` was this file's one
 *  entry; TonightSheet.tsx:168 dropped the class - see that audit's note).
 *  Left as a live mechanism rather than deleted: the next genuinely-inert
 *  class an area emits belongs here, not silently ignored.
 *
 *  STYLE-HOOK AUDIT (wave-1 classes outside the R7 areas this file scans -
 *  `AREA_CSS` only enumerates `hubs/**` directories that HAVE a css file, so
 *  `next/ui/**` primitives and the still-css-less `hubs/sky/**`,
 *  `hubs/rig/capture/` (top level) and `hubs/settings/sheets/` areas were
 *  never reached by test 4 at all - not exempted, just outside the scan).
 *  Resolved, not merely catalogued:
 *
 *  - `nx-tn-sheet` (TonightSheet.tsx, was :168) - DEAD: removed. `.nx-sheet`
 *    (the box) and `.nx-tn-body` (the body) already carry every pixel; no
 *    other sheet in the tree tags its own `Sheet` root this way, so this was
 *    a one-off with nothing depending on it.
 *  - `nx-plan-sep` (was `session/plan/plan.css:145`, a rule with NO emitter) -
 *    DEAD: rule removed. `session/plan/PlanTargetCard.tsx` already draws the
 *    identical hairline with the shared `Divider` primitive (`.nx-divider`,
 *    `next.css`) - `nx-plan-sep` was that same rule re-invented and then
 *    superseded, never cleaned up.
 *  - `nx-chip-text` (`ui/Chip.tsx`), `nx-dial-hint` (`ui/Dial.tsx`),
 *    `nx-iconbtn-glyph` (`ui/IconButton48.tsx`), `nx-glyphtile-glyph`
 *    (`ui/DeviceGlyphTile.tsx`) - KEEP, no rule needed. Each is a plain
 *    sub-part span inside a primitive whose OWN rule already sets every pixel
 *    that part renders with (`.nx-chip`'s font/color for the text,
 *    `.nx-dial-head`'s font/color for the hint, `.nx-iconbtn`/`.nx-glyphtile`'s
 *    flex centering for the glyph) - the same "give each part its own class"
 *    shape as that primitive's OTHER, styled sibling span
 *    (`.nx-chip-count`, `.nx-dial-name`, `.nx-iconbtn-label`,
 *    `.nx-glyphtile-led`). A rule here would only restate the parent's
 *    cascade or risk drifting from it.
 *  - `nx-cap-mode` (`rig/capture/CaptureScreen.tsx`) - STYLING WAS MISSING:
 *    fixed. The STILL/VIDEO toggle wraps each `Chip` in its own `flex: 1` div
 *    but `.nx-chip` alone sizes to its own label, so the two mode chips sat
 *    left-aligned in a half-width slot with a dead gap - not a two-way
 *    toggle. New `rig/capture/capture.css` (`flex: 1; justify-content:
 *    center;`), imported by `CaptureScreen.tsx`.
 *  - `nx-locked-note` (`settings/sheets/LogExportSheet.tsx`) - STYLING WAS
 *    MISSING: fixed. A plain status sentence, not the word-for-word
 *    `lockReason()` copy `next.css`'s `.nx-locknote`/`LockNote` render
 *    verbatim with their own lock glyph, so it earns its own class rather
 *    than borrowing that primitive's - but it is the same dim, small
 *    sans-serif register. New `settings/sheets/sheets.css`, imported by
 *    `LogExportSheet.tsx`.
 *  - `nx-sky-browse`, `nx-sky-cta`, `nx-sky-tool`, `nx-sky-lock`,
 *    `nx-sky-patch`, `nx-sky-secondary`, `nx-sky-framing`, `nx-sky-frametool`,
 *    `nx-sky-layer-switch` (`hubs/sky/cards/*`, `hubs/sky/frame/*`,
 *    `SkyHub.tsx`) - KEEP, no rule needed. Every card and control in this
 *    subtree is 100% inline-`style`-driven (colours, fonts, sizes all come
 *    from per-instance `style={{...}}`, because much of it - `skin.bg`,
 *    `patch?.color` - is computed at render time from props a static class
 *    could not express) and already carries a `data-testid` for the same
 *    identity purpose. These `nx-sky-*` classes are a second, class-shaped
 *    hook on top of that - harmless, consistent across all nine sites, and
 *    not something a CSS rule was ever going to style. */
const UNSTYLED_OK: Record<string, string> = {};

// Two modules emit ANOTHER area's classes and are fine because they import
// that area's barrel, which carries its stylesheet: `session/sheets/archive.tsx`
// wraps the gallery frames grid in an `nx-frames-more` row (reaches frames.css
// through `../gallery/frames`), and `rig/sheets/videoLibrary.tsx` renders the
// recordings list's `nx-vid` rows (reaches video.css through `../capture/video`).
// They need no allow-list entry - test 5 below checks the import graph, not the
// directory, so a legitimate cross-area mount passes and a stylesheet-less one
// does not.

// =========================================================== 1. the corpus
// A scan that finds nothing passes every other test in this file, so the
// numbers are asserted first. They are floors, not fixtures: adding an area
// must never make this test go red, deleting the scan must.

test("the scan found the areas, the sheets and the classes", () => {
  assert(AREA_CSS.length >= 20, `only ${AREA_CSS.length} area stylesheets found under hubs/`);
  assert(MODULES.length >= 200, `only ${MODULES.length} modules found under next/`);
  assert(SHARED_CLASSES.size >= 100, `only ${SHARED_CLASSES.size} classes in the shared sheets`);
  let emitted = 0;
  for (const e of EMITS.values()) emitted += e.exact.size;
  assert(emitted >= 400, `only ${emitted} class emissions found - is the scan working?`);
  assert(definedIn.has("nx-tn-cal"), "tonight.css's .nx-tn-cal did not parse");
  const cardPath = join(NEXT, "hubs", "session", "flows", "tonight", "CalibrationMatrixCard.tsx");
  const card = EMITS.get(cardPath);
  assert(card != null && card.exact.has("nx-tn-cal"),
    "CalibrationMatrixCard's .nx-tn-cal emission did not parse");
  // Issue #39's corpus assertion: the edge count for a KNOWN real file, so a
  // scanner that silently starts finding nothing (a regex that stops
  // matching, a comment-strip that eats the whole file) fails loudly here
  // instead of quietly starving tests 3 and 5 of edges. Computed by hand
  // against the file as it stands: `react`, the legacy `CalibrationMatrix`
  // helper import, `store`, the `ui` barrel, `./tonightModel` and
  // `./tonight.css` - six specifiers, all double-quoted today. If this
  // file's imports change, this count changes with it.
  const cardSpecs = specsOf(read(cardPath));
  assert(cardSpecs.length === 6,
    `CalibrationMatrixCard.tsx: expected 6 import specifiers, found ${cardSpecs.length} - ` +
    "either its imports changed (update this number) or the scanner is missing some");
  assert(cardSpecs.includes("./tonight.css"),
    "CalibrationMatrixCard.tsx: the scanner did not find its own css import");
});

// =================================================== 1b. every quote style
// Issue #39: specsOf used to match double-quoted specifiers only
// (`/from\s+"([^"]+)"/`), so `import './x.css'` and `from '../horizon'`
// contributed NO edge at all - invisible to test 3 ("every area stylesheet is
// imported by a module inside its own area") and test 5 ("a module that
// emits an area's class reaches that area's stylesheet"), silently, in both
// directions. Pinned against a real stylesheet already in the corpus
// (tonight.css) with synthetic source text, so no new fixture file is needed
// and nothing under hubs/ has to change.
//
// Mutation: restore the double-quote-only pattern (reproduced inline below,
// unchanged from before this fix) and this test goes red on its own probes.

test("specsOf sees a single-quoted, double-quoted and template-literal specifier", () => {
  const areaDir = join(NEXT, "hubs", "session", "flows", "tonight");
  const sheet = join(areaDir, "tonight.css");
  assert(existsSync(sheet), "tonight.css moved or was renamed - this probe's target is stale");
  const fromModule = join(areaDir, "SomeOtherCard.tsx");  // need not exist: only used to resolve "./tonight.css"

  const probes = [
    `import "./tonight.css";`,
    `import './tonight.css';`,
    "import `./tonight.css`;",
  ];
  for (const src of probes) {
    const specs = specsOf(src);
    assert(specs.includes("./tonight.css"), `specsOf missed ${JSON.stringify(src)}`);
    const resolved = resolveSpec(fromModule, "./tonight.css");
    assert(resolved === sheet, `resolveSpec did not resolve ${JSON.stringify(src)} to tonight.css`);
  }

  // The bug this issue fixes, reproduced exactly (this is what specsOf was
  // before this task): a single-quoted or template-literal import of a
  // module's own area stylesheet was invisible, which is precisely how a
  // correctly-authored `import './area.css'` would have gone unseen by test 3.
  function doubleQuoteOnlySpecsOf(text: string): string[] {
    const out: string[] = [];
    for (const m of text.matchAll(/from\s+"([^"]+)"/g)) out.push(m[1]);
    for (const m of text.matchAll(/import\s+"([^"]+)"/g)) out.push(m[1]);
    for (const m of text.matchAll(/import\(\s*"([^"]+)"\s*\)/g)) out.push(m[1]);
    return out;
  }
  assert(doubleQuoteOnlySpecsOf(probes[1]).length === 0,
    "the pre-fix scanner unexpectedly saw a single-quoted specifier - this probe no longer pins the regression");
  assert(doubleQuoteOnlySpecsOf(probes[2]).length === 0,
    "the pre-fix scanner unexpectedly saw a template-literal specifier - this probe no longer pins the regression");

  // Negative control: a template literal WITH a substitution is a computed
  // specifier, not a static one - it must not be reported as an edge.
  assert(specsOf("import `./${name}.css`;").length === 0,
    "specsOf treated a substituted template literal as if it were static");
});

// ====================================================== 2. one sheet per area
// The wave's naming rule, which is also how every other test in this file
// finds an area at all: the stylesheet is named after the directory that owns
// it. A stray `styles.css` would be owned by nobody.

test("every stylesheet under hubs/ is named after its own directory", () => {
  for (const p of AREA_CSS) {
    const parts = p.split(SEP);
    const file = parts[parts.length - 1];
    const dir = parts[parts.length - 2];
    assert(file === `${dir}.css`,
      `${rel(p)} is not named after its directory - the wave's rule is <area>/<area>.css`);
  }
});

// =============================================== 3. somebody imports the sheet
// The relaxed form of the rule (WAVE2-RULINGS: "imported by at least one
// always-loaded module of the area", not "by exactly one root"): canvas.css,
// inspector.css, create.css, files.css and system.css each have 2-4 import
// sites by design, because their areas have several entry points and each has
// to carry the stylesheet in.
//
// Sabotage: delete `import "./inspect.css"` from `rig/inspect/index.ts` and
// this goes red naming inspect.css.

test("every area stylesheet is imported by a module inside its own area", () => {
  for (const p of AREA_CSS) {
    const dir = p.split(SEP).slice(0, -1).join(SEP);
    const importers = MODULES.filter((m) =>
      m.startsWith(dir + SEP) && (cssEdges.get(m) ?? []).includes(p));
    assert(importers.length > 0,
      `${rel(p)} is imported by no module in its own area - every rule in it is inert`);
  }
});

// ================================================ 4. every class has a rule
// The brief's rule, per area: every `nx-*` class an area's own modules emit is
// defined by that area's stylesheet or by one of the three app-level sheets.
//
// Sabotage: rename `.nx-tn-cal` in tonight.css and this goes red with
// `nx-tn-cal emitted by hubs/session/flows/tonight/CalibrationMatrixCard.tsx`.

test("every class an area emits has a rule in its own sheet or a shared one", () => {
  const problems: string[] = [];
  for (const p of AREA_CSS) {
    const dir = p.split(SEP).slice(0, -1).join(SEP);
    const nested = AREA_CSS.filter((o) => o !== p && o.startsWith(dir + SEP))
      .map((o) => o.split(SEP).slice(0, -1).join(SEP));
    const own = cssMentions(read(p));
    for (const m of MODULES) {
      if (!m.startsWith(dir + SEP)) continue;
      if (nested.some((n) => m.startsWith(n + SEP))) continue;  // a nested area owns itself
      const e = EMITS.get(m) as Emission;
      for (const c of e.exact) {
        if (own.has(c) || SHARED_CLASSES.has(c) || UNSTYLED_OK[c]) continue;
        problems.push(`${c} emitted by ${rel(m)} has no rule in ${rel(p)} or a shared sheet`);
      }
      for (const pre of e.prefix) {
        const hit = [...own, ...SHARED_CLASSES].some((c) => c.startsWith(pre));
        if (!hit) problems.push(`${pre}* emitted by ${rel(m)} matches no rule in ${rel(p)}`);
      }
    }
  }
  assert(problems.length === 0, problems.join("; "));
});

// ============================================= 5. and the rule is LOADED there
// FINDING (fixed in this task, one line): `tonight/CalibrationMatrixCard.tsx`
// emits seven `nx-tn-cal*` classes and is mounted from OUTSIDE the tonight
// area by `session/flows/FlowsCanvasHost.tsx` (the canvas's inspector column)
// and by `flows/inspector/sheets.tsx` (the node sheet). Neither path loads
// `tonight.css`: it was imported only by `TonightSheet.tsx`, which those two
// screens never mount. So the calibration matrix rendered UNSTYLED until the
// operator happened to open the TONIGHT sheet first, which is exactly failure
// mode 3 in this file's header. The fix is `import "./tonight.css"` in the
// card itself - the same shape the wave already blessed for canvas.css and
// inspector.css, which carry their sheet on more than one entry point.
//
// Sabotage: remove that import line and this goes red for both mount sites.

test("a module that emits an area's class reaches that area's stylesheet", () => {
  const problems: string[] = [];
  for (const m of MODULES) {
    const e = EMITS.get(m) as Emission;
    for (const c of e.exact) {
      if (SHARED_CLASSES.has(c)) continue;
      const owners = mentionedIn.get(c);
      if (!owners) continue;  // undefined classes are test 4's subject, not this one
      if ([...owners].some((o) => coveredFor(o).has(m))) continue;
      problems.push(`${rel(m)} emits ${c} (defined in ` +
        `${[...owners].map(rel).join(", ")}) but neither it nor every module ` +
        "that imports it loads that stylesheet");
    }
  }
  assert(problems.length === 0, problems.join("; "));
});

// ============================================ 6. nobody redefines a shared class
// The one-owner rule for `next.css` (wave-r7.md section 1): 20 tasks, one
// shared sheet, one task allowed to touch it. An area css that redefines
// `.nx-card` changes every card in the product depending on which chunk loaded
// last. A SCOPED override (`.nx-planauto-row > .nx-field`, automation.css:56)
// is not a redefinition and is allowed - it narrows a shared primitive inside
// one area's subtree, which is the supported way to adjust it.
//
// Sabotage: add `.nx-card { padding: 0 }` to any area css and this goes red.

test("no area stylesheet redefines a class next.css owns", () => {
  const nextKeys = cssKeys(read(join(NEXT, "next.css")));
  const problems: string[] = [];
  for (const p of AREA_CSS) {
    for (const c of cssKeys(read(p))) {
      if (nextKeys.has(c)) problems.push(`${rel(p)} redefines .${c}, which next.css owns`);
    }
  }
  assert(problems.length === 0, problems.join("; "));
});

// ================================================== 7. one class, one stylesheet
// The same hazard between two AREAS. Class names are global; two areas that
// both define `.nx-row` are one lazy-chunk order away from restyling each
// other. Today no class is defined by two sheets at all, and this holds that.

test("every nx class is defined by exactly one stylesheet", () => {
  const problems: string[] = [];
  for (const [c, owners] of definedIn) {
    if (owners.size > 1) problems.push(`.${c} is defined by ${[...owners].map(rel).join(" and ")}`);
  }
  assert(problems.length === 0, problems.join("; "));
});

// ---------------------------------------------------------------- report
const total = passed + failed;
console.log(`r7Css.test: ${passed}/${total} passed`);
for (const f of failures) console.error(f);
if (failed > 0) process.exitCode = 1;

export { passed, failed, total };
