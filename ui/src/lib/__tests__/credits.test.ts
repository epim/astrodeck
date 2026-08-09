// credits.test.ts — the helpers, and the REAL generated data they run against.
//
//   Run directly:  npx tsx src/lib/__tests__/credits.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// Half of this is ordinary unit testing of a filter. The other half asserts
// properties of `src/credits.generated.json` itself, from the UI side, so the
// screen cannot be handed data it will silently render wrong: a group with no
// entries draws a heading over nothing, and a text reference that resolves to
// nothing draws a licence that is not there. `server/tests/test_credits.py`
// owns the "does every dependency have an entry" half; this owns "can the
// screen actually draw what it was given".

import doc from "../../credits.generated.json";
import {
  countEntries,
  entryMatches,
  filterGroups,
  flaggedEntries,
  hasReproducedText,
  obligationLabel,
  type CreditEntry,
  type CreditsDoc,
} from "../credits";

const credits = doc as unknown as CreditsDoc;

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

const entry = (over: Partial<CreditEntry> = {}): CreditEntry => ({
  name: "fastapi", version: "0.136.3", spdx: "MIT", tier: "redistributed",
  requires: ["notice"], summaries: [], texts: [], ...over,
});

// ------------------------------------------------------------------ helpers

test("obligationLabel turns a code into something a person can read", () => {
  assert(obligationLabel("notice") === "notice reproduced", "notice");
  assert(obligationLabel("share-alike") === "share-alike", "share-alike");
});

test("obligationLabel shows an unknown code rather than dropping it", () => {
  // Dropping it would make a new obligation invisible on a compliance screen.
  assert(obligationLabel("patent-grant") === "patent-grant", "unknown passes through");
});

test("entryMatches: empty query matches everything", () => {
  assert(entryMatches(entry(), ""), "empty");
  assert(entryMatches(entry(), "   "), "whitespace only");
});

test("entryMatches searches name, licence and notes", () => {
  assert(entryMatches(entry(), "FASTAPI"), "name, case-insensitively");
  assert(entryMatches(entry(), "mit"), "licence id");
  assert(entryMatches(entry({ notes: "web framework" }), "framework"), "notes");
  assert(!entryMatches(entry(), "zwo"), "no false match");
});

test("entryMatches narrows on multiple terms rather than widening", () => {
  assert(entryMatches(entry(), "fastapi mit"), "both terms present");
  assert(!entryMatches(entry(), "fastapi apache"), "one term absent -> no match");
});

test("entryMatches finds an entry by what its licence requires", () => {
  assert(entryMatches(entry({ requires: ["share-alike"] }), "share-alike"),
    "searchable by obligation");
});

test("filterGroups drops groups left empty rather than showing a bare heading", () => {
  const fixture = {
    licenses: {},
    groups: [
      { id: "a", title: "A", blurb: "", entries: [entry({ name: "alpha" })] },
      { id: "b", title: "B", blurb: "", entries: [entry({ name: "beta" })] },
    ],
  } as unknown as CreditsDoc;
  const got = filterGroups(fixture, "alpha");
  assert(got.length === 1, `one group survives, got ${got.length}`);
  assert(got[0].id === "a", "the right one");
  assert(filterGroups(fixture, "").length === 2, "no query keeps both");
});

test("hasReproducedText is false for a hash that resolves to nothing", () => {
  const d = { licenses: { abc: "" }, groups: [] } as unknown as CreditsDoc;
  assert(!hasReproducedText(d, entry({ texts: [{ title: "L", hash: "abc" }] })),
    "empty body is not reproduced text");
  assert(!hasReproducedText(d, entry({ texts: [{ title: "L", hash: "nope" }] })),
    "dangling hash is not reproduced text");
  const ok = { licenses: { abc: "MIT License ..." }, groups: [] } as unknown as CreditsDoc;
  assert(hasReproducedText(ok, entry({ texts: [{ title: "L", hash: "abc" }] })), "real body");
});

// --------------------------------------------------- the real generated data

test("the shipped credits have groups, and none of them would render empty", () => {
  assert(credits.groups.length > 0, "no groups at all");
  for (const g of credits.groups) {
    assert(g.entries.length > 0, `group ${g.id} would render an empty heading`);
    assert(!!g.title && !!g.blurb, `group ${g.id} is unlabelled`);
  }
});

test("the shipped credits cover every ecosystem the product actually has", () => {
  const ids = new Set(credits.groups.map((g) => g.id));
  for (const need of ["python", "npm", "fonts", "cargo", "vendored", "data",
                      "services", "programs", "derived"]) {
    assert(ids.has(need), `no ${need} group — the screen would omit a whole ecosystem`);
  }
});

test("every licence text an entry points at is actually present", () => {
  const dangling: string[] = [];
  for (const g of credits.groups) {
    for (const e of g.entries) {
      for (const t of e.texts) {
        if (!credits.licenses[t.hash]) dangling.push(`${e.name} -> ${t.title}`);
      }
    }
  }
  assert(dangling.length === 0, `entries point at missing licence text: ${dangling.join(", ")}`);
});

test("every entry whose licence requires reproduced text has real text", () => {
  const naked: string[] = [];
  for (const g of credits.groups) {
    for (const e of g.entries) {
      const needsText = e.requires.includes("notice") || e.requires.includes("license");
      if (!needsText) continue;
      const longest = Math.max(0, ...e.texts.map((t) => (credits.licenses[t.hash] ?? "").length));
      if (longest < 200) naked.push(`${e.name} (${e.spdx})`);
    }
  }
  assert(naked.length === 0, `licences named but not reproduced: ${naked.join(", ")}`);
});

test("fonts are credited separately from the code around them", () => {
  const fonts = credits.groups.find((g) => g.id === "fonts");
  assert(!!fonts, "no fonts group");
  assert(fonts!.entries.length > 0, "fonts group is empty");
  for (const e of fonts!.entries) {
    assert(/OFL/i.test(e.spdx), `${e.name} is in fonts but is not under the OFL: ${e.spdx}`);
    assert(e.requires.includes("reserved-name"),
      `${e.name} does not carry the OFL reserved-font-name restriction`);
  }
});

test("anything needing an owner decision is flagged and explained", () => {
  for (const e of flaggedEntries(credits)) {
    assert((e.flag ?? "").length > 40, `${e.name} is flagged with no explanation`);
  }
  // Flagged entries must be reachable from the top of the page, not buried.
  const first = credits.groups[0];
  if (flaggedEntries(credits).length > 0) {
    assert(first.id === "flagged", `flagged group is not first; first is ${first.id}`);
  }
});

test("the search can still find a package among all of them", () => {
  assert(countEntries(credits) > 50, "suspiciously few entries — did generation fail?");
  const hits = filterGroups(credits, "react").flatMap((g) => g.entries);
  assert(hits.some((e) => e.name === "react"), "cannot find react among the entries");
});

console.log(`credits.test: ${passed} passed, ${failed} failed`);
if (failed) {
  for (const f of failures) console.error(f);
  // No @types/node in this project (tsc -b gate) — guard process.exit via
  // globalThis so a failure still exits non-zero (share.test.ts idiom).
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
