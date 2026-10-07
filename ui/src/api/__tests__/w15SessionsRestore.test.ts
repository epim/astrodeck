// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w15SessionsRestore.test.ts - restoring a kept session backup, and the words
// both UIs put in front of it (#280). Pure: a stubbed fetch, no DOM.
//
//   Run directly:  node --import tsx src/api/__tests__/w15SessionsRestore.test.ts
//
// `POST /api/sessions/{id}/restore` replaces a missing or unreadable
// `<id>.json` with its `.bak` (server `SessionStore.restore_backup`).
// `GET /api/sessions` lists a `.bak` with no `.json` beside it as an unreadable
// row that says `backup: true` and `orphan: true`, and DELETE of that row
// removes the backup and the thumbnails. So the DELETE confirm has a third
// sentence (the file is already gone and the backup is what goes), and the
// RESTORE confirm has two: over a damaged file (its bytes are gone
// afterwards) and over a missing one.
//
// NAMED MUTANTS, each run from a byte copy of the file it mutates and restored
// byte-identical (sha256 checked); the observed failure is quoted at the test.
//   U1 "the orphan body never said"    api/sessions.ts: unreadableDeleteBody
//                                      ignores `row.orphan`
//   U2 "restore posts to the wrong id" api/sessions.ts: restoreSession's URL
//                                      names `/delete`
//   U3 "the restore body hides the damaged file's fate"  api/sessions.ts:
//                                      restoreBody never says a damaged file
//                                      cannot be brought back

/* eslint-disable @typescript-eslint/no-explicit-any */

// `../../api` resolves its base path off `window.location` at module scope, so
// the window goes in before the import (the sessionsListSplit idiom).
(globalThis as unknown as { window: unknown }).window = {
  location: { pathname: "/", origin: "http://local" },
} as unknown as Window;

const asked: { url: string; method: string }[] = [];
const ANSWER = { restored: "s-bad", accepted: 3, backup_ts: 1_757_000_000, detail: "Restored s-bad.json from its backup." };
(globalThis as any).fetch = async (url: string, init?: { method?: string }) => {
  asked.push({ url: String(url), method: (init?.method ?? "GET").toUpperCase() });
  return {
    ok: true,
    status: 200,
    headers: { get: () => "application/json" },
    json: async () => ANSWER,
    text: async () => JSON.stringify(ANSWER),
  };
};

const { restoreSession, restoreBody, unreadableDeleteBody } = await import("../sessions");

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

// ================================================================== the call

await test("restoreSession POSTs to the session's restore route and returns the server's answer", async () => {
  // U2 "restore posts to the wrong id" (the URL made `/api/sessions/${id}/delete`),
  // observed (4/5 passed):
  //   x restoreSession POSTs to the session's restore route and returns the
  //   server's answer: the route:
  //     expected POST /api/sessions/s-bad/restore
  //     got      POST /api/sessions/s-bad/delete
  const r = await restoreSession("s-bad");
  const last = asked[asked.length - 1];
  eq(`${last.method} ${last.url}`, "POST /api/sessions/s-bad/restore", "the route:");
  eq(r.detail, ANSWER.detail, "the server's own words:");
  eq(r.accepted, 3, "the count it reports:");
});

// =============================================================== the DELETE copy

await test("an orphaned backup's delete confirm says the file is gone and the backup goes", async () => {
  // U1 "the orphan body never said" (`if (row.orphan)` made `if (false)`),
  // observed (4/5 passed; the backup branch's sentence came out instead):
  //   x an orphaned backup's delete confirm says the file is gone and the backup
  //   goes: the confirm does not name the backup it removes:
  //   Removes only the file s-orph.json, which cannot be read. Its backup
  //   s-orph.json.bak stays, and so do the thumbnails, because the backup may
  //   be the last good copy of this session log. Saved FITS frames are NOT
  //   deleted. Removing the damaged file cannot be undone.
  const body = unreadableDeleteBody({ id: "s-orph", backup: true, orphan: true });
  eq(body,
    "Removes the backup file s-orph.json.bak and the thumbnails. The session file is already gone. "
    + "Saved FITS frames are NOT deleted. This cannot be undone.",
    "the confirm does not name the backup it removes:");
});

await test("control: the other two unreadable bodies are unchanged", async () => {
  assert(/^Removes only the file s-bak\.json, which cannot be read\. Its backup s-bak\.json\.bak stays/
    .test(unreadableDeleteBody({ id: "s-bak", backup: true })), "a damaged file with a backup lost its sentence");
  assert(/^Removes the file s-bad\.json, which cannot be read, and its thumbnails\./
    .test(unreadableDeleteBody({ id: "s-bad" })), "a damaged file with no backup lost its sentence");
  assert(/^Removes only the file/.test(unreadableDeleteBody({ id: "s-x", backup: true, orphan: false })),
    "orphan: false must read as a file that is still there");
});

// ============================================================== the RESTORE copy

await test("over a damaged file the confirm names the backup, the lost frames, and that the damaged bytes are gone", async () => {
  // U3 "the restore body hides the damaged file's fate" (the sentence "cannot be
  // brought back" removed), observed (4/5 passed):
  //   x over a damaged file the confirm names the backup, the lost frames, and
  //   that the damaged bytes are gone: the confirm does not say the damaged
  //   file is not recoverable afterwards: Replaces s-bak.json, which cannot be
  //   read, with its backup s-bak.json.bak. [...]
  const body = restoreBody({ id: "s-bak" });
  assert(body.includes("s-bak.json.bak"), `the confirm does not name the backup: ${body}`);
  assert(/Frames accepted after the backup was taken are not in it/.test(body),
    `the confirm does not say what the backup lacks: ${body}`);
  assert(/saved FITS frames are NOT deleted/i.test(body), `the confirm lost the FITS sentence: ${body}`);
  assert(/dormant/.test(body) && /auto-resume is off/i.test(body),
    `the confirm does not say how the session comes back: ${body}`);
  assert(/cannot be brought back/.test(body),
    `the confirm does not say the damaged file is not recoverable afterwards: ${body}`);
  assert(!/ledger/i.test(body), `the confirm says "ledger", not "session log": ${body}`);
});

await test("over a missing file the confirm claims no damaged file", async () => {
  const body = restoreBody({ id: "s-orph", orphan: true });
  assert(body.includes("s-orph.json.bak"), `the confirm does not name the backup: ${body}`);
  assert(/Frames accepted after the backup was taken are not in it/.test(body),
    `the confirm does not say what the backup lacks: ${body}`);
  assert(!/cannot be brought back/.test(body) && !/damaged/i.test(body),
    `the confirm talks of a damaged file that is not there: ${body}`);
});

const total = passed + failed;
console.log(`w15SessionsRestore: ${passed}/${total} passed`);
for (const f of failures) console.error(f);
export const result = { passed, failed, total };
