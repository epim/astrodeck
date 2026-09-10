// A FALSE CLAIM guard for the two remaining surfaces, sibling to
// weatherHoldClaim.test.ts (App.tsx + SkyConditionsPanel.tsx).
//
// `ui/src/next/hubs/session/now/NowBanners.tsx` (WEATHER_VETO/WEATHER_OVERRIDE)
// and `ui/src/components/sequence/SessionsPanel.tsx` (the matching per-session
// warning spans) told the user two false things about auto-resume:
//
//   1. that a high-cloud FORECAST holds the resume ("auto-resume will hold
//      unless overridden")
//   2. that "ignore weather tonight" lifts a CLOUD hold ("resume will ignore
//      clouds tonight")
//
// `WeatherService.veto_reason` (server/astrodeck/weather.py) is rain-only and
// fail-open: "RAIN VETOES. CLOUD DOES NOT." `ignore_tonight` disarms that rain
// veto until the next dusk; it never touched cloud, because cloud was never
// vetoing. A running session's cloud hold, separately, is decided from the
// rig's own frames, not from a forecast.
//
// Same dependency-free node access as weatherHoldClaim.test.ts.
//
//   Run directly:  npx tsx src/__tests__/weatherHoldClaimPanels.test.ts

interface NodeFsLike { readFileSync(path: string, encoding: string): string }
const nodeImport = (s: string): Promise<unknown> =>
  (Function("m", "return import(m)") as (m: string) => Promise<unknown>)(s);
const fs = (await nodeImport("node:fs")) as NodeFsLike;

const pathOf = (rel: string): string => {
  const u = new URL(rel, import.meta.url);
  return decodeURIComponent(u.pathname).replace(/^\/([A-Za-z]:)/, "$1");
};

/** The file's TEXT as the user would read it: full-line `//` comments dropped,
 *  and all whitespace collapsed so a sentence broken across JSX lines still
 *  reads as one sentence. */
function renderedText(rel: string): string {
  const raw = fs.readFileSync(pathOf(rel), "utf8");
  return raw
    .replace(/^[ \t]*\/\/.*$/gm, "")      // whole-line comments only: not https://
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

// The sentences both surfaces now carry, one per banner state. Graded
// literally: a reword that keeps the shape but drops "only forecast rain"
// would put the claim back.
const VETO_CLAIM =
  "it does not hold auto-resume; only forecast rain within the hour does";
const OVERRIDE_CLAIM =
  "forecast rain will not hold auto-resume until the next dusk (cloud forecasts never do)";

const SURFACES: [string, string][] = [
  ["NowBanners.tsx", "../next/hubs/session/now/NowBanners.tsx"],
  ["SessionsPanel.tsx", "../components/sequence/SessionsPanel.tsx"],
];

for (const [what, rel] of SURFACES) {
  const text = renderedText(rel);

  test(`${what}: does not claim a cloud forecast holds an auto-resume`, () => {
    assert(!/auto-resume will hold/i.test(text),
      "the removed rain-only-veto promise is back: the engine's veto_reason is "
      + "rain-only and fail-open, so a cloud forecast holds nothing");
    assert(!/hold unless overridden/i.test(text),
      "the copy still offers an override for a block that never happens");
  });

  test(`${what}: does not claim the override lifts a cloud hold`, () => {
    assert(!/ignore clouds tonight/i.test(text),
      "the override never touched cloud - cloud was never vetoing, so \"ignore "
      + "weather tonight\" cannot be described as lifting a cloud hold");
  });

  test(`${what}: says what actually decides, in the shipped wording`, () => {
    assert(text.toLowerCase().includes(VETO_CLAIM),
      `the corrected veto sentence is not on this surface. Expected to find:\n  ${VETO_CLAIM}`);
    assert(text.toLowerCase().includes(OVERRIDE_CLAIM),
      `the corrected override sentence is not on this surface. Expected to find:\n  ${OVERRIDE_CLAIM}`);
  });

  test(`${what}: still names the forecast it is reporting`, () => {
    // The correction must not have eaten the reason the surface exists - the
    // user is being shown a high-cloud forecast and should still be told so.
    assert(/cloud/i.test(text), "the surface no longer mentions cloud at all");
  });
}

// ---------------------------------------------------------------- summary
const total = passed + failed;
console.log(`weatherHoldClaimPanels.test: ${passed}/${total} passed`);
for (const f of failures) console.error(f);

export const result = { passed, failed, total };
