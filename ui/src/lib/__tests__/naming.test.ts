// Unit tests for the PRO-11 client naming mirror (naming.ts). Golden vectors
// are shared with server/tests/test_naming.py so the TS mirror can't drift
// silently from the Python engine (design doc §1.3 / Task 5).
//
// There is no vitest/jest wired into this UI (build is `tsc -b && vite
// build`), so this uses the same tiny inline-assert harness as eta.test.ts.
// Run directly with a TS-aware runner:  npx tsx src/lib/__tests__/naming.test.ts

import { DEFAULT_TEMPLATE, renderTemplatePreview, sanitizeComponent } from "../naming";

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
function eq<T>(a: T, b: T, msg = ""): void {
  if (a !== b) throw new Error(`${msg} expected ${String(b)}, got ${String(a)}`);
}
function assert(cond: boolean, msg: string): void {
  if (!cond) throw new Error(msg);
}

// ---------------------------------------------------------------- fields
const F = { TARGET: "M42", FRAMETYPE: "Light", FILTER: "Ha", DATE: "2026-07-23",
            TIME: "213045", DATETIME: "2026-07-23_213045", NIGHT: "2026-07-23",
            FRAMENR: "0001" };

// ---------------------------------------------------------------- golden vectors
// (mirror server/tests/test_naming.py test_default_template_byte_for_byte)
test("default template byte-for-byte (matches Python golden)", () => {
  eq(renderTemplatePreview(DEFAULT_TEMPLATE, F),
     "M42/Light_M42_Ha_2026-07-23_213045_0001.fits");
});
test("empty filter drops the piece — no double underscore", () => {
  eq(renderTemplatePreview(DEFAULT_TEMPLATE, { ...F, FILTER: "" }),
     "M42/Light_M42_2026-07-23_213045_0001.fits");
});

// (mirror test_folder_tokens_and_empty_folder_drop)
test("night+filter foldering; empty filter folder drops", () => {
  eq(renderTemplatePreview("$$TARGET$$/$$NIGHT$$/$$FILTER$$/$$FRAMETYPE$$_$$FRAMENR$$",
     { ...F, FILTER: "", FRAMENR: "0007" }), "M42/2026-07-23/Light_0007.fits");
});

// (mirror test_sanitizers_match_legacy)
test("sanitizers mirror server", () => {
  eq(sanitizeComponent("NGC 7000", "loose"), "NGC 7000");   // target keeps space
  eq(sanitizeComponent("@M42/x", "loose"), "_M42_x");
  eq(sanitizeComponent("L Pro", "strict"), "L_Pro");         // filter -> underscore
  eq(sanitizeComponent("_Ha_", "strict"), "Ha");
});

// (mirror test_no_path_traversal_from_values_or_literals)
test("no path traversal from a hostile target value", () => {
  const rendered = renderTemplatePreview("$$TARGET$$/$$FRAMETYPE$$_$$FRAMENR$$",
    { TARGET: "../../etc", FRAMETYPE: "Light", FILTER: "", DATE: "d", TIME: "t",
      FRAMENR: "0001" });
  assert(!rendered.includes(".."), `must not contain '..': ${rendered}`);
});

// ---------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\nnaming.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
