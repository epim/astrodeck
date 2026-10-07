// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w16DormantSetAside.test.tsx - a DORMANT session's set-aside panels are rows
// of PANELS, so RETRY SET-ASIDE PANELS has something to be drawn for once the
// run has ended (#727; backlog wave 16, WP-141; #600, backlog ruling D-07,
// owner-approved 2026-09-30).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/framing/__tests__/w16DormantSetAside.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE RULE. The rows read set-aside state off the live `state.group`, so after
// a run ended owing a set-aside panel nothing on screen said so and no button
// could be drawn. The progress route now lists a dormant session's standing
// set-aside panels on the mosaic block (`set_aside`, server
// `progress._set_aside_tonight`), and `panelRows` gives each the `set_aside`
// state a live run publishes for the same panel: worded, unnumbered and drawn
// dotted exactly as a live one is. A live state wins when both speak, a panel
// the draft skips is not set aside, and a block of another grid says nothing.
// The button itself is still the sheet's to hand a handler (`onRetry`); this
// file grades that PANELS draws it for such a row when handed one and for no
// other.
//
// Every mutant was run from a byte backup of PanelsSection.tsx, restored and
// sha256-compared after each, the mutant's marker grepped absent (#254); the
// failure it produced is quoted in the test it turned red.

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
const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement", "HTMLSelectElement",
  "HTMLTextAreaElement", "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "FocusEvent", "localStorage", "sessionStorage", "getComputedStyle", "matchMedia",
  "requestAnimationFrame", "cancelAnimationFrame", "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------------- imports
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { PanelsSection, panelRows, runRowText, RETRY_SET_ASIDE } =
  await import("../sections/PanelsSection");
type FlowProgressBlock = import("../../../../lib/flowsApi").FlowProgressBlock;
type PanelRunState = import("../../flowRunState").PanelRunState;

// ------------------------------------------------------------------ harness
const container = win.document.getElementById("root");
const root = createRoot(container);
function unmount(): void { act(() => { root.render(null); }); }
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
  finally { unmount(); }
}
function assert(cond: unknown, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (JSON.stringify(got) !== JSON.stringify(want)) {
    throw new Error(`${msg}: expected ${JSON.stringify(want)}, got ${JSON.stringify(got)}`);
  }
}

// ------------------------------------------------------------------ fixtures
// A 2x3 mosaic whose session went dormant owing 2-2 (set aside for the
// night) and 1-2 (a centring set-aside that may still expire tonight); 1-3
// is skipped. Every panel banked 2 of 20 but 2-2, which banked none, so
// least complete first would rank 2-2 first if it were in the order.
const entry = (id: string, row: number, col: number, forNow: boolean) =>
  ({ target_id: id, name: `M31 ${row + 1}-${col + 1}`, row, col, for_now: forNow });
const BLOCK: FlowProgressBlock & { set_aside?: unknown } = {
  node_id: "n2", name: "M31", kind: "target", banked: 10, owed: 110, total: 120,
  grid: { rows: 2, cols: 3 },
  group_id: "grp",
  panels: [
    { target_id: "a", name: "M31 1-1", row: 0, col: 0, banked: 2, owed: 18, total: 20, steps: [] },
    { target_id: "b", name: "M31 1-2", row: 0, col: 1, banked: 2, owed: 18, total: 20, steps: [] },
    { target_id: "d", name: "M31 2-1", row: 1, col: 0, banked: 2, owed: 18, total: 20, steps: [] },
    { target_id: "e", name: "M31 2-2", row: 1, col: 1, banked: 0, owed: 20, total: 20, steps: [] },
    { target_id: "f", name: "M31 2-3", row: 1, col: 2, banked: 4, owed: 16, total: 20, steps: [] },
  ],
  skipped: [{ target_id: "c", name: "M31 1-3", row: 0, col: 2, banked: 0 }],
  set_aside: [entry("e", 1, 1, false), entry("b", 0, 1, true)],
} as any;
const WITHOUT: FlowProgressBlock = { ...BLOCK } as any;
delete (WITHOUT as any).set_aside;

function rowsOf(over: Record<string, unknown> = {}) {
  return panelRows({
    rows: 2, cols: 3, skip: [[1, 3]], progress: BLOCK, answerPanels: null,
    order: "Least complete first", ...over,
  } as any);
}
const byLabel = (rows: ReturnType<typeof rowsOf>, label: string) => rows.find((r) => r.label === label)!;

// ---------------------------------------------------------------- the rows

// MUTANT "set_aside ignored" (PanelsSection.tsx `setAsideByCell`: the loop
// over `progress.set_aside` made to read an empty list, `[] as DormantSetAside[]`),
// 4/8. Observed, the first and the last:
//   x a dormant session's set-aside panel is a set_aside row, listed after
//     the panels that run, unnumbered, worded as the live run words it: the
//     rows' states, in the order they are listed: expected [["1-1",null],["2-1",null],["2-3",null],["1-2","set_aside"],["2-2","set_aside"],["1-3",null]], got [["2-2",null],["1-1",null],["1-2",null],["2-1",null],["2-3",null],["1-3",null]]
//   x PANELS draws RETRY for a dormant set-aside row when handed a handler,
//     and for no other: the button for a dormant set-aside row: expected "RETRY SET-ASIDE PANELS", got null
test("a dormant session's set-aside panel is a set_aside row, listed after the panels that run, unnumbered, worded as the live run words it", () => {
  const rows = rowsOf();
  eq(rows.map((r) => [r.label, r.run?.kind ?? null]),
     [["1-1", null], ["2-1", null], ["2-3", null], ["1-2", "set_aside"], ["2-2", "set_aside"], ["1-3", null]],
     "the rows' states, in the order they are listed");
  eq(rows.map((r) => [r.label, r.order]),
     [["1-1", 1], ["2-1", 2], ["2-3", 3], ["1-2", null], ["2-2", null], ["1-3", null]],
     "the panels that run are numbered 1 to n; the set-aside ones and the skipped one are not");
  const night = byLabel(rows, "2-2");
  assert(!night.skipped, "set aside is not skipped: its toggle stays ON, the run owes it frames");
  eq(runRowText(night.label, night.run), "2-2: set aside tonight", "the line for a set-aside for the night");
});

// MUTANT "forNow ignored" (`forNow: s.for_now === true` made `forNow: false`),
// 7/8. Observed:
//   x a centring set-aside that may still expire is worded for now, as the
//     live run words it: 1-2's line: expected "1-2: set aside for now; tried once more tonight", got "1-2: set aside tonight"
test("a centring set-aside that may still expire is worded for now, as the live run words it", () => {
  const row = byLabel(rowsOf(), "1-2");
  eq(runRowText(row.label, row.run), "1-2: set aside for now; tried once more tonight", "1-2's line");
});

test("CONTROL: with no set_aside on the block no row has a run state and 2-2 is numbered first, as it always was", () => {
  const rows = rowsOf({ progress: WITHOUT });
  assert(rows.every((r) => !r.run), "a row carries a run state with nothing set aside");
  eq(rows.map((r) => [r.label, r.order]),
     [["2-2", 1], ["1-1", 2], ["1-2", 3], ["2-1", 4], ["2-3", 5], ["1-3", null]],
     "the editor's order");
  // And with no progress block at all, nothing throws and nothing is set aside.
  assert(rowsOf({ progress: null }).every((r) => !r.run), "no progress, no state");
});

// MUTANT "live state loses" (the `run:` of `panelRows` made
// `dormantAside.get(k) ?? a.run?.[...] ?? null`, the progress block first),
// 6/8, the skip case going red beside it. Observed:
//   x a live state wins where both speak: the live reason, not the block's silence: expected "2-2: set aside tonight: rejected every frame", got "2-2: set aside tonight"
test("a live state wins where both speak", () => {
  const live: Record<string, PanelRunState> = {
    "2-2": { kind: "set_aside", reason: "rejected every frame", forNow: false },
    "1-2": { kind: "shooting" },
  };
  const rows = rowsOf({ run: live });
  const two = byLabel(rows, "2-2");
  eq(runRowText(two.label, two.run), "2-2: set aside tonight: rejected every frame",
     "the live reason, not the block's silence");
  eq(byLabel(rows, "1-2").run?.kind, "shooting",
     "a panel the run is shooting is shot, though the stale block still lists it");
});

// MUTANT "skip filter dropped" (`(skipped.has(k) ? null : dormantAside.get(k) ?? null)`
// made `dormantAside.get(k) ?? null`), 7/8. Observed:
//   x a panel the draft skips is not set aside: 2-2 skipped in the draft: no set-aside state: expected [true,null], got [true,"set_aside"]
test("a panel the draft skips is not set aside", () => {
  const rows = rowsOf({ skip: [[1, 3], [2, 2]] });
  const two = byLabel(rows, "2-2");
  eq([two.skipped, two.run?.kind ?? null], [true, null], "2-2 skipped in the draft: no set-aside state");
  eq(byLabel(rows, "1-2").run?.kind, "set_aside", "the panel the draft keeps is still set aside");
});

// MUTANT "grid guard dropped" (`!describesGrid(...)` removed from
// `setAsideByCell`). Observed:
//   x a block of another grid says nothing: the 3x3 draft's states: expected [], got ["1-2","2-2"]
test("a block of another grid says nothing", () => {
  const rows = panelRows({ rows: 3, cols: 3, skip: [], progress: BLOCK, answerPanels: null,
    order: "Least complete first" } as any);
  eq(rows.filter((r) => r.run).map((r) => r.label), [], "the 3x3 draft's states");
});

// MUTANT "null cell coerced to 0" (the key made `${s.row ?? 0},${s.col ?? 0}`,
// so a cell-less entry sets 1-1 aside), 7/8. Observed:
//   x an entry with no cell matches no panel: a null cell set a panel aside
test("an entry with no cell matches no panel", () => {
  const block = { ...BLOCK, set_aside: [{ ...entry("z", 0, 0, false), row: null, col: null }] } as any;
  assert(rowsOf({ progress: block }).every((r) => !r.run), "a null cell set a panel aside");
});

// ------------------------------------------------- PANELS, the button's rule

const doc = win.document;
const retryButton = () =>
  doc.querySelector('[data-testid="framing-retry"] button') as any;
function mount(rows: unknown, over: Record<string, unknown> = {}): void {
  act(() => { root.render(null); });
  act(() => {
    root.render(createElement(PanelsSection, {
      rows, order: "Least complete first", showAltitude: false, nightCard: null,
      onOrder: () => {}, onToggle: () => {}, onRetry: () => {}, ...over,
    } as any));
  });
}

// The button follows the rows, so the dormant rows are what draw it. MUTANT
// "set_aside ignored" turns the first assertion red as the first case does.
test("PANELS draws RETRY for a dormant set-aside row when handed a handler, and for no other", () => {
  mount(rowsOf());
  eq(retryButton()?.textContent ?? null, RETRY_SET_ASIDE, "the button for a dormant set-aside row");
  eq(doc.querySelector('[data-panel-run="2-2"]')?.textContent, "2-2: set aside tonight",
     "its row says the panel is set aside");
  mount(rowsOf({ progress: WITHOUT }));
  eq(retryButton()?.textContent ?? null, null, "no button when nothing is set aside");
  mount(rowsOf(), { onRetry: undefined });
  eq(retryButton()?.textContent ?? null, null, "no button when the sheet hands no handler");
});

// ------------------------------------------------------------------ report
const total = passed + failed;
console.log(`w16DormantSetAside.test: ${passed}/${total} passed`);
for (const f of failures) console.log(f);
export const result = { passed, failed, total };
export default result;
