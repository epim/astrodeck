// sessionsListSplit.test.ts - who sees an unreadable session file (#242), and
// what the recovery sentence may say (#246). Pure: a stubbed fetch, no DOM.
//
//   Run directly:  node --import tsx src/api/__tests__/sessionsListSplit.test.ts
//
// THE LIST. `GET /api/sessions` now also lists files the store cannot read,
// as `{id, name, status: "unreadable", unreadable: <reason>, updated_ts}`
// (server `session.py` `_unreadable_row`), plus `backup: true` while a `.bak`
// sits beside the file (#266; the confirm copy that reads it is held in
// components/sequence/__tests__/sessionsDeleteCopy.test.ts). Two lists draw
// them on purpose (the classic SessionsPanel and the #/next Gallery, through
// `listSessionRows`).
// Every other reader goes through `listSessions` and was written for a row
// with a name, counts and a timestamp: the Now screen's session lookup, the
// files sheet's picker, the plan editor's sessions section and Settings'
// gallery count, which sorts on `updated_ts`. Handed an unreadable row, each
// would draw a nameless session with no frames or sort on a missing field, so
// `listSessions` must never return one.
//
// THE SENTENCE. `resumeRecoveryLine` is the one place both Monitors take the
// ladder's line from, so its rules are held here once: the step as sent,
// nothing when the flag is down, nothing over a live run (the 20 s poll
// lagging the ladder's own start), nothing from a server older than H2.
//
// NAMED MUTANTS, each run from a byte copy of api/sessions.ts and restored
// byte-identical (sha256 checked); the observed failure is quoted at the test.
//   L1 "listSessions passes unreadable rows through"
//   M3 "drop the live-run guard"
//   M4 "read recovery without the flag"

/* eslint-disable @typescript-eslint/no-explicit-any */

// `../../api` resolves its base path off `window.location` at module scope, so
// the window goes in before the import (the apiErrorPayload idiom).
(globalThis as unknown as { window: unknown }).window = {
  location: { pathname: "/", origin: "http://local" },
} as unknown as Window;

let payload: any = { sessions: [] };
(globalThis as any).fetch = async () => ({
  ok: true,
  status: 200,
  headers: { get: () => "application/json" },
  json: async () => payload,
  text: async () => JSON.stringify(payload),
});

const { listSessionRows, listSessions, isUnreadableRow, resumeRecoveryLine } =
  await import("../sessions");

let passed = 0, failed = 0;
const failures: string[] = [];
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg}\n  expected ${String(want)}\n  got      ${String(got)}`);
}
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}

const READABLE = {
  id: "s-ok", name: "M31 LRGB", status: "dormant",
  created_ts: 1_756_900_000, updated_ts: 1_757_000_100,
  nights: 2, accepted: 41, total: 120, auto_resume: true,
};
/** T8's shape, `_unreadable_row`: the reason under `unreadable`, the file's
 *  mtime as `updated_ts`, and the stem as the name when the file has none. */
const UNREADABLE = {
  id: "s-bad", name: "s-bad", status: "unreadable", unreadable: "not valid JSON",
  updated_ts: 1_757_000_050,
};

// ===================================================================== list

await test("listSessionRows hands the two drawing lists every row, unreadable included", async () => {
  payload = { sessions: [READABLE, UNREADABLE] };
  const rows = await listSessionRows();
  eq(rows.map((r) => r.id).join(","), "s-ok,s-bad", "rows:");
  eq(rows.filter(isUnreadableRow).length, 1, "unreadable rows:");
});

await test("listSessions never returns an unreadable row to a reader not written for one", async () => {
  // L1 "listSessions passes unreadable rows through", observed (8/9 passed):
  //   x listSessions never returns an unreadable row to a reader not written
  //   for one: rows:
  //     expected s-ok
  //     got      s-ok,s-bad
  payload = { sessions: [READABLE, UNREADABLE] };
  const rows = await listSessions();
  eq(rows.map((r) => r.id).join(","), "s-ok", "rows:");
});

await test("control: readable rows reach listSessions unchanged", async () => {
  payload = { sessions: [READABLE] };
  const rows = await listSessions();
  eq(rows.length, 1, "rows:");
  eq(JSON.stringify(rows[0]), JSON.stringify(READABLE), "the readable row was altered:");
});

await test("control: a payload that is not a list passes through as it did before the split", async () => {
  payload = { sessions: null };
  eq(await listSessions() as unknown, null, "a malformed list must not become a rejection:");
});

// ================================================================= sentence

const LADDER = { step: "solve", session_id: "sess-1", session_name: "M31 LRGB" };
const arm = (over: Record<string, unknown>): any =>
  ({ armed: null, hold: null, recovering: false, recovery: null, ...over });

await test("the sentence is the session name and the step word, and nothing else", async () => {
  eq(resumeRecoveryLine(arm({ recovering: true, recovery: LADDER }), "idle"),
    "Auto-resume is re-centring the mount for M31 LRGB (solve)", "sentence:");
  eq(resumeRecoveryLine(arm({ recovering: true, recovery: { ...LADDER, step: "recentre" } }), "complete"),
    "Auto-resume is re-centring the mount for M31 LRGB (recentre)", "a terminal state, sentence:");
});

await test("a session with no name is named by its id rather than by nothing", async () => {
  eq(resumeRecoveryLine(arm({ recovering: true, recovery: { ...LADDER, session_name: "" } }), "idle"),
    "Auto-resume is re-centring the mount for sess-1 (solve)", "sentence:");
});

await test("control: nothing when the flag is down, whatever detail is left on the payload", async () => {
  // M4 "read recovery without the flag", observed (8/9 passed):
  //   x control: nothing when the flag is down, whatever detail is left on the
  //   payload: a lowered flag:
  //     expected null
  //     got      Auto-resume is re-centring the mount for M31 LRGB (solve)
  eq(resumeRecoveryLine(arm({ recovering: false, recovery: LADDER }), "idle"), null, "a lowered flag:");
  eq(resumeRecoveryLine(arm({ recovering: false, recovery: null }), "idle"), null, "no ladder:");
  eq(resumeRecoveryLine(null, "idle"), null, "no poll yet:");
});

await test("control: a server older than H2 sends neither field and gets no sentence", async () => {
  const old = { armed: null, hold: null } as any;
  eq(resumeRecoveryLine(old, "idle"), null, "an old payload:");
});

await test("control: nothing over a live run, which can only be the poll lagging the ladder's start", async () => {
  // M3 "drop the live-run guard", observed (8/9 passed):
  //   x control: nothing over a live run, which can only be the poll lagging
  //   the ladder's start: over "running":
  //     expected null
  //     got      Auto-resume is re-centring the mount for M31 LRGB (solve)
  for (const state of ["running", "paused", "holding", "aborting"] as const) {
    eq(resumeRecoveryLine(arm({ recovering: true, recovery: LADDER }), state), null, `over "${state}":`);
  }
  assert(resumeRecoveryLine(arm({ recovering: true, recovery: LADDER }), "aborted") != null,
    "a finished run is not a live one: the ladder picks up exactly such a night");
});

const total = passed + failed;
console.log(`sessionsListSplit: ${passed}/${total} passed`);
for (const f of failures) console.error(f);
export const result = { passed, failed, total };
