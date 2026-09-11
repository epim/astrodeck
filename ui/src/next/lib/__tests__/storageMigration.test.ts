// storageMigration.test.ts - the one-way door from a browser key to the rig
// (D-FU-1, T-U7b-11).
//
//   Run directly:  npx tsx src/next/lib/__tests__/storageMigration.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc --noEmit`.
//
// PURE, no jsdom: `migrateKey` takes its reader, its writer, its principal and
// its two booleans as arguments, so the only global it needs is `localStorage`,
// and a Map behind three methods is a complete one.
//
// What each test guards, and what goes RED when the guard is removed:
//
//  1. THE HARNESS ITSELF. A stub whose `setItem` did nothing would make every
//     "the key is gone" assertion below pass for the wrong reason, so the first
//     test writes a value and reads it back. Vacuity guard.
//  2. present -> exactly one PUT, then the key is gone, then the flag is set.
//  3. absent -> the flag alone. No PUT for a value that is not there, and no
//     second look next session.
//  4. A FAILING PUT LEAVES THE KEY AND SETS NO FLAG. This is the retry, and it
//     is the assertion that goes red if the flag is written before the write
//     lands (the named sabotage: move `writeFlag` above `await spec.put`).
//  5. A role without the write capability does NOTHING - no PUT, no delete, no
//     flag. It keeps reading its local copy and the surface says why.
//  6. A rig that cannot store the value (`supported: false`) does NOTHING
//     either, and that is the P0: this is the only path in the task that could
//     delete a night's settings on an engine with no home for them.
//  7. A second session with the flag set does nothing even if the key is pasted
//     back by hand.
//  8. SERVER WINS a conflict: no PUT, the local copy deleted, and exactly one
//     toast - and only when a toast was asked for, because the planning keys
//     deliberately raise none.

// --------------------------------------------------------------- the storage
const cells = new Map<string, string>();
const g = globalThis as unknown as { localStorage: unknown };
g.localStorage = {
  getItem: (k: string) => (cells.has(k) ? cells.get(k)! : null),
  setItem: (k: string, v: string) => { cells.set(k, String(v)); },
  removeItem: (k: string) => { cells.delete(k); },
  clear: () => { cells.clear(); },
};

const {
  migrateKey, migrationFlagKey, migrationDone, forgetLegacyKey,
} = await import("../storageMigration");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg = ""): void {
  if (got !== want) throw new Error(`${msg} expected ${JSON.stringify(want)}, got ${JSON.stringify(got)}`);
}

const KEY = "astrodeck-next-sky-quick";
const FLAG = migrationFlagKey(KEY);

const OPERATOR = {
  role: "operator", email: "op@example.test",
  caps: ["view.status", "view.preview", "control.capture", "control.mount"],
} as never;
const VIEWER = {
  role: "viewer", email: "guest@example.test", caps: ["view.status", "view.preview"],
} as never;

interface Spec {
  principal?: unknown;
  supported?: boolean;
  serverHasValue?: boolean;
  fail?: boolean;
  conflictToast?: string | null;
}
const sent: unknown[] = [];
const toasts: string[] = [];

const run = (over: Spec = {}) => migrateKey<{ hours: number }>({
  key: KEY,
  cap: "control.capture",
  principal: (over.principal === undefined ? OPERATOR : over.principal) as never,
  supported: over.supported ?? true,
  serverHasValue: over.serverHasValue ?? false,
  read: (raw) => JSON.parse(raw) as { hours: number },
  put: async (value) => {
    if (over.fail) throw new Error("the rig refused");
    sent.push(value);
  },
  conflictToast: over.conflictToast ?? null,
  onToast: (m) => { toasts.push(m); },
});

function reset(seedValue: string | null): void {
  cells.clear();
  sent.length = 0;
  toasts.length = 0;
  if (seedValue != null) cells.set(KEY, seedValue);
}

// ---------------------------------------------------------------- the tests

await test("the storage stub actually stores - without this every assertion below is vacuous", () => {
  reset(null);
  cells.set("probe", "1");
  eq((g.localStorage as Storage).getItem("probe"), "1", "the stub read back:");
  (g.localStorage as Storage).removeItem("probe");
  eq((g.localStorage as Storage).getItem("probe"), null, "after removeItem:");
  eq(FLAG, "astrodeck-next-migrated-astrodeck-next-sky-quick", "the flag key:");
  assert(FLAG !== KEY, "the flag would overwrite the value it is about");
});

await test("a stored value is PUT once, then the key is gone and the flag is set", async () => {
  reset('{"hours":6}');
  const outcome = await run();
  eq(outcome, "migrated", "the outcome:");
  eq(sent.length, 1, "writes to the rig:");
  eq(JSON.stringify(sent[0]), '{"hours":6}', "what was sent:");
  eq(cells.get(KEY), undefined, "the browser key after a successful move:");
  eq(cells.get(FLAG), "1", "the per-key flag:");
  eq(migrationDone(KEY), true, "migrationDone:");
});

await test("no local value: the flag alone, and nothing is sent", async () => {
  reset(null);
  const outcome = await run();
  eq(outcome, "absent", "the outcome:");
  eq(sent.length, 0, "writes for a value that was never there:");
  eq(cells.get(FLAG), "1", "the flag, so no later session looks again:");
});

await test("a failing write keeps the key AND leaves no flag, so the next session retries", async () => {
  reset('{"hours":6}');
  const outcome = await run({ fail: true });
  eq(outcome, "failed", "the outcome:");
  eq(cells.get(KEY), '{"hours":6}', "the value the rig did not take:");
  eq(cells.get(FLAG), undefined, "the flag after a failed write:");
  eq(migrationDone(KEY), false, "migrationDone after a failed write:");
});

await test("a role that cannot write the block touches nothing", async () => {
  reset('{"hours":6}');
  const outcome = await run({ principal: VIEWER });
  eq(outcome, "not-allowed", "the outcome:");
  eq(sent.length, 0, "writes from a role without control.capture:");
  eq(cells.get(KEY), '{"hours":6}', "the local copy it keeps reading:");
  eq(cells.get(FLAG), undefined, "the flag:");
});

await test("an unresolved principal is treated as holding nothing", async () => {
  reset('{"hours":6}');
  const outcome = await run({ principal: null });
  eq(outcome, "not-allowed", "the outcome:");
  eq(cells.get(KEY), '{"hours":6}', "the local copy:");
});

await test("a rig that cannot store the value is left alone entirely - the P0 guard", async () => {
  reset('{"hours":6}');
  const outcome = await run({ supported: false });
  eq(outcome, "unsupported", "the outcome:");
  eq(sent.length, 0, "writes to an engine with no home for the value:");
  eq(cells.get(KEY), '{"hours":6}', "the key that must survive an older rig:");
  eq(cells.get(FLAG), undefined, "the flag, which would stop the retry forever:");
});

await test("a second session does nothing, even with the key pasted back by hand", async () => {
  reset('{"hours":6}');
  await run();
  eq(cells.get(FLAG), "1", "the flag after the first pass:");
  cells.set(KEY, '{"hours":9}');
  sent.length = 0;
  const outcome = await run();
  eq(outcome, "already-done", "the outcome:");
  eq(sent.length, 0, "writes on the second pass:");
  eq(cells.get(KEY), '{"hours":9}', "and the hand-written key is not eaten either:");
});

await test("the rig's own value wins a conflict: no write, the local copy deleted, one toast", async () => {
  reset('{"hours":6}');
  const outcome = await run({
    serverHasValue: true,
    conflictToast: "Optics moved to the rig - the phone's copy was replaced by the rig's.",
  });
  eq(outcome, "server-wins", "the outcome:");
  eq(sent.length, 0, "writes over the top of the rig's own value:");
  eq(cells.get(KEY), undefined, "the local copy after the rig won:");
  eq(cells.get(FLAG), "1", "the flag:");
  eq(toasts.length, 1, "toasts raised:");
});

await test("a conflict with no toast asked for stays silent - three keys would be three toasts", async () => {
  reset('{"hours":6}');
  await run({ serverHasValue: true });
  eq(toasts.length, 0, "toasts raised for the planning keys:");
  eq(cells.get(KEY), undefined, "the local copy still goes:");
});

await test("an unreadable value is left where it is, and looked at no more", async () => {
  reset("not json at all");
  const outcome = await run();
  eq(outcome, "absent", "the outcome:");
  eq(sent.length, 0, "writes of a value nothing could parse:");
  eq(cells.get(KEY), "not json at all",
    "a shape this build cannot read may still be one another build wrote:");
  eq(cells.get(FLAG), "1", "the flag:");
});

await test("forgetLegacyKey drops an orphan and nothing else", () => {
  reset('{"hours":6}');
  cells.set("astrodeck-next-sky-site", "loc-3");
  forgetLegacyKey("astrodeck-next-sky-site");
  eq(cells.get("astrodeck-next-sky-site"), undefined, "the orphan key:");
  eq(cells.get(KEY), '{"hours":6}', "its neighbour:");
});

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`storageMigration.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);

export default { passed, failed, total };
export { passed, failed, total };
