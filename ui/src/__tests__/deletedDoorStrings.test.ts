// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// deletedDoorStrings.test.ts - the retired Plan door's strings are gone from
// ui/src (#196, #154's door half; spec 2026-09-23 flows mosaic, section 8 S6:
// "a grep test asserts the deleted strings are gone").
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/__tests__/deletedDoorStrings.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT S6 DELETED. The classic Atlas's forward button read one label for a
// mosaic and another for one target, both sending the framing to the classic
// Plan; the Sky quick sheet queued a kept mosaic there too, and the flow card
// drew a synthetic MOSAIC lane card, with a footnote constant, for the panels
// it queued. Both doors are SEND TO FLOW WIZARD now, and the Plan door's words
// must not survive anywhere a user or a later edit could meet them.
//
// HOW. Every source file under ui/src is read as text, TESTS EXCLUDED (a
// `__tests__` directory, a `__fixtures__` directory, or a `*.test.ts(x)` name),
// because a test is where a retired string is legitimately quoted: this file
// quotes all of them. Four strings are checked verbatim, the acceptance's own
// list. Three identifiers (the store's hand-off action, its bump and the bump's
// hook) and SequenceView's banner sentence are checked in CODE only, comments
// stripped, since the comments that record what was deleted and why name them.
//
// THE PENDING LIST IS GONE (the S5/S6 integration, #461). S6-DOORS could not
// edit `next/hubs/sky/sheets/flow.tsx`, which called the synthetic card's
// function, so that function survived in `flowLane.ts` as a pass-through, and
// both occurrences sat on a self-expiring pending list. The integration
// dropped the call and the function, and the list with its expiry case: an
// empty list's expiry case could not fail. It also removed the #/next Plan
// banner the retired door fed, in `next/hubs/session/plan/PlanEditor.tsx`, and
// the store slice, action and hook behind it, which the code-only list now
// holds gone with the banner's sentence.
//
// MUTATION RECORD, 2026-09-28, each run in a private scratch copy of ui/
// (scratchpad/S6-DOORS-mut in the session scratchpad, never the shared tree,
// #254). Output verbatim.
//
//   MUTANT "one string restored" (quickCopy.ts: `export const MOSAIC_FOOTNOTE`
//   put back). Observed ("deletedDoorStrings.test: 5/6 passed"):
//     x no retired string survives in ui/src outside the pending list: the retired Plan door's strings are still in these files (S6 deleted the door; SEND TO FLOW WIZARD replaced it):
//     expected []
//     got      ["next/hubs/sky/sheets/quickCopy.ts: \"MOSAIC_FOOTNOTE\""]
//
//   MUTANT "Atlas label restored" (AtlasView.tsx: the button reads the two old
//   labels again). Observed ("deletedDoorStrings.test: 5/6 passed"):
//     x no retired string survives in ui/src outside the pending list: [...]
//     expected []
//     got      ["views/AtlasView.tsx: \"panels to Plan\"","views/AtlasView.tsx: \"Add target to Plan\""]
//
//   MUTANT "first-run guide still says the Plan button" (firstRunWizard.ts: the
//   target step's body back to its old last words). Observed
//   ("deletedDoorStrings.test: 4/6 passed"):
//     x no retired string survives in ui/src outside the pending list: [...]
//     got      ["lib/firstRunWizard.ts: \"Add target to Plan\""]
//     x the first-run guide says SEND TO FLOW WIZARD, and its step ticks for a saved flow: the guide's target step does not name the door: "Open Atlas — on a phone it's behind MORE in the bottom bar. Search or tap the sky, then press Add target to Plan."
//
//   MUTANT "first-run step ignores a saved flow" (isDone "target" back to
//   `s.targetCount > 0`). Observed ("deletedDoorStrings.test: 5/6 passed"):
//     x the first-run guide says SEND TO FLOW WIZARD, and its step ticks for a saved flow: the step the guide sends to the wizard did not tick for a saved flow:
//     expected true
//     got      false
//
//   MUTANT "SequenceView's banner sentence restored in code" (a template
//   `${n} panels added from Atlas` added to SequenceView.tsx). Observed
//   ("deletedDoorStrings.test: 5/6 passed"):
//     x no retired identifier or banner sentence survives in code: still in code:
//     expected []
//     got      ["views/SequenceView.tsx: \"added from Atlas\""]
//
//   MUTANT "(expiry) the integration removes the call but leaves the pending
//   entry" (flow.tsx's import and call dropped, PENDING untouched). Observed
//   ("deletedDoorStrings.test: 5/6 passed"):
//     x every pending entry still holds its string, so none lingers after the integration: next/hubs/sky/sheets/flow.tsx no longer holds "withMosaicCard" (flow.tsx imports and calls it, and is not S6-DOORS's file: the integration drops the call): remove its entry from PENDING
//
// MUTATION RECORD, the S5/S6 integration (#461), 2026-09-28: the pending list
// and its case gone, so five cases, and the verbatim case renamed "no retired
// string survives in ui/src". Each mutant ran in a private scratch copy of
// ui/ (scratchpad/S5-FINAL-INTEG-ui-mut), from a byte backup restored with
// its sha256 checked. Output verbatim.
//
//   MUTANT "the card's function restored" (flowLane.ts given
//   `export const withMosaicCard = (c: unknown) => c;` again). Observed
//   ("deletedDoorStrings.test: 4/5 passed"):
//     x no retired string survives in ui/src: the retired Plan door's strings are still in these files (S6 deleted the door; SEND TO FLOW WIZARD replaced it):
//     expected []
//     got      ["next/hubs/sky/sheets/flowLane.ts: \"withMosaicCard\""]
//
//   MUTANT "the banner slice restored" (store.ts's cold boot given
//   `atlasBannerPending: null` again). Observed ("4/5 passed"):
//     x no retired identifier or banner sentence survives in code: still in code:
//     expected []
//     got      ["store.ts: \"atlasBannerPending\""]
//   and "the dismiss action restored" (`dismissAtlasBanner: () => {}`), the
//   same case with got ["store.ts: \"dismissAtlasBanner\""].
//
//   MUTANT "the banner's sentence restored" (PlanEditor.tsx given
//   `const BANNER = "panels added from the Sky hub";`). Observed ("4/5"):
//     x no retired identifier or banner sentence survives in code: still in code:
//     expected []
//     got      ["next/hubs/session/plan/PlanEditor.tsx: \"added from the Sky hub\""]
//
//   CONTROL "a comment names the slice" (store.ts given the line comment
//   `// atlasBannerPending and dismissAtlasBanner were here`): 5/5 passed.
//
// THE EXCLUSION CHECK COULD NOT FAIL (#484, S7 finding B14). The walk case
// asserted `FILES.filter(isTest)` was empty, and FILES is
// `ALL.filter((r) => !isTest(r))`, so it was empty for every `isTest`. It now
// grades what the exclusion excluded and kept: this file walked and not
// scanned, a second test file likewise, and `isTest` on named tests and
// named sources. MUTATION RECORD, 2026-09-28 (S7-USKYHUB), each mutant in a
// private scratch copy of ui/ (scratchpad/S7-USKYHUB-mut in the session
// scratchpad), from a byte backup restored with its sha256 checked. Output
// verbatim; a list is cut at "[...]" where it runs on.
//
//   MUTANT "isTest excludes nothing" (`const isTest = (rel: string): boolean
//   => false && rel === "";`), against the file BEFORE this change. Observed
//   ("deletedDoorStrings.test: 3/5 passed"): the walk case PASSED, the
//   tautology; only the two scans went red, because the files they now read
//   happen to quote the strings:
//     x no retired string survives in ui/src: [...] got ["__tests__/deletedDoorStrings.test.ts: \"panels to Plan\"", [...]]
//     x no retired identifier or banner sentence survives in code: still in code: [...]
//   The same mutant against this file. Observed ("2/5 passed"), the walk case
//   red on its own terms, then the same two scans:
//     x the walk reads ui/src's sources and none of its tests, so it cannot pass by reading nothing: the scan read __tests__/deletedDoorStrings.test.ts, which quotes every retired string: tests are not excluded
//
//   MUTANT "isTest misses __fixtures__" (the directory test cut to
//   `(__tests__)`). Observed ("4/5 passed"):
//     x the walk reads ui/src's sources and none of its tests, so it cannot pass by reading nothing: isTest let these tests through to the scan:
//     expected []
//     got      ["next/hubs/sky/sheets/__fixtures__/z.ts"]
//
//   MUTANT "isTest takes any name with test in it" (the name test cut to
//   `/test/`). Observed ("4/5 passed"):
//     x the walk reads ui/src's sources and none of its tests, so it cannot pass by reading nothing: isTest took these ordinary sources for tests, so the scan would skip them:
//     expected []
//     got      ["lib/testing.ts","lib/contest.ts","views/latest.tsx"]
//
//   MUTANT "a retired string injected into a production file" (quickCopy.ts
//   given `export const INJECTED = "panels to Plan";`: the scanned list holds
//   that production file, and the scan reads it). Observed ("4/5 passed"):
//     x no retired string survives in ui/src: the retired Plan door's strings are still in these files (S6 deleted the door; SEND TO FLOW WIZARD replaced it):
//     expected []
//     got      ["next/hubs/sky/sheets/quickCopy.ts: \"panels to Plan\""]
//
//   CONTROL "the same string injected into a test file"
//   (next/hubs/sky/__tests__/mosaicCopyPanelFirst.test.ts given the same
//   constant): 5/5 passed.
//
// Convention: inline test()/eq() helpers, printed tally plus the
// { passed, failed, total } export (shell-and-tests.md section 4).

/* eslint-disable @typescript-eslint/no-explicit-any */

// Dependency-free node:fs, the `lib/__tests__/lazyViews.test.ts` idiom: the
// project installs no @types/node and `tsc -b` checks everything under src/.
interface NodeFsLike {
  readFileSync(path: string, encoding: string): string;
  readdirSync(path: string): string[];
  statSync(path: string): { isDirectory(): boolean };
}
const nodeImport = (s: string): Promise<unknown> =>
  (Function("m", "return import(m)") as (m: string) => Promise<unknown>)(s);
const fs = (await nodeImport("node:fs")) as NodeFsLike;
const toPath = (u: URL): string => decodeURIComponent(u.pathname).replace(/^\/([A-Za-z]:)/, "$1");
const SRC = toPath(new URL("../", import.meta.url)).replace(/[\\/]+$/, "");

const { WIZARD_STEPS, computeWizard } = await import("../lib/firstRunWizard");

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg = ""): void {
  if (JSON.stringify(got) !== JSON.stringify(want)) {
    throw new Error(`${msg}\n  expected ${JSON.stringify(want)}\n  got      ${JSON.stringify(got)}`);
  }
}

// ================================================================= the lists

/** The acceptance's four, verbatim, case-sensitive: the two labels of the old
 *  Atlas button, the synthetic card's footnote constant, and its function. */
const RETIRED: readonly string[] = [
  "panels to Plan",
  "Add target to Plan",
  "MOSAIC_FOOTNOTE",
  "withMosaicCard",
];

/** Named in CODE only (comments stripped): the store's Plan hand-off action,
 *  its bump and the bump's hook, and SequenceView's banner sentence; then
 *  (the integration, #461) the #/next Plan banner's slice, its dismiss
 *  action and its hook, and that banner's sentence. The comments recording
 *  their deletion may name them. */
const RETIRED_CODE: readonly string[] = [
  "addTargetsToPlan",
  "atlasHandoff",
  "useAtlasHandoff",
  "added from Atlas",
  "atlasBannerPending",
  "dismissAtlasBanner",
  "useAtlasBannerPending",
  "added from the Sky hub",
];

// ================================================================= the walk

const SOURCE_EXT = /\.(ts|tsx|js|jsx|mjs|cjs|css|html|md)$/;
const CODE_EXT = /\.(ts|tsx|js|jsx|mjs|cjs)$/;
const isTest = (rel: string): boolean =>
  /(^|\/)(__tests__|__fixtures__)(\/|$)/.test(rel) || /\.test\.(ts|tsx|js|jsx|mjs)$/.test(rel);

function walk(dir: string, rel: string, out: string[]): void {
  for (const name of fs.readdirSync(dir)) {
    if (name === "node_modules") continue;
    const abs = `${dir}/${name}`;
    const r = rel ? `${rel}/${name}` : name;
    if (fs.statSync(abs).isDirectory()) walk(abs, r, out);
    else if (SOURCE_EXT.test(name)) out.push(r);
  }
}
const ALL: string[] = [];
walk(SRC, "", ALL);
const FILES = ALL.filter((r) => !isTest(r));
const TEXT = new Map(FILES.map((r) => [r, fs.readFileSync(`${SRC}/${r}`, "utf8")]));

/** Comments stripped: block comments, then line comments. A `//` inside a
 *  string (a URL) cuts the rest of that line from the scan, which can only
 *  hide an identifier, never invent one; the verbatim scan above is not
 *  stripped at all. */
function codeOf(src: string): string {
  return src.replace(/\/\*[\s\S]*?\*\//g, " ").replace(/(^|[^:\\])\/\/[^\n]*/g, "$1");
}

function found(strings: readonly string[], text: (r: string) => string): Array<{ file: string; s: string }> {
  const out: Array<{ file: string; s: string }> = [];
  for (const r of FILES) {
    const t = text(r);
    for (const s of strings) if (t.includes(s)) out.push({ file: r, s });
  }
  return out;
}

// ==================================================================== cases

test("the walk reads ui/src's sources and none of its tests, so it cannot pass by reading nothing", () => {
  assert(FILES.length > 300, `the walk read ${FILES.length} source files under ${SRC} - it is not reading ui/src`);
  // Every file one of the retired strings lived in before S6.
  for (const f of [
    "views/AtlasView.tsx", "views/SequenceView.tsx", "lib/firstRunWizard.ts", "store.ts",
    "next/hubs/sky/sheets/quickCopy.ts", "next/hubs/sky/sheets/flowLane.ts",
    "next/hubs/sky/sheets/quick.tsx", "next/hubs/sky/frame/FramingCard.tsx",
    "next/hubs/sky/sheets/flow.tsx", "next/hubs/session/plan/PlanEditor.tsx",
    "lib/authGate.ts",
  ]) assert(TEXT.has(f), `the walk did not read ${f}, where a retired string lived`);
  // THE EXCLUSION, GRADED ON WHAT IT EXCLUDED (#484). This used to assert
  // `FILES.filter(isTest)` was empty, and FILES is `ALL.filter(!isTest)`, so
  // that was empty for every `isTest` there could be. What can fail: this
  // file, which quotes every retired string, was walked and was NOT scanned;
  // so was a second test file; and the predicate answers named paths the way
  // the walk needs, both ways round.
  const SELF = "__tests__/deletedDoorStrings.test.ts";
  assert(ALL.includes(SELF), "the walk never saw this file, so its exclusion of tests is untested");
  assert(!TEXT.has(SELF), `the scan read ${SELF}, which quotes every retired string: tests are not excluded`);
  const OTHER = "next/hubs/sky/__tests__/mosaicCopyPanelFirst.test.ts";
  assert(ALL.includes(OTHER) && !TEXT.has(OTHER), `${OTHER} was not walked, or was scanned`);
  const tests = [
    "__tests__/x.test.ts", "lib/__tests__/y.ts", "next/hubs/sky/sheets/__fixtures__/z.ts",
    "lib/w.test.tsx", "lib/v.test.mjs",
  ];
  eq(tests.filter((r) => !isTest(r)), [], "isTest let these tests through to the scan:");
  const sources = [
    "next/hubs/sky/SkyHub.tsx", "next/hubs/sky/sheets/quickCopy.ts",
    "lib/testing.ts", "lib/contest.ts", "views/latest.tsx",
  ];
  eq(sources.filter(isTest), [], "isTest took these ordinary sources for tests, so the scan would skip them:");
});

test("the matchers find each retired string in the line it shipped in (known positives)", () => {
  // The pre-S6 lines, verbatim, so a matcher that finds nothing is visible.
  const shipped: Record<string, string> = {
    "panels to Plan": "                    ? `Send ${panelCount} panels to Plan`",
    "Add target to Plan": "                    : \"Add target to Plan\"}",
    "MOSAIC_FOOTNOTE": "import { MOSAIC_FOOTNOTE } from \"./quickCopy\";",
    "withMosaicCard": "export function withMosaicCard(",
  };
  for (const s of RETIRED) {
    assert(shipped[s] !== undefined, `no shipped line recorded for ${s}`);
    eq(RETIRED.filter((x) => shipped[s].includes(x)), [s], `the shipped line for ${s}, scanned:`);
  }
  // The code-only scan still sees an identifier in code, and not in a comment.
  assert(codeOf("store.addTargetsToPlan(t);\n").includes("addTargetsToPlan"), "the code scan lost code");
  assert(!codeOf("// addTargetsToPlan was here\n").includes("addTargetsToPlan"), "the code scan kept a line comment");
  assert(!codeOf("/* atlasHandoff */ x\n").includes("atlasHandoff"), "the code scan kept a block comment");
  assert(codeOf("const u = \"http://a\"; useAtlasHandoff();\n").includes("\"http:"), "a URL was read as a comment");
});

test("no retired string survives in ui/src", () => {
  const bad = found(RETIRED, (r) => TEXT.get(r) as string);
  eq(bad.map(({ file, s }) => `${file}: "${s}"`), [],
    "the retired Plan door's strings are still in these files (S6 deleted the door; SEND TO FLOW WIZARD replaced it):");
});

test("no retired identifier or banner sentence survives in code", () => {
  // Code files only: a markdown design note (next/DEVIATIONS.md) has no
  // comment syntax to strip, and it records the retired design as history.
  const bad = found(RETIRED_CODE, (r) => (CODE_EXT.test(r) ? codeOf(TEXT.get(r) as string) : ""));
  eq(bad.map(({ file, s }) => `${file}: "${s}"`), [], "still in code:");
});

// ============================================ the words that replaced them

test("the first-run guide says SEND TO FLOW WIZARD, and its step ticks for a saved flow", () => {
  const step = WIZARD_STEPS.find((s) => s.id === "target");
  assert(step != null, "the first-run guide has no 'target' step");
  assert(step!.body.includes("SEND TO FLOW WIZARD"), `the guide's target step does not name the door: "${step!.body}"`);
  assert(step!.body.includes("Atlas"), "the guide's target step no longer says where the door is");
  const blank = {
    siteIsDefault: false, equipConnected: true, profileCount: 1, targetCount: 0,
    hasCooler: false, coolerActive: false, frameCount: 0,
  };
  const done = (snap: typeof blank & { flowCount?: number }) =>
    computeWizard(snap, "target").steps.find((s) => s.id === "target")!.done;
  eq(done({ ...blank, flowCount: 1 }), true, "the step the guide sends to the wizard did not tick for a saved flow:");
  eq(done(blank), false, "control: with no flow and no Plan target the step ticked:");
  eq(done({ ...blank, targetCount: 1 }), true, "control: a Plan target typed by hand no longer ticks the step:");
});

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`deletedDoorStrings.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
