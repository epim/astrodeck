// F-C1 guard — every bare component CSS class referenced in the app MUST have a
// definition in index.css. Tailwind v4 silently no-ops an undefined custom class
// (it only generates utilities it recognizes), so a class like `alert-pulse` or
// `more-sheet-in` can be referenced for a whole batch with NO style and no build
// error — exactly the Batch-3 regression this test exists to catch.
//
// Run directly:  npx tsx src/__tests__/cssClasses.test.ts
//
// Strategy: a curated allowlist of the project's OWN component classes (not
// Tailwind utilities — those are generated, not authored). Each must appear as a
// selector token in index.css. The list is deliberately explicit so a newly
// referenced custom class without a CSS landing fails loudly here.

// This test runs via `tsx` (Node), but the project has no @types/node installed
// and `tsc -b` type-checks every file under src/. To stay dependency-free we reach
// node's fs through a dynamic import behind a locally-typed shape — `tsx` provides
// the real module at runtime; `tsc` only sees our minimal interface, so no
// @types/node and no module-augmentation (TS2664) is needed.
interface NodeFsLike {
  readFileSync(path: string, encoding: string): string;
}
const nodeImport = (s: string): Promise<unknown> =>
  (Function("m", "return import(m)") as (m: string) => Promise<unknown>)(s);
const fs = (await nodeImport("node:fs")) as NodeFsLike;

// Resolve index.css relative to this file via import.meta.url (no node:path/url).
const CSS_URL = new URL("../index.css", import.meta.url);
const CSS_PATH = decodeURIComponent(CSS_URL.pathname).replace(/^\/([A-Za-z]:)/, "$1");
const css = fs.readFileSync(CSS_PATH, "utf8");

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
    failures.push(`✗ ${name}: ${(e as Error).message}`);
  }
}
function assert(cond: boolean, msg: string): void {
  if (!cond) throw new Error(msg);
}

/** True if index.css contains a `.cls` selector token (word-boundary exact). */
function hasClassDef(cls: string): boolean {
  // Match `.<cls>` followed by a non-identifier char (space, comma, {, :, etc.).
  const re = new RegExp(`\\.${cls.replace(/[-]/g, "\\-")}(?![\\w-])`);
  return re.test(css);
}

// The project's authored component classes (NOT Tailwind utilities). Every one of
// these is referenced by a component className and styled in index.css.
const COMPONENT_CLASSES = [
  // surfaces / chrome
  "panel",
  "panel-title",
  "btn",
  "btn-accent",
  "btn-danger",
  "btn-promoted",
  "field",
  "mono",
  "label",
  // dimmer
  "dim-content",
  "dim-scrim",
  "overlay-top",
  "dimmer",
  "step-btn",
  // leds + motion
  "led",
  "led-off",
  "led-on",
  "led-warn",
  "led-bad",
  "led-busy",
  "led-letter",
  "blink",
  "blink-alert",
  "view-enter",
  "sheet-enter",
  // empty / flex / instr
  "empty-ghost",
  "empty-state",
  "fill-col",
  "fill-grow",
  // preview surface
  "preview-label",
  "preview-chip",
  "astro-surface",
  // progress
  "progress-track",
  "progress-fill",
  "progress-stripes",
  // ---- Batch-3 touch tokens (F-C1 — the classes this test was written for) ----
  "alert-pulse",
  "more-sheet-in",
  "tap",
  "tap-lg",
  "confirmhold-fill",
];

test("F-C1: every authored component class has a CSS definition in index.css", () => {
  const missing = COMPONENT_CLASSES.filter((c) => !hasClassDef(c));
  assert(missing.length === 0, `missing CSS definitions for: ${missing.join(", ")}`);
});

// Explicit Batch-3 sub-assertions so a regression names the exact missing token.
test("F-C1: alert-pulse keyframe + class are defined", () => {
  assert(/@keyframes\s+alert-pulse/.test(css), "@keyframes alert-pulse missing");
  assert(hasClassDef("alert-pulse"), ".alert-pulse missing");
});

test("F-C1: alert-pulse keeps a static-outline cue under prefers-reduced-motion", () => {
  // The reduced-motion block must KEEP the alert as a static outline (mirroring
  // .led-bad), never silently drop it. Assert alert-pulse appears in a
  // prefers-reduced-motion section with an outline fallback.
  const rmIdx = css.indexOf("prefers-reduced-motion");
  assert(rmIdx >= 0, "no prefers-reduced-motion block");
  const rmBlock = css.slice(rmIdx);
  assert(/alert-pulse/.test(rmBlock), "alert-pulse not handled under reduced-motion");
  assert(/\.led-bad,\s*\.alert-pulse\s*\{[^}]*outline/.test(rmBlock) ||
         /alert-pulse[^}]*outline/.test(rmBlock),
    "alert-pulse has no static outline KEEP fallback under reduced-motion");
});

test("F-C1: more-sheet-in keyframe + class are defined", () => {
  assert(/@keyframes\s+more-sheet-in/.test(css), "@keyframes more-sheet-in missing");
  assert(hasClassDef("more-sheet-in"), ".more-sheet-in missing");
});

test("F-C1: touch-sizing override rules + --tap-gap token are defined", () => {
  assert(hasClassDef("tap"), ".tap missing");
  assert(hasClassDef("tap-lg"), ".tap-lg missing");
  assert(/\.touch-ui\s+\.tap\b/.test(css), ".touch-ui .tap override missing");
  assert(/\.no-touch-ui\s+\.tap\b/.test(css), ".no-touch-ui .tap override missing");
  assert(/--tap-gap\s*:/.test(css), "--tap-gap token missing");
});

// ---------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\ncssClasses.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
