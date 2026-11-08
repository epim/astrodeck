// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w4Issue1184StarvedChild.test.ts - a guard on the test RUNNER itself
// (`ui/run-tests.mjs`), for issue #1184.
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/__tests__/w4Issue1184StarvedChild.test.ts
//
// pano `align.test.ts` was reported as freezing "after its own tally, before
// its import resolved" on both attempts of a CI run. It never froze. The CI
// log of that run (PR #1202, job 128130349807) shows the file's output ending
// at its twelfth case of seventeen, with no `align.test: 17/17 passed` line:
// a CPU-bound file (about 10 s alone on a desktop) that a 4-vCPU hosted runner,
// running 8 children at once, did not finish inside the 60 s wall clock. Two
// defects of the runner turned that into a "freeze after a clean tally":
//
//   1. `assertionStyle` read the word "ok" ANYWHERE in the output as the
//      throw-on-failure completion phrase. align's own PASS line "a blank
//      image gives ok: false" is enough, so a child still inside its cases
//      had a "tally": it was labelled import-pending and retried, as if it
//      had finished. A file that merely says "ok" in a case name and exits 0
//      without a tally would also have been scored as a pass.
//   2. CONCURRENCY was a fixed 8 whatever the machine had, so on 4 cores every
//      child got half a core (less, with the esbuild process each tsx child
//      starts) and the heaviest files overran the budget.
//
// The cases below pin both: the phrase is a line of its own, and the number of
// children follows the cores the process may use.

import assert from "node:assert/strict";
import { availableParallelism } from "node:os";

interface RunTestsModule {
  assertionStyle(text: string): boolean;
  timeoutPhase(r: { output: string; imported?: boolean; exiting?: boolean }): { id: string };
  shouldRetry(r: { timedOut: boolean; output: string; imported?: boolean; exiting?: boolean }): boolean;
  concurrencyFor(cores: number): number;
  CONCURRENCY: number;
}
const nodeImport = (s: string): Promise<unknown> =>
  (Function("m", "return import(m)") as (m: string) => Promise<unknown>)(s);
const RUN_TESTS_URL = new URL("../../run-tests.mjs", import.meta.url).href;
const { assertionStyle, timeoutPhase, shouldRetry, concurrencyFor, CONCURRENCY } =
  (await nodeImport(RUN_TESTS_URL)) as RunTestsModule;

let passed = 0;
let failed = 0;
function test(name: string, fn: () => void): void {
  try { fn(); passed++; console.log(`PASS ${name}`); }
  catch (e) { failed++; console.log(`FAIL ${name}\n  ${(e as Error).message.split(/\r?\n/)[0]}`); }
}

// The tail of align.test.ts's output as the CI runner captured it when it
// killed the child at 60 s: three of the cases that had printed, then the
// 200-pair diagnostic of the next case. No tally line.
const STARVED_ALIGN_OUTPUT = [
  "worst 0.0083 degrees over 27 predictions",
  "PASS after the prewarp, recovers injected yaw, pitch and roll up to 4 degrees within 0.05 with gain 0.7",
  "PASS a blank image gives ok: false, reason texture, textured: false",
  "PASS the thin-treeline fixture is ok, with yaw covariance at least 10x its pitch covariance",
  "  unrelated pairs, closure window: accepted 0 of 200 (0.0 %); PSR >= 7 on 9, ZNCC at most 0.430 among those",
  "PASS on 200 unrelated value-noise pairs alignPair accepts at most 1 (0.5 %)",
].join("\n");

test("the word ok inside a case name is not a completion phrase", () => {
  assert.equal(assertionStyle(STARVED_ALIGN_OUTPUT), false,
    "a child still inside its cases was read as having finished");
  assert.equal(assertionStyle("PASS the fixture is ok"), false);
  assert.equal(assertionStyle("it is OK to continue"), false);
});

test("a throw-on-failure file's own completion lines are still recognised", () => {
  assert.equal(assertionStyle("healthStrip.test.ts: all assertions passed"), true);
  assert.equal(assertionStyle("some setup\nall assertions passed"), true);
  assert.equal(assertionStyle("setup\nOK\n"), true);
  assert.equal(assertionStyle("factoryReset.test.ts: OK"), true);
});

test("a timeout in the middle of the cases reads as no-tally and is not retried", () => {
  const killed = { timedOut: true, output: STARVED_ALIGN_OUTPUT };
  assert.equal(timeoutPhase(killed).id, "no-tally",
    "the 60 s kill landed inside a case, and must be named that");
  assert.equal(shouldRetry(killed), false,
    "a starved child that never printed its tally is not a post-tally freeze");
});

test("a timeout after the real tally is still the retryable post-tally shape", () => {
  const frozen = { timedOut: true, output: `${STARVED_ALIGN_OUTPUT}\nalign.test: 17/17 passed` };
  assert.equal(timeoutPhase(frozen).id, "import-pending");
  assert.equal(shouldRetry(frozen), true);
});

test("concurrencyFor never runs more children than there are cores, nor more than 8, nor fewer than 1", () => {
  assert.equal(concurrencyFor(2), 2);
  assert.equal(concurrencyFor(4), 4);
  assert.equal(concurrencyFor(8), 8);
  assert.equal(concurrencyFor(24), 8);
  assert.equal(concurrencyFor(1), 1);
  assert.equal(concurrencyFor(0), 1);
});

test("the runner's own concurrency is the one for this machine's cores", () => {
  assert.equal(CONCURRENCY, concurrencyFor(availableParallelism()));
});

console.log(`w4Issue1184StarvedChild.test: ${passed}/${passed + failed} passed`);
if (failed > 0) process.exitCode = 1;
export const result = { passed, failed, total: passed + failed };
