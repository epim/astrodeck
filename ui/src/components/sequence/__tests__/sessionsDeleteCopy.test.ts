// sessionsDeleteCopy.test.ts - what the classic Sessions panel's delete
// confirm says it removes (#266). MOUNTED (jsdom), no JSX so it stays a .ts.
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/components/sequence/__tests__/sessionsDeleteCopy.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// #242 gave a file the store cannot read a row and a DELETE, and the confirm
// reused the session delete's sentence: "Removes the session ledger and
// thumbnails." Since #266 the server removes only that file when a backup
// (`<id>.json.bak`, the copy taken before an ADOPT) sits beside it, and keeps
// the backup and the thumbnails, because the backup can be the last good copy
// of the ledger. `GET /api/sessions` marks such a row `backup: true`. So the
// confirm for an unreadable row names the one file that goes and, when the
// row reports a backup, names the backup and says it stays; with no backup it
// says the thumbnails go too, and promises no backup. A readable session's
// confirm is unchanged.
//
// NAMED MUTANTS, each run in a private copy of ui/ (never the shared tree,
// #254) from the pristine bytes of the file it changes, restored and sha256
// checked; the observed failure is quoted at the test.
//   D1 "the old body"              every row's confirm is the session sentence
//   D2 "the backup never said"     unreadableDeleteBody ignores `row.backup`
//   D3 "the backup always said"    unreadableDeleteBody takes every row as backed up

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
/** The session delete's sentence as it stood before #266, verbatim. */
const SESSION_BODY =
  "Removes the session ledger and thumbnails. Saved FITS frames are NOT deleted. This cannot be undone.";

const READABLE = {
  id: "s-ok", name: "M31 LRGB", status: "dormant",
  created_ts: 1_756_900_000, updated_ts: 1_757_000_100,
  nights: 2, accepted: 12, total: 40, auto_resume: false,
};
/** Damaged after an ADOPT: the server found `s-bak.json.bak` beside it. */
const BACKED_UP = {
  id: "s-bak", name: "NGC 7000 Ha", status: "unreadable", unreadable: "not valid JSON",
  updated_ts: 1_757_000_300, backup: true,
};
/** Damaged, and nothing beside it: the #242 row, no `backup` key at all. */
const BARE = {
  id: "s-bad", name: "s-bad", status: "unreadable", unreadable: "it has no status",
  updated_ts: 1_757_000_200,
};
const SESSION = {
  id: "s-ok", schema_version: 1, name: "M31 LRGB", status: "dormant",
  created_ts: READABLE.created_ts, updated_ts: READABLE.updated_ts,
  nights: ["2026-09-07", "2026-09-08"], auto_resume: false, origin: "plan", origin_id: "",
  plan: {
    name: "M31 LRGB",
    targets: [{
      id: "t1", name: "M31", ra_hours: 0.71, dec_deg: 41.2, center: true,
      autofocus_first: false, calibration: false,
      steps: [{ id: "st-L", filter: "L", exposure_s: 120, gain: 100, offset: 30, binning: 1, count: 40, frame_type: "Light" }],
    }],
  },
  frames: [],
};

const asked: { url: string; method: string }[] = [];
g.fetch = async (url: string, init?: { method?: string }) => {
  const method = (init?.method ?? "GET").toUpperCase();
  asked.push({ url: String(url), method });
  const json = (data: unknown, status = 200) => ({
    ok: status < 400, status, statusText: status < 400 ? "OK" : "Error",
    headers: { get: () => "application/json" },
    json: async () => data, text: async () => JSON.stringify(data),
  });
  // Newest `updated_ts` first, as the server sorts.
  if (url === "/api/sessions") return json({ sessions: [BACKED_UP, BARE, READABLE] });
  if (url === "/api/sessions/s-bak" && method === "DELETE") {
    return json({
      deleted: "s-bak", backup_kept: "s-bak.json.bak",
      detail: "Removed s-bak.json, which could not be read. Its backup s-bak.json.bak remains in the sessions folder.",
    });
  }
  if (url === "/api/sessions/s-ok") return json(SESSION);
  return json({ detail: "not in this test" }, 404);
};

// ------------------------------------------------------------------ imports
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../store");
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

await act(async () => {
  useStore.setState({
    principal: { role: "operator", email: null, caps: ["view.status", "control.mount", "control.capture"] },
    sequence: { state: "idle" },
    safety: { connected: true, reading: null, streak: 0 },
    weather: null,
    confirm: null,
  } as never);
});
await act(async () => { root.render(createElement(SessionsPanel)); });
await settle();

/** Press a row's delete and return the confirm it raised, still open. */
async function confirmFor(ariaLabel: string): Promise<{ title: string; body: string }> {
  const btn = container.querySelector(`button[aria-label="${ariaLabel}"]`);
  assert(btn != null, `precondition: no "${ariaLabel}" control - the fixture is wrong, not the copy`);
  await act(async () => { btn.dispatchEvent(new win.MouseEvent("click", { bubbles: true })); });
  await settle();
  const req = (useStore.getState() as any).confirm;
  assert(req != null, "no confirm was raised - delete must never be one tap");
  return { title: String(req.title), body: String(req.body) };
}
/** Answer the open confirm. */
async function answer(ok: boolean): Promise<void> {
  await act(async () => { (useStore.getState() as any).resolveConfirm(ok); });
  await settle();
}

// ==================================================================== tests

await testAsync("an unreadable row with a backup: only that file goes, and the backup stays, by name", async () => {
  // D1 "the old body" (the unreadable row's `onDelete` handed
  // `SESSION_DELETE_BODY`), observed (2/4 passed; the other DOM and gallery
  // files stayed green, since none of them tells the two bodies apart):
  //   x an unreadable row with a backup: only that file goes, and the backup
  //   stays, by name: the confirm does not name the one file it removes:
  //   Removes the session ledger and thumbnails. Saved FITS frames are NOT
  //   deleted. This cannot be undone.
  // D2 "the backup never said" (`if (row.backup)` made `if (false)`),
  // observed (3/4 passed):
  //   x an unreadable row with a backup: only that file goes, and the backup
  //   stays, by name: the confirm does not name the one file it removes:
  //   Removes the file s-bak.json, which cannot be read, and its thumbnails.
  //   Saved FITS frames are NOT deleted. This cannot be undone.
  const { title, body } = await confirmFor("Delete unreadable session NGC 7000 Ha");
  await answer(false);
  assert(title.includes("NGC 7000 Ha"), `the confirm does not name the row: ${title}`);
  assert(/Removes only the file s-bak\.json\b/.test(body), `the confirm does not name the one file it removes: ${body}`);
  assert(/s-bak\.json\.bak stays/.test(body), `the confirm does not say the backup stays, by its name: ${body}`);
  assert(/thumbnails/.test(body) && !/and its thumbnails/.test(body),
    `the confirm says the thumbnails go, and they stay with the backup: ${body}`);
  assert(!/Removes the session ledger/.test(body), `the confirm still claims the ledger goes: ${body}`);
  assert(/Saved FITS frames are NOT deleted/.test(body), `the confirm lost the FITS sentence: ${body}`);
});

await testAsync("an unreadable row with no backup: the file and its thumbnails go, and no backup is promised", async () => {
  // D1 "the old body", observed (2/4 passed):
  //   x an unreadable row with no backup: the file and its thumbnails go, and
  //   no backup is promised: the confirm does not name the file it removes:
  //   Removes the session ledger and thumbnails. Saved FITS frames are NOT
  //   deleted. This cannot be undone.
  // D3 "the backup always said" (`if (row.backup)` made `if (true)`),
  // observed (3/4 passed):
  //   x an unreadable row with no backup: the file and its thumbnails go, and
  //   no backup is promised: the confirm does not name the file it removes:
  //   Removes only the file s-bad.json, which cannot be read. Its backup
  //   s-bad.json.bak stays, and so do the thumbnails, because the backup may
  //   be the last good copy of this ledger. Saved FITS frames are NOT deleted.
  //   Removing the damaged file cannot be undone.
  const { body } = await confirmFor("Delete unreadable session s-bad");
  await answer(false);
  assert(/^Removes the file s-bad\.json, which cannot be read, and its thumbnails\./.test(body),
    `the confirm does not name the file it removes: ${body}`);
  assert(!/backup/i.test(body), `the confirm promises a backup the row does not report: ${body}`);
  assert(!/Removes the session ledger/.test(body), `the confirm still claims a ledger goes: ${body}`);
  assert(/Saved FITS frames are NOT deleted\. This cannot be undone\.$/.test(body),
    `the confirm lost the FITS sentence or the warning: ${body}`);
});

await testAsync("control: a readable session's confirm is the session delete's sentence, unchanged", async () => {
  const { body } = await confirmFor("Delete session M31 LRGB");
  await answer(false);
  eq(body, SESSION_BODY, "the readable session's confirm body:");
});

await testAsync("control: confirming the backed-up row still deletes that file, and nothing was sent before", async () => {
  eq(asked.filter((a) => a.method === "DELETE").length, 0,
    "precondition: a cancelled confirm sent a DELETE");
  await confirmFor("Delete unreadable session NGC 7000 Ha");
  await answer(true);
  const sent = asked.filter((a) => a.method === "DELETE");
  eq(sent.map((a) => a.url).join(","), "/api/sessions/s-bak", "the DELETEs sent:");
});

await act(async () => { root.unmount(); });

// ------------------------------------------------------------------ summary
const total = passed + failed;
console.log(`sessionsDeleteCopy: ${passed}/${total} passed`);
for (const f of failures) console.error(f);
export const result = { passed, failed, total };
