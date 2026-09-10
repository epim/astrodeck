// flowGraphExtras.test.ts - the three edits the quick sheet makes to a graph the
// SERVER generated.
//
//   Run directly:  npx tsx src/next/hubs/sky/sheets/__tests__/flowGraphExtras.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE FIXTURE IS THE REAL LANE. `wizard.generate` chains dusk -> target -> slew
// -> autofocus -> guide -> cycle -> report through each node's FIRST FLOW PORT,
// so that is what these edits have to survive. Every assertion below is one way
// the edit can look right on a canvas and do nothing at runtime:
//
//   * an inserted stage whose upstream edge was not re-pointed (the run cursor
//     takes the old wire and the new node never fires)
//   * an edge left pointing at a node id that no longer terminates the lane
//   * the U+2212 / U+2026 in the DUSK FLATS window replaced by ASCII, which
//     stops the twilight window resolving because the compiler matches the string
//   * an edge built with FastAPI's Python attribute name `from_` instead of the
//     alias `from`, which round-trips and vanishes with no error anywhere

import { withDarksAfter, withDuskFlats, withRotation, withTargetPool } from "../flowGraphExtras";
import type { FlowGraphRec, FlowNodeType } from "../../../../../components/flows/flowsTypes";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg = ""): void {
  if (got !== want) throw new Error(`${msg} expected ${String(want)}, got ${String(got)}`);
}

/** The quick flow's lane, wired the way `wizard._Canvas.chain` wires it. */
function fixture(): FlowGraphRec {
  const lane: { id: string; type: FlowNodeType }[] = [
    { id: "n1", type: "dusk" },
    { id: "n2", type: "target" },
    { id: "n3", type: "slew" },
    { id: "n4", type: "autofocus" },
    { id: "n5", type: "guide" },
    { id: "n6", type: "cycle" },
    { id: "n7", type: "report" },
  ];
  return {
    nodes: lane.map((n, i) => ({ id: n.id, type: n.type, x: 30 + i * 228, y: 60, params: {} })),
    edges: [
      { id: "we0", from: "n1", fromPort: "window", to: "n2", toPort: "arm" },
      { id: "we1", from: "n2", fromPort: "target", to: "n3", toPort: "target" },
      { id: "we2", from: "n3", fromPort: "done", to: "n4", toPort: "run" },
      { id: "we3", from: "n4", fromPort: "done", to: "n5", toPort: "start" },
      { id: "we4", from: "n5", fromPort: "locked", to: "n6", toPort: "start" },
      { id: "we5", from: "n6", fromPort: "done", to: "n7", toPort: "session" },
    ],
  };
}

/** Every edge must land on a node that exists, and leave one that exists. An
 *  orphan is the failure mode this whole file guards. */
function noOrphans(g: FlowGraphRec): void {
  const ids = new Set(g.nodes.map((n) => n.id));
  for (const e of g.edges) {
    assert(ids.has(e.from), `edge ${e.id} leaves a node that is not in the graph (${e.from})`);
    assert(ids.has(e.to), `edge ${e.id} lands on a node that is not in the graph (${e.to})`);
  }
}

/** No node may be reachable by two flow wires from the same source port, and no
 *  edge may be duplicated - both are how a re-point that only ADDED looks. */
function uniqueEdges(g: FlowGraphRec): void {
  const seen = new Set<string>();
  for (const e of g.edges) {
    const key = `${e.from}:${e.fromPort}->${e.to}:${e.toPort}`;
    assert(!seen.has(key), `duplicate wire ${key}`);
    seen.add(key);
  }
}

// ----------------------------------------------------------------- flats

test("withDuskFlats splices the stage between the window and what it fed", () => {
  const g = withDuskFlats(fixture());
  const flats = g.nodes.find((n) => n.type === "duskflats");
  assert(flats != null, "no DUSK FLATS node was added");

  const fromDusk = g.edges.filter((e) => e.from === "n1" && e.fromPort === "window");
  eq(fromDusk.length, 1, "the window still drives exactly one wire");
  eq(fromDusk[0].to, flats!.id, "and it now drives the flats");
  eq(fromDusk[0].toPort, "run", "into its `run` port");

  const toTarget = g.edges.filter((e) => e.to === "n2" && e.toPort === "arm");
  eq(toTarget.length, 1, "the target is still armed by exactly one wire");
  eq(toTarget[0].from, flats!.id, "and it is now the flats that arm it");
  eq(toTarget[0].fromPort, "done", "from `flats done`");

  noOrphans(g);
  uniqueEdges(g);
});

test("the DUSK FLATS window keeps its U+2212 MINUS and U+2026 ELLIPSIS", () => {
  const g = withDuskFlats(fixture());
  const flats = g.nodes.find((n) => n.type === "duskflats");
  const window = String(flats?.params.window ?? "");
  assert(window.includes("−"), `the window lost its MINUS: ${JSON.stringify(window)}`);
  assert(window.includes("…"), `the window lost its ELLIPSIS: ${JSON.stringify(window)}`);
  assert(!window.includes("..."), "an ASCII ellipsis stops the compiler matching the window");
  eq(window, "Sun −2° … −8°", "the vocabulary's own string, character for character");
});

test("withDuskFlats is idempotent and refuses a graph with no DUSK WINDOW", () => {
  const once = withDuskFlats(fixture());
  eq(withDuskFlats(once).nodes.length, once.nodes.length, "a second call adds nothing");
  const noDusk: FlowGraphRec = { nodes: [{ id: "a", type: "report", x: 0, y: 0, params: {} }], edges: [] };
  eq(withDuskFlats(noDusk).nodes.length, 1, "no window means no flats, rather than an orphan");
});

// ----------------------------------------------------------------- darks

test("withDarksAfter wires report.done -> calib.do as an EVENT", () => {
  const g = withDarksAfter(fixture());
  const calib = g.nodes.find((n) => n.type === "calib");
  assert(calib != null, "no CALIBRATION QUEUE node was added");
  const wire = g.edges.find((e) => e.from === "n7" && e.to === calib!.id);
  assert(wire != null, "the report does not start the queue");
  eq(wire!.fromPort, "done", "the report's `target done` event");
  eq(wire!.toPort, "do", "the queue's `do` event input");
  noOrphans(g);
  uniqueEdges(g);
});

test("darks and bias are Always; flats are skipped because no panel is wired", () => {
  const calib = withDarksAfter(fixture()).nodes.find((n) => n.type === "calib");
  eq(calib?.params.darks, "Always", "the operator asked for darks");
  eq(calib?.params.bias, "Always", "");
  eq(calib?.params.flats, "Skip", "a queue waiting for a panel that never arrives is doctor rule 7");
});

test("the flow lane is untouched by the darks edit", () => {
  const g = withDarksAfter(fixture());
  const base = fixture();
  for (const e of base.edges) {
    const still = g.edges.find((x) => x.id === e.id);
    assert(still != null, `the darks edit dropped ${e.id}`);
    eq(`${still!.from}:${still!.fromPort}->${still!.to}:${still!.toPort}`,
      `${e.from}:${e.fromPort}->${e.to}:${e.toPort}`, `${e.id} was re-pointed`);
  }
});

// ------------------------------------------------------------------ pool

test("withTargetPool arms from dusk, feeds the target, and advances on report", () => {
  const g = withTargetPool(fixture(), ["M31", "M33", "NGC 869"]);
  const pool = g.nodes.find((n) => n.type === "pool");
  assert(pool != null, "no TARGET POOL node was added");
  eq(pool!.params.members, "M31, M33, NGC 869", "the pool's own comma list, in queue order");

  const fromDusk = g.edges.filter((e) => e.from === "n1" && e.fromPort === "window");
  eq(fromDusk.length, 1, "the window still drives one wire");
  eq(fromDusk[0].to, pool!.id, "and it arms the pool");

  const toTarget = g.edges.filter((e) => e.to === "n2" && e.toPort === "arm");
  eq(toTarget.length, 1, "");
  eq(toTarget[0].from, pool!.id, "the pool now feeds the target node");

  const advance = g.edges.find((e) => e.from === "n7" && e.toPort === "advance");
  assert(advance != null, "the campaign mechanism is missing: report.done -> pool.advance");
  eq(advance!.fromPort, "done", "");
  noOrphans(g);
  uniqueEdges(g);
});

test("one member is not a pool", () => {
  eq(withTargetPool(fixture(), ["M31"]).nodes.length, 7, "a single target stays a single target");
  eq(withTargetPool(fixture(), []).nodes.length, 7, "");
});

// ------------------------------------------------------------- the key trap

test("every edge any of the three writes uses the key `from`, never `from_`", () => {
  const graphs = [
    withDuskFlats(fixture()),
    withDarksAfter(fixture()),
    withTargetPool(fixture(), ["M31", "M33"]),
    withTargetPool(withDarksAfter(withDuskFlats(fixture())), ["M31", "M33"]),
  ];
  for (const g of graphs) {
    for (const e of g.edges) {
      const keys = Object.keys(e);
      assert(keys.includes("from"), `edge ${e.id} has no \`from\`: ${keys.join(",")}`);
      assert(!keys.includes("from_"),
        `edge ${e.id} carries \`from_\` - FastAPI serialises by alias and this wire would vanish`);
    }
    noOrphans(g);
    uniqueEdges(g);
  }
});

test("all three edits compose without colliding on ids", () => {
  const g = withTargetPool(withDarksAfter(withDuskFlats(fixture())), ["M31", "M33"]);
  const nodeIds = g.nodes.map((n) => n.id);
  eq(new Set(nodeIds).size, nodeIds.length, "two nodes share an id");
  const edgeIds = g.edges.map((e) => e.id);
  eq(new Set(edgeIds).size, edgeIds.length, "two edges share an id");
  // dusk -> pool -> flats?  No: the pool re-points whatever the window fed,
  // which after the flats edit is the FLATS node. The lane is still one chain.
  const fromDusk = g.edges.filter((e) => e.from === "n1" && e.fromPort === "window");
  eq(fromDusk.length, 1, "the window drives exactly one wire after three edits");
});

// ------------------------------------------------------------ the PA edit

test("withRotation writes the commanded angle onto the TARGET node", () => {
  // `wizard.quick` leaves the node vocabulary's shipped `rotation: 23.4` in
  // place and only replaces name/ra/dec, and `to_plan.py:815` reads that as a
  // REAL position angle - so every quick flow was asking a connected rotator
  // for a fixture value while the framing card promised something else.
  const seeded = fixture();
  seeded.nodes = seeded.nodes.map((n) =>
    n.type === "target" ? { ...n, params: { ...n.params, rotation: 23.4 } } : n);

  const g = withRotation(seeded, 30);
  const t = g.nodes.find((n) => n.type === "target");
  eq(t?.params.rotation, 30, "the framed angle:");
  eq(seeded.nodes.find((n) => n.type === "target")?.params.rotation, 23.4,
    "the input graph must not be mutated");
  eq(g.edges, seeded.edges, "a params edit must not touch the wiring");
});

test("no framing means NO angle constraint, which is -1 and not 0", () => {
  // `to_plan.py`'s own comment: 0 is north-up, a perfectly ordinary answer, so
  // it cannot be the sentinel. Writing 0 here would command PA 0 on every flow
  // whose target was never framed.
  const g = withRotation(fixture(), null);
  eq(g.nodes.find((n) => n.type === "target")?.params.rotation, -1, "the sentinel:");
});

test("withRotation is a no-op on a graph with no TARGET node, and when nothing changes", () => {
  const pooled: FlowGraphRec = { nodes: [{ id: "p1", type: "pool", x: 0, y: 0, params: {} }], edges: [] };
  eq(withRotation(pooled, 30), pooled, "a pool-only graph comes back untouched:");
  const once = withRotation(fixture(), 45);
  eq(withRotation(once, 45), once, "re-applying the same angle allocates nothing:");
});

const total = passed + failed;
console.log(`flowGraphExtras.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) {
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}

export default { passed, failed, total };
export { passed, failed, total };
