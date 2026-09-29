// panelOrderFixture.test.ts - the Target modal's copy of the mosaic panel
// order graded against the SERVER'S table (#412 item 1; spec 2026-09-23
// flows mosaic, 5.2 and 2.4 PANELS).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/framing/__tests__/panelOrderFixture.test.ts   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHY A SHARED FIXTURE. `sequence/panel_order.py` orders the panels the run
// visits. The modal's PANELS section numbers its rows, and the sky its panel
// labels, with a TypeScript copy of two of its keys, the snake index and
// least complete first (PanelsSection.tsx `snakeIndex`, `panelRows`), since
// the modal has the progress route's counts and neither visit times nor the
// site. Two copies of one rule drift the day one is edited, and the numbers
// on screen would then not be the order the run uses while every test on
// each side stays green. So both copies are graded against one table,
// server/tests/fixtures/panel_order_cases.json, READ, NOT COPIED:
// test_panel_order_fixture.py grades it against `order_panels` and
// `snake_index`, and this file against `panelRows` and `snakeIndex`. Do not
// replace the read with a literal fixture.
//
// WHAT IS GRADED, per case: the shot panels' labels in the order `panelRows`
// numbers them (its `order`, 1 to n), and that the rows come out in that
// order with the skipped panels after; per snake case, `snakeIndex`. A key
// the fixture grows that this file does not read fails the key check, so a
// new column cannot pass here by being skipped.
//
// Every mutant below was run in a private scratch copy of ui/ (scratchpad
// S5-TONIGHT-mut), never in the shared tree (#254), and the failure it
// produced is quoted verbatim.

/* eslint-disable @typescript-eslint/no-explicit-any */

// @ts-ignore  node built-ins; tsx supplies them at runtime
import { readFileSync } from "node:fs";

import { ORDERS, panelRows, snakeIndex } from "../sections/PanelsSection";
import type { FlowProgressBlock } from "../../../../lib/flowsApi";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: unknown, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (JSON.stringify(got) !== JSON.stringify(want)) {
    throw new Error(`${msg}: expected ${JSON.stringify(want)}, got ${JSON.stringify(got)}`);
  }
}

interface SnakeCase { cols: number; row0: number; col0: number; index: number }
interface PanelCount { row: number; col: number; banked: number; total: number }
interface OrderCase {
  name: string; rows: number; cols: number; order: string; policy: string;
  skip: [number, number][]; panels: PanelCount[]; expect: string[];
}

const FIXTURE_REL = "../../../../../../server/tests/fixtures/panel_order_cases.json";
function readFixture(): { snake: SnakeCase[]; cases: OrderCase[] } & Record<string, unknown> {
  let text: string;
  try {
    text = readFileSync(new URL(FIXTURE_REL, import.meta.url), "utf8") as string;
  } catch (e) {
    throw new Error(`cannot read ${FIXTURE_REL}, the server's panel-order table: ${(e as Error).message}`);
  }
  return JSON.parse(text);
}
const FX = readFixture();

// The keys test_panel_order_fixture.py checks, the same lists.
const TOP_KEYS = ["about", "cases", "snake"];
const SNAKE_KEYS = ["col0", "cols", "index", "row0"];
const CASE_KEYS = ["cols", "expect", "name", "order", "panels", "policy", "rows", "skip"];
const PANEL_KEYS = ["banked", "col", "row", "total"];
const keys = (o: object): string[] => Object.keys(o).sort();

/** The progress route's block for a case: every listed panel at its 0-based
 *  row and col with its banked and total subs, and the case's grid, so
 *  `panelRows` reads the counts as the sheet does. */
function progressOf(c: OrderCase): FlowProgressBlock {
  return {
    node_id: "t", name: "M31", kind: "target", banked: 0, owed: 0, total: 0,
    grid: { rows: c.rows, cols: c.cols },
    panels: c.panels.map((p) => ({
      target_id: `p${p.row}-${p.col}`, name: `M31 ${p.row}-${p.col}`,
      row: p.row - 1, col: p.col - 1, banked: p.banked,
      owed: p.total - p.banked, total: p.total, steps: [],
    })),
    skipped: [],
  };
}

test("the fixture holds only the keys both sides grade", () => {
  eq(keys(FX), TOP_KEYS, "top-level keys");
  for (const s of FX.snake) eq(keys(s), SNAKE_KEYS, "a snake case's keys");
  for (const c of FX.cases) {
    eq(keys(c), CASE_KEYS, `${c.name}: keys`);
    for (const p of c.panels) eq(keys(p), PANEL_KEYS, `${c.name}: a panel's keys`);
    assert(ORDERS.includes(c.order), `${c.name}: ${JSON.stringify(c.order)} is not one of the TARGET's ORDER options ${JSON.stringify(ORDERS)}`);
  }
  assert(FX.snake.length > 0 && FX.cases.length > 0, "premise: the fixture has cases");
});

// MUTANT "snake reversed on odd rows" (PanelsSection.tsx `snakeIndex`:
// `row0 % 2 === 0` made `row0 % 2 === 1`, so even rows run east to west and
// odd rows west to east). Observed (the file: 4/10 passed), the ten snake
// cases a reversal can move, the message beginning:
//   x snakeIndex is the server's snake index: snakeIndex(0, 1) in a grid 2
//     wide: expected 1, got 0; snakeIndex(1, 0) in a grid 2 wide: expected
//     3, got 2; snakeIndex(1, 1) in a grid 2 wide: expected 2, got 3; ...
test("snakeIndex is the server's snake index", () => {
  const wrong: string[] = [];
  for (const s of FX.snake) {
    const got = snakeIndex(s.row0, s.col0, s.cols);
    if (got !== s.index) wrong.push(`snakeIndex(${s.row0}, ${s.col0}) in a grid ${s.cols} wide: expected ${s.index}, got ${got}`);
  }
  assert(wrong.length === 0, wrong.join("; "));
});

// MUTANT "snake reversed on odd rows", as above. Observed, five of the
// eight cases, as on the server (the part-banked least-complete case, the
// 2x2 with a skip and the column of 3 tie only where the reversed snake
// still orders the same way), the first:
//   x 3x2 on night one, least complete first: everything ties, so the snake
//     decides (5.2's worked example): the run order panelRows numbers:
//     expected ["1-1","1-2","1-3","2-3","2-2","2-1"], got
//     ["1-3","1-2","1-1","2-1","2-2","2-3"]
// The server copy under the same mutant fails test_panel_order_fixture.py,
// and this file stays 10/10: each side goes red only where it is mutated.
for (const c of FX.cases) {
  test(c.name, () => {
    const rows = panelRows({
      rows: c.rows, cols: c.cols, skip: c.skip, progress: progressOf(c),
      answerPanels: null, order: c.order,
    });
    const live = rows.filter((r) => r.order !== null);
    const numbered = [...live].sort((a, b) => (a.order as number) - (b.order as number)).map((r) => r.label);
    eq(numbered, c.expect, "the run order panelRows numbers");
    // The rows themselves come out in that order, the skipped ones after,
    // each skipped and unnumbered: the section draws them in array order.
    eq(rows.slice(0, live.length).map((r) => r.label), c.expect, "the rows' own order");
    const skipped = rows.slice(live.length);
    eq(skipped.map((r) => [r.row, r.col]), c.skip, "the skipped rows, after the shot ones");
    assert(skipped.every((r) => r.skipped && r.order === null), "a skipped row is marked skipped and has no number");
  });
}

// ------------------------------------------------------------------ report
const total = passed + failed;
console.log(`panelOrderFixture.test: ${passed}/${total} passed`);
for (const f of failures) console.log(f);
export const result = { passed, failed, total };
export default result;
