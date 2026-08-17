// standardsPanel.test.ts — anti-drift for the imaging-standards block.
//
// Same shape as nodeDefs.test.ts, and for the same reason: StandardsConfig is
// declared twice, once in server/astrodeck/config.py and once in ui/src/types.ts
// with a default in StandardsPanel, and no endpoint serves the schema. Two
// hand-maintained copies of one contract drift silently — a field the server
// grows and the panel never shows is a setting nobody can reach, which is the
// exact failure #239 stage A exists to fix.
//
// It PARSES config.py rather than restating the field list a third time. A test
// whose expectations are typed by the same hand that typed the data cannot
// catch a transcription error.
//
// Run alone:  npx tsx src/components/settings/__tests__/standardsPanel.test.ts
import { readFileSync } from "node:fs";
import {
  STANDARDS_ALL_FIELDS, STANDARDS_DEFAULTS, standardsOrDefault,
} from "../../../lib/standards";

let passed = 0;
let failed = 0;
const failures: string[] = [];

function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) {
    failed++; failures.push(`x ${name}: ${(e as Error).message}`);
  }
}
function eq<T>(a: T, b: T, msg = ""): void {
  if (a !== b) throw new Error(`${msg} expected ${JSON.stringify(b)}, got ${JSON.stringify(a)}`);
}
function deepEq<T>(a: T, b: T, msg = ""): void {
  const as = JSON.stringify(a), bs = JSON.stringify(b);
  if (as !== bs) throw new Error(`${msg} expected ${bs}, got ${as}`);
}

// ---------------------------------------------------------------- config.py
const CONFIG_PY_REL = "../../../../../server/astrodeck/config.py";
const src: string = (() => {
  try {
    return readFileSync(new URL(CONFIG_PY_REL, import.meta.url), "utf8") as string;
  } catch (e) {
    // A missing config.py must FAIL, never skip: a skipped cross-check reads as
    // a green run while the only thing guarding this file's accuracy is gone.
    throw new Error(`cannot read config.py at ${CONFIG_PY_REL}: ${String(e)}`);
  }
})();

/** Field names declared on `class StandardsConfig`, in source order. */
function standardsFieldsFromPython(): string[] {
  const start = src.indexOf("class StandardsConfig(BaseModel):");
  if (start < 0) throw new Error("class StandardsConfig not found in config.py");
  const rest = src.slice(start + 1);
  const end = rest.indexOf("\nclass ");
  const body = end < 0 ? rest : rest.slice(0, end);
  const out: string[] = [];
  for (const line of body.split("\n")) {
    // `    name: type = ...` at exactly one indent level; skips #: comments,
    // docstring prose and blank lines.
    const m = /^ {4}([a-z_][a-z0-9_]*)\s*:\s*[A-Za-z]/.exec(line);
    if (m) out.push(m[1]);
  }
  return out;
}

const PY_FIELDS = standardsFieldsFromPython();

test("config.py parse found the block", () => {
  eq(PY_FIELDS.length > 0, true, "no fields parsed - the parser or the class moved");
});

test("the panel reaches every field the server has", () => {
  const shown = STANDARDS_ALL_FIELDS.map(String).sort();
  deepEq(shown, [...PY_FIELDS].sort(),
    "a server field the panel never shows is a setting nobody can reach, and a "
    + "panel field the server does not have is a control that saves nothing");
});

test("the panel's defaults cover every field", () => {
  deepEq(Object.keys(STANDARDS_DEFAULTS).sort(), [...PY_FIELDS].sort());
});

test("the defaults are the values SequencePlan used to carry", () => {
  // Hardcoded on purpose: these are the PRE-migration plan defaults, and the
  // whole no-change guarantee of stage A rests on them being exactly these.
  deepEq(STANDARDS_DEFAULTS, {
    apply_filter_offsets: true,
    refocus_on_temp_delta_c: 0,
    min_stars: 0,
    max_guide_rms: 0,
    max_eccentricity: 0,
    max_consecutive_rejects: 10,
    max_consecutive_rejects_night: 20,
  });
});

test("standardsOrDefault fills a config written before the block existed", () => {
  deepEq(standardsOrDefault(undefined), STANDARDS_DEFAULTS);
  eq(standardsOrDefault({ ...STANDARDS_DEFAULTS, min_stars: 40 }).min_stars, 40);
});

test("min_stars is not confused with the WCS one", () => {
  // Two settings, one word. The WCS star floor lives in WcsStampConfig and is
  // edited under Connect; this one decides whether a frame is REJECTED.
  eq(PY_FIELDS.includes("min_stars"), true);
  const wcsStart = src.indexOf("class WcsStampConfig(BaseModel):");
  eq(wcsStart > 0 && src.slice(wcsStart).includes("min_stars"), true,
    "WcsStampConfig.min_stars vanished - if it was consolidated into "
    + "standards.min_stars, that is the merge both codebases warn against");
});

const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\nstandardsPanel.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
