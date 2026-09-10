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

// The sentence both surfaces now carry. Graded literally: a reword that keeps
// the shape but drops "only forecast rain" would put the claim back.
const TRUE_CLAIM =
  "the forecast does not hold a run: a running session holds on what its own "
  + "frames show, and only forecast rain inside the hour blocks an auto-resume";

const SURFACES: [string, string][] = [
  ["App.tsx high-cloud dialog", "../App.tsx"],
  ["SkyConditionsPanel chips row", "../components/weather/SkyConditionsPanel.tsx"],
];

for (const [what, rel] of SURFACES) {
  const text = renderedText(rel);

  test(`${what}: does not claim a cloud forecast holds an auto-resume`, () => {
    assert(!/auto-resume will hold/i.test(text),
      "the removed rain-only-veto promise is back: the engine's veto_reason is "
      + "rain-only and fail-open, so a cloud forecast holds nothing");
    assert(!/hold unless/i.test(text),
      "the copy still offers an override for a block that never happens");
  });

  test(`${what}: says what actually decides, in the shipped wording`, () => {
    assert(text.toLowerCase().includes(TRUE_CLAIM),
      `the corrected sentence is not on this surface. Expected to find:\n  ${TRUE_CLAIM}`);
  });

  test(`${what}: still names the forecast it is reporting`, () => {
    // The correction must not have eaten the reason the surface exists - the
    // user is being shown a high-cloud forecast and should still be told so.
    assert(/cloud/i.test(text), "the surface no longer mentions cloud at all");
  });
}

// ---------------------------------------------------------------- summary
const total = passed + failed;
console.log(`weatherHoldClaim.test: ${passed}/${total} passed`);
for (const f of failures) console.error(f);

export const result = { passed, failed, total };
