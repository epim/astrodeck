// targetSummary.test.ts - the TARGET card's footer line and the loop wire's
// words (#189 S4 item 6; spec 2026-09-23 flows mosaic, 1.2 "Card footer" and
// 1.4 "How it is drawn"; S4 orchestrator ruling 1: a grid is written columns
// by rows).
//
//   Run directly:  node --import tsx src/components/flows/__tests__/targetSummary.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT IS PINNED.
//   * The footer's three forms:
//       rotating mosaic   M31 · rotate · 3x2 · PA 30.0 · 25%
//       mosaic, no loop   M31 · one panel at a time · 3x2
//       single target     NGC 7331 · any angle
//     with 3x2 meaning three columns by two rows, and "rotate" read from the
//     GRAPH (the block's loop wire, and no M12 beside it, #410), never from a
//     param. The word that tells the two mosaic lines apart comes right after
//     the name (#357).
//   * The classic card's line fitted to its budget (`fittedFooter`, S7
//     orchestrator ruling 9): the overlap first, then the name shortened with
//     an ellipsis, never the loop word or the angle, and the rungs past the
//     ruling (the grid, then the name) where the name would keep nothing.
//     cardFooterDom.test.tsx computes that budget from the mounted card and
//     holds the ruling's cases to it; this file holds the edges.
//   * The loop chip: "every pass: next panel · N panels", N the live panels the
//     COMPILE's entry for the block holds (rows x cols minus the parsed skip),
//     no count before a compile or when the compile no longer describes the
//     block, and no chip at all on a wire into `next` that is not the block's
//     loop wire.
//   * The contract two other test files lean on: the footer calls the
//     vocabulary's own `sum` exactly once (flowNodeDom.test.tsx and
//     flowProgressChip.test.tsx count card renders through it).
//
// Each named mutation was run in a private scratch copy of ui/, and what it
// turned red is recorded beside the test.

import {
  FOOTER_ELLIPSIS, LOOP_ARC_DASH, LOOP_CHIP_WORDS, fittedFooter, livePanels, loopArcOf,
  loopArcTarget, loopChip, monoChars, targetFooter, targetLoops, targetSummary,
} from "../targetSummary";
import { loopArc } from "../geometry";
import { NODE_DEFS } from "../nodeDefs";
import { panelLane } from "../panelLane";
import type { FlowEdgeRec, FlowNodeRec, FlowNodeType } from "../flowsTypes";

// ---------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];

function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function eq<T>(a: T, b: T, msg = ""): void {
  if (a !== b) throw new Error(`${msg}\n    expected ${JSON.stringify(b)}\n    got      ${JSON.stringify(a)}`);
}
function assert(cond: unknown, msg: string): void {
  if (!cond) throw new Error(msg);
}

// ---------------------------------------------------------------- fixtures
type Params = Record<string, string | number>;

function node(id: string, type: FlowNodeType, x: number, params: Params = {}): FlowNodeRec {
  return { id, type, x, y: 60, params: { ...NODE_DEFS[type].params, ...params } };
}
let edgeN = 0;
function wire(from: string, fromPort: string, to: string, toPort: string): FlowEdgeRec {
  edgeN += 1;
  return { id: `w${edgeN}`, from, fromPort, to, toPort };
}

/** M31 at three columns by two rows, Rotate to PA 30, 25% overlap. */
const M31_PARAMS: Params = {
  name: "M31", rows: 2, cols: 3, overlap: 25, angle: "Rotate to PA", rotation: 30,
};
const T = node("t", "target", 100, M31_PARAMS);
const AF = node("af", "autofocus", 340);
const CY = node("cy", "cycle", 580);
const ARM = wire("t", "target", "af", "run");
const RUN = wire("af", "focused", "cy", "run");
const LOOP = wire("cy", "pass", "t", "next");

/** The spec's worked lane: TARGET -> AUTOFOCUS -> FILTER CYCLE, and the
 *  dashed wire from the cycle's "pass done" back to "next panel". */
const LOOPED = { nodes: [T, AF, CY], edges: [ARM, RUN, LOOP] };
const UNLOOPED = { nodes: [T, AF, CY], edges: [ARM, RUN] };

const SINGLE = node("s", "target", 100, { name: "NGC 7331", angle: "Any angle", rotation: -1 });
const ALONE = { nodes: [SINGLE], edges: [] };

/** A compile answer's plan with one entry for the block, in compile_plan's
 *  own shape (server flows/compile.py `_target_entry`). */
function planWith(entry: Record<string, unknown>): Record<string, unknown> {
  return { targets: [{ name: "other", node_id: "x", mosaic: null }, entry] };
}
const M31_ENTRY = { node_id: "t", mosaic: { rows: 2, cols: 3, skip: [[2, 3]] } };

// ============================================================ the footer

// MUTANT "loop read from params" (targetLoops answers isMultiPanel(node): any
// mosaic reads as rotating, the graph unread). This case stays green; the
// unlooped one below goes red.
//
// MUTANT "spec order restored" (targetFooter: the loop word pushed last again,
// as the design wrote it; #357), re-run in the private scratch copy
// scratchpad/S5-LOOP-mut. Observed, targetSummary.test 16/22 (every mosaic
// footer case; this one):
//   x a rotating mosaic: 'M31 · rotate · 3x2 · PA 30.0 · 25%': the rotating
//     mosaic's footer
//     expected "M31 · rotate · 3x2 · PA 30.0 · 25%"
//     got      "M31 · 3x2 · PA 30.0 · 25% · rotate"
// The case that says why the order matters is cardFooterDom.test.tsx's
// budget case, which computes the classic card's 29 characters.
test("a rotating mosaic: 'M31 · rotate · 3x2 · PA 30.0 · 25%'", () => {
  eq(targetSummary(T, LOOPED), "M31 · rotate · 3x2 · PA 30.0 · 25%", "the rotating mosaic's footer");
});

// MUTANT "loop read from params". Observed on its re-run in S5-LOOP-mut,
// once the footer's order changed (#357), targetSummary.test 18/22:
//   x a mosaic without the loop wire: 'M31 · one panel at a time · 3x2': the
//     panel-first mosaic's footer
//     expected "M31 · one panel at a time · 3x2"
//     got      "M31 · rotate · 3x2 · PA 30.0 · 25%"
test("a mosaic without the loop wire: 'M31 · one panel at a time · 3x2'", () => {
  eq(targetSummary(T, UNLOOPED), "M31 · one panel at a time · 3x2", "the panel-first mosaic's footer");
});

test("a single target: 'NGC 7331 · any angle'", () => {
  eq(targetSummary(SINGLE, ALONE), "NGC 7331 · any angle", "the single target's footer");
});

// MUTANT "rows x cols" (the size written `${rows}x${cols}`). Observed on its
// re-run in S5-LOOP-mut, once the footer's order changed (#357),
// targetSummary.test 15/22 (every mosaic case; this one):
//   x the size is COLUMNS x ROWS (S4 orchestrator ruling 1): 2 rows of 3 read
//     3x2: three columns by two rows
//     expected "3x2"
//     got      "2x3"
//   x a rotating mosaic: 'M31 · rotate · 3x2 · PA 30.0 · 25%': the rotating
//     mosaic's footer
//     expected "M31 · rotate · 3x2 · PA 30.0 · 25%"
//     got      "M31 · rotate · 2x3 · PA 30.0 · 25%"
test("the size is COLUMNS x ROWS (S4 orchestrator ruling 1): 2 rows of 3 read 3x2", () => {
  const wide = node("t", "target", 100, { ...M31_PARAMS, rows: 2, cols: 3 });
  const tall = node("t", "target", 100, { ...M31_PARAMS, rows: 3, cols: 2 });
  const size = (line: string) => line.split(" · ").find((seg) => /^\d+x\d+$/.test(seg));
  eq(size(targetSummary(wide, { nodes: [wide, AF, CY], edges: [ARM, RUN] })), "3x2",
    "three columns by two rows");
  eq(size(targetSummary(tall, { nodes: [tall, AF, CY], edges: [ARM, RUN] })), "2x3",
    "two columns by three rows");
});

// A pass wire from a stage that is not the lane's tail is M12, and the
// compile does not loop on it (`loop_wires`, which panelLane.ts mirrors). The
// footer must say what the run will do. MUTANT "any wire into next loops"
// (targetLoops asks for any edge into the block's `next`). Observed,
// targetSummary.test 19/20:
//   x a pass wire from mid-lane is not the loop: the footer says one panel at
//     a time: the cycle is not the tail once a CAPTURE follows it
//     expected false
//     got      true
test("a pass wire from mid-lane is not the loop: the footer says one panel at a time", () => {
  const cap = node("cap", "capture", 820);
  const g = {
    nodes: [T, AF, CY, cap],
    edges: [ARM, RUN, wire("cy", "complete", "cap", "run"), wire("cy", "pass", "t", "next")],
  };
  eq(targetLoops(T, g), false, "the cycle is not the tail once a CAPTURE follows it");
  eq(targetSummary(T, g), "M31 · one panel at a time · 3x2", "the footer of a mid-lane pass wire");
});

// #410. A tail wire standing beside a stale one from mid-lane is the loop to
// `loop_wires` and M12 to `lane_refusals`, so `/run` refuses the flow: the
// card must not say "rotate", and the arc must carry no chip that promises
// "every pass". Before #410 both did, which is what LOOP PANELS as S4 built it
// left behind (a second wire from the tail) and what a flow saved before S4
// opens as once its tail is looped by hand.
//
// MUTANT "targetLoops ignores M12" (targetLoops: the `midLanePassWires(...)
// .length === 0` term deleted, as S4 built it), run in the private scratch
// copy scratchpad/S5-LOOP-mut. Observed, targetSummary.test 21/22:
//   x a tail loop beside a stale mid-lane pass wire (M12) rotates nothing, and
//     the card says so: a stale mid-lane wire is M12: the run does not rotate,
//     it refuses
//     expected false
//     got      true
// MUTANT "chip ignores M12" (loopChip's `isLoopWire` back to `isMultiPanel`
// in place of `targetLoops`, as S4 built it). Observed, 21/22:
//   x a tail loop beside a stale mid-lane pass wire (M12) rotates nothing, and
//     the card says so: the tail wire's chip beside a stale wire
//     expected null
//     got      "every pass: next panel · 5 panels"
test("a tail loop beside a stale mid-lane pass wire (M12) rotates nothing, and the card says so", () => {
  const cap = node("cap", "capture", 820);
  const own = wire("cap", "pass", "t", "next");
  const stale = wire("cy", "pass", "t", "next");
  const g = { nodes: [T, AF, CY, cap], edges: [ARM, RUN, wire("cy", "complete", "cap", "run"), own, stale] };
  eq(targetLoops(T, g), false, "a stale mid-lane wire is M12: the run does not rotate, it refuses");
  eq(targetSummary(T, g), "M31 · one panel at a time · 3x2", "the footer beside a stale wire");
  eq(loopChip(g, own, planWith(M31_ENTRY), false), null, "the tail wire's chip beside a stale wire");
  // CONTROL: the stale wire gone, the same tail wire is the loop again.
  const clean = { ...g, edges: g.edges.filter((e) => e !== stale) };
  eq(targetLoops(T, clean), true, "the tail wire alone");
  eq(loopChip(clean, own, planWith(M31_ENTRY), false), "every pass: next panel · 5 panels",
    "the tail wire's chip once the stale wire is gone");
});

test("a loop wire into a 1x1 block does not make it a mosaic", () => {
  const one = node("t", "target", 100, { ...M31_PARAMS, rows: 1, cols: 1 });
  const g = { nodes: [one, AF, CY], edges: [ARM, RUN, LOOP] };
  eq(targetLoops(one, g), false, "one panel has nothing to rotate between");
  eq(targetSummary(one, g), "M31 · PA 30.0", "a single target at Rotate to PA 30");
});

// THE ANGLE, as the compile reads it (compile.py `angle_code` over
// `target_angle`): a stored choice this build offers, else derived from the
// rotation alone. MUTANT "angle defaulted to any" (a missing `angle` read as
// "Any angle"). Observed, targetSummary.test 19/20:
//   x the angle: any, a PA to rotate to, or a fixed camera's PA, derived when
//     unstored: no stored angle: rotation 0 is north up, a real PA
//     expected "M31 · PA 0.0"
//     got      "M31 · any angle"
test("the angle: any, a PA to rotate to, or a fixed camera's PA, derived when unstored", () => {
  const at = (p: Params) => targetSummary(node("s", "target", 0, { name: "M31", ...p }),
    { nodes: [], edges: [] });
  eq(at({ angle: "Any angle", rotation: -1 }), "M31 · any angle", "any angle");
  eq(at({ angle: "Rotate to PA", rotation: 23.4 }), "M31 · PA 23.4", "rotate to PA");
  eq(at({ angle: "Camera fixed at PA", rotation: "23.4" }), "M31 · fixed PA 23.4", "a fixed camera");
  eq(at({ angle: "", rotation: 0 }), "M31 · PA 0.0", "no stored angle: rotation 0 is north up, a real PA");
  eq(at({ angle: "", rotation: -1 }), "M31 · any angle", "no stored angle: a negative rotation is any angle");
  eq(at({ angle: "Sideways", rotation: 12 }), "M31 · PA 12.0",
    "a word this build does not offer is read as the rotation alone says it");
});

// compile.py `angle_code` looks the stored word up after `.strip()`, so a
// padded word (a hand edit) is still that choice to the run. Added by the
// S4-UARC verifier. MUTANT "angle words not trimmed" (angleWords reads
// `targetAngle(p)` without `.trim()`), run in a private scratch copy of ui/.
// Observed, targetSummary.test 20/21:
//   x a padded angle word reads as the compile strips it: a fixed camera,
//     padded: the run holds the camera, it does not rotate to 23.4
//     expected "M31 · fixed PA 23.4"
//     got      "M31 · PA 23.4"
test("a padded angle word reads as the compile strips it", () => {
  const at = (p: Params) => targetSummary(node("s", "target", 0, { name: "M31", ...p }),
    { nodes: [], edges: [] });
  eq(at({ angle: " Camera fixed at PA ", rotation: 23.4 }), "M31 · fixed PA 23.4",
    "a fixed camera, padded: the run holds the camera, it does not rotate to 23.4");
  eq(at({ angle: " Any angle", rotation: 23.4 }), "M31 · any angle",
    "any angle, padded, over a stale rotation");
  // CONTROL: whitespace alone is no word, to the compile ("" after strip, so
  // the rotation decides) as here.
  eq(at({ angle: "  ", rotation: -1 }), "M31 · any angle", "a blank word and a negative rotation");
});

// The grid is read as the compile reads it (`_grid_dim`, mirrored by
// panelLane.ts `isMultiPanel`): "2.0" is not a whole number, so it reads 1.
// MUTANT "grid read with Number()" (gridDim is `Number(v)`). Observed on its
// re-run in S5-LOOP-mut, once the footer's order changed (#357),
// targetSummary.test 21/22:
//   x a grid side is read as the compile reads it: '2.0' rows is one row:
//     rows '2.0' and cols '3'
//     expected "M31 · one panel at a time · 3x1"
//     got      "M31 · one panel at a time · 3x2"
test("a grid side is read as the compile reads it: '2.0' rows is one row", () => {
  const t = node("t", "target", 100, { ...M31_PARAMS, rows: "2.0", cols: "3" });
  eq(targetSummary(t, { nodes: [t, AF, CY], edges: [ARM, RUN] }), "M31 · one panel at a time · 3x1",
    "rows '2.0' and cols '3'");
});

test("an empty name leaves no empty segment; an unreadable overlap is left out", () => {
  const blank = node("s", "target", 0, { name: "", angle: "Any angle", rotation: -1 });
  eq(targetSummary(blank, { nodes: [blank], edges: [] }), "any angle", "a created, unnamed TARGET");
  const t = node("t", "target", 100, { ...M31_PARAMS, overlap: "lots" });
  eq(targetSummary(t, { nodes: [t, AF, CY], edges: [ARM, RUN, LOOP] }), "M31 · rotate · 3x2 · PA 30.0",
    "an overlap that is not a number");
});

// THE CONTRACT THE RENDER COUNTERS LEAN ON. flowNodeDom.test.tsx and
// flowProgressChip.test.tsx count a card's renders by wrapping
// `NODE_DEFS.target.sum`, which the footer calls once per render. MUTANT "name
// read directly" (the name segment built from `params.name`, `sum` never
// called). Observed, targetSummary.test 19/20:
//   x the footer takes the name from the vocabulary's own sum, once per call:
//     calls to NODE_DEFS.target.sum per footer
//     expected 1
//     got      0
// and the two files that lean on the contract go red with it:
//   flowNodeDom.test 19/20:
//     x a status tick re-renders ONE card, not the graph: setup: one render each
//   flowProgressChip.test 31/33:
//     x [classic] an answer that leaves a card's count alone does not
//       re-render it: precondition: one render each (expected 1, got 0)
//     x [next] ... the same
test("the footer takes the name from the vocabulary's own sum, once per call", () => {
  const orig = NODE_DEFS.target.sum;
  let calls = 0;
  NODE_DEFS.target.sum = (p) => { calls++; return orig(p); };
  try {
    targetFooter(T, true);
    eq(calls, 1, "calls to NODE_DEFS.target.sum per footer");
    calls = 0;
    targetLoops(T, LOOPED);
    eq(calls, 0, "targetLoops runs in a store selector and must not count as a render");
  } finally {
    NODE_DEFS.target.sum = orig;
  }
});

test("CONTROL: the vocabulary's TARGET sum is unchanged: the name alone", () => {
  eq(NODE_DEFS.target.sum(M31_PARAMS as Record<string, string | number>), "M31", "TARGET's sum");
  eq(NODE_DEFS.cycle.sum(NODE_DEFS.cycle.params), "7 filters · 1/pass · ×45", "FILTER CYCLE's sum");
});

// ================================================ the footer on one line
//
// S7 orchestrator ruling 9 (#357), at the classic card's 29 characters. The
// ruling's own cases are mounted in cardFooterDom.test.tsx; these are the
// edges of the fitting itself.

const FIT = 29;
const single = (name: string, extra: Params = {}) =>
  node("s", "target", 100, { name, rows: 1, cols: 1, angle: "Any angle", rotation: -1, ...extra });

// A line of exactly the budget is drawn whole; one character more and the
// name keeps sixteen of its eighteen characters and the ellipsis. MUTANT
// "fits below the budget" (fittedFooter: `full.length < chars` for
// `<= chars`, and nameFitted's `whole.length < chars` likewise). Observed,
// targetSummary.test 27/28 (cardFooterDom.test.tsx stays 26/26: none of the
// ruling's cases is exactly 29 before it is fitted):
//   x fitted: a line of exactly 29 characters is whole; 30 shortens the name: 29 characters
//     expected "NGC 7331 Pegasus. · any angle"
//     got      "NGC 7331 Pegasus… · any angle"
test("fitted: a line of exactly 29 characters is whole; 30 shortens the name", () => {
  const at = single("NGC 7331 Pegasus.");
  eq(targetFooter(at, false).length, 29, "precondition: a 17-character name at any angle is 29");
  eq(fittedFooter(at, false, FIT).line, "NGC 7331 Pegasus. · any angle", "29 characters");
  const over = single("NGC 7331 Pegasus..");
  eq(fittedFooter(over, false, FIT).line, `NGC 7331 Pegasus${FOOTER_ELLIPSIS} · any angle`, "30 characters");
});

test("fitted: the whole line comes back beside the fitted one", () => {
  const t = node("t", "target", 100, { ...M31_PARAMS, name: "NGC 7331" });
  eq(JSON.stringify(fittedFooter(t, true, FIT)), JSON.stringify({
    line: `NGC${FOOTER_ELLIPSIS} · rotate · 3x2 · PA 30.0`,
    full: "NGC 7331 · rotate · 3x2 · PA 30.0 · 25%",
  }), "the NGC 7331 rotating mosaic");
  const fits = single("NGC 7331");
  eq(JSON.stringify(fittedFooter(fits, false, FIT)),
    JSON.stringify({ line: "NGC 7331 · any angle", full: "NGC 7331 · any angle" }), "a line that fits");
});

// A name cut at a space reads "NG…", not "NG …": the room is four, three
// characters and the ellipsis, and the third is the space in "NG 7331".
test("fitted: a name cut at a space drops the space before the ellipsis", () => {
  const t = node("t", "target", 100, { ...M31_PARAMS, name: "NG 7331" });
  eq(fittedFooter(t, true, FIT).line, `NG${FOOTER_ELLIPSIS} · rotate · 3x2 · PA 30.0`, "a cut at the space");
});

// The last rung, past the ruling: the name is left out only when not one of
// its characters fits beside the loop word and the angle even with the grid
// gone. A rotation typed as 12345.6 is the doctor's to refuse; the card still
// keeps "rotate" and the angle whole. MUTANT "the grid kept at any cost"
// (fittedFooter's last rung returns `joined(f.loop, f.size, f.angle)`, the
// line with the grid, instead of without it), which leaves the angle to
// `truncate`. Observed, targetSummary.test 27/28:
//   x fitted: past the grid, the name goes before the loop word or the angle is cut: a rotation too long for any name beside it
//     expected "rotate · fixed PA 12345.6"
//     got      "rotate · 12x10 · fixed PA 12345.6"
// "the name goes to the ellipsis before the grid" (cardFooterDom.test.tsx)
// fails this case too (27/28): got "… · rotate · fixed PA 12345.6".
test("fitted: past the grid, the name goes before the loop word or the angle is cut", () => {
  const t = node("t", "target", 100, { ...M31_PARAMS, rows: 10, cols: 12,
    angle: "Camera fixed at PA", rotation: 12345.6 });
  const { line } = fittedFooter(t, true, FIT);
  eq(line, "rotate · fixed PA 12345.6", "a rotation too long for any name beside it");
  assert(line.length <= FIT, `"${line}" is over the budget`);
  // And an unnamed block over the budget loses its grid, never a separator
  // at the front.
  const unnamed = node("t", "target", 100, { ...M31_PARAMS, name: "", rows: 10, cols: 12,
    angle: "Camera fixed at PA", rotation: 1234.5 });
  eq(fittedFooter(unnamed, true, FIT).line, "rotate · fixed PA 1234.5", "an unnamed block");
});

test("fitted: the name is still the one sum call", () => {
  const orig = NODE_DEFS.target.sum;
  let calls = 0;
  NODE_DEFS.target.sum = (p) => { calls++; return orig(p); };
  try {
    fittedFooter(node("t", "target", 100, { ...M31_PARAMS, name: "NGC 7331" }), true, FIT);
    eq(calls, 1, "calls to NODE_DEFS.target.sum per fitted footer");
  } finally {
    NODE_DEFS.target.sum = orig;
  }
});

test("monoChars: 166 px of 9.5 px mono is 29 characters", () => {
  eq(monoChars(166, 9.5), 29, "the classic footer's room");
  eq(monoChars(162, 9.5), 28, "four pixels less");
});

// ========================================================= the loop wire

// MUTANT "arc for every wire into a TARGET" (the `toPort === "next"` test
// dropped). Observed, targetSummary.test 19/20:
//   x an event wire into a TARGET's `next` is a loop arc; nothing else is: an
//     event wire into the TARGET's arm
//     expected null
//     got      {"id":"t","type":"target","x":100,"y":60,"params":{"name":"M31",...}}
test("an event wire into a TARGET's `next` is a loop arc; nothing else is", () => {
  eq(loopArcTarget(LOOP, LOOPED.nodes)?.id, "t", "the loop wire");
  eq(loopArcTarget(ARM, LOOPED.nodes), null, "a flow wire out of the TARGET");
  const armIn = wire("d", "window", "t", "arm");
  eq(loopArcTarget(armIn, [node("d", "dusk", 0), T]), null, "a flow wire into the TARGET's arm");
  // Validation refuses an event output into a flow input, but a graph that
  // arrived some other way is drawn as it arrived, and only `next` loops.
  eq(loopArcTarget(wire("cy", "frame", "t", "arm"), LOOPED.nodes), null,
    "an event wire into the TARGET's arm");
  const pool = node("p", "pool", 0);
  const advance = wire("r", "done", "p", "advance");
  eq(loopArcTarget(advance, [node("r", "report", 400), pool]), null,
    "REPORT 'done' -> POOL 'advance' is a backward event wire, but not into a TARGET");
  eq(loopArcTarget(wire("cy", "nope", "t", "next"), LOOPED.nodes), null, "an unknown source port");
});

test("loopArcOf routes the wire under the TARGET, its lane and the source", () => {
  const arc = loopArcOf(LOOP, LOOPED, "desktop");
  const want = loopArc(CY, "pass", T, "next", panelLane(LOOPED, "t"), NODE_DEFS, "desktop");
  assert(arc && want, "precondition: both arcs exist");
  eq(arc!.d, want!.d, "the arc of the loop wire");
  eq(loopArcOf(ARM, LOOPED, "desktop"), null, "a flow wire is not an arc");
  // Placed: the phone FLOW tab substitutes its auto-layout positions.
  const moved = loopArcOf(LOOP, LOOPED, "desktop",
    (n) => (n.id === "cy" ? { ...n, y: 300 } : n));
  eq(moved!.runY, 300 + 143 + 28, "the run follows a placed card");
});

test("CONTROL: the arc's dash is its own silhouette, never the lane's", () => {
  // Widened, or the compiler rules the comparison out: the constant is a
  // literal type, and an edit to it is exactly what this case is for.
  const dash: string = LOOP_ARC_DASH;
  assert(dash !== "4 5" && dash !== "7 6" && dash !== "",
    `the arc's dash ${dash} must differ from an event wire's 4 5 and a live wire's 7 6`);
});

// --------------------------------------------------------- the chip

test("before any compile the chip reads without the count", () => {
  eq(loopChip(LOOPED, LOOP, null, false), LOOP_CHIP_WORDS, "no compile answer");
  eq(loopChip(LOOPED, LOOP, undefined, false), LOOP_CHIP_WORDS, "no compiled plan");
  eq(LOOP_CHIP_WORDS, "every pass: next panel", "the chip's words");
});

// MUTANT "count ignores skip" (N = rows x cols). Observed, targetSummary.test
// 18/20:
//   x after a compile: 'every pass: next panel · N panels', N = rows x cols -
//     skip: 3x2 with 2-3 skipped
//     expected "every pass: next panel · 5 panels"
//     got      "every pass: next panel · 6 panels"
// (and the over-skipped answer below counts 6 where it must count nothing)
test("after a compile: 'every pass: next panel · N panels', N = rows x cols - skip", () => {
  eq(loopChip(LOOPED, LOOP, planWith(M31_ENTRY), false), "every pass: next panel · 5 panels",
    "3x2 with 2-3 skipped");
  eq(livePanels(planWith({ node_id: "t", mosaic: { rows: 2, cols: 3, skip: [] } }), T), 6,
    "3x2 with nothing skipped");
  eq(loopChip(LOOPED, LOOP, planWith({ node_id: "t", mosaic: { rows: 2, cols: 3,
    skip: [[1, 1], [1, 2], [1, 3], [2, 1], [2, 2]] } }), false),
  "every pass: next panel · 1 panel", "one live panel is one panel");
});

// The compile runs on an open, DONE, LOOP PANELS and a save, never on an
// inspector edit, so after an edit its entry describes the graph as it was.
// The canvases say so with `stale` (`!compiledIsCurrent`, #356, S7; it was
// `dirty` until S7, and loopChipCurrent.test.ts drives both canvases through
// the real slice). MUTANT "count while dirty" (loopChip ignores its flag),
// first observed 19/20; re-run as "count while stale" in S7-UCANVAS-mut,
// targetSummary.test 27/28:
//   x a stale answer takes the count away: it describes the graph before the edit: the chip over a stale answer
//     expected "every pass: next panel"
//     got      "every pass: next panel · 5 panels"
test("a stale answer takes the count away: it describes the graph before the edit", () => {
  eq(loopChip(LOOPED, LOOP, planWith(M31_ENTRY), true), LOOP_CHIP_WORDS, "the chip over a stale answer");
});

// MUTANT "count from a stale grid" (the grid check dropped). Observed,
// targetSummary.test 19/20:
//   x a compile whose grid is not the block's any more gives no count: a 3x2
//     entry for a block now 3x3
//     expected null
//     got      5
test("a compile whose grid is not the block's any more gives no count", () => {
  const bigger = node("t", "target", 100, { ...M31_PARAMS, rows: 3, cols: 3 });
  const g = { nodes: [bigger, AF, CY], edges: [ARM, RUN, LOOP] };
  eq(livePanels(planWith(M31_ENTRY), bigger), null, "a 3x2 entry for a block now 3x3");
  eq(loopChip(g, LOOP, planWith(M31_ENTRY), false), LOOP_CHIP_WORDS, "the chip of a re-gridded block");
});

test("an answer that cannot be counted gives no count and never throws", () => {
  const bad: unknown[] = [
    {}, { targets: "nope" }, { targets: [null, 3] }, planWith({ node_id: "t", mosaic: null }),
    planWith({ node_id: "t", mosaic: { rows: 2, cols: 3, skip: "2-3" } }),
    planWith({ node_id: "t", mosaic: { rows: "2", cols: 3, skip: [] } }),
    planWith({ node_id: "t", mosaic: { rows: 2, cols: 3, skip: [[1, 1], [1, 2], [1, 3], [2, 1], [2, 2], [2, 3], [9, 9]] } }),
  ];
  for (const plan of bad) {
    eq(loopChip(LOOPED, LOOP, plan as Record<string, unknown>, false), LOOP_CHIP_WORDS,
      `the chip for ${JSON.stringify(plan)}`);
  }
});

// A wire into `next` that the compile does not loop on gets the arc (it is
// still a backward wire, and the stock curve would still cross the cards) but
// not the words, which would claim a rotation the run will not make. MUTANT
// "chip on every arc" (loopChip asks only `loopArcTarget`). Observed,
// targetSummary.test 19/20:
//   x no chip on a wire into `next` that is not the block's loop wire: a
//     mid-lane pass wire (M12)
//     expected null
//     got      "every pass: next panel · 5 panels"
test("no chip on a wire into `next` that is not the block's loop wire", () => {
  const cap = node("cap", "capture", 820);
  const mid = wire("cy", "pass", "t", "next");
  const g = { nodes: [T, AF, CY, cap], edges: [ARM, RUN, wire("cy", "complete", "cap", "run"), mid] };
  eq(loopChip(g, mid, planWith(M31_ENTRY), false), null, "a mid-lane pass wire (M12)");
  const one = node("t", "target", 100, { ...M31_PARAMS, rows: 1, cols: 1 });
  eq(loopChip({ nodes: [one, AF, CY], edges: [ARM, RUN, LOOP] }, LOOP, null, false), null,
    "a pass wire into a 1x1 block");
  eq(loopChip(LOOPED, ARM, null, false), null, "a flow wire");
});

// ----------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\ntargetSummary.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
