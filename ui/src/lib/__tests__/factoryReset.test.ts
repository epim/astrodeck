// Factory-reset client half. Two things here are real logic with a real cost of
// being wrong, and both are cheap to pin:
//
//  1. `confirmWordOk` MUST agree with the server's check
//     (api/app.py::factory_reset_apply — trimmed + case-folded). If the button
//     arms on a word the server rejects, the admin taps a danger button and gets
//     a 400; if it refuses a word the server accepts, the feature is unreachable
//     from a phone whose keyboard auto-capitalises.
//
//  2. `clearClientState` MUST actually empty web storage. The whole point of the
//     browser half is that the next tester does not land on a fresh server
//     behind a stale client (dismissed wizard, device assignments, theme).
//
// Same inline-assert / `tsx` style as the other suites (no jsdom).
//
// Run directly:  npx tsx src/lib/__tests__/factoryReset.test.ts

// ------------------------------------------------------------ browser stubs
// Installed BEFORE the import below: the module graph under the panel reads
// localStorage at import time (store.ts).
class MemStorage {
  m = new Map<string, string>();
  get length(): number { return this.m.size; }
  key(i: number): string | null { return [...this.m.keys()][i] ?? null; }
  getItem(k: string): string | null { return this.m.has(k) ? (this.m.get(k) as string) : null; }
  setItem(k: string, v: string): void { this.m.set(k, String(v)); }
  removeItem(k: string): void { this.m.delete(k); }
  clear(): void { this.m.clear(); }
}
const g = globalThis as unknown as {
  localStorage?: Storage; sessionStorage?: Storage; location?: unknown;
  matchMedia?: unknown; document?: unknown; window?: unknown;
};
const local = new MemStorage();
const session = new MemStorage();
g.localStorage = local as unknown as Storage;
g.sessionStorage = session as unknown as Storage;
g.location ??= { pathname: "/", href: "http://localhost/", reload() {} };
g.matchMedia ??= () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
g.document ??= {
  documentElement: { style: { setProperty() {} }, classList: { toggle() {}, add() {}, remove() {} } },
  addEventListener() {}, removeEventListener() {},
};
g.window ??= g;

const { confirmWordOk, clearClientState, RESET_WORD } =
  await import("../../components/settings/FactoryResetPanel");

function ok(cond: boolean, msg: string): void {
  if (!cond) throw new Error(`FAIL: ${msg}`);
}

// --- 1. the typed-word interlock -------------------------------------------
ok(RESET_WORD === "RESET", "the word is RESET");
ok(confirmWordOk("RESET"), "exact word arms");
// a phone keyboard: auto-capitalised first letter, easy trailing space
ok(confirmWordOk("reset"), "lowercase arms (phone keyboards autocapitalise)");
ok(confirmWordOk("Reset"), "title case arms");
ok(confirmWordOk("  RESET  "), "surrounding whitespace is trimmed");
// ...and nothing else does
ok(!confirmWordOk(""), "empty does NOT arm");
ok(!confirmWordOk("RESE"), "a prefix does NOT arm");
ok(!confirmWordOk("RESETT"), "a superstring does NOT arm");
ok(!confirmWordOk("yes"), "a different word does NOT arm");
ok(!confirmWordOk("factory reset"), "a phrase containing it does NOT arm");

// --- 2. the browser half actually clears ------------------------------------
local.setItem("astrodeck-coach-seen", '{"first-run-wizard":true}');
local.setItem("astrodeck.equipment.assignments.v1", '{"camera":"x"}');
local.setItem("astrodeck-night", "1");
local.setItem("astrodeck-bright-night", "0.4");
session.setItem("whatever", "1");
ok(local.length === 4, "storage seeded");

clearClientState();

ok(local.length === 0, "localStorage emptied (wizard flag, assignments, theme)");
ok(local.getItem("astrodeck-coach-seen") === null, "wizard-dismissed flag is gone");
ok(session.length === 0, "sessionStorage emptied");

// --- 3. a storage failure must not throw ------------------------------------
// (private mode / blocked cookies) — the reload after this call is what puts the
// tester on a first run, so a throw here would strand them mid-reset.
g.localStorage = { clear() { throw new Error("blocked"); } } as unknown as Storage;
clearClientState();

console.log("factoryReset.test.ts: all assertions passed");
