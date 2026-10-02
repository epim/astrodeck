// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// sessionsPanelUnreadableDom.test.tsx - the classic Sessions panel with a
// session file the store cannot read in its list (#242). MOUNTED.
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/components/sequence/__tests__/sessionsPanelUnreadableDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// `GET /api/sessions` lists such a file as `{id, name, status: "unreadable",
// unreadable: <reason>, updated_ts}` (server `session.py` `_unreadable_row`;
// the reason is "not valid JSON", "fails validation" or "it has no status",
// #218). The panel has to show it read-only: the name inside the file, its id,
// the word, the reason as sent and the same delete a session has, gated the
// same way (control.mount). It must never look like a session with no frames
// (no "0/0", no night count, no dates: `updated_ts` is the file's mtime) and
// never offer review, resume, update from plan or auto-resume, all of which
// read the broken file.
//
// NAMED MUTANTS, each run from a byte copy of SessionsPanel.tsx and restored
// byte-identical (sha256 checked); the observed failure is quoted at the test.
//   S1 "render it as a normal session"   the unreadable branch is skipped
//   S2 "ungate the unreadable delete"    its `canControl &&` becomes `true &&`

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
// `nights: 2` left as it was on purpose when the row began counting OBSERVING
// nights (#430, H4): the detail below holds two ids that carry no start
// stamp, and the server counts each such id as a night of its own, so this
// pair is still what it would send. The count itself is graded against the
// route's recorded rows in sessionsPanelNightsDom.test.tsx.
const READABLE = {
  id: "s-ok", name: "M31 LRGB", status: "dormant",
  created_ts: 1_756_900_000, updated_ts: 1_757_000_100,
  nights: 2, accepted: 12, total: 40, auto_resume: false,
};
const REASON = "it has no status";
/** The name inside the damaged file, which the server passes on. */
const BAD_NAME = "NGC 7000 Ha";
const UNREADABLE = {
  id: "s-bad", name: BAD_NAME, status: "unreadable", unreadable: REASON,
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
  // In the server's order: newest `updated_ts` first, the file's mtime for
  // the unreadable row, so it comes first here.
  if (url === "/api/sessions") return json({ sessions: [UNREADABLE, READABLE] });
  if (url === "/api/sessions/s-bad" && method === "DELETE") return json({ deleted: "s-bad" });
  // The real server answers an unreadable file's GET with 500 session_unreadable.
  if (url === "/api/sessions/s-bad") return json({ detail: "unreadable", code: "session_unreadable" }, 500);
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

/** The card of one row: the bordered block that holds its id or its name. */
function rowOf(marker: string): any {
  return [...container.querySelectorAll("div.border.p-3")].find((d: any) =>
    (d.textContent || "").includes(marker)) ?? null;
}
const unreadableRow = () => container.querySelector('[data-testid="session-unreadable-s-bad"]') as any;
const buttonsIn = (el: any): string[] =>
  [...el.querySelectorAll("button")].map((b: any) =>
    (b.getAttribute("aria-label") || b.textContent || "").trim());

async function mount(caps: string[]): Promise<void> {
  await act(async () => { root.render(createElement("div")); });
  await act(async () => {
    useStore.setState({
      principal: { role: caps.includes("control.mount") ? "operator" : "viewer", email: null, caps },
      sequence: { state: "idle" },
      safety: { connected: true, reading: null, streak: 0 },
      weather: null,
      confirm: null,
    } as never);
  });
  await act(async () => { root.render(createElement(SessionsPanel)); });
  await settle();
}

// ==================================================================== tests

await mount(["view.status", "control.mount", "control.capture"]);

await testAsync("precondition: the panel mounted with the readable session's card", async () => {
  const ok = rowOf("M31 LRGB");
  assert(ok != null, "no card for the readable session - the fixture is wrong, not the panel");
});

await testAsync("control: the readable session's card is unchanged", async () => {
  const ok = rowOf("M31 LRGB");
  assert(/12\/40 · 2 nights/.test(ok.textContent), `its counts are gone: ${ok.textContent}`);
  const labels = buttonsIn(ok).join(" | ");
  for (const want of ["resume", "update from plan", "review", "abandon", "Delete session M31 LRGB",
    "Auto-resume M31 LRGB at dusk"]) {
    assert(labels.includes(want), `the readable card lost "${want}": ${labels}`);
  }
});

await testAsync("the unreadable file is a read-only row with its name, id, the word and the reason as sent", async () => {
  // S1 "render it as a normal session", observed (3/8 passed; the four tests
  // below and the viewer's failed with it, the viewer's as "a viewer must
  // still see the file, with its reason"):
  //   x the unreadable file is a read-only row with its name, id, the word and
  //   the reason as sent: no read-only row for the unreadable file
  const row = unreadableRow();
  assert(row != null, "no read-only row for the unreadable file");
  assert(row.textContent.includes(BAD_NAME), "the row does not carry the name inside the file");
  assert(row.textContent.includes("s-bad"), "the row does not name the file's id");
  assert(/unreadable/i.test(row.textContent), "the row does not say unreadable");
  assert(row.textContent.includes(REASON), `the reason is not shown as sent: ${row.textContent}`);
});

await testAsync("it is never a session with zero frames: nothing on it but name, id, word and reason", async () => {
  const row = rowOf("s-bad");
  assert(row != null, "precondition: the file is not in the panel at all");
  const t = row.textContent as string;
  assert(!/\d+\/\d+/.test(t), `the row carries a count: ${t}`);
  assert(!/undefined|NaN/.test(t), `the row reads fields the file does not have: ${t}`);
  assert(!/night/.test(t), `the row carries a night count: ${t}`);
  // Exact: anything else printed on it (a date off the file's mtime, a
  // placeholder count) is a claim about a ledger nobody can read.
  eq(t, `${BAD_NAME}s-badunreadable${REASON}`, "the row's whole text:");
});

await testAsync("it never offers review, resume, update or auto-resume", async () => {
  const row = rowOf("s-bad");
  assert(row != null, "precondition: the file is not in the panel at all");
  const labels = buttonsIn(row);
  eq(labels.join(" | "), `Delete unreadable session ${BAD_NAME}`, "the row's only control:");
  eq(row.querySelectorAll('[role="switch"], input[type="checkbox"]').length, 0,
    "an auto-resume switch on a file that cannot be read:");
});

await testAsync("the panel never asks the server for the unreadable file's ledger", async () => {
  eq(asked.filter((a) => a.method === "GET" && a.url === "/api/sessions/s-bad").length, 0,
    "GETs of the unreadable file (its answer is a 500 by construction):");
});

await testAsync("DELETE confirms with the session delete's own sentence, then deletes the file", async () => {
  const row = unreadableRow();
  assert(row != null, "precondition: no read-only row");
  const del = row.querySelector(`button[aria-label="Delete unreadable session ${BAD_NAME}"]`);
  assert(del != null, "no delete control on the row");
  await act(async () => { del.dispatchEvent(new win.MouseEvent("click", { bubbles: true })); });
  await settle();
  const req = (useStore.getState() as any).confirm;
  assert(req != null, "no confirm was raised - delete must never be one tap");
  assert(/Saved FITS frames are NOT deleted/.test(String(req.body)), `the confirm lost its sentence: ${req.body}`);
  assert(String(req.title).includes(BAD_NAME), `the confirm does not name the file: ${req.title}`);
  const before = asked.filter((a) => a.method === "DELETE").length;
  await act(async () => { (useStore.getState() as any).resolveConfirm(true); });
  await settle();
  const sent = asked.filter((a) => a.method === "DELETE");
  eq(sent.length, before + 1, "confirming must issue the request");
  eq(sent[sent.length - 1].url, "/api/sessions/s-bad", "and against this file");
});

await testAsync("gated like a session's delete: without control.mount there is none, on either row", async () => {
  // S2 "ungate the unreadable delete", observed (7/8 passed):
  //   x gated like a session's delete: without control.mount there is none, on
  //   either row: a delete control for a viewer on the unreadable row:
  //     expected 0
  //     got      1
  await mount(["view.status"]);
  const row = unreadableRow();
  assert(row != null, "a viewer must still see the file, with its reason");
  assert(row.textContent.includes(REASON), "the viewer does not see the reason");
  eq(row.querySelectorAll("button").length, 0, "a delete control for a viewer on the unreadable row:");
  const ok = rowOf("M31 LRGB");
  assert(ok != null, "precondition: the readable card is gone");
  eq(ok.querySelector('button[aria-label="Delete session M31 LRGB"]'), null,
    "precondition: the readable card's delete is not hidden from a viewer, so the gate compared is wrong");
});

await act(async () => { root.unmount(); });

// ------------------------------------------------------------------ summary
const total = passed + failed;
console.log(`sessionsPanelUnreadableDom: ${passed}/${total} passed`);
for (const f of failures) console.error(f);
export const result = { passed, failed, total };
