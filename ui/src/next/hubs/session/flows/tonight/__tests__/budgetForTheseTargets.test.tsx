// budgetForTheseTargets.test.tsx - the #/next TONIGHT sheet's BUDGET rows say
// whose hours they hold (#536, H4 orchestrator ruling 6).
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/next/hubs/session/flows/tonight/__tests__/budgetForTheseTargets.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT IS GUARDED. `GET /api/flows/{id}/tonight` folds only this flow's own
// targets' reports into BUDGET's banked hours, since #536: it used to fold the
// whole archive, so an M31 flow's Ha bar held M16's nights. The server's
// BUDGET row now says so, "1 h banked for these targets", and an operator on
// the #/next sheet must read those words beside the number, on STORY, where
// the row is drawn.
//
// THE SERVER'S WORDS, READ, NOT COPIED. The answer mounted here is
// `tonight_budget_for_these_targets.json`, beside this file, which
// server/tests/test_h4_budget_for_these_targets.py keeps equal to
// `resolve_tonight`'s answer for an M31 flow (a Ha capture with a goal and an
// L, R cycle) with M31's and M16's reports in the ledger. A literal copied in
// here would stay green after the server stopped saying it (#353 item 7);
// this file follows the server's file, and the server's test follows the
// server.
//
// MUTANT "the words dropped" (tonight.py `_story`: `_FOR_THESE` left out of
// both BUDGET sentences, the fixture rewritten by the server test under the
// mutant, which is what a deliberate change would do), run in the private
// copy scratchpad H4-ROUTES-A-mut from a byte backup. Observed:
//   budgetForTheseTargets.test: 1/2 passed
//     x every BUDGET row says its banked hours are for these targets: a BUDGET
//       row on the #/next sheet does not say whose hours it banks: "Ha: 1 h
//       banked / 2 h goal — tonight adds ≈2 h; the session ledger resumes the
//       remainder next clear night"
// The control stays green under it: the rows are still drawn, with their
// numbers. It can fail: MUTANT "BUDGET rows not drawn" (TonightStoryList.tsx
// mapping `story.filter((r) => r.label !== "BUDGET")`), same copy. Observed:
//   budgetForTheseTargets.test: 0/2 passed
//     x control: the sheet drew the server's two BUDGET rows, stamped BUDGET,
//       numbers and all: STORY drew 0 BUDGET rows, not the server's 2

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
const { readFileSync } = await import("node:fs");
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/", pretendToBeVisual: true },
);
const win = dom.window as any;

win.matchMedia = () => ({
  matches: false,
  addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} addEventListener() {} send() {} };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "localStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "requestAnimationFrame", "cancelAnimationFrame", "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------- the server's answer
const FIXTURE = "tonight_budget_for_these_targets.json";

interface BudgetRow { t_unix: number | null; label: string; msg: string; tone: string }

/** The server's answer. A missing or unreadable file FAILS the file rather
 *  than skipping it: a skipped fixture reads as a green sheet. */
function readAnswer(): Record<string, unknown> {
  let text: string;
  try {
    text = readFileSync(new URL(`./${FIXTURE}`, import.meta.url), "utf8") as string;
  } catch (e) {
    throw new Error(`cannot read ${FIXTURE}, the server's Tonight answer this sheet is `
      + `graded against: ${(e as Error).message}`);
  }
  const fx = JSON.parse(text) as { response?: Record<string, unknown> };
  const story = fx.response?.story;
  if (!Array.isArray(story) || story.length === 0) {
    throw new Error(`${FIXTURE} holds no BUDGET story rows`);
  }
  return fx.response!;
}
const ANSWER = readAnswer();
const BUDGET_ROWS = (ANSWER.story as BudgetRow[]).filter((r) => r.label === "BUDGET");

const FLOW_RECORD = {
  id: "budget-m31", name: "M31 Ha and LR", folder: "My flows",
  tagline: "", readonly: false, graph: { nodes: [], edges: [] },
  last_run: null, last_result: "", updated_ts: 1_757_000_500,
};

g.fetch = async (url: string) => {
  const ok = (data: unknown) => ({
    ok: true, status: 200, statusText: "OK",
    headers: { get: () => "application/json" },
    json: async () => data,
  });
  if (/\/tonight$/.test(url)) return ok(ANSWER);
  if (/^\/api\/flows\/[^/]+$/.test(url)) return ok(FLOW_RECORD);
  return {
    ok: false, status: 404, statusText: "Not Found",
    headers: { get: () => "application/json" },
    json: async () => ({ detail: "no" }),
  };
};

// ------------------------------------------------------------------ imports
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../../store");
const { FlowTonightSheet } = await import("../TonightSheet");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
const settle = async () => {
  for (let i = 0; i < 6; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};

act(() => {
  const s = useStore.getState();
  useStore.setState({
    principal: {
      role: "operator", email: null,
      caps: ["view.status", "view.preview", "view.site_derived", "control.capture"],
    } as never,
    authGate: "open",
    sequence: { state: "idle" } as never,
    status: {} as never,
    equipConnected: true,
    wsPhase: "up",
    resumeArm: null as never,
    flows: {
      ...s.flows,
      record: FLOW_RECORD as never,
      tonight: null,
      tonightLoading: false,
      tonightError: null,
      calHealth: null,
      compiled: null,
      compiling: false,
      ui: { ...s.flows.ui, tonightTab: "story" },
    } as never,
  } as never);
});
await act(async () => {
  root.render(createElement(FlowTonightSheet as any, { params: {}, depth: 0 }));
});
await settle();

/** Each drawn story row as `[stamp, sentence]`. */
function drawnRows(): [string, string][] {
  const story = container.querySelector('[data-testid="tonight-story"]');
  if (!story) throw new Error("STORY never rendered - the fetch or the store seed is wrong");
  return (Array.from(story.querySelectorAll(".nx-tn-story-row")) as any[]).map((row) => [
    (row.querySelector(".nx-tn-story-stamp")?.textContent ?? "") as string,
    (row.querySelector(".nx-tn-story-msg")?.textContent ?? "") as string,
  ]);
}

test("control: the sheet drew the server's two BUDGET rows, stamped BUDGET, numbers and all", () => {
  assert(BUDGET_ROWS.length === 2, `precondition: the answer holds two BUDGET rows, got ${BUDGET_ROWS.length}`);
  const budget = drawnRows().filter(([stamp]) => stamp === "BUDGET");
  assert(budget.length === 2, `STORY drew ${budget.length} BUDGET rows, not the server's 2`);
  assert(budget[0][1].startsWith("Ha: 1 h banked"), `the capture row's figure: "${budget[0][1]}"`);
  assert(budget[1][1].includes("0.2 h banked in its filters"), `the cycle row's figure: "${budget[1][1]}"`);
});

test("every BUDGET row says its banked hours are for these targets", () => {
  const budget = drawnRows().filter(([stamp]) => stamp === "BUDGET");
  for (const [, sentence] of budget) {
    assert(/ h banked (in its filters )?for these targets/.test(sentence),
      `a BUDGET row on the #/next sheet does not say whose hours it banks: "${sentence}"`);
  }
  for (const row of BUDGET_ROWS) {
    assert(budget.some(([, sentence]) => sentence === row.msg),
      `the server's row is not drawn as the server wrote it: "${row.msg}"`);
  }
});

await act(async () => { root.unmount(); });

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`budgetForTheseTargets.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) {
  (globalThis as unknown as { process?: { exitCode?: number } }).process!.exitCode = 1;
}
export default { passed, failed, total };
export { passed, failed, total };
