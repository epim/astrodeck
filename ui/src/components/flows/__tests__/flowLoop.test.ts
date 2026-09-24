// flowLoop.test.ts - the flow-loop rule, as the pure function both editors and
// the store share (#149; spec 2026-09-23 section 1.4 item 5, S0 item 2).
//   Run:  node --import tsx src/components/flows/__tests__/flowLoop.test.ts   (from ui/)
//
// WHAT IS WORTH GUARDING HERE
//
//   1. THE SENTENCE IS THE SERVER'S, character for character. The server's
//      FlowGraph.validation_errors refuses the same graph at save and /run, and
//      an operator who is told one thing by the canvas and another by the 422
//      is being told about two different rules.
//   2. A LOOP IS FOUND BY WALKING, not by looking for a wire that points back
//      at its neighbour. The obvious cheap check ("is there already a wire from
//      the destination to the source?") finds the three-stage loop and misses
//      the same loop drawn through AUTOFOCUS and GUIDE, which is the lane a real
//      night has.
//   3. EVENT WIRES ARE NEVER REFUSED, and a circle that closes through one is
//      not a flow loop. REPORT "target done" -> POOL "advance" is how the
//      campaign example hands out its next target; a rule that refused it, or
//      that followed it while walking, would make the campaign undrawable.
//   4. A LOOP THE GRAPH ALREADY HAS DOES NOT LOCK THE EDITOR. A flow saved
//      before S0 can carry one; the operator must still be able to wire
//      everything else while they fix it.
//   5. A FLOW WIRE IS FLOW AT BOTH ENDS, as the server's back-edge walk
//      counts it. A wire whose ports disagree (tap-to-wire can leave one) is
//      the lane rule's business; judging it here would refuse wires the save
//      does not call loops.
//
// Every case names the mutant it kills and quotes the failure that mutant
// produced when it was run from a byte-for-byte backup of flowLoop.ts.

/* eslint-disable @typescript-eslint/no-explicit-any */

import { FLOW_LOOP_REFUSAL, flowLoopRefusal } from "../flowLoop";
import type { FlowEdgeRec, FlowNodeRec } from "../flowsTypes";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) {
    throw new Error(`${msg}\n  expected ${JSON.stringify(want)}\n  got      ${JSON.stringify(got)}`);
  }
}

// ------------------------------------------------------------------ fixture
const node = (id: string, type: string, x = 0): FlowNodeRec =>
  ({ id, type: type as FlowNodeRec["type"], x, y: 0, params: {} });
let seq = 0;
const edge = (from: string, fromPort: string, to: string, toPort: string): FlowEdgeRec =>
  ({ id: `e${++seq}`, from, fromPort, to, toPort });
const wire = (from: string, fromPort: string, to: string, toPort: string) =>
  ({ from, fromPort, to, toPort });

/** The literal the server's constant carries (T1, `flows/models.py`). Typed out
 *  here rather than imported, so a change to the module's copy is a change to
 *  the contract and has to be made on purpose in both places. */
const SERVER_SENTENCE = "this flow loops back on itself at {src} -> {dst}; a flow lane runs once";

// dusk -> target -> cycle: the spec's own loop, the three stages a mosaic's
// operator would draw first when they want "and round again".
const LANE_NODES = [node("d", "dusk"), node("t", "target", 250), node("c", "cycle", 500),
                    node("r", "report", 750)];
const LANE_EDGES = [edge("d", "window", "t", "arm"), edge("t", "target", "c", "run")];

// ------------------------------------------------------------------- tests

test("the refusal template is the server's sentence, character for character", () => {
  // MUTANT "reword the template" (`a flow lane runs once` -> `a flow lane
  // runs only once`). Observed (4 failed; this one first, then the three cases
  // below that carry the filled sentence):
  //   x the refusal template is the server's sentence, character for character: the canvas and the 422 must say the same thing
  //     expected "this flow loops back on itself at {src} -> {dst}; a flow lane runs once"
  //     got      "this flow loops back on itself at {src} -> {dst}; a flow lane runs only once"
  eq(FLOW_LOOP_REFUSAL, SERVER_SENTENCE, "the canvas and the 422 must say the same thing");
});

test("a forward flow wire is not a loop", () => {
  // CONTROL. FILTER CYCLE "complete" onward into SESSION REPORT is the
  // ordinary end of a lane. MUTANT "refuse every flow wire" (return the filled
  // sentence without walking). Observed (4 failed; this one, plus three of the
  // null cases below):
  //   x a forward flow wire is not a loop: FILTER CYCLE -> SESSION REPORT runs forward, and refusing it would make every lane undrawable
  //     expected null
  //     got      "this flow loops back on itself at FILTER CYCLE -> SESSION REPORT; a flow lane runs once"
  eq(flowLoopRefusal(LANE_NODES, LANE_EDGES, wire("c", "complete", "r", "session")), null,
    "FILTER CYCLE -> SESSION REPORT runs forward, and refusing it would make every lane undrawable");
});

test("the loop wire (cycle.complete -> target.arm) is refused with the filled sentence", () => {
  // Source first, then destination, both as NODE_DEFS labels. Note that
  // target.arm is OCCUPIED (dusk feeds it), so flowsConnect would replace the
  // dusk wire: the loop is measured on that graph and is still a loop.
  //
  // MUTANT "no reachability walk" (the walk never runs, so nothing is ever
  // reached). Observed (3 failed; this one, the AUTOFOCUS/GUIDE loop and the
  // self-wire):
  //   x the loop wire (cycle.complete -> target.arm) is refused with the filled sentence: a flow wire back into its own lane must be refused, naming source then destination
  //     expected "this flow loops back on itself at FILTER CYCLE -> TARGET; a flow lane runs once"
  //     got      null
  eq(flowLoopRefusal(LANE_NODES, LANE_EDGES, wire("c", "complete", "t", "arm")),
    "this flow loops back on itself at FILTER CYCLE -> TARGET; a flow lane runs once",
    "a flow wire back into its own lane must be refused, naming source then destination");
});

test("a backward EVENT wire (report.done -> pool.advance) is never refused", () => {
  // The campaign example's own wire (server examples.py:199): the pool reaches
  // the report along flow wires, so this wire DOES close a circle - through an
  // event wire, which means "whenever" and never moves the run cursor.
  //
  // MUTANT "check event wires too" (the proposed wire's lane gate deleted).
  // Observed (2 failed; this one and the mismatched-ports case below):
  //   x a backward EVENT wire (report.done -> pool.advance) is never refused: an event wire means "whenever"; refusing it makes the campaign undrawable
  //     expected null
  //     got      "this flow loops back on itself at SESSION REPORT -> TARGET POOL; a flow lane runs once"
  const nodes = [node("p", "pool"), node("s", "slew", 250), node("c", "cycle", 500),
                 node("r", "report", 750)];
  const edges = [edge("p", "target", "s", "run"), edge("s", "centered", "c", "run"),
                 edge("c", "complete", "r", "session")];
  eq(flowLoopRefusal(nodes, edges, wire("r", "done", "p", "advance")), null,
    "an event wire means \"whenever\"; refusing it makes the campaign undrawable");
});

test("a longer loop, through AUTOFOCUS and GUIDE, is found", () => {
  // target -> autofocus -> guide -> cycle, and cycle.complete back into
  // target.arm. There is no wire from TARGET straight to FILTER CYCLE, so only
  // a walk sees the circle.
  //
  // MUTANT "only direct back-edges" (refuse only when a flow wire already runs
  // from the destination straight to the source). Observed:
  //   x a longer loop, through AUTOFOCUS and GUIDE, is found: the circle runs TARGET -> AUTOFOCUS -> GUIDE -> FILTER CYCLE -> TARGET and must be refused
  //     expected "this flow loops back on itself at FILTER CYCLE -> TARGET; a flow lane runs once"
  //     got      null
  const nodes = [node("d", "dusk"), node("t", "target", 200), node("a", "autofocus", 400),
                 node("g", "guide", 600), node("c", "cycle", 800)];
  const edges = [edge("d", "window", "t", "arm"), edge("t", "target", "a", "run"),
                 edge("a", "focused", "g", "run"), edge("g", "guiding", "c", "run")];
  eq(flowLoopRefusal(nodes, edges, wire("c", "complete", "t", "arm")),
    "this flow loops back on itself at FILTER CYCLE -> TARGET; a flow lane runs once",
    "the circle runs TARGET -> AUTOFOCUS -> GUIDE -> FILTER CYCLE -> TARGET and must be refused");
});

test("a circle that closes only through an event wire does not refuse a flow wire", () => {
  // The campaign lane, with its pool -> slew wire deleted and being drawn
  // again. SLEW reaches the POOL - but only through report.done -> pool.advance,
  // an event wire. The flow lane itself is acyclic, exactly as examples.py's
  // docstring says ("the flow lane stays acyclic").
  //
  // MUTANT "walk event wires too" (the walk follows every wire, not just flow
  // wires). Observed (2 failed; this one and the mismatched-wire circle below):
  //   x a circle that closes only through an event wire does not refuse a flow wire: re-drawing the campaign's pool -> slew wire must be allowed
  //     expected null
  //     got      "this flow loops back on itself at TARGET POOL -> SLEW + CENTER; a flow lane runs once"
  const nodes = [node("p", "pool"), node("s", "slew", 250), node("c", "cycle", 500),
                 node("r", "report", 750)];
  const edges = [edge("s", "centered", "c", "run"), edge("c", "complete", "r", "session"),
                 edge("r", "done", "p", "advance")];
  eq(flowLoopRefusal(nodes, edges, wire("p", "target", "s", "run")), null,
    "re-drawing the campaign's pool -> slew wire must be allowed");
});

test("a loop the graph already carries does not refuse an unrelated wire", () => {
  // CONTROL, and the reason the rule is "does THIS wire close a circle" rather
  // than the server's whole-graph "is there a circle". A flow saved before S0
  // can hold a loop (autofocus <-> guide here); save will now 422 until it is
  // deleted, and the operator must still be able to wire the rest meanwhile.
  //
  // MUTANT "refuse when the resulting graph has any flow cycle" (walk from
  // every node, not from the proposed wire's destination). Observed:
  //   x a loop the graph already carries does not refuse an unrelated wire: dusk -> target touches no circle, and refusing it locks a legacy flow's editor
  //     expected null
  //     got      "this flow loops back on itself at DUSK WINDOW -> TARGET; a flow lane runs once"
  const nodes = [node("d", "dusk"), node("t", "target", 200), node("a", "autofocus", 400),
                 node("g", "guide", 600)];
  const edges = [edge("a", "focused", "g", "run"), edge("g", "guiding", "a", "run")];
  eq(flowLoopRefusal(nodes, edges, wire("d", "window", "t", "arm")), null,
    "dusk -> target touches no circle, and refusing it locks a legacy flow's editor");
});

test("a flow wire from a stage into itself is a loop", () => {
  // The drag resolvers refuse a self-wire silently before this rule is asked,
  // but tap-to-wire reaches flowsConnect with no resolver in between and does
  // not refuse one, so the store's own guard is what stands here. A stage
  // whose "complete" feeds its own "run" is the smallest circle there is.
  //
  // MUTANT "walk starts from the destination's successors" (the destination
  // itself is never compared with the source). Observed:
  //   x a flow wire from a stage into itself is a loop: CAPTURE LOOP -> CAPTURE LOOP is a circle of one
  //     expected "this flow loops back on itself at CAPTURE LOOP -> CAPTURE LOOP; a flow lane runs once"
  //     got      null
  const nodes = [node("k", "capture")];
  eq(flowLoopRefusal(nodes, [], wire("k", "complete", "k", "run")),
    "this flow loops back on itself at CAPTURE LOOP -> CAPTURE LOOP; a flow lane runs once",
    "CAPTURE LOOP -> CAPTURE LOOP is a circle of one");
});

test("a wire whose ports disagree is the lane rule's to refuse, not this one's", () => {
  // FILTER CYCLE "complete" (a FLOW output) onto TARGET POOL "advance" (an
  // EVENT input), in a lane where the pool reaches the cycle. The server's
  // back-edge walk counts a wire only when BOTH ports are flow ports, so the
  // save would call this a lane mismatch and never a loop; the canvas says the
  // same, through the lane rule.
  //
  // MUTANT "judge the proposed wire by its source port alone". Observed:
  //   x a wire whose ports disagree is the lane rule's to refuse, not this one's: a flow output into an event input is a lane mismatch, not a loop
  //     expected null
  //     got      "this flow loops back on itself at FILTER CYCLE -> TARGET POOL; a flow lane runs once"
  const nodes = [node("p", "pool"), node("s", "slew", 250), node("c", "cycle", 500)];
  const edges = [edge("p", "target", "s", "run"), edge("s", "centered", "c", "run")];
  eq(flowLoopRefusal(nodes, edges, wire("c", "complete", "p", "advance")), null,
    "a flow output into an event input is a lane mismatch, not a loop");
});

test("a circle that closes only through a wire whose ports disagree refuses nothing", () => {
  // The same mismatched wire, already on the graph - tap-to-wire does no lane
  // check, so it can leave one - and the pool -> slew wire being drawn again.
  // The server's walk skips the mismatched wire, so the flow lane is acyclic
  // and the new wire must connect.
  //
  // MUTANT "walk wires by their source port alone". Observed:
  //   x a circle that closes only through a wire whose ports disagree refuses nothing: the server would not call this a loop, so neither may the canvas
  //     expected null
  //     got      "this flow loops back on itself at TARGET POOL -> SLEW + CENTER; a flow lane runs once"
  const nodes = [node("p", "pool"), node("s", "slew", 250), node("c", "cycle", 500)];
  const edges = [edge("s", "centered", "c", "run"), edge("c", "complete", "p", "advance")];
  eq(flowLoopRefusal(nodes, edges, wire("p", "target", "s", "run")), null,
    "the server would not call this a loop, so neither may the canvas");
});

console.log(`\nflowLoop: ${passed} passed, ${failed} failed`);
if (failures.length) { failures.forEach((f) => console.log(f)); process.exit(1); }
export default { passed, failed, total: passed + failed };
