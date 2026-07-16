// catalogHint.test.ts — pure zero-state copy helpers (spec R2-ATL-01 minimal).
// Inline assert harness like site.test.ts. Run: npx tsx src/lib/__tests__/catalogHint.test.ts
import {
  looksLikeSolarSystemQuery,
  catalogScopeHint,
  PLANET_SCOPE_HINT,
  GENERAL_SCOPE_HINT,
} from "../catalogHint";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) {
    failed++; failures.push(`✗ ${name}: ${(e as Error).message}`);
  }
}
function assert(cond: boolean, msg: string): void {
  if (!cond) throw new Error(msg);
}
function eq<T>(a: T, b: T, msg = ""): void {
  if (a !== b) throw new Error(`${msg} expected ${String(b)}, got ${String(a)}`);
}

// ---------------------------------------------------- looksLikeSolarSystemQuery
test("recognizes the reviewer's own example (Jupiter)", () => {
  assert(looksLikeSolarSystemQuery("Jupiter"), "Jupiter");
  assert(looksLikeSolarSystemQuery("jupiter"), "lowercase");
  assert(looksLikeSolarSystemQuery("  jupiter  "), "trims whitespace");
});

test("prefix match either direction", () => {
  assert(looksLikeSolarSystemQuery("jup"), "partial typed name");
  assert(looksLikeSolarSystemQuery("Mars"), "exact");
  assert(looksLikeSolarSystemQuery("sun"), "short full name (3 chars)");
  assert(looksLikeSolarSystemQuery("moo"), "moon prefix");
});

test("rejects deep-sky catalog queries", () => {
  assert(!looksLikeSolarSystemQuery("M31"), "M31");
  assert(!looksLikeSolarSystemQuery("Andromeda"), "Andromeda");
  assert(!looksLikeSolarSystemQuery("Orion Nebula"), "Orion Nebula");
  assert(!looksLikeSolarSystemQuery("NGC 7000"), "NGC 7000");
});

test("rejects sub-3-char needles (too ambiguous)", () => {
  assert(!looksLikeSolarSystemQuery("me"), "2 chars");
  assert(!looksLikeSolarSystemQuery("m"), "1 char");
  assert(!looksLikeSolarSystemQuery(""), "empty");
  assert(!looksLikeSolarSystemQuery("   "), "whitespace only");
});

// ---------------------------------------------------- catalogScopeHint
test("catalogScopeHint routes planet-like queries to the planet copy", () => {
  eq(catalogScopeHint("Jupiter"), PLANET_SCOPE_HINT, "Jupiter");
  eq(catalogScopeHint("saturn"), PLANET_SCOPE_HINT, "saturn");
});

test("catalogScopeHint falls back to the general copy otherwise", () => {
  eq(catalogScopeHint("xyz123"), GENERAL_SCOPE_HINT, "gibberish");
  eq(catalogScopeHint("Andromeda"), GENERAL_SCOPE_HINT, "Andromeda");
});

// ---------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\ncatalogHint.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
export const result = { passed, failed, total };
