// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w2_5RunCopyOwesMore.test.ts - a finished flow session that owes more shows
// CONTINUE before the press (#778, #1112, WP 2.5).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/__tests__/w2_5RunCopyOwesMore.test.ts   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// `runCopy` read CONTINUE only for a dormant session, so a finished flow
// edited to owe more showed RUN and asked "Add to the finished session?" after
// the press. The progress route now says `owes_more` on a complete session
// (server `Session.owes_more`, with the shared-step test `reopen_report`
// adds), and runCopy reads that and nothing else: it never works the answer
// out from the blocks, because a re-framed block owes frames and shares no
// step, and its press starts fresh.
//
// NAMED MUTANT "owes_more ignored": runCopy.ts's `continues` made to read
// `s.status === "dormant"` alone (the pre-#778 rule). RED, observed below the
// test that grades it.

// @ts-ignore  no @types/node guaranteed; tsx supplies fs at runtime
import { readFileSync } from "node:fs";

import { runCopy } from "../runCopy";
import type { FlowProgress } from "../../../lib/flowsApi";

// ---------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];

function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function eq<T>(got: T, want: T, msg: string): void {
  const g = JSON.stringify(got);
  const w = JSON.stringify(want);
  if (g !== w) throw new Error(`${msg}\n    expected ${w}\n    got      ${g}`);
}

// ---------------------------------------------------------------- fixtures
const FIXTURE = "../../../../../server/tests/fixtures/flow_progress_continue.json";
const DORMANT = (JSON.parse(readFileSync(new URL(FIXTURE, import.meta.url), "utf8") as string)
  .response) as FlowProgress;
if (!DORMANT?.session) throw new Error("the recorded progress answer holds no session");

/** The recorded answer with its session in another state, `extra` merged in.
 *  The route sends `continue_night` for a dormant session only, so another
 *  state has none. */
const asSession = (status: string, extra: Record<string, unknown> = {}): FlowProgress => {
  const { continue_night: _night, ...rest } = DORMANT.session!;
  return {
    ...DORMANT,
    session: { ...rest, status, ...extra } as unknown as FlowProgress["session"],
  };
};

const counts = (() => {
  const c = runCopy("M31", DORMANT, false);
  return { banked: c.banked, total: c.total };
})();
if (counts.banked === null || counts.total === null) {
  throw new Error("premise: the recorded dormant answer has readable counts");
}

// ---------------------------------------------------------------- tests
// MUTANT "owes_more ignored". Observed:
//   x a complete session that owes more reads CONTINUE with the counts and no night, before the press
//     expected ["CONTINUE",null,"CONTINUE M31 (<banked>/<total> subs)"]
//     got      ["RUN",null,"RUN"]
test("a complete session that owes more reads CONTINUE with the counts and no night, before the press", () => {
  const owing = runCopy("M31", asSession("complete", { owes_more: true }), false);
  eq([owing.verb, owing.night, owing.text],
    ["CONTINUE", null, `CONTINUE M31 (${counts.banked}/${counts.total} subs)`],
    "complete + owes_more");
  eq([owing.banked, owing.total], [counts.banked, counts.total], "the counts are the blocks'");
});

test("a complete session carries no continue_night, so the button prints the counts alone", () => {
  const owing = runCopy("M31", asSession("complete", { owes_more: true }), false);
  eq([owing.verb, owing.night, owing.detail],
    ["CONTINUE", null, `(${counts.banked}/${counts.total} subs)`], "complete, no continue_night");
});

test("a complete session that does not say it owes more reads RUN", () => {
  for (const extra of [{}, { owes_more: false }, { owes_more: "yes" }, { owes_more: 1 }]) {
    const c = runCopy("M31", asSession("complete", extra), false);
    eq([c.verb, c.text], ["RUN", "RUN"], `complete + ${JSON.stringify(extra)}`);
  }
});

test("owes_more changes nothing for an active session, and STOP wins while live", () => {
  eq(runCopy("M31", asSession("active", { owes_more: true }), false).verb, "RUN", "active, not live here");
  eq(runCopy("M31", asSession("complete", { owes_more: true }), true).verb, "STOP", "live");
});

test("a dormant session reads exactly as before, with its night", () => {
  const c = runCopy("M31", DORMANT, false);
  eq(c.verb, "CONTINUE", "dormant");
  eq(c.night, DORMANT.session!.continue_night ?? null, "the route's own night");
  eq(c.detail, DORMANT.session!.continue_night === undefined
    ? `(${counts.banked}/${counts.total} subs)`
    : `(night ${DORMANT.session!.continue_night}, ${counts.banked}/${counts.total} subs)`, "detail");
});

test("an unreadable count prints no parenthetical but still reads CONTINUE for a complete session that owes more", () => {
  const broken = asSession("complete", { owes_more: true });
  broken.blocks = [{ ...broken.blocks[0], banked: "x" as unknown as number }, ...broken.blocks.slice(1)];
  const c = runCopy("M31", broken, false);
  eq([c.verb, c.detail, c.text], ["CONTINUE", "", "CONTINUE M31"], "unreadable counts");
});

// ------------------------------------------------------------------ tally
const total = passed + failed;
console.log(`w2_5RunCopyOwesMore.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) (globalThis as any).process.exitCode = 1;
export const result = { passed, failed, total };
export default result;
