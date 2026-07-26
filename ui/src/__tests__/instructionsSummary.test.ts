// Review finding (D): the Instructions panel's COLLAPSED row used to join every
// rule's description with " · " into one string and let CSS truncate it, so a
// multi-rule plan showed a fragment of rule 1 and no sign the others existed.
// `instructionsSummary` replaces that with "first rule + N more". It is the only
// piece of that change with real logic, so it is the only piece pinned here.
//
// Run directly:  npx tsx src/__tests__/instructionsSummary.test.ts
//
// InstructionsPanel.tsx is a React module; importing it pulls in the store, which
// reads localStorage at import time. Stub the browser globals FIRST (same idiom as
// src/__tests__/store.test.ts), then dynamic-import the panel.

class MemStorage {
  private m = new Map<string, string>();
  getItem(k: string): string | null {
    return this.m.has(k) ? (this.m.get(k) as string) : null;
  }
  setItem(k: string, v: string): void { this.m.set(k, String(v)); }
  removeItem(k: string): void { this.m.delete(k); }
  clear(): void { this.m.clear(); }
}
const g = globalThis as unknown as {
  localStorage?: Storage;
  matchMedia?: unknown;
  document?: unknown;
  window?: unknown;
};
const documentStub = {
  documentElement: {
    classList: { add() {}, remove() {}, contains: () => false, toggle() {} },
    style: { setProperty() {} },
    dataset: {} as Record<string, string>,
  },
  addEventListener() {},
  removeEventListener() {},
  querySelectorAll: () => [],
};
if (!g.localStorage) g.localStorage = new MemStorage() as unknown as Storage;
if (!g.document) g.document = documentStub;
if (!g.matchMedia) {
  g.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
}
// lib/base.ts derives the API base from window.location at IMPORT time.
if (!g.window) {
  g.window = {
    location: { pathname: "/", href: "http://localhost/", origin: "http://localhost" },
    matchMedia: g.matchMedia,
    addEventListener() {},
    removeEventListener() {},
    localStorage: g.localStorage,
    document: documentStub,
  };
}

const { instructionsSummary, NO_INSTRUCTIONS_SUMMARY } =
  await import("../components/sequence/InstructionsPanel");

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
    failures.push(`${name}: ${e instanceof Error ? e.message : String(e)}`);
  }
}
function eq(actual: unknown, expected: unknown, what: string): void {
  if (actual !== expected) {
    throw new Error(`${what}: expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  }
}

// ---------------------------------------------------------------- tests
test("no rules -> the invite copy, not an empty row", () => {
  eq(instructionsSummary([]), NO_INSTRUCTIONS_SUMMARY, "empty");
});

test("one rule -> that rule verbatim, with no '+N more' tail", () => {
  eq(instructionsSummary(["when HFR > 4.0 then refocus"]),
    "when HFR > 4.0 then refocus", "single");
});

test("several rules -> first rule in full + a count of the remainder", () => {
  const out = instructionsSummary([
    "when HFR > 4.0 then refocus",
    "when guide error > 2.00\" then pause",
    "at 04:30 then abort",
  ]);
  eq(out, "when HFR > 4.0 then refocus  ·  +2 more", "three rules");
});

test("the count is the REMAINDER, not the total", () => {
  const descs = ["a", "b", "c", "d", "e"];
  const out = instructionsSummary(descs);
  eq(out.endsWith(`+${descs.length - 1} more`), true, "remainder count");
  eq(out.includes("+5 more"), false, "must not print the total");
});

test("only the first description is rendered — later rules never leak in", () => {
  const out = instructionsSummary(["first", "SECOND", "THIRD"]);
  eq(out.includes("SECOND"), false, "second rule text absent");
  eq(out.includes("THIRD"), false, "third rule text absent");
  eq(out.startsWith("first"), true, "starts with the first rule");
});

// ---------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\ninstructionsSummary.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}

export const result = { passed, failed, total };
