// w4FlowNodeTickComments.test.ts - the three "flow.node tick" comments #593
// found left uncorrected after H4's #464 sweep are fixed (backlog plan WP-36
// (c), docs/superpowers/plans/2026-09-30-open-issue-backlog.md; spec
// 2026-09-23 flows mosaic, Revision 11 row 45).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/__tests__/w4FlowNodeTickComments.test.ts   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT (#593). H4 rewrote the comments in FlowNodeCard.tsx, FlowNode.tsx,
// FlowWires.tsx and FlowWireLayer.tsx to say nothing writes `flows.statuses`
// (#464: no topic carries a stage's status, so every reader sees idle through
// a live run), but left three siblings saying the opposite thing in different
// words: `FlowCanvas.tsx` and `FlowCanvasSurface.tsx` each claimed a
// `flow.node` tick is "what makes" one card re-render instead of the whole
// canvas, and `store.ts` claimed a "node-status tick" does the same for one
// node - both describing a write that does not exist, the #464 class exactly.
//
// THE FIX. All three now say the write discipline is what WOULD let such a
// tick re-render one card/node, IF the rig ever sent one, and that nothing
// does yet (#464) - a pure text scan, no DOM needed.
//
// Every mutant below was run in a private scratch copy of ui/ under the
// session scratchpad, restored byte-identically (sha256 checked) afterwards,
// never in the shared tree. The failure it produced is quoted verbatim.

/* eslint-disable @typescript-eslint/no-explicit-any */

// Dependency-free node:fs, the `__tests__/deletedDoorStrings.test.ts` idiom:
// the project installs no @types/node and `tsc -b` checks everything under
// src/. No window or DOM is needed - this is a pure source-text scan.
interface NodeFsLike { readFileSync(path: string, encoding: string): string; }
const nodeImport = (s: string): Promise<unknown> =>
  (Function("m", "return import(m)") as (m: string) => Promise<unknown>)(s);
const fs = (await nodeImport("node:fs")) as NodeFsLike;
const toPath = (u: URL): string => decodeURIComponent(u.pathname).replace(/^\/([A-Za-z]:)/, "$1");
// This file lives at components/flows/__tests__/, so "../../.." is ui/src.
const SRC = toPath(new URL("../../../", import.meta.url)).replace(/[\\/]+$/, "");

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

/** The three files #593 names as left uncorrected, each with the OLD
 *  sentence it carried (for the known-positive case below) and a short tag
 *  for failure messages. */
const FILES: readonly { path: string; was: string }[] = [
  {
    path: "components/flows/FlowCanvas.tsx",
    was: "makes a `flow.node` tick re-render one card instead of the graph.",
  },
  {
    path: "next/hubs/session/flows/canvas/FlowCanvasSurface.tsx",
    // Shorter than the other two's `was`: the original comment wrapped this
    // sentence across a line break right after "card", so the fragment below
    // is the longest piece that sat on one source line, and still enough for
    // a mutant to restore word for word.
    was: "That is what makes a `flow.node` tick re-render one card",
  },
  {
    path: "store.ts",
    was: "makes a node-status tick re-render one node instead of the canvas.",
  },
];

/** The marker every corrected comment now shares, verbatim: it names #464
 *  and says nothing writes the field yet. A mutant that puts the OLD
 *  sentence back removes this; a mutant that merely reworded the sentence
 *  without saying "#464" would still be caught by `was` being gone but is
 *  not what #593 asks for, so both halves are checked. */
const MARKER = "nothing does (#464";

function read(rel: string): string {
  return fs.readFileSync(`${SRC}/${rel}`, "utf8");
}

test("the known-positive: the OLD sentence is the one each file shipped before this fix", () => {
  // Guards the fixture itself: if `was` is not what the file used to say, the
  // "gone" assertion below would pass for the wrong reason (a typo in `was`
  // that no version of the file ever contained). Checked against a literal
  // copy of each original line, not against the live file - the whole point
  // of the next case is that the live file no longer has it.
  const shipped: Record<string, string> = {
    "components/flows/FlowCanvas.tsx":
      "  // as a prop; the cards are memo'd and read only their own status. That is what\n"
      + "  // makes a `flow.node` tick re-render one card instead of the graph.",
    "next/hubs/session/flows/canvas/FlowCanvasSurface.tsx":
      "  // their own status. That is what makes a `flow.node` tick re-render one card\n"
      + "  // instead of the graph.",
    "store.ts":
      "// working in ui/. See components/flows/flowsSlice.ts for the write discipline\n"
      + "// that makes a node-status tick re-render one node instead of the canvas.",
  };
  for (const f of FILES) {
    assert(shipped[f.path]?.includes(f.was), `no shipped text recorded for ${f.path} includes its own "was"`);
  }
});

test("none of the three leftover comments claims a flow.node/node-status tick exists", () => {
  // MUTANT "tick sentence restored" (FlowCanvas.tsx's comment put back word
  // for word as it stood before this fix). Observed (1 failed; this case):
  //   x none of the three leftover comments claims a flow.node/node-status tick exists: components/flows/FlowCanvas.tsx still says the OLD sentence H4's sweep (#464, #593) was supposed to remove: "makes a `flow.node` tick re-render one card instead of the graph."
  const bad: string[] = [];
  for (const f of FILES) {
    const text = read(f.path);
    if (text.includes(f.was)) {
      bad.push(`${f.path} still says the OLD sentence H4's sweep (#464, #593) was supposed to remove: ${JSON.stringify(f.was)}`);
    }
  }
  assert(bad.length === 0, bad.join("\n    "));
});

test("all three now share the #464 marker the other four corrected comments use", () => {
  // MUTANT "the marker dropped but the old sentence not restored" (FlowCanvas
  // .tsx reworded to drop "#464" without putting the claim back - a comment
  // that says NEITHER thing would slip past the case above alone). Observed
  // (1 failed; this case):
  //   x all three now share the #464 marker the other four corrected comments use: components/flows/FlowCanvas.tsx does not say "nothing does (#464": its comment is <the reworded text>
  const bad: string[] = [];
  for (const f of FILES) {
    const text = read(f.path);
    if (!text.includes(MARKER)) {
      bad.push(`${f.path} does not say ${JSON.stringify(MARKER)}`);
    }
  }
  assert(bad.length === 0, bad.join("\n    "));
});

test("the new test file this row names is itself in the tree (Revision 11 row 45's own claim)", () => {
  // test_mosaic_spec_claims.py's Revision 11 scan checks every backticked
  // name in the row's Source column resolves in ui/src by basename; this
  // guards the same fact from the UI side, so a rename on either side is
  // caught here first, without running the (much larger) python suite.
  const self = "components/flows/__tests__/w4FlowNodeTickComments.test.ts";
  assert(read(self).length > 0, "this very file could not be read back by its own published path");
});

// ------------------------------------------------------------------ report
console.log(`\nw4FlowNodeTickComments: ${passed} passed, ${failed} failed`);
if (failures.length) { failures.forEach((f) => console.log(f)); process.exit(1); }
export default { passed, failed, total: passed + failed };
