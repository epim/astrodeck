// panelLane.test.ts - the TypeScript panel lane graded against the SERVER'S
// worked cases (#151, #189; spec 2026-09-23 flows mosaic, 1.4 and 1.5).
//
//   Run:  node --import tsx src/components/flows/__tests__/panelLane.test.ts   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE FIXTURE IS server/tests/fixtures/panel_lane_cases.json, READ, NOT COPIED.
// test_flows_panel_lane.py grades compile.py `owner_of` and the lane against
// the same file, so the two walks are held to one table: a case added there is
// graded here on the next run, and a rule that changes there and not here goes
// red here. Do not replace the read with a literal fixture.
//
// WHAT IS GRADED, per case: `owners` (ownerOf), and per block in `lanes` the
// lane (panelLane), the tail (laneTail), `branched` (laneBranched) and `loop`
// (loopWires is non-empty). NOT graded, deliberately: each lane's `next`
// (compile.py `lane_next`, what runs after the block; nothing in the editor
// asks it yet) and each case's `compile` (the compiled steps, Python only).
// A key the fixture grows that is in neither list fails the key check below,
// so a new column cannot pass here by being skipped.
//
// ALSO GRADED: the fixture's `loop_cases` (#189 S4), the graphs `withLoop`
// makes when the modal's DONE places or lifts a block's loop wire, and its
// `carry_cases` (#331), the graphs flowsSlice.ts `carryLoopWire` makes when
// a stage is appended after a mosaic's tail. Here the TypeScript must make
// each recorded result; test_flows_panel_lane.py holds each result to
// compile.py (`loop_wires`, `lane_refusals`).
//
// ONE CASE IS A FLOW CYCLE with no way in, and a walk without its seen-set
// never returns from it. A synchronous hang cannot be interrupted from its own
// thread, so that case is graded in a CHILD process with a deadline: under
// the "no seen-set" mutant it fails with a sentence instead of freezing the
// file until the runner's 60 s kill.
//
// Every mutant below was run in a private scratch copy of ui/, never in the
// shared tree (#254), and the failure it produced is quoted verbatim.

/* eslint-disable @typescript-eslint/no-explicit-any */

// @ts-ignore  node built-ins; tsx supplies them at runtime
import { readFileSync } from "node:fs";
// @ts-ignore  node built-ins; tsx supplies them at runtime
import { spawnSync } from "node:child_process";
// @ts-ignore  node built-ins; tsx supplies them at runtime
import { fileURLToPath } from "node:url";

import {
  isMultiPanel, laneBranched, laneTail, loopSource, loopWires, ownerOf, panelLane, withLoop,
} from "../panelLane";
import type { LaneGraph } from "../panelLane";
import type { FlowNodeRec, FlowNodeType } from "../flowsTypes";

const proc = (globalThis as any).process;

const FIXTURE_REL = "../../../../../server/tests/fixtures/panel_lane_cases.json";

interface LaneExpect {
  lane: string[]; tail: string | null; branched: boolean; loop: boolean;
  next?: string[];
}
interface LaneCase {
  id: string;
  spec: string;
  graph: LaneGraph;
  owners: Record<string, string | null>;
  lanes: Record<string, LaneExpect>;
  compile?: unknown;
}

/** The server's cases. A missing or unreadable file must FAIL every case,
 *  never skip: a skipped fixture reads as a green mirror. */
function readCases(): LaneCase[] {
  let text: string;
  try {
    text = readFileSync(new URL(FIXTURE_REL, import.meta.url), "utf8") as string;
  } catch (e) {
    throw new Error(`cannot read ${FIXTURE_REL}, the worked cases this mirror is graded against: ${(e as Error).message}`);
  }
  const cases = (JSON.parse(text) as { cases?: LaneCase[] }).cases;
  if (!Array.isArray(cases) || cases.length === 0) {
    throw new Error(`${FIXTURE_REL} holds no cases`);
  }
  return cases;
}

/** What this file's walk says about one case, in the fixture's own shape. */
function observe(c: LaneCase): { owners: Record<string, string | null>; lanes: Record<string, LaneExpect> } {
  const owners: Record<string, string | null> = {};
  for (const id of Object.keys(c.owners)) owners[id] = ownerOf(c.graph, id)?.id ?? null;
  const lanes: Record<string, LaneExpect> = {};
  for (const block of Object.keys(c.lanes)) {
    lanes[block] = {
      lane: panelLane(c.graph, block).map((n) => n.id),
      tail: laneTail(c.graph, block)?.id ?? null,
      branched: laneBranched(c.graph, block),
      loop: loopWires(c.graph, block).length > 0,
    };
  }
  return { owners, lanes };
}

// ------------------------------------------------------------ child mode
// Run as `node --import tsx panelLane.test.ts` with PANEL_LANE_CASE set, this
// file prints `observe()` for that one case and grades nothing. It is how the
// cycle case gets a deadline. run-tests.mjs never sets the variable.
const CHILD_CASE: string | undefined = proc?.env?.PANEL_LANE_CASE;
if (CHILD_CASE) {
  const c = readCases().find((x) => x.id === CHILD_CASE);
  proc.stdout.write(JSON.stringify(c ? observe(c) : { missing: CHILD_CASE }));
}

/** `observe(case)`, computed in a child process given `timeoutMs`. */
function observeBounded(c: LaneCase, timeoutMs = 15_000): ReturnType<typeof observe> {
  const uiRoot = fileURLToPath(new URL("../../../../", import.meta.url));
  const r = spawnSync(proc.execPath, ["--import", "tsx", fileURLToPath(import.meta.url)], {
    cwd: uiRoot, encoding: "utf8", timeout: timeoutMs,
    env: { ...proc.env, PANEL_LANE_CASE: c.id },
  });
  if ((r.error as { code?: string } | undefined)?.code === "ETIMEDOUT" || r.signal) {
    throw new Error(`the walk on ${c.id} did not return within ${timeoutMs / 1000} s`);
  }
  if (r.status !== 0) {
    throw new Error(`the child grading ${c.id} exited ${r.status}: ${String(r.stderr).trim().slice(-400)}`);
  }
  return JSON.parse(r.stdout);
}

// --------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
const js = (v: unknown) => JSON.stringify(v);

/** Grade one case, every column named in the failure. */
function grade(c: LaneCase, got: ReturnType<typeof observe>): void {
  const bad: string[] = [];
  for (const [id, want] of Object.entries(c.owners)) {
    if (got.owners[id] !== want) bad.push(`owner of ${id}: expected ${js(want)}, got ${js(got.owners[id])}`);
  }
  for (const [block, want] of Object.entries(c.lanes)) {
    const g = got.lanes[block];
    if (js(g.lane) !== js(want.lane)) bad.push(`${block} lane: expected ${js(want.lane)}, got ${js(g.lane)}`);
    if (g.tail !== want.tail) bad.push(`${block} tail: expected ${js(want.tail)}, got ${js(g.tail)}`);
    if (g.branched !== want.branched) bad.push(`${block} branched: expected ${want.branched}, got ${g.branched}`);
    if (g.loop !== want.loop) bad.push(`${block} loop: expected ${want.loop}, got ${g.loop}`);
  }
  assert(bad.length === 0, bad.join("; "));
}

/** The cases that need a deadline: a flow wire cycle among lane nodes. */
function hasFlowCycle(c: LaneCase): boolean {
  const next = new Map<string, string[]>();
  for (const e of c.graph.edges) {
    if (e.fromPort === "pass") continue;
    next.set(e.from, [...(next.get(e.from) ?? []), e.to]);
  }
  const onPath = new Set<string>();
  const done = new Set<string>();
  const visit = (id: string): boolean => {
    if (onPath.has(id)) return true;
    if (done.has(id)) return false;
    onPath.add(id);
    const cyc = (next.get(id) ?? []).some(visit);
    onPath.delete(id);
    done.add(id);
    return cyc;
  };
  return c.graph.nodes.some((n) => visit(n.id));
}

// ================================================================= cases
//
// MUTANT "set walk" (panelLane.ts: walkToOwner replaced by the doctor's walk,
// a breadth-first climb through EVERY flow parent of any type that returns
// the nearest TARGET or POOL). Of the fixture's cases it fails the DOME case,
// and only it; the two-parent case below goes red with it. Observed (2
// failed, 12 passed):
//   x dome-ends-the-lane (1.5 worked case 4: TARGET(3x2) -> CYCLE -> DOME -> CAPTURE (the CAPTURE is M13)): owner of ha: expected null, got "t"; t lane: expected ["cy"], got ["cy","ha"]; t tail: expected "cy", got "ha"; t loop: expected true, got false
//   x two flow parents have no owner; one parent wired twice is still one: two TARGETs feeding one CAPTURE handed it to "a"
//
// MUTANT "no seen-set" (panelLane.ts walkToOwner: `|| seen.has(up.id)`
// deleted). Observed (1 failed, 13 passed), after the child's 15 s deadline:
//   x flow-cycle-with-no-way-in (1.5 item 3: a seen-set bounds the walk. Two stages feed each other and nothing feeds them; a walk without one never returns): the walk on flow-cycle-with-no-way-in did not return within 15 s
//
// MUTANT "tail of a branched lane" (panelLane.ts laneTail: the depth check
// deleted, so the last member is the tail whatever the shape). Observed (1
// failed, 13 passed):
//   x branched-lane (1.5 item 4: AF fans out to two stages, so 'the last stage' is ambiguous (M12)): t tail: expected null, got "ha"
//
// MUTANT "a pass wire from any stage" (panelLane.ts loopWires: the wire's
// source compared with every lane member instead of the tail). Observed (2
// failed, 12 passed):
//   x branched-lane (1.5 item 4: AF fans out to two stages, so 'the last stage' is ambiguous (M12)): t loop: expected false, got true
//   x mid-lane-pass-wire (1.4 item 4 / 1.5 item 5: the pass wire leaves CYCLE but CAPTURE Ha comes after it (M12)): t loop: expected false, got true
//
// MUTANT "any node can be walked from" (panelLane.ts walkToOwner: the
// lane-type check on the starting node deleted, so a REPORT, a DOME or a
// second TARGET joins the lane above it). Observed (6 failed, 8 passed),
// first and last:
//   x af-guide-cycle (1.5 worked case 1: TARGET(3x2) -> AF -> GUIDE -> CYCLE -> REPORT): t lane: expected ["af","g","cy"], got ["af","g","cy","r"]; t tail: expected "cy", got "r"; t loop: expected true, got false
//   x a node that ends the lane is owned by nobody: dm is owned by "t"

const GRADED_CASE_KEYS = ["id", "spec", "graph", "owners", "lanes"];
const UNGRADED_CASE_KEYS = ["compile"];
const GRADED_LANE_KEYS = ["lane", "tail", "branched", "loop"];
const UNGRADED_LANE_KEYS = ["next"];

if (!CHILD_CASE) {
  let cases: LaneCase[] = [];
  test("the server's worked cases are read from panel_lane_cases.json", () => {
    cases = readCases();
    // The spec's four worked cases and the three shapes the rule refuses.
    // Not a pin on the file's size: a case added there is graded here.
    //
    // MUTANT "a case renamed away" (a scratch copy of the fixture with
    // "branched-lane" renamed). Observed:
    //   x the server's worked cases are read from panel_lane_cases.json: the fixture no longer carries case branched-lane
    for (const id of ["af-guide-cycle", "cycle-then-capture", "second-target-owns-the-capture",
                      "dome-ends-the-lane", "branched-lane", "mid-lane-pass-wire",
                      "flow-cycle-with-no-way-in"]) {
      assert(cases.some((c) => c.id === id), `the fixture no longer carries case ${id}`);
    }
  });

  test("every column the fixture carries is graded here or named as Python's", () => {
    // A column added to the fixture that this file neither grades nor names
    // would pass here by being ignored. Observed with "nxt" added to one lane
    // of a scratch copy of the fixture:
    //   x every column the fixture carries is graded here or named as Python's: af-guide-cycle lane t carries "nxt", which this mirror neither grades nor names
    const bad: string[] = [];
    for (const c of cases) {
      for (const k of Object.keys(c)) {
        if (!GRADED_CASE_KEYS.includes(k) && !UNGRADED_CASE_KEYS.includes(k)) {
          bad.push(`${c.id} carries "${k}", which this mirror neither grades nor names`);
        }
      }
      for (const [block, l] of Object.entries(c.lanes)) {
        for (const k of Object.keys(l)) {
          if (!GRADED_LANE_KEYS.includes(k) && !UNGRADED_LANE_KEYS.includes(k)) {
            bad.push(`${c.id} lane ${block} carries "${k}", which this mirror neither grades nor names`);
          }
        }
      }
    }
    assert(bad.length === 0, bad.join("; "));
  });

  for (const c of cases) {
    const bounded = hasFlowCycle(c);
    test(`${c.id} (${c.spec})`, () => grade(c, bounded ? observeBounded(c) : observe(c)));
  }

  test("the cycle case really went through the deadline", () => {
    // The bound is only a bound if the cycle case takes it. Without this, a
    // fixture edit that broke the cycle would let the case run in-process and
    // a later seen-set mutant would freeze the file.
    //
    // MUTANT "the fixture's cycle broken" (a scratch copy of the fixture with
    // the b -> a wire deleted). Observed:
    //   x the cycle case really went through the deadline: the cases graded in a child are []
    const cyc = cases.filter(hasFlowCycle).map((c) => c.id);
    assert(js(cyc) === js(["flow-cycle-with-no-way-in"]),
      `the cases graded in a child are ${js(cyc)}`);
  });

  // ------------------------------------ shapes the fixture does not carry
  //
  // The fixture's graphs are ones an editor could draw. These are the ones
  // only a hand-built or half-built graph holds, which the editor still opens
  // (test_flows_panel_lane.py grades compile.py on each). Without them the
  // "any wire is a flow wire" mutant survived every fixture case, observed
  // "panelLane.test: 11/11 passed".
  const n = (id: string, type: FlowNodeType, x = 0, params: Record<string, string | number> = {}): FlowNodeRec =>
    ({ id, type, x, y: 0, params });
  const w = (from: string, fromPort: string, to: string, toPort: string, id = `${from}.${fromPort}-${to}.${toPort}`) =>
    ({ id, from, fromPort, to, toPort });

  // MUTANT "any wire is a flow wire" (panelLane.ts laneIndex: the two
  // port-kind checks deleted). Observed:
  //   x a wire from an event output is never a step of the lane: FILTER CYCLE "frame graded" into a CAPTURE made the CAPTURE the mosaic's: "t"; a CONDITION's event into a stage the TARGET feeds took its owner away: null
  test("a wire from an event output is never a step of the lane", () => {
    // FILTER CYCLE "frame graded" (event) dropped on a CAPTURE's "run" (flow)
    // crosses the lane boundary: validation refuses it, and read as a step it
    // would hand the CAPTURE to every panel.
    const g1: LaneGraph = {
      nodes: [n("t", "target", 0, { rows: 3, cols: 2 }), n("cy", "cycle", 200), n("ha", "capture", 400)],
      edges: [w("t", "target", "cy", "run"), w("cy", "frame", "ha", "run")],
    };
    // A CONDITION's event beside the TARGET's own flow wire: read as a
    // second parent it would leave the stage with two answers and no owner.
    const g2: LaneGraph = {
      nodes: [n("t", "target", 0, { rows: 3, cols: 2 }), n("k", "condition", 0), n("ha", "capture", 400)],
      edges: [w("t", "target", "ha", "run"), w("k", "fire", "ha", "run")],
    };
    const bad: string[] = [];
    const o1 = ownerOf(g1, "ha")?.id ?? null;
    if (o1 !== null) bad.push(`FILTER CYCLE "frame graded" into a CAPTURE made the CAPTURE the mosaic's: ${js(o1)}`);
    const o2 = ownerOf(g2, "ha")?.id ?? null;
    if (o2 !== "t") bad.push(`a CONDITION's event into a stage the TARGET feeds took its owner away: ${js(o2)}`);
    assert(bad.length === 0, bad.join("; "));
  });

  // MUTANT "the first parent wins" (panelLane.ts walkToOwner: `ups.length
  // !== 1` -> `ups.length === 0`). Observed:
  //   x two flow parents have no owner; one parent wired twice is still one: two TARGETs feeding one CAPTURE handed it to "a"
  //
  // MUTANT "a parent wired twice is two parents" (laneIndex: the
  // already-listed check deleted). Observed:
  //   x two flow parents have no owner; one parent wired twice is still one: a CAPTURE wired twice from one TARGET lost its owner: null
  test("two flow parents have no owner; one parent wired twice is still one", () => {
    const two: LaneGraph = {
      nodes: [n("a", "target", 0, { rows: 3, cols: 2 }), n("b", "target", 0), n("c", "capture", 200)],
      edges: [w("a", "target", "c", "run"), w("b", "target", "c", "run")],
    };
    const twice: LaneGraph = {
      nodes: [n("a", "target", 0, { rows: 3, cols: 2 }), n("c", "capture", 200)],
      edges: [w("a", "target", "c", "run", "w1"), w("a", "target", "c", "run", "w2")],
    };
    const bad: string[] = [];
    const o2 = ownerOf(two, "c")?.id ?? null;
    if (o2 !== null) bad.push(`two TARGETs feeding one CAPTURE handed it to ${js(o2)}`);
    const o1 = ownerOf(twice, "c")?.id ?? null;
    if (o1 !== "a") bad.push(`a CAPTURE wired twice from one TARGET lost its owner: ${js(o1)}`);
    assert(bad.length === 0, bad.join("; "));
  });

  // MUTANT "any node can be walked from" (as in the fixture block above; the
  // REPORT stays nobody's because its parent is the DOME). Observed here:
  //   x a node that ends the lane is owned by nobody: dm is owned by "t"
  test("a node that ends the lane is owned by nobody", () => {
    // DOME, REPORT and a missing id are in no lane; answering with the block
    // above them would put a DOME in a mosaic's lane.
    const g: LaneGraph = {
      nodes: [n("t", "target", 0, { rows: 3, cols: 2 }), n("dm", "dome", 200), n("r", "report", 400)],
      edges: [w("t", "target", "dm", "run"), w("dm", "open", "r", "session")],
    };
    const bad = ["dm", "r", "no-such-node"]
      .map((id) => [id, ownerOf(g, id)?.id ?? null] as const)
      .filter(([, o]) => o !== null)
      .map(([id, o]) => `${id} is owned by ${js(o)}`);
    assert(bad.length === 0, bad.join("; "));
  });

  // ------------------------------------------------------ isMultiPanel
  //
  // compile.py `is_multi_panel` switches the whole graph's scoping on, and
  // the editor reads it before carrying a loop wire (flowsConnect). Mirrors
  // `_grid_dim`: Python's `int()` decides which text is a whole number.
  //
  // MUTANT "any number is a side" (panelLane.ts gridDim: `Number(v)` for
  // every string, so "3.0" and "1e1" read as whole). Observed:
  //   x a TARGET's grid reads as compile.py _grid_dim reads it: rows "3.0" x cols 1: expected false, got true; rows "1e1" x cols 1: expected false, got true
  //
  // MUTANT "a POOL can be a mosaic" (panelLane.ts isMultiPanel: the type
  // check deleted). Observed:
  //   x a TARGET's grid reads as compile.py _grid_dim reads it: a pool with rows 2 x cols 2: expected false, got true
  test("a TARGET's grid reads as compile.py _grid_dim reads it", () => {
    const t = (rows: unknown, cols: unknown, type: FlowNodeType = "target"): FlowNodeRec =>
      ({ id: "t", type, x: 0, y: 0, params: { rows, cols } as any });
    const rows: [string, FlowNodeRec, boolean][] = [
      ["rows 3 x cols 2", t(3, 2), true],
      ["rows 1 x cols 2", t(1, 2), true],
      ["rows 1 x cols 1", t(1, 1), false],
      ["no grid at all", { id: "t", type: "target", x: 0, y: 0, params: {} }, false],
      ['rows "3" x cols 1 (int("3") is 3)', t("3", 1), true],
      ['rows " +2 " x cols 1', t(" +2 ", 1), true],
      ['rows "3.0" x cols 1', t("3.0", 1), false],
      ['rows "1e1" x cols 1', t("1e1", 1), false],
      ["rows 1.9 x cols 1", t(1.9, 1), false],
      ["rows 0 x cols 5", t(0, 5), true],
      ["rows -3 x cols 1", t(-3, 1), false],
      ["rows NaN x cols Infinity", t(NaN, Infinity), false],
      ['rows "banana" x cols 2', t("banana", 2), true],
      ["a pool with rows 2 x cols 2", t(2, 2, "pool"), false],
    ];
    const bad = rows.filter(([, n, want]) => isMultiPanel(n) !== want)
      .map(([what, n, want]) => `${what}: expected ${want}, got ${isMultiPanel(n)}`);
    assert(bad.length === 0, bad.join("; "));
  });

  // -------------------------------------- placing and lifting the loop wire
  //
  // Spec 1.4 "When the wire is added" and 2.5 DONE (#189 S4). `withLoop` is
  // the editor's rule for ADDING the loop wire, which compile.py does not
  // have. The fixture's `loop_cases` record the graphs it makes, and
  // test_flows_panel_lane.py grades those graphs against compile.py:
  // `loop_wires` finds exactly the recorded wire and `lane_refusals` (M12,
  // M13) is empty. So a rule that drifts here from what the server runs goes
  // red against a graph the server has already accepted.
  //
  // MUTANT "loop from the first owned stage" (panelLane.ts loopSource:
  // `panelLane(g, blockId)[0] ?? null` in place of `laneTail(g, blockId)`).
  // Observed (4 failed, 18 passed; the other three are the controls below
  // whose lanes also start at a stage with a pass output, and
  // flowsApplyFraming.test.ts's DONE cases go red with it):
  //   x loop case loop-on (1.4 'When the wire is added' / 1.5 item 5: DONE with the loop on, on a 3x2 block whose lane is CYCLE -> CAPTURE Ha. The wire leaves the tail, CAPTURE Ha, never the first stage): withLoop made ["e1:t.target->cy.run","e2:cy.complete->ha.run","e3:ha.complete->r.session","loop:cy.pass->t.next"], the fixture the server grades records ["e1:t.target->cy.run","e2:cy.complete->ha.run","e3:ha.complete->r.session","loop:ha.pass->t.next"]
  // The graph that mutant makes, handed to compile.py, is one the server
  // refuses: `loop_wires(g, "t")` is [] and `lane_refusals(g)` answers
  //   M12 cy | the loop wire starts at FILTER CYCLE, but CAPTURE LOOP Ha comes after it in the TARGET M31 panel lane. Start the loop wire at CAPTURE LOOP Ha to shoot every stage on every panel, or give CAPTURE LOOP Ha a TARGET of its own.
  //
  // MUTANT "loop added to a 1x1 block" (loopSource: `|| !isMultiPanel(block)`
  // deleted). Observed (1 failed, 21 passed):
  //   x loop case loop-on-single-target (1.4 item 4 (control): DONE with the loop on, on a 1x1 block. One panel has nothing to rotate between, so no wire is added): withLoop made ["e1:t.target->cy.run","e2:cy.complete->r.session","loop:cy.pass->t.next"], the fixture the server grades records ["e1:t.target->cy.run","e2:cy.complete->r.session"]
  //
  // MUTANT "lift only the loop wire" (withLoop, loop false: removes only
  // `loopWires(g, blockId)`, the tail's). Observed (1 failed, 21 passed):
  //   x loop case loop-off (1.4 'When it is deleted' / 2.5 DONE: the loop off lifts every pass wire into M31's next, its loop wire and a stale one from a mid-lane stage (M12) alike, and leaves the loop of the M33 mosaic that follows it): withLoop made ["e1:t.target->cy.run","e2:cy.complete->ha.run","e3:ha.complete->m33.arm","e4:m33.target->c2.run","e5:c2.complete->r.session","stale:cy.pass->t.next","m33-loop:c2.pass->m33.next"], the fixture the server grades records ["e1:t.target->cy.run","e2:cy.complete->ha.run","e3:ha.complete->m33.arm","e4:m33.target->c2.run","e5:c2.complete->r.session","m33-loop:c2.pass->m33.next"]
  //
  // MUTANT "lift every pass wire" (withLoop, loop false: the `e.to ===
  // blockId` check deleted, so another mosaic's loop goes too). Observed (1
  // failed, 21 passed):
  //   x loop case loop-off (1.4 'When it is deleted' / 2.5 DONE: the loop off lifts every pass wire into M31's next, its loop wire and a stale one from a mid-lane stage (M12) alike, and leaves the loop of the M33 mosaic that follows it): withLoop made ["e1:t.target->cy.run","e2:cy.complete->ha.run","e3:ha.complete->m33.arm","e4:m33.target->c2.run","e5:c2.complete->r.session"], the fixture the server grades records ["e1:t.target->cy.run","e2:cy.complete->ha.run","e3:ha.complete->m33.arm","e4:m33.target->c2.run","e5:c2.complete->r.session","m33-loop:c2.pass->m33.next"]
  //
  // MUTANT of the FIXTURE "loop-on recorded from the first owned stage" (a
  // scratch copy: loop-on's `loop` wire and `loop_wire` from `cy`, the graph
  // the first mutant above makes). This file and test_flows_panel_lane.py
  // both fail it; here, observed (1 failed, 21 passed):
  //   x loop case loop-on (1.4 'When the wire is added' / 1.5 item 5: DONE with the loop on, on a 3x2 block whose lane is CYCLE -> CAPTURE Ha. The wire leaves the tail, CAPTURE Ha, never the first stage): withLoop made ["e1:t.target->cy.run","e2:cy.complete->ha.run","e3:ha.complete->r.session","loop:ha.pass->t.next"], the fixture the server grades records ["e1:t.target->cy.run","e2:cy.complete->ha.run","e3:ha.complete->r.session","loop:cy.pass->t.next"]; the result's loop wires are [], loop_wire records ["loop:cy.pass->t.next"]

  interface LoopCase {
    id: string; spec: string; block: string; loop: boolean;
    graph: LaneGraph; result: LaneGraph;
    loop_wire: { id: string; from: string; fromPort: string; to: string; toPort: string } | null;
  }
  const LOOP_CASE_KEYS = ["id", "spec", "block", "loop", "graph", "result", "loop_wire"];

  /** The fixture's `loop_cases`, failing (never skipping) when absent. */
  function readLoopCases(): LoopCase[] {
    const text = readFileSync(new URL(FIXTURE_REL, import.meta.url), "utf8") as string;
    const list = (JSON.parse(text) as { loop_cases?: LoopCase[] }).loop_cases;
    if (!Array.isArray(list) || list.length === 0) {
      throw new Error(`${FIXTURE_REL} holds no loop_cases`);
    }
    return list;
  }
  /** Every wire as `id:from.port->to.port`, in order: what the cases compare. */
  const wireList = (g: { edges: readonly { id: string; from: string; fromPort: string; to: string; toPort: string }[] }) =>
    g.edges.map((e) => `${e.id}:${e.from}.${e.fromPort}->${e.to}.${e.toPort}`);

  let loopCases: LoopCase[] = [];
  test("the loop cases are read from panel_lane_cases.json, every column graded", () => {
    // MUTANT "a loop case renamed away" (a scratch copy of the fixture with
    // "loop-off" renamed). Observed (1 failed, 21 passed):
    //   x the loop cases are read from panel_lane_cases.json, every column graded: the fixture no longer carries loop case loop-off
    loopCases = readLoopCases();
    for (const id of ["loop-on", "loop-off", "loop-on-single-target"]) {
      assert(loopCases.some((c) => c.id === id), `the fixture no longer carries loop case ${id}`);
    }
    const bad = loopCases.flatMap((c) => Object.keys(c)
      .filter((k) => !LOOP_CASE_KEYS.includes(k))
      .map((k) => `${c.id} carries "${k}", which this mirror does not grade`));
    assert(bad.length === 0, bad.join("; "));
  });

  for (const c of loopCases) {
    test(`loop case ${c.id} (${c.spec})`, () => {
      const edges = withLoop(c.graph, c.block, c.loop, () => "loop");
      const bad: string[] = [];
      if (js(wireList({ edges })) !== js(wireList(c.result))) {
        bad.push(`withLoop made ${js(wireList({ edges }))}, the fixture the server grades records ${js(wireList(c.result))}`);
      }
      if (js(c.graph.nodes) !== js(c.result.nodes)) bad.push("the case's result changes a node; the loop is wires only");
      // The recorded wire is the result's loop wire by this file's walk too,
      // so the column the server grades cannot drift from the result.
      const loops = loopWires(c.result, c.block).map((e) => wireList({ edges: [e] })[0]);
      const want = c.loop_wire ? [wireList({ edges: [c.loop_wire] })[0]] : [];
      if (js(loops) !== js(want)) bad.push(`the result's loop wires are ${js(loops)}, loop_wire records ${js(want)}`);
      assert(bad.length === 0, bad.join("; "));
    });
  }

  // Shapes the fixture does not carry, each one condition of `loopSource`.
  const lane = (tailType: FlowNodeType, rows = 3): LaneGraph => ({
    nodes: [n("t", "target", 0, { rows, cols: 2 }), n("cy", "cycle", 200), n("x", tailType, 400)],
    edges: [w("t", "target", "cy", "run"), w("cy", "complete", "x", "run")],
  });

  test("control: loop on leaves a lane that already has its loop wire as it was, the same array", () => {
    // A second loop wire into one `next` is the doctor's "one is enough".
    //
    // MUTANT "a loop wire every time" (withLoop: the `loopWires(g,
    // blockId).length > 0` check deleted). Observed (1 failed, 21 passed):
    //   x control: loop on leaves a lane that already has its loop wire as it was, the same array: a lane with its loop wire was rewritten: ["t.target-cy.run:t.target->cy.run","cy.complete-x.run:cy.complete->x.run","own:x.pass->t.next","loop:x.pass->t.next"]
    const g = lane("capture");
    const looped: LaneGraph = { ...g, edges: [...g.edges, w("x", "pass", "t", "next", "own")] };
    const out = withLoop(looped, "t", true, () => "loop");
    assert(out === looped.edges, `a lane with its loop wire was rewritten: ${js(wireList({ edges: out }))}`);
  });

  test("control: loop on after a tail with no pass output adds nothing", () => {
    // Legacy SLEW is the one lane type with no "pass done" port; a wire from
    // it is refused at save ("slew has no output port 'pass'"), which would
    // block every save. This control used an AUTOFOCUS until S4 gave AUTOFOCUS
    // and GUIDE the port (#331); the AUTOFOCUS case now loops, below.
    //
    // MUTANT "no pass-port check" (loopSource: the `outs.some(PASS_PORT)`
    // test deleted). Observed:
    //   x control: loop on after a tail with no pass output adds nothing: a wire was drawn from a port SLEW lacks: ["t.target-cy.run:t.target->cy.run","cy.complete-x.run:cy.complete->x.run","loop:x.pass->t.next"]
    //   (panelLane.test: 30/31 passed)
    const g = lane("slew");
    const out = withLoop(g, "t", true, () => "loop");
    assert(out === g.edges, `a wire was drawn from a port SLEW lacks: ${js(wireList({ edges: out }))}`);
    assert(loopSource(g, "t") === null, "SLEW was named as the stage the loop leaves");
  });

  test("loop on after an AUTOFOCUS or GUIDE tail adds the wire from it (S4, #331)", () => {
    // DELIBERATE FLIP of the control above as it stood: either can end a
    // panel lane, and since S4 either has a `pass` output to give.
    //
    // MUTANT "AUTOFOCUS has no pass" (nodeDefs.ts, in a private scratch
    // copy). Observed:
    //   x loop on after an AUTOFOCUS or GUIDE tail adds the wire from it (S4, #331): autofocus: ["t.target-cy.run:t.target->cy.run","cy.complete-x.run:cy.complete->x.run"]
    //   (panelLane.test: 29/31 passed, with carry case 1 below)
    const bad: string[] = [];
    for (const t of ["autofocus", "guide"] as const) {
      const g = lane(t);
      const out = withLoop(g, "t", true, () => "loop");
      const want = [...wireList(g), "loop:x.pass->t.next"];
      if (js(wireList({ edges: out })) !== js(want)) bad.push(`${t}: ${js(wireList({ edges: out }))}`);
    }
    assert(bad.length === 0, bad.join("; "));
  });

  test("control: a branched lane, an empty lane and an unknown block gain no wire", () => {
    const branched: LaneGraph = { ...lane("capture"),
      edges: [...lane("capture").edges.slice(0, 1), w("t", "target", "x", "run")] };
    const empty: LaneGraph = { nodes: [n("t", "target", 0, { rows: 3, cols: 2 }), n("r", "report", 200)],
      edges: [w("t", "target", "r", "session")] };
    const bad = ([["branched", branched, "t"], ["empty", empty, "t"], ["unknown block", lane("capture"), "nope"]] as const)
      .filter(([, g, id]) => withLoop(g, id, true, () => "loop") !== g.edges)
      .map(([what]) => `${what}: a wire was added`);
    assert(bad.length === 0, bad.join("; "));
  });

  test("control: loop undefined leaves every wire, and loop off with nothing to lift is the same array", () => {
    const g = lane("capture");
    const looped: LaneGraph = { ...g, edges: [...g.edges, w("x", "pass", "t", "next", "own")] };
    assert(withLoop(looped, "t", undefined, () => "loop") === looped.edges,
      "a DONE that said nothing about the loop touched the wires");
    assert(withLoop(g, "t", false, () => "loop") === g.edges,
      "turning off a loop that is not there rewrote the wires");
    assert(loopSource(g, "t")?.id === "x", "premise: the capture is the stage the loop would leave");
  });

  // ---------------------------------------- carrying the loop wire (#331)
  //
  // Spec 1.5 item 6. The connect that appends a stage after a mosaic's tail
  // carries the lane's loop wire to the new tail in the same write
  // (flowsSlice.ts `carryLoopWire`). The fixture's `carry_cases` record the
  // graphs it makes, and test_flows_panel_lane.py grades each against
  // compile.py (a valid graph, the recorded tail and loop wires, no lane
  // refusal). Here the TypeScript must make each one. flowsSlice.test.ts
  // drives the same cases through `flowsConnect`, where "the same write" is
  // countable.
  //
  // MUTANT "carry only from the pre-connect tail" (carryLoopWire: `loops`
  // back to `new Set(loopWires(graph, owner.id))`, the tail's loop wire only,
  // as S3 built it). Observed:
  //   x carry case carry-capture-after-autofocus-stranding-the-loop (1.5 item 6 / #331 case 2: ...): carryLoopWire made ["e1:t.target->cy.run","e2:cy.complete->af.run","loop:cy.pass->t.next","drawn:af.focused->ha.run"], the fixture the server grades records ["e1:t.target->cy.run","e2:cy.complete->af.run","loop:ha.pass->t.next","drawn:af.focused->ha.run"]
  //   (panelLane.test: 31/32 passed on the re-run of 2026-09-27; 30/31 when
  //   first recorded, before the foreign-wire control). Case 2 stays M12: the graph that
  //   mutant makes, handed to compile.py, answers `lane_refusals`
  //     M12 cy | the loop wire starts at FILTER CYCLE, but AUTOFOCUS and CAPTURE LOOP Ha come after it in the TARGET M31 panel lane. Start the loop wire at CAPTURE LOOP Ha to shoot every stage on every panel, or give AUTOFOCUS and CAPTURE LOOP Ha a TARGET of their own.
  //
  // MUTANT "AUTOFOCUS has no pass" (nodeDefs.ts). Observed:
  //   x carry case carry-autofocus-after-looped-cycle (1.5 item 6 / #331 case 1: ...): carryLoopWire made ["e1:t.target->cy.run","loop:cy.pass->t.next","drawn:cy.complete->af.run"], the fixture the server grades records ["e1:t.target->cy.run","loop:af.pass->t.next","drawn:cy.complete->af.run"]
  //   (panelLane.test: 30/32 passed on the re-run of 2026-09-27, with the
  //   AUTOFOCUS loop-on case above).
  //   Case 1 stays M12: the graph that mutant makes answers `lane_refusals`
  //     M12 cy | the loop wire starts at FILTER CYCLE, but AUTOFOCUS comes after it in the TARGET M31 panel lane. Start the loop wire at AUTOFOCUS to shoot every stage on every panel, or give AUTOFOCUS a TARGET of its own.
  //
  // MUTANT "carry from any owned stage" (carryLoopWire: `lane.has(e.from)`
  // -> `ownerOf(graph, e.from) !== null`, so a pass wire from ANOTHER
  // block's lane into this block's `next` is carried too). It passed all 31
  // cases here and all 47 in flowsSlice.test.ts until the fixture gained
  // carry-foreign-pass-wire-control, the lane's other edge. Observed since:
  //   x carry case carry-foreign-pass-wire-control (1.5 item 6 / M4 (control): ...): carryLoopWire made ["e1:t.target->cy.run","e2:t2.target->c2.run","stray:af.pass->t.next","drawn:cy.complete->af.run"], the fixture the server grades records ["e1:t.target->cy.run","e2:t2.target->c2.run","stray:c2.pass->t.next","drawn:cy.complete->af.run"]
  //   x control: a carry that moves nothing hands back the very array it was given: carry-foreign-pass-wire-control: the edges were copied
  //   (panelLane.test: 30/32 passed). That graph, handed to compile.py, makes
  //   the stray wire M31's loop: M31 would rotate although the operator left
  //   it panel-first (test_flows_panel_lane.py holds that premise).
  //
  // THE TWO CONTROLS THE CARRY MUST LEAVE ALONE, each shown red here.
  // MUTANT "every block is a mosaic" (carryLoopWire: the isMultiPanel check
  // deleted). Observed:
  //   x carry case carry-single-target-control (1.4 item 4 (control): ...): carryLoopWire made ["e1:t.target->cy.run","pw:af.pass->t.next","drawn:cy.complete->af.run"], the fixture the server grades records ["e1:t.target->cy.run","pw:cy.pass->t.next","drawn:cy.complete->af.run"]
  //   x control: a carry that moves nothing hands back the very array it was given: carry-single-target-control: the edges were copied
  //   (panelLane.test: 30/32 passed).
  // MUTANT "add when missing" (carryLoopWire: a lane with no loop wire is
  // given one from the new tail). Observed:
  //   x carry case carry-no-loop-wire-control (1.4 'When it is deleted' (control): ...): carryLoopWire made ["e1:t.target->cy.run","drawn:cy.complete->af.run","added:af.pass->t.next"], the fixture the server grades records ["e1:t.target->cy.run","drawn:cy.complete->af.run"]
  //   with the foreign-wire case and the identity control below
  //   (panelLane.test: 29/32 passed).
  //
  // flowsSlice.ts imports lib/flowsApi -> lib/api -> lib/base, which reads
  // `window.location` at module scope, so it is imported after a window
  // exists, as flowsSlice.test.ts does. Nothing it offers a request is used.
  const g0 = globalThis as any;
  g0.window ??= { location: { pathname: "/", origin: "http://local" } };
  g0.localStorage ??= { getItem: () => null, setItem() {}, removeItem() {} };
  const { carryLoopWire } = await import("../flowsSlice");

  interface CarryCase {
    id: string; spec: string; block: string;
    graph: LaneGraph; result: LaneGraph;
    connect: { id: string; from: string; fromPort: string; to: string; toPort: string };
    lane_tail: string | null; loop_wires: string[];
  }
  const CARRY_CASE_KEYS = ["id", "spec", "block", "graph", "connect", "result", "lane_tail", "loop_wires"];

  /** The fixture's `carry_cases`, failing (never skipping) when absent. */
  function readCarryCases(): CarryCase[] {
    const text = readFileSync(new URL(FIXTURE_REL, import.meta.url), "utf8") as string;
    const list = (JSON.parse(text) as { carry_cases?: CarryCase[] }).carry_cases;
    if (!Array.isArray(list) || list.length === 0) {
      throw new Error(`${FIXTURE_REL} holds no carry_cases`);
    }
    return list;
  }

  let carryCases: CarryCase[] = [];
  test("the carry cases are read from panel_lane_cases.json, every column graded", () => {
    // MUTANT "a carry case renamed away" (a scratch copy of the fixture with
    // "carry-autofocus-after-looped-cycle" renamed). Observed:
    //   x the carry cases are read from panel_lane_cases.json, every column graded: the fixture no longer carries carry case carry-autofocus-after-looped-cycle
    //   (panelLane.test: 30/31 passed)
    carryCases = readCarryCases();
    for (const id of ["carry-autofocus-after-looped-cycle",
                      "carry-capture-after-autofocus-stranding-the-loop",
                      "carry-capture-after-autofocus-holding-the-loop",
                      "carry-guide-after-looped-capture",
                      "carry-single-target-control", "carry-no-loop-wire-control",
                      "carry-foreign-pass-wire-control"]) {
      assert(carryCases.some((c) => c.id === id), `the fixture no longer carries carry case ${id}`);
    }
    const bad = carryCases.flatMap((c) => Object.keys(c)
      .filter((k) => !CARRY_CASE_KEYS.includes(k))
      .map((k) => `${c.id} carries "${k}", which this mirror does not grade`));
    assert(bad.length === 0, bad.join("; "));
  });

  for (const c of carryCases) {
    test(`carry case ${c.id} (${c.spec})`, () => {
      // The connect as flowsConnect writes it here: into a free flow input,
      // so no wire is replaced, and the drawn wire goes last.
      const taken = c.graph.edges.some((e) => e.to === c.connect.to && e.toPort === c.connect.toPort);
      assert(!taken, `${c.id}'s connect lands on an input that already has a wire`);
      const drawn = [...c.graph.edges, c.connect];
      const out = carryLoopWire(c.graph as any, drawn as any, c.connect.from);
      const bad: string[] = [];
      if (js(wireList({ edges: out })) !== js(wireList(c.result))) {
        bad.push(`carryLoopWire made ${js(wireList({ edges: out }))}, the fixture the server grades records ${js(wireList(c.result))}`);
      }
      if (js(c.graph.nodes) !== js(c.result.nodes)) bad.push("the case's result changes a node; a connect is wires only");
      // The recorded columns by this file's walk too, so they cannot drift
      // from the result the server grades.
      const tail = laneTail(c.result, c.block)?.id ?? null;
      if (tail !== c.lane_tail) bad.push(`the result's tail is ${js(tail)}, lane_tail records ${js(c.lane_tail)}`);
      const loops = loopWires(c.result, c.block).map((e) => e.id);
      if (js(loops) !== js(c.loop_wires)) bad.push(`the result's loop wires are ${js(loops)}, loop_wires records ${js(c.loop_wires)}`);
      assert(bad.length === 0, bad.join("; "));
    });
  }

  test("control: a carry that moves nothing hands back the very array it was given", () => {
    // flowsConnect writes what carryLoopWire returns, so an identity answer
    // is what tells "nothing to carry" apart from "a copy that looks alike".
    //
    // MUTANT "a copy for nothing" (carryLoopWire: `return [...edges]` for a
    // lane with no loop wire). Observed on the re-run of 2026-09-27 (31/32
    // passed; the foreign-wire control, added since it was first recorded,
    // has no loop wire of its lane either, so it is copied too):
    //   x control: a carry that moves nothing hands back the very array it was given: carry-no-loop-wire-control: the edges were copied; carry-foreign-pass-wire-control: the edges were copied
    // flowsSlice.test.ts stays green under this mutant (49 passed): only an
    // identity check tells a copy from the same wires.
    const bad = carryCases
      .filter((c) => c.id.endsWith("-control"))
      .filter((c) => {
        const drawn = [...c.graph.edges, c.connect];
        return carryLoopWire(c.graph as any, drawn as any, c.connect.from) !== drawn;
      })
      .map((c) => `${c.id}: the edges were copied`);
    assert(bad.length === 0, bad.join("; "));
  });
}

// ------------------------------------------------------------------- tally
const total = passed + failed;
if (!CHILD_CASE) {
  console.log(`panelLane.test: ${passed}/${total} passed`);
  for (const f of failures) console.log("  " + f);
  if (failed) proc.exitCode = 1;
}
export const result = { passed, failed, total };
export default result;
