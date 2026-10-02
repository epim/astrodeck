// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// flowsApplyFraming.test.ts - the TARGET modal's DONE: every param and the
// loop wire in one write, then one compile (#189 S4 item 3; spec 2.5, 1.4
// "When the wire is added").
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/__tests__/flowsApplyFraming.test.ts   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT IS WORTH GUARDING HERE
//
// DONE changes several params at once (name, coordinates, grid, overlap,
// angle, field) and perhaps the loop wire. Written one key at a time, every
// write between the first and the last is a block nobody framed (a 3x2 grid
// on the old coordinates), and each write that starts a compile adds an
// answer racing to be the doctor chip. So:
//
//   - ONE graph write, ONE dirty flip, exactly ONE compile, of the final
//     graph; the promise resolves when that compile has answered, which is
//     when the card may say it is valid.
//   - Each value coerced by the type of its default, as `flowsSetParam`
//     coerces, NaN falling back to the default.
//   - The loop wire placed (true), lifted (false) or left (undefined) by
//     panelLane.ts `withLoop`, judged on the FRAMED block: DONE that turns a
//     1x1 into a 3x2 and asks for the loop gets it.
//
// panelLane.test.ts grades `withLoop` itself against the fixture's graphs;
// this file grades that DONE uses it, in the same write.
//
// Every mutant below was run in a private scratch copy of ui/ (#254), and the
// failure it produced is quoted verbatim.

/* eslint-disable @typescript-eslint/no-explicit-any */

// The slice imports lib/flowsApi -> lib/api -> lib/base, and base.ts reads
// `window.location.pathname` AT MODULE SCOPE. No request is made: the
// compile is replaced below.
(globalThis as any).window = { location: { pathname: "/", origin: "http://local" } };
(globalThis as any).localStorage = { getItem: () => null, setItem() {}, removeItem() {} };

const { createFlowsActions, FLOWS_INIT, coerceParam } = await import("../flowsSlice");
const { flowsApi } = await import("../../../lib/flowsApi");
const { NODE_DEFS } = await import("../nodeDefs");
type FlowsHost = import("../flowsSlice").FlowsHost;
type FlowsState = import("../flowsSlice").FlowsState;
type FlowGraphRec = import("../flowsTypes").FlowGraphRec;

let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) {
    throw new Error(`${msg}\n  expected ${JSON.stringify(want)}\n  got      ${JSON.stringify(got)}`);
  }
}

// ---------------------------------------------------------------- compile
/** Every graph a compile was started with, in order. `gate`, when set, holds
 *  each compile open until it is released. */
let compiled: FlowGraphRec[] = [];
let gate: Promise<void> | null = null;
(flowsApi as any).compileDraft = async (graph: FlowGraphRec) => {
  compiled.push(JSON.parse(JSON.stringify(graph)));
  if (gate) await gate;
  return { plan: { n: compiled.length }, structural: [], issues: [], unmapped: [] };
};

/** A miniature store. Counts graph writes and dirty flips, which "one write,
 *  one dirty flip" makes countable. */
function harness(graph: FlowGraphRec) {
  let state: FlowsHost;
  let graphWrites = 0;
  let dirtyFlips = 0;
  const set = (fn: (s: FlowsHost) => Partial<FlowsHost>) => {
    const before = state.flows;
    state = { ...state, ...fn(state) } as FlowsHost;
    if (state.flows.graph !== before.graph) graphWrites++;
    if (state.flows.dirty && !before.dirty) dirtyFlips++;
  };
  const actions = createFlowsActions(set, () => state);
  state = { ...actions, flows: { ...FLOWS_INIT, graph } } as FlowsHost;
  compiled = [];
  return {
    get flows(): FlowsState { return state.flows; },
    get graphWrites() { return graphWrites; },
    get dirtyFlips() { return dirtyFlips; },
    a: actions,
  };
}

/** A value as the failure prints it: JSON, except NaN, which JSON calls null. */
const show = (v: unknown) => (typeof v === "number" && Number.isNaN(v) ? "NaN" : JSON.stringify(v));
const wires = (g: FlowGraphRec) => g.edges.map((e) => `${e.from}.${e.fromPort}->${e.to}.${e.toPort}`).join(", ");
const loopsInto = (g: FlowGraphRec, id: string) =>
  g.edges.filter((e) => e.fromPort === "pass" && e.to === id && e.toPort === "next");

/** DUSK -> TARGET (as dropped: 1x1, blank) -> FILTER CYCLE -> CAPTURE Ha ->
 *  REPORT, the flow's own settings beside it. */
function dropped(params: Record<string, string | number> = {}): FlowGraphRec {
  return {
    nodes: [
      { id: "d", type: "dusk", x: 0, y: 0, params: {} },
      { id: "t", type: "target", x: 250, y: 0,
        params: { name: "", ra: "", dec: "", rotation: -1, angle: "Any angle", rows: 1, cols: 1, ...params } },
      { id: "cy", type: "cycle", x: 500, y: 0, params: {} },
      { id: "ha", type: "capture", x: 750, y: 0, params: { filter: "Ha" } },
      { id: "r", type: "report", x: 1000, y: 0, params: {} },
    ],
    edges: [
      { id: "k1", from: "d", fromPort: "window", to: "t", toPort: "arm" },
      { id: "k2", from: "t", fromPort: "target", to: "cy", toPort: "run" },
      { id: "k3", from: "cy", fromPort: "complete", to: "ha", toPort: "run" },
      { id: "k4", from: "ha", fromPort: "complete", to: "r", toPort: "session" },
    ],
    settings: { whenWaiting: "Wait for the mosaic" },
  };
}

/** What the modal hands DONE for M31 as a 3x2: text from its fields, as the
 *  inputs give it. */
const FRAMED = {
  name: "M31", ra: "00h 42m 44s", dec: "+41 16 09", rows: "3", cols: "2", overlap: "20",
  fovX: "2.0", fovY: "1.33", angle: "Rotate to PA", rotation: "30",
};

// ======================================================= one write, one compile

// MUTANT "one flowsSetParam per key" (flowsSlice.ts flowsApplyFraming: each
// key written through `get().flowsSetParam(id, key, String(raw))`, each
// followed by `get().flowsCompile()`, then the loop wire in a write of its
// own). Observed (5/10 passed):
//   x DONE is one graph write, one dirty flip and exactly one compile, of the framed graph: 11 graph writes, not 1; 10 compiles started, not 1; the compile did not see the framed graph
//   x the promise resolves only once the compile has answered: the compile's answer is not in hand
//   expected "{\"n\":1}"
//   got      "{\"n\":10}"
//   x control: DONE that frames a single target and asks for the loop adds no wire: precondition: the params were written
//   expected 1
//   got      3
//   x loop false lifts every pass wire into the block; loop undefined leaves the wires: the params and the lifted wires must be one write
//   expected 1
//   got      2
//   x control: DONE that changes nothing writes nothing and compiles nothing: a DONE that changed nothing touched the flow
//   expected "0 writes, 0 compiles, dirty false"
//   got      "3 writes, 3 compiles, dirty true"
//
// MUTANT "DONE is not an edit" (flowsApplyFraming: the write made with
// `patch(s, { graph })` in place of `touch(s, graph)`, so `dirty` never
// flips and a close drops the framing unsaved). Observed (9/10 passed):
//   x DONE is one graph write, one dirty flip and exactly one compile, of the framed graph: 0 dirty flips, not 1
await test("DONE is one graph write, one dirty flip and exactly one compile, of the framed graph", async () => {
  const h = harness(dropped());
  await h.a.flowsApplyFraming("t", FRAMED, true);
  const t = h.flows.graph.nodes.find((n) => n.id === "t")!;
  const bad: string[] = [];
  if (h.graphWrites !== 1) bad.push(`${h.graphWrites} graph writes, not 1`);
  if (h.dirtyFlips !== 1) bad.push(`${h.dirtyFlips} dirty flips, not 1`);
  if (compiled.length !== 1) bad.push(`${compiled.length} compiles started, not 1`);
  if (JSON.stringify(compiled[compiled.length - 1]) !== JSON.stringify(h.flows.graph)) {
    bad.push("the compile did not see the framed graph");
  }
  if (t.params.name !== "M31" || t.params.rows !== 3 || t.params.cols !== 2) {
    bad.push(`the block is not the framed one: ${JSON.stringify(t.params)}`);
  }
  if (loopsInto(h.flows.graph, "t").map((e) => e.from).join() !== "ha") {
    bad.push(`the loop wire does not leave the tail, CAPTURE Ha: ${wires(h.flows.graph)}`);
  }
  assert(bad.length === 0, bad.join("; "));
  eq(h.flows.dirty, true, "DONE is an edit");
});

await test("the promise resolves only once the compile has answered", async () => {
  // The card turns valid only after the compiler answers (spec 2.5), so the
  // modal awaits DONE; a promise that resolved on the write would let it
  // mark a block valid that no compile has seen.
  let release!: () => void;
  gate = new Promise<void>((r) => { release = r; });
  const h = harness(dropped());
  let done = false;
  const p = h.a.flowsApplyFraming("t", FRAMED, true).then(() => { done = true; });
  await Promise.resolve();
  await Promise.resolve();
  eq(done, false, "DONE resolved before its compile answered");
  eq(h.flows.compiling, true, "the compile DONE started is not in flight");
  release();
  await p;
  gate = null;
  eq(done, true, "DONE never resolved");
  eq(JSON.stringify(h.flows.compiled?.plan), JSON.stringify({ n: 1 }), "the compile's answer is not in hand");
});

await test("the flow's settings and every other node survive DONE", async () => {
  const h = harness(dropped());
  const before = h.flows.graph;
  await h.a.flowsApplyFraming("t", FRAMED, true);
  const g = h.flows.graph;
  eq(JSON.stringify(g.settings), JSON.stringify(before.settings), "DONE dropped the flow's settings");
  const others = g.nodes.filter((n) => n.id !== "t");
  assert(others.every((n) => before.nodes.includes(n)), "DONE rewrote a node it was not framing");
  assert(before.edges.every((e) => g.edges.includes(e)), `DONE rewrote a wire: ${wires(g)}`);
});

// ================================================================ coercion

// MUTANT "NaN kept" (flowsSlice.ts coerceParam: a numeric `raw` returned as
// it is, the NaN check skipped). Observed (8/10 passed):
//   x each value is coerced by its default's type, and NaN falls back to the default: overlap: expected 25, got NaN
//   x coerceParam is flowsSetParam's rule: the two cannot drift: NaN on a number: coerceParam(25, NaN) = NaN, expected 25
//
// MUTANT "values written raw" (flowsSlice.ts patchedParams: `raw` written
// in place of `coerceParam(defaults[key], raw)`). Observed (7/10 passed):
//   x DONE is one graph write, one dirty flip and exactly one compile, of the framed graph: the block is not the framed one: {"name":"M31","ra":"00h 42m 44s","dec":"+41 16 09","rotation":"30","angle":"Rotate to PA","rows":"3","cols":"2","overlap":"20","fovX":"2.0","fovY":"1.33"}
//   x each value is coerced by its default's type, and NaN falls back to the default: rows: expected 1, got "banana"; cols: expected 2, got "2"; overlap: expected 25, got NaN; fovX: expected 1.5, got "1.5"; name: expected "7331", got 7331
//   x control: DONE that changes nothing writes nothing and compiles nothing: a DONE that changed nothing touched the flow
//   expected "0 writes, 0 compiles, dirty false"
//   got      "1 writes, 1 compiles, dirty true"
await test("each value is coerced by its default's type, and NaN falls back to the default", async () => {
  const h = harness(dropped());
  await h.a.flowsApplyFraming("t", {
    rows: "banana", cols: "2", overlap: Number.NaN, fovX: "1.5", fovY: 0.9,
    name: 7331, skip: "1,3", angle: "Camera fixed at PA",
  });
  const p = h.flows.graph.nodes.find((n) => n.id === "t")!.params;
  const want: Record<string, string | number> = {
    rows: NODE_DEFS.target.params.rows as number, cols: 2,
    overlap: NODE_DEFS.target.params.overlap as number, fovX: 1.5, fovY: 0.9,
    // a text default takes text, so the card and the compile read a name
    name: "7331", skip: "1,3",
    // `angle` has no default (it is derived), so it is kept as given
    angle: "Camera fixed at PA",
  };
  const bad = Object.entries(want).filter(([k, v]) => p[k] !== v)
    .map(([k, v]) => `${k}: expected ${show(v)}, got ${show(p[k])}`);
  assert(bad.length === 0, bad.join("; "));
});

await test("coerceParam is flowsSetParam's rule: the two cannot drift", async () => {
  // flowsSetParam coerces through the same function; these are its old
  // cases, byte for byte (flowsSlice.test.ts holds the action itself).
  const rows: [string, string | number | undefined, string | number, string | number][] = [
    ["a number from text", 60, "240", 240],
    ["garbage text on a number", 60, "banana", 60],
    ["text on a text default", "1", "2", "2"],
    ["a key with no default", undefined, "Rotate to PA", "Rotate to PA"],
    ["NaN on a number", 25, Number.NaN, 25],
    ["a number on a text default", "", 3, "3"],
  ];
  const bad = rows.filter(([, base, raw, want]) => coerceParam(base, raw) !== want)
    .map(([what, base, raw, want]) =>
      `${what}: coerceParam(${show(base)}, ${show(raw)}) = ${show(coerceParam(base, raw))}, expected ${show(want)}`);
  assert(bad.length === 0, bad.join("; "));
});

// ================================================================ the loop

// MUTANT "loop judged on the old params" (flowsApplyFraming: `withLoop(g,
// ...)`, the graph before the patch, in place of `withLoop({ ...g, nodes },
// ...)`). Observed (7/10 passed):
//   x DONE is one graph write, one dirty flip and exactly one compile, of the framed graph: the loop wire does not leave the tail, CAPTURE Ha: d.window->t.arm, t.target->cy.run, cy.complete->ha.run, ha.complete->r.session
//   x DONE that makes a 1x1 block a 3x2 and asks for the loop gets the wire, from the tail: the new mosaic has no loop wire from its tail: d.window->t.arm, t.target->cy.run, cy.complete->ha.run, ha.complete->r.session
//   expected "ha.pass"
//   got      ""
//   x control: DONE that frames a single target and asks for the loop adds no wire: one panel has nothing to rotate between, wires now: d.window->t.arm, t.target->cy.run, cy.complete->ha.run, ha.complete->r.session, ha.pass->t.next
//   expected 0
//   got      1
await test("DONE that makes a 1x1 block a 3x2 and asks for the loop gets the wire, from the tail", async () => {
  const h = harness(dropped());
  await h.a.flowsApplyFraming("t", { rows: 3, cols: 2 }, true);
  const loops = loopsInto(h.flows.graph, "t");
  eq(loops.map((e) => `${e.from}.${e.fromPort}`).join(), "ha.pass",
    `the new mosaic has no loop wire from its tail: ${wires(h.flows.graph)}`);
});

// MUTANT "loop added to a 1x1 block" (panelLane.ts loopSource: `||
// !isMultiPanel(block)` deleted). Observed (9/10 passed; panelLane.test.ts's
// single-target fixture case goes red with it):
//   x control: DONE that frames a single target and asks for the loop adds no wire: one panel has nothing to rotate between, wires now: d.window->t.arm, t.target->cy.run, cy.complete->ha.run, ha.complete->r.session, ha.pass->t.next
//   expected 0
//   got      1
await test("control: DONE that frames a single target and asks for the loop adds no wire", async () => {
  const h = harness(dropped({ rows: 3, cols: 2 }));
  await h.a.flowsApplyFraming("t", { rows: 1, cols: 1, name: "NGC 7331" }, true);
  eq(loopsInto(h.flows.graph, "t").length, 0,
    `one panel has nothing to rotate between, wires now: ${wires(h.flows.graph)}`);
  eq(h.graphWrites, 1, "precondition: the params were written");
});

await test("loop false lifts every pass wire into the block; loop undefined leaves the wires", async () => {
  const looped = dropped({ rows: 3, cols: 2 });
  looped.edges.push({ id: "loop", from: "ha", fromPort: "pass", to: "t", toPort: "next" },
                    { id: "stale", from: "cy", fromPort: "pass", to: "t", toPort: "next" });
  const off = harness(looped);
  await off.a.flowsApplyFraming("t", { overlap: 15 }, false);
  eq(loopsInto(off.flows.graph, "t").length, 0, `a pass wire into the block survived: ${wires(off.flows.graph)}`);
  eq(off.graphWrites, 1, "the params and the lifted wires must be one write");

  const left = harness(looped);
  await left.a.flowsApplyFraming("t", { overlap: 15 });
  assert(left.flows.graph.edges === looped.edges,
    `a DONE that said nothing about the loop rewrote the wires: ${wires(left.flows.graph)}`);
});

// ================================================================ controls

// MUTANT "always write" (flowsApplyFraming: the no-change return deleted, so
// a DONE that changed nothing writes and compiles). Observed (9/10 passed):
//   x control: DONE that changes nothing writes nothing and compiles nothing: a DONE that changed nothing touched the flow
//   expected "0 writes, 0 compiles, dirty false"
//   got      "1 writes, 1 compiles, dirty true"
//
// MUTANT "a loop wire every time" (panelLane.ts withLoop: the existing-loop
// check deleted) is caught here too, by the same case. Observed (9/10
// passed):
//   x control: DONE that changes nothing writes nothing and compiles nothing: a DONE that changed nothing touched the flow
//   expected "0 writes, 0 compiles, dirty false"
//   got      "1 writes, 1 compiles, dirty true"
await test("control: DONE that changes nothing writes nothing and compiles nothing", async () => {
  const g = dropped({ rows: 3, cols: 2, name: "M31" });
  g.edges.push({ id: "loop", from: "ha", fromPort: "pass", to: "t", toPort: "next" });
  const h = harness(g);
  await h.a.flowsApplyFraming("t", { rows: "3", cols: 2, name: "M31" }, true);
  eq(`${h.graphWrites} writes, ${compiled.length} compiles, dirty ${h.flows.dirty}`,
    "0 writes, 0 compiles, dirty false",
    "a DONE that changed nothing touched the flow");
  assert(h.flows.graph === g, "the graph was replaced");
});

await test("control: DONE on a node that is gone writes nothing and compiles nothing", async () => {
  const h = harness(dropped());
  await h.a.flowsApplyFraming("gone", FRAMED, true);
  eq(`${h.graphWrites} writes, ${compiled.length} compiles`, "0 writes, 0 compiles",
    "a DONE for a deleted block wrote into the flow");
});

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`flowsApplyFraming.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) (globalThis as any).process.exitCode = 1;
export const result = { passed, failed, total };
export default result;
