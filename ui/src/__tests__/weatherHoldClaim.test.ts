// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// A FALSE CLAIM guard, not a copy-style test.
//
// Two surfaces in the classic shell told the user that a high-cloud FORECAST
// would hold an auto-resume:
//
//   App.tsx                     "Auto-resume will hold unless \"ignore weather
//                                tonight\" is set."
//   weather/SkyConditionsPanel  "high cloud tonight - auto-resume will hold
//                                unless overridden"
//
// A third carries the same sentence and was never graded: the #/next shell's
// high-cloud dialog (next/NextApp.tsx). #687 (WP-94) added it here and reworded
// all three to one plain sentence, because the first correction ("the forecast
// does not hold a run: a running session holds on what its own frames show, and
// only forecast rain inside the hour blocks an auto-resume") read as clunky to an
// operator. The reporter's own rewrite said the run "will proceed as long as the
// sky quality meets parameters defined in your astroflow". That is a claim
// nothing keeps: the CLOUD WATCH node's threshold never reaches the engine
// (flows/to_plan.py's note, "does not reach the engine: this trigger fires on the
// detector's own verdict"), so it is graded ABSENT below.
//
// The engine does not do that and has deliberately not done it since 2026-08-19.
// `WeatherService.veto_reason` (server/astrodeck/weather.py) is rain-only and
// fail-open, and its own docstring says why: "RAIN VETOES. CLOUD DOES NOT ...
// A forecast over a ~10 km grid cell is the WRONG instrument, and using it to
// refuse to start is self-fulfilling: decline to open and you never learn the
// sky was clear." It then names the two consecutive nights this gate refused
// under a 100% cloud forecast that turned out clear past 02:00.
//
// So the promise was not merely stale wording - it described a mechanism that
// was removed, and it pointed the user at an override ("ignore weather tonight")
// for a block that would never happen. A cloud HOLD is measured in-run, from the
// rig's own frames.
//
// This reads the two sources rather than importing them, because neither string
// is exported: one is built inline in a `confirmDialog` call, the other is JSX
// text. Same dependency-free node access as src/__tests__/cssClasses.test.ts.
//
//   Run directly:  npx tsx src/__tests__/weatherHoldClaim.test.ts

interface NodeFsLike { readFileSync(path: string, encoding: string): string }
const nodeImport = (s: string): Promise<unknown> =>
  (Function("m", "return import(m)") as (m: string) => Promise<unknown>)(s);
const fs = (await nodeImport("node:fs")) as NodeFsLike;

const pathOf = (rel: string): string => {
  const u = new URL(rel, import.meta.url);
  return decodeURIComponent(u.pathname).replace(/^\/([A-Za-z]:)/, "$1");
};

/** The file's TEXT as the user would read it: full-line `//` comments dropped
 *  (this test's own subject matter is quoted in them), template-literal joins
 *  closed up, and all whitespace collapsed so a sentence broken across JSX or
 *  string-concatenation lines still reads as one sentence. */
function renderedText(rel: string): string {
  const raw = fs.readFileSync(pathOf(rel), "utf8");
  return raw
    .replace(/^[ \t]*\/\/.*$/gm, "")      // whole-line comments only: not https://
    .replace(/`\s*\+\s*`/g, "")           // `...a ` + `b...`  ->  `...ab...`
    .replace(/\s+/g, " ");
}

// ---------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

// The sentence all three surfaces carry. Graded literally: a reword that keeps
// the shape but drops "only forecast rain" would put the claim back. Each clause
// is a fact about the server, pinned against its source further down:
//
//   1. "A cloud forecast does not hold a run."  WeatherService.veto_reason is
//      rain-only and fail-open (weather.py).
//   2. "While it images, the rig checks the sky in its own frames and pauses
//      when they show cloud."  SequenceEngine._safety_gate falls back to the
//      frames' own cloud verdict (`safety.sky_fallback_hold`, on by default).
//   3. "Only forecast rain within the next hour holds anything: it blocks an
//      automatic restart and dusk preparation."  The two callers of
//      veto_reason are resume_arm.py (an automatic restart) and dusk_arm.py
//      (dusk preparation); `_rain_veto` looks one hour ahead.
const TRUE_CLAIM =
  "A cloud forecast does not hold a run. While it images, the rig checks the "
  + "sky in its own frames and pauses when they show cloud. Only forecast rain "
  + "within the next hour holds anything: it blocks an automatic restart and "
  + "dusk preparation.";

const SURFACES: [string, string][] = [
  ["App.tsx high-cloud dialog", "../App.tsx"],
  ["NextApp.tsx high-cloud dialog", "../next/NextApp.tsx"],
  ["SkyConditionsPanel chips row", "../components/weather/SkyConditionsPanel.tsx"],
];

// How far back from the corrected claim to look for the word "cloud". Large
// enough to reach across the surrounding sentence/JSX wrapper in both
// surfaces (measured: 16 chars on SkyConditionsPanel, 144 on App.tsx's
// `confirmDialog` body), small enough to stay inside that one message and
// not reach whatever unrelated code happens to precede it in the file.
const CLOUD_CONTEXT_WINDOW = 250;

for (const [what, rel] of SURFACES) {
  const text = renderedText(rel);
  const claimIndex = text.toLowerCase().indexOf(TRUE_CLAIM.toLowerCase());

  test(`${what}: does not claim a cloud forecast holds an auto-resume`, () => {
    assert(!/auto-resume will hold/i.test(text),
      "the removed rain-only-veto promise is back: the engine's veto_reason is "
      + "rain-only and fail-open, so a cloud forecast holds nothing");
    assert(!/hold unless/i.test(text),
      "the copy still offers an override for a block that never happens");
  });

  test(`${what}: does not promise parameters from the astroflow`, () => {
    // Mutant (insert "parameters defined in your astroflow" into App.tsx): RED
    // - the reporter's #687 wording is on this surface: the CLOUD WATCH
    // threshold never reaches the engine, so no astroflow parameter keeps a run
    // going or stops it
    assert(!/defined in your astroflow/i.test(text),
      "the reporter's #687 wording is on this surface: the CLOUD WATCH threshold "
      + "never reaches the engine, so no astroflow parameter keeps a run going "
      + "or stops it");
  });

  test(`${what}: says what actually decides, in the shipped wording`, () => {
    // Mutant (NextApp.tsx reverted to the old "The forecast does not hold a run:
    // a running session holds on what its own frames show ..." sentence): RED -
    // "NextApp.tsx high-cloud dialog: says what actually decides, in the shipped
    // wording: the corrected sentence is not on this surface." That surface was
    // never graded before this file listed it.
    assert(claimIndex >= 0,
      `the corrected sentence is not on this surface. Expected to find:\n  ${TRUE_CLAIM}`);
  });

  test(`${what}: still names the forecast, in the same message as the corrected claim`, () => {
    // Graded around the claim's own position, not the whole file. `cloud` is
    // also a property name and prefix elsewhere in these files (SkyConditions-
    // Panel.tsx alone has 14 hits that are all identifiers - `f.cloud[i]`,
    // `out.cloud.push`, `cloud_low` - none of them visible text), so a bare
    // /cloud/i.test(text) over the whole source stays green even if the
    // user-visible "high cloud" wording were deleted from this exact message.
    // Require "cloud" in the text immediately BEFORE the corrected claim -
    // the paragraph the user actually reads it in.
    assert(claimIndex >= 0, "the corrected sentence is missing (see the previous assertion)");
    const surroundingText = text.slice(Math.max(0, claimIndex - CLOUD_CONTEXT_WINDOW), claimIndex);
    assert(/cloud/i.test(surroundingText),
      "the message introducing the corrected claim no longer mentions cloud - checked the "
      + `${CLOUD_CONTEXT_WINDOW} characters before it: ${JSON.stringify(surroundingText)}`);
  });
}

// ------------------------------------------- the clauses, against the server
// The sentence above is three facts about the server. Read as text (a UI test
// cannot import Python), so a change that makes any of them untrue breaks THIS
// test, rather than leaving a sentence that nothing keeps.
const serverText = (rel: string): string => fs.readFileSync(pathOf(rel), "utf8");

test("clause 1 and 3: veto_reason is rain-only, and only an automatic restart and dusk preparation ask it", () => {
  const weather = serverText("../../../server/astrodeck/weather.py");
  const veto = /def veto_reason\(self, now: float\)[\s\S]*?\n    def /.exec(weather);
  assert(veto != null, "weather.py no longer defines WeatherService.veto_reason");
  // Every value veto_reason can return: the fail-open `None`s and the rain veto.
  // Anything else (a cloud outlook, a wind figure) is a forecast that holds.
  const returns = [...veto![0].matchAll(/^[ \t]+return[ \t]+(\S.*?)[ \t]*\r?$/gm)]
    .map((m) => m[1].replace(/\s+#.*$/, ""));    // a trailing Python comment
  assert(returns.includes("self._rain_veto(now)"),
    "veto_reason no longer returns the rain veto: nothing is held by forecast rain");
  const others = returns.filter((r) => r !== "None" && r !== "self._rain_veto(now)");
  assert(others.length === 0,
    `veto_reason now returns something besides the rain veto (${JSON.stringify(others)}): `
    + "a cloud forecast may hold a run again, and the sentence on all three surfaces says it does not");
  assert(/self\._weather\.veto_reason\(/.test(serverText("../../../server/astrodeck/sequence/resume_arm.py")),
    "the automatic restart no longer asks veto_reason: 'it blocks an automatic restart' is untrue");
  assert(/self\.weather\.veto_reason\(/.test(serverText("../../../server/astrodeck/dusk_arm.py")),
    "dusk preparation no longer asks veto_reason: 'and dusk preparation' is untrue");
});

test("clause 2: a run's own frames hold it, by default", () => {
  assert(/sky_fallback_hold:\s*bool\s*=\s*True/.test(serverText("../../../server/astrodeck/config.py")),
    "safety.sky_fallback_hold is no longer on by default: 'the rig checks the sky in "
    + "its own frames and pauses when they show cloud' is untrue on a default install");
});

// ---------------------------------------------------------------- summary
const total = passed + failed;
console.log(`weatherHoldClaim.test: ${passed}/${total} passed`);
for (const f of failures) console.error(f);

export const result = { passed, failed, total };
