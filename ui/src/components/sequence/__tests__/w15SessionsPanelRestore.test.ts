// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w15SessionsPanelRestore.test.ts - RESTORE on the classic Sessions panel's
// unreadable rows (#280). MOUNTED (jsdom), no JSX so it stays a .ts.
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/components/sequence/__tests__/w15SessionsPanelRestore.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// A row that says `backup: true` (a `<id>.json.bak` beside the damaged file, or
// all that is left of it: `orphan: true`) gets a RESTORE button beside the
// trash icon, for whoever may delete (`control.mount`; this panel hides a
// control a principal cannot use, as it does the trash). It confirms first
// (`restoreBody`: the backup named, what it lacks, how the session comes
// back), then POSTs `/api/sessions/{id}/restore`. The toast is the server's
// sentence WHOLE: a refusal names the step of a damaged backup to repair in
// its last clause, and the log-line truncation of a toast would cut it.
//
// NAMED MUTANTS, each run from a byte copy of the file it mutates and restored
// byte-identical (sha256 checked); the observed failure is quoted at the test.
//   P1 "RESTORE not gated on backup"   SessionsPanel.tsx: `u.backup === true &&`
//                                      made `true &&`
//   P2 "RESTORE not gated on control"  SessionsPanel.tsx: `canControl &&`
//                                      dropped from the RESTORE button
//   P3 "a cancelled confirm restores"  SessionsPanel.tsx: onRestore's
//                                      `if (!ok) return;` removed
//   P4 "success toast truncated"       SessionsPanel.tsx: `{ verbatim: true }`
//                                      dropped from the success toast
//   P5 "failure toast truncated"       SessionsPanel.tsx: `{ verbatim: true }`
//                                      dropped from the failure toast
//   P6 "restore does not re-read"      SessionsPanel.tsx: the `await refresh()`
//                                      after a restore removed

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/", pretendToBeVisual: true },
);
const win = dom.window as any;
win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} addEventListener() {} send() {} };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement", "Element", "Node",
  "Event", "CustomEvent", "MouseEvent", "KeyboardEvent", "localStorage", "getComputedStyle",
  "matchMedia", "WebSocket", "requestAnimationFrame", "cancelAnimationFrame",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------- the fake rig
/** Damaged, with `s-bak.json.bak` beside it. */
const BACKED_UP = {
  id: "s-bak", name: "NGC 7000 Ha", status: "unreadable", unreadable: "not valid JSON",
  updated_ts: 1_757_000_300, backup: true,
};
/** The session file is gone and only `s-orph.json.bak` is left. */
const ORPHANED = {
  id: "s-orph", name: "M101 LRGB", status: "unreadable",
  unreadable: "the session file is gone; only its backup remains",
  updated_ts: 1_757_000_250, backup: true, orphan: true,
};
/** Damaged, and nothing beside it: the #242 row, no `backup` key at all. */
const BARE = {
  id: "s-bad", name: "s-bad", status: "unreadable", unreadable: "it has no status",
  updated_ts: 1_757_000_200,
};
/** Long enough that a toast's 140-character cut would take the repair from it. */
const DETAIL = "Restored s-bak.json from its backup. Frames accepted after the backup was taken are not in it; "
  + "the FITS files are untouched. The session is dormant and auto-resume is off.";
const REFUSAL = "the backup cannot be read: fails validation: target 'M42', step 2 of 2 (filter 'Ha'): "
  + "frame_type 'DarkFlat' is not one of Light, Dark, Bias, Flat (in any case)";

const asked: { url: string; method: string }[] = [];
g.fetch = async (url: string, init?: { method?: string }) => {
  const method = (init?.method ?? "GET").toUpperCase();
  asked.push({ url: String(url), method });
  const json = (data: unknown, status = 200) => ({
    ok: status < 400, status, statusText: status < 400 ? "OK" : "Error",
    headers: { get: () => "application/json" },
    json: async () => data, text: async () => JSON.stringify(data),
  });
  if (url === "/api/sessions") return json({ sessions: [BACKED_UP, ORPHANED, BARE] });
  if (url === "/api/sessions/s-bak/restore" && method === "POST") {
    return json({ restored: "s-bak", accepted: 3, backup_ts: 1_757_000_000, detail: DETAIL });
  }
  if (url === "/api/sessions/s-orph/restore" && method === "POST") {
    return json({ detail: { detail: REFUSAL, code: "backup_unreadable" } }, 422);
  }
  return json({ detail: "not in this test" }, 404);
};

// ------------------------------------------------------------------ imports
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../store");
const { restoreBody } = await import("../../../api/sessions");
const SessionsPanel = (await import("../SessionsPanel")).default;

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function testAsync(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg}\n  expected ${String(want)}\n  got      ${String(got)}`);
}
const settle = async () => {
  for (let i = 0; i < 6; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};

const container = win.document.getElementById("root") as any;
const root = createRoot(container);

async function mount(caps: string[]): Promise<void> {
  await act(async () => { root.render(createElement("div")); });
  await act(async () => {
    useStore.setState({
      principal: { role: "operator", email: null, caps },
      sequence: { state: "idle" },
      safety: { connected: true, reading: null, streak: 0 },
      weather: null,
      confirm: null,
      toasts: [],
    } as never);
  });
  await act(async () => { root.render(createElement(SessionsPanel)); });
  await settle();
}
const restoreBtn = (name: string): any =>
  container.querySelector(`button[aria-label="Restore unreadable session ${name}"]`);
const posts = () => asked.filter((a) => a.method === "POST");
const toastsNow = () => (useStore.getState() as any).toasts as { level?: string; title?: string }[];
async function press(el: any): Promise<void> {
  await act(async () => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true })); });
  await settle();
}
async function answer(ok: boolean): Promise<void> {
  await act(async () => { (useStore.getState() as any).resolveConfirm(ok); });
  await settle();
}

const OPERATOR = ["view.status", "control.mount", "control.capture"];

// ==================================================================== tests

await mount(OPERATOR);

await testAsync("RESTORE is on a row with a backup, orphaned or not, and on no other", async () => {
  // P1 "RESTORE not gated on backup" (`canControl && u.backup === true &&` made
  // `canControl && true &&`), observed (4/5 passed; the bare row grew a RESTORE
  // the server could only 404):
  //   x RESTORE is on a row with a backup, orphaned or not, and on no other: a
  //   row with no backup offers RESTORE
  assert(restoreBtn("NGC 7000 Ha") != null, "a damaged file with a backup has no RESTORE");
  assert(restoreBtn("M101 LRGB") != null, "an orphaned backup has no RESTORE");
  assert(restoreBtn("s-bad") == null, "a row with no backup offers RESTORE");
});

await testAsync("RESTORE asks first, with the restore body for that row, and a cancel sends nothing", async () => {
  // P3 "a cancelled confirm restores" (`if (!ok) return;` removed), observed
  // (3/5 passed; the cancelled orphan's POST was refused, so the next test's
  // toast came out an error as well):
  //   x RESTORE asks first, with the restore body for that row, and a cancel
  //   sends nothing: a cancelled confirm sent a POST
  //     expected 0
  //     got      2
  const before = posts().length;
  await press(restoreBtn("NGC 7000 Ha"));
  const first = (useStore.getState() as any).confirm;
  assert(first != null, "no confirm was raised - a restore must never be one tap");
  eq(String(first.body), restoreBody({ id: "s-bak" }), "a damaged file's confirm:");
  assert(String(first.title).includes("NGC 7000 Ha"), `the confirm does not name the row: ${first.title}`);
  await answer(false);

  await press(restoreBtn("M101 LRGB"));
  const second = (useStore.getState() as any).confirm;
  assert(second != null, "no confirm was raised for the orphan");
  eq(String(second.body), restoreBody({ id: "s-orph", orphan: true }), "an orphan's confirm:");
  await answer(false);
  eq(posts().length, before, "a cancelled confirm sent a POST");
});

await testAsync("confirming posts once to the restore route, says the server's sentence whole, and re-reads the list", async () => {
  // P4 "success toast truncated" (`{ verbatim: true }` dropped), observed (4/5
  // passed; the sentence is cut at 137 characters and an ellipsis):
  //   x confirming posts once to the restore route, says the server's sentence
  //   whole, and re-reads the list: the toast is not the server's sentence whole
  // P6 "restore does not re-read" (`await refresh()` removed), observed:
  //   x confirming posts once to the restore route, says the server's sentence
  //   whole, and re-reads the list: the list was not re-read after the restore
  const before = posts().length;
  const listsBefore = asked.filter((a) => a.method === "GET" && a.url === "/api/sessions").length;
  await press(restoreBtn("NGC 7000 Ha"));
  await answer(true);
  const sent = posts();
  eq(sent.length, before + 1, "confirming must issue exactly one request:");
  eq(sent[sent.length - 1].url, "/api/sessions/s-bak/restore", "against this row's restore route:");
  const t = toastsNow()[toastsNow().length - 1];
  eq(t?.level, "success", "the toast's level:");
  assert(t?.title === DETAIL, `the toast is not the server's sentence whole: ${t?.title}`);
  assert(asked.filter((a) => a.method === "GET" && a.url === "/api/sessions").length > listsBefore,
    "the list was not re-read after the restore");
});

await testAsync("a refused restore says the server's whole reason and no success", async () => {
  // P5 "failure toast truncated" (`{ verbatim: true }` dropped), observed (4/5
  // passed; the repair, the last clause, is cut at 137 characters):
  //   x a refused restore says the server's whole reason and no success: the
  //   failure toast is not the server's whole reason: Restore failed: the
  //   backup cannot be read: fails validation: target 'M42', step 2 of 2
  //   (filter 'Ha'): frame_type 'DarkFlat' is not one of[ellipsis]
  await mount(OPERATOR);
  await press(restoreBtn("M101 LRGB"));
  await answer(true);
  const ts = toastsNow();
  assert(!ts.some((t) => t.level === "success"), `a success toast over a refused restore: ${JSON.stringify(ts)}`);
  const t = ts[ts.length - 1];
  eq(t?.level, "error", "the toast's level:");
  assert(t?.title === `Restore failed: ${REFUSAL}`, `the failure toast is not the server's whole reason: ${t?.title}`);
});

await testAsync("a principal who cannot control the mount sees no RESTORE, as it sees no trash", async () => {
  // P2 "RESTORE not gated on control" (`canControl &&` dropped), observed (4/5
  // passed; the two rows with a backup each grew a button for a viewer):
  //   x a principal who cannot control the mount sees no RESTORE, as it sees no
  //   trash: buttons on the unreadable rows of a viewer:
  //     expected 0
  //     got      2
  await mount(["view.status"]);
  eq(container.querySelectorAll("button").length, 0, "buttons on the unreadable rows of a viewer:");
  assert(restoreBtn("NGC 7000 Ha") == null, "a viewer is offered RESTORE");
});

await act(async () => { root.unmount(); });

// ------------------------------------------------------------------ summary
const total = passed + failed;
console.log(`w15SessionsPanelRestore: ${passed}/${total} passed`);
for (const f of failures) console.error(f);
export const result = { passed, failed, total };
