// targetsCatalog.test.ts - which row a sheet gets back when it asks the
// catalogue for one object.
//
//   Run directly:  npx tsx src/next/hubs/sky/sheets/__tests__/targetsCatalog.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc --noEmit`.
//
// `pickRow` is three lines and it decides what a whole night is pointed at.
// `GET /api/catalog?q=<id>` is a SEARCH: it answers with everything that
// matches, in its own order, and "nothing matched the id you asked for" is one
// of its answers. The fallback to `rows[0]` turned that answer into a
// different object carrying the same confidence - the brief titles it, and the
// quick sheet takes its `ra_hours`/`dec_deg` straight into the flow it
// generates. A mistyped id is then a night spent on a neighbour.
//
// Pure module, but it is reached through `api.ts` -> `lib/base.ts`, which read
// `window.location` AT MODULE SCOPE, so the browser globals go in first and the
// import is dynamic (the convention `skyCards.test.ts` uses, for the same
// reason).

/* eslint-disable @typescript-eslint/no-explicit-any */
{
  const g = globalThis as any;
  if (typeof g.window === "undefined") {
    g.window = {
      location: { pathname: "/", protocol: "http:", host: "test", hash: "" },
      addEventListener() {}, removeEventListener() {},
      matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }),
    };
  }
  if (typeof g.localStorage === "undefined") {
    const m = new Map<string, string>();
    g.localStorage = {
      getItem: (k: string) => (m.has(k) ? (m.get(k) as string) : null),
      setItem: (k: string, v: string) => { m.set(k, String(v)); },
      removeItem: (k: string) => { m.delete(k); },
      clear: () => { m.clear(); },
    };
  }
}

const { pickRow } = await import("../targetsCatalog");
type Row = Parameters<typeof pickRow>[0][number];

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg = ""): void {
  if (got !== want) throw new Error(`${msg} expected ${String(want)}, got ${String(got)}`);
}

const row = (id: string, name: string): Row => ({
  id, name, type: "Galaxy", ra_hours: 1, dec_deg: 2, mag: 9, size_arcmin: 3,
}) as Row;

// The shape the defect lives in: a search for one designation that also matches
// OTHER rows by name, with the asked-for row absent.
const NEIGHBOURS = [
  row("ngc224", "NGC 224, the companion to M31"),
  row("ngc205", "NGC 205, a satellite of M31"),
];

test("the id wins, wherever it sits in the answer", () => {
  const rows = [...NEIGHBOURS, row("m31", "Andromeda Galaxy")];
  eq(pickRow(rows, "m31")?.id, "m31", "the row the caller asked for:");
  eq(pickRow(rows, "M31")?.id, "m31", "case is not part of a designation:");
  eq(pickRow(rows, "M 31")?.id, "m31", "nor is a separator - the server squashes them too:");
});

test("an exact NAME match is the second key, not the first", () => {
  const rows = [row("ngc224", "Andromeda Galaxy"), row("m31", "Messier 31")];
  eq(pickRow(rows, "Andromeda Galaxy")?.id, "ngc224", "a name the caller typed in full:");
  eq(pickRow(rows, "m31")?.id, "m31", "and an id still beats it:");
});

test("NO MATCH IS NULL - never the first row the search happened to return", () => {
  eq(pickRow(NEIGHBOURS, "m31"), null,
    "the asked-for object is not in the answer, so there is no row to hand back:");
  eq(pickRow([], "m31"), null, "an empty answer is also no row:");
  // The exact failure the fallback produced: a confident wrong object. Both
  // callers render whatever comes back AS the target - the brief titles it and
  // the quick sheet flows to its coordinates - so a neighbour here is a night
  // pointed somewhere nobody chose.
  const got = pickRow(NEIGHBOURS, "m31");
  assert(got === null || (got as Row).id === "m31",
    `pickRow answered "${(got as Row | null)?.id}" for a search for "m31"`);
});

const total = passed + failed;
console.log(`targetsCatalog.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
