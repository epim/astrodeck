// sessionsPanelNightsDom.test.tsx - the classic Sessions panel's night count
// is the observing nights the server's row answers (#430, H4 task
// H4-SESSIONS; S7 orchestrator ruling 7). MOUNTED.
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/components/sequence/__tests__/sessionsPanelNightsDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// Each card prints its row's `nights` as "N nights". The server's row counted
// RUNS until H4 (`len(s.nights)`, one report id per engine start), so a
// session restarted the same night read "2 nights" here and "night 1" on its
// flow card. It now answers `len(s.observing_nights())`.
//
// THE ROWS ARE THE ROUTE'S, NOT TYPED HERE. The panel only prints what it is
// sent, so a row written into this file would go on passing whatever the
// server counts. `sessionsListNights.recorded.json`, beside this file, is
// `GET /api/sessions` as recorded by
// server/tests/test_h4_sessions_list_counts_nights.py through the real
// routes and a real engine.start, on a pinned clock and zone: one session
// after two starts inside one night key (`one_night`, 2 runs), then after a
// third start the next evening (`next_evening`, 3 runs). That test grades
// the file against what the route serves now, so the literals below are
// graded against the server, through the file.
//
// NAMED MUTANTS, each run in a private copy of server/ and ui/ from a byte
// backup and restored byte-identical (sha256 checked); observed failures are
// quoted at the tests.
//   R1 "row counts runs"            server session.py: the row's `nights` made
//                                   `len(s.nights)`, and this file rewritten
//                                   from the route under it
//                                   (ASTRODECK_REWRITE_H4_SESSIONS_FIXTURE=1)
//   U1 "the card adds tonight"      SessionsPanel.tsx prints `r.nights + 1`
//                                   (the CONTINUE button's rule), pluralised
//                                   on the same number
//   U2 "one night is plural"        SessionsPanel.tsx's `r.nights === 1 ? ""
//                                   : "s"` made `"s"`

/* eslint-disable @typescript-eslint/no-explicit-any */

import { readFileSync } from "node:fs";

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

// ----------------------------------------------------------- the recording
// A missing or unreadable file FAILS the whole file, never skips it: a
// skipped recording reads as a green panel.
const RECORDED = "./sessionsListNights.recorded.json";
let recorded: any;
try {
  recorded = JSON.parse(readFileSync(new URL(RECORDED, import.meta.url), "utf8") as string);
} catch (e) {
  throw new Error(`cannot read ${RECORDED}, the route's rows this panel is graded `
    + `against: ${(e as Error).message}`);
}
const ONE_NIGHT = recorded?.one_night;
const NEXT_EVENING = recorded?.next_evening;
if (!ONE_NIGHT?.row || !NEXT_EVENING?.row) {
  throw new Error("the recording does not hold the one_night and next_evening rows");
}

// ------------------------------------------------------------- the fake rig
/** The rows `GET /api/sessions` answers; each test sets the one it grades. */
let served: any[] = [];
g.fetch = async (url: string) => {
  const json = (data: unknown, status = 200) => ({
    ok: status < 400, status, statusText: status < 400 ? "OK" : "Error",
    headers: { get: () => "application/json" },
    json: async () => data, text: async () => JSON.stringify(data),
  });
  if (url === "/api/sessions") return json({ sessions: served });
  // The card's per-target bars read the session itself; none are graded
  // here, so its ledger is empty.
  const hit = served.find((r) => url === `/api/sessions/${r.id}`);
  if (hit) return json({ id: hit.id, name: hit.name, plan: { name: hit.name, targets: [] }, frames: [] });
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

/** Mount the panel with `row` as the list's only session, and answer the
 *  counts line its card prints. */
async function countsFor(row: any): Promise<string> {
  served = [row];
  await act(async () => { root.render(createElement("div")); });
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
  const spans = [...container.querySelectorAll('[data-testid="session-counts"]')];
  eq(spans.length, 1, "precondition: one card with a counts line");
  return (spans[0] as any).textContent as string;
}

// ==================================================================== tests

await testAsync("precondition: the recording is one session of 2 runs, then 3", async () => {
  eq(ONE_NIGHT.row.id, NEXT_EVENING.row.id, "the two rows are not one session");
  eq(ONE_NIGHT.runs, 2, "the one-night row's run count");
  eq(NEXT_EVENING.runs, 3, "the next-evening row's run count");
});

await testAsync("two starts inside one night key read '1 night' on the card", async () => {
  // R1 "row counts runs", with the file rewritten under it, observed (1/3
  // passed; the control below failed with it, "3 nights" for "2 nights"):
  //   x two starts inside one night key read '1 night' on the card: the card
  //   counted a same-night restart as another night:
  //     expected 2/5 · 1 night
  //     got      2/5 · 2 nights
  // U1 "the card adds tonight", observed (1/3 passed):
  //   x two starts inside one night key read '1 night' on the card: the card
  //   counted a same-night restart as another night:
  //     expected 2/5 · 1 night
  //     got      2/5 · 2 nights
  // U2 "one night is plural", observed (2/3 passed):
  //   x two starts inside one night key read '1 night' on the card: the card
  //   counted a same-night restart as another night:
  //     expected 2/5 · 1 night
  //     got      2/5 · 1 nights
  const r = ONE_NIGHT.row;
  eq(await countsFor(r), `${r.accepted}/${r.total} · 1 night`,
    "the card counted a same-night restart as another night:");
});

await testAsync("control: a start the next evening reads '2 nights'", async () => {
  // Not a control that cannot fail: U1 "the card adds tonight", observed:
  //   x control: a start the next evening reads '2 nights': the next
  //   evening's start is a second night, and no more:
  //     expected 3/5 · 2 nights
  //     got      3/5 · 3 nights
  const r = NEXT_EVENING.row;
  eq(await countsFor(r), `${r.accepted}/${r.total} · 2 nights`,
    "the next evening's start is a second night, and no more:");
});

await act(async () => { root.unmount(); });

// ------------------------------------------------------------------ summary
const total = passed + failed;
console.log(`sessionsPanelNightsDom: ${passed}/${total} passed`);
for (const f of failures) console.error(f);
export const result = { passed, failed, total };
