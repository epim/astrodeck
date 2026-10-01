// w4LoopWireConsumedCommentMatches.test.ts - flowsApplyFraming's comment on
// a pass wire left into a 1x1 block matches the compile's actual
// consumed-wire behaviour (#397 item 4; backlog plan WP-36 (b),
// docs/superpowers/plans/2026-09-30-open-issue-backlog.md).
//
//   Run:  node --import tsx src/components/flows/__tests__/w4LoopWireConsumedCommentMatches.test.ts   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT. The comment named the case (a pass wire left in a one-panel
// block after DONE turns a looped mosaic into a single target) but did not
// say what the server's compile actually does with it: `doctor.py`'s M4 rule
// treats it as a NOTE the compile CONSUMES (`one_panel_pass_wires`) -- no
// refusal, no loss, and Run asks nothing about it (S4 orchestrator ruling 3).
// An operator reading only the comment could not tell that leaving the wire
// in place is harmless, which is exactly the case the comment exists to
// explain.
//
// THE FIX. The comment now says, in words the server's own doctor.py M4
// comment uses ("consumes", "no rule and no loss"): the compile CONSUMES the
// wire as a note, with no rule, no loss and no question from Run.
//
// Mutant "the consumed-wire sentence reverted" run in a private scratch copy
// of ui/ under the session scratchpad, restored byte-identically (sha256
// checked) afterwards, never in the shared tree. The failure it produced is
// quoted verbatim in the case below.

/* eslint-disable @typescript-eslint/no-explicit-any */

interface NodeFsLike { readFileSync(path: string, encoding: string): string; }
const nodeImport = (s: string): Promise<unknown> =>
  (Function("m", "return import(m)") as (m: string) => Promise<unknown>)(s);
const fs = (await nodeImport("node:fs")) as NodeFsLike;
const toPath = (u: URL): string => decodeURIComponent(u.pathname).replace(/^\/([A-Za-z]:)/, "$1");
const SLICE = toPath(new URL("../flowsSlice.ts", import.meta.url));

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

/** The comment block in `flowsApplyFraming`, as a single line (newlines and
 *  each line's leading `//` collapsed to one space), so wording that wraps
 *  across source lines can still be matched as one sentence. Anchored on the
 *  "must send `false`" sentence, which is unique in the file and precedes the
 *  comment's final clause - the one #397 item 4 found stale. */
function loopWireComment(): string {
  const text = fs.readFileSync(SLICE, "utf8");
  const at = text.indexOf("that makes a looped mosaic a single target must send");
  assert(at >= 0, "flowsApplyFraming's loop-wire comment (\"must send `false`\") was not found at all");
  const end = text.indexOf("const edges = withLoop(", at);
  assert(end > at, "the comment block did not end before `const edges = withLoop(`");
  return text.slice(at, end)
    .split("\n").map((l) => l.replace(/^\s*\/\/\s?/, ""))
    .join(" ").replace(/\s+/g, " ").trim();
}

test("the comment still names the case #397 item 4 is about", () => {
  // Guards the anchor itself: if this ever goes missing, every case below
  // would vacuously pass on a comment that no longer exists.
  const c = loopWireComment();
  assert(c.includes("1x1 block"), `the comment no longer names the 1x1-block case: ${c}`);
});

test("the comment says the compile CONSUMES the wire, with no rule, no loss and no question from Run (#397 item 4)", () => {
  // MUTANT "the consumed-wire sentence reverted" (the comment's last clause
  // put back to the stale form #397 item 4 found: "the doctor's M4 note
  // (spec 1.4 \"As built\", #349)." with none of "consumes", "no rule",
  // "no loss" or "Run asks"). Observed, verbatim (1 failed; this case):
  //   x the comment says the compile CONSUMES the wire, with no rule, no loss and no question from Run (#397 item 4): the comment does not say the compile CONSUMES the wire: that makes a looped mosaic a single target must send `false`: left in place, the wire is a pass wire into a 1x1 block, the doctor's M4 note (spec 1.4 "As built", #349).
  const c = loopWireComment();
  assert(/compile CONSUMES/.test(c), `the comment does not say the compile CONSUMES the wire: ${c}`);
  assert(/no rule, no loss/.test(c), `the comment does not say "no rule, no loss": ${c}`);
  assert(/Run\s+asks\s+nothing/.test(c), `the comment does not say Run asks nothing: ${c}`);
  // The citation stays - #397 item 4 fixed the consequence the sentence
  // drew, not the ruling and spec reference it already carried correctly.
  assert(c.includes("ruling 3"), `the comment dropped its citation of S4 orchestrator ruling 3: ${c}`);
  assert(c.includes("#349"), `the comment dropped its citation of #349: ${c}`);
});

// ------------------------------------------------------------------ report
console.log(`\nw4LoopWireConsumedCommentMatches: ${passed} passed, ${failed} failed`);
if (failures.length) { failures.forEach((f) => console.log(f)); process.exit(1); }
export default { passed, failed, total: passed + failed };
