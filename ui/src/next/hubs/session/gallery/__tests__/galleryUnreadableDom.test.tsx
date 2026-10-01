// galleryUnreadableDom.test.tsx - the SESSION / GALLERY shelf with a session
// file the store cannot read in its list (#242). MOUNTED.
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/next/hubs/session/gallery/__tests__/galleryUnreadableDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// `GET /api/sessions` lists such a file as `{id, name, status: "unreadable",
// unreadable: <reason>, updated_ts}` (server `session.py` `_unreadable_row`).
// The shelf has to draw it as its own read-only card: the name inside the
// file, its id, the word, the reason as sent, and DELETE gated exactly as a
// session card's DELETE (the same capability sentence, the same route). It
// must never become a session card: no "SUBS" count, no thumbnail read, no
// MORE menu with RESUME, UPDATE FROM PLAN or AUTO-RESUME. The count above the
// grid and the GALLERY chip count it, because it is on the shelf.
//
// THE CONFIRM AND THE TOAST ARE NOT ONE VERBATIM SENTENCE ANY MORE (#279).
// Since #266 a DELETE on an unreadable file keeps its `.bak`, with the
// thumbnails, when one sits beside it (the row then carries `backup: true`,
// and the DELETE response carries `backup_kept`/`detail`), and `CONFIRM_DELETE`
// ("Removes the session ledger and thumbnails") is false for that file: the
// backup and the thumbnails stay. `cardActions.ts`'s `runDelete` now looks the
// row up fresh and picks `unreadableDeleteBody(row)` for an unreadable row,
// `CONFIRM_DELETE` otherwise, and the success toast reads the response's
// `backup_kept`/`detail` rather than the generic FITS sentence when a backup
// survived.
//
// NAMED MUTANTS, each run from a byte copy of the file it mutates and restored
// byte-identical (sha256 checked); the observed failure is quoted at the test.
//   G1 "render it as a normal session"   sessionsIndex.ts: buildCards keeps the
//                                        unreadable row and unreadableCards
//                                        returns nothing
//   G2 "ungate the unreadable DELETE"    UnreadableSessionCard.tsx: lockedReason
//                                        is null for every principal
//   G3 "unreadable delete uses the session body"   cardActions.ts:
//                                        `deleteConfirmBody` returns
//                                        `CONFIRM_DELETE` unconditionally
//   G4 "toast ignores backup_kept"       cardActions.ts: `deleteToastDetail`
//                                        always returns the FITS sentence

/* eslint-disable @typescript-eslint/no-explicit-any */

// --------------------------------------------------------------- the css hook
{
  const { registerHooks } = await import("node:module");
  registerHooks({
    load(url: string, context: any, nextLoad: any) {
      if (url.endsWith(".css")) {
        return { format: "module", shortCircuit: true, source: "export default {};" };
      }
      return nextLoad(url, context);
    },
  } as any);
}

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
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "localStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "requestAnimationFrame", "cancelAnimationFrame",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------- the fake rig
const ROW = {
  id: "s1", name: "M31 LRGB", status: "dormant",
  created_ts: 1_756_900_000, updated_ts: 1_757_000_100,
  nights: 2, accepted: 41, total: 120, auto_resume: false,
};
const REASON = "fails validation";
/** The name inside the damaged file, which the server passes on. */
const BAD_NAME = "NGC 7000 Ha";
/** `backup` starts false (no `.bak` beside the file) and a later test flips it
 *  to true, re-mounting fresh each time: one row stands in for both of #266's
 *  cases rather than two fixtures that could drift apart. */
const BAD: { id: string; name: string; status: "unreadable"; unreadable: string; updated_ts: number; backup?: boolean } = {
  id: "s-bad", name: BAD_NAME, status: "unreadable", unreadable: REASON,
  updated_ts: 1_757_000_200,
};
/** The server's words for a kept backup (#266) - invented for this fixture,
 *  not the production sentence verbatim, since the test pins only that the
 *  toast carries WHATEVER the response's `detail` says, not its exact words. */
const BACKUP_DETAIL = "Removed s-bad, which could not be read. Its backup s-bad.json.bak remains.";
const SESSION = {
  id: "s1", schema_version: 1, name: "M31 LRGB",
  created_ts: ROW.created_ts, updated_ts: ROW.updated_ts, status: "dormant",
  nights: ["2026-09-07", "2026-09-08"], auto_resume: false,
  plan: { name: "M31 LRGB", targets: [] },
  frames: [],
};

const asked: { url: string; method: string }[] = [];
g.fetch = async (url: string, init?: { method?: string }) => {
  const method = init?.method ?? "GET";
  asked.push({ url, method });
  const json = (data: unknown, status = 200) => ({
    ok: status < 400, status, statusText: status < 400 ? "OK" : "Error",
    headers: { get: () => "application/json" },
    json: async () => data, text: async () => JSON.stringify(data),
  });
  // In the server's order: newest `updated_ts` first (the file's mtime for BAD).
  if (url === "/api/sessions") return json({ sessions: [BAD, ROW] });
  if (url === "/api/sessions/s-bad" && method === "DELETE") {
    return json(BAD.backup
      ? { deleted: "s-bad", backup_kept: "s-bad.json.bak", detail: BACKUP_DETAIL }
      : { deleted: "s-bad" });
  }
  if (url === "/api/sessions/s-bad") return json({ detail: "unreadable", code: "session_unreadable" }, 500);
  if (url === "/api/sessions/s1") return json(SESSION);
  if (url.startsWith("/api/reports")) return json([]);
  if (url.startsWith("/api/gallery/nights")) {
    return json({ current: "2026-09-08", nights: [{ night: "2026-09-08", frames: 41, bytes: 1024 }], truncated: false });
  }
  return json({ detail: "no" }, 404);
};

// ------------------------------------------------------------------ imports
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { CONFIRM_DELETE } = await import("../cardActions");
const { GalleryScreen } = await import("../GalleryScreen");
const { galleryCountFrom, resetSessionsIndex } = await import("../sessionsIndex");
const { unreadableDeleteBody } = await import("../../../../../api/sessions");

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

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
const settle = async () => {
  for (let i = 0; i < 6; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};
const tid = (t: string): any => container.querySelector(`[data-testid="${t}"]`);
const click = async (el: any) => {
  await act(async () => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
  await settle();
};

async function mount(role: string, caps: string[]): Promise<void> {
  await act(async () => { root.render(createElement("div")); });
  resetSessionsIndex();
  await act(async () => {
    useStore.setState({
      principal: { role, email: null, caps } as never,
      authGate: "open",
      sequence: { state: "idle" } as never,
      safety: { connected: true, reading: null, streak: 0 } as never,
      status: null, equipConnected: true, wsPhase: "up",
      confirm: null, toasts: [],
    } as never);
  });
  await act(async () => { root.render(createElement(GalleryScreen as any)); });
  await settle();
}

// ==================================================================== tests

await mount("admin", ["view.status", "view.preview", "view.media", "control.mount", "control.capture"]);

await testAsync("precondition: the shelf rendered, with the readable session's card", async () => {
  assert(tid("session-gallery") != null, "no session-gallery marker - the fixture is wrong, not the screen");
  assert(tid("session-card-s1") != null, "the readable session's card never rendered");
});

await testAsync("control: the readable session's card is unchanged", async () => {
  const c = tid("session-card-s1");
  assert(/M31 LRGB/.test(c.textContent), "the card lost its name");
  assert(/41 SUBS/.test(c.textContent), "the card lost its sub count");
  assert(tid("session-more-s1") != null, "the card lost its MORE menu");
});

await testAsync("the unreadable file is its own read-only card: name, id, the word, the reason as sent", async () => {
  // G1 "render it as a normal session", observed (3/8 passed; the next two
  // tests, the DELETE test and the viewer's failed with it):
  //   x the unreadable file is its own read-only card: name, id, the word, the
  //   reason as sent: no read-only card for the unreadable file
  const c = tid("session-unreadable-s-bad");
  assert(c != null, "no read-only card for the unreadable file");
  assert(c.textContent.includes(BAD_NAME), "the card does not carry the name inside the file");
  assert(c.textContent.includes("s-bad"), "the card does not name the file's id");
  assert(/UNREADABLE/.test(c.textContent), "the card does not say UNREADABLE");
  assert(c.textContent.includes(REASON), `the reason is not shown as sent: ${c.textContent}`);
  // Exact: nothing else is printed on it (no date off the file's mtime, no
  // count), because every such line would be read off the broken ledger.
  eq(c.textContent, `${BAD_NAME}s-badUNREADABLE${REASON}DELETE`, "the card's whole text:");
});

await testAsync("it is never a session card: no SUBS count, no MORE menu, no session verbs", async () => {
  // G1, observed: the file came out as a session card instead -
  //   x it is never a session card: no SUBS count, no MORE menu, no session
  //   verbs: the unreadable file was drawn as a session card
  //     expected null
  //     got      [object HTMLDivElement]
  eq(tid("session-card-s-bad"), null, "the unreadable file was drawn as a session card");
  eq(tid("session-more-s-bad"), null, "a MORE menu (RESUME, UPDATE, AUTO-RESUME) for a file nobody can read");
  const c = tid("session-unreadable-s-bad");
  assert(c != null, "precondition: no read-only card");
  assert(!/SUBS/.test(c.textContent), `a sub count on a ledger nobody can count: ${c.textContent}`);
  const labels = [...c.querySelectorAll("button")].map((b: any) => b.textContent);
  eq(labels.join(","), "DELETE", "the card's only control:");
});

await testAsync("the shelf never asks the server for the unreadable file's ledger", async () => {
  // G1, observed: as a session card it was queued for a thumbnail read -
  //   x the shelf never asks the server for the unreadable file's ledger:
  //   thumbnail reads of the unreadable file (its answer is a 500 by
  //   construction):
  //     expected 0
  //     got      1
  eq(asked.filter((a) => a.method === "GET" && a.url === "/api/sessions/s-bad").length, 0,
    "thumbnail reads of the unreadable file (its answer is a 500 by construction):");
});

await testAsync("the count above the grid and the GALLERY chip both count the file, and agree", async () => {
  const s = tid("gallery-summary");
  assert(/2 sessions/.test(s.textContent), `the summary does not count the card on the shelf: "${s.textContent}"`);
  eq(galleryCountFrom([ROW as any, BAD as any], []), 2, "the chip's count:");
  eq(galleryCountFrom([ROW as any], []), 1, "control, the chip's count with no unreadable file:");
});

await testAsync("DELETE confirms with the unreadable row's own body (no backup), then deletes the file", async () => {
  // G3 "unreadable delete uses the session body", observed:
  //   x DELETE confirms with the unreadable row's own body (no backup), then
  //   deletes the file: the confirm body must name the damaged file, not the
  //   session delete's sentence:
  //     expected "Removes the file s-bad.json, which cannot be read, and its
  //     thumbnails. Saved FITS frames are NOT deleted. This cannot be undone."
  //     got      "Removes the session ledger and thumbnails. Saved FITS
  //     frames are NOT deleted. This cannot be undone."
  const del = tid("session-unreadable-delete-s-bad");
  assert(del != null, "no DELETE on the card");
  assert(del.getAttribute("aria-disabled") == null, "an admin's DELETE must be live");
  await click(del);
  const req = (useStore.getState() as any).confirm;
  assert(req != null, "no confirm was raised - delete must never be one tap");
  eq(req?.body as string, unreadableDeleteBody(BAD),
    "the confirm body must name the damaged file, not the session delete's sentence");
  assert(req?.body !== CONFIRM_DELETE,
    "control: an unreadable row with no backup must not fall back to the session sentence either");
  const before = asked.filter((a) => a.method === "DELETE").length;
  await act(async () => { (useStore.getState() as any).resolveConfirm(true); });
  await settle();
  const sent = asked.filter((a) => a.method === "DELETE");
  eq(sent.length, before + 1, "confirming must issue the request");
  eq(sent[sent.length - 1].url, "/api/sessions/s-bad", "and against this file");
  const toasts = (useStore.getState() as any).toasts as { title?: string; detail?: string }[];
  eq(toasts[toasts.length - 1]?.detail, "The saved FITS frames are untouched.",
    "with no backup_kept, the toast keeps the generic FITS sentence");
});

await testAsync("with a backup beside the file, DELETE names what stays and the toast says so", async () => {
  // G3, observed (this case): the confirm names the file but never the
  // backup -
  //   x with a backup beside the file, DELETE names what stays and the toast
  //   says so: the confirm does not say the backup stays:
  //     expected true
  //     got      false
  // G4 "toast ignores backup_kept", observed:
  //   x with a backup beside the file, DELETE names what stays and the toast
  //   says so: the toast dropped the server's backup_kept detail:
  //     expected "Removed s-bad, which could not be read. Its backup
  //     s-bad.json.bak remains."
  //     got      "The saved FITS frames are untouched."
  BAD.backup = true;
  await mount("admin", ["view.status", "view.preview", "view.media", "control.mount", "control.capture"]);
  const del = tid("session-unreadable-delete-s-bad");
  assert(del != null, "no DELETE on the card");
  await click(del);
  const req = (useStore.getState() as any).confirm;
  assert(req != null, "no confirm was raised");
  eq(req?.body as string, unreadableDeleteBody(BAD), "the confirm body must name the kept backup");
  assert(/backup/i.test(String(req?.body)), `the confirm does not say the backup stays: ${req?.body}`);
  await act(async () => { (useStore.getState() as any).resolveConfirm(true); });
  await settle();
  const toasts = (useStore.getState() as any).toasts as { title?: string; detail?: string }[];
  eq(toasts[toasts.length - 1]?.detail, BACKUP_DETAIL,
    "the toast dropped the server's backup_kept detail");
  BAD.backup = false;
});

await testAsync("a viewer sees the card with DELETE honest-disabled, carrying the session delete's reason", async () => {
  // G2 "ungate the unreadable DELETE", observed (7/8 passed):
  //   x a viewer sees the card with DELETE honest-disabled, carrying the
  //   session delete's reason: the viewer's DELETE is not locked:
  //     expected true
  //     got      null
  await mount("viewer", ["view.status", "view.preview"]);
  const c = tid("session-unreadable-s-bad");
  assert(c != null, "a viewer must still see the file and its reason");
  const del = tid("session-unreadable-delete-s-bad");
  assert(del != null, "DELETE must still be rendered for a viewer, locked");
  eq(del.getAttribute("aria-disabled"), "true", "the viewer's DELETE is not locked:");

  // The same sentence the session card's DELETE carries for this viewer.
  await click(tid("session-more-s1"));
  const sessionDel = win.document.querySelector('[data-testid="session-verb-delete-s1"]');
  assert(sessionDel != null, "precondition: the session card's DELETE verb is not rendered");
  const sessionReason = sessionDel.getAttribute("title");
  assert(sessionReason, "precondition: the session card's DELETE carries no reason");

  const before = asked.filter((a) => a.method === "DELETE").length;
  await click(del);
  const toasts = (useStore.getState() as any).toasts as { title?: string }[];
  assert(toasts.some((t) => t.title === sessionReason),
    `the locked DELETE did not explain itself with the session delete's reason "${sessionReason}": `
    + JSON.stringify(toasts.map((t) => t.title)));
  eq(asked.filter((a) => a.method === "DELETE").length, before, "a viewer's press reached the network");
});

await act(async () => { root.unmount(); });

// ------------------------------------------------------------------ summary
const total = passed + failed;
console.log(`galleryUnreadableDom: ${passed}/${total} passed`);
for (const f of failures) console.error(f);
export const result = { passed, failed, total };
