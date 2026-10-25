// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// wp15uRunnerReport.test.ts - what run-tests.mjs PRINTS about a broken file
// (#753), and that a silent or dishonestly-green child is named and timed
// (#754, #914; WP 1.5u).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/__tests__/wp15uRunnerReport.test.ts   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// #753. `main` printed each broken file's whole captured output, up to the
// runner's 8 MiB maxBuffer, so one file that floods its stdout buried the
// reason line under megabytes in the CI log. A broken file's block is now
// bounded (OUTPUT_CAP_BYTES): when its output is over the cap, the block
// opens with a line saying how much was cut and where the whole of it was
// written, then shows the failing cases and the last lines. The whole output
// goes to a file whose path is printed, so nothing is lost by the cut.
//
// #754, #914. A direct run of a test file once sat idle for four minutes at
// 0.3 s of CPU, and another once exited 1 while printing "2/2 passed"; neither
// reproduced (the cause is NOT found: see the WP 1.5u report). What the
// runner owes the next occurrence is that it is named and timed: a silent
// child is killed at its timeout and the reason says how long it ran, and a
// child that exits nonzero after a clean tally is a broken file, never a pass.
//
// The runner's cap and report are exercised on real child output (a flood
// fixture run through `runOne`), not only on strings, because a function that
// caps a string it is handed says nothing about what `runOne` captured.
//
// NAMED MUTANTS (each applied to run-tests.mjs from a byte backup and restored):
//
// "the cap lets everything through" (`capOutput`'s `if (byteLength <=
// OUTPUT_CAP_BYTES)` made `if (true)`). Observed red: the over-cap cases, for
// example "an over-cap output is cut to the cap".
//
// "the report drops the elapsed time" (`brokenReason`'s `elapsed ...` clause
// removed). Observed red: "a silent child is killed at its timeout and the
// reason names how long it ran".
//
// "a clean tally hides a nonzero exit" (`computeOk`'s `if (err &&
// !graceKilled) return false;` removed). Observed red: "a child that prints a
// clean tally and then exits 1 is broken, and the reason says it exited 1".

import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

interface RunOneResult {
  file: string;
  counts: { passed: number; failed: number; total: number } | null;
  ok: boolean;
  timedOut: boolean;
  output: string;
  exitCode: number | string | null;
  elapsedMs: number;
  timeoutMs: number;
}
interface RunTestsModule {
  OUTPUT_CAP_BYTES: number;
  capOutput(output: string, fullPath: string): { text: string; cut: boolean };
  formatBroken(b: { name: string; why: string; output: string }, outDir: string): string;
  runOne(file: string, timeoutMs?: number): Promise<RunOneResult>;
  brokenReason(r: RunOneResult): string;
}
const nodeImport = (s: string): Promise<unknown> =>
  (Function("m", "return import(m)") as (m: string) => Promise<unknown>)(s);
const RUN_TESTS_URL = new URL("../../run-tests.mjs", import.meta.url).href;
const { OUTPUT_CAP_BYTES, capOutput, formatBroken, runOne, brokenReason } =
  (await nodeImport(RUN_TESTS_URL)) as RunTestsModule;

// ---------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
async function testAsync(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
const bytes = (s: string): number => Buffer.byteLength(s, "utf8");

// ------------------------------------------------------- the output shapes
const FIRST_FAILURE = "  x the first broken case: boom";
const LAST_FAILURE = "  x the last broken case: bang";
const LAST_LINE = "flood.test: 1/3 passed";
/** About 3 MiB of noise with a failure near each end and a marker in the
 *  middle that no bounded report may carry. */
function floodOutput(): string {
  const lines: string[] = [];
  for (let i = 0; i < 30_000; i++) {
    lines.push(i === 5 ? FIRST_FAILURE : i === 15_000 ? "MIDDLE-NOISE-MARKER" : `noise line ${i} ${"n".repeat(90)}`);
  }
  lines.push(LAST_FAILURE, LAST_LINE);
  return lines.join("\n");
}

const FULL_PATH = join(tmpdir(), "astrodeck-wp15u-full.log");

test("a small output is printed whole and writes nothing", () => {
  const small = `a.test: 1/2 passed\n${FIRST_FAILURE}`;
  const r = capOutput(small, FULL_PATH);
  assert(!r.cut && r.text === small, `a small output must pass through unchanged, got ${JSON.stringify(r)}`);
});

test("an over-cap output is cut to the cap", () => {
  const out = floodOutput();
  assert(bytes(out) > 20 * OUTPUT_CAP_BYTES, `the fixture is too small to mean anything: ${bytes(out)} bytes`);
  const r = capOutput(out, FULL_PATH);
  assert(r.cut, "an output over the cap must be reported as cut");
  assert(bytes(r.text) <= OUTPUT_CAP_BYTES,
    `the printed block must fit the cap (${OUTPUT_CAP_BYTES}), got ${bytes(r.text)} bytes`);
});

test("the cut block opens with the reason line, then the failing cases and the last lines", () => {
  const out = floodOutput();
  const lines = capOutput(out, FULL_PATH).text.split("\n");
  assert(/^\[output cut: /.test(lines[0]) && lines[0].includes(String(bytes(out)))
    && lines[0].includes(FULL_PATH),
    `the first line must say how much was cut and where the whole is, got ${JSON.stringify(lines[0])}`);
  const text = lines.join("\n");
  assert(text.includes(FIRST_FAILURE) && text.includes(LAST_FAILURE),
    "both failing cases must survive the cut");
  assert(text.includes(LAST_LINE), "the tally line at the end must survive the cut");
  assert(!text.includes("MIDDLE-NOISE-MARKER"), "the middle of the flood must not be printed");
});

test("one enormous line is bounded too", () => {
  const out = `x.test: 0/1 passed\n${"y".repeat(5 << 20)}`;
  const r = capOutput(out, FULL_PATH);
  assert(r.cut && bytes(r.text) <= OUTPUT_CAP_BYTES,
    `a single 5 MiB line must be cut to the cap, got ${bytes(r.text)} bytes (cut=${r.cut})`);
});

// ------------------------------------------------------------ real children
let dir: string | undefined;
try {
  dir = mkdtempSync(join(tmpdir(), "astrodeck-wp15u-"));
  const write = (name: string, lines: string[]): string => {
    const p = join(dir!, name);
    writeFileSync(p, lines.join("\n") + "\n", "utf8");
    return p;
  };
  const outDir = join(dir, "full-output");

  const flooding = write("flood.test.mjs", [
    `const out = ${JSON.stringify(floodOutput())};`,
    "await new Promise((r) => process.stdout.write(out + \"\\n\", r));",
    "export const result = { passed: 1, failed: 2, total: 3 };",
  ]);
  const clean = write("clean.test.mjs", [
    'console.log("clean.test: 1/1 passed");',
    "export const result = { passed: 1, failed: 0, total: 1 };",
  ]);
  // The #914 shape: the tally is clean and the process then exits 1. The
  // runner's own child ends with `process.exit(0)`, and an 'exit' listener
  // that sets the exit code is what turns that into 1.
  const cleanThenExit1 = write("cleanThenExit1.test.mjs", [
    'console.log("cleanThenExit1.test: 2/2 passed");',
    'process.on("exit", () => { process.exitCode = 1; });',
  ]);
  // The #754 shape: nothing printed, the import never returns, the process
  // alive and idle (the timer only keeps it from exiting on an unsettled
  // top-level await).
  const silent = write("silent.test.mjs", [
    "setInterval(() => {}, 1000);",
    "await new Promise(() => {});",
  ]);

  await testAsync("a real child that floods its output is reported in a block bounded by the cap, and the whole of it is in the printed file", async () => {
    const r = await runOne(flooding, 60_000);
    assert(bytes(r.output) > 20 * OUTPUT_CAP_BYTES && !r.ok,
      `the flood child must produce a broken file with a large output: ok=${r.ok} bytes=${bytes(r.output)}`);
    const name = "src/flood.test.mjs";
    const block = formatBroken({ name, why: brokenReason(r), output: r.output }, outDir);
    const lines = block.split("\n").filter((l) => l !== "");
    assert(lines[0] === `--- ${name}: ${brokenReason(r)}`,
      `the block must open with the file and its reason, got ${JSON.stringify(lines[0])}`);
    assert(bytes(block) <= OUTPUT_CAP_BYTES + 4096,
      `the block must stay near the cap, got ${bytes(block)} bytes`);
    const pathLine = /the whole of it is in (.+)\]/.exec(block);
    assert(pathLine !== null, `the block must print the path of the full output: ${block.slice(0, 400)}`);
    const full = (pathLine as RegExpExecArray)[1];
    assert(existsSync(full), `the printed path does not exist: ${full}`);
    assert(readFileSync(full, "utf8") === r.output, "the file must hold the whole captured output");
  });

  await testAsync("a real child with a short output writes no file", async () => {
    const r = await runOne(clean, 60_000);
    const before = existsSync(outDir);
    const block = formatBroken({ name: "src/clean.test.mjs", why: "x", output: r.output }, outDir);
    assert(!block.includes("[output cut"), `a short output must not be cut: ${block}`);
    assert(existsSync(outDir) === before, "a short output must not create the full-output directory");
  });

  await testAsync("a silent child is killed at its timeout and the reason names how long it ran", async () => {
    const r = await runOne(silent, 2000);
    assert(r.timedOut && !r.ok, `the silent child must be reported timed out: timedOut=${r.timedOut} ok=${r.ok}`);
    assert(r.elapsedMs >= 1900 && r.elapsedMs < 30_000,
      `elapsedMs must be the real wall time of the run, got ${r.elapsedMs}`);
    assert(r.timeoutMs === 2000, `the timeout the run used must be on the result, got ${r.timeoutMs}`);
    const why = brokenReason(r);
    assert(/^timed out after 2s \(elapsed \d+\.\ds; /.test(why) && /no tally/.test(why),
      `the reason must carry the timeout, the elapsed time and the phase, got: ${why}`);
  });

  await testAsync("a child that prints a clean tally and then exits 1 is broken, and the reason says it exited 1", async () => {
    const r = await runOne(cleanThenExit1, 60_000);
    assert(r.counts !== null && r.counts.failed === 0 && r.exitCode === 1,
      `the fixture must print 2/2 and exit 1: counts=${JSON.stringify(r.counts)} exitCode=${r.exitCode}`);
    assert(!r.ok, "a nonzero exit after a clean tally must never be scored as a pass");
    assert(brokenReason(r) === "0 failed, but the child exited with code 1",
      `the reason must say the child exited 1, got: ${brokenReason(r)}`);
  });
} finally {
  if (dir) rmSync(dir, { recursive: true, force: true });
}

console.log(`wp15uRunnerReport.test: ${passed}/${passed + failed} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total: passed + failed };
