// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w7SetAsideForNow.test.ts - PANELS words a set-aside that may still expire
// tonight differently from one that lasts the rest of the night (#573, #534
// follow-up; backlog ruling D-07, owner-approved 2026-09-30, WP-49).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/framing/__tests__/w7SetAsideForNow.test.ts   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE RULING. `_group_state` (engine.py) now publishes `for_now` on every
// `state.group.set_aside` record (always sent: `_awaiting_expiry`'s answer),
// alongside `kind`. `panelStateOf` (flowRunState.ts) carries `for_now` into
// `PanelRunState`'s `forNow`; PanelsSection's `runLine` is graded here on
// `forNow` ALONE, never on `kind`, which is published for other readers
// (spec: the two set-aside wordings) but does not drive PANELS' words.
//
// WHAT IS GRADED. `runLine`/`runRowText`: FOR NOW reads "set aside for now:
// <reason>; tried once more tonight"; TONIGHT reads "set aside tonight:
// <reason>" exactly as before #573. Both with and without a reason, and
// `runRowText`'s "do not name the panel twice" rule (#509) still keys off
// the reason alone, so a for-now reason that names its panel is not
// prefixed, tail and all.
//
// MUTANT "forNow ignored" (PanelsSection.tsx `runLine`'s set-aside branch
// given the literal `SET_ASIDE_TONIGHT` and an empty tail, `run.forNow`
// never read), run in a private scratch copy of ui/ (scratchpad w7-WP49-mut,
// from a byte backup of PanelsSection.tsx, restored and sha256-compared
// after), 2026-10-01. Each `test()` call stops at its first failed
// assertion, so only the first case of each is shown red; the file's other
// assertions were not reached. Observed verbatim:
//   w7SetAsideForNow.test: 0/2 passed
//   x runLine: FOR NOW reads differently from TONIGHT, with and without a reason: a for-now reason
//       expected "set aside for now: centring failed on 2-2 on 3 consecutive visits: plate solve
//       failed, used raw GoTo; tried once more tonight"
//       got      "set aside tonight: centring failed on 2-2 on 3 consecutive visits: plate solve
//       failed, used raw GoTo"
//   x runRowText: the for-now tail never duplicates the panel label, even when the reason names it:
//       a for-now reason naming its panel stands alone, tail included
//       expected "set aside for now: centring failed on 2-2 on 3 consecutive visits: plate solve
//       failed, used raw GoTo; tried once more tonight"
//       got      "set aside tonight: centring failed on 2-2 on 3 consecutive visits: plate solve
//       failed, used raw GoTo"

import { runLine, runRowText, SET_ASIDE_FOR_NOW, SET_ASIDE_TONIGHT } from "../sections/PanelsSection";
import type { PanelRunState } from "../../flowRunState";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg}\n    expected ${JSON.stringify(want)}\n    got      ${JSON.stringify(got)}`);
}
function assert(cond: unknown, msg: string): void { if (!cond) throw new Error(msg); }

const REASON = "centring failed on 2-2 on 3 consecutive visits: plate solve failed, used raw GoTo";

function aside(reason: string, forNow: boolean): PanelRunState {
  return { kind: "set_aside", reason, forNow };
}

test("runLine: FOR NOW reads differently from TONIGHT, with and without a reason", () => {
  eq(runLine(aside(REASON, true)), `${SET_ASIDE_FOR_NOW}: ${REASON}; tried once more tonight`,
    "a for-now reason");
  eq(runLine(aside(REASON, false)), `${SET_ASIDE_TONIGHT}: ${REASON}`, "a tonight reason");
  eq(runLine(aside("", true)), `${SET_ASIDE_FOR_NOW}; tried once more tonight`,
    "a for-now record with no reason");
  eq(runLine(aside("", false)), SET_ASIDE_TONIGHT, "a tonight record with no reason");
  // The two words are never confused with one another.
  assert((SET_ASIDE_FOR_NOW as string) !== (SET_ASIDE_TONIGHT as string),
    "premise: the two words differ");
  assert(!runLine(aside(REASON, true))!.startsWith(SET_ASIDE_TONIGHT),
    "a for-now line does not start with the tonight words");
  assert(!runLine(aside(REASON, false))!.includes("tried once more"),
    "a tonight line carries no retry tail");
});

test("runRowText: the for-now tail never duplicates the panel label, even when the reason names it", () => {
  // A for-now reason that names its panel stands alone: no "2-2:" prefix,
  // the tail still appended (#509's rule, unmoved by #573).
  eq(runRowText("2-2", aside(REASON, true)),
    `${SET_ASIDE_FOR_NOW}: ${REASON}; tried once more tonight`,
    "a for-now reason naming its panel stands alone, tail included");
  // A for-now reason that does NOT name its panel keeps the label, before
  // the for-now words.
  const guiding = "guiding did not start on any panel of M31";
  eq(runRowText("2-2", aside(guiding, true)),
    `2-2: ${SET_ASIDE_FOR_NOW}: ${guiding}; tried once more tonight`,
    "a for-now reason naming no panel keeps the label");
  // The tonight wording, unchanged from before #573.
  eq(runRowText("2-2", aside(REASON, false)), `${SET_ASIDE_TONIGHT}: ${REASON}`,
    "a tonight reason naming its panel stands alone");
});

// ------------------------------------------------------------------ report
const total = passed + failed;
console.log(`w7SetAsideForNow.test: ${passed}/${total} passed`);
for (const f of failures) console.log(f);
export const result = { passed, failed, total };
export default result;
