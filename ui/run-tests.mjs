// Run every UI test file and fail the process if any assertion failed.
//
//   npm test
//
// Why this exists: the test files are plain tsx scripts. Each runs its own
// assertions, prints "N/M passed", and exports { passed, failed, total }. That
// design is deliberate (no framework, no jsdom) but it has a hole — a file that
// FAILS still exits zero, because printing a failure is not reporting one. So
// CI ran `npm run build` and nothing else: 83 test files gating nothing.
//
// Why a process per file, which is slower: these files were written to be run
// one at a time (`npx tsx <file>`), and they act like it.
//   * Several install fake globals — window, WebSocket, localStorage, fetch —
//     and never restore them. Imported into one shared process they stomp each
//     other: ws.test.ts passes alone and fails after another file has replaced
//     `window`. A runner that reports that is manufacturing failures.
//   * Four of them call process.exit() on failure, which would kill the whole
//     run partway and leave every later file silently unreported.
// Isolation is not a nicety here; without it the runner cannot be believed.
//
// Each child imports its file and exits on the file's own exported result, so
// the pass/fail signal is the file's, not a guess parsed out of its stdout.

import { readdirSync, statSync } from "node:fs";
import { execFile } from "node:child_process";
import { join, relative } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = fileURLToPath(new URL(".", import.meta.url));
//: enough to keep the box busy without oversubscribing a CI runner
const CONCURRENCY = 8;
const TIMEOUT_MS = 60_000;

function findTests(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) {
      if (name !== "node_modules") findTests(p, out);
    } else if (name.endsWith(".test.ts") || name.endsWith(".test.tsx")) {
      out.push(p);
    }
  }
  return out;
}

/** Recover the pass/fail counts from a file's printed summary.
 *
 *  Most files export `result`, which is exact. About forty do not — they only
 *  print their tally — and they are no less real for it, so the printed line is
 *  parsed rather than treated as a missing result. Two formats are in use:
 *
 *      name: 7/7 passed
 *      name: 25 passed, 0 failed
 *
 *  Returns null when NEITHER a result nor a recognisable tally is present. That
 *  is a genuine "cannot tell", and it fails — a file whose output we cannot
 *  read must never be scored as a pass.
 */
function parseCounts(text) {
  const both = /(\d+)\s+passed,\s*(\d+)\s+failed/i.exec(text);
  if (both) {
    return { passed: +both[1], failed: +both[2], total: +both[1] + +both[2] };
  }
  const ratio = /(\d+)\s*\/\s*(\d+)\s+passed/i.exec(text);
  if (ratio) {
    const [passed, total] = [+ratio[1], +ratio[2]];
    return { passed, failed: total - passed, total };
  }
  const only = /(\d+)\s+passed\b/i.exec(text);
  if (only) return { passed: +only[1], failed: 0, total: +only[1] };
  return null;
}

/** A handful of files use a throw-on-failure convention instead of a tally:
 *  they assert, and print "OK" or "all assertions passed" only if they reach
 *  the end. For those the CHILD'S EXIT CODE is the signal — a failed assertion
 *  throws, the import rejects, and the child dies nonzero. Recognised by the
 *  phrase so that a file which prints nothing still counts as unscorable. */
function assertionStyle(text) {
  return /\bOK\b|all assertions passed/i.test(text);
}

function runOne(file) {
  // The child imports the file, which runs its assertions, then reports the
  // file's own exported `result` when it has one.
  const url = pathToFileURL(file).href;
  const code = `
    const m = await import(${JSON.stringify(url)});
    const r = m.result;
    if (r && typeof r.failed === "number") {
      console.error("__COUNTS__" + JSON.stringify(r));
      process.exit(r.failed > 0 ? 1 : 0);
    }
    process.exit(0);   // no export: the parent reads the printed tally
  `;
  return new Promise((resolve) => {
    execFile(
      process.execPath,
      ["--import", "tsx", "--input-type=module", "--eval", code],
      { cwd: ROOT, timeout: TIMEOUT_MS, maxBuffer: 8 << 20 },
      (err, stdout, stderr) => {
        const output = [stdout, (stderr || "").replace(/__COUNTS__.*\n?/, "")]
          .join("").trim();
        const tagged = /__COUNTS__(\{.*\})/.exec(stderr || "");
        const counts = tagged ? JSON.parse(tagged[1]) : parseCounts(output);
        const timedOut = !!err && err.killed;
        // Exit 0 + a completion phrase = a throw-on-failure file that reached
        // its end. Counted as green but contributing no assertion count, since
        // it never told us one — better an undercount than an invented number.
        const byExit = !err && counts === null && assertionStyle(output);
        resolve({
          file,
          counts,
          byExit,
          // A crash (nonzero exit with no counts) is a failure even though the
          // file never got as far as saying so.
          ok: !timedOut && ((counts !== null && counts.failed === 0) || byExit),
          timedOut,
          output,
        });
      },
    );
  });
}

const files = findTests(join(ROOT, "src")).sort();
if (files.length === 0) {
  console.error("no test files found — the discovery walk is broken, which " +
                "would otherwise read as a clean run");
  process.exit(1);
}

const results = [];
let next = 0;
await Promise.all(
  Array.from({ length: Math.min(CONCURRENCY, files.length) }, async () => {
    while (next < files.length) results.push(await runOne(files[next++]));
  }),
);

let passed = 0, failed = 0;
const broken = [];
for (const r of results) {
  passed += r.counts?.passed ?? 0;
  failed += r.counts?.failed ?? 0;
  if (!r.ok) {
    const why = r.timedOut ? `timed out after ${TIMEOUT_MS / 1000}s`
      : r.counts ? `${r.counts.failed} failed`
        : "no pass/fail tally in its output — cannot be scored";
    broken.push({ name: relative(ROOT, r.file), why, output: r.output });
  }
}

console.log(`\n${"=".repeat(64)}`);
console.log(`${files.length} files · ${passed} passed · ${failed} failed`);
if (broken.length) {
  for (const b of broken) {
    console.error(`\n--- ${b.name}: ${b.why}`);
    if (b.output) console.error(b.output);
  }
  console.error(`\n${broken.length} file(s) not green`);
  process.exit(1);
}
console.log("all green");
